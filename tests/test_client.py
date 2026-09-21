"""Tests for gemini_bridge/client.py — GeminiClient session management and ask()."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from google.genai import types

from gemini_bridge.client import (
    DEFAULT_MODEL,
    MAX_SESSIONS,
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
        for i in range(MAX_SESSIONS + 1):
            client.get_or_create_session(f"session:{i}")

        assert len(client._sessions) == MAX_SESSIONS
        assert f"session:0:{DEFAULT_MODEL}" not in client._sessions
        assert f"session:{MAX_SESSIONS}:{DEFAULT_MODEL}" in client._sessions


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

    def test_latest_alias_resolves_before_choosing_thinking_param(self) -> None:
        # #69: '-latest' aliases were assumed Gemini 2.x and sent thinking_budget. They now
        # resolve to a concrete id first (here the pinned Flash, no catalog) -> thinking_level.
        client = _make_client()
        cfg = client._build_generation_config("low", model="gemini-flash-latest")
        assert cfg.thinking_config.thinking_level is not None  # type: ignore[union-attr]
        assert cfg.thinking_config.thinking_budget is None  # type: ignore[union-attr]

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


def _call_response(name: str, args: dict) -> types.GenerateContentResponse:  # type: ignore[type-arg]
    return types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                content=types.Content(
                    role="model",
                    parts=[types.Part(function_call=types.FunctionCall(name=name, args=args))],
                ),
                finish_reason=types.FinishReason.STOP,
            )
        ]
    )


def _echo_registry():  # type: ignore[no-untyped-def]
    from gemini_bridge.tool_loop import ToolRegistry

    reg = ToolRegistry()

    async def handler(args: dict) -> dict:  # type: ignore[type-arg]
        return {"content": "file body"}

    reg.add(types.FunctionDeclaration(name="read_file", description="read"), handler)
    return reg


class TestAskWithTools:
    async def test_tool_turns_not_committed_to_history(self) -> None:
        client = _make_client()
        _mock_generate(
            client,
            side_effect=[_call_response("read_file", {"path": "a.py"}), _text_response("done")],
        )
        session = client.get_or_create_session()
        records: list = []  # type: ignore[type-arg]

        result = await client.ask(session, "q", "low", registry=_echo_registry(), records=records)
        assert result == "done"
        assert [c.role for c in session.history] == ["user", "model"]
        assert session.history[1].parts[0].text == "done"  # type: ignore[index]
        assert [r.name for r in records] == ["read_file"]

    async def test_declarations_sent_with_afc_disabled(self) -> None:
        client = _make_client()
        gen = _mock_generate(client, return_value=_text_response("ok"))
        session = client.get_or_create_session()
        await client.ask(session, "q", "low", registry=_echo_registry())

        cfg = gen.call_args.kwargs["config"]
        assert [d.name for d in cfg.tools[0].function_declarations] == ["read_file"]
        assert cfg.automatic_function_calling.disable is True

    def test_build_config_without_declarations_has_no_tools(self) -> None:
        cfg = _make_client().build_config("low")
        assert cfg.tools is None and cfg.tool_config is None

    def test_build_config_always_disables_sdk_afc(self) -> None:
        # The bridge runs its own loop; SDK AFC must never engage (and never log its warning).
        cfg = _make_client().build_config("low")
        assert cfg.automatic_function_calling.disable is True  # type: ignore[union-attr]

    def test_build_config_allow_tools_false_sets_mode_none(self) -> None:
        decl = types.FunctionDeclaration(name="t", description="d")
        cfg = _make_client().build_config("low", declarations=[decl], allow_tools=False)
        assert cfg.tool_config.function_calling_config.mode == types.FunctionCallingConfigMode.NONE  # type: ignore[union-attr]
        assert cfg.tools is not None


def _catalog(*ids: str) -> list:  # type: ignore[type-arg]
    from types import SimpleNamespace

    return [SimpleNamespace(name=f"models/{i}", supported_actions=["generateContent"]) for i in ids]


LIVE_IDS = (
    "gemini-3.5-flash",
    "gemini-3.8-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-3.1-pro-preview",
    "gemini-2.5-pro",
)


def _resolved_client(default_model=None) -> GeminiClient:  # type: ignore[no-untyped-def]
    config = Config(auth={"method": "api_key"}, default_model=default_model)
    with patch("google.genai.Client"):
        client = GeminiClient(config, api_key="k")
    client._raw_client.models.list.return_value = _catalog(*LIVE_IDS)
    client.refresh_latest()
    return client


class TestLatestResolution:
    """#69: omit the model -> newest Flash; aliases resolve on both backends."""

    def test_default_is_newest_flash(self) -> None:
        assert _resolved_client().default_model == "gemini-3.8-flash"

    def test_fallback_is_newest_flash_lite(self) -> None:
        assert _resolved_client().fallback_model == "gemini-3.5-flash-lite"

    def test_unresolved_client_uses_pinned_constants(self) -> None:
        from gemini_bridge.client import FALLBACK_MODEL

        client = _make_client()
        assert client.default_model == DEFAULT_MODEL
        assert client.fallback_model == FALLBACK_MODEL

    def test_refresh_failure_keeps_pinned(self) -> None:
        client = _make_client()
        client._raw_client.models.list.side_effect = RuntimeError("network down")
        assert client.refresh_latest() == {}
        assert client.default_model == DEFAULT_MODEL

    @pytest.mark.parametrize(
        ("requested", "concrete"),
        [
            ("flash", "gemini-3.8-flash"),
            ("pro", "gemini-3.1-pro-preview"),
            ("flash-lite", "gemini-3.5-flash-lite"),
            ("gemini-pro-latest", "gemini-3.1-pro-preview"),
            ("gemini-flash-latest", "gemini-3.8-flash"),
            ("gemini-3.5-flash", "gemini-3.5-flash"),  # explicit pin is never rewritten
        ],
    )
    def test_resolve_model(self, requested: str, concrete: str) -> None:
        assert _resolved_client().resolve_model(requested) == concrete

    def test_alias_without_resolution_uses_pinned_family(self) -> None:
        client = _make_client()
        assert client.resolve_model("flash") == DEFAULT_MODEL
        assert client.resolve_model("pro") == "gemini-3.1-pro-preview"

    def test_config_default_can_be_an_alias(self) -> None:
        assert _resolved_client(default_model="pro").default_model == "gemini-3.1-pro-preview"

    def test_config_default_pin_beats_latest(self) -> None:
        assert _resolved_client(default_model="gemini-3.5-flash").default_model == (
            "gemini-3.5-flash"
        )

    def test_sessions_keyed_by_concrete_model(self) -> None:
        client = _resolved_client()
        a = client.get_or_create_session("ask:default", model="flash")
        b = client.get_or_create_session("ask:default", model="gemini-3.8-flash")
        assert a is b and a.model == "gemini-3.8-flash"

    def test_alias_gets_gemini3_thinking_config(self) -> None:
        # Previously '-latest' aliases were assumed Gemini 2.x and sent thinking_budget.
        cfg = _resolved_client().build_config("medium", model="gemini-pro-latest")
        assert cfg.thinking_config.thinking_level is not None  # type: ignore[union-attr]
        assert cfg.thinking_config.thinking_budget is None  # type: ignore[union-attr]


