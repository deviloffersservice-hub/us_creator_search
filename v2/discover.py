"""v2 discovery: find new US LinkedIn creators and append them to the Bulk tab.

Same idea as v1's bulk_add.py (logged-out public post pages, niche lanes, related posts), plus:
  * memory of people already checked (too small, too big, not US, no recent post) so their
    pages aren't fetched again for weeks: fewer wasted page views per creator found
  * a crawl queue kept best-first in the Actions cache (not alphabetical, not in a sheet)
  * lanes that don't starve: more search phrases per niche, older topic posts as a fallback
  * niche tagged from every post seen from that person, plus a "Found Via" column
  * page data kept for an hour so verify needn't download the same pages again
It writes to V2_SHEET_ID (the main sheet); the old experiment sheet is also read to avoid duplicates.
"""
from __future__ import annotations

import argparse
import heapq
import logging
import random
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from creator_search import niches, rules
from creator_search import config as v1config
from creator_search.linkedin import (Blocked, Fetcher, NotFound, activity_id_from_url, activity_time,
                                     canonical_post_url, normalize_slug, profile_url, slug_from_post_url,
                                     topic_post_urls)
from creator_search.sheets import (BULK_HEADERS, PROFILE_RE, QUALIFIED_HEADERS, Sheet, fmt_date, fmt_time,
                                   normalize_name, now_ist, parse_time)

from . import config, phrases
from .sources import CompanyPagesBlocked, Searcher, company_page
from .state import State

log = logging.getLogger("v2.discover")

V2_BULK_HEADERS = BULK_HEADERS + ["Found Via"]
V2_QUALIFIED_HEADERS = QUALIFIED_HEADERS + ["Found Via"]
K_CANDIDATE, K_SEED, K_KNOWN, K_OTHER = 0, 1, 2, 3


def rank(kind: int, fresh: bool, too_old: bool = False) -> int | None:
    """Fetch order within a lane (lower first); None = not worth fetching.
    too_old: older than the newest-post limit, so its author can't qualify from this post."""
    if kind == K_CANDIDATE and too_old:
        return None
    if kind in (K_CANDIDATE, K_SEED):
        return 0 if fresh else (1 if kind == K_CANDIDATE else 3)
    if kind == K_KNOWN:
        return 2 if fresh else 4
    return 3 if fresh else None  # K_OTHER: old non-US/company pages lead nowhere


class Lane:
    def __init__(self, niche: str | None, budget: float, now: datetime):
        self.niche = niche
        self.budget = budget
        self.now = now
        self.used = 0
        self.found = 0
        self.heap: list[tuple] = []
        self.entry: dict[str, tuple] = {}  # url -> (rank, kind, depth, reactions, aid, niche)
        self._n = 0
        self.companies: list[str] = []  # company pages still to read for this lane

    def add_company(self, slug: str, done: dict[str, int], now: float):
        if slug and slug not in self.companies and now - done.get(slug, 0) > config.COMPANY_REVISIT_HOURS * 3600:
            self.companies.append(slug)

    def push(self, url: str, kind: int, depth: int = 0, reactions: int = 0, niche: str = "",
             likely: bool = False) -> bool:
        """likely: the related-post card already showed a follower count in range, so fetch it first."""
        url = canonical_post_url(url)
        aid = int(activity_id_from_url(url) or 0)
        posted = activity_time(aid) if aid else None
        r = rank(kind, rules.recent(posted, self.now),
                 bool(posted) and not rules.recent(posted, self.now, days=v1config.MAX_SEEN_POST_AGE_DAYS))
        if r is not None and likely:
            r -= 1
        if r is None or (url in self.entry and self.entry[url][0] <= r):
            return False
        self.entry[url] = (r, kind, depth, reactions, aid, niche or self.niche or "")
        self._n += 1
        heapq.heappush(self.heap, (r, -reactions, -aid, self._n, url))
        return True

    def pop(self, visited: set[str]) -> tuple[str, int, int, int] | None:
        while self.heap:
            r, *_, url = heapq.heappop(self.heap)
            entry = self.entry.get(url)
            if not entry or entry[0] != r:
                continue
            del self.entry[url]
            if url in visited:
                continue
            return url, entry[1], entry[2], entry[3]
        return None

    def best_rank(self) -> int | None:
        while self.heap:
            r, *_, url = self.heap[0]
            entry = self.entry.get(url)
            if entry and entry[0] == r:
                return r
            heapq.heappop(self.heap)
        return None

    def has_work(self) -> bool:
        """A niche lane gets turns while it has new people or start posts to fetch; once only
        crawl-only posts are left, its pages go to the general lane instead."""
        if self.used >= self.budget:
            return False
        best = self.best_rank()
        return best is not None and (self.niche is None or best <= 1)

    def leftovers(self) -> list[tuple]:
        """(sort key..., url, kind, niche, reactions), best first."""
        return sorted((e[0], -e[3], -e[4], url, e[1], e[5], e[3]) for url, e in self.entry.items())


