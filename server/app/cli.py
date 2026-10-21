"""Command line helpers for local development and one-off jobs.

    python -m app.cli init-db     create tables in DATABASE_URL
    python -m app.cli collect     run one collection now (same code path as POST /admin/collect)
    python -m app.cli status      print the last runs and table counts
"""
import argparse
import json
import sys

from sqlalchemy import func, select

from .collector import LAST_SUCCESS_KEY, get_state, run_collection
from .config import get_settings
from .db import init_db, make_engine, make_session_factory
from .main import job_json
from .models import Article, ExplorePick, JobRun, Story, Summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    parser.add_argument("command", choices=["init-db", "collect", "status"])
    args = parser.parse_args(argv)

    settings = get_settings()
    engine = make_engine(settings.database_url)
    init_db(engine)
    session_factory = make_session_factory(engine)

    if args.command == "init-db":
        print("tables ready")
        return 0

    if args.command == "collect":
        job = run_collection(session_factory, settings, trigger="cli")
        print(json.dumps(job_json(job), indent=2))
        return 0 if job.status in ("success", "partial") else 1

    with session_factory() as session:
        runs = session.scalars(select(JobRun).order_by(JobRun.id.desc()).limit(5)).all()
        print(
            json.dumps(
                {
                    "last_successful_collection_at": get_state(session, LAST_SUCCESS_KEY),
                    "articles": session.scalar(select(func.count(Article.id))),
                    "stories": session.scalar(select(func.count(Story.id))),
                    "summaries": session.scalar(select(func.count(Summary.story_id))),
                    "explore_picks": session.scalar(select(func.count(ExplorePick.id))),
                    "recent_runs": [job_json(r) for r in runs],
                },
                indent=2,
            )
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
