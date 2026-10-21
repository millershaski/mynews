"""Explore globe: one AI-chosen archive story per (country, year, month, category), shared by every reader."""
import re
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import func, inspect, select, text

from app import explore
from app.collector import run_collection
from app.db import init_db, make_engine
from app.explore import ExploreInputError, get_explore
from app.feeds import FEEDS
from app.models import ExplorePick
from conftest import FakeAI, ai_error, fixture_items, make_fetcher, make_settings, story_of

NOW = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)


def _result(n, headline, trail="", section="World news"):
    return {
        "type": "article",
        "id": f"world/2020/jan/{n:02d}/story-{n}",
        "webUrl": f"https://www.theguardian.com/world/2020/jan/{n:02d}/story-{n}",
        "webPublicationDate": f"2020-01-{n:02d}T09:00:00Z",
        "sectionName": section,
        "fields": {"headline": headline, "trailText": trail, "thumbnail": f"https://media.example/{n}.jpg"},
    }


DEFAULT_RESULTS = [
    _result(1, "Tokyo prepares for the Olympic year", "Japan's capital readies venues."),
    _result(2, "Volunteers restore a Japanese forest after typhoon", "Community effort replants trees."),
    _result(3, "Global markets slide", "Stocks fell worldwide."),
    _result(4, "Japan's robots help an ageing workforce", "Engineers in Osaka show new machines."),
    _result(5, "Scientists in Japan map a deep-sea trench", "Researchers publish a new survey."),
    _result(6, "Japanese tea growers adapt to warmer seasons", "Farmers change harvest dates."),
]


def _rss(*items) -> bytes:
    """items: (title, link, summary, RFC 822 pubDate)."""
    body = "".join(
        f"<item><title>{t}</title><link>{link}</link><description>{d}</description><pubDate>{p}</pubDate></item>"
        for t, link, d, p in items
    )
    return f'<?xml version="1.0"?><rss version="2.0"><channel><title>feed</title>{body}</channel></rss>'.encode()


BBC_FEED = "feeds.bbci.co.uk/news/world/rss.xml"
BBC_JAN_2020 = _rss(
    ("Japan's emperor greets crowds at new year", "https://www.bbc.co.uk/news/world-asia-1", "Thousands gather at the palace in Tokyo.", "Thu, 02 Jan 2020 08:00:00 GMT"),
    ("Australia bushfires spread", "https://www.bbc.co.uk/news/world-australia-2", "Firefighters battle blazes.", "Fri, 03 Jan 2020 08:00:00 GMT"),
    ("Japan court rules on whaling", "https://www.bbc.co.uk/news/world-asia-3?at_medium=RSS", "A ruling in Osaka.", "Tue, 31 Dec 2019 22:00:00 GMT"),
    ("Japan's bullet trains run on time", "https://www.bbc.co.uk/news/world-asia-4", "Punctuality data published.", "Sat, 11 Jan 2020 08:00:00 GMT"),
)


class Archive:
    """A mock Wayback Machine. `feeds` maps an archived feed URL to the RSS body of its captures."""

    def __init__(self, feeds=None, status=200):
        self.feeds = feeds or {}
        self.status = status

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if self.status != 200:
            return httpx.Response(self.status)
        if request.url.path == "/cdx/search/cdx":
            url = request.url.params["url"]
            if url not in self.feeds:
                return httpx.Response(200, text="[]")
            stamp = request.url.params["from"]
            return httpx.Response(200, json=[["timestamp", "original"], [f"{stamp}15120000", url]])
        for url, body in self.feeds.items():
            if request.url.path.endswith(url):
                return httpx.Response(200, content=body)
        return httpx.Response(404)


class Guardian:
    """A mock Guardian search endpoint that records every request. Wayback requests go to `archive`."""

    def __init__(self, results=None, status=200, archive=None):
        self.results = DEFAULT_RESULTS if results is None else results
        self.status = status
        self.archive = archive
        self.requests: list[dict] = []
        self.archive_requests: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "web.archive.org":
            self.archive_requests.append(str(request.url))
            return self.archive(request) if self.archive else httpx.Response(200, text="[]")
        self.requests.append(dict(request.url.params))
        if self.status != 200:
            return httpx.Response(self.status, json={"message": "nope"})
        return httpx.Response(200, json={"response": {"status": "ok", "results": self.results}})

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handler))


