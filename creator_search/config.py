"""All tunable settings live here. Each one can be overridden with an env var."""
import os
from zoneinfo import ZoneInfo


def _int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


def _float(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


SHEET_ID = os.environ.get("SHEET_ID", "1GdZxogrK0wYMXcWGC3tbelUrEZE39foweCHNBbK-6-w")
BULK_TAB = os.environ.get("BULK_TAB", "Bulk")
QUALIFIED_TAB = os.environ.get("QUALIFIED_TAB", "Qualified")
# Crawl frontier carried between runs so each run picks up where the last stopped.
QUEUE_TAB = os.environ.get("QUEUE_TAB", "Crawl Queue")
QUEUE_MAX = _int("QUEUE_MAX", 3000)

# Qualification rules.
MIN_FOLLOWERS = _int("MIN_FOLLOWERS", 5_000)
MAX_FOLLOWERS = _int("MAX_FOLLOWERS", 100_000)
MAX_POST_AGE_DAYS = _int("MAX_POST_AGE_DAYS", 14)
# LinkedIn serves US members' public URLs on www.linkedin.com and everyone else on a
# country subdomain (uk., in., ca., ...). That is the only logged-out location signal.
US_SUBDOMAIN = "www"

# Run sizes.
BULK_TARGET = _int("BULK_TARGET", 100)
MAX_PAGES_PER_BULK_RUN = _int("MAX_PAGES_PER_BULK_RUN", 700)
# Niche lanes: each run takes the next NICHES_PER_RUN niches in rotation and splits
# NICHE_LANE_SHARE of the page budget equally between them; the rest goes to general discovery.
NICHES_PER_RUN = _int("NICHES_PER_RUN", 6)
NICHE_LANE_SHARE = _float("NICHE_LANE_SHARE", 0.7)
LANE_DEPTH = _int("LANE_DEPTH", 3)  # hops of related posts a lane follows before handing them on
TOPIC_SUBPAGES = _int("TOPIC_SUBPAGES", 2)  # extra LinkedIn topic pages fetched per niche lane
SEARCH_QUERIES_PER_RUN = _int("SEARCH_QUERIES_PER_RUN", 12)
# Activity check for creators whose newest public post we found is older than 14 days:
# their total post count is recorded and compared at later checks. If it rises they have
# posted since the previous check (= active). Rejected only after WATCH_DAYS with no rise.
WATCH_DAYS = _int("WATCH_DAYS", 14)
WATCH_RECHECK_HOURS = _int("WATCH_RECHECK_HOURS", 4)
WATCH_MAX_PER_RUN = _int("WATCH_MAX_PER_RUN", 200)
# Bulk skips people whose newest post we can find is older than this (clearly dormant).
MAX_SEEN_POST_AGE_DAYS = _int("MAX_SEEN_POST_AGE_DAYS", 30)
# Recheck visits the least recently checked creators first, up to this many per day.
RECHECK_MAX_PER_RUN = _int("RECHECK_MAX_PER_RUN", 2000)

# Politeness / blocking.
REQUEST_DELAY_MIN = _float("REQUEST_DELAY_MIN", 0.8)
REQUEST_DELAY_MAX = _float("REQUEST_DELAY_MAX", 2.0)
MAX_CONSECUTIVE_BLOCKS = _int("MAX_CONSECUTIVE_BLOCKS", 5)

IST = ZoneInfo("Asia/Kolkata")
