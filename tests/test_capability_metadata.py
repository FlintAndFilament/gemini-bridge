"""Tests for the client-facing capability metadata (#74).

#68 gave Gemini repository file tools; these tests pin the *advertised* surface — the tool
descriptions, the MCP tool annotations, and the server instructions — so a capability change
that forgets to tell the calling Claude session fails here.
"""

import asyncio
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from mcp.server.fastmcp import FastMCP
from mcp.types import Tool

from gemini_bridge.client import GeminiClient
from gemini_bridge.config import Config
from gemini_bridge.server import build_server, server_instructions
from gemini_bridge.tools.base import capability_hint, tool_annotations
from gemini_bridge.transcript import TranscriptWriter
from gemini_bridge.workspace import Workspace, build_workspace
from tests.test_tools import _text_response

READ_TOOL_NAMES = ("list_dir", "glob", "grep", "read_file")
WRITE_TOOLS = ("gemini_architect", "gemini_brainstorm", "gemini_review")
READ_ONLY_TOOLS = ("gemini_ask", "gemini_debug")
GENERATING_TOOLS = WRITE_TOOLS + READ_ONLY_TOOLS


def _client() -> GeminiClient:
    with patch("google.genai.Client"):
        return GeminiClient(Config(auth={"method": "api_key"}), api_key="test-key")


def _workspace(root: Path, **file_tools: object) -> Workspace:
    cfg = Config(auth={"method": "api_key"}, file_tools=file_tools or {})  # type: ignore[arg-type]
    return build_workspace(cfg, root)


def _server(tmp_path: Path, workspace: object) -> FastMCP:
    transcript = TranscriptWriter(str(tmp_path / "t"), datetime.now())
    return build_server(_client(), transcript, workspace)  # type: ignore[arg-type]


def _tools(mcp: FastMCP) -> dict[str, Tool]:
    return {t.name: t for t in asyncio.run(mcp.list_tools())}


class TestCapabilityHint:
    """capability_hint() — the sentence appended to each tool's description."""

    def test_no_workspace_says_unavailable(self) -> None:
        hint = capability_hint(None, write=False)
        assert "cannot read" in hint.lower()
        for name in READ_TOOL_NAMES:
            assert name not in hint

    def test_enabled_read_row_names_the_tools_and_root(self, tmp_path: Path) -> None:
        hint = capability_hint(_workspace(tmp_path), write=False)
        for name in READ_TOOL_NAMES:
            assert name in hint
        assert str(tmp_path.resolve()) in hint
        assert "write_file" not in hint

    def test_enabled_read_row_steers_away_from_pasting(self, tmp_path: Path) -> None:
        assert "pasting" in capability_hint(_workspace(tmp_path), write=False).lower()

    def test_enabled_write_row_warns_about_overwriting(self, tmp_path: Path) -> None:
        hint = capability_hint(_workspace(tmp_path), write=True)
        assert "write_file" in hint
        assert "overwrite" in hint.lower()

    def test_write_row_states_the_size_cap(self, tmp_path: Path) -> None:
        ws = _workspace(tmp_path, max_write_bytes=4096)
        assert "4096" in capability_hint(ws, write=True)

    @pytest.mark.parametrize("write", [False, True])
    def test_kill_switch_names_the_reason(self, tmp_path: Path, write: bool) -> None:
        hint = capability_hint(_workspace(tmp_path, enabled=False), write=write)
        assert "file_tools.enabled=false" in hint
        assert "write_file" not in hint

    def test_unsafe_root_names_the_reason(self) -> None:
        home = Path.home()
        hint = capability_hint(_workspace(home), write=True)
        assert "home directory" in hint
        assert "write_file" not in hint


