"""Tests for gemini_bridge/tools — tool registration and call_gemini() helper."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from google.genai import types

from gemini_bridge.client import GeminiClient
from gemini_bridge.config import Config
from gemini_bridge.tools.base import call_gemini, model_param_hint
from gemini_bridge.transcript import TranscriptWriter


def _make_client() -> GeminiClient:
    config = Config(project="test-project")
    mock_creds = MagicMock()
    with patch("google.genai.Client"):
        return GeminiClient(config, mock_creds)


def _make_client_api_key() -> GeminiClient:
    config = Config(auth={"method": "api_key"})
    with patch("google.genai.Client"):
        return GeminiClient(config, api_key="test-key")


def _model_description(mcp: object, tool_name: str) -> str:
    """Pull the actual `model` param description from a registered tool's JSON schema."""
    tools = asyncio.run(mcp.list_tools())  # type: ignore[attr-defined]
    tool = next(t for t in tools if t.name == tool_name)
    return tool.inputSchema["properties"]["model"]["description"]


def _param_description(mcp: object, tool_name: str, param: str):
    """Pull a named param's description from a registered tool's JSON schema (None if absent)."""
    tools = asyncio.run(mcp.list_tools())  # type: ignore[attr-defined]
    tool = next(t for t in tools if t.name == tool_name)
    return tool.inputSchema["properties"][param].get("description")


def _register_all_tools(tmp_path: object) -> object:
    from datetime import datetime

    from mcp.server.fastmcp import FastMCP

    from gemini_bridge.tools import (
        register_architect,
        register_ask,
        register_brainstorm,
        register_debug,
        register_review,
    )

    client = _make_client()
    transcript = TranscriptWriter(str(tmp_path), datetime.now())
    mcp = FastMCP("t")
    register_ask(mcp, client, transcript)
    register_brainstorm(mcp, client, transcript)
    register_review(mcp, client, transcript)
    register_debug(mcp, client, transcript)
    register_architect(mcp, client, transcript)
    return mcp


def _make_transcript(tmp_path: "Path") -> TranscriptWriter:
    from datetime import datetime

    return TranscriptWriter(str(tmp_path), datetime.now())


def _text_response(text: str) -> types.GenerateContentResponse:
    return types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                content=types.Content(role="model", parts=[types.Part.from_text(text=text)]),
                finish_reason=types.FinishReason.STOP,
            )
        ]
    )


def _mock_generate(client: GeminiClient, **kwargs: object) -> AsyncMock:
    mock = AsyncMock(**kwargs)
    client._raw_client.aio.models.generate_content = mock
    return mock


class TestCallGemini:
    async def test_returns_response_on_success(self, tmp_path: Path) -> None:
        client = _make_client()
        _mock_generate(client, return_value=_text_response("brainstorm result"))

        transcript = _make_transcript(tmp_path)
        result = await call_gemini(
            client=client,
            transcript=transcript,
            tool_name="gemini_brainstorm",
            session_name="default",
            system_instruction="Be creative.",
            prompt="Ideas for caching?",
            thinking="low",
        )
        assert result == "brainstorm result"

    async def test_returns_error_string_on_client_error(self, tmp_path: Path) -> None:
        client = _make_client()
        _mock_generate(client, side_effect=Exception("API down"))

        transcript = _make_transcript(tmp_path)
        result = await call_gemini(
            client=client,
            transcript=transcript,
            tool_name="gemini_ask",
            session_name="default",
            system_instruction="Answer.",
            prompt="Hello",
            thinking="none",
        )
        assert result.startswith("[gemini-bridge error]")

    async def test_appends_to_transcript_on_success(self, tmp_path: Path) -> None:
        client = _make_client()
        _mock_generate(client, return_value=_text_response("done"))

        transcript = _make_transcript(tmp_path)
        await call_gemini(
            client=client,
            transcript=transcript,
            tool_name="gemini_review",
            session_name="default",
            system_instruction="Review.",
            prompt="Check this code.",
            thinking="medium",
        )
        content = transcript.path.read_text()
        assert "gemini_review" in content
        assert "Check this code." in content

    async def test_fallback_to_default_model_on_503(self, tmp_path: Path) -> None:
        from gemini_bridge.client import FALLBACK_MODEL

        client = _make_client()
        busy = Exception("503 UNAVAILABLE model overloaded")
        gen = _mock_generate(client, side_effect=[busy] * 4 + [_text_response("fallback answer")])

        transcript = _make_transcript(tmp_path)
        with patch("gemini_bridge.client.asyncio.sleep", new=AsyncMock()):
            result = await call_gemini(
                client=client,
                transcript=transcript,
                tool_name="gemini_ask",
                session_name="default",
                system_instruction="Answer.",
                prompt="Hello",
                thinking="low",
                model="gemini-3.5-flash",  # busy model
            )
        assert "[gemini-bridge notice]" in result
        assert "gemini-3.5-flash" in result
        assert FALLBACK_MODEL in result
        assert "fallback answer" in result
        assert gen.call_args.kwargs["model"] == FALLBACK_MODEL

    async def test_fallback_when_model_omitted_and_default_overloaded(self, tmp_path: Path) -> None:
        # Regression: when the caller omits `model`, the DEFAULT_MODEL is what gets tried.
        # If the default (!= fallback) overloads, we must still fall back — the guard must
        # compare against the model actually used, not FALLBACK_MODEL.
        from gemini_bridge.client import DEFAULT_MODEL, FALLBACK_MODEL

        assert DEFAULT_MODEL != FALLBACK_MODEL, "test only meaningful when they differ"

        client = _make_client()
        busy = Exception("503 UNAVAILABLE model overloaded")
        _mock_generate(client, side_effect=[busy] * 4 + [_text_response("fallback answer")])

        transcript = _make_transcript(tmp_path)
        with patch("gemini_bridge.client.asyncio.sleep", new=AsyncMock()):
            result = await call_gemini(
                client=client,
                transcript=transcript,
                tool_name="gemini_ask",
                session_name="default",
                system_instruction="Answer.",
                prompt="Hello",
                thinking="low",
                # model omitted → default is tried
            )
        assert "[gemini-bridge notice]" in result
        assert DEFAULT_MODEL in result  # names the model that was unavailable
        assert FALLBACK_MODEL in result
        assert "fallback answer" in result