def _explore(sf, settings, guardian, ai, country="JP", year=2020, month=1, category="all", now=NOW):
    with guardian.client() as http:
        return get_explore(sf, settings, country, year, month, category, http=http, ai=ai, now=now)


def _picks(ai):
    return [c for c in ai.calls if c["purpose"] == "explore_pick"]


def test_first_request_uses_ai_then_everyone_gets_the_cached_pick(session_factory, settings, fake_ai):
    g = Guardian()
    first = _explore(session_factory, settings, g, fake_ai)
    assert first["status"] == "ok" and first["cached"] is False
    assert first["pick"]["headline"] == "Tokyo prepares for the Olympic year"
    assert first["pick"]["publisher"] == "The Guardian"
    assert first["ai"]["label"] == "AI-generated" and first["ai"]["reason"] and first["ai"]["disclosure"]
    assert first["expires_at"] is None and first["is_current_month"] is False
    assert first["attribution"] and first["archive_note"]

    second = _explore(session_factory, settings, g, fake_ai)
    third = _explore(session_factory, settings, g, fake_ai, country="jp")  # case-insensitive key
    assert second["cached"] is True and third["cached"] is True
    assert second["pick"] == first["pick"] == third["pick"]
    assert second["ai"] == first["ai"]
    assert len(_picks(fake_ai)) == 1
    assert len(g.requests) == 1  # tag search returned enough results; cache served the rest
    assert g.requests[0]["tag"] == "world/japan"
    assert g.requests[0]["from-date"] == "2020-01-01" and g.requests[0]["to-date"] == "2020-01-31"
    assert "api-key" in g.requests[0]


def test_each_combination_is_cached_separately(session_factory, settings, fake_ai):
    g = Guardian()
    _explore(session_factory, settings, g, fake_ai)
    _explore(session_factory, settings, g, fake_ai, month=2)
    _explore(session_factory, settings, g, fake_ai, category="technology")
    _explore(session_factory, settings, g, fake_ai, country="KE")
    assert len(_picks(fake_ai)) == 4
    with session_factory() as s:
        assert s.scalar(select(func.count(ExplorePick.id))) == 4


def test_category_with_a_guardian_section_restricts_the_search(session_factory, settings, fake_ai):
    g = Guardian()
    _explore(session_factory, settings, g, fake_ai, category="science")
    assert g.requests[0]["section"] == "science"
    assert '"Japan"' in g.requests[0]["q"]


def test_feel_good_rule_is_sent_to_the_ai(session_factory, settings, fake_ai):
    _explore(session_factory, settings, Guardian(), fake_ai, category="feel_good")
    text = _picks(fake_ai)[0]["user_text"]
    assert "Category: Feel good" in text and "uplifting" in text
    assert text.count("<<<CANDIDATES_BEGIN>>>") == 1 and text.count("<<<CANDIDATES_END>>>") == 1


def test_candidates_naming_the_country_come_first(session_factory, settings, fake_ai):
    _explore(session_factory, settings, Guardian(), fake_ai)
    text = _picks(fake_ai)[0]["user_text"]
    assert text.index("Global markets slide") > text.index("Japanese tea growers")


def test_no_candidates_is_stored(session_factory, settings, fake_ai):
    g = Guardian(results=[])
    out = _explore(session_factory, settings, g, fake_ai)
    assert out["status"] == "no_candidates" and out["pick"] is None and out["message"]
    assert fake_ai.calls == []
    again = _explore(session_factory, settings, g, fake_ai)
    assert again["cached"] is True and again["status"] == "no_candidates"