def tag(texts: list[str], url: str = "") -> str:
    return niches.classify(" ".join(texts) + " " + niches.text_from_post_url(url))


def load_seed_file() -> list[str]:
    path = Path(__file__).resolve().parent.parent / "seeds.txt"
    if not path.exists():
        return []
    return [ln.strip() for ln in path.read_text().splitlines() if ln.strip() and not ln.startswith("#")]


def phrase_order(options: list[str], stats: dict[str, list[int]]) -> list[str]:
    """Phrases that found posts before first (best answer rate first), then untried ones, then
    phrases that have never found anything (they still get an occasional turn, last)."""
    def key(p):
        asked, answered = stats.get(p, [0, 0])
        if not asked:
            return (1, random.random())
        if not answered:
            return (2 if asked >= 2 else 1, random.random())
        return (0, -answered / asked, random.random())
    return sorted(options, key=key)


def search_plan(lane_niches: list[str], stats: dict[str, list[int]] | None = None) -> list[tuple[str, str]]:
    """(phrase, niche): round-robin across niches so every niche gets its best phrase early."""
    picks = {n: phrase_order(phrases.for_niche(n), stats or {})[: config.PHRASES_PER_NICHE] for n in lane_niches}
    plan = [(picks[n][i], n) for i in range(config.PHRASES_PER_NICHE) for n in lane_niches if i < len(picks[n])]
    return plan[: config.SEARCH_QUERIES]


KIND_NAMES = {K_CANDIDATE: "new person", K_SEED: "start post", K_KNOWN: "known person", K_OTHER: "non-US/company"}


def card_status(followers: int | None) -> str | None:
    """Reject from the follower count a related-post card shows, without fetching the post. Cards
    can lag the post page a little, so only clear misses are rejected."""
    if followers is None:
        return None
    if followers < v1config.MIN_FOLLOWERS * config.CARD_MARGIN_LOW:
        return "small"
    if followers > v1config.MAX_FOLLOWERS * config.CARD_MARGIN_HIGH:
        return "big"
    return None


def reaction_bucket(n: int) -> str:
    return "0-9" if n < 10 else "10-49" if n < 50 else "50-199" if n < 200 else "200+"


def rejection_status(page) -> str | None:
    a = page.author
    if not a.is_person:
        return "company"
    if not a.is_us:
        return "nonus"
    if a.followers is None:
        return None
    if a.followers < v1config.MIN_FOLLOWERS:
        return "small"
    if a.followers > v1config.MAX_FOLLOWERS:
        return "big"
    return None


