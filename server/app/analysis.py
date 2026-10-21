"""AI story analysis: prompt construction, response validation and storage."""
import hashlib
from datetime import datetime

from sqlalchemy.orm import Session

from .config import PROMPTS_DIR, Settings
from .gemini import AIError, GeminiClient
from .models import Article, Story, Summary
from .timeutil import iso, utcnow

STORY_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "overview": {"type": "STRING"},
        "agreement": {"type": "STRING"},
        "disagreement_or_uncertainty": {"type": "STRING"},
        "overview_sources": {"type": "ARRAY", "items": {"type": "STRING"}},
        "perspectives_status": {"type": "STRING", "enum": ["supported", "insufficient_evidence"]},
        "perspectives": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "label": {"type": "STRING"},
                    "summary": {"type": "STRING"},
                    "evidence_limits": {"type": "STRING"},
                    "sources": {"type": "ARRAY", "items": {"type": "STRING"}},
                },
                "required": ["label", "summary", "evidence_limits", "sources"],
            },
        },
    },
    "required": [
        "overview",
        "agreement",
        "disagreement_or_uncertainty",
        "overview_sources",
        "perspectives_status",
        "perspectives",
    ],
}


def story_input_hash(articles: list[Article]) -> str:
    parts = sorted(f"{a.id}:{a.content_hash}" for a in articles)
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def _sanitize(text: str) -> str:
    # Stop feed text from imitating our data delimiters.
    return text.replace("<<<", "‹‹‹").replace(">>>", "›››")


def select_prompt_articles(articles: list[Article], limit: int) -> list[Article]:
    """Newest article from each publisher first, then fill with the remaining newest."""
    newest = sorted(articles, key=lambda a: (a.published_at, a.id), reverse=True)
    chosen, seen = [], set()
    for a in newest:
        if a.publisher_id not in seen:
            chosen.append(a)
            seen.add(a.publisher_id)
    for a in newest:
        if len(chosen) >= limit:
            break
        if a not in chosen:
            chosen.append(a)
    chosen = chosen[:limit]
    return sorted(chosen, key=lambda a: (a.published_at, a.id))


def build_user_text(articles: list[Article], max_excerpt: int) -> tuple[str, dict[str, str]]:
    refs: dict[str, str] = {}
    lines = [
        f"Number of articles: {len(articles)}. Distinct publishers: {len({a.publisher_id for a in articles})}.",
        "Each article below is: reference code, publisher, publication time (UTC), headline, feed excerpt.",
        "<<<ARTICLES_BEGIN>>>",
    ]
    for i, a in enumerate(articles, start=1):
        ref = f"A{i}"
        refs[ref] = a.id
        excerpt = _sanitize(a.excerpt or "")[:max_excerpt] or "(no excerpt provided by the feed)"
        lines.append(
            f"[{ref}] publisher: {_sanitize(a.publisher_name)} | published: {iso(a.published_at)}"
            f"{' (estimated)' if a.date_estimated else ''}\n"
            f"headline: {_sanitize(a.title)}\nexcerpt: {excerpt}"
        )
    lines.append("<<<ARTICLES_END>>>")
    return "\n\n".join(lines), refs


def _clean(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def _map_refs(values, refs: dict[str, str]) -> list[str]:
    out = []
    for v in values if isinstance(values, list) else []:
        aid = refs.get(_clean(v).strip("[]").upper())
        if aid and aid not in out:
            out.append(aid)
    return out


def validate_result(result: dict, refs: dict[str, str]) -> dict:
    """Check the model output and map reference codes to article IDs. Raises AIError if unusable."""
    overview = _clean(result.get("overview"))
    agreement = _clean(result.get("agreement"))
    disagreement = _clean(result.get("disagreement_or_uncertainty"))
    if not overview or not agreement or not disagreement:
        raise AIError("response missing overview, agreement or disagreement text")
    overview_sources = _map_refs(result.get("overview_sources"), refs)
    if not overview_sources:
        raise AIError("overview cited no valid source references")

    perspectives = []
    if result.get("perspectives_status") == "supported":
        for p in result.get("perspectives") or []:
            if not isinstance(p, dict):
                continue
            label, summary = _clean(p.get("label")), _clean(p.get("summary"))
            sources = _map_refs(p.get("sources"), refs)
            if label and summary and sources:
                perspectives.append(
                    {
                        "label": label,
                        "summary": summary,
                        "evidence_limits": _clean(p.get("evidence_limits"))
                        or "Based only on the feed excerpts listed as sources.",
                        "sources": sources,
                    }
                )
    status = "supported" if len(perspectives) >= 2 else "insufficient_evidence"
    if status == "insufficient_evidence":
        perspectives = []
    return {
        "overview": overview,
        "agreement": agreement,
        "disagreement_or_uncertainty": disagreement,
        "overview_sources": overview_sources,
        "perspectives_status": status,
        "perspectives": perspectives,
    }


def analyze_story(
    session: Session,
    client: GeminiClient,
    settings: Settings,
    story: Story,
    articles: list[Article],
    daily_cap: int,
    now: datetime | None = None,
) -> Summary:
    cfg = settings.model_config
    prompt = (PROMPTS_DIR / cfg["story_prompt_file"]).read_text(encoding="utf-8")
    chosen = select_prompt_articles(articles, cfg.get("max_articles_per_story_prompt", 12))
    user_text, refs = build_user_text(chosen, cfg.get("max_excerpt_chars_in_prompt", 600))
    raw = client.generate_json(
        session,
        purpose="story_summary",
        ref=story.id,
        system_prompt=prompt,
        user_text=user_text,
        schema=STORY_SCHEMA,
        daily_cap=daily_cap,
    )
    data = validate_result(raw, refs)

    summary = story.summary or Summary(story_id=story.id)
    summary.input_hash = story.input_hash
    summary.article_ids = [a.id for a in chosen]
    summary.overview = data["overview"]
    summary.agreement = data["agreement"]
    summary.disagreement_or_uncertainty = data["disagreement_or_uncertainty"]
    summary.overview_sources = data["overview_sources"]
    summary.perspectives_status = data["perspectives_status"]
    summary.perspectives = data["perspectives"]
    summary.model = client.last_model  # the model that answered, which may be a fallback
    summary.prompt_version = cfg["story_prompt_version"]
    summary.generated_at = now or utcnow()
    if story.summary is None:
        session.add(summary)
        story.summary = summary
    return summary
