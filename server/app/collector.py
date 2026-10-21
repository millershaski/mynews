"""The hourly collection job.

Order of work (each step commits, so a later failure never undoes or erases earlier, consistent work):

  1. guard against overlapping runs
  2. fetch every feed independently (one failed feed is recorded and skipped)
  3. upsert articles, deduplicated by normalised URL
  4. delete articles older than the retention window, any summary that cited them, empty stories and
     background resources no story links to any more
  5. group new articles into stories and refresh headline / latest time / input hash
  6. look up background resources
  7. generate AI summaries for stories whose inputs changed, within the per-run and daily budgets
  8. record the job result and the last successful collection time
"""
import hashlib
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Callable

import httpx
from sqlalchemy import delete, exists, select
from sqlalchemy.orm import Session, sessionmaker

from . import background as bg
from .analysis import analyze_story, story_input_hash
from .config import Settings
from .feeds import FEEDS, FeedItem, article_id, content_hash, fetch_feed, normalize_url
from .gemini import AIError, GeminiClient
from .grouping import Doc, group, pick_headline
from .models import (
    AppState,
    Article,
    BackgroundResource,
    FeedStatus,
    JobRun,
    Story,
    StoryBackground,
    Summary,
)
from .timeutil import iso, utcnow

log = logging.getLogger("mynews.collector")

RUN_STALE_AFTER = timedelta(minutes=30)
FUTURE_TOLERANCE = timedelta(hours=1)
AI_RETRY_AFTER = timedelta(minutes=50)
MAX_PROVIDER_FAILURES_IN_A_ROW = 3

LAST_SUCCESS_KEY = "last_successful_collection_at"
LAST_RUN_KEY = "last_run_at"

Fetcher = Callable[[httpx.Client, dict], list[FeedItem]]


# --------------------------------------------------------------------------- app state helpers


def get_state(session: Session, key: str) -> str | None:
    row = session.get(AppState, key)
    return row.value if row else None


def set_state(session: Session, key: str, value: str) -> None:
    row = session.get(AppState, key)
    if row:
        row.value = value
    else:
        session.add(AppState(key=key, value=value))


# --------------------------------------------------------------------------- steps


def _start_run(session: Session, trigger: str, now: datetime) -> JobRun | None:
    """Returns the new running JobRun, or None (after recording a skipped run) if another run is active."""
    running = session.scalars(select(JobRun).where(JobRun.status == "running")).all()
    for r in running:
        if r.started_at >= now - RUN_STALE_AFTER:
            session.add(
                JobRun(
                    trigger=trigger,
                    started_at=now,
                    finished_at=now,
                    status="skipped",
                    error=f"run {r.id} already in progress since {iso(r.started_at)}",
                )
            )
            session.commit()
            return None
        r.status = "failed"
        r.finished_at = now
        r.error = (r.error or "") + " abandoned: did not finish within 30 minutes"
    job = JobRun(trigger=trigger, started_at=now, status="running", details={})
    session.add(job)
    session.commit()
    return job


def _fetch_all(http: httpx.Client, fetcher: Fetcher, feeds: list[dict]) -> dict[str, tuple[list[FeedItem], str | None]]:
    def one(feed):
        try:
            return feed["id"], (fetcher(http, feed), None)
        except Exception as exc:  # any failure of one feed is recorded, never fatal
            return feed["id"], ([], f"{exc.__class__.__name__}: {str(exc)[:300]}")

    with ThreadPoolExecutor(max_workers=min(8, len(feeds) or 1)) as pool:
        return dict(pool.map(one, feeds))


