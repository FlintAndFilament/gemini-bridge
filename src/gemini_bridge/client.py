"""
gemini_bridge/client.py
------------------------
Gemini chat session manager and unified ask() interface.

Responsibilities:
  - Build the google-genai Client from credentials and config
  - Create and cache named sessions (one per tool+session+model triple); the bridge owns each
    session's conversation history rather than delegating it to the SDK's chat object
  - Translate named thinking levels to model-appropriate API parameters
  - Expose async ask() as the single interface all tools use

Design notes:
  - Single Responsibility: session lifecycle + inference only; credentials come in ready-made
  - Open/Closed: new session names need no code changes — sessions are created on demand
  - Interface Segregation: tools receive only GeminiClient; they cannot access config or credentials
  - Dependency Inversion: client depends on google.auth.credentials.Credentials abstraction
  - History is committed only when a call succeeds, so a failed call never leaves a session
    holding a half-finished exchange

Raises:
  ClientError — wraps inference and session failures with context for Claude to surface

Used by:  tools/*.py (via ask()), server.py (instantiates GeminiClient at startup)
Imports:  config.py (Config, ThinkingLevel), auth.py (build_credentials)
"""

import asyncio
import logging
import random
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Optional

_log = logging.getLogger(__name__)

import google.auth.credentials
from google import genai
from google.genai import types
from google.genai.types import GenerateContentConfig, ThinkingConfig
from google.genai.types import ThinkingLevel as SDKThinkingLevel

from gemini_bridge.config import Config, ModelFamily, ThinkingLevel
from gemini_bridge.errors import ClientError
from gemini_bridge.tool_loop import ToolCallRecord, ToolRegistry, run_tool_loop

__all__ = ["ClientError", "GeminiClient", "Session", "DEFAULT_MODEL", "FALLBACK_MODEL"]

# Default model used when a tool call does not specify one (unless overridden by config).
# gemini-3.5-flash is GA on both the Developer API and Vertex AI — the frontier Flash model.
DEFAULT_MODEL = "gemini-3.5-flash"
# Fallback model used when the requested model returns a terminal 503/429. gemini-3.1-flash-lite
# is GA on both backends (retires 2027+), the cheapest/most-available Gemini 3 model, and has a
# Developer-API free tier — an ideal safety net, and distinct from the default so it substitutes.
FALLBACK_MODEL = "gemini-3.1-flash-lite"

# Thinking budget token counts for Gemini 2.x models
_THINKING_BUDGET_2X: dict[str, int] = {
    "none": 0,
    "low": 1024,
    "medium": 8192,
    "high": 32768,
}
# gemini-2.x Pro models enforce a minimum thinking budget of 128; budget=0 is rejected
_THINKING_BUDGET_2X_PRO_MIN = 128

# Thinking level enum values for Gemini 3.x models
_THINKING_LEVEL_3X: dict[str, SDKThinkingLevel] = {
    "none": SDKThinkingLevel.MINIMAL,
    "low": SDKThinkingLevel.LOW,
    "medium": SDKThinkingLevel.MEDIUM,
    "high": SDKThinkingLevel.HIGH,
}

_MAX_SESSIONS = 50  # LRU cap; oldest session evicted when exceeded
_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 1.0  # seconds; doubles each attempt plus jitter


def _is_retryable(exc: Exception) -> bool:
    msg = str(exc).upper()
    return any(token in msg for token in ("503", "429", "UNAVAILABLE", "RESOURCE_EXHAUSTED"))


def _warn_model_backend_mismatch(model: str, is_vertex: bool) -> None:
    """Log a warning when the model name looks mismatched with the active backend.

    Developer API (api_key): unversioned names and '-latest' aliases. Preview models
               (e.g. 'gemini-3.1-pro-preview') may not be available and can 404.
    Vertex AI: versioned IDs or stable names; '-latest' aliases are a Developer-API
               convention and will 404 on Vertex.
    """
    if not is_vertex and "preview" in model:
        _log.warning(
            "model %r is a preview model — availability via Google AI Studio API keys "
            "varies. If you get 404/503, try a GA model like 'gemini-3.5-flash' or "
            "'gemini-3.1-flash-lite'.",
            model,
        )
    elif is_vertex and "-latest" in model:
        _log.warning(
            "model %r uses a '-latest' alias, which is a Developer API convention. "
            "Vertex AI uses versioned IDs (e.g. 'gemini-2.0-flash-001'). "
            "This may 404 on the Vertex endpoint.",
            model,
        )