class TestToolAnnotations:
    """tool_annotations() — the structured MCP hints."""

    def test_write_row_is_destructive(self, tmp_path: Path) -> None:
        assert tool_annotations(_workspace(tmp_path), write=True).destructiveHint is True

    def test_read_row_is_not_destructive(self, tmp_path: Path) -> None:
        assert tool_annotations(_workspace(tmp_path), write=False).destructiveHint is False

    def test_disabled_write_row_is_not_destructive(self, tmp_path: Path) -> None:
        ws = _workspace(tmp_path, enabled=False)
        assert tool_annotations(ws, write=True).destructiveHint is False

    def test_no_workspace_is_not_destructive(self) -> None:
        assert tool_annotations(None, write=True).destructiveHint is False

    def test_every_call_is_open_world(self, tmp_path: Path) -> None:
        assert tool_annotations(_workspace(tmp_path), write=False).openWorldHint is True

    def test_nothing_is_read_only_because_of_the_transcript(self, tmp_path: Path) -> None:
        assert tool_annotations(_workspace(tmp_path), write=False).readOnlyHint is False


class TestRegisteredDescriptions:
    """What the MCP client actually receives from list_tools()."""

    @pytest.mark.parametrize("tool", GENERATING_TOOLS)
    def test_description_advertises_reading(self, tmp_path: Path, tool: str) -> None:
        described = _tools(_server(tmp_path, _workspace(tmp_path)))[tool].description or ""
        assert "read_file" in described

    @pytest.mark.parametrize("tool", WRITE_TOOLS)
    def test_write_tools_advertise_writing(self, tmp_path: Path, tool: str) -> None:
        described = _tools(_server(tmp_path, _workspace(tmp_path)))[tool].description or ""
        assert "write_file" in described

    @pytest.mark.parametrize("tool", READ_ONLY_TOOLS)
    def test_read_tools_do_not_advertise_writing(self, tmp_path: Path, tool: str) -> None:
        described = _tools(_server(tmp_path, _workspace(tmp_path)))[tool].description or ""
        assert "write_file" not in described

    @pytest.mark.parametrize("tool", GENERATING_TOOLS)
    def test_original_purpose_text_survives(self, tmp_path: Path, tool: str) -> None:
        """The capability sentence is appended — it must not replace what the tool is for."""
        described = _tools(_server(tmp_path, _workspace(tmp_path)))[tool].description or ""
        assert described.startswith("Ask Gemini")

    @pytest.mark.parametrize("tool", GENERATING_TOOLS)
    def test_disabled_state_is_advertised(self, tmp_path: Path, tool: str) -> None:
        server = _server(tmp_path, _workspace(tmp_path, enabled=False))
        described = _tools(server)[tool].description or ""
        assert "file_tools.enabled=false" in described
        assert "write_file" not in described

    @pytest.mark.parametrize("tool", WRITE_TOOLS)
    def test_annotations_reach_the_client(self, tmp_path: Path, tool: str) -> None:
        annotations = _tools(_server(tmp_path, _workspace(tmp_path)))[tool].annotations
        assert annotations is not None and annotations.destructiveHint is True

    def test_list_models_is_not_given_a_file_hint(self, tmp_path: Path) -> None:
        described = _tools(_server(tmp_path, _workspace(tmp_path)))[
            "gemini_list_models"
        ].description
        assert "read_file" not in (described or "")


class TestServerInstructions:
    """The server-level instructions string, which Claude Code surfaces on connect."""

    def test_enabled_names_root_and_capability_rows(self, tmp_path: Path) -> None:
        text = server_instructions(_workspace(tmp_path))
        assert str(tmp_path.resolve()) in text
        for name in READ_TOOL_NAMES:
            assert name in text
        for tool in WRITE_TOOLS:
            assert tool in text
        assert "write_file" in text

    def test_enabled_mentions_the_deny_list(self, tmp_path: Path) -> None:
        assert ".env" in server_instructions(_workspace(tmp_path))

    def test_disabled_names_the_reason_and_omits_write(self, tmp_path: Path) -> None:
        text = server_instructions(_workspace(tmp_path, enabled=False))
        assert "file_tools.enabled=false" in text
        assert "write_file" not in text

    def test_no_workspace_is_stated_not_crashed(self) -> None:
        assert "cannot read" in server_instructions(None).lower()

    def test_reaches_the_built_server(self, tmp_path: Path) -> None:
        mcp = _server(tmp_path, _workspace(tmp_path))
        assert mcp.instructions is not None
        assert str(tmp_path.resolve()) in mcp.instructions


