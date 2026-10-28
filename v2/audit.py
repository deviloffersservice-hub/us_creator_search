"""Read-only audit of the sheet v2 writes to. Prints "audit:" lines to the job log for the daily
health check: status counts, duplicates, rows that break the creator rules, stuck rows.

Never writes to the sheet and never fails the job (problems are reported, not fixed here).
"""
from __future__ import annotations

import logging
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone

from creator_search import config as v1config
from creator_search.linkedin import normalize_slug, to_int
from creator_search.sheets import PROFILE_RE, Sheet, parse_time

from . import config
from .discover import V2_BULK_HEADERS, V2_QUALIFIED_HEADERS

log = logging.getLogger("v2.audit")


def _slug(row: dict) -> str | None:
    m = PROFILE_RE.search(row.get("Profile URL", ""))
    return normalize_slug(m.group(1)) if m else None


def duplicates(rows: list[dict]) -> dict[str, list[int]]:
    """profile slug -> sheet row numbers, for slugs that appear more than once."""
    where: dict[str, list[int]] = {}
    for r in rows:
        if (s := _slug(r)):
            where.setdefault(s, []).append(r["_row"])
    return {s: rows_ for s, rows_ in where.items() if len(rows_) > 1}


def rule_breakers(rows: list[dict], status_col: str, good: set[str]) -> list[tuple[int, str]]:
    """(row, why) for rows marked good whose followers or location break the creator rules."""
    out = []
    for r in rows:
        if r.get(status_col, "").strip() not in good:
            continue
        f = to_int(r.get("Followers"))
        if f is not None and not v1config.MIN_FOLLOWERS <= f <= v1config.MAX_FOLLOWERS:
            out.append((r["_row"], f"followers {f:,}"))
        loc = r.get("Location Signal", "").strip()
        if loc and not loc.startswith("US"):
            out.append((r["_row"], f"location {loc}"))
    return out


def stuck_pending(rows: list[dict], now: datetime, hours: int = 3) -> list[int]:
    return [r["_row"] for r in rows if r.get("Status", "").strip() in ("", "Pending")
            and (t := parse_time(r.get("Added At (IST)", ""))) and now - t > timedelta(hours=hours)]


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    sheet = Sheet(config.V2_SHEET_ID)
    bulk = sheet.tab(config.BULK_TAB, V2_BULK_HEADERS).rows()
    qual = sheet.tab(config.QUALIFIED_TAB, V2_QUALIFIED_HEADERS).rows()
    now = datetime.now(timezone.utc)

    log.info("audit: Bulk %d rows: %s", len(bulk), dict(Counter(r.get("Status", "").strip() or "Pending"
                                                                for r in bulk).most_common()))
    log.info("audit: Qualified %d rows: %s", len(qual), dict(Counter(r.get("Status", "").strip()
                                                                     for r in qual).most_common()))
    added_24h = sum(1 for r in bulk if (t := parse_time(r.get("Added At (IST)", ""))) and now - t <= timedelta(days=1))
    qual_24h = sum(1 for r in qual if (t := parse_time(r.get("First Qualified (IST)", "")))
                   and now - t <= timedelta(days=1))
    log.info("audit: last 24h: %d added to Bulk, %d added to Qualified", added_24h, qual_24h)
    for name, rows in (("Bulk", bulk), ("Qualified", qual)):
        d = duplicates(rows)
        log.info("audit: %s duplicate profiles: %d%s", name, len(d), f" e.g. {list(d.items())[:5]}" if d else "")
    bad = rule_breakers(qual, "Status", {"Active"}) + rule_breakers(bulk, "Status", {"Qualified"})
    log.info("audit: qualified rows breaking the rules: %d%s", len(bad), f" e.g. {bad[:5]}" if bad else "")
    stuck = stuck_pending(bulk, now)
    log.info("audit: Pending for over 3 hours: %d%s", len(stuck), f" e.g. rows {stuck[:10]}" if stuck else "")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # an audit must never break the pipeline
        logging.getLogger("v2.audit").error("audit: failed: %s", e)
        sys.exit(0)
