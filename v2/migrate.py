"""Copy the creators v2 found while it was an experiment into the sheet it now writes to.

Runs at the start of each v2 run, but only does anything once: rows already in the target
(same profile link) are skipped, and a flag in v2's memory stops it after a successful copy.
"""
from __future__ import annotations

import logging
import sys

from creator_search.linkedin import normalize_slug
from creator_search.sheets import PROFILE_RE, Sheet

from . import config
from .discover import V2_BULK_HEADERS, V2_QUALIFIED_HEADERS
from .state import State

log = logging.getLogger("v2.migrate")


def _slug(row: dict) -> str | None:
    m = PROFILE_RE.search(row.get("Profile URL", ""))
    return normalize_slug(m.group(1)) if m else None


def new_rows(source: list[dict], target: list[dict]) -> list[dict]:
    have = {s for r in target if (s := _slug(r))}
    out = []
    for r in source:
        s = _slug(r)
        if s and s not in have:
            have.add(s)
            out.append({k: v for k, v in r.items() if k != "_row"})
    return out


def run() -> int:
    st = State.load()
    flag = f"{config.EXPERIMENT_SHEET_ID}->{config.V2_SHEET_ID}"
    if config.EXPERIMENT_SHEET_ID == config.V2_SHEET_ID or st.migrated == flag:
        return 0
    src, dst = Sheet(config.EXPERIMENT_SHEET_ID), Sheet(config.V2_SHEET_ID)
    for tab, headers in ((config.BULK_TAB, V2_BULK_HEADERS), (config.QUALIFIED_TAB, V2_QUALIFIED_HEADERS)):
        rows = new_rows(src.tab(tab, headers).rows(), (target := dst.tab(tab, headers)).rows())
        target.append(rows)
        log.info("copied %d %s rows from the experiment sheet", len(rows), tab)
    st.migrated = flag
    st.save()
    return 0


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    sys.exit(run())


if __name__ == "__main__":
    main()
