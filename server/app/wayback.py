"""Archived publisher RSS feeds from the Internet Archive's Wayback Machine, used by the explore globe.

The Guardian has a searchable archive API; the other seven publishers do not offer a free one. Their RSS
feeds, however, have been captured by the Wayback Machine for many years. For a requested month we look
up the captures of each publisher's feed (CDX API, no key needed), fetch a few captures spread across the
month and keep the items published in that month. Only the headline and the publisher's own RSS summary
are used, exactly as in the live collector.

One publisher failing never affects the others. A month that was read completely is kept in memory, so
the other countries and categories of that month do not contact the Wayback Machine again (the current
month is re-read after a few hours). After a 429 the module stops contacting the Wayback Machine for a while.
"""
import threading
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import httpx

from .feeds import FEEDS, FeedItem, normalize_url, parse_feed

CDX_URL = "https://web.archive.org/cdx/search/cdx"
SNAPSHOT_URL = "https://web.archive.org/web/{ts}id_/{url}"
HOST = "web.archive.org"
SNAPSHOTS_PER_MONTH = 4
MAX_WORKERS = 4
RATE_LIMIT_COOLDOWN = timedelta(minutes=10)
CURRENT_MONTH_CACHE_TTL = timedelta(hours=6)
MAX_CACHED_MONTHS = 400  # publisher-months; a month of one publisher is at most a few hundred items

# Feed URLs as they were captured over the years, tried in order until one has captures for the month.
ARCHIVED_FEEDS: dict[str, list[str]] = {
    "bbc": ["feeds.bbci.co.uk/news/world/rss.xml", "feeds.bbci.co.uk/news/rss.xml"],
    "npr": ["feeds.npr.org/1004/rss.xml", "www.npr.org/rss/rss.php?id=1004", "feeds.npr.org/1001/rss.xml"],
    "fox": ["feeds.foxnews.com/foxnews/world", "moxie.foxnews.com/google-publisher/world.xml"],
    "cbs": ["www.cbsnews.com/latest/rss/world"],
    "nyt": ["rss.nytimes.com/services/xml/rss/nyt/World.xml", "www.nytimes.com/services/xml/rss/nyt/World.xml"],
    "aljazeera": ["www.aljazeera.com/xml/rss/all.xml"],
    "abc": ["abcnews.go.com/abcnews/internationalheadlines", "feeds.abcnews.com/abcnews/internationalheadlines"],
}
PUBLISHER_NAMES = {f["id"]: f["name"] for f in FEEDS}

_state_lock = threading.Lock()
_cooldown_until: datetime | None = None
_cache: "OrderedDict[tuple, tuple[datetime, MonthResult]]" = OrderedDict()


class WaybackError(Exception):
    pass


class WaybackRateLimited(WaybackError):
    pass


@dataclass
class MonthResult:
    publisher_id: str
    status: str  # ok | not_covered | unavailable
    items: list[FeedItem] = field(default_factory=list)
    feed_url: str | None = None
    snapshots: int = 0
    detail: str | None = None


def reset_state() -> None:
    global _cooldown_until
    with _state_lock:
        _cooldown_until = None
        _cache.clear()


def _cooling_down(now: datetime) -> bool:
    with _state_lock:
        return _cooldown_until is not None and now < _cooldown_until


def _start_cooldown(now: datetime) -> None:
    global _cooldown_until
    with _state_lock:
        _cooldown_until = now + RATE_LIMIT_COOLDOWN


def _cached(key: tuple, now: datetime, current_month: bool) -> MonthResult | None:
    with _state_lock:
        hit = _cache.get(key)
        if hit is None:
            return None
        if current_month and now - hit[0] >= CURRENT_MONTH_CACHE_TTL:
            _cache.pop(key, None)
            return None
        _cache.move_to_end(key)
        return hit[1]


def _remember(key: tuple, now: datetime, result: MonthResult) -> None:
    with _state_lock:
        _cache[key] = (now, result)
        _cache.move_to_end(key)
        while len(_cache) > MAX_CACHED_MONTHS:
            _cache.popitem(last=False)


def _get(client: httpx.Client, url: str, params: dict | None = None) -> httpx.Response:
    try:
        resp = client.get(url, params=params, timeout=20.0, follow_redirects=True)
    except httpx.HTTPError as exc:
        raise WaybackError(f"network error: {exc.__class__.__name__}") from exc
    if resp.status_code == 429:
        raise WaybackRateLimited("Wayback Machine rate limit reached (429)")
    if resp.status_code >= 400:
        raise WaybackError(f"Wayback Machine error {resp.status_code}")
    return resp


