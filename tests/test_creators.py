"""Offline tests for the creator check (v2/creators.py): no network, no sheets."""
import json
from datetime import datetime, timedelta, timezone

from creator_search.linkedin import Author, NotFound, PostPage, RelatedPost, Unavailable, parse_post_page
from creator_search.sheets import fmt_time
from v2 import creators

NOW = datetime(2026, 11, 17, 12, 0, tzinfo=timezone.utc)
URL = "https://www.linkedin.com/in/jane-doe"
POST = "https://www.linkedin.com/posts/jane-doe_x-activity-7590000000000000000-ab"
OTHER_POST = "https://www.linkedin.com/posts/jane-doe_y-activity-7591000000000000000-cd"


def _ld_page(stats=None, extra=""):
    ld = {"@type": "SocialMediaPosting", "datePublished": "2026-11-10T10:00:00.000Z",
          "author": {"name": "Jane Doe", "url": "https://in.linkedin.com/in/jane-doe",
                     "interactionStatistic": {"interactionType": "http://schema.org/FollowAction",
                                              "userInteractionCount": 20000}}}
    if stats is not None:
        ld["interactionStatistic"] = stats
    return f'<script type="application/ld+json">{json.dumps(ld)}</script>{extra}'


# --- parsing -------------------------------------------------------------------------------

def test_engagement_comes_from_json_ld():
    page = _ld_page([{"interactionType": "http://schema.org/LikeAction", "userInteractionCount": 17},
                     {"interactionType": "https://schema.org/CommentAction", "userInteractionCount": 4}])
    p = parse_post_page(page, POST)
    assert (p.reactions, p.comments) == (17, 4)


def test_engagement_never_read_from_a_related_post():
    page = _ld_page(extra='<section class="related-posts"><span data-num-reactions="999"></span></section>')
    p = parse_post_page(page, POST)
    assert p.reactions is None and p.comments is None  # unknown, not someone else's 999


def test_engagement_falls_back_to_own_markup():
    page = _ld_page(extra='<span data-num-reactions="42"></span><span data-num-comments="7"></span>'
                          '<section class="related-posts"><span data-num-reactions="999"></span></section>')
    p = parse_post_page(page, POST)
    assert (p.reactions, p.comments) == (42, 7)


def test_unavailable_is_still_a_not_found_for_old_callers():
    assert issubclass(Unavailable, NotFound)


# --- merging the source tabs ------------------------------------------------------------------

def _src(**kw):
    row = {"Name": "Jane Doe", "Profile URL": URL, "Niche": "Sales", "Followers": "20000",
           "Last Activity": "2026-11-14"}
    row.update(kw)
    return row


def test_candidates_join_qualified_and_bulk_and_dedupe():
    source = [_src(), _src(Name="Jane D", **{"Profile URL": "https://in.linkedin.com/in/Jane-Doe/"}),
              _src(**{"Profile URL": "not a link"})]
    qual = [{"Profile URL": URL, "Total Posts": "1,200", "Latest Post URL": POST,
             "Last Checked (IST)": "2026-11-16 10:00"}]
    bulk = [{"Profile URL": URL, "Total Posts": "1180", "Source Post URL": OTHER_POST,
             "Verified At (IST)": "2026-11-09 10:00"}]
    cands, skipped = creators.candidates(source, qual, bulk)
    assert len(cands) == 1 and skipped == {"duplicate": 1, "no profile link": 1}
    c = cands[0]
    assert c.slug == "jane-doe" and c.lifetime_posts == 1200
    assert c.post_urls == [POST, OTHER_POST]
    assert len(c.post_points) == 2


def test_candidate_without_any_post_link_is_kept():
    cands, _ = creators.candidates([_src()], [], [])
    assert cands[0].post_urls == [] and cands[0].lifetime_posts is None


# --- pace ------------------------------------------------------------------------------------

def _t(days_ago):
    return NOW - timedelta(days=days_ago)


def test_pace_uses_the_widest_window():
    assert creators.pace([(_t(7), 100), (_t(3), 104), (_t(0), 114)]) == (14.0, None)


