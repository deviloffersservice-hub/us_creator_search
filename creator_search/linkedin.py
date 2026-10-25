"""Logged-out LinkedIn access: fetch public post pages and parse them.

Public post pages (linkedin.com/posts/...) are served to logged-out visitors and carry:
  * JSON-LD with the author's name, profile URL and exact follower count, plus datePublished
  * an author card with the author's total post count
  * a "More Relevant Posts" list: other posts, each with its author's profile link
    (country subdomain included), follower count and activity id

Profile pages (/in/...) are NOT used: they return 999/429 to logged-out traffic.
"""
from __future__ import annotations

import html as htmllib
import json
import logging
import random
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import quote, unquote

import requests

from . import config

log = logging.getLogger(__name__)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:131.0) Gecko/20100101 Firefox/131.0",
]

ACTOR_RE = re.compile(r"https://([a-z]{2,3})\.linkedin\.com/(in|company|school|showcase)/([^\"?&/#\s]+)")
POST_URL_RE = re.compile(r"https://(?:[a-z]{2,3}\.)?linkedin\.com/posts/[^\"'?&#\s<>]+")
ACTIVITY_RE = re.compile(r"(?:activity|ugcPost|share)[-:](\d{18,20})")
FOLLOWERS_RE = re.compile(r"([\d,]+)\s+followers")


class Blocked(Exception):
    """LinkedIn is refusing logged-out requests (999 / 429 / authwall)."""


class NotFound(Exception):
    pass


def activity_time(activity_id: int | str) -> datetime:
    """LinkedIn activity ids are snowflakes: the top 41 bits are epoch milliseconds."""
    return datetime.fromtimestamp((int(activity_id) >> 22) / 1000, tz=timezone.utc)


def activity_id_from_url(url: str) -> str | None:
    m = ACTIVITY_RE.search(url)
    return m.group(1) if m else None


def normalize_slug(slug: str) -> str:
    return unquote(slug).strip().strip("/").lower()


def profile_url(slug: str) -> str:
    return f"https://www.linkedin.com/in/{quote(slug, safe='-_.~%')}"


def slug_from_post_url(url: str) -> str | None:
    m = re.search(r"/posts/([^/?#]+?)_", url)
    return normalize_slug(m.group(1)) if m else None


def canonical_post_url(url: str) -> str:
    """Strip tracking params and force www so the same post always dedupes."""
    url = htmllib.unescape(url).split("?")[0].split("#")[0]
    return re.sub(r"^https://(?:[a-z]{2,3}\.)?linkedin\.com", "https://www.linkedin.com", url)


def to_int(text) -> int | None:
    if text is None or text == "":
        return None
    if isinstance(text, (int, float)):
        return int(text)
    digits = re.sub(r"[^\d]", "", str(text))
    return int(digits) if digits else None


def _visible_text(fragment: str) -> str:
    text = re.sub(r"<[^>]+>", " ", fragment)
    return re.sub(r"\s+", " ", htmllib.unescape(text)).strip()


@dataclass
class Author:
    name: str = ""
    slug: str = ""
    kind: str = ""  # "in" for people, "company" etc. otherwise
    country: str = ""  # subdomain of the author's public URL: "www" == US
    followers: int | None = None

    @property
    def is_person(self) -> bool:
        return self.kind == "in"

    @property
    def is_us(self) -> bool:
        return self.country == config.US_SUBDOMAIN


@dataclass
class RelatedPost:
    url: str
    activity_id: str | None
    author: Author
    text: str = ""
    reactions: int = 0

    @property
    def published(self) -> datetime | None:
        return activity_time(self.activity_id) if self.activity_id else None


@dataclass
class PostPage:
    url: str
    author: Author
    published: datetime | None
    total_posts: int | None
    text: str
    related: list[RelatedPost] = field(default_factory=list)


def _author_from_url(url: str, name: str = "", followers: int | None = None) -> Author:
    m = ACTOR_RE.search(url or "")
    if not m:
        return Author(name=name, followers=followers)
    return Author(name=name.strip(), slug=normalize_slug(m.group(3)), kind=m.group(2),
                  country=m.group(1), followers=followers)


def _follow_count(stats) -> int | None:
    for s in stats if isinstance(stats, list) else [stats]:
        if isinstance(s, dict) and "Follow" in str(s.get("interactionType", "")):
            return to_int(s.get("userInteractionCount"))
    return None


def _main_jsonld(page: str) -> dict:
    for m in re.finditer(r'<script type="application/ld\+json">(.*?)</script>', page, re.S):
        try:
            data = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        for item in data if isinstance(data, list) else [data]:
            if isinstance(item, dict) and (item.get("author") or item.get("creator")):
                return item
    return {}


