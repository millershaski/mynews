"""GeminiClient against a fake HTTP transport: model fallback, thinking config per model family, call logging."""
import json

import httpx
import pytest
from sqlalchemy import select

from app.gemini import AIError, GeminiClient
from app.models import AiCallLog
from conftest import make_settings

SCHEMA = {"type": "OBJECT", "properties": {"answer": {"type": "STRING"}}, "required": ["answer"]}


def ok_response(payload: dict) -> httpx.Response:
    return httpx.Response(
        200, json={"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": json.dumps(payload)}]}}]}
    )


def make_client(statuses: dict[str, int], seen: list):
    """A client whose fake provider answers each model with the given status (200 = valid JSON)."""
    config = dict(make_settings().model_config, fallback_models=["gemini-3.5-flash", "gemini-flash-latest"])
    settings = make_settings(gemini_model="gemini-3.8-flash", model_config=config)

    def handler(request: httpx.Request) -> httpx.Response:
        model = request.url.path.rsplit("/", 1)[-1].split(":")[0]
        seen.append((model, json.loads(request.content)))
        code = statuses.get(model, 200)
        if code == 200:
            return ok_response({"answer": model})
        return httpx.Response(code, json={"error": {"message": "simulated"}})

    return GeminiClient(settings, http=httpx.Client(transport=httpx.MockTransport(handler)))


def call(client, session):
    return client.generate_json(
        session, purpose="test", ref="r", system_prompt="s", user_text="u", schema=SCHEMA, daily_cap=100
    )


def logged(session):
    return [(r.model, r.status_code, r.ok) for r in session.scalars(select(AiCallLog).order_by(AiCallLog.id))]


def test_overloaded_or_retired_model_falls_back_to_next(session_factory):
    seen = []
    client = make_client({"gemini-3.8-flash": 503, "gemini-3.5-flash": 404}, seen)
    with session_factory() as s:
        assert call(client, s) == {"answer": "gemini-flash-latest"}
        assert client.last_model == "gemini-flash-latest"
        # Every attempt is logged under the model that was actually called.
        assert logged(s) == [
            ("gemini-3.8-flash", 503, False),
            ("gemini-3.5-flash", 404, False),
            ("gemini-flash-latest", 200, True),
        ]


def test_primary_model_used_when_it_answers(session_factory):
    seen = []
    client = make_client({}, seen)
    with session_factory() as s:
        call(client, s)
    assert [m for m, _ in seen] == ["gemini-3.8-flash"]
    assert client.last_model == "gemini-3.8-flash"


def test_rate_limited_model_falls_back_to_next(session_factory):
    # Free-tier limits are per model, so one model's 429 must not block the others.
    seen = []
    client = make_client({"gemini-3.8-flash": 429}, seen)
    with session_factory() as s:
        assert call(client, s) == {"answer": "gemini-3.5-flash"}
    assert [m for m, _ in seen] == ["gemini-3.8-flash", "gemini-3.5-flash"]


def test_every_model_rate_limited_is_a_quota_stop(session_factory):
    seen = []
    client = make_client({m: 429 for m in ("gemini-3.8-flash", "gemini-3.5-flash", "gemini-flash-latest")}, seen)
    with session_factory() as s, pytest.raises(AIError) as err:
        call(client, s)
    assert err.value.quota and err.value.status_code == 429
    assert len(seen) == 3


def test_mixed_failures_are_not_a_quota_stop(session_factory):
    seen = []
    client = make_client({"gemini-3.8-flash": 429, "gemini-3.5-flash": 503, "gemini-flash-latest": 429}, seen)
    with session_factory() as s, pytest.raises(AIError) as err:
        call(client, s)
    assert not err.value.quota and err.value.status_code == 503


def test_credential_error_does_not_try_other_models(session_factory):
    seen = []
    client = make_client({"gemini-3.8-flash": 403}, seen)
    with session_factory() as s, pytest.raises(AIError) as err:
        call(client, s)
    assert err.value.quota
    assert [m for m, _ in seen] == ["gemini-3.8-flash"]


def test_daily_cap_counts_every_attempt(session_factory):
    seen = []
    client = make_client({"gemini-3.8-flash": 503}, seen)
    with session_factory() as s, pytest.raises(AIError) as err:
        client.generate_json(s, purpose="t", ref="r", system_prompt="s", user_text="u", schema=SCHEMA, daily_cap=1)
    assert err.value.quota
    assert [m for m, _ in seen] == ["gemini-3.8-flash"]


def test_all_models_failing_raises_last_error(session_factory):
    seen = []
    client = make_client({"gemini-3.8-flash": 503, "gemini-3.5-flash": 503, "gemini-flash-latest": 500}, seen)
    with session_factory() as s, pytest.raises(AIError) as err:
        call(client, s)
    assert err.value.status_code == 500 and not err.value.quota
    assert len(seen) == 3


def test_failed_model_is_skipped_on_the_next_request(session_factory):
    seen = []
    client = make_client({"gemini-3.8-flash": 503}, seen)
    with session_factory() as s:
        call(client, s)
        call(client, s)
    # The busy primary costs one call, not one per request.
    assert [m for m, _ in seen] == ["gemini-3.8-flash", "gemini-3.5-flash", "gemini-3.5-flash"]


def test_when_every_model_is_cooling_down_only_one_is_tried(session_factory):
    seen = []
    client = make_client({m: 503 for m in ("gemini-3.8-flash", "gemini-3.5-flash", "gemini-flash-latest")}, seen)
    with session_factory() as s:
        with pytest.raises(AIError):
            call(client, s)
        seen.clear()
        with pytest.raises(AIError):
            call(client, s)
    # An outage costs one call per request, not one per configured model; the soonest-available model goes first.
    assert [m for m, _ in seen] == ["gemini-3.8-flash"]


def test_thinking_config_matches_model_family(session_factory):
    seen = []
    client = make_client({"gemini-3.8-flash": 503, "gemini-3.5-flash": 503}, seen)
    with session_factory() as s:
        call(client, s)
    configs = {m: body["generationConfig"].get("thinkingConfig") for m, body in seen}
    assert configs["gemini-3.8-flash"] == {"thinkingLevel": "low"}
    assert configs["gemini-flash-latest"] is None  # alias: unknown family, provider default
    assert client._thinking_config("gemini-2.5-flash-lite") == {"thinkingBudget": 0}
