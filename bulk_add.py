"""Discover new US LinkedIn creators and append them to the Bulk tab as Pending.

How discovery works (no login, no paid APIs):
  1. Pick this run's focus niches: the ones with the fewest creators in the Qualified tab
     (HR, VCs, engineering, finance, ... see creator_search/niches.py), so every niche and
     role keeps getting filled.
  2. Seed the crawl with posts from those niches: LinkedIn's public topic pages for them,
     crawl-queue posts tagged with them, a best-effort web search, plus the general seeds
     (queue left by the previous run, qualified creators' posts, post links in the sheet,
     seeds.txt).
  3. Fetch each public post page. Every page lists ~10 "More Relevant Posts" (usually on
     the same topic), each with its author's profile link. The link's subdomain gives the
     author's country (www == US); the post's activity id gives its exact time.
  4. Follow posts by US people not yet in the sheet: focus niches first, alternating
     between fresh posts and new people. Each fetched page gives the author's exact
     follower count and total post count. People who are US, have 5K-100K followers and
     aren't already anywhere in the spreadsheet are added, until we have --target rows.

verify.py then re-checks every Pending row independently (including activity).
"""
from __future__ import annotations

import argparse
import heapq
import logging
import random
import sys
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from creator_search import config, niches, rules
from creator_search.linkedin import (Blocked, Fetcher, NotFound, activity_id_from_url, activity_time,
                                     canonical_post_url, normalize_slug, profile_url, topic_post_urls)
from creator_search.search import recent_post_urls
from creator_search.sheets import PROFILE_RE, Sheet, fmt_date, fmt_time, normalize_name, now_ist, parse_time

log = logging.getLogger("bulk_add")

# What a queued post is. Stored as these numbers in the Crawl Queue tab.
K_CANDIDATE, K_SEED, K_KNOWN, K_OTHER = 0, 1, 2, 3


class Frontier:
    """Which post page to fetch next.

    Four queues are taken in turn (focus fresh, other fresh, focus older, other older), so
    focus niches get half the pages without starving everything else. "Fresh" and "older":
      fresh lane:     posts from the last 14 days (a page's related posts are usually from
                      around the same time, so this keeps the crawl anchored to the present);
      candidate lane: older posts by US people we haven't seen yet (more creators per page;
                      verify.py then checks their activity via their post count).
    Within a lane, by tier: focus-niche candidates > focus-niche seeds / known people >
    other candidates > other seeds > other known people > non-US / company pages; then more
    reactions first (bigger audiences get more reactions); then newest.
    """
    FOCUS_TIERS = 2  # tiers 0 and 1

    def __init__(self, now: datetime, focus: set[str] = frozenset()):
        self.now = now
        self.focus = set(focus)
        # [focus fresh, focus older, rest fresh, rest older]
        self.heaps: list[list[tuple]] = [[], [], [], []]
        self.entry: dict[str, tuple[int, int, str]] = {}  # url -> (tier, kind, niche)
        self.visited: set[str] = set()
        self._n = 0
        self._turn = 0

    def tier(self, kind: int, niche: str) -> int:
        if kind == K_OTHER:
            return 5
        in_focus = niche in self.focus
        if kind == K_CANDIDATE:
            return 0 if in_focus else 2
        if in_focus:
            return 1
        return 3 if kind == K_SEED else 4

    def push(self, url: str, kind: int, reactions: int = 0, niche: str = ""):
        url = canonical_post_url(url)
        tier = self.tier(kind, niche)
        if url in self.visited or (url in self.entry and self.entry[url][0] <= tier):
            return
        aid = activity_id_from_url(url)
        fresh = bool(aid) and rules.recent(activity_time(aid), self.now)
        if not fresh and kind == K_OTHER:
            return  # old non-US/company pages lead nowhere useful
        self.entry[url] = (tier, kind, niche)
        self._n += 1
        heapq.heappush(self._heap(tier, fresh), (tier, -reactions, -int(aid or 0), self._n, url))

    def _heap(self, tier: int, fresh: bool) -> list:
        return self.heaps[(0 if tier < self.FOCUS_TIERS else 2) + (0 if fresh else 1)]

    def pop(self, capped_niches: frozenset = frozenset()) -> str | None:
        """Candidates from niches that already hit this run's cap are demoted to 'known'."""
        f_fresh, f_older, r_fresh, r_older = self.heaps
        # Whose turn it is, then fallbacks: same group's other lane first, then the other group.
        orders = [[f_fresh, f_older, r_fresh, r_older], [r_fresh, r_older, f_fresh, f_older],
                  [f_older, f_fresh, r_older, r_fresh], [r_older, r_fresh, f_older, f_fresh]]
        order = orders[self._turn % len(orders)]
        self._turn += 1
        for heap in order:
            while heap:
                tier, neg_reactions, neg_aid, _, url = heapq.heappop(heap)
                if url in self.visited or self.entry.get(url, (None,))[0] != tier:
                    continue  # already fetched, or superseded by a better-tier push
                _, kind, niche = self.entry[url]
                if kind == K_CANDIDATE and niche in capped_niches:
                    demoted = self.tier(K_KNOWN, niche)
                    self.entry[url] = (demoted, K_KNOWN, niche)
                    self._n += 1
                    fresh = heap is f_fresh or heap is r_fresh
                    heapq.heappush(self._heap(demoted, fresh), (demoted, neg_reactions, neg_aid, self._n, url))
                    continue
                self.visited.add(url)
                del self.entry[url]
                return url
        return None

    def __len__(self):
        return len(self.entry)

    def leftovers(self, limit: int) -> list[tuple[str, int, str]]:
        out = []
        for _, entry in sorted((i, e) for i, heap in enumerate(self.heaps) for e in heap):
            tier, *_, url = entry
            if self.entry.get(url, (None,))[0] == tier:
                out.append((url, self.entry[url][1], self.entry[url][2]))
                if len(out) >= limit:
                    break
        return out