def test_none_suitable_is_stored_with_the_ai_reason(session_factory, settings):
    ai = FakeAI(explore_mode="none")
    out = _explore(session_factory, settings, Guardian(), ai, category="feel_good")
    assert out["status"] == "no_match" and out["pick"] is None
    assert "Feel good" in out["message"]
    assert out["ai"]["reason"] == "Nothing fits the filter."
    assert _explore(session_factory, settings, Guardian(), ai, category="feel_good")["cached"] is True
    assert len(_picks(ai)) == 1


@pytest.mark.parametrize("category", ["all", "science"])
def test_ai_failure_falls_back_to_labelled_top_result_and_is_not_stored(session_factory, settings, category):
    ai = FakeAI(fail=ai_error("provider down"))
    out = _explore(session_factory, settings, Guardian(), ai, category=category)
    assert out["status"] == "relevance_fallback"
    assert out["pick"]["headline"] == "Tokyo prepares for the Olympic year"
    assert out["ai"] is None  # nothing is presented as AI output
    assert "not been checked by AI" in out["message"]
    with session_factory() as s:
        assert s.scalar(select(func.count(ExplorePick.id))) == 0


def test_invalid_ai_choice_is_not_served_as_a_pick(session_factory, settings):
    ai = FakeAI(explore_mode="invalid_choice")
    out = _explore(session_factory, settings, Guardian(), ai, category="feel_good")
    assert out["status"] == "ai_unavailable" and out["pick"] is None and out["ai"] is None


def test_feel_good_needs_ai_so_failure_is_reported_honestly(session_factory, settings):
    ai = FakeAI(configured=False)
    out = _explore(session_factory, settings, Guardian(), ai, category="feel_good")
    assert out["status"] == "ai_unavailable" and out["pick"] is None
    assert "Feel good" in out["message"]
    assert ai.calls == []


def test_failures_are_not_stored_and_cool_down_before_retry(session_factory, settings, fake_ai):
    down = Guardian(status=401)
    out = _explore(session_factory, settings, down, fake_ai)
    assert out["status"] == "source_unavailable" and out["pick"] is None
    assert len(down.requests) == 1

    up = Guardian()
    soon = _explore(session_factory, settings, up, fake_ai, now=NOW + timedelta(minutes=2))
    assert soon["status"] == "source_unavailable" and up.requests == []  # cooldown: provider not hit

    later = _explore(session_factory, settings, up, fake_ai, now=NOW + timedelta(minutes=11))
    assert later["status"] == "ok" and later["cached"] is False


def test_missing_guardian_key_still_searches_the_other_publishers(session_factory, fake_ai):
    settings = make_settings(guardian_api_key="")
    g = Guardian(archive=Archive({BBC_FEED: BBC_JAN_2020}))
    out = _explore(session_factory, settings, g, fake_ai)
    assert g.requests == []  # the Guardian API is never called without a key
    assert out["status"] == "ok" and out["pick"]["publisher"] == "BBC News"
    guardian_src = next(s for s in out["sources"] if s["publisher"] == "The Guardian")
    assert guardian_src["status"] == "not_configured"
    assert out["expires_at"] == "2026-10-22T12:00:00Z"  # retried once the key may have been added


def test_missing_guardian_key_and_no_archive_matches_is_no_candidates(session_factory, fake_ai):
    settings = make_settings(guardian_api_key="")
    out = _explore(session_factory, settings, Guardian(), fake_ai)
    assert out["status"] == "no_candidates" and out["pick"] is None
    assert out["expires_at"] == "2026-10-22T12:00:00Z"
    assert fake_ai.calls == []


