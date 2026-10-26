"""Offline tests for the v2 experiment (no network, no sheets)."""
import time
from datetime import datetime, timedelta, timezone

from creator_search.linkedin import Author, PostPage, slug_from_post_url
from creator_search.sheets import fmt_time
from v2 import config, discover, verify as v2verify
from v2.state import State

NOW = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)


def _post(slug, days_ago):
    aid = int((NOW - timedelta(days=days_ago)).timestamp() * 1000) << 22
    return f"https://www.linkedin.com/posts/{slug}_x-activity-{aid}-ab"


def test_state_memory_expires_and_round_trips(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    st = State()
    now = time.time()
    st.remember("small-person", "small", 1200, now=now)
    st.remember("stale-person", "stale", 9000, now=now - 8 * 86400)  # stale expires after 7 days
    st.remember("added-person", "added", 20000, now=now)
    assert st.skip("small-person", now) and st.status("small-person", now) == "small"
    assert not st.skip("stale-person", now)
    assert not st.skip("added-person", now)  # "added" is handled as already-known, not as rejected
    st.queue = [["u1", 0, "Legal", 5]]
    st.snapshots["x"] = {"t": int(now)}
    st.save()
    back = State.load()
    assert "stale-person" not in back.seen  # pruned
    assert back.seen["small-person"][0] == "small" and back.queue == [["u1", 0, "Legal", 5]]
    assert "x" in back.snapshots


def test_lane_leftovers_are_best_first_not_alphabetical():
    lane = discover.Lane(None, float("inf"), NOW)
    lane.push(_post("aaa-old-known", 20), discover.K_KNOWN)
    lane.push(_post("zzz-fresh-candidate", 2), discover.K_CANDIDATE, reactions=50, niche="Sales")
    lane.push(_post("mmm-fresh-candidate", 2), discover.K_CANDIDATE, reactions=5)
    left = [slug_from_post_url(item[3]) for item in lane.leftovers()]
    assert left == ["zzz-fresh-candidate", "mmm-fresh-candidate", "aaa-old-known"]
    assert lane.leftovers()[0][5] == "Sales"  # niche is kept with the queued post


def test_search_plan_round_robin():
    plan = discover.search_plan(["HR & People Ops", "Legal"])
    niches_in_order = [n for _, n in plan]
    assert niches_in_order[:2] == ["HR & People Ops", "Legal"]
    assert len(plan) == 2 * config.PHRASES_PER_NICHE
    assert len({p for p, _ in plan}) == len(plan)


def test_rejection_status():
    def page(kind="in", country="www", followers=20000):
        return PostPage(url="u", author=Author("A", "a", kind, country, followers), published=None,
                        total_posts=1, text="")
    assert discover.rejection_status(page()) is None
    assert discover.rejection_status(page(followers=400)) == "small"
    assert discover.rejection_status(page(followers=500000)) == "big"
    assert discover.rejection_status(page(country="uk")) == "nonus"
    assert discover.rejection_status(page(kind="company")) == "company"


def test_watch_schedule_fast_then_slow():
    now = datetime.now(timezone.utc)
    young = {"Added At (IST)": fmt_time(now - timedelta(hours=10)), "Verified At (IST)": fmt_time(now - timedelta(hours=3))}
    assert v2verify.watch_due(young, now)  # young: every 2 hours
    old = {"Added At (IST)": fmt_time(now - timedelta(days=5)), "Verified At (IST)": fmt_time(now - timedelta(hours=3))}
    assert not v2verify.watch_due(old, now)  # older: every 4 hours


def test_verify_from_snapshot_uses_same_rules():
    from verify import decide
    now = datetime.now(timezone.utc)
    snap = {"name": "Jane", "kind": "in", "country": "www", "followers": 12000, "total_posts": 300,
            "url": "u", "published": (now - timedelta(days=2)).isoformat(), "t": int(time.time())}
    page = v2verify.page_from_snapshot(snap)
    page.author.slug = "jane"
    row = {"Added At (IST)": fmt_time(now), "Total Posts": "300", "Newest Post Seen": ""}
    assert decide(page, row, "jane", now)[0] == "Qualified"
    snap["followers"] = 3000
    assert decide(v2verify.page_from_snapshot(snap), row, None, now)[0] == "Rejected"


def test_runlog_row():
    from v2.runlog import HEADERS, row_for
    row = row_for({"run_at": "2026-10-26 10:00", "pages": 600, "found": 80, "qualified": 50,
                   "lane_found": {"Legal": [80, 9]}, "niches": {"Legal": 9}, "discover_minutes": 12.5})
    assert set(row) == set(HEADERS)
    assert row["Found"] == 80 and row["Blocked"] == ""
