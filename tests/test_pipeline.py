"""Offline tests: parsing of LinkedIn's public post page markup and the qualification rules.

The HTML below is a trimmed copy of the structure LinkedIn serves logged-out visitors.
"""
import json
from datetime import datetime, timedelta, timezone

import pytest

from creator_search import niches, rules
from creator_search.linkedin import (Author, PostPage, activity_time, canonical_post_url, parse_post_page,
                                     slug_from_post_url)
from creator_search.sheets import fmt_time, parse_time

NOW = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)


def page_html(author_url="https://www.linkedin.com/in/jane-doe", followers=12345, published="2026-10-20T10:00:00.000Z",
              posts="1,220"):
    ld = {"@context": "http://schema.org", "@type": "SocialMediaPosting", "datePublished": published,
          "headline": "How we grew our B2B sales pipeline",
          "author": {"name": "Jane Doe", "url": author_url, "@type": "Person",
                     "interactionStatistic": {"@type": "InteractionCounter",
                                              "interactionType": "http://schema.org/FollowAction",
                                              "userInteractionCount": followers}}}
    return f"""<html><head><script type="application/ld+json">{json.dumps(ld)}</script></head><body>
<p class="public-post-author-card__followers text-sm"> 12,345 followers </p>
<ul><li class="public-post-author-card__posts mr-1"><a class="link" href="x"> {posts} Posts </a></li></ul>
<section class="core-section-container my-3 related-posts"><h2> More Relevant Posts </h2><ul>
<li class="mb-1.5"><div class="link-overlay relative">
  <a aria-label="Post by John Smith" href="https://www.linkedin.com/posts/johnsmith_leadership-activity-7516240991830376448-XUsn"></a>
  <article class="main-feed-activity-card related-posts__crosslink" data-activity-urn="urn:li:activity:7516240991830376448">
  <a href="https://www.linkedin.com/in/johnsmith?trk=public_post_feed-actor-name"> John Smith </a>
  <p class="attributed-text-segment-list__content">Great leaders listen first.</p></article></div></li>
<li class="mb-1.5"><div class="link-overlay relative">
  <a aria-label="Post by Acme" href="https://www.linkedin.com/posts/acme_news-activity-7518362934549479424-cwdt"></a>
  <article class="main-feed-activity-card related-posts__crosslink" data-activity-urn="urn:li:activity:7518362934549479424">
  <a href="https://www.linkedin.com/company/acme?trk=public_post_feed-actor-name"> Acme </a>
  <p class="!text-xs"> 34,000 followers </p></article></div></li>
<li class="mb-1.5"><div class="link-overlay relative">
  <a aria-label="Post by Priya K" href="https://www.linkedin.com/posts/priyak_ai-activity-7515267296534126592-woYh"></a>
  <article class="main-feed-activity-card related-posts__crosslink" data-activity-urn="urn:li:activity:7515267296534126592">
  <a href="https://in.linkedin.com/in/priyak?trk=public_post_feed-actor-name"> Priya K </a></article></div></li>
</ul></section></body></html>"""


def test_activity_id_is_a_timestamp():
    assert activity_time(7518662320697401346) == datetime(2026, 10, 21, 13, 19, 47, 245000, tzinfo=timezone.utc)


def test_parse_main_post():
    p = parse_post_page(page_html(), "https://uk.linkedin.com/posts/jane-doe_sales-activity-7517000000000000000-abcd?trk=x")
    assert p.url == "https://www.linkedin.com/posts/jane-doe_sales-activity-7517000000000000000-abcd"
    assert (p.author.name, p.author.slug, p.author.kind, p.author.country) == ("Jane Doe", "jane-doe", "in", "www")
    assert p.author.followers == 12345
    assert p.total_posts == 1220
    assert p.published == datetime(2026, 10, 20, 10, tzinfo=timezone.utc)


def test_parse_related_posts_keep_country_and_kind():
    rel = parse_post_page(page_html(), "https://www.linkedin.com/posts/x_y-activity-7517000000000000000-a").related
    assert [(r.author.slug, r.author.kind, r.author.country) for r in rel] == [
        ("johnsmith", "in", "www"), ("acme", "company", "www"), ("priyak", "in", "in")]
    assert rel[0].published.date().isoformat() == "2026-10-14"
    assert rel[0].text == "Great leaders listen first."
    assert rel[1].author.followers == 34000


@pytest.mark.parametrize("url,followers,expected", [
    ("https://www.linkedin.com/in/jane-doe", 12345, []),
    ("https://uk.linkedin.com/in/jane-doe", 12345, ["not US-based"]),
    ("https://www.linkedin.com/in/jane-doe", 4999, ["followers (outside"]),
    ("https://www.linkedin.com/in/jane-doe", 100001, ["followers (outside"]),
    ("https://www.linkedin.com/company/jane-doe", 12345, ["not a personal profile"]),
])
def test_profile_rules(url, followers, expected):
    p = parse_post_page(page_html(author_url=url, followers=followers), "https://www.linkedin.com/posts/a_b-activity-1")
    failures = rules.profile_failures(p)
    assert len(failures) == len(expected)
    for want, got in zip(expected, failures):
        assert want in got


def test_recent_window():
    assert rules.recent(NOW - timedelta(days=13), NOW)
    assert not rules.recent(NOW - timedelta(days=15), NOW)
    assert not rules.recent(None, NOW)


def _page(published, total_posts, followers=20000, country="www"):
    return PostPage(url="u", author=Author("Jane", "jane", "in", country, followers), published=published,
                    total_posts=total_posts, text="")


def test_verify_decisions():
    from verify import decide
    added = fmt_time(NOW - timedelta(days=1))
    row = {"Added At (IST)": added, "Verified At (IST)": "", "Total Posts": "100", "Newest Post Seen": ""}
    # Fresh post -> qualified straight away.
    assert decide(_page(NOW - timedelta(days=3), 100), row, "jane", NOW)[0] == "Qualified"
    # Old post, post count unchanged -> keep watching.
    assert decide(_page(NOW - timedelta(days=40), 100), row, "jane", NOW)[0] == "Watching"
    # Old post, but post count went up since the last check -> they posted, qualified.
    status, reason, activity, _ = decide(_page(NOW - timedelta(days=40), 101), row, "jane", NOW)
    assert status == "Qualified" and "100 -> 101" in reason and activity == parse_time(added)
    # Watched for 14+ days with no new post -> rejected as inactive.
    old_row = dict(row, **{"Added At (IST)": fmt_time(NOW - timedelta(days=15))})
    assert decide(_page(NOW - timedelta(days=40), 100), old_row, "jane", NOW)[0] == "Rejected"
    # Profile failures always win.
    assert decide(_page(NOW, 101, followers=200), row, "jane", NOW)[0] == "Rejected"
    assert decide(_page(NOW, 101, country="ca"), row, "jane", NOW)[0] == "Rejected"


def test_helpers():
    assert slug_from_post_url("https://www.linkedin.com/posts/jane-doe_sales-activity-1") == "jane-doe"
    assert canonical_post_url("https://de.linkedin.com/posts/a_b?trk=1&amp;x=2") == "https://www.linkedin.com/posts/a_b"
    assert niches.classify("Tips for your B2B sales pipeline and prospecting") == "Sales"
    assert niches.classify("") == niches.OTHER