def run(target: int, max_pages: int, use_sheet: bool, use_search: bool, min_gap_minutes: int = 0,
        save_state: bool = True) -> int:
    st = State.load()
    started = time.time()
    now = datetime.now(timezone.utc)
    known_slugs: set[str] = set()
    known_names: set[str] = set()
    sheet_posts: list[str] = []
    qualified_rows: dict[str, dict] = {}
    bulk = qual = None

    if use_sheet:
        out = Sheet(config.V2_SHEET_ID)
        bulk = out.tab(config.BULK_TAB, V2_BULK_HEADERS)
        qual = out.tab(config.QUALIFIED_TAB, V2_QUALIFIED_HEADERS)
        if min_gap_minutes:
            last = max((t for r in bulk.rows()[-300:] if (t := parse_time(r.get("Added At (IST)", "")))),
                       default=None)
            if last and now - last < timedelta(minutes=min_gap_minutes):
                log.info("v2 added creators at %s IST, under %d minutes ago; skipping", fmt_time(last),
                         min_gap_minutes)
                return 0
        for sheet_id in dict.fromkeys([config.V2_SHEET_ID, config.MAIN_SHEET_ID, config.EXPERIMENT_SHEET_ID]):
            sheet = out if sheet_id == config.V2_SHEET_ID else Sheet(sheet_id)
            slugs, names, posts = sheet.known_creators()
            known_slugs |= slugs
            known_names |= names
            if sheet_id == config.EXPERIMENT_SHEET_ID:
                sheet_posts += posts
        # Only posts v2 itself found are seeds (rows with "Found Via"): v1's posts lead into the
        # clusters it has already harvested, where nearly every related person is already known.
        sheet_posts += [r["Source Post URL"] for r in bulk.rows() if r.get("Found Via") and r.get("Source Post URL")]
        for row in qual.rows():
            m = PROFILE_RE.search(row.get("Profile URL", ""))
            if m:
                qualified_rows[normalize_slug(m.group(1))] = row
    known_slugs |= {s for s, e in st.seen.items() if e[0] == "added"}

    slot = int(time.time() // 3600) + config.NICHE_SLOT_OFFSET
    lane_niches = niches.rotation(config.NICHES_PER_RUN, slot=slot)
    log.info("niche lanes this run: %s", ", ".join(lane_niches))
    lane_budget = max_pages * config.LANE_SHARE / max(len(lane_niches), 1)
    lanes = {n: Lane(n, lane_budget, now) for n in lane_niches}
    general = Lane(None, float("inf"), now)
    fetcher = Fetcher()

    # Seeds: fresh niche posts from search first, then topic pages (older ones only if a lane is short).
    plan = search_plan(lane_niches, st.phrases) if use_search else []
    searcher = Searcher() if use_search else None
    search_posts = 0

    def search_turn():
        """Run the next planned search (if the engines aren't cooling down) and seed its lane."""
        nonlocal search_posts
        if not plan or not searcher.ready():
            return
        phrase, niche = plan.pop(0)
        urls = searcher.search(phrase)
        stat = st.phrases.setdefault(phrase, [0, 0])
        stat[0] += 1
        stat[1] += bool(urls)
        for url in urls:
            search_posts += lanes[niche].push(url, K_SEED)

    for _ in range(config.SEARCH_UPFRONT):
        search_turn()
    for niche, lane in lanes.items():
        topics = list(niches.NICHES[niche]["topics"])
        random.shuffle(topics)
        if topics:
            for url in topic_post_urls(fetcher, topics[0], subtopics=config.TOPIC_SUBPAGES):
                lane.push(url, K_SEED)
    for item in st.queue:
        url, kind, niche, reactions = (item + ["", 0])[:4]
        target_lane = lanes.get(niche, general)
        target_lane.push(url, int(kind), reactions=int(reactions or 0), niche=niche)
    for niche, lane in lanes.items():
        topics = list(niches.NICHES[niche]["topics"])
        if len(lane.entry) < config.LANE_MIN_SEEDS and topics:
            for url in topic_post_urls(fetcher, random.choice(topics), subtopics=3, max_age_days=365):
                lane.push(url, K_SEED)
    for row in qualified_rows.values():
        if row.get("Latest Post URL"):
            lanes.get(row.get("Niche"), general).push(row["Latest Post URL"], K_KNOWN, niche=row.get("Niche", ""))
    for url in sheet_posts + load_seed_file():
        general.push(url, K_SEED, niche=tag([], url))
    tnow = time.time()
    for key, lane in [*lanes.items(), ("", general)]:
        for slug in st.companies.get(key, []) + (phrases.COMPANIES.get(key, []) if key else []):
            lane.add_company(slug, st.companies_done, tnow)
    for lane in [*lanes.values(), general]:
        log.info("lane %s: %d posts and %d company pages to start from", lane.niche or "general",
                 len(lane.entry), len(lane.companies))

    found: list[dict] = []
    decided: set[str] = set()
    newest: dict[str, tuple[datetime, str]] = {}
    texts: dict[str, list[str]] = defaultdict(list)  # every post text seen per person, for niche tagging
    visited: set[str] = set()
    via: dict[str, str] = {}
    pages = skipped_known = candidate_cards = company_pages = company_posts = 0
    companies_ok = True
    blocked = False
    last_search_at = 0
    outcomes: dict[str, Counter] = defaultdict(Counter)  # kind of page fetched -> what its author turned out to be
    by_reactions: dict[str, Counter] = defaultdict(Counter)  # new-person pages: reactions bucket -> outcome
    related_cards = related_with_followers = card_rejected = 0
    card_followers: dict[str, int] = {}  # follower count shown on related-post cards, to check against post pages
    card_checks = Counter()
    turn = 0

    def saw(slug: str, published: datetime | None, url: str, text: str = ""):
        if not slug:
            return
        if text and len(texts[slug]) < 8:
            texts[slug].append(text[:600])
        if published and (slug not in newest or published > newest[slug][0]):
            newest[slug] = (published, url)

    def next_page():
        nonlocal turn
        order = list(lanes.values())
        for i in range(len(order)):
            lane = order[(turn + i) % len(order)]
            if lane.has_work() and (item := lane.pop(visited)):
                turn = (turn + i + 1) % len(order)
                return (lane, *item)
        while general.entry or any(lane.entry for lane in lanes.values()):
            if (item := general.pop(visited)):
                return (general, *item)
            for lane in lanes.values():
                for *_, url, kind, niche, reactions in lane.leftovers():
                    general.push(url, kind, reactions=reactions, niche=niche)
                lane.entry.clear()
        return None

    while len(found) < target and pages < max_pages:
        if companies_ok and pages and pages % config.COMPANY_EVERY == 0:
            # Company page turn: fresh posts from a company in one of the lanes, plus similar companies.
            order = [ln for ln in [*lanes.values(), general] if ln.companies and ln.used < ln.budget]
            if order:
                clane = order[(pages // config.COMPANY_EVERY) % len(order)]
                slug = clane.companies.pop(0)
                st.companies_done[slug] = int(time.time())
                try:
                    posts, similar = company_page(fetcher, slug)
                except CompanyPagesBlocked as e:
                    log.warning("company pages refused (%s); not using them for the rest of this run", e)
                    companies_ok = False
                    clane.companies.insert(0, slug)
                    st.companies_done.pop(slug, None)
                    continue
                pages += 1
                clane.used += 1
                company_pages += 1
                for url in posts:
                    if clane.push(url, K_SEED):
                        company_posts += 1
                for other in similar:
                    clane.add_company(other, st.companies_done, time.time())
                continue
        if searcher and pages - last_search_at >= config.SEARCH_EVERY_PAGES:
            last_search_at = pages
            search_turn()
        nxt = next_page()
        if not nxt:
            log.info("nothing left to crawl")
            break
        lane, url, kind, depth, reactions = nxt
        visited.add(url)
        if kind == K_CANDIDATE and slug_from_post_url(url) in decided:
            continue  # another post of someone already judged this run; the queue has plenty to crawl
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
        if pages % 50 == 0:
            log.info("progress: %d pages, %d found, %d new US people seen in related posts, %d already known",
                     pages, len(found), candidate_cards, skipped_known)
        a = page.author
        saw(a.slug, page.published, page.url, page.text)

        follow = lane if lane.niche and depth < config.LANE_DEPTH else general
        for r in page.related:
            ra = r.author
            related_cards += 1
            related_with_followers += ra.followers is not None
            saw(ra.slug, r.published, r.url, r.text)
            likely = False
            if ra.is_person and ra.is_us:
                if ra.slug in known_slugs or ra.slug in decided or st.skip(ra.slug):
                    rkind = K_KNOWN
                    skipped_known += 1
                elif (cstatus := card_status(ra.followers)):
                    st.remember(ra.slug, cstatus, ra.followers)
                    decided.add(ra.slug)
                    rkind = K_KNOWN
                    card_rejected += 1
                else:
                    rkind = K_CANDIDATE
                    candidate_cards += 1
                    if ra.followers is not None:
                        likely = True
                        card_followers[ra.slug] = ra.followers
            else:
                rkind = K_OTHER
                if ra.kind == "company" and ra.is_us:
                    (lane if lane.niche else general).add_company(ra.slug, st.companies_done, time.time())
            follow.push(r.url, rkind, depth + 1 if follow is lane else 0, r.reactions,
                        niche=lane.niche or tag([r.text], r.url), likely=likely)

        def outcome(what: str):
            outcomes[KIND_NAMES[kind]][what] += 1
            if kind == K_CANDIDATE:
                by_reactions[reaction_bucket(reactions)][what] += 1

        if not a.is_person or not a.slug:
            outcome("company" if a.slug else "unreadable")
            continue
        if a.slug in decided:
            outcome("seen this run")
            continue
        decided.add(a.slug)
        if a.slug in card_followers and a.followers:
            card_checks["card within 10% of page" if abs(card_followers[a.slug] - a.followers) <= 0.1 * a.followers
                        else "card differs"] += 1
        if a.slug in known_slugs or normalize_name(a.name) in known_names:
            outcome("already in a sheet")
            continue
        status = rejection_status(page)
        if status or a.followers is None:
            outcome(status or "no follower count")
            if status:
                st.remember(a.slug, status, a.followers)
            continue
        last_seen, _ = newest.get(a.slug, (None, ""))
        if not rules.recent(last_seen, now, days=v1config.MAX_SEEN_POST_AGE_DAYS):
            outcome("stale")
            st.remember(a.slug, "stale", a.followers)
            continue
        outcome("added")
        st.remember(a.slug, "added", a.followers)
        st.snapshots[a.slug] = {"name": a.name, "kind": a.kind, "country": a.country, "followers": a.followers,
                                "total_posts": page.total_posts, "url": page.url, "t": int(time.time()),
                                "published": page.published.isoformat() if page.published else None}
        via[a.slug] = lane.niche or "general"
        lane.found += 1
        found.append({"_slug": a.slug, "Added At (IST)": fmt_time(now_ist()), "Name": a.name,
                      "Profile URL": profile_url(a.slug), "Followers": a.followers,
                      "Total Posts": page.total_posts if page.total_posts is not None else "",
                      "Location Signal": rules.location_signal(page), "Status": "Pending",
                      "Found Via": lane.niche or "general"})
        log.info("[%d/%d] %s | %s followers | via %s", len(found), target, a.name, f"{a.followers:,}",
                 lane.niche or "general")

    for r in found:  # niche from every post seen of this person; newest post seen
        slug = r.pop("_slug")
        last_seen, last_url = newest[slug]
        r["Newest Post Seen"], r["Source Post URL"] = fmt_date(last_seen), last_url
        niche = tag(texts[slug])
        r["Niche"] = via[slug] if niche == niches.OTHER and via[slug] != "general" else niche
    fresh = sum(1 for r in found if rules.recent(parse_time(r["Newest Post Seen"]), now))
    lane_found = {lane.niche or "general": [lane.used, lane.found] for lane in [*lanes.values(), general]}
    niche_counts = dict(Counter(r["Niche"] for r in found).most_common())
    log.info("fetched %d pages, %d new creators (%d posted in the last %d days), %d known people skipped",
             pages, len(found), fresh, v1config.MAX_POST_AGE_DAYS, skipped_known)
    for name, (used, n) in lane_found.items():
        log.info("  lane %-36s %3d pages, %3d found", name, used, n)
    log.info("niches added: %s", niche_counts)
    for name, c in outcomes.items():
        log.info("  pages from %-15s %4d: %s", name, sum(c.values()), dict(c.most_common()))
    for bucket in ("0-9", "10-49", "50-199", "200+"):
        if by_reactions[bucket]:
            c = by_reactions[bucket]
            log.info("  new-person posts with %-6s reactions %4d: %s", bucket, sum(c.values()), dict(c.most_common()))
    log.info("related post cards: %d, %d of them show a follower count; %d people rejected from the card alone; "
             "card vs page follower counts: %s", related_cards, related_with_followers, card_rejected, dict(card_checks))

    if use_sheet:
        bulk.append(found)
        for slug, row in qualified_rows.items():
            if slug in newest:
                published, url = newest[slug]
                last = parse_time(row.get("Last Activity", ""))
                if last is None or published > last:
                    qual.set(row["_row"], {"Last Activity": fmt_date(published), "Latest Post URL": url})
        qual.flush()
    else:
        for r in found:
            print(r)

    left = sorted(item for lane in [*lanes.values(), general] for item in lane.leftovers() if item[3] not in visited)
    st.queue = [[url, kind, niche, reactions] for *_, url, kind, niche, reactions in left[: config.QUEUE_MAX]]
    for key, lane in [*lanes.items(), ("", general)]:
        st.companies[key] = list(dict.fromkeys(lane.companies + st.companies.get(key, [])))[:300]
    log.info("company pages read: %d, giving %d fresh posts to start from", company_pages, company_posts)
    if searcher:
        log.info("searches: %d asked, %d answered, %d new posts to start from", searcher.asked, searcher.answered,
                 search_posts)
    st.current = {"run_at": fmt_time(now_ist()), "lanes": ", ".join(lane_niches), "pages": pages,
                  "found": len(found), "fresh": fresh, "skipped_known": skipped_known,
                  "candidate_cards": candidate_cards, "company_pages": company_pages,
                  "company_posts": company_posts, "company_pages_refused": not companies_ok,
                  "searches": searcher.asked if searcher else 0,
                  "searches_answered": searcher.answered if searcher else 0, "search_posts": search_posts,
                  "blocked": blocked,
                  "outcomes": {k: dict(v) for k, v in outcomes.items()}, "card_rejected": card_rejected,
                  "card_checks": dict(card_checks),
                  "by_reactions": {k: dict(v) for k, v in by_reactions.items()},
                  "lane_found": lane_found, "niches": niche_counts, "discover_minutes": round((time.time() - started) / 60, 1)}
    if save_state:
        st.save()
    return 2 if blocked and not found else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--target", type=int, default=config.TARGET)
    ap.add_argument("--max-pages", type=int, default=config.MAX_PAGES)
    ap.add_argument("--no-sheet", action="store_true", help="dry run: print results, don't touch any sheet")
    ap.add_argument("--no-search", action="store_true")
    ap.add_argument("--no-save", action="store_true", help="don't write the v2 state file")
    ap.add_argument("--min-gap-minutes", type=int, default=0)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    sys.exit(run(args.target, args.max_pages, not args.no_sheet, not args.no_search, args.min_gap_minutes,
                 not args.no_save))


if __name__ == "__main__":
    main()
