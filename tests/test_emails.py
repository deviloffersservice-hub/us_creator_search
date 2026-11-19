from v2 import emails
from v2.emails import Person, SearchRefused


def person(name="Khanak Arodia", slug="khanakarodia", tier="Strong", score=90):
    return Person(slug, name, f"https://www.linkedin.com/in/{slug}", tier, score, "HR", "1000")


def res(href, body, title=""):
    return {"href": href, "body": body, "title": title}


def test_clean_name_drops_titles_and_taglines():
    assert emails.clean_name("Deepa Chandrasekaran, Career and Leadership Coach") == "Deepa Chandrasekaran"
    assert emails.clean_name("Dr. Birendra Singh") == "Birendra Singh"
    assert emails.clean_name("Rajdeep Bhattacharya,DBA(HC)") == "Rajdeep Bhattacharya"
    assert emails.clean_name("Kanishk | Fintech") == "Kanishk"


def test_emails_in_skips_placeholders_and_images():
    text = "Mail: Riya.U@Gmail.com. or your@example.com, logo@2x.png, collab@brand.in"
    assert emails.emails_in(text) == ["riya.u@gmail.com", "collab@brand.in"]


def test_is_own_page_matches_profile_and_posts_only():
    assert emails.is_own_page("https://in.linkedin.com/in/khanakarodia", "khanakarodia")
    assert emails.is_own_page("https://in.linkedin.com/in/khanakarodia/?trk=x", "khanakarodia")
    assert emails.is_own_page("https://www.linkedin.com/posts/khanakarodia_tips-activity-1", "khanakarodia")
    assert not emails.is_own_page("https://in.linkedin.com/in/khanakarodia2", "khanakarodia")
    assert not emails.is_own_page("https://in.linkedin.com/in/kanishk-yadav", "kanishk")
    assert not emails.is_own_page("https://topmate.io/khanakarodia", "khanakarodia")


def test_confidence():
    p = person()
    assert emails.confidence("khanakarodia05@gmail.com", p) == "High"
    assert emails.confidence("collab.xyz@gmail.com", p) == "High"
    assert emails.confidence("nglmahfooz@gmail.com", p) == "Check"


def test_pick_ignores_other_peoples_pages_and_prefers_own_looking_email():
    p = person()
    results = [
        res("https://in.linkedin.com/in/someone-else", "reach me at khanak.fake@gmail.com"),
        res("https://in.linkedin.com/in/khanakarodia", "ops: zz12@gmail.com Open to connect: khanakarodia05@gmail.com"),
    ]
    found = emails.pick(results, p)
    assert found.email == "khanakarodia05@gmail.com"
    assert found.others == ("zz12@gmail.com",)
    assert found.source.endswith("/in/khanakarodia")
    assert emails.pick(results[:1], p) is None


def test_find_tries_queries_until_hit_and_reports_errors():
    p = person()
    calls = []

    def searcher(q):
        calls.append(q)
        if len(calls) < 2:
            return []
        return [res("https://in.linkedin.com/in/khanakarodia", "khanakarodia05@gmail.com")]

    found, status = emails.find(p, searcher, pause=0)
    assert status == "Found" and len(calls) == 2 and "Khanak Arodia" in calls[0]

    def refuse(q):
        raise SearchRefused(q)

    assert emails.find(p, refuse, pause=0) == (None, "Error")
    assert emails.find(p, lambda q: [], pause=0) == (None, "Not found")


def test_people_orders_strong_first_then_score_and_skips_others():
    rows = [
        {"Name": "A", "Profile URL": "https://www.linkedin.com/in/a", "Tier": "Possible", "Score": "99"},
        {"Name": "B", "Profile URL": "https://www.linkedin.com/in/b", "Tier": "Strong", "Score": "60"},
        {"Name": "C", "Profile URL": "https://www.linkedin.com/in/c", "Tier": "Strong", "Score": "95"},
        {"Name": "D", "Profile URL": "https://www.linkedin.com/in/d", "Tier": "Not a creator", "Score": "10"},
        {"Name": "C2", "Profile URL": "https://www.linkedin.com/in/c", "Tier": "Strong", "Score": "1"},
        {"Name": "E", "Profile URL": "", "Tier": "Strong", "Score": "x"},
    ]
    assert [p.name for p in emails.people(rows)] == ["C", "B", "A"]


def test_due_skips_done_and_retries_errors():
    ps = [person(slug="a"), person(slug="b"), person(slug="c"), person(slug="d")]
    rows = {"a": {"Status": "Found"}, "b": {"Status": "Not found"}, "c": {"Status": "Error"}}
    assert [p.slug for p in emails.due(ps, rows)] == ["c", "d"]