def list_captures(client: httpx.Client, feed_url: str, year: int, month: int, today: datetime) -> list[tuple[str, str]]:
    """(timestamp, original url) for at most one capture per day in the month, oldest first."""
    stamp = f"{year:04d}{month:02d}"
    resp = _get(
        client,
        CDX_URL,
        {"url": feed_url, "from": stamp, "to": stamp, "output": "json", "filter": "statuscode:200",
         "collapse": "timestamp:8", "fl": "timestamp,original"},
    )
    if not resp.text.strip():
        return []
    try:
        rows = resp.json()
    except ValueError as exc:
        raise WaybackError("unexpected CDX response") from exc
    if not isinstance(rows, list):
        raise WaybackError("unexpected CDX response")
    limit = today.strftime("%Y%m%d%H%M%S")
    out = []
    for row in rows[1:]:  # the first row is the header
        if isinstance(row, list) and len(row) >= 2 and str(row[0]).isdigit() and str(row[0]) <= limit:
            out.append((str(row[0]), str(row[1])))
    return out


def spread(captures: list, n: int = SNAPSHOTS_PER_MONTH) -> list:
    """Pick n captures spread evenly across the month (first and last included)."""
    if len(captures) <= n:
        return list(captures)
    idx = sorted({round(i * (len(captures) - 1) / (n - 1)) for i in range(n)})
    return [captures[i] for i in idx]


def _capture_time(ts: str) -> datetime | None:
    try:
        return datetime.strptime(ts[:14], "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def fetch_publisher_month(
    client: httpx.Client, publisher_id: str, year: int, month: int, today: datetime
) -> MonthResult:
    """Read one publisher's archived feed for one month. Raises WaybackError (incl. rate limiting)."""
    feed = {"id": publisher_id, "name": PUBLISHER_NAMES[publisher_id]}
    captures, feed_url = [], None
    for url in ARCHIVED_FEEDS[publisher_id]:
        captures = list_captures(client, url, year, month, today)
        if captures:
            feed_url = url
            break
    if not captures:
        return MonthResult(publisher_id, "not_covered", detail="no archived feed captures for this month")
    items: dict[str, FeedItem] = {}
    chosen = spread(captures)
    failed = 0
    for ts, original in chosen:
        try:
            parsed = parse_feed(feed, _get(client, SNAPSHOT_URL.format(ts=ts, url=original)).content)
        except WaybackRateLimited:
            raise
        except (WaybackError, ValueError):
            failed += 1
            continue
        captured = _capture_time(ts)
        for it in parsed:
            if it.published_at is None:
                it.published_at = captured  # the feed listed it no later than the capture
            if it.published_at is not None and (it.published_at.year, it.published_at.month) == (year, month):
                items.setdefault(normalize_url(it.url), it)
    if failed == len(chosen):
        return MonthResult(publisher_id, "unavailable", feed_url=feed_url, detail="archived captures could not be read")
    if failed:  # usable, but incomplete: not cached so a later request can read the whole month
        return MonthResult(publisher_id, "unavailable", list(items.values()), feed_url, len(chosen) - failed,
                           f"{failed} of {len(chosen)} archived captures could not be read")
    return MonthResult(publisher_id, "ok", list(items.values()), feed_url, len(chosen))


def fetch_month(
    client: httpx.Client, publisher_ids: list[str], year: int, month: int, today: datetime
) -> dict[str, MonthResult]:
    """Archived items for several publishers, fetched in parallel. Never raises; each publisher gets a status."""
    current = (year, month) == (today.year, today.month)
    results: dict[str, MonthResult] = {}
    todo = []
    for pid in publisher_ids:
        hit = _cached((pid, year, month), today, current)
        if hit is not None:
            results[pid] = hit
        else:
            todo.append(pid)

    def one(pid: str) -> MonthResult:
        if _cooling_down(datetime.now(timezone.utc)):
            return MonthResult(pid, "unavailable", detail="Wayback Machine rate limit; retrying later")
        try:
            result = fetch_publisher_month(client, pid, year, month, today)
        except WaybackRateLimited as exc:
            _start_cooldown(datetime.now(timezone.utc))
            return MonthResult(pid, "unavailable", detail=str(exc))
        except WaybackError as exc:
            return MonthResult(pid, "unavailable", detail=str(exc))
        except Exception as exc:  # never let one publisher break the request
            return MonthResult(pid, "unavailable", detail=f"unexpected error: {exc.__class__.__name__}")
        if result.status in ("ok", "not_covered"):
            _remember((pid, year, month), today, result)
        return result

    if todo:
        with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(todo))) as pool:
            for r in pool.map(one, todo):
                results[r.publisher_id] = r
    return {pid: results[pid] for pid in publisher_ids}
