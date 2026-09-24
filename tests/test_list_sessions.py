"""Tests for sidekick.tools.list_sessions — the in-memory conversations Claude can continue (#15)."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from google.genai import types

from sidekick.client import DEFAULT_MODEL, GeminiClient
from sidekick.config import Config
from sidekick.guide import help_text, server_instructions
from sidekick.tools import list_sessions as ls
from tests.test_capability_metadata import _server, _tools, _workspace


def _client() -> GeminiClient:
    with patch("google.genai.Client"):
        return GeminiClient(Config(project="test-project"), MagicMock())


def _turn(client: GeminiClient, name: str, model: str | None = None) -> None:
    """Commit one exchange the way GeminiClient.ask does: a user prompt plus the answer."""
    session = client.get_or_create_session(name=name, model=model)
    session.history.extend(
        [
            types.Content(role="user", parts=[types.Part.from_text(text="q")]),
            types.Content(role="model", parts=[types.Part.from_text(text="a")]),
        ]
    )


class TestClientSessions:
    def test_empty_before_any_call(self) -> None:
        assert _client().sessions() == []

    def test_most_recently_used_first(self) -> None:
        client = _client()
        client.get_or_create_session("ask:old")
        client.get_or_create_session("ask:new")
        client.get_or_create_session("ask:old")  # touching it makes it the newest
        assert [s.name for s in client.sessions()] == ["ask:old", "ask:new"]

    def test_session_names_may_contain_colons(self) -> None:
        client = _client()
        client.get_or_create_session("review:pr:42")
        assert client.sessions()[0].name == "review:pr:42"


class TestFormatSessionList:
    def test_empty_explains_how_sessions_start(self) -> None:
        text = ls.format_session_list([])
        assert "No conversations yet" in text
        assert "new name starts fresh" in text

    def test_row_shows_tool_name_model_and_turns(self) -> None:
        client = _client()
        _turn(client, "review:pr:42")
        _turn(client, "review:pr:42")
        text = ls.format_session_list(client.sessions())
        row = next(line for line in text.splitlines() if "pr:42" in line)
        assert row.split()[:4] == ["review", "pr:42", DEFAULT_MODEL, "2"]

    def test_created_but_unanswered_session_counts_zero_turns(self) -> None:
        client = _client()
        client.get_or_create_session("ask:default")
        row = next(
            line
            for line in ls.format_session_list(client.sessions()).splitlines()
            if "default" in line and DEFAULT_MODEL in line
        )
        assert row.split()[3] == "0"

    def test_same_name_on_two_models_is_two_rows(self) -> None:
        client = _client()
        _turn(client, "ask:x", model="gemini-2.5-pro")
        _turn(client, "ask:x")
        rows = [ln for ln in ls.format_session_list(client.sessions()).splitlines() if " x " in ln]
        assert len(rows) == 2

    def test_says_how_to_continue(self) -> None:
        client = _client()
        _turn(client, "ask:x")
        assert "session_name" in ls.format_session_list(client.sessions())


class TestRegistration:
    def test_registered_read_only_and_closed_world(self, tmp_path: Path) -> None:
        tool = _tools(_server(tmp_path, _workspace(tmp_path)))["list_sessions"]
        assert tool.annotations is not None
        assert tool.annotations.readOnlyHint is True
        assert tool.annotations.destructiveHint is False
        assert tool.annotations.openWorldHint is False

    def test_advertised_in_instructions_and_help(self, tmp_path: Path) -> None:
        ws = _workspace(tmp_path)
        assert "list_sessions" in server_instructions(ws)
        assert "list_sessions" in help_text(ws, None, topic="tools")
        assert "list_sessions" in help_text(ws, None, topic="sessions")
