"""Append this run's metrics to the v2 sheet's Run Log tab, and fail the job (so GitHub emails
the repo owner) if LinkedIn blocked us or several runs in a row found nobody."""
from __future__ import annotations

import json
import logging
import os
import sys

from creator_search.sheets import Sheet

from . import config
from .state import State

log = logging.getLogger("v2.runlog")

HEADERS = ["Run At (IST)", "Run", "Niche Lanes", "Pages", "Found", "Posted <14d", "Known Skipped",
           "Verified", "Qualified", "Watching", "Rejected", "Verify Reused", "Verify Fetched",
           "Found per Lane (pages, found)", "Niches Added", "Blocked", "Minutes"]


def row_for(m: dict) -> dict:
    minutes = sum(m.get(k, 0) or 0 for k in ("discover_minutes", "verify_minutes", "recheck_minutes"))
    return {
        "Run At (IST)": m.get("run_at", ""), "Run": os.environ.get("GITHUB_RUN_NUMBER", "local"),
        "Niche Lanes": m.get("lanes", ""), "Pages": m.get("pages", 0), "Found": m.get("found", 0),
        "Posted <14d": m.get("fresh", 0), "Known Skipped": m.get("skipped_known", 0),
        "Verified": m.get("verified", 0), "Qualified": m.get("qualified", 0), "Watching": m.get("watching", 0),
        "Rejected": m.get("rejected", 0), "Verify Reused": m.get("verify_reused", 0),
        "Verify Fetched": m.get("verify_fetched", 0),
        "Found per Lane (pages, found)": json.dumps(m.get("lane_found", {})),
        "Niches Added": json.dumps(m.get("niches", {})),
        "Blocked": "YES" if m.get("blocked") or m.get("verify_blocked") else "", "Minutes": round(minutes, 1),
    }


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    st = State.load()
    m = st.current
    if not m.get("run_at"):
        log.info("no discovery metrics for this run (skipped run?)")
        return
    Sheet(config.V2_SHEET_ID).tab(config.RUN_LOG_TAB, HEADERS).append([row_for(m)])
    st.runs.append(m)
    st.current = {}
    st.save()
    recent = st.runs[-config.ALERT_AFTER_EMPTY_RUNS:]
    if m.get("blocked") or m.get("verify_blocked"):
        log.error("LinkedIn blocked this run; failing the job so GitHub sends an alert")
        sys.exit(1)
    if len(recent) == config.ALERT_AFTER_EMPTY_RUNS and all(r.get("found", 0) == 0 for r in recent):
        log.error("%d runs in a row found nobody; failing the job so GitHub sends an alert", len(recent))
        sys.exit(1)


if __name__ == "__main__":
    main()
