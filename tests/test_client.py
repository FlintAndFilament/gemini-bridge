"""Tests for gemini_bridge/client.py — GeminiClient session management and ask()."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from google.genai import types

from gemini_bridge.client import (
    _MAX_SESSIONS,
    DEFAULT_MODEL,
    ClientError,
    GeminiClient,
    Session,
)
from gemini_bridge.config import Config


def _make_client() -> GeminiClient:
    config = Config(project="test-project")
    mock_creds = MagicMock()
    with patch("google.genai.Client"):
        return GeminiClient(config, mock_creds)


def _text_response(text: str, finish: str = "STOP") -> types.GenerateContentResponse:
    parts = [types.Part.from_text(text=text)] if text else []
    return types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                content=types.Content(role="model", parts=parts),
                finish_reason=types.FinishReason(finish),
            )
        ]
    )


def _mock_generate(client: GeminiClient, **kwargs: object) -> AsyncMock:
    mock = AsyncMock(**kwargs)
    client._raw_client.aio.models.generate_content = mock
    return mock


class TestSessionManagement:
    def test_get_or_create_session_creates_new(self) -> None:
        client = _make_client()
        session = client.get_or_create_session("default")
        assert isinstance(session, Session)
        assert session.model == DEFAULT_MODEL
        assert session.history == []

    def test_get_or_create_session_returns_existing(self) -> None:
        client = _make_client()
        s1 = client.get_or_create_session("default")
        s2 = client.get_or_create_session("default")
        assert s1 is s2

    def test_different_session_names_create_separate_sessions(self) -> None:
        client = _make_client()
        sa = client.get_or_create_session("ask:default")
        sb = client.get_or_create_session("brainstorm:default")
        assert sa is not sb

    def test_different_models_create_separate_sessions(self) -> None:
        client = _make_client()
        sa = client.get_or_create_session("ask:default", model="gemini-2.5-flash")
        sb = client.get_or_create_session("ask:default", model="gemini-2.5-pro")
        assert sa is not sb
        assert sb.model == "gemini-2.5-pro"

    def test_session_creation_makes_no_api_call(self) -> None:
        client = _make_client()
        gen = _mock_generate(client)
        client.get_or_create_session("default")
        gen.assert_not_awaited()

    def test_session_cache_evicts_oldest_when_full(self) -> None:
        client = _make_client()
        for i in range(_MAX_SESSIONS + 1):
            client.get_or_create_session(f"session:{i}")

        assert len(client._sessions) == _MAX_SESSIONS
        assert f"session:0:{DEFAULT_MODEL}" not in client._sessions
        assert f"session:{_MAX_SESSIONS}:{DEFAULT_MODEL}" in client._sessions


class TestThinkingConfig:
    def test_gemini2_thinking_none_maps_to_budget_zero(self) -> None:
        client = _make_client()
        config = client._build_generation_config("none", model="gemini-2.5-flash")
        assert config.thinking_config.thinking_budget == 0  # type: ignore[union-attr]

    def test_gemini2_thinking_high_maps_to_budget_32768(self) -> None:
        client = _make_client()
        config = client._build_generation_config("high", model="gemini-2.5-pro")
        assert config.thinking_config.thinking_budget == 32768  # type: ignore[union-attr]

    def test_gemini3_thinking_maps_to_level_enum(self) -> None:
        from google.genai.types import ThinkingLevel as SDKThinkingLevel

        client = _make_client()
        config = client._build_generation_config("medium", model="gemini-3.5-flash")
        assert config.thinking_config.thinking_level == SDKThinkingLevel.MEDIUM  # type: ignore[union-attr]

    def test_unknown_model_family_raises_client_error(self) -> None:
        client = _make_client()
        with pytest.raises(ClientError, match="Unrecognized model family"):
            client._build_generation_config("low", model="gpt-4-bad")

    def test_gemini3_hyphen_preview_maps_to_level_enum(self) -> None:
        # gemini-3-pro-preview (hyphen, no dot) is a valid Gemini 3 model and must use the
        # 3.x thinking_level path — previously it raised "Unrecognized model family".
        from google.genai.types import ThinkingLevel as SDKThinkingLevel

        client = _make_client()
        for model in ("gemini-3-pro-preview", "gemini-3-flash-preview"):
            cfg = client._build_generation_config("medium", model=model)
            assert cfg.thinking_config.thinking_level == SDKThinkingLevel.MEDIUM, model  # type: ignore[union-attr]

    def test_gemini2_hyphen_form_maps_to_budget(self) -> None:
        client = _make_client()
        cfg = client._build_generation_config("low", model="gemini-2-flash-exp")
        assert cfg.thinking_config.thinking_budget == 1024  # type: ignore[union-attr]

    def test_latest_alias_treated_as_gemini2(self) -> None:
        client = _make_client()
        cfg = client._build_generation_config("low", model="gemini-flash-latest")
        # Should use thinking_budget (GEMINI_2 path), not thinking_level
        assert cfg.thinking_config.thinking_budget == 1024  # type: ignore[union-attr]

    def test_gemini2_pro_thinking_none_clamped_to_minimum(self) -> None:
        client = _make_client()
        config = client._build_generation_config("none", model="gemini-2.5-pro")
        assert config.thinking_config.thinking_budget == 128  # type: ignore[union-attr]

    def test_gemini2_flash_thinking_none_stays_zero(self) -> None:
        client = _make_client()
        config = client._build_generation_config("none", model="gemini-2.5-flash")
        assert config.thinking_config.thinking_budget == 0  # type: ignore[union-attr]


class TestAsk:
    async def test_ask_returns_response_text(self) -> None:
        client = _make_client()
        _mock_generate(client, return_value=_text_response("Gemini response"))
        session = client.get_or_create_session()

        result = await client.ask(session, "Hello", "low")
        assert result == "Gemini response"

    async def test_ask_raises_client_error_on_empty_response(self) -> None:
        client = _make_client()
        _mock_generate(client, return_value=_text_response("", finish="SAFETY"))
        session = client.get_or_create_session()

        with pytest.raises(ClientError, match="finish_reason=SAFETY"):
            await client.ask(session, "Hello", "low")

    async def test_ask_empty_response_unknown_finish_reason(self) -> None:
        client = _make_client()
        _mock_generate(client, return_value=types.GenerateContentResponse(candidates=[]))
        session = client.get_or_create_session()

        with pytest.raises(ClientError, match="finish_reason=UNKNOWN"):
            await client.ask(session, "Hello", "low")

    async def test_ask_raises_client_error_on_exception(self) -> None:
        client = _make_client()
        _mock_generate(client, side_effect=RuntimeError("API error"))
        session = client.get_or_create_session()

        with pytest.raises(ClientError, match="inference failed"):
            await client.ask(session, "Hello", "medium")

    async def test_ask_retries_on_503_and_eventually_succeeds(self) -> None:
        client = _make_client()
        gen = _mock_generate(
            client, side_effect=[RuntimeError("503 UNAVAILABLE"), _text_response("ok")]
        )
        session = client.get_or_create_session()
        with patch("gemini_bridge.client.asyncio.sleep", new=AsyncMock()):
            result = await client.ask(session, "Hello", "low")
        assert result == "ok"
        assert gen.await_count == 2

    async def test_ask_raises_after_all_retries_exhausted(self) -> None:
        client = _make_client()
        _mock_generate(client, side_effect=RuntimeError("503 UNAVAILABLE"))
        session = client.get_or_create_session()
        with patch("gemini_bridge.client.asyncio.sleep", new=AsyncMock()):
            with pytest.raises(ClientError, match="after 4 attempt"):
                await client.ask(session, "Hello", "low")

    async def test_ask_does_not_retry_non_retryable_error(self) -> None:
        client = _make_client()
        gen = _mock_generate(client, side_effect=RuntimeError("400 INVALID_ARGUMENT"))
        session = client.get_or_create_session()
        with patch("gemini_bridge.client.asyncio.sleep", new=AsyncMock()) as mock_sleep:
            with pytest.raises(ClientError):
                await client.ask(session, "Hello", "low")
        mock_sleep.assert_not_awaited()
        assert gen.await_count == 1

    async def test_ask_uses_config_default_thinking_when_none(self) -> None:
        client = _make_client()
        gen = _mock_generate(client, return_value=_text_response("ok"))
        session = client.get_or_create_session()

        await client.ask(session, "Hello", None)
        gen.assert_awaited_once()
        assert gen.call_args.kwargs["config"].thinking_config is not None

    async def test_ask_passes_system_instruction_in_gen_config(self) -> None:
        client = _make_client()
        gen = _mock_generate(client, return_value=_text_response("ok"))
        session = client.get_or_create_session()

        await client.ask(session, "Hello", "low", system_instruction="You are a critic.")
        assert gen.call_args.kwargs["config"].system_instruction == "You are a critic."

    async def test_ask_uses_session_model(self) -> None:
        client = _make_client()
        gen = _mock_generate(client, return_value=_text_response("ok"))
        session = client.get_or_create_session(model="gemini-2.5-pro")

        await client.ask(session, "Hello", "low")
        assert gen.call_args.kwargs["model"] == "gemini-2.5-pro"

    async def test_ask_success_appends_prompt_and_answer_to_history(self) -> None:
        client = _make_client()
        _mock_generate(client, return_value=_text_response("answer"))
        session = client.get_or_create_session()

        await client.ask(session, "question", "low")
        assert [c.role for c in session.history] == ["user", "model"]
        assert session.history[0].parts[0].text == "question"  # type: ignore[index]
        assert session.history[1].parts[0].text == "answer"  # type: ignore[index]

    async def test_ask_sends_prior_history(self) -> None:
        client = _make_client()
        gen = _mock_generate(client, return_value=_text_response("a"))
        session = client.get_or_create_session()

        await client.ask(session, "first", "low")
        await client.ask(session, "second", "low")
        sent = gen.call_args.kwargs["contents"]
        assert [c.parts[0].text for c in sent] == ["first", "a", "second"]

    async def test_ask_failure_leaves_history_unchanged(self) -> None:
        client = _make_client()
        _mock_generate(client, return_value=_text_response("a"))
        session = client.get_or_create_session()
        await client.ask(session, "first", "low")
        before = list(session.history)

        _mock_generate(client, side_effect=RuntimeError("400 INVALID_ARGUMENT"))
        with pytest.raises(ClientError):
            await client.ask(session, "second", "low")
        assert session.history == before

    def test_default_thinking_property(self) -> None:
        client = _make_client()
        assert client.default_thinking == "medium"

    def test_build_generation_config_includes_system_instruction(self) -> None:
        client = _make_client()
        cfg = client._build_generation_config("low", system_instruction="Be concise.")
        assert cfg.system_instruction == "Be concise."

    def test_build_generation_config_no_system_instruction(self) -> None:
        client = _make_client()
        cfg = client._build_generation_config("low")
        assert cfg.system_instruction is None


class TestDefaultModel:
    def _client_with_default(self, default_model=None) -> GeminiClient:
        config = Config(project="test-project", default_model=default_model)
        with patch("google.genai.Client"):
            return GeminiClient(config, MagicMock())

    def test_falls_back_to_builtin_when_unset(self) -> None:
        from gemini_bridge.client import DEFAULT_MODEL

        assert self._client_with_default().default_model == DEFAULT_MODEL

    def test_fallback_model_is_flash_lite_and_differs_from_default(self) -> None:
        # #58: FALLBACK_MODEL must be a long-lived GA model distinct from the default so the
        # 503/429 safety net actually substitutes something.
        from gemini_bridge.client import DEFAULT_MODEL, FALLBACK_MODEL

        assert FALLBACK_MODEL == "gemini-3.1-flash-lite"
        assert FALLBACK_MODEL != DEFAULT_MODEL

    def test_uses_config_override_when_set(self) -> None:
        client = self._client_with_default("gemini-2.5-pro")
        assert client.default_model == "gemini-2.5-pro"

    def test_omitted_model_uses_config_default_for_session(self) -> None:
        client = self._client_with_default("gemini-2.5-pro")
        client.get_or_create_session("ask:default")  # model omitted
        assert "ask:default:gemini-2.5-pro" in client._sessions

    def test_omitted_model_uses_config_default_for_thinking_config(self) -> None:
        # config default gemini-2.5-pro -> GEMINI_2 pro path (thinking_budget, min-clamped)
        client = self._client_with_default("gemini-2.5-pro")
        cfg = client._build_generation_config("none")  # no model arg
        assert cfg.thinking_config.thinking_budget == 128  # type: ignore[union-attr]
