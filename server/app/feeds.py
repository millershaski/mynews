"""Feed sources, fetching, parsing and URL normalisation."""
import calendar
import hashlib
import html
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import feedparser
import httpx

FEEDS = [
    {"id": "bbc", "name": "BBC News", "url": "https://feeds.bbci.co.uk/news/rss.xml"},
    {"id": "npr", "name": "NPR", "url": "https://feeds.npr.org/1001/rss.xml"},
    {"id": "fox", "name": "Fox News", "url": "https://moxie.foxnews.com/google-publisher/latest.xml"},
    {"id": "guardian", "name": "The Guardian", "url": "https://www.theguardian.com/world/rss"},
    {"id": "cbs", "name": "CBS News", "url": "https://www.cbsnews.com/latest/rss/main"},
    {"id": "nyt", "name": "The New York Times", "url": "https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml"},
    {"id": "aljazeera", "name": "Al Jazeera", "url": "https://www.aljazeera.com/xml/rss/all.xml"},
    {"id": "abc", "name": "ABC News", "url": "https://feeds.abcnews.com/abcnews/topstories"},
]

TRACKING_PARAMS = {
    "fbclid", "gclid", "cmpid", "cmp", "ocid", "ftag", "at_medium", "at_campaign", "at_link_id",
    "at_link_origin", "at_link_type", "at_format", "at_bbc_team", "at_ptr_name", "at_ptr_type",
    "at_campaign_type", "partner", "smid", "smtyp", "taid", "ref", "rss", "dicbo", "intcid",
}

MAX_EXCERPT_CHARS = 700


@dataclass
class FeedItem:
    publisher_id: str
    publisher_name: str
    url: str
    guid: str | None
    title: str
    excerpt: str
    published_at: datetime | None  # None when missing or unparseable


def normalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    scheme = "https" if parts.scheme in ("http", "https", "") else parts.scheme.lower()
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=False)
        if not k.lower().startswith("utm_") and k.lower() not in TRACKING_PARAMS
    ]
    path = parts.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path[:-1]
    return urlunsplit((scheme, host, path, urlencode(sorted(query)), ""))


def article_id(normalized_url: str) -> str:
    return hashlib.sha1(normalized_url.encode("utf-8")).hexdigest()


def content_hash(title: str, excerpt: str) -> str:
    return hashlib.sha256(f"{title}\n{excerpt}".encode("utf-8")).hexdigest()


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def clean_text(raw: str | None) -> str:
    if not raw:
        return ""
    text = _TAG_RE.sub(" ", raw)
    text = html.unescape(text)
    return _WS_RE.sub(" ", text).strip()


def _entry_time(entry) -> datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        st = entry.get(key)
        if st:
            try:
                return datetime.fromtimestamp(calendar.timegm(st), tz=timezone.utc)
            except (OverflowError, ValueError, TypeError):
                continue
    return None


def parse_feed(feed: dict, body: bytes) -> list[FeedItem]:
    parsed = feedparser.parse(body)
    if parsed.bozo and not parsed.entries:
        raise ValueError(f"unparseable feed: {parsed.get('bozo_exception')!r}")
    items = []
    for entry in parsed.entries:
        link = (entry.get("link") or "").strip()
        title = clean_text(entry.get("title"))
        if not link or not title:
            continue
        excerpt = clean_text(entry.get("summary") or entry.get("description"))
        if len(excerpt) > MAX_EXCERPT_CHARS:
            excerpt = excerpt[: MAX_EXCERPT_CHARS - 1].rstrip() + "…"
        items.append(
            FeedItem(
                publisher_id=feed["id"],
                publisher_name=feed["name"],
                url=link,
                guid=entry.get("id"),
                title=title,
                excerpt=excerpt,
                published_at=_entry_time(entry),
            )
        )
    return items


def fetch_feed(client: httpx.Client, feed: dict) -> list[FeedItem]:
    resp = client.get(feed["url"], timeout=20.0, follow_redirects=True)
    resp.raise_for_status()
    return parse_feed(feed, resp.content)
