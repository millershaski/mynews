"""Rolling 7-day window: what is stored, what expires, and what the API is allowed to serve."""
from datetime import timedelta

from sqlalchemy import func, select

from app.collector import expire_and_cleanup, run_collection
from app.feeds import FEEDS, FeedItem
from app.models import Article, BackgroundResource, Story, StoryBackground, Summary
from app.timeutil import utcnow
from app.views import cutoff_for, list_stories, story_detail
from conftest import fixture_items, make_fetcher, story_of


def _collect(sf, settings, ai, items, now=None):
    return run_collection(sf, settings, ai=ai, fetcher=make_fetcher(items), feeds=FEEDS, now=now)


def _item(guid, published_at, publisher="bbc", name="BBC News"):
    return FeedItem(publisher, name, f"https://example.com/{publisher}/{guid}", guid,
                    f"Headline for {guid}", f"Excerpt for {guid}.", published_at)


def _add_background(sf, story_id, url):
    with sf() as s:
        res = BackgroundResource(url=url, title="Topic", description="d", source="Wikipedia", created_at=utcnow())
        s.add(res)
        s.flush()
        s.add(StoryBackground(story_id=story_id, resource_id=res.id, entity="Topic"))
        s.commit()


def test_items_older_than_window_are_not_stored(session_factory, settings, fake_ai):
    now = utcnow()
    job = _collect(session_factory, settings, fake_ai, [_item("old", now - timedelta(hours=169)),
                                                       _item("fresh", now - timedelta(hours=167))], now=now)
    assert job.details["dates"]["too_old_skipped"] == 1
    assert story_of(session_factory, "old") is None
    assert story_of(session_factory, "fresh") is not None


def test_missing_and_future_dates(session_factory, settings, fake_ai):
    now = utcnow()
    items = [
        _item("nodate", None),
        _item("future", now + timedelta(hours=5)),
        _item("slightly-ahead", now + timedelta(minutes=30)),  # within the 1h clock-skew tolerance
    ]
    job = _collect(session_factory, settings, fake_ai, items, now=now)
    assert job.details["dates"]["date_missing"] == 1
    assert job.details["dates"]["date_future_clamped"] == 1
    with session_factory() as s:
        arts = {a.guid: a for a in s.scalars(select(Article))}
    assert arts["nodate"].date_estimated and arts["nodate"].published_at == now
    assert arts["future"].date_estimated and arts["future"].published_at == now
    assert not arts["slightly-ahead"].date_estimated
    assert arts["slightly-ahead"].published_at == now + timedelta(minutes=30)


def test_later_run_expires_old_articles_and_deletes_empty_stories(session_factory, settings, fake_ai):
    base = utcnow()
    _collect(session_factory, settings, fake_ai, fixture_items(now=base), now=base)
    g5 = story_of(session_factory, "g5-aj")  # articles 20-26h old at `base`
    g1 = story_of(session_factory, "g1-bbc")  # articles 0-3h old
    _add_background(session_factory, g5, "https://en.wikipedia.org/wiki/Nile")
    _add_background(session_factory, g1, "https://en.wikipedia.org/wiki/Hokkaido")

    later = base + timedelta(hours=150)  # cutoff = base - 18h: every G5 article is now too old
    job = _collect(session_factory, settings, fake_ai, [], now=later)

    assert job.status == "success"
    assert job.articles_expired >= 3
    assert job.stories_deleted >= 1
    with session_factory() as s:
        assert s.get(Story, g5) is None
        assert s.scalar(select(func.count()).select_from(Summary).where(Summary.story_id == g5)) == 0
        assert s.scalar(select(func.count()).select_from(StoryBackground).where(StoryBackground.story_id == g5)) == 0
        urls = set(s.scalars(select(BackgroundResource.url)))
        assert urls == {"https://en.wikipedia.org/wiki/Hokkaido"}  # orphan removed, shared-in-use kept
        assert s.get(Story, g1) is not None
        oldest = s.scalar(select(func.min(Article.published_at)))
        assert oldest >= cutoff_for(later, settings.retention_hours)


def test_summary_citing_an_expired_article_is_deleted(session_factory, settings, fake_ai):
    base = utcnow()
    _collect(session_factory, settings, fake_ai, fixture_items({"G5"}, now=base), now=base)
    sid = story_of(session_factory, "g5-aj")
    with session_factory() as s:
        assert s.get(Story, sid).summary is not None

    # cutoff = base - 22h: only the 26h-old NPR article expires, the story survives with two articles
    with session_factory() as s:
        result = expire_and_cleanup(s, base - timedelta(hours=22))
    assert result == {"articles_expired": 1, "stories_deleted": 0, "summaries_invalidated": 1}
    with session_factory() as s:
        story = s.get(Story, sid)
        assert story is not None and story.summary is None
        assert story.ai_status == "pending"
        assert len(story.articles) == 2


def test_api_hides_expired_articles_even_before_cleanup_runs(session_factory, settings, fake_ai):
    base = utcnow()
    _collect(session_factory, settings, fake_ai, fixture_items({"G1", "G5"}, now=base), now=base)
    g5 = story_of(session_factory, "g5-aj")
    g1 = story_of(session_factory, "g1-bbc")

    # Read as if 146h had passed with no collection: g5-npr (26h) is outside the window.
    cutoff = cutoff_for(base + timedelta(hours=146), settings.retention_hours)
    with session_factory() as s:
        detail = story_detail(s, g5, cutoff)
    assert {a["id"] for a in detail["articles"]}.isdisjoint({story_of(session_factory, "g5-npr")})
    assert len(detail["articles"]) == 2
    assert detail["ai"]["status"] == "outdated"  # its summary cited the expired article
    assert detail["ai"]["overview"] is None and detail["ai"]["overview_sources"] == []

    # Read as if 200h had passed: G5 is gone entirely, G1 too (3h + 200h > 168h).
    cutoff = cutoff_for(base + timedelta(hours=200), settings.retention_hours)
    with session_factory() as s:
        assert story_detail(s, g5, cutoff) is None
        stories, total = list_stories(s, cutoff, limit=50)
    assert total == 0 and stories == []

    cutoff = cutoff_for(base + timedelta(hours=160), settings.retention_hours)
    with session_factory() as s:
        stories, total = list_stories(s, cutoff, limit=50)
    assert [st["id"] for st in stories] == [g1]


def test_failed_run_never_deletes_data(session_factory, settings, fake_ai):
    base = utcnow()
    _collect(session_factory, settings, fake_ai, fixture_items(now=base), now=base)
    with session_factory() as s:
        before = s.scalar(select(func.count(Article.id)))
    job = run_collection(session_factory, settings, ai=fake_ai, fetcher=make_fetcher([], failing={f["id"] for f in FEEDS}),
                         feeds=FEEDS, now=base + timedelta(minutes=5))
    assert job.status == "failed"
    with session_factory() as s:
        assert s.scalar(select(func.count(Article.id))) == before
