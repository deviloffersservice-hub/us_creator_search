"""The qualification rules, in one place, shared by bulk prescreen, verify and recheck."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from . import config
from .linkedin import PostPage


def followers_ok(n: int | None) -> bool:
    return n is not None and config.MIN_FOLLOWERS <= n <= config.MAX_FOLLOWERS


def recent(dt: datetime | None, now: datetime | None = None, days: int | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    days = config.MAX_POST_AGE_DAYS if days is None else days
    return dt is not None and now - dt <= timedelta(days=days)


def profile_failures(page: PostPage, expected_slug: str | None = None) -> list[str]:
    """Everything except activity. Empty list == person, US, follower count in range."""
    a = page.author
    reasons = []
    if expected_slug and a.slug and a.slug != expected_slug:
        reasons.append(f"post author is {a.slug}, not {expected_slug}")
    if not a.is_person:
        reasons.append(f"not a personal profile ({a.kind or 'unknown'})")
    if not a.is_us:
        reasons.append(f"not US-based (LinkedIn domain: {a.country or 'unknown'})")
    if a.followers is None:
        reasons.append("follower count not shown")
    elif not followers_ok(a.followers):
        reasons.append(f"{a.followers:,} followers (outside {config.MIN_FOLLOWERS:,}-{config.MAX_FOLLOWERS:,})")
    return reasons


def newer(a: datetime | None, b: datetime | None) -> datetime | None:
    if a is None or b is None:
        return a or b
    return max(a, b)


def location_signal(page: PostPage) -> str:
    c = page.author.country
    return "US (www.linkedin.com)" if c == config.US_SUBDOMAIN else f"{c}.linkedin.com"