class TestToolRegistration:
    def test_all_tools_register_without_error(self, tmp_path: Path) -> None:
        from datetime import datetime

        from mcp.server.fastmcp import FastMCP

        from gemini_bridge.tools import (
            register_architect,
            register_ask,
            register_brainstorm,
            register_debug,
            register_review,
        )

        client = _make_client()
        transcript = TranscriptWriter(str(tmp_path), datetime.now())
        mcp = FastMCP("test-server")
        register_ask(mcp, client, transcript)
        register_brainstorm(mcp, client, transcript)
        register_review(mcp, client, transcript)
        register_debug(mcp, client, transcript)
        register_architect(mcp, client, transcript)


class TestModelParamHint:
    def test_helper_developer_includes_aliases(self) -> None:
        hint = model_param_hint(_make_client_api_key())
        assert "gemini-flash-latest" in hint
        assert "gemini-3.5-flash" in hint
        assert "gemini_list_models" in hint

    def test_helper_vertex_omits_aliases(self) -> None:
        hint = model_param_hint(_make_client())  # adc -> vertex
        assert "-latest" not in hint

    def test_helper_reflects_config_default_model(self) -> None:
        config = Config(auth={"method": "api_key"}, default_model="gemini-2.5-pro")
        with patch("google.genai.Client"):
            client = GeminiClient(config, api_key="k")
        hint = model_param_hint(client)
        assert "server default (gemini-2.5-pro)" in hint

    def test_registered_schema_developer_lists_aliases(self, tmp_path: Path) -> None:
        from datetime import datetime

        from mcp.server.fastmcp import FastMCP

        from gemini_bridge.tools import register_ask

        transcript = TranscriptWriter(str(tmp_path), datetime.now())
        mcp = FastMCP("dev")
        register_ask(mcp, _make_client_api_key(), transcript)
        desc = _model_description(mcp, "gemini_ask")
        # The hint must actually reach the tool schema (Field(description=...), not a bare str).
        assert "gemini-flash-latest" in desc
        assert "gemini_list_models" in desc

    def test_registered_schema_vertex_omits_aliases(self, tmp_path: Path) -> None:
        from datetime import datetime

        from mcp.server.fastmcp import FastMCP

        from gemini_bridge.tools import register_ask

        transcript = TranscriptWriter(str(tmp_path), datetime.now())
        mcp = FastMCP("vertex")
        register_ask(mcp, _make_client(), transcript)  # adc -> vertex
        desc = _model_description(mcp, "gemini_ask")
        assert "-latest" not in desc
        assert "gemini-3.5-flash" in desc


