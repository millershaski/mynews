"""Runtime configuration, read from environment variables (see .env.example)."""
import json
import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parents[1]
PROMPTS_DIR = SERVER_DIR / "prompts"
DATA_DIR = Path(__file__).resolve().parent / "data"


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    return int(raw) if raw else default


def _float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    return float(raw) if raw else default


def load_model_config() -> dict:
    return json.loads((PROMPTS_DIR / "model_config.json").read_text(encoding="utf-8"))


@dataclass
class Settings:
    database_url: str
    admin_token: str
    gemini_api_key: str
    gemini_model: str
    guardian_api_key: str
    retention_hours: int
    max_ai_calls_per_run: int
    ai_min_interval_seconds: float
    ai_daily_limit: int
    explore_ai_reserve: int
    max_background_lookups_per_run: int
    user_agent: str
    model_config: dict = field(default_factory=dict)
    # Wikimedia's User-Agent policy requires a URL or email to contact the operator; requests without one get 403.
    wiki_contact: str = ""

    @property
    def wiki_user_agent(self) -> str:
        return f"MyNewsCourseProject/1.0 ({self.wiki_contact}; CS 403 course project) python-httpx"


def load_dotenv(path: Path = SERVER_DIR / ".env") -> None:
    """Read KEY=value lines from server/.env for local development. Real environment variables win."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key, value)


@lru_cache
def get_settings() -> Settings:
    load_dotenv()
    model_config = load_model_config()
    return Settings(
        # A blank DATABASE_URL (as in .env.example) means "use local SQLite", not an empty URL.
        database_url=os.environ.get("DATABASE_URL", "").strip() or "sqlite:///./mynews.db",
        admin_token=os.environ.get("ADMIN_TOKEN", ""),
        gemini_api_key=os.environ.get("GEMINI_API_KEY", ""),
        gemini_model=os.environ.get("GEMINI_MODEL", "").strip() or model_config["model"],
        guardian_api_key=os.environ.get("GUARDIAN_API_KEY", ""),
        retention_hours=_int("RETENTION_HOURS", 168),
        max_ai_calls_per_run=_int("MAX_AI_CALLS_PER_RUN", 6),
        ai_min_interval_seconds=_float("AI_MIN_INTERVAL_SECONDS", 7.0),
        ai_daily_limit=_int("AI_DAILY_LIMIT", 200),
        explore_ai_reserve=_int("EXPLORE_AI_RESERVE", 60),
        max_background_lookups_per_run=_int("MAX_BACKGROUND_LOOKUPS_PER_RUN", 30),
        user_agent=os.environ.get(
            "HTTP_USER_AGENT",
            "MyNewsCourseProject/1.0 (student project; RSS reader) Mozilla/5.0",
        ),
        model_config=model_config,
        # Render sets RENDER_EXTERNAL_URL automatically, so deployments have a contact URL without extra setup.
        wiki_contact=(os.environ.get("WIKI_CONTACT") or os.environ.get("RENDER_EXTERNAL_URL") or "").strip(),
    )