def test_pace_needs_three_days():
    assert creators.pace([(_t(2), 100), (_t(0), 110)]) == (None, None)


def test_pace_when_post_count_drops_a_lot_is_unknown_and_flagged():
    value, flag = creators.pace([(_t(7), 100), (_t(0), 90)])
    assert value is None and flag


def test_a_small_drop_from_a_deleted_post_is_not_flagged():
    # 51 of 11,019 people in the real data, median drop 0.4%: someone deleted a post.
    assert creators.pace([(_t(7), 1171), (_t(0), 1164)]) == (None, None)


# --- scoring ---------------------------------------------------------------------------------

def test_strong_creator():
    s, known = creators.score(lifetime=1500, pace=4, reactions=250, comments=40)
    assert known == 100 and s >= creators.STRONG_AT


def test_casual_poster_is_not_a_creator():
    s, _ = creators.score(lifetime=40, pace=0.2, reactions=6, comments=0)
    assert s < creators.POSSIBLE_AT


def test_unknown_parts_are_left_out_not_counted_as_zero():
    as_zero, _ = creators.score(lifetime=1500, pace=0, reactions=250, comments=40)
    partial, known = creators.score(lifetime=1500, pace=None, reactions=250, comments=40)
    assert known == 75 and partial > as_zero


def test_tier_rules():
    T = creators.tier
    assert T(70, 100, reactions=120, comments=12) == "Strong"
    assert T(45, 100, reactions=30, comments=1) == "Possible"
    assert T(10, 100, reactions=3, comments=0) == "Not a creator"
    # Never "Not a creator" without having read their engagement: a failed fetch can't drop anyone,
    # and post volume alone never makes anyone a creator either.
    assert T(10, 55) == "Needs check"
    assert T(80, 55) == "Needs check"
    assert T(90, 30, reactions=200) == "Needs check"  # too little known to judge
    assert T(10, 100, reactions=1, comments=0, too_new=True) == "Needs check"
    assert T(70, 100, reactions=120, comments=12, organisation=True) == "Organisation?"


def test_tier_needs_an_audience_not_just_volume():
    T = creators.tier
    # 2,000+ posts at 90 a week but 3 reactions: a reposter, not an influencer.
    assert T(55, 100, reactions=3, comments=0) == "Not a creator"
    assert T(75, 100, reactions=30, comments=2) == "Possible"  # good score, audience too small for Strong
    assert T(75, 100, reactions=24, comments=18) == "Strong"   # comments count as an audience too
    assert T(45, 85, reactions=None, comments=6) == "Possible"


def test_engagement_alone_is_enough_to_judge():
    # Post count unknown (a page without the author card) but reactions and comments were read.
    value, known = creators.score(lifetime=None, pace=None, reactions=118, comments=7)
    assert known == 45 and creators.tier(value, known, reactions=118, comments=7) != "Needs check"


def test_same_person_after_a_profile_link_change():
    same = creators.same_person
    assert same("pratheepan-~-5b6572250", "Pratheepan ~", "pratheep-~-5b6572250", "Pratheep ~")
    assert same("jane-doe", "Jane Doe", "jane-doe-writes", "Jane Doe")  # same name
    assert not same("jane-doe", "Jane Doe", "john-smith", "John Smith")
    assert not same("amit-kumar-1", "Amit Kumar", "rahul-1", "Rahul")  # short trailing tokens don't count


def test_organisation_names():
    org = creators.looks_like_organisation
    assert org("IIM Mumbai Alumni Association")
    assert org("Kriprimaa HR Services")
    assert org("Sunfocus Solutions Pvt. Ltd.")
    assert not org("Dr. Mayur Patnala Kankanala (India/Bharat)")
    assert not org("Raghvendra Singh 🇮🇳")
    assert not org("Priya Services-Kumar")  # a surname can't trip it: whole words at the end only


# --- one creator check -------------------------------------------------------------------------

