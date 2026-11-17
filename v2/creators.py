"""Creator check: which people in the "India not in DB" tab are real creators worth emailing?

Having 5,000+ followers and one recent post doesn't make someone a creator. This scores each person
0-100 from what a logged-out visitor can see on their public post pages:

  Lifetime posts   (30 points)  the author card's total post count
  Posting pace     (25 points)  posts per week, from post counts recorded on different days
  Best reactions   (30 points)  the most reactions seen on any of their posts
  Best comments    (15 points)  the most comments seen on any of their posts

Tiers: Strong (60+ and an audience: 50+ reactions or 10+ comments on a post)
       Possible (35+ and 15+ reactions or 5+ comments)
       Not a creator, Needs check (not enough known yet), Organisation?
Posting a lot is not enough on its own: without people reacting, it's reposting, not influence.
Results go to CREATOR_SCORE_TAB, one row per person, sorted best first.

Built so a good creator is never dropped because of a problem on our side:
  * a page that fails to load leaves the previous result as it was; the person is retried later
  * "Not a creator" needs their engagement to have been read; without it they are "Needs check"
  * parts that are unknown are left out of the score, not counted as zero
  * best reactions/comments only ever go up, so one quiet post can't sink anyone
  * a post link that turns out to be someone else's post is ignored, but a person who changed
    their profile link is still recognised (same LinkedIn id at the end of the link, or same name)
  * "3000+ Posts" counts as 3,000 lifetime posts (and is left out of the posting pace)
  * a post under 2 days old hasn't collected its reactions yet, so low numbers there only wait
  * a dead post link falls back to the person's other known post links
  * results are written by profile link, not row number, so sorting the tab mid-run is safe
"""
from __future__ import annotations

import argparse
import logging
import re
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from gspread.utils import rowcol_to_a1

from creator_search.linkedin import Blocked, Fetcher, NotFound, PostPage, Unavailable, normalize_slug, to_int
from creator_search.sheets import PROFILE_RE, Sheet, Tab, _retry, fmt_time, normalize_name, parse_time

from . import config

log = logging.getLogger("v2.creators")

SCORE_HEADERS = [
    "Name", "Profile URL", "Niche", "Followers", "Tier", "Score", "Lifetime Posts", "Posts / Week",
    "Best Reactions", "Best Comments", "Latest Reactions", "Latest Comments", "Engagement %", "Flags",
    "Post URL", "Pace Since (IST)", "Pace Posts", "Checks", "Last Scored (IST)", "Last Error",
    "Retry After (IST)", "Rank",
]
# The tab is sorted by Rank, then Score: the outreach list first, people to skip last.
TIER_RANK = {"Strong": 1, "Possible": 2, "Needs check": 3, "Organisation?": 4, "Not a creator": 5}

STRONG_AT = 60
POSSIBLE_AT = 35
MIN_KNOWN = 45  # points that must be measurable before any tier is given (e.g. reactions + comments)
# The audience a tier needs on at least one post, on top of its score.
STRONG_AUDIENCE = (50, 10)  # reactions, or comments
POSSIBLE_AUDIENCE = (15, 5)
MIN_PACE_DAYS = 3
BIG_DROP = 0.05  # a post count falling more than this is worth a flag; less is just a deleted post
FRESH_POST_DAYS = 2

# (minimum, points), highest first. A value below every minimum scores 0.
LIFETIME_POINTS = ((1000, 30), (300, 24), (150, 16), (50, 8))
PACE_POINTS = ((5, 25), (2, 20), (1, 14), (0.5, 8), (0.01, 4))
REACTION_POINTS = ((300, 30), (100, 25), (50, 20), (20, 12), (10, 6))
COMMENT_POINTS = ((50, 15), (20, 12), (10, 9), (5, 5), (1, 2))
PART_MAX = {"lifetime": 30, "pace": 25, "reactions": 30, "comments": 15}

# Organisation pages that use a personal (/in/) profile. Words that mark one anywhere in the name,
# and words that do so only as the last word ("Priya Services-Kumar" is a person).
ORG_ANYWHERE = {"alumni", "association", "foundation", "pvt", "ltd", "llp", "limited", "council", "society",
                "institute", "university", "college", "academy", "consultancy", "inc"}
ORG_LAST_WORD = {"services", "solutions", "technologies", "consultants", "community", "hub", "network", "group",
                 "official", "team", "agency", "enterprises", "overseas", "careers", "llc", "studio", "media"}