def _record_feeds(session: Session, job: JobRun, feeds: list[dict], results: dict, now: datetime) -> None:
    detail = {}
    for feed in feeds:
        items, error = results[feed["id"]]
        row = session.get(FeedStatus, feed["id"]) or FeedStatus(publisher_id=feed["id"], name=feed["name"], url=feed["url"])
        row.name, row.url = feed["name"], feed["url"]
        row.last_attempt_at = now
        if error:
            row.last_error = error
            job.feeds_failed += 1
        else:
            row.last_success_at = now
            row.last_error = None
            row.last_item_count = len(items)
            job.feeds_ok += 1
            job.articles_fetched += len(items)
        session.add(row)
        detail[feed["id"]] = {"ok": error is None, "items": len(items), "error": error}
    job.details = {**(job.details or {}), "feeds": detail}
    session.commit()


def _upsert_articles(session: Session, job: JobRun, items: list[FeedItem], now: datetime, cutoff: datetime) -> None:
    """Insert new articles and refresh changed ones. Date rules (documented in README "Retention"):

    * missing or unparseable date -> first time we saw the item, flagged `date_estimated`
    * more than 1 hour in the future -> clamped to the fetch time, flagged `date_estimated`
    * older than the retention cutoff -> not stored
    """
    stats = {"date_missing": 0, "date_future_clamped": 0, "too_old_skipped": 0, "duplicates_in_run": 0}
    seen: dict[str, FeedItem] = {}
    for item in items:
        norm = normalize_url(item.url)
        aid = article_id(norm)
        if aid in seen:
            stats["duplicates_in_run"] += 1
            continue
        seen[aid] = item

        existing = session.get(Article, aid)
        chash = content_hash(item.title, item.excerpt)
        if existing:
            existing.last_seen_at = now
            if existing.content_hash != chash:
                existing.title, existing.excerpt, existing.content_hash = item.title, item.excerpt, chash
                job.articles_updated += 1
            continue

        published, estimated = item.published_at, False
        if published is None:
            published, estimated = now, True
            stats["date_missing"] += 1
        elif published > now + FUTURE_TOLERANCE:
            published, estimated = now, True
            stats["date_future_clamped"] += 1
        if published < cutoff:
            stats["too_old_skipped"] += 1
            continue

        session.add(
            Article(
                id=aid,
                normalized_url=norm,
                url=item.url,
                guid=item.guid,
                publisher_id=item.publisher_id,
                publisher_name=item.publisher_name,
                title=item.title,
                excerpt=item.excerpt,
                published_at=published,
                date_estimated=estimated,
                first_seen_at=now,
                last_seen_at=now,
                content_hash=chash,
            )
        )
        job.articles_new += 1
    job.details = {**(job.details or {}), "dates": stats}
    session.commit()


def expire_and_cleanup(session: Session, cutoff: datetime, job: JobRun | None = None) -> dict:
    """Delete expired articles, summaries citing them, empty stories and orphaned background resources.

    Runs as one transaction so a reader never sees a summary whose source articles are gone.
    """
    expired = session.execute(select(Article.id, Article.story_id).where(Article.published_at < cutoff)).all()
    expired_ids = {r.id for r in expired}
    touched = {r.story_id for r in expired if r.story_id}

    invalidated = 0
    for sid in touched:
        summary = session.scalar(select(Summary).where(Summary.story_id == sid))
        if summary and expired_ids & set(summary.article_ids or []):
            session.delete(summary)
            story = session.get(Story, sid)
            if story:
                story.ai_status, story.ai_error = "pending", None
            invalidated += 1
    session.flush()

    ids = list(expired_ids)
    for i in range(0, len(ids), 500):
        session.execute(delete(Article).where(Article.id.in_(ids[i : i + 500])))

    empty = session.scalars(select(Story.id).where(~exists().where(Article.story_id == Story.id))).all()
    for i in range(0, len(empty), 500):
        chunk = empty[i : i + 500]
        session.execute(delete(Summary).where(Summary.story_id.in_(chunk)))
        session.execute(delete(StoryBackground).where(StoryBackground.story_id.in_(chunk)))
        session.execute(delete(Story).where(Story.id.in_(chunk)))
    session.execute(
        delete(BackgroundResource).where(~exists().where(StoryBackground.resource_id == BackgroundResource.id))
    )
    session.commit()
    session.expire_all()

    result = {"articles_expired": len(expired_ids), "stories_deleted": len(empty), "summaries_invalidated": invalidated}
    if job is not None:
        job.articles_expired += result["articles_expired"]
        job.stories_deleted += result["stories_deleted"]
        job.summaries_invalidated += result["summaries_invalidated"]
        session.commit()
    return result