class TestWorkspaceExposesItsLimits:
    """The metadata needs these to be public, not reached for through privates."""

    def test_max_write_bytes_is_public(self, tmp_path: Path) -> None:
        assert _workspace(tmp_path, max_write_bytes=8192).max_write_bytes == 8192

    def test_deny_list_is_public(self, tmp_path: Path) -> None:
        assert ".env" in _workspace(tmp_path).sandbox.deny

    def test_deny_list_is_immutable(self, tmp_path: Path) -> None:
        deny = _workspace(tmp_path).sandbox.deny
        with pytest.raises((AttributeError, TypeError)):
            deny.append("x")  # type: ignore[attr-defined]


def test_no_secrets_in_metadata(tmp_path: Path) -> None:
    """The advertised text is built from config; make sure no credential can ride along."""
    mcp = _server(tmp_path, _workspace(tmp_path))
    blob = (mcp.instructions or "") + "".join(t.description or "" for t in _tools(mcp).values())
    assert "test-key" not in blob


class TestAdvertisedTextMatchesWhatActuallyHitsDisk:
    """Cross-checks, not string presence (review of #74).

    The first pass asserted only that the advertised text existed. These tests run the tool
    and compare the files that appear against what the text promised, so a description that
    claims more (or less) than the bridge does fails here.
    """

    ARGS = {
        "gemini_ask": {"prompt": "q"},
        "gemini_debug": {"error": "boom"},
        "gemini_brainstorm": {"topic": "t"},
        "gemini_architect": {"description": "d"},
        "gemini_review": {"content": "c"},
    }

    def _call(self, tmp_path: Path, tool: str, workspace: object, **extra: object) -> FastMCP:
        client = _client()
        client._raw_client.aio.models.generate_content = AsyncMock(  # type: ignore[attr-defined]
            return_value=_text_response("ok")
        )
        transcript = TranscriptWriter(str(tmp_path / "transcripts"), datetime.now())
        mcp = build_server(client, transcript, workspace)  # type: ignore[arg-type]
        asyncio.run(mcp.call_tool(tool, {**self.ARGS[tool], **extra}))
        return mcp

    @pytest.mark.parametrize("tool", GENERATING_TOOLS)
    def test_transcript_write_is_disclosed(self, tmp_path: Path, tool: str) -> None:
        """Every call writes a transcript entry — the description must say so, and must not
        claim the call leaves the tree untouched.

        The advertised text embeds the sandbox root, and pytest builds tmp_path from the test
        name, so the root is stripped before matching — otherwise the path alone satisfies it.
        """
        root = tmp_path / "repo"
        root.mkdir()
        mcp = self._call(tmp_path, tool, _workspace(root))
        assert list((tmp_path / "transcripts").glob("*.md")), "no transcript was written"
        described = (_tools(mcp)[tool].description or "").replace(str(root.resolve()), "")
        assert "session transcript" in described

    @pytest.mark.parametrize("tool", GENERATING_TOOLS)
    def test_no_tool_claims_nothing_is_written(self, tmp_path: Path, tool: str) -> None:
        described = _tools(_server(tmp_path, _workspace(tmp_path)))[tool].description or ""
        assert "cannot modify the working tree — the answer comes back to you only" not in described

    def test_artifact_still_written_with_file_tools_off_and_disclosed(self, tmp_path: Path) -> None:
        """Artifact saving ignores the kill switch (tools/base.py), so the OFF wording must
        still disclose the file that appears."""
        root = tmp_path / "repo"
        root.mkdir()
        ws = _workspace(root, enabled=False)
        mcp = self._call(tmp_path, "gemini_review", ws)
        assert list(Path(ws.artifacts.directory).glob("*.md")), "no artifact was written"
        described = (_tools(mcp)["gemini_review"].description or "").lower()
        assert "artifact" in described

    def test_read_only_tools_write_no_artifact_and_promise_none(self, tmp_path: Path) -> None:
        root = tmp_path / "repo"
        root.mkdir()
        ws = _workspace(root)
        mcp = self._call(tmp_path, "gemini_ask", ws)
        assert not Path(ws.artifacts.directory).exists()
        assert "artifact" not in (_tools(mcp)["gemini_ask"].description or "").lower()

    def test_brainstorm_opt_in_artifact_is_disclosed(self, tmp_path: Path) -> None:
        root = tmp_path / "repo"
        root.mkdir()
        ws = _workspace(root)
        mcp = self._call(tmp_path, "gemini_brainstorm", ws, write_artifact=True)
        assert list(Path(ws.artifacts.directory).glob("*.md")), "no artifact was written"
        described = _tools(mcp)["gemini_brainstorm"].description or ""
        assert "write_artifact=true" in described

    def test_server_instructions_name_brainstorm_as_an_artifact_writer(
        self, tmp_path: Path
    ) -> None:
        """#4: brainstorm accepts write_artifact too; the instructions must not imply it never
        writes one."""
        text = server_instructions(_workspace(tmp_path))
        sentence = next(s for s in text.split("\n") if "artifact" in s)
        assert "gemini_brainstorm" in sentence
        assert "write_artifact=true" in sentence

    def test_server_instructions_disclose_the_transcript_path(self, tmp_path: Path) -> None:
        transcript = TranscriptWriter(str(tmp_path / "transcripts"), datetime.now())
        text = server_instructions(_workspace(tmp_path), transcript)
        assert str(transcript.path) in text

    def test_disabled_instructions_still_disclose_artifacts(self, tmp_path: Path) -> None:
        text = server_instructions(_workspace(tmp_path, enabled=False))
        assert "artifact" in text.lower()