def _page(slug="jane-doe", reactions=120, comments=15, total=1300, age_days=5, related=(), name="Jane Doe"):
    a = Author(name=name, slug=slug, kind="in", country="in", followers=21000)
    return PostPage(url=POST, author=a, published=NOW - timedelta(days=age_days), total_posts=total, text="",
                    related=list(related), reactions=reactions, comments=comments)


class FakeFetcher:
    def __init__(self, results):
        self.results = results
        self.asked = []

    def post(self, url):
        self.asked.append(url)
        r = self.results.get(url)
        if isinstance(r, Exception):
            raise r
        if r is None:
            raise NotFound(url)
        return r


def _cand():
    cands, _ = creators.candidates(
        [_src()],
        [{"Profile URL": URL, "Total Posts": "1200", "Latest Post URL": POST, "Last Checked (IST)": "2026-11-10 12:00"}],
        [{"Profile URL": URL, "Source Post URL": OTHER_POST}])
    return cands[0]


def test_check_reads_engagement_and_same_author_related_posts():
    related = [RelatedPost(url=OTHER_POST, activity_id=None, author=Author(slug="jane-doe", kind="in"), reactions=400),
               RelatedPost(url="x", activity_id=None, author=Author(slug="someone-else", kind="in"), reactions=9999)]
    row = creators.check(_cand(), {}, FakeFetcher({POST: _page(related=related)}), NOW)
    assert row["Best Reactions"] == 400 and row["Latest Reactions"] == 120 and row["Best Comments"] == 15
    assert row["Tier"] == "Strong" and row["Last Error"] == ""


def test_check_falls_back_to_the_next_post_link():
    f = FakeFetcher({POST: NotFound("gone"), OTHER_POST: _page()})
    row = creators.check(_cand(), {}, f, NOW)
    assert f.asked == [POST, OTHER_POST] and row["Post URL"] == OTHER_POST
    assert row["Latest Reactions"] == 120


def test_temporary_failure_keeps_the_previous_result():
    previous = {"Tier": "Strong", "Score": "72", "Best Reactions": "300", "Best Comments": "20", "Checks": "2",
                "Lifetime Posts": "1200", "Last Scored (IST)": "2026-11-15 10:00"}
    f = FakeFetcher({POST: Unavailable("timeout"), OTHER_POST: Unavailable("timeout")})
    row = creators.check(_cand(), previous, f, NOW)
    assert row["Tier"] == "Strong" and row["Score"] == "72" and row["Best Reactions"] == "300"
    assert "try again" in row["Last Error"] and row["Retry After (IST)"]
    assert row["Last Scored (IST)"] == "2026-11-15 10:00"  # not counted as a fresh check


def test_every_post_gone_means_needs_check_not_rejected():
    row = creators.check(_cand(), {}, FakeFetcher({}), NOW)
    assert row["Tier"] == "Needs check" and "no post" in row["Flags"]


def test_post_by_someone_else_is_ignored():
    row = creators.check(_cand(), {}, FakeFetcher({POST: _page(slug="someone-else", reactions=5000, name="Someone Else"),
                                                   OTHER_POST: _page(slug="someone-else", name="Someone Else")}), NOW)
    assert row["Best Reactions"] in ("", None) and "someone else" in row["Flags"]


def test_best_engagement_never_goes_down():
    previous = {"Best Reactions": "300", "Best Comments": "40", "Checks": "1"}
    row = creators.check(_cand(), previous, FakeFetcher({POST: _page(reactions=12, comments=1)}), NOW)
    assert row["Best Reactions"] == 300 and row["Best Comments"] == 40 and row["Latest Reactions"] == 12


def test_fresh_post_with_low_numbers_waits():
    row = creators.check(_cand(), {}, FakeFetcher({POST: _page(reactions=1, comments=0, total=60, age_days=0.5)}),
                         NOW)
    assert row["Tier"] == "Needs check" and "under 2 days" in row["Flags"]


# --- which rows each run checks ------------------------------------------------------------------

