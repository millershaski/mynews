"""Minimal Gemini REST client with per-run budget, rate spacing, daily cap and call logging."""
import json
import threading
import time
from datetime import datetime, timedelta

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import Settings
from .models import AiCallLog
from .timeutil import utcnow

API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
# Errors that mean "this model cannot answer right now" (retired for this key, rate limited, overloaded, server
# fault). The next model in the fallback list is tried. Free-tier rate limits are counted per model, so a 429 on
# one model does not stop the others; only when every model is rate limited is it treated as a quota stop.
# Credential errors (401/403) and the local daily cap are never retried elsewhere.
FALLBACK_STATUSES = {404, 429, 500, 502, 503, 504}
# After such an error the model is skipped for a while, so a busy model does not cost a wasted call per request.
# A retired model (404) will not come back, so it is skipped for much longer.
MODEL_COOLDOWN = {404: timedelta(hours=24), 429: timedelta(minutes=2)}
DAILY_LIMIT_COOLDOWN = timedelta(hours=1)  # 429 whose message names a per-day quota
DEFAULT_MODEL_COOLDOWN = timedelta(minutes=10)
REQUEST_TIMEOUT = 90.0


class AIError(Exception):
    """Any failure to get a valid model response. `quota` is True for quota/auth problems (stop calling)."""

    def __init__(self, message: str, status_code: int | None = None, quota: bool = False):
        super().__init__(message)
        self.status_code = status_code
        self.quota = quota


_spacing_lock = threading.Lock()
_last_call_at = 0.0
_cooldown_lock = threading.Lock()
_model_cooldown_until: dict[str, datetime] = {}


def reset_model_cooldowns() -> None:
    with _cooldown_lock:
        _model_cooldown_until.clear()


def calls_today(session: Session) -> int:
    now = utcnow()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return session.scalar(select(func.count(AiCallLog.id)).where(AiCallLog.started_at >= start)) or 0


def calls_in_last(session: Session, seconds: int) -> int:
    since = utcnow() - timedelta(seconds=seconds)
    return session.scalar(select(func.count(AiCallLog.id)).where(AiCallLog.started_at >= since)) or 0