class TestAdvertisedSandboxMatchesTheRealOne:
    """The deny wording must not promise protection the sandbox does not give."""

    def test_named_deny_examples_are_really_denied(self, tmp_path: Path) -> None:
        """The hint summarises the default deny-list; every example it names must hold."""
        ws = _workspace(tmp_path)
        hint = capability_hint(ws, write=False)
        assert "the default deny-list blocks" in hint
        for denied in (".git/config", ".env", "secrets.pem"):
            assert ws.sandbox.is_denied(tmp_path / denied), denied

    def test_a_readable_dotfile_is_not_claimed_to_be_denied(self, tmp_path: Path) -> None:
        (tmp_path / ".gitignore").write_text("x\n")
        ws = _workspace(tmp_path)
        assert not ws.sandbox.is_denied(tmp_path / ".gitignore"), "fixture assumption broke"
        hint = capability_hint(ws, write=False)
        assert "dotfiles and secrets are denied" not in hint
        assert "everything else under the root is readable" in hint

    def test_tool_descriptions_use_the_live_deny_list_too(self, tmp_path: Path) -> None:
        """#74 review: the per-tool hint hardcoded the default list while the server
        instructions read the live one, so a custom deny made the two contradict."""
        ws = _workspace(tmp_path, deny=["custom-secret.txt"])
        hint = capability_hint(ws, write=False)
        assert "custom-secret.txt" in hint
        assert ".env" not in hint  # no longer denied, so it must not be advertised as such

    def test_search_skipped_directories_are_disclosed(self, tmp_path: Path) -> None:
        """glob/grep silently skip dependency and cache dirs; a caller told 'everything else is
        readable' would misread an empty grep as proof of absence."""
        text = server_instructions(_workspace(tmp_path))
        for skipped in ("node_modules", ".venv", "dist"):
            assert skipped in text
        assert "symlinked directories" in text
        assert "glob and grep" in capability_hint(_workspace(tmp_path), write=False)

    def test_denied_names_come_from_the_live_deny_list(self, tmp_path: Path) -> None:
        ws = _workspace(tmp_path, deny=["custom-secret.txt"])
        text = server_instructions(ws)
        assert "custom-secret.txt" in text

    def test_empty_deny_list_reads_as_nothing(self, tmp_path: Path) -> None:
        text = server_instructions(_workspace(tmp_path, deny=[]))
        assert "Denied at any depth: nothing (the deny-list is empty)." in text


