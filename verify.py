"""Verify Bulk rows and mark each one Qualified, Rejected or Watching.

Every row's post page is fetched fresh and the rules are re-checked:
  * personal profile (not a company page)
  * US-based (public profile URL on www.linkedin.com, not a country subdomain)
  * 5,000-100,000 followers (exact count from LinkedIn's page data)
  * active: a post in the last 14 days

Activity without logging in: if the newest post we can see is within 14 days, that's
proof. Otherwise the creator's total post count is compared with the count recorded at
the previous check; if it went up, they posted since then -> Qualified. If it hasn't, the
row is "Watching" and re-checked on later runs; after 14 days with no new post it is
Rejected as inactive.

Qualified creators are also copied to the Qualified tab.
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta, timezone

from creator_search import config, niches, rules
from creator_search.linkedin import Blocked, Fetcher, NotFound, PostPage, normalize_slug, profile_url, to_int
from creator_search.sheets import PROFILE_RE, Sheet, fmt_date, fmt_time, now_ist, parse_time

log = logging.getLogger("verify")


def rows_to_check(rows: list[dict], now: datetime, watch_limit: int) -> list[dict]:
    pending = [r for r in rows if r.get("Status", "").strip() in ("", "Pending")]
    due = now - timedelta(hours=config.WATCH_RECHECK_HOURS)
    watching = [r for r in rows if r.get("Status", "").strip() == "Watching"
                and (parse_time(r.get("Verified At (IST)", "")) or now) <= due]
    watching.sort(key=lambda r: parse_time(r["Verified At (IST)"]))
    return [r for r in pending + watching[:watch_limit] if r.get("Source Post URL")]


def decide(page: PostPage, row: dict, expected_slug: str | None, now: datetime):
    """-> (status, reason, activity time or None, newest post time or None)."""
    added_at = parse_time(row.get("Added At (IST)", ""))
    prev_check = parse_time(row.get("Verified At (IST)", "")) or added_at
    prev_posts = to_int(row.get("Total Posts"))
    failed = rules.profile_failures(page, expected_slug=expected_slug)
    newest = rules.newer(page.published, parse_time(row.get("Newest Post Seen", "")))
    activity, evidence = None, ""
    if rules.recent(newest, now):
        activity, evidence = newest, f"posted on {fmt_date(newest)}"
    elif page.total_posts is not None and prev_posts is not None and page.total_posts > prev_posts and prev_check:
        activity = prev_check
        evidence = f"posted since {fmt_time(prev_check)} IST (post count {prev_posts:,} -> {page.total_posts:,})"

    if failed:
        return "Rejected", "; ".join(failed), None, newest
    if activity:
        return "Qualified", evidence, activity, newest
    seen = fmt_date(newest) or "unknown"
    if added_at and now - added_at >= timedelta(days=config.WATCH_DAYS):
        return "Rejected", f"inactive: no new post in {config.WATCH_DAYS} days of checks (newest post seen {seen})", None, newest
    return "Watching", f"US + followers OK; newest post seen {seen}, re-checking post count for new posts", None, newest


def run(limit: int | None, watch_limit: int) -> int:
    sheet = Sheet()
    bulk, qual = sheet.bulk(), sheet.qualified()
    already_qualified = {normalize_slug(m.group(1)) for r in qual.rows()
                         if (m := PROFILE_RE.search(r.get("Profile URL", "")))}
    now = datetime.now(timezone.utc)
    todo = rows_to_check(bulk.rows(), now, watch_limit)
    if limit:
        todo = todo[:limit]
    log.info("%d rows to verify", len(todo))

    fetcher = Fetcher()
    new_qualified: list[dict] = []
    counts = {"Qualified": 0, "Rejected": 0, "Watching": 0}
    blocked = False
    for i, row in enumerate(todo, 1):
        m = PROFILE_RE.search(row.get("Profile URL", ""))
        expected = normalize_slug(m.group(1)) if m else None
        try:
            page = fetcher.post(row["Source Post URL"])
        except NotFound:
            bulk.set(row["_row"], {"Status": "Rejected", "Reason": "post deleted or not public",
                                   "Verified At (IST)": fmt_time(now_ist())})
            counts["Rejected"] += 1
            continue
        except Blocked as e:
            log.error("LinkedIn stopped serving pages (%s); %d rows left for the next run", e, len(todo) - i + 1)
            blocked = True
            break

        a = page.author
        status, reason, activity, newest = decide(page, row, expected, now)
        counts[status] += 1
        bulk.set(row["_row"], {
            "Name": a.name or row.get("Name", ""),
            "Followers": a.followers if a.followers is not None else "",
            "Total Posts": page.total_posts if page.total_posts is not None else row.get("Total Posts", ""),
            "Newest Post Seen": fmt_date(newest),
            "Location Signal": rules.location_signal(page),
            "Status": status,
            "Reason": reason,
            "Verified At (IST)": fmt_time(now_ist()),
        })
        log.info("[%d/%d] %s -> %s: %s", i, len(todo), a.name, status, reason)
        if status == "Qualified" and a.slug not in already_qualified:
            already_qualified.add(a.slug)
            new_qualified.append({
                "Name": a.name,
                "Profile URL": profile_url(a.slug),
                "Niche": row.get("Niche") or niches.classify(page.text),
                "Followers": a.followers,
                "Total Posts": page.total_posts if page.total_posts is not None else "",
                "Last Activity": fmt_date(activity),
                "Latest Post URL": page.url,
                "Status": "Active",
                "First Qualified (IST)": fmt_time(now_ist()),
                "Last Checked (IST)": fmt_time(now_ist()),
                "Notes": reason,
            })
        if i % 25 == 0:  # save progress regularly in case the job is killed
            bulk.flush()

    bulk.flush()
    qual.append(new_qualified)
    log.info("done: %s; %d added to %s", counts, len(new_qualified), config.QUALIFIED_TAB)
    return 1 if blocked and not sum(counts.values()) else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limit", type=int, help="verify at most this many rows")
    ap.add_argument("--watch-limit", type=int, default=config.WATCH_MAX_PER_RUN,
                    help="max Watching rows to re-check this run")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    sys.exit(run(args.limit, args.watch_limit))


if __name__ == "__main__":
    main()
