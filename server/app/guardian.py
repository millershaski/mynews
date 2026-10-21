"""The Guardian Open Platform content search, used to find archive candidates for the explore globe.

Only metadata is used: headline, the publisher's standfirst (trailText), section, date, link and thumbnail.
"""
import calendar
from dataclasses import dataclass
from datetime import datetime

import httpx

from .feeds import clean_text
from .timeutil import as_utc

GUARDIAN_SEARCH = "https://content.guardianapis.com/search"
PAGE_SIZE = 30
MIN_TAG_RESULTS = 5  # below this, widen the search with a keyword query
MAX_QUERY_ALIASES = 4


class GuardianError(Exception):
    pass


@dataclass
class Candidate:
    id: str
    url: str
    headline: str
    trail_text: str
    thumbnail: str | None
    section: str
    published_at: datetime | None
    publisher: str = "The Guardian"
    category_enforced: bool = False  # True when the search itself restricted results to the category


def month_range(year: int, month: int, today: datetime | None = None) -> tuple[str, str]:
    last = calendar.monthrange(year, month)[1]
    end = f"{year:04d}-{month:02d}-{last:02d}"
    if today is not None and (today.year, today.month) == (year, month):
        end = today.strftime("%Y-%m-%d")
    return f"{year:04d}-{month:02d}-01", end


def _parse(result: dict) -> Candidate | None:
    if result.get("type") != "article":
        return None
    fields = result.get("fields") or {}
    headline = clean_text(fields.get("headline") or result.get("webTitle"))
    if not headline or not result.get("webUrl"):
        return None
    published = None
    raw = result.get("webPublicationDate")
    if raw:
        try:
            published = as_utc(datetime.fromisoformat(raw.replace("Z", "+00:00")))
        except ValueError:
            published = None
    return Candidate(
        id=result.get("id", ""),
        url=result["webUrl"],
        headline=headline,
        trail_text=clean_text(fields.get("trailText")),
        thumbnail=fields.get("thumbnail"),
        section=result.get("sectionName") or result.get("sectionId") or "",
        published_at=published,
    )


def search(client: httpx.Client, api_key: str, *, from_date: str, to_date: str, **filters) -> list[Candidate]:
    params = {
        "api-key": api_key,
        "from-date": from_date,
        "to-date": to_date,
        "page-size": str(PAGE_SIZE),
        "order-by": "relevance",
        "show-fields": "trailText,headline,thumbnail",
    }
    params.update({k.replace("_", "-"): v for k, v in filters.items() if v})
    try:
        resp = client.get(GUARDIAN_SEARCH, params=params, timeout=20.0)
    except httpx.HTTPError as exc:
        raise GuardianError(f"network error: {exc.__class__.__name__}") from exc
    if resp.status_code in (401, 403):
        raise GuardianError(f"Guardian API rejected the key ({resp.status_code})")
    if resp.status_code == 429:
        raise GuardianError("Guardian API rate limit reached (429)")
    if resp.status_code >= 400:
        raise GuardianError(f"Guardian API error {resp.status_code}")
    try:
        results = resp.json()["response"].get("results") or []
    except (ValueError, KeyError, TypeError) as exc:
        raise GuardianError("unexpected Guardian API response") from exc
    return [c for c in (_parse(r) for r in results) if c is not None]


def alias_query(country: dict) -> str:
    aliases = [a for a in country["aliases"] if len(a) > 2][:MAX_QUERY_ALIASES] or [country["name"]]
    return " OR ".join(f'"{a}"' for a in aliases)


def find_candidates(
    client: httpx.Client, api_key: str, country: dict, category: dict, year: int, month: int, today: datetime
) -> tuple[list[Candidate], str]:
    """Return (candidates, description of the query used)."""
    from_date, to_date = month_range(year, month, today)
    section = category.get("guardian_section")
    q = alias_query(country)
    if section:
        found = search(client, api_key, from_date=from_date, to_date=to_date, section=section, q=q)
        for c in found:
            c.category_enforced = True
        return found, f"section={section} q={q} {from_date}..{to_date}"

    found, desc = [], []
    tag = country.get("guardian_tag")
    if tag:
        found = search(client, api_key, from_date=from_date, to_date=to_date, tag=tag)
        desc.append(f"tag={tag}")
    if len(found) < MIN_TAG_RESULTS:
        seen = {c.id for c in found}
        extra = search(client, api_key, from_date=from_date, to_date=to_date, q=q)
        found += [c for c in extra if c.id not in seen]
        desc.append(f"q={q}")
    return found, f"{' + '.join(desc)} {from_date}..{to_date}"
