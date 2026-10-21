"""HTTP API. Public read endpoints are documented in README.md ("API"); FastAPI also serves /docs."""
import logging
import secrets
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, select

from . import explore as explore_mod
from . import wayback
from .collector import LAST_RUN_KEY, LAST_SUCCESS_KEY, get_state, run_collection
from .config import Settings, get_settings
from .db import init_db, make_engine, make_session_factory
from .feeds import FEEDS
from .gemini import calls_today
from .models import Article, ExplorePick, FeedStatus, JobRun, Story, Summary
from .timeutil import iso, utcnow
from .views import cutoff_for, list_stories, story_detail

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

VERSION = "1.0.0"


def job_json(job: JobRun) -> dict:
    return {
        "id": job.id,
        "trigger": job.trigger,
        "status": job.status,
        "started_at": iso(job.started_at),
        "finished_at": iso(job.finished_at),
        "feeds_ok": job.feeds_ok,
        "feeds_failed": job.feeds_failed,
        "articles_fetched": job.articles_fetched,
        "articles_new": job.articles_new,
        "articles_updated": job.articles_updated,
        "articles_expired": job.articles_expired,
        "articles_active": job.articles_active,
        "stories_created": job.stories_created,
        "stories_deleted": job.stories_deleted,
        "stories_active": job.stories_active,
        "summaries_generated": job.summaries_generated,
        "summaries_invalidated": job.summaries_invalidated,
        "ai_failures": job.ai_failures,
        "details": job.details,
        "error": job.error,
    }


def create_app(
    settings: Settings | None = None,
    session_factory=None,
    ai_client=None,
    explore_http=None,
    collect_kwargs: dict | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    if session_factory is None:
        session_factory = make_session_factory(make_engine(settings.database_url))
    collect_kwargs = collect_kwargs or {}

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        init_db(session_factory.kw["bind"])
        yield

    app = FastAPI(
        title="My News API",
        version=VERSION,
        description="Grouped news stories from several publishers over a rolling 7-day window, with shared "
        "AI overviews, plus an archive explorer. All times are UTC (ISO 8601).",
        lifespan=lifespan,
    )
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"], allow_headers=["*"])

    def require_admin(authorization: str | None) -> None:
        if not settings.admin_token:
            raise HTTPException(503, "ADMIN_TOKEN is not configured on the server")
        expected = f"Bearer {settings.admin_token}"
        if not authorization or not secrets.compare_digest(authorization.encode(), expected.encode()):
            raise HTTPException(401, "missing or invalid admin token", headers={"WWW-Authenticate": "Bearer"})

    @app.get("/")
    def root():
        return {
            "name": "My News API",
            "version": VERSION,
            "docs": "/docs",
            "endpoints": ["/stories", "/stories/{id}", "/explore", "/status", "/runs", "/health"],
        }

    @app.get("/health")
    def health():
        return {"ok": True, "time": iso(utcnow())}

    @app.get("/stories")
    def get_stories(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
        now = utcnow()
        cutoff = cutoff_for(now, settings.retention_hours)
        with session_factory() as session:
            items, total = list_stories(session, cutoff, limit, offset)
            last_success = get_state(session, LAST_SUCCESS_KEY)
        return {
            "stories": items,
            "count": len(items),
            "total": total,
            "limit": limit,
            "offset": offset,
            "window_start": iso(cutoff),
            "window_hours": settings.retention_hours,
            "last_successful_collection_at": last_success,
            "generated_at": iso(now),
        }

    @app.get("/stories/{story_id}")
    def get_story(story_id: str):
        now = utcnow()
        with session_factory() as session:
            detail = story_detail(session, story_id, cutoff_for(now, settings.retention_hours))
            last_success = get_state(session, LAST_SUCCESS_KEY)
        if detail is None:
            raise HTTPException(404, "story not found or no longer within the 7-day window")
        return {**detail, "last_successful_collection_at": last_success, "generated_at": iso(now)}

    @app.get("/explore")
    def get_explore(
        country: str = Query(..., min_length=2, max_length=2, description="ISO 3166-1 alpha-2 code"),
        year: int = Query(..., ge=1900, le=2100),
        month: int = Query(..., ge=1, le=12),
        category: str = Query("all"),
    ):
        try:
            return explore_mod.get_explore(
                session_factory, settings, country, year, month, category, http=explore_http, ai=ai_client
            )
        except explore_mod.ExploreInputError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/explore/categories")
    def explore_categories():
        return {
            "categories": [{"id": k, "label": v["label"]} for k, v in explore_mod.CATEGORIES.items()],
            "earliest": {"year": explore_mod.EARLIEST[0], "month": explore_mod.EARLIEST[1]},
        }

    @app.get("/status")
    def status():
        now = utcnow()
        with session_factory() as session:
            feeds = {f.publisher_id: f for f in session.scalars(select(FeedStatus))}
            last = session.scalars(select(JobRun).order_by(JobRun.id.desc()).limit(1)).one_or_none()
            return {
                "time": iso(now),
                "last_successful_collection_at": get_state(session, LAST_SUCCESS_KEY),
                "last_run_at": get_state(session, LAST_RUN_KEY),
                "last_run": job_json(last) if last else None,
                "window_hours": settings.retention_hours,
                "articles": session.scalar(select(func.count(Article.id))),
                "stories": session.scalar(select(func.count(Story.id))),
                "summaries": session.scalar(select(func.count(Summary.story_id))),
                "explore_picks": session.scalar(select(func.count(ExplorePick.id))),
                "ai": {
                    "configured": bool(settings.gemini_api_key),
                    "model": settings.gemini_model,
                    "fallback_models": settings.model_config.get("fallback_models", []),
                    "calls_today_utc": calls_today(session),
                    "daily_limit": settings.ai_daily_limit,
                },
                "archive_configured": True,  # the Wayback Machine sources need no key
                "archive_sources": {
                    "guardian_api": "configured" if settings.guardian_api_key else "not_configured",
                    "wayback_publishers": [wayback.PUBLISHER_NAMES[p] for p in wayback.ARCHIVED_FEEDS],
                },
                "feeds": [
                    {
                        "id": f["id"],
                        "name": f["name"],
                        "url": f["url"],
                        "last_attempt_at": iso(feeds[f["id"]].last_attempt_at) if f["id"] in feeds else None,
                        "last_success_at": iso(feeds[f["id"]].last_success_at) if f["id"] in feeds else None,
                        "last_error": feeds[f["id"]].last_error if f["id"] in feeds else None,
                        "last_item_count": feeds[f["id"]].last_item_count if f["id"] in feeds else 0,
                    }
                    for f in FEEDS
                ],
            }

    @app.get("/runs")
    def runs(limit: int = Query(20, ge=1, le=100)):
        with session_factory() as session:
            rows = session.scalars(select(JobRun).order_by(JobRun.id.desc()).limit(limit)).all()
            return {"runs": [job_json(r) for r in rows]}

    @app.post("/admin/collect")
    def admin_collect(
        authorization: str | None = Header(None),
        trigger: str = Query("manual", pattern="^(manual|schedule)$"),
    ):
        require_admin(authorization)
        job = run_collection(session_factory, settings, trigger=trigger, ai=ai_client, **collect_kwargs)
        return job_json(job)

    return app
# Run with: uvicorn app.main:create_app --factory  (importing this module never touches the database)
