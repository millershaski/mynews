"""Background links come from Wikipedia, are checked before use, and failures are visible but never fatal."""
import httpx
from sqlalchemy import func, select

from app.collector import run_collection
from app.feeds import FEEDS
from app.models import BackgroundResource
from app.timeutil import utcnow
from app.views import NO_BACKGROUND, cutoff_for, story_detail
from conftest import fixture_items, make_fetcher, make_settings, story_of

PAGES = {
    "hokkaido": [
        {"index": 1, "title": "Hokkaido (disambiguation)", "pageprops": {"disambiguation": ""}},
        {"index": 2, "title": "Hokkaido", "fullurl": "https://en.wikipedia.org/wiki/Hokkaido", "description": "Japanese island"},
    ],
    "japan": [{"index": 1, "title": "Japan", "fullurl": "https://en.wikipedia.org/wiki/Japan", "description": "Country in East Asia"}],
}


class Wiki:
    def __init__(self, status=200):
        self.status = status
        self.agents: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.host != "en.wikipedia.org":
            raise AssertionError(f"unexpected host {request.url.host}")
        self.agents.append(request.headers["user-agent"])
        if self.status != 200:
            return httpx.Response(self.status)
        pages = PAGES.get(request.url.params["gsrsearch"].lower(), [])
        return httpx.Response(200, json={"query": {"pages": pages}} if pages else {})

    def client(self) -> httpx.Client:
        return httpx.Client(headers={"User-Agent": "Feed reader Mozilla/5.0"}, transport=httpx.MockTransport(self.handler))


def _collect(sf, wiki, ai):
    settings = make_settings(max_background_lookups_per_run=30)
    with wiki.client() as http:
        job = run_collection(sf, settings, http=http, ai=ai, fetcher=make_fetcher(fixture_items({"G1"})), feeds=FEEDS)
    return settings, job


def _background(sf, settings):
    with sf() as s:
        return story_detail(s, story_of(sf, "g1-bbc"), cutoff_for(utcnow(), settings.retention_hours))["background"]


def test_links_are_checked_and_use_a_wikimedia_compliant_agent(session_factory, fake_ai):
    wiki = Wiki()
    settings, job = _collect(session_factory, wiki, fake_ai)
    assert job.status == "success"
    assert wiki.agents and all("Mozilla" not in ua and ua == settings.wiki_user_agent for ua in wiki.agents)
    assert "https://example.test/my-news" in wiki.agents[0]

    bg = _background(session_factory, settings)
    urls = [link["url"] for link in bg["resources"]]
    assert "https://en.wikipedia.org/wiki/Hokkaido" in urls
    assert all("disambiguation" not in u for u in urls)
    assert bg["message"] is None


def test_wikipedia_outage_is_partial_and_shows_no_background(session_factory, fake_ai):
    settings, job = _collect(session_factory, Wiki(status=403), fake_ai)
    assert job.status == "partial"
    assert job.details["background"]["failures"] >= 1
    bg = _background(session_factory, settings)
    assert bg["resources"] == [] and bg["message"] == NO_BACKGROUND
    with session_factory() as s:
        assert s.scalar(select(func.count(BackgroundResource.id))) == 0


def test_lookups_skipped_without_a_contact(session_factory, fake_ai):
    wiki = Wiki()
    settings = make_settings(max_background_lookups_per_run=30, wiki_contact="")
    with wiki.client() as http:
        job = run_collection(session_factory, settings, http=http, ai=fake_ai, fetcher=make_fetcher(fixture_items({"G1"})), feeds=FEEDS)
    assert wiki.agents == []
    assert "WIKI_CONTACT" in job.details["background"]["skipped"]
    assert job.status == "success"
    assert _background(session_factory, settings)["message"] == NO_BACKGROUND