def _doc(a: Article) -> Doc:
    return Doc(id=a.id, title=a.title, excerpt=a.excerpt or "", published_at=a.published_at, publisher_id=a.publisher_id)


def group_and_refresh(session: Session, now: datetime, job: JobRun | None = None) -> dict:
    """Assign unassigned articles to stories, then refresh every story's derived fields."""
    articles = session.scalars(select(Article)).all()
    by_id = {a.id: a for a in articles}
    assignments = {a.id: a.story_id for a in articles if a.story_id}
    taken = set(session.scalars(select(Story.id)).all())

    decisions = group([_doc(a) for a in articles], assignments, taken)
    created = 0
    for d in decisions:
        if d.created:
            a = by_id[d.doc_id]
            session.add(
                Story(
                    id=d.story_id,
                    headline=a.title,
                    created_at=now,
                    updated_at=now,
                    latest_article_at=a.published_at,
                    input_hash="",
                    ai_status="pending",
                    background_status="pending",
                )
            )
            created += 1
    session.flush()
    for d in decisions:
        by_id[d.doc_id].story_id = d.story_id
    session.flush()

    members: dict[str, list[Article]] = {}
    for a in articles:
        if a.story_id:
            members.setdefault(a.story_id, []).append(a)
    for story in session.scalars(select(Story)).all():
        arts = members.get(story.id)
        if not arts:
            continue
        new_hash = story_input_hash(arts)
        if new_hash != story.input_hash:
            story.input_hash = new_hash
            story.updated_at = now
        head = pick_headline([_doc(a) for a in arts])
        story.headline = head.title
        story.latest_article_at = max(a.published_at for a in arts)
    session.commit()

    result = {"stories_created": created, "articles_grouped": len(decisions), "joined_existing": len(decisions) - created}
    if job is not None:
        job.stories_created += created
        job.details = {**(job.details or {}), "grouping": result}
        session.commit()
    return result


def _story_priority(story: Story, arts: list[Article]):
    return (-len({a.publisher_id for a in arts}), -story.latest_article_at.timestamp())


def _load_members(session: Session) -> dict[str, list[Article]]:
    members: dict[str, list[Article]] = {}
    for a in session.scalars(select(Article).where(Article.story_id.is_not(None))).all():
        members.setdefault(a.story_id, []).append(a)
    return members