def tag(text: str, url: str) -> str:
    return niches.classify(f"{text} {niches.text_from_post_url(url)}")


def load_seed_file() -> list[str]:
    path = Path(__file__).with_name("seeds.txt")
    if not path.exists():
        return []
    return [line.strip() for line in path.read_text().splitlines()
            if line.strip() and not line.startswith("#")]


def run(target: int, max_pages: int, use_sheet: bool, use_search: bool, min_gap_minutes: int = 0) -> int:
    sheet = Sheet() if use_sheet else None
    known_slugs: set[str] = set()
    known_names: set[str] = set()
    qualified_rows: dict[str, dict] = {}
    niche_counts: Counter = Counter()
    now = datetime.now(timezone.utc)
    queue_rows: list[dict] = []
    sheet_posts: list[str] = []

    if sheet:
        bulk, qual = sheet.bulk(), sheet.qualified()
        if min_gap_minutes:
            last = max((t for r in bulk.rows()[-300:] if (t := parse_time(r.get("Added At (IST)", "")))),
                       default=None)
            if last and now - last < timedelta(minutes=min_gap_minutes):
                log.info("last bulk add was at %s IST, under %d minutes ago; skipping this run",
                         fmt_time(last), min_gap_minutes)
                return 0
        known_slugs, known_names, sheet_posts = sheet.known_creators()
        for row in qual.rows():
            niche_counts[row.get("Niche", "")] += 1
            m = PROFILE_RE.search(row.get("Profile URL", ""))
            if m:
                qualified_rows[normalize_slug(m.group(1))] = row
        queue_rows = sheet.queue().rows()

    focus = niches.focus_niches(niche_counts, config.FOCUS_NICHES_PER_RUN, rotation=int(time.time() // 3600))
    log.info("focus niches this run: %s", ", ".join(focus))
    frontier = Frontier(now, set(focus))
    fetcher = Fetcher()

    # Seeds for the focus niches first: LinkedIn topic pages and web search.
    for niche in focus:
        topics = niches.NICHES[niche]["topics"]
        if topics:
            for url in topic_post_urls(fetcher, random.choice(topics), subtopics=config.TOPIC_SUBPAGES):
                frontier.push(url, K_SEED, niche=niche)
    if use_search:
        # Up to 2 phrases per focus niche; every niche's first phrase goes before any second one.
        picks = {n: random.sample(niches.NICHES[n]["search"], min(2, len(niches.NICHES[n]["search"])))
                 for n in focus}
        phrase_niche = {picks[n][i]: n for i in range(2) for n in focus if i < len(picks[n])}
        found = recent_post_urls(list(phrase_niche)[: config.SEARCH_QUERIES_PER_RUN])
        for phrase, urls in found.items():
            for url in urls:
                frontier.push(url, K_SEED, niche=phrase_niche[phrase])
    # General seeds.
    for row in queue_rows:
        if row.get("Post URL"):
            kind = int(row.get("Priority") or K_KNOWN)
            kind = min(max(kind, K_CANDIDATE), K_OTHER)
            frontier.push(row["Post URL"], kind, niche=row.get("Niche") or tag("", row["Post URL"]))
    for row in qualified_rows.values():
        if row.get("Latest Post URL"):
            frontier.push(row["Latest Post URL"], K_SEED, niche=row.get("Niche", ""))
    for url in sheet_posts + load_seed_file():
        frontier.push(url, K_SEED, niche=tag("", url))
    log.info("starting with %d queued posts", len(frontier))

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
        return len(fresh) + len(older) + len(overflow) >= target and pages >= max_pages // 2

    while not done() and pages < max_pages:
        capped = frozenset(n for n, c in per_niche.items() if c >= config.NICHE_CAP_PER_RUN)
        url = frontier.pop(capped)
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
            log.info("progress: %d pages, %d fresh + %d older + %d spare found, %d posts queued",
                     pages, len(fresh), len(older), len(overflow), len(frontier))
        a = page.author
        saw(a.slug, page.published, page.url)

        for r in page.related:
            ra = r.author
            saw(ra.slug, r.published, r.url)
            if ra.is_person and ra.is_us:
                known = ra.slug in known_slugs or ra.slug in decided
                frontier.push(r.url, K_KNOWN if known else K_CANDIDATE, r.reactions, tag(r.text, r.url))
            else:
                frontier.push(r.url, K_OTHER, niche=tag(r.text, r.url))

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
        niche = tag(page.text, page.url)
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
            log.info("[spare %d] %s | %s (niche already has %d this run)", len(overflow), a.name, niche,
                     per_niche[niche])
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
    log.info("niches added: %s", dict(Counter(r["Niche"] for r in added).most_common()))

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
    ap.add_argument("--min-gap-minutes", type=int, default=0,
                    help="skip the run if creators were added less than this many minutes ago "
                         "(the hourly schedule has a backup trigger; this stops it doubling up)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    sys.exit(run(args.target, args.max_pages, not args.no_sheet, not args.no_search, args.min_gap_minutes))


if __name__ == "__main__":
    main()