def parse_post_page(page: str, url: str) -> PostPage:
    ld = _main_jsonld(page)
    raw_author = ld.get("author") or ld.get("creator") or {}
    if isinstance(raw_author, list):
        raw_author = next((a for a in raw_author if isinstance(a, dict)), {})
    author = _author_from_url(raw_author.get("url", ""), raw_author.get("name", ""),
                              _follow_count(raw_author.get("interactionStatistic")))

    if author.followers is None:
        m = re.search(r'public-post-author-card__followers[^>]*>\s*([\d,]+)\s+followers', page)
        author.followers = to_int(m.group(1)) if m else None
    m = re.search(r'public-post-author-card__posts.*?>\s*([\d,]+)\s+Posts?\s*<', page, re.S)
    total_posts = to_int(m.group(1)) if m else None

    published = None
    if ld.get("datePublished"):
        published = datetime.fromisoformat(ld["datePublished"].replace("Z", "+00:00"))
    elif (aid := activity_id_from_url(url)):
        published = activity_time(aid)

    text = " ".join(str(ld.get(k, "")) for k in ("headline", "articleBody", "description", "keywords"))
    return PostPage(url=canonical_post_url(url), author=author, published=published,
                    total_posts=total_posts, text=text, related=parse_related(page))


def parse_related(page: str) -> list[RelatedPost]:
    start = page.find("related-posts")
    if start < 0:
        return []
    section = page[start:]
    out = []
    for chunk in re.split(r'<li class="mb-1\.5">', section)[1:]:
        post = POST_URL_RE.search(chunk)
        if not post:
            continue
        url = canonical_post_url(post.group(0))
        aid_m = re.search(r'data-activity-urn="urn:li:activity:(\d+)"', chunk)
        actor = ACTOR_RE.search(chunk)
        name_m = re.search(r'aria-label="Post by ([^"]+)"', chunk)
        followers_m = FOLLOWERS_RE.search(_visible_text(chunk[:6000]))
        author = _author_from_url(actor.group(0) if actor else "",
                                  htmllib.unescape(name_m.group(1)) if name_m else "",
                                  to_int(followers_m.group(1)) if followers_m else None)
        if not author.slug:
            author.slug = slug_from_post_url(url) or ""
        body = re.search(r'attributed-text-segment-list__content[^>]*>(.*?)</p>', chunk, re.S)
        reactions = re.search(r'data-num-reactions="(\d+)"', chunk)
        out.append(RelatedPost(url=url, activity_id=aid_m.group(1) if aid_m else activity_id_from_url(url),
                               author=author, text=_visible_text(body.group(1)) if body else "",
                               reactions=int(reactions.group(1)) if reactions else 0))
    return out


class Fetcher:
    """Polite logged-out fetcher with backoff. Raises Blocked when LinkedIn shuts us out."""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        })
        self.consecutive_blocks = 0
        self.requests_made = 0
        self._last = 0.0

    def _sleep(self):
        wait = random.uniform(config.REQUEST_DELAY_MIN, config.REQUEST_DELAY_MAX)
        elapsed = time.monotonic() - self._last
        if elapsed < wait:
            time.sleep(wait - elapsed)
        self._last = time.monotonic()

    def get(self, url: str) -> str:
        url = canonical_post_url(url)
        for attempt in range(3):
            self._sleep()
            self.requests_made += 1
            try:
                r = self.session.get(url, timeout=25, allow_redirects=True)
            except requests.RequestException as e:
                log.warning("network error %s: %s", url, e)
                time.sleep(5 * (attempt + 1))
                continue
            final = r.url
            if r.status_code == 404:
                self.consecutive_blocks = 0
                raise NotFound(url)
            if r.status_code == 200 and re.search(r"/(signup|login|uas)/", final):
                # This one post wants a login (e.g. restricted visibility). Not an IP block.
                raise NotFound(f"{url} redirects to login")
            blocked = r.status_code in (429, 999) or "/authwall" in final
            if r.status_code == 200 and not blocked:
                self.consecutive_blocks = 0
                return r.text
            if blocked:
                self.consecutive_blocks += 1
                log.warning("blocked (%s) on %s [%d in a row]", r.status_code, url, self.consecutive_blocks)
                if self.consecutive_blocks >= config.MAX_CONSECUTIVE_BLOCKS:
                    raise Blocked(f"{self.consecutive_blocks} blocked responses in a row")
                time.sleep(20 * (attempt + 1) + random.uniform(0, 10))
                self.session.headers["User-Agent"] = random.choice(USER_AGENTS)
            else:
                log.warning("HTTP %s on %s", r.status_code, url)
                time.sleep(5)
        raise NotFound(f"gave up on {url}")

    def post(self, url: str) -> PostPage:
        return parse_post_page(self.get(url), url)
