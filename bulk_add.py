"""Discover new US LinkedIn creators and append them to the Bulk tab as Pending.

How discovery works (no login, no paid APIs):
  1. Start from seed post URLs: the crawl queue left by the previous run, recent posts of
     already-qualified creators, a best-effort free web search, and seeds.txt.
  2. Fetch each public post page. Every page lists ~10 "More Relevant Posts", each with
     its author's profile link. The link's subdomain tells us the author's country
     (www == US), and the post's activity id tells us exactly when it was posted.
  3. Follow posts by US people not yet in the sheet, freshest first. Each fetched page
     gives the author's exact follower count and total post count. People who are US,
     have 5K-100K followers and aren't already anywhere in the spreadsheet are added,
     preferring those with a post in the last 14 days, until we have --target rows.

verify.py then re-checks every Pending row independently (including activity).
"""
from __future__ import annotations

import argparse
import heapq
import logging
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from creator_search import config, niches, rules
from creator_search.linkedin import (Blocked, Fetcher, NotFound, activity_id_from_url, activity_time,
                                     canonical_post_url, normalize_slug, profile_url)
from creator_search.search import recent_post_urls
from creator_search.sheets import PROFILE_RE, Sheet, fmt_date, fmt_time, normalize_name, now_ist, parse_time

log = logging.getLogger("bulk_add")

# Crawl order: newest posts first (a page's related posts are usually from around the same
# time, so staying near the present keeps finding active people), and within the same age
# band: unknown US people > seeds > US people already known > non-US / company pages, and
# then by reactions (bigger audiences get more reactions, so 5K+ accounts surface sooner).
T_CANDIDATE, T_SEED, T_KNOWN, T_OTHER = 0, 1, 2, 3
AGE_BANDS_DAYS = (config.MAX_POST_AGE_DAYS, 45, 120)


class Frontier:
    def __init__(self, now: datetime):
        self.now = now
        self.heap: list[tuple] = []
        self.tier: dict[str, int] = {}
        self.visited: set[str] = set()
        self._n = 0

    def _age_band(self, aid: str | None) -> int:
        if not aid:
            return len(AGE_BANDS_DAYS)
        days = (self.now - activity_time(aid)).days
        return next((i for i, d in enumerate(AGE_BANDS_DAYS) if days <= d), len(AGE_BANDS_DAYS))

    def push(self, url: str, tier: int, reactions: int = 0):
        url = canonical_post_url(url)
        if url in self.visited or self.tier.get(url, 99) <= tier:
            return
        self.tier[url] = tier
        aid = activity_id_from_url(url)
        self._n += 1
        heapq.heappush(self.heap, (self._age_band(aid), tier, -reactions, -int(aid or 0), self._n, url))

    def pop(self) -> str | None:
        while self.heap:
            _, tier, *_, url = heapq.heappop(self.heap)
            if url in self.visited or self.tier.get(url) != tier:
                continue  # already fetched, or superseded by a better-tier push
            self.visited.add(url)
            del self.tier[url]
            return url
        return None

    def __len__(self):
        return len(self.tier)

    def leftovers(self, limit: int) -> list[tuple[str, int]]:
        out = []
        for _, tier, *_, url in sorted(self.heap):
            if self.tier.get(url) == tier:
                out.append((url, tier))
                if len(out) >= limit:
                    break
        return out


def load_seed_file() -> list[str]:
    path = Path(__file__).with_name("seeds.txt")
    if not path.exists():
        return []
    return [line.strip() for line in path.read_text().splitlines()
            if line.strip() and not line.startswith("#")]


def search_terms_for_this_run() -> list[str]:
    now = now_ist()
    slot = now.timetuple().tm_yday * 3 + now.hour // 8
    n = config.SEARCH_QUERIES_PER_RUN
    terms = niches.SEARCH_TERMS
    start = (slot * n) % len(terms)
    return [terms[(start + i) % len(terms)] for i in range(n)]


