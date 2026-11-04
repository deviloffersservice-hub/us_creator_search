"""The India market settings (MARKET_NAME / MARKET_SUBDOMAIN / follower range) offline."""
import importlib

import pytest

from creator_search import config, rules
from creator_search.linkedin import Author, PostPage


@pytest.fixture
def india(monkeypatch):
    monkeypatch.setattr(config, "MARKET_NAME", "India")
    monkeypatch.setattr(config, "MARKET_SUBDOMAIN", "in")
    monkeypatch.setattr(config, "MIN_FOLLOWERS", 10_000)
    monkeypatch.setattr(config, "MAX_FOLLOWERS", 200_000)


def _page(country, followers, kind="in"):
    return PostPage(url="u", author=Author("A", "a", kind, country, followers), published=None,
                    total_posts=1, text="")


def test_india_creator_in_range_passes(india):
    assert rules.profile_failures(_page("in", 150_000)) == []
    assert rules.location_signal(_page("in", 150_000)) == "India (in.linkedin.com)"


def test_india_rejects_us_profiles_and_out_of_range(india):
    assert rules.profile_failures(_page("www", 50_000)) == ["not India-based (LinkedIn domain: www)"]
    assert rules.profile_failures(_page("in", 7_000)) == ["7,000 followers (outside 10,000-200,000)"]
    assert rules.profile_failures(_page("in", 250_000)) == ["250,000 followers (outside 10,000-200,000)"]


def test_us_defaults_unchanged():
    assert config.MARKET_NAME == "US" and config.MARKET_SUBDOMAIN == "www"
    assert rules.location_signal(_page("www", 20_000)) == "US (www.linkedin.com)"
    assert _page("www", 20_000).author.in_market and not _page("in", 20_000).author.in_market


def test_india_company_pages_follow_indian_companies(india):
    from v2.sources import parse_company_page
    page = """<a href="https://in.linkedin.com/company/razorpay?trk=similar-pages">Razorpay</a>
    <a href="https://www.linkedin.com/company/globex">Globex</a>"""
    assert parse_company_page(page)[1] == ["razorpay"]


def test_india_search_terms_and_companies(monkeypatch):
    from v2 import phrases
    monkeypatch.setattr(config, "MARKET_NAME", "India")
    try:
        p = importlib.reload(phrases)
        terms = p.for_niche("Corporate Finance & Accounting")
        assert terms[:2] == ["chartered accountant", "CA India"]
        assert "CPA" in terms  # the general terms are still used, after the India ones
        assert p.COMPANIES is p.COMPANIES_INDIA
    finally:
        monkeypatch.undo()
        p = importlib.reload(phrases)
    assert p.COMPANIES is p.COMPANIES_US and "chartered accountant" not in p.for_niche("Corporate Finance & Accounting")
