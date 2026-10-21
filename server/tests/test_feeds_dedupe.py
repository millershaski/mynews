from sqlalchemy import func, select

from app.collector import run_collection
from app.feeds import FEEDS, normalize_url, parse_feed
from app.models import Article
from conftest import fixture_items, make_fetcher

RSS = b"""<?xml version="1.0"?>
<rss version="2.0"><channel><title>t</title>
<item><title>First &amp; <b>bold</b></title><link>https://www.example.com/a/1/?utm_source=rss&amp;id=5</link>
<description>&lt;p&gt;Hello   world&lt;/p&gt;</description><pubDate>Mon, 19 Oct 2026 10:00:00 GMT</pubDate></item>
<item><title>No date</title><link>https://example.com/a/2</link><description>x</description></item>
<item><title></title><link>https://example.com/a/3</link></item>
</channel></rss>"""


def test_normalize_url_strips_tracking_and_cosmetics():
    a = normalize_url("http://www.Example.com/news/story/?utm_source=rss&utm_medium=x&id=7&fbclid=abc#top")
    b = normalize_url("https://example.com/news/story?id=7")
    assert a == b == "https://example.com/news/story?id=7"


def test_normalize_url_keeps_meaningful_query():
    assert normalize_url("https://example.com/s?id=1") != normalize_url("https://example.com/s?id=2")


def test_parse_feed_cleans_text_and_handles_missing_dates():
    items = parse_feed({"id": "bbc", "name": "BBC News"}, RSS)
    assert [i.title for i in items] == ["First & bold", "No date"]  # untitled item dropped
    assert items[0].excerpt == "Hello world"
    assert items[0].published_at.isoformat() == "2026-10-19T10:00:00+00:00"
    assert items[1].published_at is None


def test_same_url_with_tracking_params_is_stored_once(session_factory, settings, fake_ai):
    items = fixture_items({"G1"}) + fixture_items({"G1"}, url_suffix="?utm_source=twitter&utm_campaign=z")
    job = run_collection(session_factory, settings, ai=fake_ai, fetcher=make_fetcher(items), feeds=FEEDS)
    with session_factory() as s:
        assert s.scalar(select(func.count(Article.id))) == 3
    assert job.articles_fetched == 6
    assert job.articles_new == 3
    assert job.details["dates"]["duplicates_in_run"] == 3


def test_second_run_does_not_duplicate_and_updates_changed_text(session_factory, settings, fake_ai):
    items = fixture_items({"G1"})
    run_collection(session_factory, settings, ai=fake_ai, fetcher=make_fetcher(items), feeds=FEEDS)
    items[0].title = items[0].title + " (updated)"
    job = run_collection(session_factory, settings, ai=fake_ai, fetcher=make_fetcher(items), feeds=FEEDS)
    assert job.articles_new == 0
    assert job.articles_updated == 1
    with session_factory() as s:
        assert s.scalar(select(func.count(Article.id))) == 3
        assert s.scalar(select(Article.title).where(Article.guid == items[0].guid)).endswith("(updated)")