@dataclass
class Candidate:
    slug: str
    name: str
    profile_url: str
    niche: str
    followers: int | None
    lifetime_posts: int | None = None
    post_urls: list[str] = field(default_factory=list)
    post_points: list[tuple[datetime, int]] = field(default_factory=list)  # (when, total posts then)


def slug_of(url: str) -> str | None:
    m = PROFILE_RE.search(url or "")
    return normalize_slug(m.group(1)) if m else None


def _by_slug(rows: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for r in rows:
        s = slug_of(r.get("Profile URL", ""))
        if s and s not in out:
            out[s] = r
    return out


def _point(when: str, posts: str) -> tuple[datetime, int] | None:
    t, n = parse_time(when), to_int(posts)
    return (t, n) if t and n is not None else None


def candidates(source: list[dict], qual: list[dict], bulk: list[dict]) -> tuple[list[Candidate], dict[str, int]]:
    """One Candidate per person in the source tab, with post links and post counts from the
    Qualified and Bulk tabs. Rows without a profile link, and repeats of a person, are skipped."""
    q, b = _by_slug(qual), _by_slug(bulk)
    out, seen, skipped = [], set(), Counter()
    for r in source:
        s = slug_of(r.get("Profile URL", ""))
        if not s:
            skipped["no profile link"] += 1
            continue
        if s in seen:
            skipped["duplicate"] += 1
            continue
        seen.add(s)
        qr, br = q.get(s, {}), b.get(s, {})
        counts = [n for n in (to_int(qr.get("Total Posts")), to_int(br.get("Total Posts"))) if n is not None]
        urls = [u.strip() for u in (qr.get("Latest Post URL", ""), br.get("Source Post URL", "")) if u.strip()]
        points = [p for p in (_point(qr.get("Last Checked (IST)", ""), qr.get("Total Posts", "")),
                              _point(br.get("Verified At (IST)", ""), br.get("Total Posts", ""))) if p]
        out.append(Candidate(slug=s, name=r.get("Name", "").strip(), profile_url=r.get("Profile URL", "").strip(),
                             niche=r.get("Niche", ""), followers=to_int(r.get("Followers")),
                             lifetime_posts=max(counts) if counts else None,
                             post_urls=list(dict.fromkeys(urls)), post_points=points))
    return out, dict(skipped)


def pace(points: list[tuple[datetime, int]]) -> tuple[float | None, str | None]:
    """Posts per week between the earliest and latest post counts (None if under 3 days apart)."""
    if len(points) < 2:
        return None, None
    first, last = min(points, key=lambda p: p[0]), max(points, key=lambda p: p[0])
    days = (last[0] - first[0]).total_seconds() / 86400
    if days < MIN_PACE_DAYS:
        return None, None
    if last[1] < first[1]:
        big = first[1] - last[1] > BIG_DROP * first[1]
        return None, ("post count went down a lot (deleted posts?)" if big else None)
    return round((last[1] - first[1]) / days * 7, 1), None


def _points(value: float | None, table: tuple) -> int | None:
    if value is None:
        return None
    return next((pts for minimum, pts in table if value >= minimum), 0)


def score(lifetime: int | None, pace: float | None, reactions: int | None,
          comments: int | None) -> tuple[int, int]:
    """(score 0-100 over the parts that are known, how many points were knowable)."""
    parts = {"lifetime": _points(lifetime, LIFETIME_POINTS), "pace": _points(pace, PACE_POINTS),
             "reactions": _points(reactions, REACTION_POINTS), "comments": _points(comments, COMMENT_POINTS)}
    known = sum(PART_MAX[k] for k, v in parts.items() if v is not None)
    got = sum(v for v in parts.values() if v is not None)
    return (round(100 * got / known) if known else 0), known


def tier(value: int, known: int, reactions: int | None = None, comments: int | None = None,
         too_new: bool = False, organisation: bool = False) -> str:
    if organisation:
        return "Organisation?"
    if known < MIN_KNOWN or (reactions is None and comments is None):
        return "Needs check"  # engagement unread: never written off
    r, c = reactions or 0, comments or 0
    if value >= STRONG_AT and (r >= STRONG_AUDIENCE[0] or c >= STRONG_AUDIENCE[1]):
        return "Strong"
    if value >= POSSIBLE_AT and (r >= POSSIBLE_AUDIENCE[0] or c >= POSSIBLE_AUDIENCE[1]):
        return "Possible"
    return "Needs check" if too_new else "Not a creator"


def same_person(slug: str, name: str, other_slug: str, other_name: str) -> bool:
    """True for the same profile link, or for a changed link that keeps LinkedIn's id at the end
    (pratheepan-~-5b6572250 -> pratheep-~-5b6572250), or a link with the same name on it."""
    a, b = normalize_slug(slug), normalize_slug(other_slug)
    if a == b:
        return True
    tail_a, tail_b = a.rsplit("-", 1)[-1], b.rsplit("-", 1)[-1]
    if tail_a == tail_b and len(tail_a) >= 6 and any(ch.isdigit() for ch in tail_a):
        return True
    na, nb = normalize_name(name), normalize_name(other_name)
    return bool(na) and na == nb


def looks_like_organisation(name: str) -> bool:
    words = re.findall(r"[a-z]+", (name or "").lower())
    return bool(words) and (bool(ORG_ANYWHERE & set(words)) or words[-1] in ORG_LAST_WORD)


def _max(*values) -> int | None:
    nums = [n for n in (to_int(v) for v in values) if n is not None]
    return max(nums) if nums else None


def _fetch(cand: Candidate, previous: dict, fetcher) -> tuple[PostPage | None, str | None, bool, bool]:
    """(page, its url, a load failed for a passing reason, some link was someone else's post)."""
    urls = list(dict.fromkeys([u for u in [previous.get("Post URL", "")] + cand.post_urls if u]))
    passing = other = False
    for url in urls:
        try:
            page = fetcher.post(url)
        except Unavailable:
            passing = True
            continue
        except NotFound:  # deleted, or behind a login: try their next post link
            continue
        if page.author.slug and not same_person(cand.slug, cand.name, page.author.slug, page.author.name):
            other = True
            continue
        return page, url, passing, other
    return None, None, passing, other


def build_row(cand: Candidate, previous: dict, page: PostPage | None, url: str | None, now: datetime,
              extra_flags: list[str] = (), scored: bool = True) -> dict:
    """The person's row from everything known so far. Never lowers best reactions/comments."""
    flags = list(extra_flags)
    points = list(cand.post_points)
    if (p := _point(previous.get("Pace Since (IST)", ""), previous.get("Pace Posts", ""))):
        points.append(p)
    if page and page.total_posts is not None:
        points.append((now, page.total_posts))
    weekly, pace_flag = pace(points)
    if pace_flag:
        flags.append(pace_flag)
    if page and page.total_posts is not None:
        lifetime, capped = page.total_posts, False
    else:
        floor = page.posts_at_least if page else None
        lifetime = _max(floor, cand.lifetime_posts, previous.get("Lifetime Posts"))
        capped = lifetime is not None and (floor == lifetime or str(previous.get("Lifetime Posts", "")).endswith("+"))
    if page and page.author.slug and normalize_slug(page.author.slug) != cand.slug:
        flags.append(f"profile link changed to linkedin.com/in/{page.author.slug}")

    latest_r = page.reactions if page else None
    latest_c = page.comments if page else None
    own_slugs = {cand.slug} | ({normalize_slug(page.author.slug)} if page and page.author.slug else set())
    own_related = [r.reactions for r in (page.related if page else [])
                   if r.author.slug and normalize_slug(r.author.slug) in own_slugs]
    best_r = _max(previous.get("Best Reactions"), latest_r, *own_related)
    best_c = _max(previous.get("Best Comments"), latest_c)
    too_new = bool(page and page.published and now - page.published < timedelta(days=FRESH_POST_DAYS)
                   and to_int(previous.get("Best Reactions")) is None)
    if too_new:
        flags.append(f"latest post under {FRESH_POST_DAYS} days old")
    organisation = looks_like_organisation(cand.name)
    if organisation:
        flags.append("looks like an organisation")

    value, known = score(lifetime, weekly, best_r, best_c)
    followers = page.author.followers if page and page.author.followers else cand.followers
    since = min(points, key=lambda p: p[0]) if points else None
    row = {
        "Name": cand.name, "Profile URL": cand.profile_url, "Niche": cand.niche,
        "Followers": followers if followers is not None else "",
        "Tier": tier(value, known, best_r, best_c, too_new, organisation),
        "Score": value if known >= MIN_KNOWN else "",
        "Lifetime Posts": "" if lifetime is None else f"{lifetime}+" if capped else lifetime,
        "Posts / Week": weekly if weekly is not None else "",
        "Best Reactions": best_r if best_r is not None else "",
        "Best Comments": best_c if best_c is not None else "",
        "Latest Reactions": latest_r if latest_r is not None else "",
        "Latest Comments": latest_c if latest_c is not None else "",
        "Engagement %": round(100 * best_r / followers, 2) if best_r is not None and followers else "",
        "Flags": "; ".join(dict.fromkeys(flags)),
        "Post URL": url or previous.get("Post URL") or (cand.post_urls[0] if cand.post_urls else ""),
        "Pace Since (IST)": fmt_time(since[0]) if since else "",
        "Pace Posts": since[1] if since else "",
        "Checks": (to_int(previous.get("Checks")) or 0) + (1 if scored else 0),
        "Last Scored (IST)": fmt_time(now) if scored else previous.get("Last Scored (IST)", ""),
        "Last Error": "", "Retry After (IST)": "",
    }
    return {**row, "Rank": TIER_RANK[row["Tier"]]}


def check(cand: Candidate, previous: dict, fetcher, now: datetime) -> dict:
    """Open the person's post page and score them. Raises Blocked if LinkedIn shuts us out."""
    page, url, passing, other = _fetch(cand, previous, fetcher)
    if page is None and passing:
        # A load failed for a passing reason: keep what we had, try again in a few hours.
        kept = dict(previous) if previous.get("Tier") else build_row(cand, previous, None, None, now, scored=False)
        return {**kept, "Last Error": "post page didn't load; will try again",
                "Retry After (IST)": fmt_time(now + timedelta(hours=config.CREATOR_RETRY_HOURS))}
    flags = []
    if other:
        flags.append("a post link was someone else's post")
    if page is None:
        flags.append("no post page could be opened")
    return build_row(cand, previous, page, url, now, flags)


def due(cands: list[Candidate], rows: dict[str, dict], now: datetime, limit: int) -> list[Candidate]:
    """Who to check this run: never checked (best estimate first), then retries, then the stalest."""
    epoch = datetime.min.replace(tzinfo=timezone.utc)
    new, retry, stale = [], [], []
    for c in cands:
        r = rows.get(c.slug, {})
        retry_at = parse_time(r.get("Retry After (IST)", ""))
        scored = parse_time(r.get("Last Scored (IST)", ""))
        if retry_at and retry_at > now:
            continue
        if retry_at:
            retry.append((retry_at, c))
        elif not scored:
            new.append((-(to_int(r.get("Score")) or -1), c))
        elif now - scored >= timedelta(days=config.CREATOR_RESCORE_DAYS):
            stale.append((scored or epoch, c))
    ordered = [c for _, c in sorted(new, key=lambda x: x[0])] + \
              [c for _, c in sorted(retry, key=lambda x: x[0])] + \
              [c for _, c in sorted(stale, key=lambda x: x[0])]
    return ordered[:limit]


# --- sheet I/O ------------------------------------------------------------------------------------

def read_tab(sheet: Sheet, title: str) -> list[dict]:
    """Read-only: a tab's rows as dicts keyed by its own header row (never changes the tab)."""
    values = _retry(sheet.book.worksheet(title).get_all_values)
    if not values:
        return []
    head = values[0]
    return [dict(zip(head, row + [""] * (len(head) - len(row)))) for row in values[1:] if any(row)]


def write(tab: Tab, updates: dict[str, dict]) -> int:
    """Write rows by profile link. Looks the rows up just before writing, so a tab that was
    sorted or filtered by hand in the meantime still gets each value in the right row."""
    if not updates:
        return 0
    col = tab.headers.index("Profile URL") + 1
    where: dict[str, int] = {}
    for i, url in enumerate(_retry(tab.ws.col_values, col)[1:], start=2):
        s = slug_of(url)
        if s and s not in where:
            where[s] = i
    # Re-add rows deleted by hand mid-run, but only whole rows (a partial update has no profile link).
    missing = [values for s, values in updates.items() if s not in where and values.get("Profile URL")]
    for s, values in updates.items():
        if s in where:
            tab.set(where[s], {k: v for k, v in values.items() if k in tab.headers})
    tab.flush()
    if missing:
        log.warning("%d rows were deleted from the tab during the run; adding them back", len(missing))
        tab.append(missing)
    return len(updates)


def sort(tab: Tab) -> None:
    """Best first: by Rank, then Score. The range is counted from the data, because the
    worksheet object remembers the size the tab had when it was opened."""
    rows = len(_retry(tab.ws.col_values, tab.headers.index("Profile URL") + 1))
    if rows < 3:
        return
    last = rowcol_to_a1(rows, len(tab.headers))
    _retry(tab.ws.sort, (tab.headers.index("Rank") + 1, "asc"), (tab.headers.index("Score") + 1, "des"),
           range=f"A2:{last}")


def existing_rows(tab: Tab) -> dict[str, dict]:
    rows, dupes = {}, 0
    for r in tab.rows():
        s = slug_of(r.get("Profile URL", ""))
        if s and s in rows:
            dupes += 1
        elif s:
            rows[s] = r
    if dupes:
        log.warning("%d repeated people in %s; only the first row of each is used", dupes, tab.ws.title)
    return rows


def refresh_estimates(tab: Tab, cands: list[Candidate], rows: dict[str, dict], now: datetime) -> dict[str, dict]:
    """Rows nobody has checked yet get today's estimate from the sheet data (no page visits).
    Only cells whose value changed are written. Returns the rows as they now are."""
    changed: dict[str, dict] = {}
    for c in cands:
        r = rows.get(c.slug)
        if not r or r.get("Last Scored (IST)") or r.get("Retry After (IST)"):
            continue
        estimate = build_row(c, {}, None, None, now, scored=False)
        diff = {k: v for k, v in estimate.items() if str(v) != str(r.get(k, ""))}
        if diff:
            changed[c.slug] = diff
    if changed:
        write(tab, changed)
        log.info("refreshed the estimate on %d unchecked rows", len(changed))
    return {s: ({**r, **{k: str(v) for k, v in changed[s].items()}} if s in changed else r) for s, r in rows.items()}


def run(limit: int, dry_run: bool) -> int:
    started, now = time.time(), datetime.now(timezone.utc)
    sheet = Sheet(config.V2_SHEET_ID)
    source = read_tab(sheet, config.CREATOR_SOURCE_TAB)
    cands, skipped = candidates(source, read_tab(sheet, config.QUALIFIED_TAB), read_tab(sheet, config.BULK_TAB))
    log.info("%d people in %s (%s skipped: %s)", len(cands), config.CREATOR_SOURCE_TAB, sum(skipped.values()),
             skipped or "none")

    out = None if dry_run else sheet.tab(config.CREATOR_SCORE_TAB, SCORE_HEADERS)
    rows = existing_rows(out) if out else {}
    fresh = [build_row(c, {}, None, None, now, scored=False) for c in cands if c.slug not in rows]
    if fresh and out:
        for i in range(0, len(fresh), 2000):
            out.append(fresh[i:i + 2000])
        log.info("added %d new people with a first estimate from the sheet alone", len(fresh))
        rows = existing_rows(out)

    if out:
        rows = refresh_estimates(out, cands, rows, now)
    todo = due(cands, rows, now, limit)
    log.info("%d people due for a check this run", len(todo))
    fetcher, updates, tally, blocked = Fetcher(), {}, Counter(), False
    for i, c in enumerate(todo, 1):
        if time.time() - started > config.CREATOR_MAX_MINUTES * 60:
            log.info("time budget used; %d left for the next run", len(todo) - i + 1)
            break
        try:
            row = check(c, rows.get(c.slug, {}), fetcher, datetime.now(timezone.utc))
        except Blocked as e:
            log.error("LinkedIn stopped serving pages (%s); %d left for the next run", e, len(todo) - i + 1)
            blocked = True
            break
        updates[c.slug] = row
        tally["retry later" if row["Last Error"] else row["Tier"]] += 1
        log.info("[%d/%d] %s -> %s %s (%s)", i, len(todo), c.name, row["Tier"], row["Score"],
                 row["Last Error"] or row["Flags"] or "ok")
        if out and len(updates) >= config.CREATOR_FLUSH_EVERY:
            write(out, updates)
            updates = {}
    if out:
        write(out, updates)
        sort(out)
        overall = Counter(r.get("Tier", "") for r in existing_rows(out).values())
        log.info("whole tab now: %s", dict(overall))
    log.info("done: %s; %d pages opened in %.0f min", dict(tally), fetcher.requests_made,
             (time.time() - started) / 60)
    return 2 if blocked and not sum(tally.values()) else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limit", type=int, default=config.CREATOR_CHECK_PER_RUN)
    ap.add_argument("--dry-run", action="store_true", help="read and check, but write nothing")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    sys.exit(run(args.limit, args.dry_run))


if __name__ == "__main__":
    main()
