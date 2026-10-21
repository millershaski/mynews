"""Grouping is checked against the hand-labelled fixture in fixtures/grouping_cases.json."""
from collections import defaultdict
from datetime import timedelta

from sqlalchemy import select

from app.collector import run_collection
from app.feeds import FEEDS, FeedItem
from app.models import Story
from app.timeutil import utcnow
from conftest import FIXTURE, fixture_items, make_fetcher, story_of


def _collect(session_factory, settings, ai, items):
    return run_collection(session_factory, settings, ai=ai, fetcher=make_fetcher(items), feeds=FEEDS)


def _story_by_label(session_factory):
    by_label = defaultdict(set)
    for a in FIXTURE["articles"]:
        by_label[a["group"]].add(story_of(session_factory, a["id"]))
    return by_label


def test_fixture_groups_match_labels(session_factory, settings, fake_ai):
    _collect(session_factory, settings, fake_ai, fixture_items())
    by_label = _story_by_label(session_factory)

    # every label maps to exactly one story, and no two labels share a story
    for label, ids in by_label.items():
        assert len(ids) == 1 and None not in ids, f"{label} split across {ids}"
    story_ids = [next(iter(ids)) for ids in by_label.values()]
    assert len(set(story_ids)) == len(story_ids), "two labelled groups were merged"


def test_five_multi_publisher_groups(session_factory, settings, fake_ai):
    _collect(session_factory, settings, fake_ai, fixture_items())
    by_label = _story_by_label(session_factory)
    with session_factory() as s:
        for label in ("G1", "G2", "G3", "G4", "G5"):
            story = s.get(Story, next(iter(by_label[label])))
            assert len({a.publisher_id for a in story.articles}) >= 3, label


def test_related_but_separate_stories_stay_apart(session_factory, settings, fake_ai):
    _collect(session_factory, settings, fake_ai, fixture_items())
    by_label = _story_by_label(session_factory)
    assert by_label["G1"] != by_label["R1a"]  # Japan quake vs Chile quake
    assert by_label["G2"] != by_label["R2a"]  # Fed vs ECB


def test_single_source_stories_are_kept(session_factory, settings, fake_ai):
    _collect(session_factory, settings, fake_ai, fixture_items())
    with session_factory() as s:
        for guid in ("s1-nyt", "s2-cbs"):
            story = s.get(Story, story_of(session_factory, guid))
            assert story is not None and len(story.articles) == 1


def test_one_shared_keyword_is_not_enough(session_factory, settings, fake_ai):
    now = utcnow()
    items = [
        FeedItem("bbc", "BBC News", "https://example.com/bbc/k1", "k1",
                 "Storm damages homes in Texas", "High winds tore roofs off homes across Texas overnight.", now),
        FeedItem("npr", "NPR", "https://example.com/npr/k2", "k2",
                 "Texas lawmakers pass school funding bill", "The legislature approved new school funding.", now),
    ]
    _collect(session_factory, settings, fake_ai, items)
    assert story_of(session_factory, "k1") != story_of(session_factory, "k2")


def test_story_ids_are_stable_and_new_coverage_joins(session_factory, settings, fake_ai):
    items = fixture_items()
    _collect(session_factory, settings, fake_ai, items)
    before = {a["id"]: story_of(session_factory, a["id"]) for a in FIXTURE["articles"]}

    late = FeedItem(
        "nyt", "The New York Times", "https://example.com/nyt/g1-nyt-late", "g1-nyt-late",
        "Hokkaido earthquake: Japan assesses damage after tsunami warning lifted",
        "Japan's government surveyed damage across Hokkaido after the magnitude 7.1 earthquake and tsunami warning.",
        utcnow() - timedelta(minutes=10),
    )
    job = _collect(session_factory, settings, fake_ai, items + [late])

    after = {a["id"]: story_of(session_factory, a["id"]) for a in FIXTURE["articles"]}
    assert before == after, "existing story ids changed between runs"
    assert story_of(session_factory, "g1-nyt-late") == before["g1-bbc"]
    assert job.stories_created == 0
    assert job.details["grouping"]["joined_existing"] >= 1


def test_headline_and_latest_time_follow_members(session_factory, settings, fake_ai):
    _collect(session_factory, settings, fake_ai, fixture_items({"G1"}))
    with session_factory() as s:
        story = s.scalars(select(Story)).one()
        assert story.headline in {a.title for a in story.articles}
        assert story.latest_article_at == max(a.published_at for a in story.articles)
