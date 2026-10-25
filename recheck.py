"""Daily re-check of the Qualified tab: are these creators still active and in range?

For each creator (least recently checked first) the latest known post page is fetched:
  * follower count is refreshed (must still be 5,000-100,000)
  * the author card's total post count is compared with the last check; if it went up,
    the creator has posted since then, so their last activity is at least that recent
  * bulk_add.py also records any newer post it sees by a qualified creator
Status becomes Active, Inactive (no activity seen in 14 days) or Out of range.
Rows are never deleted, so a creator who starts posting again flips back to Active.
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone

from creator_search import config, rules
from creator_search.linkedin import Blocked, Fetcher, NotFound, to_int
from creator_search.sheets import Sheet, fmt_date, fmt_time, now_ist, parse_time

log = logging.getLogger("recheck")


def run(limit: int) -> int:
    sheet = Sheet()
    qual = sheet.qualified()
    rows = qual.rows()
    epoch = datetime.min.replace(tzinfo=timezone.utc)
    rows.sort(key=lambda r: parse_time(r.get("Last Checked (IST)", "")) or epoch)
    rows = rows[:limit]
    log.info("rechecking %d creators", len(rows))

    fetcher = Fetcher()
    tally: dict[str, int] = {}
    blocked = False
    for i, row in enumerate(rows, 1):
        checked_at = now_ist()
        prev_checked = parse_time(row.get("Last Checked (IST)", ""))
        last_activity = parse_time(row.get("Last Activity", ""))
        prev_posts = to_int(row.get("Total Posts"))
        followers = to_int(row.get("Followers"))
        notes = []
        update: dict = {}
        try:
            if not row.get("Latest Post URL"):
                raise NotFound("no post URL on row")
            page = fetcher.post(row["Latest Post URL"])
        except NotFound:
            page = None
            notes.append("latest known post is no longer public")
        except Blocked as e:
            log.error("LinkedIn stopped serving pages (%s); %d creators left for tomorrow", e, len(rows) - i + 1)
            blocked = True
            break

        if page:
            a = page.author
            followers = a.followers if a.followers is not None else followers
            update["Followers"] = followers if followers is not None else ""
            if page.published and (last_activity is None or page.published > last_activity):
                last_activity = page.published
            if page.total_posts is not None:
                update["Total Posts"] = page.total_posts
                if prev_posts is not None and page.total_posts > prev_posts and prev_checked:
                    # They posted after the previous check: activity is at least that recent.
                    if last_activity is None or prev_checked > last_activity:
                        last_activity = prev_checked
                    notes.append(f"posted since {fmt_date(prev_checked)} ({prev_posts:,} -> {page.total_posts:,} posts)")
            if not a.is_us:
                notes.append(f"profile now on {a.country}.linkedin.com")

        if page and not page.author.is_us:
            status = "Moved outside US"
        elif not rules.followers_ok(followers):
            status = f"Out of range ({followers:,} followers)" if followers is not None else "Out of range"
        elif not rules.recent(last_activity):
            status = "Inactive"
        else:
            status = "Active"
        tally[status.split(" (")[0]] = tally.get(status.split(" (")[0], 0) + 1
        update.update({
            "Last Activity": fmt_date(last_activity),
            "Status": status,
            "Last Checked (IST)": fmt_time(checked_at),
            "Notes": "; ".join(notes),
        })
        qual.set(row["_row"], update)
        log.info("[%d/%d] %s -> %s %s", i, len(rows), row.get("Name"), status, "; ".join(notes))
        if i % 25 == 0:
            qual.flush()

    qual.flush()
    log.info("done: %s", tally)
    return 1 if blocked and not tally else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limit", type=int, default=config.RECHECK_MAX_PER_RUN)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    sys.exit(run(args.limit))


if __name__ == "__main__":
    main()
