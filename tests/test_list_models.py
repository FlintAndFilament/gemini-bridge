"""Tests for sidekick.tools.list_models — live catalog rendering + graceful degradation."""

from types import SimpleNamespace
from unittest.mock import patch

from sidekick import models
from sidekick.client import DEFAULT_MODEL, ClientError, GeminiClient
from sidekick.config import Config
from sidekick.tools import list_models as lm


def _meta(
    name: str, display: str = "", actions=("generateContent", "countTokens")
) -> SimpleNamespace:
    return SimpleNamespace(name=name, display_name=display, supported_actions=list(actions))


def _client_api_key() -> GeminiClient:
    with patch("google.genai.Client"):
        return GeminiClient(Config(auth={"method": "api_key"}), api_key="k")


class TestClientListModels:
    def test_passes_through_raw_client(self) -> None:
        client = _client_api_key()
        fakes = [_meta("models/gemini-2.5-flash")]
        client._raw_client.models.list.return_value = fakes
        assert client.list_models() == fakes

    def test_wraps_failure_in_client_error(self) -> None:
        client = _client_api_key()
        client._raw_client.models.list.side_effect = RuntimeError("network down")
        try:
            client.list_models()
        except ClientError as exc:
            assert "network down" in str(exc)
        else:  # pragma: no cover
            raise AssertionError("expected ClientError")


class TestFormatModelList:
    def _raw(self) -> list:
        return [
            _meta("models/gemini-3.5-flash", "Gemini 3.5 Flash"),
            _meta("models/gemini-2.5-flash", "Gemini 2.5 Flash"),
            _meta("models/gemini-flash-latest", "Gemini Flash Latest"),
            _meta("models/gemini-2.5-flash-image", "Image"),  # must be filtered out
            _meta("models/gemini-2.5-flash-preview-tts", "TTS"),  # must be filtered out
            _meta("models/gemini-embedding-001", "Embed", actions=["embedContent"]),  # filtered
            _meta("models/gemma-4-31b-it", "Gemma"),  # non-gemini family, filtered
            _meta("models/gemini-2.5-computer-use-preview-10-2025", "CU"),  # specialized, filtered
        ]

    def test_excludes_non_chat_models(self) -> None:
        out = lm.format_model_list(self._raw(), models.DEVELOPER_API, DEFAULT_MODEL)
        assert "gemini-2.5-flash-image" not in out
        assert "gemini-2.5-flash-preview-tts" not in out
        assert "gemini-embedding-001" not in out
        assert "gemma-4-31b-it" not in out
        assert "computer-use" not in out
        assert "gemini-2.5-flash" in out

    def test_marks_default_and_alias(self) -> None:
        out = lm.format_model_list(self._raw(), models.DEVELOPER_API, DEFAULT_MODEL)
        lines = {ln.split()[0]: ln for ln in out.splitlines() if ln.startswith("  gemini")}
        assert "(default)" in lines["gemini-3.5-flash"]
        assert "(alias)" in lines["gemini-flash-latest"]
        assert "(default)" not in lines["gemini-2.5-flash"]

    def test_default_listed_first(self) -> None:
        out = lm.format_model_list(self._raw(), models.DEVELOPER_API, DEFAULT_MODEL)
        model_lines = [ln for ln in out.splitlines() if ln.startswith("  gemini")]
        assert model_lines[0].split()[0] == DEFAULT_MODEL

    def test_names_backend_and_count(self) -> None:
        out = lm.format_model_list(self._raw(), models.DEVELOPER_API, DEFAULT_MODEL)
        assert "Developer API" in out
        assert "(3 available)" in out  # 3 chat models survive the filter

    def test_empty_live_list_degrades_to_shortlist(self) -> None:
        out = lm.format_model_list([], models.VERTEX, DEFAULT_MODEL)
        assert "Live model list unavailable" in out
        assert "gemini-3.5-flash" in out


class TestStaticFallback:
    def test_developer_shortlist_with_aliases(self) -> None:
        out = lm.format_static_fallback(models.DEVELOPER_API, DEFAULT_MODEL, "boom")
        assert "boom" in out
        assert "gemini-flash-latest" in out
        assert "(default)" in out

    def test_vertex_shortlist_alias_free(self) -> None:
        out = lm.format_static_fallback(models.VERTEX, DEFAULT_MODEL, "boom")
        assert "-latest" not in out
        assert "gemini-3.5-flash" in out


class TestRenderModelList:
    def test_success_renders_live(self) -> None:
        client = _client_api_key()
        client._raw_client.models.list.return_value = [_meta("models/gemini-2.5-flash", "Flash")]
        out = lm.render_model_list(client, models.DEVELOPER_API)
        assert "gemini-2.5-flash" in out
        assert "Live model list unavailable" not in out

    def test_failure_degrades_to_static(self) -> None:
        client = _client_api_key()
        client._raw_client.models.list.side_effect = RuntimeError("boom")
        out = lm.render_model_list(client, models.DEVELOPER_API)
        assert "Live model list unavailable" in out
        assert "gemini-3.5-flash" in out  # from the static shortlist

    def test_default_marker_reflects_config_default(self) -> None:
        config = Config(auth={"method": "api_key"}, default_model="gemini-2.5-flash")
        with patch("google.genai.Client"):
            client = GeminiClient(config, api_key="k")
        client._raw_client.models.list.return_value = [
            _meta("models/gemini-3.5-flash", "Gemini 3.5 Flash"),
            _meta("models/gemini-2.5-flash", "Gemini 2.5 Flash"),
        ]
        out = lm.render_model_list(client, models.DEVELOPER_API)
        lines = [ln for ln in out.splitlines() if ln.startswith("  gemini")]
        assert lines[0].split()[0] == "gemini-2.5-flash"  # config default listed first
        assert "(default)" in lines[0]
        assert "default (gemini-2.5-flash)" in out


class TestRegistration:
    def test_registers_and_lists_via_schema(self, tmp_path: object) -> None:
        import asyncio
        from datetime import datetime

        from mcp.server.fastmcp import FastMCP

        from sidekick.transcript import TranscriptWriter

        client = _client_api_key()
        client._raw_client.models.list.return_value = [_meta("models/gemini-2.5-flash", "Flash")]
        mcp = FastMCP("t")
        lm.register(mcp, client, TranscriptWriter(str(tmp_path), datetime.now()))
        tool = next(t for t in asyncio.run(mcp.list_tools()) if t.name == "gemini_list_models")
        assert "refresh" in tool.inputSchema["properties"]


class TestLatestMarkers:
    def test_marks_latest_per_family(self) -> None:
        from sidekick.tools.list_models import format_model_list

        out = format_model_list(
            [_meta("models/gemini-3.8-flash", "3.8"), _meta("models/gemini-3.5-flash", "3.5")],
            "developer",
            "gemini-3.8-flash",
            {"flash": "gemini-3.8-flash"},
        )
        line = next(ln for ln in out.splitlines() if "gemini-3.8-flash " in ln)
        assert "default" in line and "latest flash" in line
        assert "flash / flash-lite / pro" in out