def test_due_order_new_then_retries_then_oldest():
    cands = [creators.Candidate(slug=s, name=s, profile_url="", niche="", followers=None) for s in "abcde"]
    rows = {
        "a": {"Last Scored (IST)": fmt_time(NOW - timedelta(days=1))},                    # fresh: skip
        "b": {"Last Scored (IST)": fmt_time(NOW - timedelta(days=9))},                    # stale
        "c": {"Last Scored (IST)": fmt_time(NOW - timedelta(days=20))},                   # staler
        "d": {"Retry After (IST)": fmt_time(NOW - timedelta(hours=1)), "Last Error": "x"},  # retry due
        # "e" never checked
    }
    assert [c.slug for c in creators.due(cands, rows, NOW, limit=10)] == ["e", "d", "c", "b"]
    assert [c.slug for c in creators.due(cands, rows, NOW, limit=2)] == ["e", "d"]


def test_retry_not_due_yet_waits():
    cands = [creators.Candidate(slug="d", name="d", profile_url="", niche="", followers=None)]
    rows = {"d": {"Retry After (IST)": fmt_time(NOW + timedelta(hours=1)), "Last Error": "x"}}
    assert creators.due(cands, rows, NOW, limit=10) == []


def test_capped_post_count_counts_as_lifetime_but_not_pace():
    page = _page(total=None)
    page.posts_at_least = 3000
    row = creators.check(_cand(), {}, FakeFetcher({POST: page}), NOW)
    assert row["Lifetime Posts"] == "3000+" and row["Posts / Week"] == ""


def test_renamed_profile_link_still_counts():
    page = _page(slug="jane-doe-writes")
    row = creators.check(_cand(), {}, FakeFetcher({POST: page}), NOW)
    assert row["Latest Reactions"] == 120 and "profile link changed" in row["Flags"]


# --- the whole run against a fake spreadsheet ------------------------------------------------------

from gspread.utils import a1_to_rowcol  # noqa: E402

from creator_search.linkedin import Blocked  # noqa: E402


class FakeWS:
    def __init__(self, title, values=None):
        self.title, self.values, self.sorted_by = title, [list(r) for r in (values or [])], None

    col_count = 40

    def get_all_values(self):
        return [list(r) for r in self.values]

    def row_values(self, n):
        return list(self.values[n - 1]) if len(self.values) >= n else []

    def col_values(self, c):
        return [r[c - 1] if len(r) >= c else "" for r in self.values]

    def _put(self, row, col, val):
        while len(self.values) < row:
            self.values.append([])
        r = self.values[row - 1]
        r.extend([""] * (col - len(r)))
        r[col - 1] = val

    def update(self, values, range_name, value_input_option=None):
        row, col = a1_to_rowcol(range_name)
        for i, line in enumerate(values):
            for j, v in enumerate(line):
                self._put(row + i, col + j, v)

    def batch_update(self, chunk, value_input_option=None):
        for item in chunk:
            row, col = a1_to_rowcol(item["range"])
            self._put(row, col, item["values"][0][0])

    def append_rows(self, rows, **kw):
        self.values += [list(r) for r in rows]

    def freeze(self, rows):
        pass

    def add_cols(self, n):
        pass

    def sort(self, *specs, range=None):
        start, end = range.split(":")
        first, last = a1_to_rowcol(start)[0], a1_to_rowcol(end)[0]
        body = self.values[first - 1:last]
        for col, order in reversed(specs):  # stable sorts, last key first; blanks always at the end
            filled = [r for r in body if len(r) >= col and r[col - 1] != ""]
            blank = [r for r in body if not (len(r) >= col and r[col - 1] != "")]
            filled.sort(key=lambda r: float(r[col - 1]), reverse=order == "des")
            body = filled + blank
        self.values[first - 1:last] = body
        self.sorted_by = (specs, range)


class FakeBook:
    def __init__(self, tabs):
        self.tabs = tabs

    def worksheet(self, title):
        return self.tabs[title]


