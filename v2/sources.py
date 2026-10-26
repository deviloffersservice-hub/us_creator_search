"""Extra discovery sources for v2, found by reviewing open-source LinkedIn tools.

* Company pages (linkedin.com/company/<slug>/) are public. Each lists the company's ~10 latest
  posts and ~20 "similar pages", so they're an endless supply of fresh, industry-specific posts.
* Search across several engines (ddgs backends), one query at a time spread over the run, since
  engines rate-limit per time window.
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


# "auto" lets ddgs pick among its engines itself; in live runs it answered far more often than any
# single engine. bing, google and yandex aren't available in ddgs 9.x (they silently become "auto").
BACKENDS = ["auto", "duckduckgo", "brave", "yahoo", "startpage", "mojeek"]


class Searcher:
    """One search at a time, spread over the whole run (engines rate-limit per time window, so a
    query every minute or so gets far more answers than a burst at the start).

    Each query tries the engines in BACKENDS order until one answers. An engine is dropped for the
    run after ENGINE_MAX_FAILS failures in a row. A query no engine answers is often just a phrase
    with no posts this week, so it doesn't count against the engines by itself; instead the next
    search turns are skipped (1, 2, 4... turns) to give the engines time to cool down.
    """

    ENGINE_MAX_FAILS = 4

    def __init__(self, per_query: int = 25):
        self.per_query = per_query
        self.fails = {b: 0 for b in BACKENDS}
        self.dead_in_a_row = 0
        self.cooldown = 0
        self.answered = self.asked = 0
        try:
            from ddgs import DDGS
            self._ddgs = DDGS
        except ImportError:
            self._ddgs = None
            self.fails = {}
        for noisy in ("primp", "ddgs", "httpx"):
            logging.getLogger(noisy).setLevel(logging.WARNING)

    @property
    def alive(self) -> bool:
        return bool(self.fails)

    def ready(self) -> bool:
        """Call once per search turn; False while cooling down after unanswered queries."""
        if self.cooldown > 0:
            self.cooldown -= 1
            return False
        return self.alive

    def search(self, term: str) -> list[str]:
        self.asked += 1
        failed = []
        for backend in list(self.fails):
            try:
                results = self._ddgs().text(f"site:linkedin.com/posts {term}", region="us-en", timelimit="w",
                                            max_results=self.per_query, backend=backend)
                urls = [canonical_post_url(r["href"]) for r in results if POST_URL_RE.match(r.get("href", ""))]
            except Exception as e:  # blocked, captcha, timeout or no results
                urls = []
                log.debug("search %r on %s failed (%s)", term, backend, str(e)[:80])
            if urls:
                for b in failed:  # another engine answered this phrase, so those failures were real
                    self._failed(b)
                self.fails[backend] = 0
                self.dead_in_a_row = 0
                self.answered += 1
                log.info("search %r on %s -> %d posts", term, backend, len(urls))
                return list(dict.fromkeys(urls))
            failed.append(backend)
            time.sleep(2)
        self.dead_in_a_row += 1
        self.cooldown = 2 ** (self.dead_in_a_row - 1)
        if self.dead_in_a_row >= 3:  # three phrases in a row with no answer anywhere: engines are refusing
            for b in failed:
                self._failed(b)
        log.info("search %r: no engine answered (cooling down for %d turns)", term, self.cooldown)
        return []

    def _failed(self, backend: str):
        if backend not in self.fails:
            return
        self.fails[backend] += 1
        if self.fails[backend] >= self.ENGINE_MAX_FAILS:
            log.info("dropping search engine %s for this run", backend)
            del self.fails[backend]
