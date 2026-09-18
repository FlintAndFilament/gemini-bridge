"""
gemini_bridge/tools/base.py
----------------------------
Shared type definitions and helper for all MCP tools.

Responsibilities:
  - Define ToolResult type alias (return type for all tool handlers)
  - Define ThinkingParam type alias (optional thinking level parameter)
  - Provide call_gemini() helper that combines ask() + transcript append
  - Give Gemini the file tools matching the calling tool's capability row (read / read+write)
  - Save the final answer as an artifact when the calling tool asks for one

Design notes:
  - Interface Segregation: tools import only what they need from here; no server/config exposure
  - Single Responsibility: shared plumbing only — no tool-specific logic lives here
  - Liskov Substitution: all tools return ToolResult; callers are agnostic to which tool ran

Raises:
  (none directly) — call_gemini() surfaces ClientError messages as ToolResult strings

Used by:  tools/ask.py, tools/brainstorm.py, tools/review.py, tools/debug.py, tools/architect.py
Imports:  client.py (GeminiClient), transcript.py (TranscriptWriter), config.py (ThinkingLevel),
          workspace.py (Workspace), tool_loop.py (ToolCallRecord)
"""

import logging
from typing import Optional

from gemini_bridge import models
from gemini_bridge.client import (
    ClientError,
    GeminiClient,
    _is_retryable,
)

_log = logging.getLogger(__name__)
from gemini_bridge.config import ThinkingLevel
from gemini_bridge.tool_loop import ToolCallRecord
from gemini_bridge.transcript import TranscriptWriter
from gemini_bridge.workspace import Workspace

# Return type for all MCP tool handlers.
ToolResult = str

# Optional thinking level parameter type accepted by every tool.
ThinkingParam = Optional[ThinkingLevel]

_READ_PREAMBLE = (
    "\n\nYou can inspect the repository under discussion with the tools list_dir, glob, grep, "
    "and read_file. Paths are relative to the repository root. Read the relevant files before "
    "making claims about them, and prefer one tool call at a time."
)
_WRITE_PREAMBLE = (
    " You can also create or overwrite files with write_file; use it only when the request "
    "explicitly calls for writing a file."
)
_ARTIFACT_PREAMBLE = (
    " Your final answer is saved to a file automatically — do not write it out with write_file."
)


def model_param_hint(client: GeminiClient) -> str:
    """Backend-aware description for the `model` param, computed once at tool registration.

    api_key mode surfaces the Developer-API shortlist (incl. '-latest' aliases); Vertex
    modes surface the alias-free Vertex shortlist. Injected into each tool's Annotated[...]
    so the hint can never contradict the active backend.
    """
    return models.schema_hint(
        models.backend_for(client.auth_method), client.default_model, client.resolved_latest
    )


