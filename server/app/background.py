"""Background resources: links to Wikipedia pages for the main named entities in a story.

A page is accepted only when its title matches the entity's words and it is not a disambiguation page.
If nothing passes these checks the story shows "No background resource available".
"""
from collections import Counter

import httpx

from .textutil import entities, tokens

WIKI_API = "https://en.wikipedia.org/w/api.php"
MAX_LINKS_PER_STORY = 3
MAX_ENTITIES_TRIED = 4


def candidate_entities(titles: list[str], excerpts: list[str]) -> list[str]:
    """Entity phrases ranked by how many articles mention them (headline mentions count double)."""
    counts: Counter = Counter()
    display: dict[str, str] = {}
    for i, title in enumerate(titles):
        seen = set()
        for phrase in entities(title) * 2 + entities(excerpts[i] if i < len(excerpts) else ""):
            key = " ".join(tokens(phrase))
            if not key:
                continue
            display.setdefault(key, phrase)
            counts[key] += 1 if key in seen else 2
            seen.add(key)
    # Drop single-word entities that are contained in a longer, equally frequent phrase.
    ranked = []
    for key, n in counts.most_common():
        if any(key != other and key in other.split() and counts[other] >= n for other in counts):
            continue
        ranked.append(display[key])
    return ranked


def _title_matches(entity: str, page_title: str) -> bool:
    ent = set(tokens(entity))
    page = set(tokens(page_title.split("(")[0]))
    return bool(ent) and ent <= page and len(page) <= len(ent) + 2


def lookup(client: httpx.Client, entity: str, user_agent: str | None = None) -> dict | None:
    resp = client.get(
        WIKI_API,
        headers={"User-Agent": user_agent} if user_agent else None,
        params={
            "action": "query",
            "format": "json",
            "formatversion": "2",
            "generator": "search",
            "gsrsearch": entity,
            "gsrlimit": "3",
            "prop": "info|description|pageprops",
            "inprop": "url",
            "ppprop": "disambiguation",
            "redirects": "1",
        },
        timeout=15.0,
    )
    resp.raise_for_status()
    pages = sorted(resp.json().get("query", {}).get("pages", []), key=lambda p: p.get("index", 99))
    for page in pages:
        if "disambiguation" in (page.get("pageprops") or {}):
            continue
        if not _title_matches(entity, page.get("title", "")):
            continue
        return {
            "title": page["title"],
            "url": page.get("fullurl") or f"https://en.wikipedia.org/wiki/{page['title'].replace(' ', '_')}",
            "description": page.get("description", ""),
        }
    return None


def label_for(resource_title: str, description: str, source: str) -> str:
    if description:
        return f"{resource_title} — {description} ({source})"
    return f"{resource_title} ({source})"
