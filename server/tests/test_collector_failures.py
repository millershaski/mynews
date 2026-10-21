"""A failed feed or AI call must never crash the run or erase data, and every run is recorded."""
from datetime import timedelta

from sqlalchemy import func, select

from app.collector import LAST_SUCCESS_KEY, get_state, run_collection
from app.feeds import FEEDS
from app.models import Article, FeedStatus, JobRun, Story
from app.timeutil import utcnow
from conftest import FakeAI, ai_error, fixture_items, make_fetcher

ALL_FEEDS = {f["id"] for f in FEEDS}


def _collect(sf, settings, ai, items, failing=frozenset(), now=None):
    return run_collection(sf, settings, ai=ai, fetcher=make_fetcher(items, failing), feeds=FEEDS, now=now)


def test_one_failing_feed_is_recorded_and_others_still_stored(session_factory, settings, fake_ai):
    job = _collect(session_factory, settings, fake_ai, fixture_items(), failing={"bbc"})
    assert job.status == "partial"
    assert job.feeds_failed == 1 and job.feeds_ok == len(FEEDS) - 1
    assert job.details["feeds"]["bbc"]["ok"] is False
    assert "simulated outage" in job.details["feeds"]["bbc"]["error"]
    with session_factory() as s:
        status = s.get(FeedStatus, "bbc")
        assert status.last_error and "simulated outage" in status.last_error
        assert status.last_success_at is None
        assert s.scalar(select(func.count(Article.id)).where(Article.publisher_id == "bbc")) == 0
        assert s.scalar(select(func.count(Article.id))) > 0
        assert get_state(s, LAST_SUCCESS_KEY) is not None


def test_feed_recovers_and_clears_its_error(session_factory, settings, fake_ai):
    _collect(session_factory, settings, fake_ai, fixture_items(), failing={"bbc"})
    _collect(session_factory, settings, fake_ai, fixture_items())
    with session_factory() as s:
        status = s.get(FeedStatus, "bbc")
        assert status.last_error is None and status.last_success_at is not None


def test_all_feeds_failing_keeps_data_and_last_success_time(session_factory, settings, fake_ai):
    first = _collect(session_factory, settings, fake_ai, fixture_items())
    assert first.status == "success"
    with session_factory() as s:
        last_success = get_state(s, LAST_SUCCESS_KEY)
        articles = s.scalar(select(func.count(Article.id)))
        stories = s.scalar(select(func.count(Story.id)))

    job = _collect(session_factory, settings, fake_ai, [], failing=ALL_FEEDS)
    assert job.status == "failed"
    assert job.error == "every feed failed; existing data kept"
    with session_factory() as s:
        assert get_state(s, LAST_SUCCESS_KEY) == last_success
        assert s.scalar(select(func.count(Article.id))) == articles
        assert s.scalar(select(func.count(Story.id))) == stories


def test_ai_error_marks_run_partial_and_keeps_articles(session_factory, settings):
    ai = FakeAI(fail=ai_error("provider down"))
    job = _collect(session_factory, settings, ai, fixture_items({"G1", "G2"}))
    assert job.status == "partial"
    assert job.ai_failures == 2 and job.summaries_generated == 0
    with session_factory() as s:
        assert s.scalar(select(func.count(Article.id))) == 6
        stories = s.scalars(select(Story)).all()
        assert all(st.ai_status == "failed" and "provider down" in st.ai_error for st in stories)
        assert get_state(s, LAST_SUCCESS_KEY) is not None  # collection itself succeeded


def test_quota_error_stops_ai_calls_for_the_run(session_factory, settings):
    ai = FakeAI(fail=ai_error("daily quota reached", quota=True, status_code=429))
    job = _collect(session_factory, settings, ai, fixture_items({"G1", "G2", "G3"}))
    assert len(ai.calls) == 1
    assert job.details["ai"]["stopped_reason"] == "daily quota reached"
    assert job.status == "partial"


def test_provider_outage_stops_ai_calls_after_a_few_failures(session_factory, settings):
    ai = FakeAI(fail=ai_error("provider down", status_code=503))
    job = _collect(session_factory, settings, ai, fixture_items({"G1", "G2", "G3", "G4", "G5"}))
    assert len(ai.calls) == 3  # not one failed call per queued story
    assert "AI provider unavailable" in job.details["ai"]["stopped_reason"]
    assert job.status == "partial"


def test_concurrent_run_is_skipped(session_factory, settings, fake_ai):
    now = utcnow()
    with session_factory() as s:
        s.add(JobRun(trigger="schedule", started_at=now - timedelta(minutes=5), status="running", details={}))
        s.commit()
    job = _collect(session_factory, settings, fake_ai, fixture_items(), now=now)
    assert job.status == "skipped"
    assert "already in progress" in job.error
    assert fake_ai.calls == []
    with session_factory() as s:
        assert s.scalar(select(func.count(Article.id))) == 0


def test_stale_running_job_is_marked_abandoned(session_factory, settings, fake_ai):
    now = utcnow()
    with session_factory() as s:
        stale = JobRun(trigger="schedule", started_at=now - timedelta(hours=2), status="running", details={})
        s.add(stale)
        s.commit()
        stale_id = stale.id
    job = _collect(session_factory, settings, fake_ai, fixture_items(), now=now)
    assert job.status == "success"
    with session_factory() as s:
        old = s.get(JobRun, stale_id)
        assert old.status == "failed" and "abandoned" in old.error and old.finished_at is not None


def test_every_run_is_recorded(session_factory, settings, fake_ai):
    _collect(session_factory, settings, fake_ai, fixture_items())
    _collect(session_factory, settings, fake_ai, [], failing=ALL_FEEDS)
    _collect(session_factory, settings, fake_ai, fixture_items(), failing={"npr"})
    with session_factory() as s:
        runs = s.scalars(select(JobRun).order_by(JobRun.id)).all()
    assert [r.status for r in runs] == ["success", "failed", "partial"]
    assert all(r.started_at and r.finished_at and r.finished_at >= r.started_at for r in runs)


def test_unexpected_step_error_is_recorded_not_raised(session_factory, settings, fake_ai, monkeypatch):
    from app import collector

    def boom(*_a, **_k):
        raise ValueError("grouping exploded")

    _collect(session_factory, settings, fake_ai, fixture_items({"G1"}))
    monkeypatch.setattr(collector, "group_and_refresh", boom)
    job = _collect(session_factory, settings, fake_ai, fixture_items({"G1", "G2"}))
    assert job.status == "failed"
    assert job.error.startswith("failed during group: ValueError: grouping exploded")
    with session_factory() as s:
        assert s.scalar(select(func.count(Story.id))) == 1  # earlier stories untouched