class TestThinkingParameter:
    """The parameter name depends on the concrete model generation."""

    @pytest.mark.parametrize("model", ["gemini-4-flash", "gemini-5.2-pro", "gemini-3.8-flash"])
    def test_gemini3_and_later_use_level(self, model: str) -> None:
        cfg = _make_client().build_config("low", model=model)
        assert cfg.thinking_config.thinking_level is not None  # type: ignore[union-attr]

    def test_gemini2_uses_budget(self) -> None:
        cfg = _make_client().build_config("low", model="gemini-2.5-flash")
        assert cfg.thinking_config.thinking_budget == 1024  # type: ignore[union-attr]


def _api_error(message: str) -> Exception:
    return RuntimeError(
        "400 INVALID_ARGUMENT. {'error': {'code': 400, 'message': '" + message + "', "
        "'status': 'INVALID_ARGUMENT'}}"
    )


class TestThinkingSelfHeal:
    """Messages verbatim from the live Developer API, 2026-09-17."""

    async def test_minimal_rejected_steps_up_to_low_and_is_remembered(self) -> None:
        from google.genai.types import ThinkingLevel as L

        client = _make_client()
        gen = _mock_generate(
            client,
            side_effect=[
                _api_error(
                    "Thinking level MINIMAL is not supported for this model. "
                    "Please retry with other thinking level."
                ),
                _text_response("ok"),
                _text_response("ok again"),
            ],
        )
        session = client.get_or_create_session(model="gemini-3.8-flash")
        assert await client.ask(session, "q", "none") == "ok"
        levels = [c.kwargs["config"].thinking_config.thinking_level for c in gen.call_args_list]
        assert levels == [L.MINIMAL, L.LOW]

        await client.ask(session, "q2", "none")  # remembered: no second rejection
        assert gen.call_args.kwargs["config"].thinking_config.thinking_level == L.LOW
        assert gen.await_count == 3

    async def test_higher_levels_unaffected_by_floor(self) -> None:
        from google.genai.types import ThinkingLevel as L

        client = _make_client()
        client._thinking_floor["gemini-3.8-flash"] = L.LOW
        cfg = client.build_config("high", model="gemini-3.8-flash")
        assert cfg.thinking_config.thinking_level == L.HIGH  # type: ignore[union-attr]

    async def test_level_unsupported_switches_to_budget(self) -> None:
        client = _make_client()
        gen = _mock_generate(
            client,
            side_effect=[
                _api_error("Thinking level is not supported for this model."),
                _text_response("ok"),
            ],
        )
        session = client.get_or_create_session(model="gemini-3.9-flash")
        assert await client.ask(session, "q", "low") == "ok"
        assert gen.call_args.kwargs["config"].thinking_config.thinking_budget == 1024

    async def test_zero_budget_rejected_raises_budget(self) -> None:
        client = _make_client()
        gen = _mock_generate(
            client,
            side_effect=[
                _api_error("Budget 0 is invalid. This model only works in thinking mode."),
                _text_response("ok"),
            ],
        )
        session = client.get_or_create_session(model="gemini-2.7-flash")
        assert await client.ask(session, "q", "none") == "ok"
        assert gen.call_args.kwargs["config"].thinking_config.thinking_budget == 128

    async def test_unrelated_400_is_not_retried(self) -> None:
        client = _make_client()
        gen = _mock_generate(
            client, side_effect=_api_error("Request contains an invalid argument.")
        )
        session = client.get_or_create_session(model="gemini-3.8-flash")
        with pytest.raises(ClientError):
            await client.ask(session, "q", "none")
        assert gen.await_count == 1

    async def test_does_not_loop_forever(self) -> None:
        client = _make_client()
        err = _api_error("Thinking level MINIMAL is not supported for this model.")
        gen = _mock_generate(client, side_effect=[err] * 10)
        session = client.get_or_create_session(model="gemini-3.8-flash")
        with pytest.raises(ClientError):
            await client.ask(session, "q", "none")
        assert gen.await_count <= 4