class TestAllParamDescriptionsSurface:
    """Every tool param description must reach the generated JSON schema (#55).

    A bare string in Annotated[...] is silently dropped by Pydantic v2 / FastMCP — only
    Field(description=...) surfaces. This pins that all params carry a schema description.
    """

    EXPECTED = {
        "gemini_ask": ["prompt", "thinking", "session_name", "model"],
        "gemini_brainstorm": ["topic", "context", "thinking", "session_name", "model"],
        "gemini_review": ["content", "question", "thinking", "session_name", "model"],
        "gemini_debug": ["error", "context", "thinking", "session_name", "model"],
        "gemini_architect": ["description", "question", "thinking", "session_name", "model"],
    }

    def test_every_param_has_a_schema_description(self, tmp_path: Path) -> None:
        mcp = _register_all_tools(tmp_path)
        missing = []
        for tool, params in self.EXPECTED.items():
            for param in params:
                if not _param_description(mcp, tool, param):
                    missing.append(f"{tool}.{param}")
        assert not missing, f"params missing schema descriptions: {missing}"

    def test_representative_descriptions_are_correct(self, tmp_path: Path) -> None:
        mcp = _register_all_tools(tmp_path)
        assert "question or request" in (_param_description(mcp, "gemini_ask", "prompt") or "")
        assert "Reasoning depth" in (_param_description(mcp, "gemini_ask", "thinking") or "")
        assert "stack trace" in (_param_description(mcp, "gemini_debug", "error") or "")
        assert "brainstorm" in (_param_description(mcp, "gemini_brainstorm", "topic") or "")


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


def _workspace(root: Path, **file_tools: object):  # type: ignore[no-untyped-def]
    from gemini_bridge.workspace import build_workspace

    cfg = Config(auth={"method": "api_key"}, file_tools=file_tools or {})  # type: ignore[arg-type]
    return build_workspace(cfg, root)


def _declared(gen: AsyncMock, call: int = 0) -> set[str]:
    cfg = gen.call_args_list[call].kwargs["config"]
    if not cfg.tools:
        return set()
    return {d.name for d in cfg.tools[0].function_declarations}


READ_TOOLS = {"list_dir", "glob", "grep", "read_file"}


class TestCapabilityMatrix:
    """Each MCP tool, called through FastMCP, declares its row of the capability matrix."""

    ARGS = {
        "gemini_ask": {"prompt": "q"},
        "gemini_debug": {"error": "boom"},
        "gemini_brainstorm": {"topic": "t"},
        "gemini_architect": {"description": "d"},
        "gemini_review": {"content": "c"},
    }
    EXPECTED = {
        "gemini_ask": READ_TOOLS,
        "gemini_debug": READ_TOOLS,
        "gemini_brainstorm": READ_TOOLS | {"write_file"},
        "gemini_architect": READ_TOOLS | {"write_file"},
        "gemini_review": READ_TOOLS | {"write_file"},
    }

    async def _run(self, tmp_path: Path, tool: str, workspace: object) -> AsyncMock:
        from datetime import datetime

        from mcp.server.fastmcp import FastMCP

        from gemini_bridge.server import build_server

        client = _make_client_api_key()
        gen = _mock_generate(client, return_value=_text_response("ok"))
        transcript = TranscriptWriter(str(tmp_path / "t"), datetime.now())
        mcp: FastMCP = build_server(client, transcript, workspace)  # type: ignore[arg-type]
        await mcp.call_tool(tool, self.ARGS[tool])
        return gen

    @pytest.mark.parametrize("tool", sorted(EXPECTED))
    async def test_row(self, tmp_path: Path, tool: str) -> None:
        gen = await self._run(tmp_path, tool, _workspace(tmp_path))
        assert _declared(gen) == self.EXPECTED[tool]

    @pytest.mark.parametrize("tool", sorted(EXPECTED))
    async def test_no_workspace_no_tools(self, tmp_path: Path, tool: str) -> None:
        gen = await self._run(tmp_path, tool, None)
        assert _declared(gen) == set()

    async def test_kill_switch(self, tmp_path: Path) -> None:
        gen = await self._run(tmp_path, "gemini_review", _workspace(tmp_path, enabled=False))
        assert _declared(gen) == set()

    async def test_tools_preamble_added_to_system_instruction(self, tmp_path: Path) -> None:
        gen = await self._run(tmp_path, "gemini_ask", _workspace(tmp_path))
        si = gen.call_args.kwargs["config"].system_instruction
        assert "read_file" in si and "write_file" not in si


