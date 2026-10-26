"""Extra discovery sources for v2, found by reviewing open-source LinkedIn tools.

* Company pages (linkedin.com/company/<slug>/) are public. Each lists the company's ~10 latest
  posts and ~20 "similar pages", so they're an endless supply of fresh, industry-specific posts.
* Search across several engines (ddgs backends). Each engine rate-limits separately, so rotating
  them gets more fresh posts per run than DuckDuckGo alone.
"""
from __future__ import annotations

import logging
import re
import time

from creator_search.linkedin import POST_URL_RE, canonical_post_url

log = logging.getLogger("v2.sources")

COMPANY_RE = re.compile(r"https://([a-z]{2,3})\.linkedin\.com/company/([^\"?&/#\s<]+)")


def parse_company_page(page: str) -> tuple[list[str], list[str]]:
    """-> (recent post URLs, slugs of US companies linked from the page, e.g. 'similar pages')."""
    posts = [canonical_post_url(u) for u in POST_URL_RE.findall(page)]
    companies = [slug for sub, slug in COMPANY_RE.findall(page) if sub == "www"]
    return list(dict.fromkeys(posts)), list(dict.fromkeys(companies))


class CompanyPagesBlocked(Exception):
    """LinkedIn refused a company page. Company pages are better protected than post pages, so v2
    stops using them for the rest of the run instead of risking the post crawl."""


def company_page(fetcher, slug: str) -> tuple[list[str], list[str]]:
    """One polite attempt, no retries, and it never counts towards the post crawl's block limit."""
    fetcher._sleep()
    try:
        r = fetcher.session.get(f"https://www.linkedin.com/company/{slug}/", timeout=25, allow_redirects=True)
    except Exception as e:  # network trouble: just skip this company
        log.info("company %s: %s", slug, str(e)[:80])
        return [], []
    if r.status_code in (429, 999) or "/authwall" in r.url or re.search(r"/(signup|login|uas)/", r.url):
        raise CompanyPagesBlocked(f"{r.status_code} on company/{slug}")
    if r.status_code != 200:
        return [], []
    return parse_company_page(r.text)


BACKENDS = ["duckduckgo", "brave", "bing", "mojeek", "yahoo", "startpage", "google", "yandex"]


def search_many_engines(terms: list[str], per_query: int = 25) -> dict[str, list[str]]:
    """term -> LinkedIn post URLs. Each query goes to the next engine in rotation; an engine that
    fails twice is dropped for this run."""
    try:
        from ddgs import DDGS
    except ImportError:
        return {}
    for noisy in ("primp", "ddgs", "httpx"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    alive = {b: 0 for b in BACKENDS}
    out: dict[str, list[str]] = {}
    turn = 0
    for term in terms:
        tried = 0
        while alive and tried < len(alive):
            backend = list(alive)[turn % len(alive)]
            turn += 1
            tried += 1
            try:
                results = DDGS().text(f"site:linkedin.com/posts {term}", region="us-en", timelimit="w",
                                      max_results=per_query, backend=backend)
                urls = [canonical_post_url(r["href"]) for r in results if POST_URL_RE.match(r.get("href", ""))]
            except Exception as e:  # blocked, captcha, timeout or no results
                urls = []
                log.info("search %r on %s failed (%s)", term, backend, str(e)[:80])
            if urls:
                out[term] = urls
                log.info("search %r on %s -> %d posts", term, backend, len(urls))
                time.sleep(2)
                break
            alive[backend] += 1
            if alive[backend] >= 2:
                log.info("dropping search engine %s for this run", backend)
                del alive[backend]
            time.sleep(3)
        if not alive:
            log.info("all search engines are refusing us; stopping searches")
            break
    return out