def refresh_background(session: Session, http: httpx.Client, settings: Settings, job: JobRun | None = None) -> dict:
    if not settings.wiki_contact:
        result = {"skipped": "WIKI_CONTACT not configured (Wikimedia requires a contact URL or email)"}
        if job is not None:
            job.details = {**(job.details or {}), "background": result}
            session.commit()
        return result
    members = _load_members(session)
    stories = [s for s in session.scalars(select(Story)).all() if s.id in members]
    stories.sort(key=lambda s: _story_priority(s, members[s.id]))

    budget = settings.max_background_lookups_per_run
    lookups = updated = failures = 0
    cache: dict[str, dict | None] = {}
    for story in stories:
        arts = members[story.id]
        ents = bg.candidate_entities([a.title for a in arts], [a.excerpt or "" for a in arts])[: bg.MAX_ENTITIES_TRIED]
        key = hashlib.sha256("|".join(ents).encode()).hexdigest()
        if key == story.background_input_hash and story.background_status in ("found", "none"):
            continue
        uncached = [e for e in ents if e.lower() not in cache]
        if lookups + len(uncached) > budget:
            continue  # try again next run

        found, failed = [], False
        for entity in ents:
            if len(found) >= bg.MAX_LINKS_PER_STORY:
                break
            ck = entity.lower()
            if ck not in cache:
                lookups += 1
                try:
                    cache[ck] = bg.lookup(http, entity, settings.wiki_user_agent)
                except Exception as exc:
                    log.warning("background lookup failed for %r: %s", entity, exc)
                    failed = True
                    cache[ck] = None
                    continue
            res = cache[ck]
            if res and all(res["url"] != f[1]["url"] for f in found):
                found.append((entity, res))

        if failed and not found:
            story.background_status = "failed"  # keep any existing links; retried next run
            failures += 1
            continue
        session.execute(delete(StoryBackground).where(StoryBackground.story_id == story.id))
        for entity, res in found:
            resource = session.scalar(select(BackgroundResource).where(BackgroundResource.url == res["url"]))
            if resource is None:
                resource = BackgroundResource(
                    url=res["url"], title=res["title"], description=res.get("description", ""), source="Wikipedia", created_at=utcnow()
                )
                session.add(resource)
                session.flush()
            session.add(StoryBackground(story_id=story.id, resource_id=resource.id, entity=entity))
        story.background_status = "found" if found else "none"
        story.background_input_hash = key if not failed else ""
        updated += 1
        session.commit()

    session.execute(
        delete(BackgroundResource).where(~exists().where(StoryBackground.resource_id == BackgroundResource.id))
    )
    session.commit()
    result = {"wikipedia_requests": lookups, "stories_updated": updated, "failures": failures}
    if job is not None:
        job.details = {**(job.details or {}), "background": result}
        session.commit()
    return result


def stories_needing_ai(session: Session, now: datetime) -> list[tuple[Story, list[Article]]]:
    members = _load_members(session)
    out = []
    for story in session.scalars(select(Story)).all():
        arts = members.get(story.id)
        if not arts:
            continue
        if story.summary is not None and story.summary.input_hash == story.input_hash:
            continue
        if (
            story.ai_status == "failed"
            and story.ai_last_attempt_at
            and story.ai_last_attempt_at > now - AI_RETRY_AFTER
        ):
            continue
        out.append((story, arts))
    # Missing summaries before outdated ones, then most publishers, then newest.
    out.sort(key=lambda sa: (sa[0].summary is not None, *_story_priority(*sa)))
    return out


def generate_summaries(session: Session, ai: GeminiClient, settings: Settings, now: datetime, job: JobRun | None = None) -> dict:
    result = {"generated": 0, "failed": 0, "queued": 0, "stopped_reason": None}
    if not ai.configured:
        result["stopped_reason"] = "GEMINI_API_KEY not configured"
        if job is not None:
            job.details = {**(job.details or {}), "ai": result}
            session.commit()
        return result
    queue = stories_needing_ai(session, now)
    result["queued"] = len(queue)
    daily_cap = max(0, settings.ai_daily_limit - settings.explore_ai_reserve)
    provider_failures_in_a_row = 0
    for story, arts in queue[: settings.max_ai_calls_per_run]:
        story.ai_last_attempt_at = utcnow()
        try:
            analyze_story(session, ai, settings, story, arts, daily_cap=daily_cap)
            story.ai_status, story.ai_error = "ok", None
            result["generated"] += 1
            provider_failures_in_a_row = 0
            session.commit()
        except Exception as exc:  # provider, validation or unexpected errors: record and move on
            sid = story.id
            session.rollback()
            story = session.get(Story, sid)
            if story is not None:
                msg = str(exc) if isinstance(exc, AIError) else f"{exc.__class__.__name__}: {exc}"
                story.ai_status, story.ai_error = "failed", msg[:500]
                story.ai_last_attempt_at = utcnow()
                session.commit()
            result["failed"] += 1
            if getattr(exc, "quota", False):
                result["stopped_reason"] = str(exc)
                break
            # A provider outage (every model busy) would otherwise cost one failed call per queued story.
            # Stop early; the remaining stories stay queued for the next hourly run.
            if isinstance(exc, AIError) and exc.status_code is not None:
                provider_failures_in_a_row += 1
                if provider_failures_in_a_row >= MAX_PROVIDER_FAILURES_IN_A_ROW:
                    result["stopped_reason"] = f"AI provider unavailable ({provider_failures_in_a_row} failures in a row): {exc}"[:500]
                    break
            else:
                provider_failures_in_a_row = 0
    if job is not None:
        job.summaries_generated += result["generated"]
        job.ai_failures += result["failed"]
        job.details = {**(job.details or {}), "ai": result}
        session.commit()
    return result


