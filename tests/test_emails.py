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

    def searcher(q, backend):
        calls.append(q)
        if len(calls) < 2:
            return []
        return [res("https://in.linkedin.com/in/khanakarodia", "khanakarodia05@gmail.com")]

    found, status = emails.find(p, searcher, pause=0)
    assert status == "Found" and len(calls) == 2 and "Khanak Arodia" in calls[0]

    def refuse(q, backend):
        raise SearchRefused(q)

    assert emails.find(p, refuse, pause=0) == (None, "Error")
    assert emails.find(p, lambda q, b: [], pause=0) == (None, "Error")  # never saw their page
    own_no_email = [res("https://in.linkedin.com/in/khanakarodia", "HR leader")]
    assert emails.find(p, lambda q, b: own_no_email, pause=0) == (None, "Not found")


def test_find_searches_by_profile_link_when_namesakes_hide_their_page():
    p = person(name="Aman Garg", slug="amangarg4")
    namesakes = [res("https://in.linkedin.com/in/aman-garg-0860741a7", "aman.other@gmail.com")]
    tried = []

    def searcher(q, backend):
        tried.append(q)
        if "amangarg4" in q:
            return [res("https://in.linkedin.com/in/amangarg4", "Collab: amangarg.creates@gmail.com")]
        return namesakes

    found, status = emails.find(p, searcher, pause=0)
    assert status == "Found" and found.email == "amangarg.creates@gmail.com"
    assert tried[-1] == "amangarg4 linkedin gmail" and len(tried) == 4
    # Engines answered every time but only with namesakes: not a block, so not retried forever
    assert emails.find(p, lambda q, b: namesakes, pause=0) == (None, "Page not found")
    assert emails.row_for(p, None, "Page not found", __import__("datetime").datetime.now())["Rank"] == 4


def test_find_skips_link_search_once_their_page_showed():
    p = person()
    tried = []
    own_no_email = [res("https://in.linkedin.com/in/khanakarodia", "HR leader")]
    assert emails.find(p, lambda q, b: tried.append(q) or own_no_email, pause=0) == (None, "Not found")
    assert len(tried) == len(emails.PLAN)


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
    rows = {"a": {"Status": "Found"}, "b": {"Status": "Not found"}, "c": {"Status": "Error"}, "d": {"Status": "Recheck"}}
    assert [p.slug for p in emails.due(ps, rows)] == ["d", "c"]  # throttled ones last


def test_due_retries_errors_least_recently_searched_first():
    ps = [person(slug="a"), person(slug="b"), person(slug="c")]
    rows = {"a": {"Status": "Error", "Searched (IST)": "2026-11-28 07:10"},
            "b": {"Status": "Error", "Searched (IST)": "2026-11-27 22:00"},
            "c": {"Status": "Error", "Searched (IST)": "2026-11-28 07:10"}}
    assert [p.slug for p in emails.due(ps, rows)] == ["b", "a", "c"]  # ties keep the score order


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
    def search(q, backend=None):
        if hits == "refuse":
            raise SearchRefused(q)
        i = int(q.split("Person ")[1].split()[0].strip('"'))
        if i in hits:
            return [emails_res(i)]
        return [res(f"https://in.linkedin.com/in/person-{i}", "Creator. No email here.")]
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
    emails.finish(500)
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
    emails.finish(2)
    assert out.read_text().strip().endswith("left=0")
    emails.finish(3)
    assert out.read_text().strip().endswith("left=4")  # persons 4, 1, 3, 5 never searched
    calls = []
    _run(monkeypatch, book, target=3, searcher=lambda q, b: calls.append(q) or _searcher({0, 2, 4})(q))
    assert sum(r["Status"] == "Found" for r in _tab(book)) == 3
    assert not any("Person 0 " in c or '"Person 0"' in c for c in calls)  # already searched


