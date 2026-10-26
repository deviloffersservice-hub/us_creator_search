"""Persistent v2 memory, stored as one gzipped JSON file in the Actions cache between runs.

  seen:      slug -> [status, followers, epoch]   people already checked (see config.SEEN_TTL_DAYS)
  queue:     [[url, kind, niche, reactions], ...] crawl frontier, best first
  snapshots: slug -> page data from discovery, so verify needn't re-download it within the hour
  runs:      recent run metrics (for the Run Log and alerts)
"""
from __future__ import annotations

import gzip
import json
import logging
import time

from . import config

log = logging.getLogger(__name__)

REJECTED = {"small", "big", "nonus", "company", "stale"}


class State:
    def __init__(self, data: dict | None = None):
        data = data or {}
        self.seen: dict[str, list] = data.get("seen", {})
        self.queue: list[list] = data.get("queue", [])
        self.snapshots: dict[str, dict] = data.get("snapshots", {})
        self.runs: list[dict] = data.get("runs", [])
        self.companies: dict[str, list[str]] = data.get("companies", {})  # niche ("" = general) -> slugs to visit
        self.companies_done: dict[str, int] = data.get("companies_done", {})  # slug -> when its page was read
        self.phrases: dict[str, list[int]] = data.get("phrases", {})  # search phrase -> [times asked, times answered]
        self.migrated: str = data.get("migrated", "")  # "<from sheet>-><to sheet>" once v2.migrate has copied rows
        self.current: dict = {}  # metrics for this run, filled in by each step

    @property
    def path(self):
        return config.STATE_DIR / "state.json.gz"

    @classmethod
    def load(cls) -> "State":
        path = config.STATE_DIR / "state.json.gz"
        if not path.exists():
            log.info("no saved v2 state yet; starting fresh")
            return cls()
        with gzip.open(path, "rt") as f:
            data = json.load(f)
        st = cls(data)
        st.current = data.get("current", {})
        log.info("v2 state: %d people remembered, %d queued posts, %d runs", len(st.seen), len(st.queue),
                 len(st.runs))
        return st

    def save(self):
        self.prune()
        config.STATE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        with gzip.open(tmp, "wt") as f:
            json.dump({"seen": self.seen, "queue": self.queue[: config.QUEUE_MAX], "snapshots": self.snapshots,
                       "runs": self.runs[-200:], "current": self.current,
                       "companies": {k: v[:300] for k, v in self.companies.items()},
                       "companies_done": self.companies_done, "migrated": self.migrated,
                       "phrases": self.phrases}, f, separators=(",", ":"))
        tmp.replace(self.path)

    # --- people already checked -------------------------------------------------------------
    def remember(self, slug: str, status: str, followers: int | None = None, now: float | None = None):
        self.seen[slug] = [status, followers, int(now or time.time())]

    def status(self, slug: str, now: float | None = None) -> str | None:
        """The remembered status if it hasn't expired, else None."""
        entry = self.seen.get(slug)
        if not entry:
            return None
        status, _, at = entry
        if (now or time.time()) - at > config.SEEN_TTL_DAYS.get(status, 30) * 86400:
            return None
        return status

    def skip(self, slug: str, now: float | None = None) -> bool:
        return self.status(slug, now) in REJECTED

    # --- housekeeping -----------------------------------------------------------------------
    def prune(self, now: float | None = None):
        now = now or time.time()
        self.seen = {s: e for s, e in self.seen.items()
                     if now - e[2] <= config.SEEN_TTL_DAYS.get(e[0], 30) * 86400}
        self.companies_done = {s: t for s, t in self.companies_done.items() if now - t <= 3 * 86400}
        keep = config.SNAPSHOT_MINUTES * 60 * 3
        self.snapshots = {s: v for s, v in self.snapshots.items() if now - v.get("t", 0) <= keep}
