"""All timestamps are stored and returned in UTC."""
from datetime import datetime, timezone


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def iso(dt: datetime | None) -> str | None:
    dt = as_utc(dt)
    if dt is None:
        return None
    return dt.isoformat(timespec="seconds").replace("+00:00", "Z")