def test_run_gives_up_when_engines_refuse_and_retries_later(monkeypatch, tmp_path):
    monkeypatch.setattr(emails, "GIVE_UP_AFTER", 2)
    out = tmp_path / "o"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    book = FakeBook({"India Creator Scores": _scores(5)})
    assert _run(monkeypatch, book, searcher=_searcher("refuse")) == 2
    assert [r["Status"] for r in _tab(book)] == ["Error", "Error"]
    emails.finish(500)
    assert out.read_text() == "left=5\n"  # the 2 refused are searched again, plus 3 never tried
    _run(monkeypatch, book, searcher=_searcher({0}))
    assert sum(r["Status"] == "Found" for r in _tab(book)) == 1 and len(_tab(book)) == 5


def test_dry_run_writes_nothing(monkeypatch):
    book = FakeBook({"India Creator Scores": _scores(2)})
    _run(monkeypatch, book, dry_run=True, searcher=_searcher({0}))
    assert "India Creator Emails" not in book.tabs


def test_own_texts_removes_text_bled_in_from_the_next_result():
    results = [
        res("https://in.linkedin.com/in/rajinder", "Founder of Trident. He can be reached at rajendra.g@gmail.com today",
            "Rajinder Gupta - TridentRajendra Gupta - Top Database"),
        res("https://in.linkedin.com/in/rajendraguptas", "He can be reached at rajendra.g@gmail.com today",
            "Rajendra Gupta - Top Database"),
    ]
    texts = emails.own_texts(results)
    assert "rajendra.g@gmail.com" not in texts[0] and "Rajendra" not in texts[0]
    assert "rajendra.g@gmail.com" in texts[1]
    p = person(name="Rajinder Gupta", slug="rajinder")
    assert emails.pick(results, p) is None


def test_pick_rejects_an_email_that_also_shows_on_someone_elses_page():
    p = person()
    results = [res("https://in.linkedin.com/in/khanakarodia", "jobs: hiring.team@gmail.com"),
               res("https://in.linkedin.com/in/other", "jobs: hiring.team@gmail.com and more")]
    assert emails.pick(results, p) is None


def test_context_shows_words_around_the_email():
    text = "x " * 200 + "📩 Email: Leena.kakani@gmail.com · Experience: Brand"
    c = emails.context(text, "leena.kakani@gmail.com", width=20)
    assert c.startswith("…") and "Leena.kakani@gmail.com · Experience" in c


def test_shards_split_people_without_overlap(monkeypatch):
    book = FakeBook({"India Creator Scores": _scores(6)})
    seen = []
    for k in range(3):
        _run(monkeypatch, book, shard=k, shards=3, searcher=lambda q, b: seen.append(q) or [])
    rows = _tab(book)
    assert len(rows) == 6 and len({r["Profile URL"] for r in rows}) == 6
    assert book.tabs["India Creator Emails"].sorted_by is None  # shards never sort
    emails.finish(500)
    assert book.tabs["India Creator Emails"].sorted_by is not None


def test_find_tries_the_next_engine_when_one_shows_nothing():
    p = person()
    tried = []

    def searcher(q, backend):
        tried.append(backend)
        if backend == "auto":
            return [res("https://in.linkedin.com/in/khanakarodia", "Open to connect: khanakarodia05@gmail.com")]
        return [res("https://in.linkedin.com/in/khanakarodia", "HR leader")]

    found, status = emails.find(p, searcher, pause=0)
    assert status == "Found" and tried == ["yahoo", "yahoo", "auto"]


def test_search_turns_engine_errors_into_refused(monkeypatch):
    import ddgs

    class Boom:
        def text(self, *a, **k):
            raise RuntimeError("202 Ratelimit")

    class Empty:
        def text(self, *a, **k):
            raise RuntimeError("No results found.")

    monkeypatch.setattr(ddgs, "DDGS", Boom)
    import pytest
    with pytest.raises(SearchRefused):
        emails.search("q", "yahoo")
    monkeypatch.setattr(ddgs, "DDGS", Empty)
    assert emails.search("q", "yahoo") == []