class FakeSheet:
    book = None

    def __init__(self, _id=None):
        self.book = FakeSheet.book

    def tab(self, title, headers):
        if title not in self.book.tabs:
            self.book.tabs[title] = FakeWS(title)
        return creators.Tab(self.book.tabs[title], headers)


def _book(n=3):
    people = [(f"Person {i}", f"https://www.linkedin.com/in/person-{i}") for i in range(n)]
    posts = {slug: f"https://www.linkedin.com/posts/person-{slug}_x-activity-75900000000000000{slug}0-ab"
             for slug in range(n)}
    source = [["Name", "Profile URL", "Niche", "Followers", "Last Activity"]] + \
             [[name, url, "Sales", "20000", "2026-11-14"] for name, url in people]
    qual = [["Name", "Profile URL", "Total Posts", "Latest Post URL", "Last Checked (IST)"]] + \
           [[name, url, "500", posts[i], "2026-11-10 10:00"] for i, (name, url) in enumerate(people)]
    bulk = [["Name", "Profile URL", "Total Posts", "Source Post URL", "Verified At (IST)"]]
    tabs = {"India not in DB": FakeWS("India not in DB", source), "India Qualified": FakeWS("India Qualified", qual),
            "India Bulk": FakeWS("India Bulk", bulk)}
    return FakeBook(tabs), posts


class PageFetcher:
    """Serves a strong-creator page for every post, or raises what `fail` says for a given call."""

    def __init__(self, fail=None):
        self.fail, self.requests_made = fail or {}, 0

    def post(self, url):
        self.requests_made += 1
        if self.requests_made in self.fail:
            raise self.fail[self.requests_made]
        slug = url.split("/posts/")[1].split("_")[0]
        a = Author(name=slug.replace("-", " ").title(), slug=slug, kind="in", country="in", followers=21000)
        return PostPage(url=url, author=a, published=NOW - timedelta(days=5), total_posts=520, text="",
                        reactions=150, comments=20)


def _run(monkeypatch, book, fetcher, limit=100, dry_run=False):
    monkeypatch.setattr(creators.config, "QUALIFIED_TAB", "India Qualified")
    monkeypatch.setattr(creators.config, "BULK_TAB", "India Bulk")
    FakeSheet.book = book
    monkeypatch.setattr(creators, "Sheet", FakeSheet)
    monkeypatch.setattr(creators, "Fetcher", lambda: fetcher)
    return creators.run(limit, dry_run)


def _score_rows(book):
    v = book.tabs["India Creator Scores"].values
    return [dict(zip(v[0], r + [""] * (len(v[0]) - len(r)))) for r in v[1:]]


def test_run_scores_everyone_and_sorts(monkeypatch):
    book, _ = _book(3)
    assert _run(monkeypatch, book, PageFetcher()) == 0
    rows = _score_rows(book)
    assert len(rows) == 3 and {r["Tier"] for r in rows} == {"Strong"}


def test_sort_covers_every_row_and_puts_tiers_in_order(monkeypatch):
    # The first run sorted only rows 2-1,000 (the size the tab had when created).
    book, _ = _book(1200)
    weak = {n: Unavailable("x") for n in range(1, 1200, 2)}  # half the people: page didn't load
    _run(monkeypatch, book, PageFetcher(fail=weak), limit=1200)
    rows = _score_rows(book)
    assert len(rows) == 1200
    tiers = [r["Tier"] for r in rows]
    assert tiers == sorted(tiers, key=lambda t: creators.TIER_RANK[t])  # Strong ... Needs check, no mixing
    _, rng = book.tabs["India Creator Scores"].sorted_by
    assert rng.endswith("1201")
    # Source tabs are only read, never written.
    assert book.tabs["India not in DB"].values[0] == ["Name", "Profile URL", "Niche", "Followers", "Last Activity"]


def test_second_run_adds_no_duplicates_and_skips_fresh_rows(monkeypatch):
    book, _ = _book(3)
    _run(monkeypatch, book, PageFetcher())
    again = PageFetcher()
    _run(monkeypatch, book, again)
    assert len(_score_rows(book)) == 3 and again.requests_made == 0


