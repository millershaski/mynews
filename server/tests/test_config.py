from app.config import get_settings


def test_blank_database_url_falls_back_to_sqlite(monkeypatch):
    # .env.example ships "DATABASE_URL=" with no value; that must mean local SQLite, not an empty URL.
    monkeypatch.setenv("DATABASE_URL", "   ")
    get_settings.cache_clear()
    try:
        assert get_settings().database_url == "sqlite:///./mynews.db"
    finally:
        get_settings.cache_clear()
