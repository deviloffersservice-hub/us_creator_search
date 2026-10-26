"""v2 verify: check every Pending row (and Watching rows that are due) in the v2 sheet.

Same rules and decisions as v1 (verify.decide). Differences:
  * a Pending row discovered within the last hour is checked against the page data discovery just
    downloaded, instead of fetching the same page again (saves ~1 LinkedIn request per creator)
  * Watching rows are re-checked every 2 hours for their first 48 hours (prolific people post daily,
    so they qualify sooner), then every 4 hours
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime, timedelta, timezone

from creator_search import niches, rules
from creator_search.linkedin import Author, Blocked, Fetcher, NotFound, PostPage, normalize_slug, profile_url
from creator_search.sheets import PROFILE_RE, Sheet, fmt_date, fmt_time, now_ist, parse_time
from verify import decide  # v1's decision logic, unchanged

from . import config
from .discover import V2_BULK_HEADERS, V2_QUALIFIED_HEADERS
from .state import State

log = logging.getLogger("v2.verify")


def watch_due(row: dict, now: datetime) -> bool:
    added = parse_time(row.get("Added At (IST)", "")) or now
    checked = parse_time(row.get("Verified At (IST)", "")) or added
    young = now - added < timedelta(hours=config.WATCH_FAST_FOR_HOURS)
    gap = config.WATCH_FAST_HOURS if young else config.WATCH_SLOW_HOURS
    return now - checked >= timedelta(hours=gap)


def rows_to_check(rows: list[dict], now: datetime) -> list[dict]:
    pending = [r for r in rows if r.get("Status", "").strip() in ("", "Pending")]
    watching = [r for r in rows if r.get("Status", "").strip() == "Watching" and watch_due(r, now)]
    watching.sort(key=lambda r: parse_time(r.get("Verified At (IST)", "")) or now)
    return [r for r in pending + watching[: config.WATCH_MAX] if r.get("Source Post URL")]


def page_from_snapshot(snap: dict) -> PostPage:
    published = datetime.fromisoformat(snap["published"]) if snap.get("published") else None
    author = Author(name=snap.get("name", ""), slug="", kind=snap.get("kind", ""), country=snap.get("country", ""),
                    followers=snap.get("followers"))
    return PostPage(url=snap.get("url", ""), author=author, published=published,
                    total_posts=snap.get("total_posts"), text="")


def run() -> int:
    st = State.load()
    started = time.time()
    sheet = Sheet(config.V2_SHEET_ID)
    bulk = sheet.tab(config.BULK_TAB, V2_BULK_HEADERS)
    qual = sheet.tab(config.QUALIFIED_TAB, V2_QUALIFIED_HEADERS)
    already = {normalize_slug(m.group(1)) for r in qual.rows() if (m := PROFILE_RE.search(r.get("Profile URL", "")))}
    now = datetime.now(timezone.utc)
    todo = rows_to_check(bulk.rows(), now)
    log.info("%d rows to verify", len(todo))

    fetcher = Fetcher()
    counts = {"Qualified": 0, "Rejected": 0, "Watching": 0}
    reused = fetched = 0
    new_qualified, blocked = [], False
    for i, row in enumerate(todo, 1):
        m = PROFILE_RE.search(row.get("Profile URL", ""))
        slug = normalize_slug(m.group(1)) if m else None
        snap = st.snapshots.get(slug or "")
        is_pending = row.get("Status", "").strip() in ("", "Pending")
        try:
            if is_pending and snap and time.time() - snap.get("t", 0) <= config.SNAPSHOT_MINUTES * 60:
                page = page_from_snapshot(snap)
                page.author.slug = slug
                reused += 1
            else:
                page = fetcher.post(row["Source Post URL"])
                fetched += 1
        except NotFound:
            bulk.set(row["_row"], {"Status": "Rejected", "Reason": "post deleted or not public",
                                   "Verified At (IST)": fmt_time(now_ist())})
            counts["Rejected"] += 1
            continue
        except Blocked as e:
            log.error("LinkedIn stopped serving pages (%s); %d rows left for the next run", e, len(todo) - i + 1)
            blocked = True
            break

        status, reason, activity, newest = decide(page, row, slug, now)
        counts[status] += 1
        a = page.author
        bulk.set(row["_row"], {
            "Name": a.name or row.get("Name", ""),
            "Followers": a.followers if a.followers is not None else "",
            "Total Posts": page.total_posts if page.total_posts is not None else row.get("Total Posts", ""),
            "Newest Post Seen": fmt_date(newest), "Location Signal": rules.location_signal(page),
            "Status": status, "Reason": reason, "Verified At (IST)": fmt_time(now_ist()),
        })
        if status == "Qualified" and slug and slug not in already:
            already.add(slug)
            new_qualified.append({
                "Name": a.name or row.get("Name", ""), "Profile URL": profile_url(slug),
                "Niche": row.get("Niche") or niches.OTHER, "Followers": a.followers,
                "Total Posts": page.total_posts if page.total_posts is not None else "",
                "Last Activity": fmt_date(activity), "Latest Post URL": row["Source Post URL"],
                "Status": "Active", "First Qualified (IST)": fmt_time(now_ist()),
                "Last Checked (IST)": fmt_time(now_ist()), "Notes": reason, "Found Via": row.get("Found Via", ""),
            })
        if i % 25 == 0:
            bulk.flush()
    bulk.flush()
    qual.append(new_qualified)
    log.info("done: %s; %d added to Qualified; %d checked from fresh page data, %d pages fetched",
             counts, len(new_qualified), reused, fetched)
    st.current.update({"verified": sum(counts.values()), **{k.lower(): v for k, v in counts.items()},
                       "verify_reused": reused, "verify_fetched": fetched, "verify_blocked": blocked,
                       "verify_minutes": round((time.time() - started) / 60, 1)})
    st.save()
    return 2 if blocked and not sum(counts.values()) else 0


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    sys.exit(run())


if __name__ == "__main__":
    main()
