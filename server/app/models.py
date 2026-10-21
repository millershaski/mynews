"""Data model. See README "Data model" for a diagram."""
import json
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base
from .timeutil import as_utc


class UTCDateTime(TypeDecorator):
    """Timezone-aware UTC datetimes on both Postgres and SQLite."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return as_utc(value)

    def process_result_value(self, value, dialect):
        return as_utc(value)


class JSONText(TypeDecorator):
    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return None if value is None else json.dumps(value)

    def process_result_value(self, value, dialect):
        return None if value is None else json.loads(value)


class Story(Base):
    __tablename__ = "stories"

    id: Mapped[str] = mapped_column(String(24), primary_key=True)
    headline: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime)  # last time membership or content changed
    latest_article_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    input_hash: Mapped[str] = mapped_column(String(64), default="")
    ai_status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | ok | failed
    ai_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_last_attempt_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    background_status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | found | none | failed
    background_input_hash: Mapped[str] = mapped_column(String(64), default="")

    articles: Mapped[list["Article"]] = relationship(back_populates="story")
    summary: Mapped["Summary | None"] = relationship(
        back_populates="story", cascade="all, delete-orphan", passive_deletes=True, uselist=False
    )
    background_links: Mapped[list["StoryBackground"]] = relationship(
        back_populates="story", cascade="all, delete-orphan", passive_deletes=True
    )


class Article(Base):
    __tablename__ = "articles"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)  # sha1(normalized_url)
    normalized_url: Mapped[str] = mapped_column(Text, unique=True)
    url: Mapped[str] = mapped_column(Text)
    guid: Mapped[str | None] = mapped_column(Text, nullable=True)
    publisher_id: Mapped[str] = mapped_column(String(40), index=True)
    publisher_name: Mapped[str] = mapped_column(String(80))
    title: Mapped[str] = mapped_column(Text)
    excerpt: Mapped[str] = mapped_column(Text, default="")
    published_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    date_estimated: Mapped[bool] = mapped_column(Boolean, default=False)
    first_seen_at: Mapped[datetime] = mapped_column(UTCDateTime)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime)
    content_hash: Mapped[str] = mapped_column(String(64))
    story_id: Mapped[str | None] = mapped_column(
        ForeignKey("stories.id", ondelete="SET NULL"), nullable=True, index=True
    )

    story: Mapped[Story | None] = relationship(back_populates="articles")


class Summary(Base):
    """AI analysis of one story. One row per story; replaced when the inputs change."""

    __tablename__ = "summaries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    story_id: Mapped[str] = mapped_column(ForeignKey("stories.id", ondelete="CASCADE"), unique=True)
    input_hash: Mapped[str] = mapped_column(String(64))
    article_ids: Mapped[list] = mapped_column(JSONText)
    overview: Mapped[str] = mapped_column(Text)
    agreement: Mapped[str] = mapped_column(Text)
    disagreement_or_uncertainty: Mapped[str] = mapped_column(Text)
    overview_sources: Mapped[list] = mapped_column(JSONText)
    perspectives_status: Mapped[str] = mapped_column(String(30))  # supported | insufficient_evidence
    perspectives: Mapped[list] = mapped_column(JSONText)
    model: Mapped[str] = mapped_column(String(80))
    prompt_version: Mapped[str] = mapped_column(String(40))
    generated_at: Mapped[datetime] = mapped_column(UTCDateTime)

    story: Mapped[Story] = relationship(back_populates="summary")


class BackgroundResource(Base):
    __tablename__ = "background_resources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    url: Mapped[str] = mapped_column(Text, unique=True)
    title: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(40), default="Wikipedia")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)


class StoryBackground(Base):
    __tablename__ = "story_background"

    story_id: Mapped[str] = mapped_column(ForeignKey("stories.id", ondelete="CASCADE"), primary_key=True)
    resource_id: Mapped[int] = mapped_column(
        ForeignKey("background_resources.id", ondelete="CASCADE"), primary_key=True
    )
    entity: Mapped[str] = mapped_column(Text)

    story: Mapped[Story] = relationship(back_populates="background_links")
    resource: Mapped[BackgroundResource] = relationship()


class FeedStatus(Base):
    __tablename__ = "feed_status"

    publisher_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    url: Mapped[str] = mapped_column(Text)
    last_attempt_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_item_count: Mapped[int] = mapped_column(Integer, default=0)


class JobRun(Base):
    __tablename__ = "job_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trigger: Mapped[str] = mapped_column(String(30))  # schedule | manual | cli | test
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(20))  # running | success | partial | failed | skipped
    feeds_ok: Mapped[int] = mapped_column(Integer, default=0)
    feeds_failed: Mapped[int] = mapped_column(Integer, default=0)
    articles_fetched: Mapped[int] = mapped_column(Integer, default=0)
    articles_new: Mapped[int] = mapped_column(Integer, default=0)
    articles_updated: Mapped[int] = mapped_column(Integer, default=0)
    articles_expired: Mapped[int] = mapped_column(Integer, default=0)
    articles_active: Mapped[int] = mapped_column(Integer, default=0)
    stories_created: Mapped[int] = mapped_column(Integer, default=0)
    stories_deleted: Mapped[int] = mapped_column(Integer, default=0)
    stories_active: Mapped[int] = mapped_column(Integer, default=0)
    summaries_generated: Mapped[int] = mapped_column(Integer, default=0)
    summaries_invalidated: Mapped[int] = mapped_column(Integer, default=0)
    ai_failures: Mapped[int] = mapped_column(Integer, default=0)
    details: Mapped[dict | None] = mapped_column(JSONText, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class AppState(Base):
    __tablename__ = "app_state"

    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


class AiCallLog(Base):
    """One row per model call (no prompt or response bodies). Used for quota tracking and evidence."""

    __tablename__ = "ai_call_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    purpose: Mapped[str] = mapped_column(String(30))  # story_summary | explore_pick
    ref: Mapped[str] = mapped_column(String(80))
    model: Mapped[str] = mapped_column(String(80))
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    ok: Mapped[bool] = mapped_column(Boolean, default=False)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class ExplorePick(Base):
    """The one archive story shown to every user for a (country, year, month, category) combination."""

    __tablename__ = "explore_picks"
    __table_args__ = (UniqueConstraint("country", "year", "month", "category", name="uq_explore_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    country: Mapped[str] = mapped_column(String(2))
    year: Mapped[int] = mapped_column(Integer)
    month: Mapped[int] = mapped_column(Integer)
    category: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20))  # ok | no_match | no_candidates
    source_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    headline: Mapped[str | None] = mapped_column(Text, nullable=True)
    trail_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    thumbnail: Mapped[str | None] = mapped_column(Text, nullable=True)
    section: Mapped[str | None] = mapped_column(Text, nullable=True)
    publisher: Mapped[str | None] = mapped_column(String(80), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    ai_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_intro: Mapped[str | None] = mapped_column(Text, nullable=True)
    candidates_count: Mapped[int] = mapped_column(Integer, default=0)
    query: Mapped[str | None] = mapped_column(Text, nullable=True)
    sources: Mapped[list | None] = mapped_column(JSONText, nullable=True)  # per-source status of the archive search
    model: Mapped[str | None] = mapped_column(String(80), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    generated_at: Mapped[datetime] = mapped_column(UTCDateTime)
    # Set for the current month, and for past months when an archive source failed (so the pick is retried).
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