def test_blocked_mid_run_keeps_what_was_done(monkeypatch):
    book, _ = _book(3)
    code = _run(monkeypatch, book, PageFetcher(fail={2: Blocked("999")}))
    rows = _score_rows(book)
    assert code == 0 and len(rows) == 3
    assert sorted(r["Tier"] for r in rows) == ["Needs check", "Needs check", "Strong"]
    assert sum(1 for r in rows if r["Last Scored (IST)"]) == 1


def test_blocked_before_anything_fails_the_run(monkeypatch):
    book, _ = _book(2)
    assert _run(monkeypatch, book, PageFetcher(fail={1: Blocked("999")})) == 2


def test_write_finds_rows_after_the_tab_was_sorted_by_hand(monkeypatch):
    book, _ = _book(3)
    _run(monkeypatch, book, PageFetcher())
    ws = book.tabs["India Creator Scores"]
    ws.values = ws.values[:1] + list(reversed(ws.values[1:]))  # someone re-sorted the tab
    tab = creators.Tab(ws, creators.SCORE_HEADERS)
    creators.write(tab, {"person-0": {"Tier": "Possible", "Profile URL": "https://www.linkedin.com/in/person-0"}})
    by_slug = {creators.slug_of(r["Profile URL"]): r for r in _score_rows(book)}
    assert by_slug["person-0"]["Tier"] == "Possible"
    assert by_slug["person-1"]["Tier"] == "Strong" and by_slug["person-2"]["Tier"] == "Strong"


def test_dry_run_writes_nothing(monkeypatch):
    book, _ = _book(2)
    _run(monkeypatch, book, PageFetcher(), dry_run=True)
    assert "India Creator Scores" not in book.tabs


def test_unchecked_rows_get_a_refreshed_estimate_with_only_changed_cells(monkeypatch):
    book, _ = _book(3)
    _run(monkeypatch, book, PageFetcher(), limit=0)  # rows added, nobody checked
    ws = book.tabs["India Creator Scores"]
    flags = ws.values[0].index("Flags")
    ws.values[1][flags] = "post count went down (deleted posts?)"  # written by an older version
    writes = []
    real = ws.batch_update
    monkeypatch.setattr(ws, "batch_update", lambda chunk, **kw: (writes.extend(chunk), real(chunk, **kw)))
    _run(monkeypatch, book, PageFetcher(), limit=0)
    assert all(r["Flags"] == "" for r in _score_rows(book))
    assert len(writes) == 1  # just that one cell


def test_rows_deleted_mid_run_come_back_whole_or_not_at_all(monkeypatch):
    book, _ = _book(2)
    _run(monkeypatch, book, PageFetcher())
    ws = book.tabs["India Creator Scores"]
    ws.values = ws.values[:1]  # someone deleted every row
    tab = creators.Tab(ws, creators.SCORE_HEADERS)
    creators.write(tab, {"person-0": {"Tier": "Strong", "Profile URL": "https://www.linkedin.com/in/person-0"},
                         "person-1": {"Flags": ""}})  # partial update: not re-added as a broken row
    rows = _score_rows(book)
    assert [creators.slug_of(r["Profile URL"]) for r in rows] == ["person-0"]


def test_rank_follows_tier_on_checked_rows(monkeypatch):
    book, _ = _book(2)
    _run(monkeypatch, book, PageFetcher())
    ws = book.tabs["India Creator Scores"]
    rank, tier_col = ws.values[0].index("Rank"), ws.values[0].index("Tier")
    ws.values[1][rank] = ""             # a row scored before the Rank column existed
    ws.values[2][tier_col] = "Possible"  # a Tier edited by hand
    _run(monkeypatch, book, PageFetcher(), limit=0)
    assert {(r["Tier"], str(r["Rank"])) for r in _score_rows(book)} == {("Strong", "1"), ("Possible", "2")}
    assert [r["Tier"] for r in _score_rows(book)] == ["Strong", "Possible"]
