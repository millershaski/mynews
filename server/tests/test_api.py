"""HTTP contract for the documented endpoints and the protected collection trigger."""
import httpx
import pytest
from fastapi.testclient import TestClient

from app.feeds import FEEDS
from app.main import create_app
from conftest import fixture_items, make_fetcher, make_settings, story_of

AUTH = {"Authorization": "Bearer test-admin-token"}


def _guardian_client():
    def handler(request):
        results = [
            {
                "type": "article",
                "id": f"world/2020/jan/{n}/a",
                "webUrl": f"https://www.theguardian.com/world/2020/jan/{n}/a",
                "webPublicationDate": "2020-01-10T08:00:00Z",
                "sectionName": "World news",
                "fields": {"headline": f"Japan story {n}", "trailText": "Tokyo news."},
            }
            for n in range(1, 7)
        ]
        return httpx.Response(200, json={"response": {"results": results}})

    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.fixture
def client(session_factory, settings, fake_ai):
    app = create_app(
        settings,
        session_factory,
        ai_client=fake_ai,
        explore_http=_guardian_client(),
        collect_kwargs={"fetcher": make_fetcher(fixture_items()), "feeds": FEEDS},
    )
    with TestClient(app) as c:
        yield c


def test_root_and_health(client):
    assert "/stories" in client.get("/").json()["endpoints"]
    body = client.get("/health").json()
    assert body["ok"] is True and body["time"].endswith("Z")


def test_admin_collect_requires_token(client):
    assert client.post("/admin/collect").status_code == 401
    assert client.post("/admin/collect", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/runs").json()["runs"] == []  # rejected calls never start a run


def test_admin_collect_disabled_without_configured_token(session_factory, fake_ai):
    app = create_app(make_settings(admin_token=""), session_factory, ai_client=fake_ai)
    with TestClient(app) as c:
        r = c.post("/admin/collect", headers={"Authorization": "Bearer "})
    assert r.status_code == 503 and "ADMIN_TOKEN" in r.json()["detail"]


def test_admin_collect_runs_and_is_recorded(client):
    r = client.post("/admin/collect?trigger=schedule", headers=AUTH)
    assert r.status_code == 200
    job = r.json()
    assert job["status"] == "success" and job["trigger"] == "schedule"
    assert job["feeds_ok"] == len(FEEDS) and job["articles_new"] > 0
    assert client.post("/admin/collect?trigger=bogus", headers=AUTH).status_code == 422

    runs = client.get("/runs").json()["runs"]
    assert [r["id"] for r in runs] == [job["id"]]
    status = client.get("/status").json()
    assert status["last_successful_collection_at"].endswith("Z")
    assert status["last_run"]["id"] == job["id"]
    assert {f["id"] for f in status["feeds"]} == {f["id"] for f in FEEDS}
    assert all(f["last_success_at"] for f in status["feeds"])
    assert status["stories"] > 0 and status["window_hours"] == 168


def test_stories_list_shape_and_order(client, session_factory):
    empty = client.get("/stories").json()
    assert empty["stories"] == [] and empty["total"] == 0 and empty["last_successful_collection_at"] is None

    client.post("/admin/collect", headers=AUTH)
    body = client.get("/stories").json()
    assert body["count"] == len(body["stories"]) == body["total"] > 0
    assert body["window_hours"] == 168 and body["window_start"].endswith("Z")
    times = [s["latest_published_at"] for s in body["stories"]]
    assert times == sorted(times, reverse=True)
    first = body["stories"][0]
    assert {"id", "headline", "latest_published_at", "publisher_count", "publishers"} <= set(first)
    assert first["id"] == story_of(session_factory, "g1-bbc")  # newest fixture article
    assert first["publisher_count"] == 3

    page = client.get("/stories?limit=2&offset=1").json()
    assert [s["id"] for s in page["stories"]] == [s["id"] for s in body["stories"][1:3]]
    assert client.get("/stories?limit=0").status_code == 422
    assert client.get("/stories?limit=201").status_code == 422


def test_story_detail(client, session_factory):
    client.post("/admin/collect", headers=AUTH)
    sid = story_of(session_factory, "g2-nyt")
    body = client.get(f"/stories/{sid}").json()
    assert body["id"] == sid and len(body["articles"]) >= 2
    assert all(a["url"].startswith("https://") and a["excerpt"] for a in body["articles"])
    assert body["ai"]["label"] == "AI-generated" and body["ai"]["status"] == "ok"
    assert body["background"]["message"] == "No background resource available"

    r = client.get("/stories/does-not-exist")
    assert r.status_code == 404 and "7-day window" in r.json()["detail"]


def test_explore_endpoints(client):
    cats = client.get("/explore/categories").json()
    assert [c["id"] for c in cats["categories"]] == ["all", "feel_good", "science", "technology"]
    assert cats["earliest"] == {"year": 1999, "month": 1}

    first = client.get("/explore?country=JP&year=2020&month=1").json()
    assert first["status"] == "ok" and first["cached"] is False and first["pick"]["url"].startswith("https://")
    again = client.get("/explore?country=JP&year=2020&month=1").json()
    assert again["cached"] is True and again["pick"] == first["pick"]

    assert client.get("/explore?country=ZZ&year=2020&month=1").status_code == 400
    assert client.get("/explore?country=JP&year=2020&month=1&category=sports").status_code == 400
    assert client.get("/explore?country=JP&year=2020&month=13").status_code == 422
    assert client.get("/explore?country=JPN&year=2020&month=1").status_code == 422
    assert client.get("/explore?year=2020&month=1").status_code == 422
