"""Discover new US LinkedIn creators and append them to the Bulk tab as Pending.

How discovery works (no login, no paid APIs):
  1. Niche lanes. Each run takes the next few niches in a fixed rotation through every niche
     and role in creator_search/niches.py (HR, recruiting, VCs, engineering, finance, ...),
     so all of them are covered several times a day. Each of those niches gets its own lane
     with an equal share of the run's page budget.
  2. A lane starts from fresh posts in its niche: a web search for the niche's phrases (past
     week), recent posts from LinkedIn's public topic pages for it, and crawl-queue posts
     saved under that niche by earlier runs.
  3. Fetch each public post page. Every page lists ~10 "More Relevant Posts" (LinkedIn picks
     them by topic), each with its author's profile link. The link's subdomain gives the
     author's country (www == US); the post's activity id gives its exact time. A lane follows
     those related posts for a few hops, so it stays inside its niche.
  4. Each fetched page gives the author's exact follower count and total post count. People who
     are US, have 5K-100K followers, have a post from the last 30 days and aren't already
     anywhere in the spreadsheet are added.
  5. A general lane (the saved crawl queue, qualified creators' posts, post links in the sheet,
     seeds.txt) uses whatever budget the niche lanes don't, until we have --target rows.

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


def rank(kind: int, fresh: bool) -> int | None:
    """Fetch order within a lane (lower first); None = not worth fetching.

    Fresh posts (last 14 days) lead to people who are visibly active, and their related posts are
    fresh too. Unknown US people are where new creators come from; non-US / company pages only
    help reach more related posts.
    """
    if kind in (K_CANDIDATE, K_SEED):
        return 0 if fresh else (1 if kind == K_CANDIDATE else 3)
    if kind == K_KNOWN:
        return 2 if fresh else 4
    return 3 if fresh else None  # K_OTHER


class Lane:
    """A queue of post pages to fetch, with a page budget. Ties go to more reactions (bigger
    audiences get more reactions, so 5K+ accounts surface sooner), then to newer posts."""

    def __init__(self, niche: str | None, budget: float, now: datetime):
        self.niche = niche  # None for the general lane
        self.budget = budget
        self.now = now
        self.used = 0
        self.found = 0
        self.heap: list[tuple] = []
        self.entry: dict[str, tuple[int, int, int]] = {}  # url -> (rank, kind, depth)
        self._n = 0

    def push(self, url: str, kind: int, depth: int = 0, reactions: int = 0) -> bool:
        url = canonical_post_url(url)
        aid = activity_id_from_url(url)
        r = rank(kind, bool(aid) and rules.recent(activity_time(aid), self.now))
        if r is None or (url in self.entry and self.entry[url][0] <= r):
            return False
        self.entry[url] = (r, kind, depth)
        self._n += 1
        heapq.heappush(self.heap, (r, -reactions, -int(aid or 0), self._n, url))
        return True

    def pop(self, visited: set[str]) -> tuple[str, int, int] | None:
        while self.heap:
            r, *_, url = heapq.heappop(self.heap)
            entry = self.entry.get(url)
            if not entry or entry[0] != r:
                continue  # superseded by a better push of the same post
            del self.entry[url]
            if url in visited:
                continue  # already fetched by another lane
            return url, entry[1], entry[2]
        return None

    def has_work(self) -> bool:
        return self.used < self.budget and bool(self.entry)

    def leftovers(self) -> list[tuple[int, str, int]]:
        return [(entry[0], url, entry[1]) for url, entry in self.entry.items()]


def tag(text: str, url: str) -> str:
    return niches.classify(f"{text} {niches.text_from_post_url(url)}")


def load_seed_file() -> list[str]:
    path = Path(__file__).with_name("seeds.txt")
    if not path.exists():
        return []
    return [line.strip() for line in path.read_text().splitlines()
            if line.strip() and not line.startswith("#")]


def search_phrases(lane_niches: list[str]) -> dict[str, str]:
    """phrase -> niche: up to 2 per niche, every niche's first phrase before any second one."""
    picks = {n: random.sample(niches.NICHES[n]["search"], min(2, len(niches.NICHES[n]["search"])))
             for n in lane_niches}
    return {picks[n][i]: n for i in range(2) for n in lane_niches if i < len(picks[n])}