# --------------------------------------------------------------------------- entry point


def _background_failed(job: JobRun) -> bool:
    """Background links are optional, but a failed lookup still makes the run "partial" so it is visible."""
    bg_result = (job.details or {}).get("background") or {}
    return bool(bg_result.get("error") or bg_result.get("failures"))


def run_collection(
    session_factory: sessionmaker,
    settings: Settings,
    *,
    trigger: str = "manual",
    http: httpx.Client | None = None,
    ai: GeminiClient | None = None,
    fetcher: Fetcher = fetch_feed,
    feeds: list[dict] | None = None,
    now: datetime | None = None,
) -> JobRun:
    feeds = feeds if feeds is not None else FEEDS
    now = now or utcnow()
    cutoff = now - timedelta(hours=settings.retention_hours)
    own_http = http is None
    http = http or httpx.Client(headers={"User-Agent": settings.user_agent}, follow_redirects=True)
    ai = ai or GeminiClient(settings)

    with session_factory() as session:
        job = _start_run(session, trigger, now)
        if job is None:
            return session.scalars(select(JobRun).order_by(JobRun.id.desc()).limit(1)).one()
        step = "start"
        try:
            step = "fetch"
            results = _fetch_all(http, fetcher, feeds)
            _record_feeds(session, job, feeds, results, now)

            step = "store"
            items = [it for fid in (f["id"] for f in feeds) for it in results[fid][0]]
            _upsert_articles(session, job, items, now, cutoff)

            step = "expire"
            expire_and_cleanup(session, cutoff, job)

            step = "group"
            group_and_refresh(session, now, job)

            step = "background"
            try:
                refresh_background(session, http, settings, job)
            except Exception as exc:  # background links are optional; never fail the run for them
                session.rollback()
                log.exception("background step failed")
                job.details = {**(job.details or {}), "background": {"error": str(exc)[:300]}}
                session.commit()

            step = "ai"
            generate_summaries(session, ai, settings, now, job)

            step = "finish"
            job.articles_active = session.query(Article).count()
            job.stories_active = session.query(Story).count()
            if job.feeds_ok == 0:
                job.status = "failed"
                job.error = "every feed failed; existing data kept"
            elif job.feeds_failed or job.ai_failures or _background_failed(job):
                job.status = "partial"
            else:
                job.status = "success"
            job.finished_at = utcnow()
            if job.feeds_ok > 0:
                set_state(session, LAST_SUCCESS_KEY, iso(job.finished_at))
            set_state(session, LAST_RUN_KEY, iso(job.finished_at))
            session.commit()
        except Exception as exc:
            log.exception("collection failed during %s", step)
            session.rollback()
            job = session.get(JobRun, job.id)
            job.status = "failed"
            job.error = f"failed during {step}: {exc.__class__.__name__}: {str(exc)[:400]}"
            job.finished_at = utcnow()
            set_state(session, LAST_RUN_KEY, iso(job.finished_at))
            session.commit()
        finally:
            if own_http:
                http.close()
        return job
