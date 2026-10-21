"""Shared test fixtures. Tests never touch the network: feeds, Gemini, Guardian and Wayback are all faked."""
import json
import re
import sys
from datetime import timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import explore, wayback  # noqa: E402
from app.config import Settings, load_model_config  # noqa: E402
from app.db import init_db, make_engine, make_session_factory  # noqa: E402
from app.feeds import FEEDS, FeedItem  # noqa: E402
from app.gemini import AIError, reset_model_cooldowns  # noqa: E402
from app.timeutil import utcnow  # noqa: E402

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "grouping_cases.json").read_text(encoding="utf-8"))
FEED_NAMES = {f["id"]: f["name"] for f in FEEDS}


def make_settings(**overrides) -> Settings:
    values = dict(
        database_url="sqlite://",
        admin_token="test-admin-token",
        gemini_api_key="fake-key",
        gemini_model="fake-model",
        guardian_api_key="fake-guardian",
        retention_hours=168,
        max_ai_calls_per_run=50,
        ai_min_interval_seconds=0,
        ai_daily_limit=1000,
        explore_ai_reserve=10,
        max_background_lookups_per_run=0,
        user_agent="tests",
        wiki_contact="https://example.test/my-news",
        model_config=load_model_config(),
    )
    values.update(overrides)
    return Settings(**values)


@pytest.fixture
def settings():
    return make_settings()


@pytest.fixture
def session_factory(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'test.db'}")
    init_db(engine)
    yield make_session_factory(engine)
    engine.dispose()


@pytest.fixture(autouse=True)
def _reset_explore_failures():
    explore.reset_failures()
    reset_model_cooldowns()
    wayback.reset_state()
    yield
    explore.reset_failures()
    reset_model_cooldowns()
    wayback.reset_state()


class FakeAI:
    """Stands in for GeminiClient. Responses are built from the reference codes in the prompt."""

    def __init__(self, configured=True, fail=None, story_mode="supported", explore_mode="pick_first"):
        self.configured = configured
        self.model = "fake-model"
        self.last_model = "fake-model"
        self.calls: list[dict] = []
        self.fail = fail  # None, or an exception instance to raise on every call
        self.story_mode = story_mode
        self.explore_mode = explore_mode

    def generate_json(self, session, *, purpose, ref, system_prompt, user_text, schema, daily_cap):
        self.calls.append({"purpose": purpose, "ref": ref, "user_text": user_text, "system_prompt": system_prompt})
        if self.fail is not None:
            raise self.fail
        if purpose == "story_summary":
            return self._story(user_text)
        if purpose == "explore_pick":
            return self._explore(user_text)
        raise AssertionError(f"unexpected purpose {purpose}")

    def _story(self, user_text):
        refs = re.findall(r"^\[(A\d+)\]", user_text, flags=re.M)
        if self.story_mode == "garbage":
            return {"overview": "", "agreement": "x", "disagreement_or_uncertainty": "y", "overview_sources": []}
        if self.story_mode == "bad_refs":
            return {
                "overview": "o", "agreement": "a", "disagreement_or_uncertainty": "d",
                "overview_sources": ["A99"], "perspectives_status": "insufficient_evidence", "perspectives": [],
            }
        perspectives = []
        status = "insufficient_evidence"
        if self.story_mode == "supported" and len(refs) >= 2:
            status = "supported"
            perspectives = [
                {"label": "First view", "summary": "One framing.", "evidence_limits": "Excerpts only.", "sources": [refs[0]]},
                {"label": "Second view", "summary": "Another framing.", "evidence_limits": "Excerpts only.", "sources": [refs[1]]},
            ]
        return {
            "overview": "Overview of the coverage.",
            "agreement": "Sources agree on the basic event.",
            "disagreement_or_uncertainty": "Details remain unclear.",
            "overview_sources": refs,
            "perspectives_status": status,
            "perspectives": perspectives,
        }

    def _explore(self, user_text):
        codes = re.findall(r"^\[(C\d+)\]", user_text, flags=re.M)
        if self.explore_mode == "none":
            return {"status": "none_suitable", "choice": "", "reason": "Nothing fits the filter.", "intro": ""}
        if self.explore_mode == "invalid_choice":
            return {"status": "picked", "choice": "C999", "reason": "r", "intro": "i"}
        choice = codes[-1] if self.explore_mode == "pick_last" else codes[0]
        return {"status": "picked", "choice": choice, "reason": "A clear, notable story.", "intro": "Short intro."}


@pytest.fixture
def fake_ai():
    return FakeAI()


def fixture_items(groups=None, *, now=None, url_suffix="?utm_source=rss") -> list[FeedItem]:
    """FeedItems for the labelled grouping fixture, timed relative to the real clock."""
    now = now or utcnow()
    out = []
    for a in FIXTURE["articles"]:
        if groups is not None and a["group"] not in groups:
            continue
        out.append(
            FeedItem(
                publisher_id=a["publisher"],
                publisher_name=FEED_NAMES[a["publisher"]],
                url=f"https://example.com/{a['publisher']}/{a['id']}{url_suffix}",
                guid=a["id"],
                title=a["title"],
                excerpt=a["excerpt"],
                published_at=now - timedelta(hours=a["hours"]),
            )
        )
    return out


def make_fetcher(items: list[FeedItem], failing: set[str] = frozenset()):
    """A fetcher with the same signature as feeds.fetch_feed that serves the given items."""

    def fetcher(_http, feed):
        if feed["id"] in failing:
            raise RuntimeError(f"simulated outage for {feed['id']}")
        return [it for it in items if it.publisher_id == feed["id"]]

    return fetcher


def story_of(session_factory, guid: str) -> str | None:
    from sqlalchemy import select

    from app.models import Article

    with session_factory() as s:
        return s.scalar(select(Article.story_id).where(Article.guid == guid))


def ai_error(message="provider down", quota=False, status_code=503):
    return AIError(message, status_code=status_code, quota=quota)