def _model_family(model: str) -> ModelFamily:
    """Resolve model string to ModelFamily. Raises ClientError for unrecognized names.

    Generation is detected by the 'gemini-2' / 'gemini-3' prefix in either form — dotted
    (gemini-3.5-flash, gemini-3.1-pro-preview) or hyphenated (gemini-3-pro-preview,
    gemini-3-flash-preview). Both are valid, usable models; the hyphenated previews must
    resolve to the right thinking-config family rather than being rejected.

    '-latest' aliases (gemini-flash-latest, gemini-pro-latest, etc.) are accepted and
    treated as GEMINI_2. A debug log notes the assumption so it's visible if behavior changes.
    """
    if model.startswith("gemini-2"):
        return ModelFamily.GEMINI_2
    if model.startswith("gemini-3"):
        return ModelFamily.GEMINI_3
    if model.endswith("-latest") or "-latest-" in model:
        _log.debug(
            "model %r uses a '-latest' alias; assuming GEMINI_2 thinking-config. "
            "If thinking-config errors occur, specify a versioned model instead.",
            model,
        )
        return ModelFamily.GEMINI_2
    raise ClientError(
        f"Unrecognized model family: {model!r}. "
        "Expected a 'gemini-2*' / 'gemini-3*' model or a '-latest' alias."
    )


@dataclass
class Session:
    """One named conversation: its model and the committed history (prompts + final answers)."""

    model: str
    history: list[types.Content] = field(default_factory=list)