def test_archived_publisher_feeds_supply_candidates(session_factory, settings, fake_ai):
    g = Guardian(results=[], archive=Archive({BBC_FEED: BBC_JAN_2020}))
    out = _explore(session_factory, settings, g, fake_ai)
    assert out["status"] == "ok" and out["cached"] is False
    assert out["pick"]["publisher"] == "BBC News"
    assert out["pick"]["headline"] == "Japan's emperor greets crowds at new year"
    assert out["pick"]["thumbnail"] is None
    text = _picks(fake_ai)[0]["user_text"]
    assert "publisher: BBC News" in text
    assert "Australia bushfires" not in text  # does not mention Japan
    assert "whaling" not in text  # published in December 2019, outside the month
    assert out["candidates_considered"] == 2

    by_name = {s["publisher"]: s for s in out["sources"]}
    assert set(by_name) == {f["name"] for f in FEEDS}
    assert by_name["BBC News"]["status"] == "ok" and by_name["BBC News"]["candidates"] == 2
    assert by_name["The Guardian"]["status"] == "ok" and by_name["The Guardian"]["candidates"] == 0
    assert by_name["NPR"]["status"] == "not_covered"
    assert out["expires_at"] is None  # every source answered: a past month is permanent

    calls = len(g.archive_requests)
    again = _explore(session_factory, settings, g, fake_ai)
    assert again["cached"] is True and again["pick"] == out["pick"] and again["sources"] == out["sources"]
    assert len(g.archive_requests) == calls and len(_picks(fake_ai)) == 1


def test_publishers_take_turns_in_the_candidate_list(session_factory, settings, fake_ai):
    _explore(session_factory, settings, Guardian(archive=Archive({BBC_FEED: BBC_JAN_2020})), fake_ai)
    text = _picks(fake_ai)[0]["user_text"]
    publishers = re.findall(r"^\[C\d+\] publisher: ([^|]+?) \|", text, flags=re.M)
    assert publishers[:5] == ["The Guardian", "BBC News", "The Guardian", "BBC News", "The Guardian"]


def test_same_article_from_two_sources_is_offered_once(session_factory, settings, fake_ai):
    dup = _rss(("Tokyo prepares for the Olympic year", "https://www.theguardian.com/world/2020/jan/01/story-1?CMP=rss",
                "Japan's capital readies venues.", "Wed, 01 Jan 2020 09:00:00 GMT"))
    _explore(session_factory, settings, Guardian(archive=Archive({BBC_FEED: dup})), fake_ai)
    assert _picks(fake_ai)[0]["user_text"].count("Tokyo prepares for the Olympic year") == 1


def test_archive_outage_does_not_hide_guardian_results(session_factory, settings, fake_ai):
    g = Guardian(archive=Archive(status=500))
    out = _explore(session_factory, settings, g, fake_ai)
    assert out["status"] == "ok" and out["pick"]["publisher"] == "The Guardian"
    wayback_sources = [s for s in out["sources"] if s["publisher"] != "The Guardian"]
    assert len(wayback_sources) == 7 and all(s["status"] == "unavailable" for s in wayback_sources)
    assert out["expires_at"] == "2026-10-22T12:00:00Z"  # incomplete: try again tomorrow
    assert _explore(session_factory, settings, g, fake_ai, now=NOW + timedelta(hours=23))["cached"] is True
    later = _explore(session_factory, settings, Guardian(archive=Archive({BBC_FEED: BBC_JAN_2020})), fake_ai,
                     now=NOW + timedelta(hours=25))
    assert later["cached"] is False and later["expires_at"] is None


def test_every_source_down_is_source_unavailable(session_factory, settings, fake_ai):
    out = _explore(session_factory, settings, Guardian(status=500, archive=Archive(status=500)), fake_ai)
    assert out["status"] == "source_unavailable" and out["pick"] is None
    assert fake_ai.calls == []
    with session_factory() as s:
        assert s.scalar(select(func.count(ExplorePick.id))) == 0


def test_ai_failure_fallback_can_be_an_archived_publisher(session_factory, settings):
    ai = FakeAI(fail=ai_error("provider down"))
    g = Guardian(results=[], archive=Archive({BBC_FEED: BBC_JAN_2020}))
    out = _explore(session_factory, settings, g, ai)
    assert out["status"] == "relevance_fallback" and out["ai"] is None
    assert out["pick"]["publisher"] == "BBC News"


def test_explore_uses_the_eight_publisher_prompt(session_factory, settings, fake_ai):
    out = _explore(session_factory, settings, Guardian(), fake_ai)
    assert "BBC News" in _picks(fake_ai)[0]["system_prompt"]
    assert out["ai"]["prompt_version"] == "explore_pick_v2"