def run(target: int, max_pages: int, use_sheet: bool, use_search: bool) -> int:
    sheet = Sheet() if use_sheet else None
    known_slugs: set[str] = set()
    known_names: set[str] = set()
    qualified_rows: dict[str, dict] = {}
    now = datetime.now(timezone.utc)
    frontier = Frontier(now)

    if sheet:
        bulk, qual = sheet.bulk(), sheet.qualified()
        known_slugs, known_names, sheet_posts = sheet.known_creators()
        for url in sheet_posts:
            frontier.push(url, T_SEED)
        for row in qual.rows():
            m = PROFILE_RE.search(row.get("Profile URL", ""))
            if m:
                qualified_rows[normalize_slug(m.group(1))] = row
        for row in sheet.queue().rows():
            if row.get("Post URL"):
                tier = int(row.get("Priority") or T_KNOWN)
                frontier.push(row["Post URL"], min(max(tier, T_CANDIDATE), T_OTHER))
        for row in qualified_rows.values():
            if row.get("Latest Post URL"):
                frontier.push(row["Latest Post URL"], T_SEED)
    if use_search:
        for url in recent_post_urls(search_terms_for_this_run()):
            frontier.push(url, T_SEED)
    for url in load_seed_file():
        frontier.push(url, T_SEED)
    log.info("starting with %d queued posts", len(frontier))

    fetcher = Fetcher()
    fresh: list[dict] = []  # newest post found is within 14 days
    older: list[dict] = []  # fits otherwise; verify.py watches their post count for new posts
    overflow: list[dict] = []  # fits, but its niche hit the per-run cap
    per_niche: Counter = Counter()
    decided: set[str] = set()  # authors evaluated this run
    newest: dict[str, tuple[datetime, str]] = {}  # author -> newest post seen anywhere this run
    pages = 0
    blocked = False

    def saw(slug: str, published: datetime | None, url: str):
        if slug and published and (slug not in newest or published > newest[slug][0]):
            newest[slug] = (published, url)

    def done() -> bool:
        if len(fresh) >= target:
            return True
        return len(fresh) + len(older) >= target and pages >= max_pages // 2

    while not done() and pages < max_pages:
        url = frontier.pop()
        if not url:
            log.info("frontier exhausted")
            break
        try:
            page = fetcher.post(url)
        except NotFound:
            continue
        except Blocked as e:
            log.error("LinkedIn stopped serving pages: %s", e)
            blocked = True
            break
        pages += 1
        if pages % 25 == 0:
            log.info("progress: %d pages, %d fresh + %d older found, %d posts queued",
                     pages, len(fresh), len(older), len(frontier))
        a = page.author
        saw(a.slug, page.published, page.url)

        for r in page.related:
            ra = r.author
            saw(ra.slug, r.published, r.url)
            if ra.is_person and ra.is_us:
                known = ra.slug in known_slugs or ra.slug in decided
                frontier.push(r.url, T_KNOWN if known else T_CANDIDATE, r.reactions)
            else:
                frontier.push(r.url, T_OTHER)

        if not a.is_person or not a.slug or a.slug in decided:
            continue
        decided.add(a.slug)
        if a.slug in known_slugs or normalize_name(a.name) in known_names:
            continue
        if rules.profile_failures(page):
            continue
        if a.slug not in newest:
            continue  # no post date at all
        last_seen, last_url = newest[a.slug]
        if not rules.recent(last_seen, now, days=config.MAX_SEEN_POST_AGE_DAYS):
            continue
        niche = niches.classify(page.text)
        record = {
            "Added At (IST)": fmt_time(now_ist()),
            "Name": a.name,
            "Profile URL": profile_url(a.slug),
            "Niche": niche,
            "Followers": a.followers,
            "Total Posts": page.total_posts if page.total_posts is not None else "",
            "Newest Post Seen": fmt_date(last_seen),
            "Source Post URL": last_url,
            "Location Signal": rules.location_signal(page),
            "Status": "Pending",
            "_slug": a.slug,
        }
        if per_niche[niche] >= config.NICHE_CAP_PER_RUN:
            overflow.append(record)
            continue
        per_niche[niche] += 1
        (fresh if rules.recent(last_seen, now) else older).append(record)
        log.info("[fresh %d, older %d / %d] %s | %s | %s followers | newest post %s", len(fresh), len(older),
                 target, a.name, niche, f"{a.followers:,}", record["Newest Post Seen"])

    # Fresh first, then the most recently active of the rest.
    older.sort(key=lambda r: r["Newest Post Seen"], reverse=True)
    overflow.sort(key=lambda r: r["Newest Post Seen"], reverse=True)
    added = (fresh + older + overflow)[:target]
    for r in added:  # a newer post by the same person may have turned up later in the crawl
        last_seen, last_url = newest[r.pop("_slug")]
        r["Newest Post Seen"], r["Source Post URL"] = fmt_date(last_seen), last_url
    log.info("fetched %d pages, %d new creators (%d with a post in the last %d days)", pages, len(added),
             sum(1 for r in added if r in fresh), config.MAX_POST_AGE_DAYS)

    if sheet:
        bulk.append(added)
        updates = 0
        for slug, row in qualified_rows.items():
            if slug not in newest:
                continue
            published, url = newest[slug]
            last = parse_time(row.get("Last Activity", ""))
            if last is None or published > last:
                qual.set(row["_row"], {"Last Activity": fmt_date(published), "Latest Post URL": url})
                updates += 1
        qual.flush()
        if updates:
            log.info("refreshed latest post for %d qualified creators", updates)
        sheet.replace_queue(frontier.leftovers(config.QUEUE_MAX))
    else:
        for r in added:
            print(r)

    if blocked and not added:
        return 1
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--target", type=int, default=config.BULK_TARGET)
    ap.add_argument("--max-pages", type=int, default=config.MAX_PAGES_PER_BULK_RUN)
    ap.add_argument("--no-sheet", action="store_true", help="dry run: print results, don't touch the sheet")
    ap.add_argument("--no-search", action="store_true", help="skip best-effort web search seeding")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    sys.exit(run(args.target, args.max_pages, not args.no_sheet, not args.no_search))


if __name__ == "__main__":
    main()