class GeminiClient:
    def __init__(self, settings: Settings, http: httpx.Client | None = None):
        self.settings = settings
        self.model = settings.gemini_model
        self.cfg = settings.model_config
        self.http = http or httpx.Client(timeout=REQUEST_TIMEOUT)
        fallbacks = [m for m in self.cfg.get("fallback_models", []) if m and m != self.model]
        self.models = [self.model, *dict.fromkeys(fallbacks)]
        self.last_model = self.model  # the model that produced the most recent successful answer

    def _thinking_config(self, model: str) -> dict | None:
        # Gemini 2.5 takes a token budget (0 = off); Gemini 3 takes a level and rejects "minimal" on some models.
        # Aliases such as gemini-flash-latest get the provider default rather than a guess.
        if "2.5" in model and "thinking_budget" in self.cfg:
            return {"thinkingBudget": self.cfg["thinking_budget"]}
        if model.startswith("gemini-3") and self.cfg.get("thinking_level"):
            return {"thinkingLevel": self.cfg["thinking_level"]}
        return None

    @property
    def configured(self) -> bool:
        return bool(self.settings.gemini_api_key)

    def _space_calls(self) -> None:
        global _last_call_at
        with _spacing_lock:
            wait = self.settings.ai_min_interval_seconds - (time.monotonic() - _last_call_at)
            if wait > 0:
                time.sleep(wait)
            _last_call_at = time.monotonic()

    def generate_json(
        self,
        session: Session,
        *,
        purpose: str,
        ref: str,
        system_prompt: str,
        user_text: str,
        schema: dict,
        daily_cap: int,
    ) -> dict:
        if not self.configured:
            raise AIError("GEMINI_API_KEY is not configured")
        errors: list[AIError] = []
        for model in self._models_to_try():
            # Every attempt is a real provider call, so each one counts toward the daily cap and spacing.
            if calls_today(session) >= daily_cap:
                raise AIError("daily AI call budget reached", quota=True)
            try:
                result = self._call(session, model, purpose, ref, system_prompt, user_text, schema)
            except AIError as exc:
                if exc.status_code not in FALLBACK_STATUSES:
                    raise
                with _cooldown_lock:
                    _model_cooldown_until[model] = utcnow() + self._cooldown_for(exc)
                errors.append(exc)
                continue
            with _cooldown_lock:
                _model_cooldown_until.pop(model, None)
            self.last_model = model
            return result
        if all(e.status_code == 429 for e in errors):
            raise AIError("every configured model is rate limited (429)", 429, quota=True)
        # Report a non-quota failure, so callers retry later rather than stopping the run.
        raise next(e for e in reversed(errors) if e.status_code != 429)

    @staticmethod
    def _cooldown_for(exc: AIError) -> timedelta:
        if exc.status_code == 429 and "PerDay" in str(exc):
            return DAILY_LIMIT_COOLDOWN
        return MODEL_COOLDOWN.get(exc.status_code, DEFAULT_MODEL_COOLDOWN)

    def _models_to_try(self) -> list[str]:
        """Models in preference order, minus any cooling down.

        If every model is cooling down, only the one that becomes available soonest is tried, so a provider-wide
        outage costs one call per request instead of one per configured model.
        """
        now = utcnow()
        with _cooldown_lock:
            until = {m: _model_cooldown_until.get(m, now) for m in self.models}
        ready = [m for m in self.models if until[m] <= now]
        return ready or [min(self.models, key=lambda m: until[m])]

    def _call(
        self, session: Session, model: str, purpose: str, ref: str, system_prompt: str, user_text: str, schema: dict
    ) -> dict:
        self._space_calls()
        body = {
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": user_text}]}],
            "generationConfig": {
                "temperature": self.cfg.get("temperature", 0.2),
                "maxOutputTokens": self.cfg.get("max_output_tokens", 2048),
                "responseMimeType": "application/json",
                "responseSchema": schema,
            },
        }
        thinking = self._thinking_config(model)
        if thinking:
            body["generationConfig"]["thinkingConfig"] = thinking

        log = AiCallLog(purpose=purpose, ref=ref[:80], model=model, started_at=utcnow(), ok=False)
        t0 = time.monotonic()
        try:
            resp = self.http.post(
                f"{API_BASE}/{model}:generateContent",
                headers={"x-goog-api-key": self.settings.gemini_api_key, "Content-Type": "application/json"},
                json=body,
                timeout=REQUEST_TIMEOUT,
            )
            log.status_code = resp.status_code
            if resp.status_code == 429:
                # Not marked as quota here: limits are per model, so generate_json decides whether to stop.
                raise AIError(f"rate limited by provider (429): {resp.text[:300]}", 429)
            if resp.status_code in (401, 403):
                raise AIError(f"provider rejected credentials ({resp.status_code})", resp.status_code, quota=True)
            if resp.status_code >= 400:
                raise AIError(f"provider error {resp.status_code}: {resp.text[:200]}", resp.status_code)
            data = resp.json()
            candidates = data.get("candidates") or []
            if not candidates:
                reason = (data.get("promptFeedback") or {}).get("blockReason", "no candidates")
                raise AIError(f"empty response ({reason})")
            cand = candidates[0]
            if cand.get("finishReason") not in (None, "STOP"):
                raise AIError(f"generation stopped: {cand.get('finishReason')}")
            text = "".join(p.get("text", "") for p in (cand.get("content") or {}).get("parts", []))
            try:
                result = json.loads(text)
            except json.JSONDecodeError as exc:
                raise AIError(f"response was not valid JSON: {exc}") from exc
            if not isinstance(result, dict):
                raise AIError("response JSON was not an object")
            log.ok = True
            return result
        except httpx.HTTPError as exc:
            log.error = f"network error: {exc.__class__.__name__}"
            raise AIError(log.error) from exc
        except AIError as exc:
            log.error = str(exc)[:500]
            raise
        finally:
            log.duration_ms = int((time.monotonic() - t0) * 1000)
            session.add(log)
            session.commit()