class TestPreviewWarning:
    def test_alias_resolving_to_preview_does_not_warn(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        client = _resolved_client()
        with caplog.at_level("WARNING", logger="gemini_bridge.client"):
            client.get_or_create_session("x", model="pro")
        assert "preview model" not in caplog.text

    def test_explicit_preview_id_still_warns(self, caplog: pytest.LogCaptureFixture) -> None:
        client = _resolved_client()
        with caplog.at_level("WARNING", logger="gemini_bridge.client"):
            client.get_or_create_session("x", model="gemini-3.1-pro-preview")
        assert "preview model" in caplog.text


class TestSelfHealLogging:
    async def test_healed_call_logs_no_error(self, caplog: pytest.LogCaptureFixture) -> None:
        client = _make_client()
        _mock_generate(
            client,
            side_effect=[
                _api_error("Thinking level MINIMAL is not supported for this model."),
                _text_response("ok"),
            ],
        )
        session = client.get_or_create_session(model="gemini-3.8-flash")
        with caplog.at_level("WARNING", logger="gemini_bridge.client"):
            await client.ask(session, "q", "none")
        assert not [r for r in caplog.records if r.levelname == "ERROR"]


class TestReviewFindings69:
    async def test_concurrent_rejection_after_fix_learned_still_retries(self) -> None:
        # Finding 1: a second in-flight request rejected for MINIMAL after another request
        # already raised the floor must retry (with LOW), not fail.
        from google.genai.types import ThinkingLevel as L

        client = _make_client()
        client._thinking_floor["gemini-3.8-flash"] = L.LOW
        err = _api_error("Thinking level MINIMAL is not supported for this model.")
        assert client._learn_thinking("gemini-3.8-flash", err) is True

    def test_already_budget_model_retries(self) -> None:
        client = _make_client()
        client._budget_models.add("m")
        assert client._learn_thinking("m", _api_error(_LEVEL_UNSUPPORTED_MSG)) is True

    def test_budget_floor_already_set_retries(self) -> None:
        client = _make_client()
        client._budget_floor["m"] = 128
        assert client._learn_thinking("m", _api_error("Budget 0 is invalid.")) is True

    def test_highest_level_rejected_gives_up(self) -> None:
        client = _make_client()
        err = _api_error("Thinking level HIGH is not supported for this model.")
        assert client._learn_thinking("m", err) is False

    def test_implicit_default_preview_does_not_warn(self, caplog: pytest.LogCaptureFixture) -> None:
        # Finding 2: the resolver may pick a preview as newest Flash; that is intended.
        config = Config(auth={"method": "api_key"})
        with patch("google.genai.Client"):
            client = GeminiClient(config, api_key="k")
        client._raw_client.models.list.return_value = _catalog("gemini-3.9-flash-preview")
        client.refresh_latest()
        with caplog.at_level("WARNING", logger="gemini_bridge.client"):
            client.get_or_create_session("x")
        assert "preview model" not in caplog.text


_LEVEL_UNSUPPORTED_MSG = "Thinking level is not supported for this model."