def test_row_for_sets_rank_by_status_and_confidence():
    p = person()
    found = emails.Found("khanakarodia05@gmail.com", (), "https://in.linkedin.com/in/khanakarodia", "s")
    from datetime import datetime, timezone
    now = datetime(2026, 11, 19, tzinfo=timezone.utc)
    assert emails.row_for(p, found, "Found", now)["Rank"] == 1
    assert emails.row_for(p, None, "Not found", now)["Rank"] == 3
    assert emails.row_for(p, None, "Error", now)["Email"] == ""


def test_report_writes_left(tmp_path, monkeypatch):
    out = tmp_path / "o"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    emails._report(7)
    assert out.read_text() == "left=7\n"


def test_pick_from_a_post_needs_an_address_that_looks_like_theirs():
    p = person(name="Dhairya Gangwani", slug="dhairya-gangwani")
    post = "https://www.linkedin.com/posts/dhairya-gangwani_hiring-activity-1"
    assert emails.pick([res(post, "Send CVs to anjali.shukla.careers@gmail.com")], p) is None
    assert emails.pick([res(post, "Collabs: dhairya.g@gmail.com")], p).email == "dhairya.g@gmail.com"
    profile = "https://in.linkedin.com/in/dhairya-gangwani"
    assert emails.pick([res(profile, "Write to zz12@gmail.com")], p).email == "zz12@gmail.com"


# --- whole run against the fake spreadsheet from test_creators ---------------------------------------

from tests.test_creators import FakeBook, FakeSheet, FakeWS  # noqa: E402


def _scores(n):
    head = ["Name", "Profile URL", "Niche", "Followers", "Tier", "Score"]
    rows = [[f"Person {i}", f"https://www.linkedin.com/in/person-{i}", "HR", "9000",
             "Strong" if i % 2 == 0 else "Possible", str(90 - i)] for i in range(n)]
    rows.append(["Org", "https://www.linkedin.com/in/org", "HR", "9", "Not a creator", "5"])
    return FakeWS("India Creator Scores", [head] + rows)


def _searcher(hits):
    """person-<i> has an email when i is in hits; 'refuse' raises for everyone."""
    def search(q):
        if hits == "refuse":
            raise SearchRefused(q)
        i = int(q.split("Person ")[1].split()[0].strip('"'))
        if i in hits:
            return [emails_res(i)]
        return []
    return search


def emails_res(i):
    return res(f"https://in.linkedin.com/in/person-{i}", f"collab: person{i}@gmail.com")


def _run(monkeypatch, book, **kw):
    FakeSheet.book = book
    monkeypatch.setattr(emails, "Sheet", FakeSheet)
    args = {"target": 500, "limit": 100, "max_minutes": 5, "dry_run": False, "pause": 0}
    args.update(kw)
    return emails.run(**args)


def _tab(book):
    v = book.tabs["India Creator Emails"].values
    return [dict(zip(v[0], r)) for r in v[1:]]


def test_run_writes_found_first_and_skips_non_creators(monkeypatch):
    book = FakeBook({"India Creator Scores": _scores(4)})
    assert _run(monkeypatch, book, searcher=_searcher({1, 2})) == 0
    rows = _tab(book)
    assert len(rows) == 4 and "Org" not in [r["Name"] for r in rows]
    assert [r["Status"] for r in rows[:2]] == ["Found", "Found"]
    assert {r["Email"] for r in rows[:2]} == {"person1@gmail.com", "person2@gmail.com"}


def test_run_stops_at_target_and_next_run_does_not_repeat(monkeypatch, tmp_path):
    out = tmp_path / "o"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    book = FakeBook({"India Creator Scores": _scores(6)})
    _run(monkeypatch, book, target=2, searcher=_searcher({0, 2, 4}))
    assert sum(r["Status"] == "Found" for r in _tab(book)) == 2
    assert out.read_text().strip().endswith("left=0")
    calls = []
    _run(monkeypatch, book, target=3, searcher=lambda q: calls.append(q) or _searcher({0, 2, 4})(q))
    assert sum(r["Status"] == "Found" for r in _tab(book)) == 3
    assert not any("Person 0 " in c or '"Person 0"' in c for c in calls)  # already searched


def test_run_gives_up_when_engines_refuse_and_retries_later(monkeypatch, tmp_path):
    monkeypatch.setattr(emails, "GIVE_UP_AFTER", 2)
    out = tmp_path / "o"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    book = FakeBook({"India Creator Scores": _scores(5)})
    assert _run(monkeypatch, book, searcher=_searcher("refuse")) == 2
    assert [r["Status"] for r in _tab(book)] == ["Error", "Error"]
    assert out.read_text() == "left=0\n"
    _run(monkeypatch, book, searcher=_searcher({0}))
    assert sum(r["Status"] == "Found" for r in _tab(book)) == 1 and len(_tab(book)) == 5


def test_dry_run_writes_nothing(monkeypatch):
    book = FakeBook({"India Creator Scores": _scores(2)})
    _run(monkeypatch, book, dry_run=True, searcher=_searcher({0}))
    assert "India Creator Emails" not in book.tabs
