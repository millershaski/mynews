"""Wayback Machine archived feeds: capture selection, month filtering, caching and rate-limit handling."""
from datetime import datetime, timezone

import httpx

from app import wayback

TODAY = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)
BBC = "feeds.bbci.co.uk/news/world/rss.xml"


def _rss(*items) -> bytes:
    body = "".join(
        f"<item><title>{t}</title><link>{link}</link><pubDate>{p}</pubDate></item>" for t, link, p in items
    )
    return f'<?xml version="1.0"?><rss version="2.0"><channel><title>f</title>{body}</channel></rss>'.encode()


JAN = _rss(
    ("In January", "https://www.bbc.co.uk/news/1", "Thu, 02 Jan 2020 08:00:00 GMT"),
    ("Also January", "https://www.bbc.co.uk/news/2?at_medium=RSS", "Fri, 10 Jan 2020 08:00:00 GMT"),
    ("December", "https://www.bbc.co.uk/news/3", "Tue, 31 Dec 2019 22:00:00 GMT"),
)


class Archive:
    def __init__(self, captures=None, body=JAN, cdx_status=200, snapshot_status=None, cdx_text=None):
        self.captures = captures if captures is not None else {BBC: ["20200105120000", "20200120120000"]}
        self.body = body
        self.cdx_status = cdx_status
        self.snapshot_status = snapshot_status or {}  # timestamp -> status
        self.cdx_text = cdx_text
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path == "/cdx/search/cdx":
            if self.cdx_status != 200:
                return httpx.Response(self.cdx_status)
            if self.cdx_text is not None:
                return httpx.Response(200, text=self.cdx_text)
            url = request.url.params["url"]
            stamps = self.captures.get(url, [])
            if not stamps:
                return httpx.Response(200, text="[]")
            return httpx.Response(200, json=[["timestamp", "original"], *[[s, url] for s in stamps]])
        ts = request.url.path.split("/")[2].removesuffix("id_")
        status = self.snapshot_status.get(ts, 200)
        return httpx.Response(status, content=self.body if status == 200 else b"")

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self))


def _bbc(archive, year=2020, month=1, today=TODAY):
    with archive.client() as http:
        return wayback.fetch_month(http, ["bbc"], year, month, today)["bbc"]


def test_spread_keeps_first_and_last_and_spaces_the_rest():
    assert wayback.spread([1, 2, 3]) == [1, 2, 3]
    assert wayback.spread(list(range(31))) == [0, 10, 20, 30]


def test_reads_captures_and_keeps_only_items_from_the_month():
    a = Archive()
    r = _bbc(a)
    assert r.status == "ok" and r.feed_url == BBC and r.snapshots == 2
    assert sorted(i.title for i in r.items) == ["Also January", "In January"]  # deduped across captures
    assert all(i.publisher_name == "BBC News" for i in r.items)
    cdx = a.requests[0].url.params
    assert cdx["from"] == "202001" and cdx["to"] == "202001" and cdx["output"] == "json"


def test_falls_back_to_older_feed_urls():
    a = Archive(captures={"feeds.bbci.co.uk/news/rss.xml": ["20200105120000"]})
    r = _bbc(a)
    assert r.status == "ok" and r.feed_url == "feeds.bbci.co.uk/news/rss.xml"


def test_no_captures_is_not_covered():
    assert _bbc(Archive(captures={})).status == "not_covered"
    assert _bbc(Archive(cdx_text="")).status == "not_covered"


def test_future_captures_are_ignored():
    a = Archive(captures={BBC: ["20261025120000"]})
    assert _bbc(a, year=2026, month=10).status == "not_covered"


def test_malformed_cdx_is_unavailable():
    r = _bbc(Archive(cdx_text="<html>oops</html>"))
    assert r.status == "unavailable" and r.items == []


def test_complete_month_is_cached():
    a = Archive()
    _bbc(a)
    n = len(a.requests)
    again = _bbc(a)
    assert again.status == "ok" and len(a.requests) == n


def test_partially_read_month_is_usable_but_not_cached():
    a = Archive(snapshot_status={"20200120120000": 500})
    r = _bbc(a)
    assert r.status == "unavailable" and len(r.items) == 2
    assert "1 of 2" in r.detail
    n = len(a.requests)
    _bbc(a)
    assert len(a.requests) > n  # re-read next time


def test_all_snapshots_failing_is_unavailable():
    a = Archive(snapshot_status={"20200105120000": 503, "20200120120000": 503})
    r = _bbc(a)
    assert r.status == "unavailable" and r.items == []


def test_rate_limit_starts_a_cooldown_for_every_publisher():
    a = Archive(cdx_status=429)
    with a.client() as http:
        first = wayback.fetch_month(http, ["bbc"], 2020, 1, TODAY)
    assert first["bbc"].status == "unavailable" and "429" in first["bbc"].detail
    b = Archive()
    with b.client() as http:
        later = wayback.fetch_month(http, ["bbc", "npr"], 2020, 2, TODAY)
    assert {r.status for r in later.values()} == {"unavailable"}
    assert all("rate limit" in r.detail for r in later.values())
    assert b.requests == []  # nothing is sent during the cooldown


def test_one_publisher_failing_does_not_affect_another():
    class Mixed(Archive):
        def __call__(self, request):
            if request.url.path == "/cdx/search/cdx" and "npr" in request.url.params["url"]:
                self.requests.append(request)
                return httpx.Response(500)
            return super().__call__(request)

    with Mixed().client() as http:
        out = wayback.fetch_month(http, ["bbc", "npr"], 2020, 1, TODAY)
    assert out["bbc"].status == "ok" and out["npr"].status == "unavailable"