class TestWriteCapIsTheEnforcedOne:
    """The advertised cap must be the value write_file() checks, not a second copy."""

    def test_workspace_reports_the_file_tools_cap(self, tmp_path: Path) -> None:
        ws = _workspace(tmp_path, max_write_bytes=1234)
        assert ws.max_write_bytes == ws.file_tools.max_write_bytes == 1234

    def test_advertised_cap_is_the_one_that_rejects(self, tmp_path: Path) -> None:
        ws = _workspace(tmp_path, max_write_bytes=16)
        assert "16" in capability_hint(ws, write=True)
        with pytest.raises(ValueError, match="max_write_bytes"):
            ws.file_tools.write_file("big.txt", "x" * 17)
        assert ws.file_tools.write_file("ok.txt", "x" * 16)["path"] == "ok.txt"


class TestAdvertisedRowsComeFromTheToolModules:
    """#74 review: server.py hardcoded which tools write; `_WRITE` is the source of truth."""

    def test_rows_match_the_exported_capability_set(self, tmp_path: Path) -> None:
        from gemini_bridge.tools import CAPABILITIES

        text = server_instructions(_workspace(tmp_path))
        read_line = next(ln for ln in text.splitlines() if "cannot modify the working tree" in ln)
        write_line = next(ln for ln in text.splitlines() if "write_file" in ln)
        for cap in CAPABILITIES:
            assert cap.name in (write_line if cap.write else read_line)
            assert cap.name not in (read_line if cap.write else write_line)

    def test_artifact_line_matches_the_exported_modes(self, tmp_path: Path) -> None:
        from gemini_bridge.tools import CAPABILITIES

        # "Markdown artifact", not "artifact": pytest derives tmp_path from the test name,
        # so the sandbox-root line can contain the bare word.
        line = next(
            ln
            for ln in server_instructions(_workspace(tmp_path)).splitlines()
            if "Markdown artifact" in ln
        )
        for cap in CAPABILITIES:
            if cap.artifacts == "never":
                assert cap.name not in line
            else:
                assert cap.name in line

    def test_capability_rows_match_each_tools_declared_write_flag(self) -> None:
        """The exported rows must agree with what build_file_registry is actually handed."""
        from gemini_bridge.tools import CAPABILITIES

        assert {c.name for c in CAPABILITIES} == set(GENERATING_TOOLS)
        assert {c.name for c in CAPABILITIES if c.write} == set(WRITE_TOOLS)

    def test_list_models_is_excluded_and_declared_silent(self, tmp_path: Path) -> None:
        """It takes a TranscriptWriter for signature parity but never writes one."""
        text = server_instructions(_workspace(tmp_path))
        assert "gemini_list_models is a metadata call and writes nothing" in text

    def test_no_artifact_promise_without_a_workspace(self) -> None:
        """call_gemini gates artifact saving on workspace is not None, so with no workspace
        the instructions must not promise a file that never appears."""
        text = server_instructions(None)
        assert "artifact" not in text.lower()


def test_skipped_list_excludes_what_the_deny_list_already_blocks(tmp_path: Path) -> None:
    """.git is in both WALK_SKIP_DIRS and DEFAULT_DENY; advertising it as 'readable by path'
    would contradict the deny row two lines above."""
    ws = _workspace(tmp_path)
    text = server_instructions(ws)
    skipped_line = next(ln for ln in text.splitlines() if "skipped by glob and grep" in ln)
    assert ".git," not in skipped_line and not skipped_line.endswith(".git.")
    assert "node_modules" in skipped_line
    assert ws.sandbox.is_denied(tmp_path / ".git")