async def call_gemini(
    client: GeminiClient,
    transcript: TranscriptWriter,
    tool_name: str,
    session_name: str,
    system_instruction: str,
    prompt: str,
    thinking: ThinkingParam,
    model: Optional[str] = None,
    workspace: Optional[Workspace] = None,
    write: bool = False,
    artifact_topic: Optional[str] = None,
) -> ToolResult:
    """Get a session, call ask(), log to transcript, return response or error string.

    With a workspace, Gemini gets the read file tools (plus write_file when `write`) unless the
    file_tools kill switch is off. Every tool call is recorded in the transcript — including
    calls made before a failure. With `artifact_topic` (and a workspace), the final answer is
    saved as an artifact and its path appended to the reply; a failed save adds a notice but
    never loses the answer.

    If the requested model returns a terminal overload error (503/429 after retries),
    automatically retries once against the fallback model (newest Flash-Lite) and prefixes the response with a
    visible notice so Claude and the user know a substitution occurred.
    """
    effective_thinking: ThinkingLevel = thinking or client.default_thinking
    _log.debug(
        "%s session=%r model=%s thinking=%s",
        tool_name,
        session_name,
        model or "default",
        effective_thinking,
    )

    registry = workspace.registry(write=write) if workspace else None
    saving = workspace is not None and artifact_topic is not None
    if registry:
        system_instruction += _READ_PREAMBLE + (_WRITE_PREAMBLE if write else "")
        if write and saving:
            system_instruction += _ARTIFACT_PREAMBLE
    records: list[ToolCallRecord] = []

    async def _do_ask(use_model: Optional[str]) -> str:
        session = client.get_or_create_session(name=f"{tool_name}:{session_name}", model=use_model)
        return await client.ask(
            session,
            prompt,
            thinking,
            system_instruction=system_instruction,
            registry=registry,
            records=records,
        )

    fallback_at: Optional[int] = None  # records index where the fallback attempt began

    def _tool_lines() -> list[str]:
        lines = [r.render() for r in records]
        if fallback_at is not None:
            lines.insert(
                fallback_at,
                f"— {requested_model} unavailable; retried on fallback model {fallback_model} —",
            )
        return lines

    def _log_failure(message: str) -> ToolResult:
        if records:
            transcript.append(
                tool_name=tool_name,
                prompt=prompt,
                response=message,
                thinking=effective_thinking,
                session=session_name,
                tool_calls=_tool_lines(),
            )
        return message

    fallback_notice: Optional[str] = None
    requested_model = client.resolve_model(model)
    fallback_model = client.fallback_model
    answered_by = requested_model
    try:
        response = await _do_ask(model)
    except ClientError as exc:
        # If retryable and the model we tried is not already the fallback, retry once on it.
        # `model or client.default_model` reflects the model actually used — when the caller
        # omits `model`, the effective default (not the fallback) was tried, so fallback applies.
        requested = requested_model
        wrote = any(r.name == "write_file" and r.ok for r in records)
        if _is_retryable(exc) and requested != fallback_model and wrote:
            # Replaying the loop on another model would repeat writes that already happened.
            _log.error(
                "%s: %r overloaded after writing files — not falling back", tool_name, requested
            )
            return _log_failure(
                f"[gemini-bridge error] {exc} (not retried on {fallback_model}: files were "
                "already written this call — see the transcript)"
            )
        if _is_retryable(exc) and requested != fallback_model:
            _log.warning(
                "%s: model %r overloaded — falling back to %r",
                tool_name,
                requested,
                fallback_model,
            )
            fallback_at = len(records)
            try:
                response = await _do_ask(fallback_model)
                answered_by = fallback_model
                fallback_notice = (
                    f"[gemini-bridge notice] Model '{requested}' was unavailable (503/429); "
                    f"this response was generated by fallback model '{fallback_model}'.\n\n"
                )
            except ClientError as fallback_exc:
                _log.error("%s fallback also failed: %s", tool_name, fallback_exc)
                return _log_failure(
                    f"[gemini-bridge error] {exc} (fallback also failed: {fallback_exc})"
                )
        else:
            _log.error("%s session=%r failed: %s", tool_name, session_name, exc)
            return _log_failure(f"[gemini-bridge error] {exc}")

    _log.debug("%s session=%r OK", tool_name, session_name)
    transcript.append(
        tool_name=tool_name,
        prompt=prompt,
        response=response,
        thinking=effective_thinking,
        session=session_name,
        tool_calls=_tool_lines(),
    )
    if saving:
        assert workspace is not None and artifact_topic is not None
        try:
            path = workspace.artifacts.save(
                tool_name, artifact_topic, response, model=answered_by, session=session_name
            )
            response += f"\n\n[gemini-bridge] artifact saved: {workspace.sandbox.relative(path)}"
        except OSError as exc:
            _log.error("%s: artifact not saved: %s", tool_name, exc)
            response += f"\n\n[gemini-bridge notice] artifact not saved: {exc}"
    if fallback_notice:
        return f"{fallback_notice}{response}"
    return response
