"""v2 settings. Every value can be overridden with an environment variable of the same name."""
import os
from pathlib import Path

from creator_search.config import _float, _int

# v2 is the main pipeline (since 2026-10-26) and writes to the main sheet. The experiment sheet it
# used before is still read so nobody found there is added twice; v2.migrate copies its creators over.
MAIN_SHEET_ID = os.environ.get("MAIN_SHEET_ID", "1GdZxogrK0wYMXcWGC3tbelUrEZE39foweCHNBbK-6-w")
EXPERIMENT_SHEET_ID = os.environ.get("EXPERIMENT_SHEET_ID", "1zHA9Rm3tniFflGhO8w-1ZGKsXaFBiFVktasFvI-TYBk")
V2_SHEET_ID = os.environ.get("V2_SHEET_ID", MAIN_SHEET_ID)  # where v2 writes
BULK_TAB, QUALIFIED_TAB, RUN_LOG_TAB = "Bulk", "Qualified", "Run Log"

STATE_DIR = Path(os.environ.get("V2_STATE_DIR", ".v2state"))

# Discovery budget per hourly run: about 25 minutes of fetching, leaving room for verify and recheck.
TARGET = _int("V2_TARGET", 100)
MAX_PAGES = _int("V2_MAX_PAGES", 1000)
NICHES_PER_RUN = _int("V2_NICHES_PER_RUN", 6)
NICHE_SLOT_OFFSET = _int("V2_NICHE_SLOT_OFFSET", 3)  # shifts which niches get lanes in a given hour
LANE_SHARE = _float("V2_LANE_SHARE", 0.7)
LANE_DEPTH = _int("V2_LANE_DEPTH", 3)
LANE_MIN_SEEDS = _int("V2_LANE_MIN_SEEDS", 20)  # below this, also use older topic-page posts
PHRASES_PER_NICHE = _int("V2_PHRASES_PER_NICHE", 3)
SEARCH_QUERIES = _int("V2_SEARCH_QUERIES", 18)
TOPIC_SUBPAGES = _int("V2_TOPIC_SUBPAGES", 2)
COMPANY_EVERY = _int("V2_COMPANY_EVERY", 5)  # every Nth page is a company page (fresh posts + similar companies)
COMPANY_REVISIT_HOURS = _int("V2_COMPANY_REVISIT_HOURS", 24)

# Memory of people already checked: skip re-fetching them for a while.
SEEN_TTL_DAYS = {"small": 30, "big": 60, "nonus": 120, "company": 120, "stale": 7, "added": 3650}
QUEUE_MAX = _int("V2_QUEUE_MAX", 20000)
SNAPSHOT_MINUTES = _int("V2_SNAPSHOT_MINUTES", 60)  # verify reuses page data this fresh

# Watching: check often while new (prolific people post daily), then every few hours.
WATCH_FAST_HOURS = _int("V2_WATCH_FAST_HOURS", 2)
WATCH_FAST_FOR_HOURS = _int("V2_WATCH_FAST_FOR_HOURS", 48)
WATCH_SLOW_HOURS = _int("V2_WATCH_SLOW_HOURS", 4)
WATCH_MAX = _int("V2_WATCH_MAX", 200)

RECHECK_PER_RUN = _int("V2_RECHECK_PER_RUN", 150)
ALERT_AFTER_EMPTY_RUNS = _int("V2_ALERT_AFTER_EMPTY_RUNS", 3)

# A related-post card's follower count rejects someone without fetching their post only when it is
# clearly out of range (under 80% of the minimum or over 120% of the maximum).
CARD_MARGIN_LOW = _float("V2_CARD_MARGIN_LOW", 0.8)
CARD_MARGIN_HIGH = _float("V2_CARD_MARGIN_HIGH", 1.2)