class TestNoHandWrittenRestatements:
    """#74 review round 3: values the text names must come from what enforces them."""

    def test_read_tool_names_come_from_the_declarations(self, tmp_path: Path) -> None:
        """Adding a read declaration must change the advertised list, not silently diverge."""
        from gemini_bridge.file_tools import READ_TOOL_NAMES

        ws = _workspace(tmp_path)
        registry = ws.registry(write=False)
        assert registry is not None
        assert set(READ_TOOL_NAMES) == {d.name for d in registry.declarations}

        hint = capability_hint(ws, write=False)
        text = server_instructions(ws)
        for name in READ_TOOL_NAMES:
            assert name in hint and name in text

    def test_write_tool_name_comes_from_the_declaration(self, tmp_path: Path) -> None:
        from gemini_bridge.file_tools import READ_TOOL_NAMES, WRITE_TOOL_NAME

        ws = _workspace(tmp_path)
        registry = ws.registry(write=True)
        assert registry is not None
        names = {d.name for d in registry.declarations}
        assert names - set(READ_TOOL_NAMES) == {WRITE_TOOL_NAME}
        assert WRITE_TOOL_NAME in capability_hint(ws, write=True)

    def test_result_caps_are_the_enforced_constants(self, tmp_path: Path) -> None:
        from gemini_bridge import file_tools as ft

        text = server_instructions(_workspace(tmp_path))
        assert str(ft.LIST_MAX_ENTRIES) in text
        assert str(ft.GLOB_MAX_RESULTS) in text
        assert str(ft.GREP_MAX_MATCHES) in text
        assert str(ft.READ_MAX_BYTES // 1024) in text

    def test_an_empty_search_is_declared_inconclusive(self, tmp_path: Path) -> None:
        """Truncation and skipping are silent; a caller must not read 0 results as absence."""
        text = server_instructions(_workspace(tmp_path))
        assert "not as proof" in text
        assert "truncated=true" in text
        assert "an empty result is not proof of absence" in capability_hint(
            _workspace(tmp_path), write=False
        )


class TestInstructionsStayWellFormed:
    """The rows are built from a mutable capability set; the prose must survive any of them."""

    def _always_writes_line(self, caps: object, tmp_path: Path) -> str:
        with patch("gemini_bridge.server.CAPABILITIES", caps):
            text = server_instructions(_workspace(tmp_path))
        return next(ln for ln in text.splitlines() if "Markdown artifact" in ln)

    def test_no_dangling_verb_when_nothing_saves_by_default(self, tmp_path: Path) -> None:
        from gemini_bridge.tools.base import ToolCapability

        caps = (
            ToolCapability("gemini_ask", write=False, artifacts="never"),
            ToolCapability("gemini_review", write=True, artifacts="opt-in"),
        )
        line = self._always_writes_line(caps, tmp_path)
        assert "-  " not in line
        assert "write_artifact=false" not in line  # nothing saves by default
        assert "gemini_review saves the answer" in line

    def test_no_artifact_line_when_no_tool_saves_one(self, tmp_path: Path) -> None:
        from gemini_bridge.tools.base import ToolCapability

        caps = (ToolCapability("gemini_ask", write=False, artifacts="never"),)
        with patch("gemini_bridge.server.CAPABILITIES", caps):
            text = server_instructions(_workspace(tmp_path))
        assert "Markdown artifact" not in text

    def test_skipped_row_reads_as_nothing_when_all_are_denied(self, tmp_path: Path) -> None:
        from gemini_bridge.sandbox import WALK_SKIP_DIRS

        ws = _workspace(
            tmp_path, deny=[f"**/{name}/**" for name in WALK_SKIP_DIRS] + list(WALK_SKIP_DIRS)
        )
        line = next(
            ln for ln in server_instructions(ws).splitlines() if "skipped by glob and grep" in ln
        )
        assert "nothing (all of them are denied)" in line


class TestListModelsAnnotations:
    """The only tool that writes nothing must say so — absent hints mean the opposite."""

    def test_declared_read_only_and_non_destructive(self, tmp_path: Path) -> None:
        annotations = _tools(_server(tmp_path, _workspace(tmp_path)))[
            "gemini_list_models"
        ].annotations
        assert annotations is not None
        assert annotations.readOnlyHint is True
        assert annotations.destructiveHint is False

    def test_every_registered_tool_carries_annotations(self, tmp_path: Path) -> None:
        for name, tool in _tools(_server(tmp_path, _workspace(tmp_path))).items():
            assert tool.annotations is not None, f"{name} has no annotations"