def test_current_month_offers_collected_articles_from_all_publishers(session_factory, settings, fake_ai):
    run_collection(session_factory, settings, ai=fake_ai, fetcher=make_fetcher(fixture_items()), feeds=FEEDS)
    now = datetime.now(timezone.utc)
    out = _explore(session_factory, settings, Guardian(results=[]), fake_ai, country="KE",
                   year=now.year, month=now.month, now=now)
    assert out["status"] == "ok"
    assert out["pick"]["url"].startswith("https://example.com/")
    assert out["pick"]["publisher"] in {f["name"] for f in FEEDS}
    local = next(s for s in out["sources"] if s["publisher"] == "All publishers")
    assert local["status"] == "ok" and local["candidates"] >= 1


def test_older_databases_gain_the_new_explore_columns(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE explore_picks (id INTEGER PRIMARY KEY, country VARCHAR(2) NOT NULL, "
            "year INTEGER NOT NULL, month INTEGER NOT NULL, category VARCHAR(32) NOT NULL)"
        ))
    init_db(engine)
    columns = {c["name"] for c in inspect(engine).get_columns("explore_picks")}
    assert {"sources", "expires_at", "publisher"} <= columns
    engine.dispose()


@pytest.mark.parametrize(
    "args, message",
    [
        (("XX", 2020, 1, "all"), "unknown country"),
        (("JP", 2020, 1, "sports"), "unknown category"),
        (("JP", 2020, 13, "all"), "month must be"),
        (("JP", 1998, 12, "all"), "January 1999"),
        (("JP", 2026, 11, "all"), "future"),
    ],
)
def test_invalid_input(session_factory, settings, fake_ai, args, message):
    with pytest.raises(ExploreInputError, match=message):
        get_explore(session_factory, settings, *args, http=Guardian().client(), ai=fake_ai, now=NOW)


def test_current_month_refreshes_after_24_hours(session_factory, settings):
    ai = FakeAI()
    g = Guardian()
    first = _explore(session_factory, settings, g, ai, year=2026, month=10, now=NOW)
    assert first["is_current_month"] is True
    assert first["expires_at"] == "2026-10-22T12:00:00Z"
    assert explore.CURRENT_MONTH_NOTE in first["archive_note"]
    assert g.requests[0]["to-date"] == "2026-10-21"

    assert _explore(session_factory, settings, g, ai, year=2026, month=10, now=NOW + timedelta(hours=23))["cached"]
    ai.explore_mode = "pick_last"
    refreshed = _explore(session_factory, settings, g, ai, year=2026, month=10, now=NOW + timedelta(hours=25))
    assert refreshed["cached"] is False and refreshed["status"] == "ok"
    assert len(_picks(ai)) == 2
    with session_factory() as s:
        assert s.scalar(select(func.count(ExplorePick.id))) == 1  # same row, replaced


def test_current_month_includes_live_grouped_stories_for_the_country(session_factory, settings, fake_ai):
    run_collection(session_factory, settings, ai=fake_ai, fetcher=make_fetcher(fixture_items()), feeds=FEEDS)
    now = datetime.now(timezone.utc)
    out = _explore(session_factory, settings, Guardian(), fake_ai, country="KE", year=now.year, month=now.month, now=now)
    ids = [s["id"] for s in out["live_stories"]]
    assert ids == [story_of(session_factory, "g5-aj")]
    assert out["live_stories"][0]["publisher_count"] == 3

    past = _explore(session_factory, settings, Guardian(), fake_ai, country="KE", year=2020, month=1)
    assert past["live_stories"] == []


def test_explore_may_use_the_full_daily_ai_limit(session_factory, settings):
    seen = {}

    class CapturingAI(FakeAI):
        def generate_json(self, session, **kw):
            seen["daily_cap"] = kw["daily_cap"]
            return super().generate_json(session, **kw)

    _explore(session_factory, settings, Guardian(), CapturingAI())
    assert seen["daily_cap"] == settings.ai_daily_limit
