"""
gemini_bridge/server.py
------------------------
MCP server construction and tool registration.

Responsibilities:
  - Build the FastMCP server instance
  - Accept pre-constructed GeminiClient, TranscriptWriter, and Workspace (injected by __main__.py)
  - Register all 6 tools by calling each tool module's register() function
  - Advertise the workspace's file-tool capability to the client as server instructions (#74)
  - Return the configured server instance for running

Design notes:
  - Single Responsibility: server wiring only; no config loading, no credential logic
  - Open/Closed: add a new tool by importing its register() + one function call — nothing else changes
  - Dependency Inversion: depends on GeminiClient and TranscriptWriter abstractions, not concrete init

Raises:
  (none) — startup errors surface from __main__.py where they are caught and reported

Used by:  __main__.py -> build_server()
Imports:  client.py (GeminiClient), transcript.py (TranscriptWriter), tools/__init__.py,
          workspace.py (Workspace)
"""

from collections.abc import Sequence
from typing import Optional

from mcp.server.fastmcp import FastMCP

from gemini_bridge.client import GeminiClient
from gemini_bridge.file_tools import (
    GLOB_MAX_RESULTS,
    GREP_MAX_MATCHES,
    GREP_TIMEOUT_SECONDS,
    LIST_MAX_ENTRIES,
    READ_MAX_BYTES,
    READ_TOOL_NAMES,
)
from gemini_bridge.sandbox import WALK_SKIP_DIRS
from gemini_bridge.tools import (
    CAPABILITIES,
    LIST_MODELS_TOOL_NAME,
    register_architect,
    register_ask,
    register_brainstorm,
    register_debug,
    register_list_models,
    register_review,
)
from gemini_bridge.transcript import TranscriptWriter
from gemini_bridge.workspace import Workspace

_SERVER_NAME = "gemini-bridge"

_PURPOSE = (
    "gemini-bridge gives Claude a second opinion from Google Gemini. Five generating tools "
    "({generating}) each hold their own conversation session; {list_models} reports the models "
    "available live on the active backend."
)

_NO_ACCESS = (
    "Repository access is OFF{reason}: Gemini cannot read your files and sees only what you put "
    "in the call, so paste the code or context it needs. Gemini itself cannot write anything."
)

_ACCESS_HEAD = (
    "Repository access is ON. Gemini reads the repository itself, so name paths instead of "
    "pasting file contents — this keeps your own context free for the answer.\n\n"
    "- Sandbox root: {root} — nothing outside it is reachable, at any depth.\n"
    "- Read tools Gemini can call: {read_tools}.\n"
    "- Denied at any depth: {deny}.\n"
    "- Readable by path, but skipped by glob and grep: {skipped}. glob and grep also never "
    "follow symlinked directories, so a search can miss files that read_file still reaches.\n"
    "- Results are capped and flagged with truncated=true when they hit a limit: {caps}. grep "
    f"also gives up after {GREP_TIMEOUT_SECONDS:.0f}s with timed_out=true, and read_file "
    "refuses binary files. Treat an empty or truncated result as inconclusive, not as proof "
    "that something is absent.\n"
)


def _sentence(names: Sequence[str]) -> str:
    """'a', 'a and b', 'a, b and c' — for rows built from the capability set."""
    names = list(names)
    if len(names) <= 1:
        return names[0] if names else ""
    return ", ".join(names[:-1]) + " and " + names[-1]


def _readable_but_skipped(workspace: Workspace) -> str:
    """Walk-skipped directories that are not already denied — .git is both, and deny wins."""
    names = sorted(
        name
        for name in WALK_SKIP_DIRS
        if not workspace.sandbox.is_denied(workspace.sandbox.root / name)
    )
    return ", ".join(names)


