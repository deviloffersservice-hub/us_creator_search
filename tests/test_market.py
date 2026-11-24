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
        assert "CPA India" in terms and "CPA" not in terms  # general terms get "India" added
        assert p.COMPANIES is p.COMPANIES_INDIA
    finally:
        monkeypatch.undo()
        p = importlib.reload(phrases)
    assert p.COMPANIES is p.COMPANIES_US and "chartered accountant" not in p.for_niche("Corporate Finance & Accounting")


class _QualTab:
    def __init__(self, n):
        self._rows = [{"_row": i + 2, "Name": f"P{i}", "Latest Post URL": f"https://x/{i}",
                       "Followers": "20,000", "Status": "Active"} for i in range(n)]
        self.written = {}

    def rows(self):
        return [dict(r) for r in self._rows]

    def set(self, row, values):
        self.written[row] = values

    def flush(self):
        pass


def _recheck(monkeypatch, countries, tab=False):
    import recheck

    want_tab, tab = tab, _QualTab(len(countries))
    pages = iter(countries)

    class _Fetcher:
        def post(self, url):
            return _page(next(pages), 20_000)

    monkeypatch.setattr(recheck, "Sheet", lambda: type("S", (), {"qualified": lambda self: tab})())
    monkeypatch.setattr(recheck, "Fetcher", _Fetcher)
    assert recheck.run(len(countries)) == 0
    return tab if want_tab else {r: v["Status"] for r, v in tab.written.items()}


def test_recheck_marks_a_real_move(india, monkeypatch):
    got = _recheck(monkeypatch, ["in", "www", "in"])
    assert got[3] == "Moved outside India" and got[2] == got[4] == "Inactive"


def test_recheck_stops_when_linkedin_shows_everyone_outside_the_market(india, monkeypatch):
    # 24 Nov 2026: every India profile suddenly on www.linkedin.com; 150 marked "Moved outside India".
    tab = _recheck(monkeypatch, ["in"] + ["www"] * 20, tab=True)
    assert tab.written[2]["Status"] == "Inactive"
    held = [tab.written[r] for r in range(3, 13)]  # the 10 "moved": only stamped, status unchanged
    assert all("Status" not in v and v["Last Checked (IST)"] for v in held)
    assert len(tab.written) == 11  # the rest wait for a later run


def test_recheck_keeps_a_long_run_of_real_moves_to_mixed_countries(india, monkeypatch):
    countries = ["www", "fr", "it", "www", "ae", "uk", "www", "sg", "ca", "www", "au", "in"]
    got = _recheck(monkeypatch, countries)
    assert [got[r] for r in range(2, 13)] == ["Moved outside India"] * 11 and got[13] == "Inactive"
