"""Best-effort free web search for fresh LinkedIn posts, used only to add seed posts.

Free search engines rate-limit servers hard, so any failure here is logged and ignored;
the crawler works without it.
"""
from __future__ import annotations

import logging
import time

from .linkedin import POST_URL_RE, canonical_post_url

log = logging.getLogger(__name__)


def recent_post_urls(terms: list[str], per_query: int = 25) -> dict[str, list[str]]:
    """term -> post URLs found for it (empty dict if the search engine refuses us)."""
    try:
        from ddgs import DDGS
    except ImportError:
        log.info("ddgs not installed; skipping search seeding")
        return {}
    for noisy in ("primp", "ddgs", "httpx"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    found_by_term: dict[str, list[str]] = {}
    failures = 0
    for term in terms:
        if failures >= 2:
            log.info("search engine is rate-limiting us; skipping the remaining queries")
            break
        try:
            try:
                results = DDGS().text(f"site:linkedin.com/posts {term}", region="us-en",
                                      timelimit="w", max_results=per_query, backend="duckduckgo")
            except Exception:  # let ddgs try its other engines once
                results = DDGS().text(f"site:linkedin.com/posts {term}", region="us-en",
                                      timelimit="w", max_results=per_query, backend="auto")
        except Exception as e:  # ddgs raises its own exception types for blocks/no results
            log.info("search %r gave nothing (%s)", term, str(e)[:120])
            failures += 1
            time.sleep(15)
            continue
        failures = 0
        found = [canonical_post_url(r["href"]) for r in results if POST_URL_RE.match(r.get("href", ""))]
        log.info("search %r -> %d posts", term, len(found))
        found_by_term[term] = found
        time.sleep(4)
    return found_by_term