class GeminiClient:
    """Manages persistent Gemini chat sessions and provides a unified ask() interface."""

    def __init__(
        self,
        config: Config,
        credentials: Optional[google.auth.credentials.Credentials] = None,
        api_key: Optional[str] = None,
    ) -> None:
        self._config = config
        if api_key:
            self._raw_client = genai.Client(api_key=api_key)
        else:
            self._raw_client = genai.Client(
                vertexai=True,
                project=config.project,
                location=config.location,
                credentials=credentials,
            )
        self._is_vertex = api_key is None
        # Keyed by "{name}:{model}" — sessions are model-specific
        self._sessions: OrderedDict[str, Session] = OrderedDict()

    def get_or_create_session(
        self,
        name: str = "default",
        model: Optional[str] = None,
    ) -> Session:
        """Return existing session or create a new one (LRU-capped at _MAX_SESSIONS).

        Sessions are keyed by (name, model) — changing the model creates a new session.
        Creating a session makes no API call.
        """
        effective_model = model or self.default_model
        _warn_model_backend_mismatch(effective_model, self._is_vertex)
        cache_key = f"{name}:{effective_model}"
        if cache_key in self._sessions:
            self._sessions.move_to_end(cache_key)
            return self._sessions[cache_key]
        session = Session(model=effective_model)
        self._sessions[cache_key] = session
        if len(self._sessions) > _MAX_SESSIONS:
            evicted, _ = self._sessions.popitem(last=False)
            _log.debug("session cache evicted (LRU): %s", evicted)
        return session

    @property
    def default_thinking(self) -> ThinkingLevel:
        return self._config.default_thinking

    @property
    def default_model(self) -> str:
        """Effective default model for calls that omit `model`: the config's `default_model`
        override if set, else the built-in DEFAULT_MODEL."""
        return self._config.default_model or DEFAULT_MODEL

    @property
    def auth_method(self) -> str:
        """The configured auth method (e.g. 'api_key', 'adc'). Public accessor so tools
        need not reach into ._config; models.backend_for() maps it to a backend."""
        return self._config.auth.method

    def list_models(self) -> list[Any]:
        """Return the backend's model catalog (raw google-genai Model objects).

        Thin pass-through so tools never touch ._raw_client. Raises ClientError on failure
        so callers can degrade gracefully (e.g. fall back to a static shortlist).
        """
        try:
            return list(self._raw_client.models.list())
        except Exception as exc:
            _log.error("models.list failed: %s", exc)
            raise ClientError(f"Failed to list models: {exc}") from exc

    def build_config(
        self,
        thinking: ThinkingLevel,
        system_instruction: Optional[str] = None,
        model: Optional[str] = None,
        declarations: Optional[list[types.FunctionDeclaration]] = None,
        allow_tools: bool = True,
    ) -> GenerateContentConfig:
        """Build the request config: thinking for the model family, system instruction, and —
        when declarations are given — the tool set. SDK automatic function calling is always
        disabled (the bridge runs its own loop). allow_tools=False keeps the declarations but
        forbids calls."""
        effective_model = model or self.default_model
        si: dict[str, Any] = {
            "automatic_function_calling": types.AutomaticFunctionCallingConfig(disable=True)
        }
        if system_instruction:
            si["system_instruction"] = system_instruction
        if declarations:
            si["tools"] = [types.Tool(function_declarations=declarations)]
            if not allow_tools:
                si["tool_config"] = types.ToolConfig(
                    function_calling_config=types.FunctionCallingConfig(
                        mode=types.FunctionCallingConfigMode.NONE
                    )
                )
        family = _model_family(effective_model)
        if family == ModelFamily.GEMINI_2:
            budget = _THINKING_BUDGET_2X[thinking]
            if "pro" in effective_model and budget < _THINKING_BUDGET_2X_PRO_MIN:
                _log.debug(
                    "thinking=none clamped to %d for Pro model %r",
                    _THINKING_BUDGET_2X_PRO_MIN,
                    effective_model,
                )
                budget = _THINKING_BUDGET_2X_PRO_MIN
            return GenerateContentConfig(
                thinking_config=ThinkingConfig(thinking_budget=budget),
                **si,
            )
        if family == ModelFamily.GEMINI_3:
            return GenerateContentConfig(
                thinking_config=ThinkingConfig(thinking_level=_THINKING_LEVEL_3X[thinking]),
                **si,
            )
        raise ClientError(
            f"Unrecognized model family: {effective_model!r}. "
            "Expected 'gemini-2.*' or 'gemini-3.*'. Check your model name."
        )

    # Kept for callers/tests written against the original name.
    _build_generation_config = build_config

    async def generate(
        self,
        model: str,
        contents: list[types.Content],
        config: GenerateContentConfig,
    ) -> types.GenerateContentResponse:
        """One generate_content request with retry/backoff on 503/429. Raises ClientError."""
        for attempt in range(1, _MAX_RETRIES + 2):
            try:
                return await self._raw_client.aio.models.generate_content(
                    model=model, contents=contents, config=config
                )
            except Exception as exc:
                is_last = attempt > _MAX_RETRIES
                if not is_last and _is_retryable(exc):
                    delay = _RETRY_BASE_DELAY * (2 ** (attempt - 1)) + random.uniform(0, 0.5)
                    _log.warning(
                        "inference attempt %d/%d failed (retryable) — retrying in %.1fs: %s",
                        attempt,
                        _MAX_RETRIES + 1,
                        delay,
                        exc,
                    )
                    await asyncio.sleep(delay)
                    continue
                suffix = f" after {attempt} attempt(s)" if attempt > 1 else ""
                _log.error("inference failed%s: %s", suffix, exc)
                hint = ""
                if _is_retryable(exc):
                    hint = (
                        " The model appears overloaded or quota-limited. "
                        "Try again shortly, or pass a different model "
                        "(e.g. model='gemini-3.1-flash-lite') to avoid the busy endpoint."
                    )
                raise ClientError(f"Gemini inference failed{suffix}: {exc}{hint}") from exc
        raise AssertionError("unreachable")  # pragma: no cover

    async def ask(
        self,
        session: Session,
        prompt: str,
        thinking: Optional[ThinkingLevel] = None,
        system_instruction: Optional[str] = None,
        registry: Optional[ToolRegistry] = None,
        records: Optional[list[ToolCallRecord]] = None,
    ) -> str:
        """Send prompt in the session's context and return the answer text.

        With a non-empty `registry`, Gemini may call its tools; each executed call is appended
        to `records` as it happens. Only [prompt, final answer] is committed to
        session.history, and only on success. Raises ClientError.
        """
        effective_thinking: ThinkingLevel = thinking or self._config.default_thinking
        declarations = registry.declarations if registry else None
        _log.debug(
            "ask: model=%s thinking=%s prompt_len=%d tools=%d",
            session.model,
            effective_thinking,
            len(prompt),
            len(declarations or []),
        )

        async def generate(
            contents: list[types.Content], allow_tools: bool
        ) -> types.GenerateContentResponse:
            config = self.build_config(
                effective_thinking, system_instruction, session.model, declarations, allow_tools
            )
            return await self.generate(session.model, contents, config)

        user_content = types.Content(role="user", parts=[types.Part.from_text(text=prompt)])
        result = await run_tool_loop(
            generate,
            [*session.history, user_content],
            registry or ToolRegistry(),
            records if records is not None else [],
        )
        session.history.extend([user_content, result.content])
        _log.debug("response_len=%d", len(result.text))
        return result.text
