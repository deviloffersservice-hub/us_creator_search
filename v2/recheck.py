"""v2 recheck: v1's recheck logic, pointed explicitly at the v2 sheet's Qualified tab."""
from __future__ import annotations

import argparse
import logging
import sys
import time

import recheck as v1_recheck  # unchanged v1 module
from creator_search.sheets import Sheet

from . import config
from .discover import V2_QUALIFIED_HEADERS
from .state import State


class _V2Sheet(Sheet):
    def __init__(self):
        super().__init__(config.V2_SHEET_ID)

    def qualified(self):
        return self.tab(config.QUALIFIED_TAB, V2_QUALIFIED_HEADERS)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=config.RECHECK_PER_RUN)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    v1_recheck.Sheet = _V2Sheet  # never the main sheet
    started = time.time()
    code = v1_recheck.run(args.limit)
    st = State.load()
    st.current.update({"recheck_minutes": round((time.time() - started) / 60, 1)})
    st.save()
    sys.exit(code)


if __name__ == "__main__":
    main()