class TestToolCallsInTranscript:
    async def test_tool_calls_logged_on_success(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("x = 1\n")
        client = _make_client_api_key()
        _mock_generate(
            client,
            side_effect=[
                _call_response("read_file", {"path": "a.py"}),
                _call_response("read_file", {"path": "../escape"}),
                _text_response("answer"),
            ],
        )
        transcript = _make_transcript(tmp_path / "t")
        result = await call_gemini(
            client=client,
            transcript=transcript,
            tool_name="gemini_ask",
            session_name="default",
            system_instruction="Answer.",
            prompt="q",
            thinking="low",
            workspace=_workspace(tmp_path),
        )
        assert result == "answer"
        content = transcript.path.read_text()
        assert "→ read_file(path='a.py')" in content
        assert "✗ read_file(path='../escape') → rejected: path escapes repo root" in content

    async def test_tool_calls_logged_when_call_fails(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("x = 1\n")
        client = _make_client_api_key()
        _mock_generate(
            client,
            side_effect=[
                _call_response("read_file", {"path": "a.py"}),
                RuntimeError("400 INVALID_ARGUMENT"),
            ],
        )
        transcript = _make_transcript(tmp_path / "t")
        result = await call_gemini(
            client=client,
            transcript=transcript,
            tool_name="gemini_ask",
            session_name="default",
            system_instruction="Answer.",
            prompt="q",
            thinking="low",
            workspace=_workspace(tmp_path),
        )
        assert result.startswith("[gemini-bridge error]")
        content = transcript.path.read_text()
        assert "→ read_file(path='a.py')" in content
        assert "[gemini-bridge error]" in content


class TestArtifacts:
    async def _call(
        self,
        tmp_path: Path,
        tool: str,
        args: dict,
        **ws: object,  # type: ignore[type-arg]
    ) -> str:
        from datetime import datetime

        from gemini_bridge.server import build_server

        client = _make_client_api_key()
        _mock_generate(client, return_value=_text_response("the answer"))
        transcript = TranscriptWriter(str(tmp_path / "t"), datetime.now())
        mcp = build_server(client, transcript, _workspace(tmp_path, **ws))
        content, _ = await mcp.call_tool(tool, args)  # type: ignore[misc]
        return content[0].text  # type: ignore[no-any-return,index]

    def _artifacts(self, tmp_path: Path) -> list[Path]:
        d = tmp_path / "gemini-artifacts"
        return sorted(d.iterdir()) if d.exists() else []

    async def test_architect_saves_by_default(self, tmp_path: Path) -> None:
        reply = await self._call(tmp_path, "gemini_architect", {"description": "Design a cache"})
        [path] = self._artifacts(tmp_path)
        assert path.name.endswith("-gemini-architect-design-a-cache.md")
        assert "the answer" in path.read_text()
        assert reply.startswith("the answer")
        assert f"[gemini-bridge] artifact saved: gemini-artifacts/{path.name}" in reply

    async def test_review_saves_by_default(self, tmp_path: Path) -> None:
        await self._call(tmp_path, "gemini_review", {"content": "code", "question": "Is auth ok"})
        [path] = self._artifacts(tmp_path)
        assert path.name.endswith("-gemini-review-is-auth-ok.md")

    async def test_opt_out(self, tmp_path: Path) -> None:
        reply = await self._call(
            tmp_path, "gemini_architect", {"description": "d", "write_artifact": False}
        )
        assert self._artifacts(tmp_path) == []
        assert "artifact" not in reply

    async def test_brainstorm_off_by_default(self, tmp_path: Path) -> None:
        await self._call(tmp_path, "gemini_brainstorm", {"topic": "t"})
        assert self._artifacts(tmp_path) == []

    async def test_brainstorm_opt_in(self, tmp_path: Path) -> None:
        await self._call(tmp_path, "gemini_brainstorm", {"topic": "t", "write_artifact": True})
        assert len(self._artifacts(tmp_path)) == 1

    async def test_saved_even_with_tools_disabled(self, tmp_path: Path) -> None:
        await self._call(tmp_path, "gemini_review", {"content": "c"}, enabled=False)
        assert len(self._artifacts(tmp_path)) == 1

    async def test_save_failure_keeps_answer(self, tmp_path: Path, monkeypatch: object) -> None:
        from gemini_bridge.artifacts import ArtifactStore

        def boom(*a: object, **k: object) -> Path:
            raise OSError("disk full")

        monkeypatch.setattr(ArtifactStore, "save", boom)  # type: ignore[attr-defined]
        reply = await self._call(tmp_path, "gemini_review", {"content": "c"})
        assert reply.startswith("the answer")
        assert "[gemini-bridge notice] artifact not saved: disk full" in reply

    def test_ask_and_debug_have_no_write_artifact_param(self, tmp_path: Path) -> None:
        mcp = _register_all_tools(tmp_path)
        tools = {t.name: t for t in asyncio.run(mcp.list_tools())}  # type: ignore[attr-defined]
        assert "write_artifact" not in tools["gemini_ask"].inputSchema["properties"]
        assert "write_artifact" not in tools["gemini_debug"].inputSchema["properties"]
        assert (
            tools["gemini_architect"].inputSchema["properties"]["write_artifact"]["default"] is True
        )
        assert (
            tools["gemini_brainstorm"].inputSchema["properties"]["write_artifact"]["default"]
            is False
        )