def _access_rows(workspace: Workspace) -> str:
    """The read/write rows, built from each tool module's declared capability (#74)."""
    read_only = _sentence([c.name for c in CAPABILITIES if not c.write])
    writers = _sentence([c.name for c in CAPABILITIES if c.write])
    rows = _ACCESS_HEAD.format(
        root=workspace.sandbox.root,
        read_tools=", ".join(READ_TOOL_NAMES),
        deny=", ".join(workspace.sandbox.deny) or "nothing (the deny-list is empty)",
        skipped=_readable_but_skipped(workspace) or "nothing (all of them are denied)",
        caps=(
            f"{LIST_MAX_ENTRIES} entries per list_dir, {GLOB_MAX_RESULTS} paths per glob, "
            f"{GREP_MAX_MATCHES} matches per grep, {READ_MAX_BYTES // 1024} KiB per read_file"
        ),
    )
    if read_only:
        rows += f"- Gemini cannot modify the working tree on: {read_only}.\n"
    if writers:
        rows += (
            "- Gemini MAY create or overwrite files under the root with write_file (up to "
            f"{workspace.max_write_bytes} bytes each; it can never delete, rename, or execute) "
            f"on: {writers}. Review their output before relying on it.\n"
        )
    return rows.rstrip()


def _always_writes(transcript_path: str, artifacts_dir: Optional[str]) -> str:
    """What reaches disk regardless of the repository-access setting."""
    generating = _sentence([c.name for c in CAPABILITIES])
    lines = [
        "What this server writes to disk, whatever the repository-access setting above says:",
        "",
        f"- Every call to {generating} appends the exchange to the session transcript at "
        f"{transcript_path}. {LIST_MODELS_TOOL_NAME} is a metadata call and writes nothing.",
    ]
    by_default = _sentence([c.name for c in CAPABILITIES if c.artifacts == "default"])
    opt_in = _sentence([c.name for c in CAPABILITIES if c.artifacts == "opt-in"])
    if artifacts_dir and (by_default or opt_in):
        clauses = []
        if by_default:
            clauses.append(
                f"{by_default} save their answer as a Markdown artifact under {artifacts_dir} "
                "unless called with write_artifact=false"
            )
        if opt_in:
            where = "" if by_default else f" under {artifacts_dir}"
            clauses.append(
                f"{opt_in} saves the answer as a Markdown artifact{where} only when called with "
                "write_artifact=true"
            )
        lines.append(
            "- "
            + "; ".join(clauses)
            + ". These are written by the bridge, not by Gemini, are always new files (never an "
            "overwrite), and happen even when repository access is off."
        )
    return "\n".join(lines)


def server_instructions(
    workspace: Optional[Workspace], transcript: Optional[TranscriptWriter] = None
) -> str:
    """The instructions string the MCP client sees on connect (#74).

    Every row is derived from what enforces it — the capability set exported by the tool
    modules, the live deny-list, the walk-skip set, and the workspace's write cap — so the
    advertised capability cannot drift from the one actually wired up. The caller learns the
    sandbox root, everything that reaches disk on any call, which calls let Gemini write, and
    why file access is off when it is.
    """
    transcript_path = str(transcript.path) if transcript else "the configured transcript directory"
    if workspace is None:
        access = _NO_ACCESS.format(reason="")
        artifacts_dir = None  # no workspace means call_gemini never saves one
    elif not workspace.tools_enabled:
        access = _NO_ACCESS.format(reason=f" ({workspace.disabled_reason})")
        artifacts_dir = str(workspace.artifacts.directory)
    else:
        access = _access_rows(workspace)
        artifacts_dir = str(workspace.artifacts.directory)
    purpose = _PURPOSE.format(
        generating=_sentence([c.name for c in CAPABILITIES]),
        list_models=LIST_MODELS_TOOL_NAME,
    )
    return f"{purpose}\n\n{access}\n\n{_always_writes(transcript_path, artifacts_dir)}"


def build_server(
    client: GeminiClient,
    transcript: TranscriptWriter,
    workspace: Optional[Workspace] = None,
) -> FastMCP:
    """Construct and return the configured MCP server with all tools registered.

    `workspace` gives the generating tools repo file access; None disables it entirely."""
    mcp = FastMCP(_SERVER_NAME, instructions=server_instructions(workspace, transcript))
    register_ask(mcp, client, transcript, workspace)
    register_brainstorm(mcp, client, transcript, workspace)
    register_review(mcp, client, transcript, workspace)
    register_debug(mcp, client, transcript, workspace)
    register_architect(mcp, client, transcript, workspace)
    register_list_models(mcp, client, transcript)
    return mcp