def run(target: int, max_pages: int, use_sheet: bool, use_search: bool, min_gap_minutes: int = 0) -> int:
    sheet = Sheet() if use_sheet else None
    known_slugs: set[str] = set()
    known_names: set[str] = set()
    qualified_rows: dict[str, dict] = {}
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
            m = PROFILE_RE.search(row.get("Profile URL", ""))
            if m:
                qualified_rows[normalize_slug(m.group(1))] = row
        queue_rows = sheet.queue().rows()

    lane_niches = niches.rotation(config.NICHES_PER_RUN, slot=int(time.time() // 3600))
    log.info("niche lanes this run: %s", ", ".join(lane_niches))
    lane_budget = max_pages * config.NICHE_LANE_SHARE / max(len(lane_niches), 1)
    lanes = {n: Lane(n, lane_budget, now) for n in lane_niches}
    general = Lane(None, float("inf"), now)
    fetcher = Fetcher()

    # Seed the niche lanes: web search (fresh posts), LinkedIn topic pages, saved queue posts.
    if use_search:
        phrase_niche = search_phrases(lane_niches)
        for phrase, urls in recent_post_urls(list(phrase_niche)[: config.SEARCH_QUERIES_PER_RUN]).items():
            for url in urls:
                lanes[phrase_niche[phrase]].push(url, K_SEED)
    for niche, lane in lanes.items():
        topics = niches.NICHES[niche]["topics"]
        if topics:
            for url in topic_post_urls(fetcher, random.choice(topics), subtopics=config.TOPIC_SUBPAGES):
                lane.push(url, K_SEED)
    # Everything else goes to the general lane (or to a niche lane if it's tagged with one).
    for row in queue_rows:
        if row.get("Post URL"):
            kind = min(max(int(row.get("Priority") or K_KNOWN), K_CANDIDATE), K_OTHER)
            lanes.get(row.get("Niche"), general).push(row["Post URL"], kind)
    for row in qualified_rows.values():
        if row.get("Latest Post URL"):
            lanes.get(row.get("Niche"), general).push(row["Latest Post URL"], K_KNOWN)
    for url in sheet_posts + load_seed_file():
        general.push(url, K_SEED)
    for lane in [*lanes.values(), general]:
        log.info("lane %s: %d posts to start from", lane.niche or "general", len(lane.entry))

    found: list[dict] = []
    decided: set[str] = set()  # authors evaluated this run
    newest: dict[str, tuple[datetime, str]] = {}  # author -> newest post seen anywhere this run
    visited: set[str] = set()
    pages = 0
    blocked = False
    turn = 0

    def saw(slug: str, published: datetime | None, url: str):
        if slug and published and (slug not in newest or published > newest[slug][0]):
            newest[slug] = (published, url)

    def next_page() -> tuple[Lane, str, int, int] | None:
        nonlocal turn
        order = list(lanes.values())
        for i in range(len(order)):  # round-robin over niche lanes that still have budget and work
            lane = order[(turn + i) % len(order)]
            if lane.has_work() and (item := lane.pop(visited)):
                turn = (turn + i + 1) % len(order)
                return (lane, *item)
        while general.entry or any(lane.entry for lane in lanes.values()):
            if (item := general.pop(visited)):
                return (general, *item)
            # General lane is dry: hand over what the niche lanes didn't get to.
            for lane in lanes.values():
                for _, url, kind in lane.leftovers():
                    general.push(url, kind)
                lane.entry.clear()
        return None

    while len(found) < target and pages < max_pages:
        nxt = next_page()
        if not nxt:
            log.info("nothing left to crawl")
            break
        lane, url, kind, depth = nxt
        visited.add(url)
        try:
            page = fetcher.post(url)
        except NotFound:
            continue
        except Blocked as e:
            log.error("LinkedIn stopped serving pages: %s", e)
            blocked = True
            break
        pages += 1
        lane.used += 1
        if pages % 25 == 0:
            log.info("progress: %d pages, %d found", pages, len(found))
        a = page.author
        saw(a.slug, page.published, page.url)

        # Related posts stay in this niche's lane for a few hops; after that they go to the general lane.
        follow = lane if lane.niche and depth < config.LANE_DEPTH else general
        for r in page.related:
            ra = r.author
            saw(ra.slug, r.published, r.url)
            if ra.is_person and ra.is_us:
                rkind = K_KNOWN if (ra.slug in known_slugs or ra.slug in decided) else K_CANDIDATE
            else:
                rkind = K_OTHER
            follow.push(r.url, rkind, depth + 1 if follow is lane else 0, r.reactions)

        if not a.is_person or not a.slug or a.slug in decided:
            continue
        decided.add(a.slug)
        if a.slug in known_slugs or normalize_name(a.name) in known_names:
            continue
        if rules.profile_failures(page) or a.slug not in newest:
            continue
        last_seen, _ = newest[a.slug]
        if not rules.recent(last_seen, now, days=config.MAX_SEEN_POST_AGE_DAYS):
            continue
        niche = tag(page.text, page.url)
        if niche == niches.OTHER and lane.niche:
            niche = lane.niche  # LinkedIn's own topic match is a better guess than "Other"
        lane.found += 1
        found.append({
            "Added At (IST)": fmt_time(now_ist()),
            "Name": a.name,
            "Profile URL": profile_url(a.slug),
            "Niche": niche,
            "Followers": a.followers,
            "Total Posts": page.total_posts if page.total_posts is not None else "",
            "Location Signal": rules.location_signal(page),
            "Status": "Pending",
            "_slug": a.slug,
        })
        log.info("[%d/%d] %s | %s | %s followers | newest post %s | via %s lane", len(found), target, a.name,
                 niche, f"{a.followers:,}", fmt_date(last_seen), lane.niche or "general")

    for r in found:  # use the newest post seen for each person (it may have turned up later in the crawl)
        last_seen, last_url = newest[r.pop("_slug")]
        r["Newest Post Seen"], r["Source Post URL"] = fmt_date(last_seen), last_url
    fresh = sum(1 for r in found if rules.recent(parse_time(r["Newest Post Seen"]), now))
    log.info("fetched %d pages, %d new creators (%d with a post in the last %d days)", pages, len(found), fresh,
             config.MAX_POST_AGE_DAYS)
    for lane in [*lanes.values(), general]:
        log.info("  lane %-36s %3d pages, %3d found", lane.niche or "general", lane.used, lane.found)
    log.info("niches added: %s", dict(Counter(r["Niche"] for r in found).most_common()))

    if sheet:
        bulk.append(found)
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
        # Save what's left for later runs, best first, remembering each niche lane's posts by niche.
        left = sorted((r, url, kind, lane.niche or "") for lane in [*lanes.values(), general]
                      for r, url, kind in lane.leftovers() if url not in visited)
        sheet.replace_queue([(url, kind, niche) for _, url, kind, niche in left[: config.QUEUE_MAX]])
    else:
        for r in found:
            print(r)

    if blocked and not found:
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
