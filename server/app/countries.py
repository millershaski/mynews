"""Country metadata for the explore globe: names, text aliases and Guardian tags (ISO 3166-1 alpha-2 codes)."""
import json
import re
from functools import lru_cache

from .config import DATA_DIR


@lru_cache
def all_countries() -> dict[str, dict]:
    return json.loads((DATA_DIR / "countries.json").read_text(encoding="utf-8"))


def get_country(code: str) -> dict | None:
    return all_countries().get((code or "").upper())


@lru_cache(maxsize=512)
def alias_pattern(code: str) -> re.Pattern | None:
    """Case-sensitive whole-word match on any alias. Aliases are proper nouns, so case avoids 'turkey' etc."""
    country = get_country(code)
    if not country:
        return None
    aliases = sorted({a for a in country["aliases"] if a}, key=len, reverse=True)
    body = "|".join(re.escape(a) for a in aliases)
    return re.compile(rf"(?<![A-Za-z0-9])(?:{body})(?![A-Za-z0-9])")


def mentions(text: str, code: str) -> bool:
    pattern = alias_pattern(code)
    return bool(pattern and text and pattern.search(text))
