"""
gemini_bridge/transcript.py
----------------------------
Append tool exchanges to a session transcript file in Markdown format.

Responsibilities:
  - Determine the transcript file path from config.transcript_dir and startup timestamp
  - Create the transcript directory if it does not exist, or fail loudly at startup with an
    actionable message when it cannot be created (#87, #94)
  - Append each exchange (tool name, thinking level, session, prompt, tool calls, response)
    as Markdown
  - Fail silently on write errors *after* construction, so a transcript failure never breaks
    a tool call

Design notes:
  - Single Responsibility: transcript writing only; no Gemini calls, no config loading
  - Open/Closed: exchange format is isolated in _format_exchange(); changing format touches one fn
  - Transcript file is opened in append mode per exchange — no persistent file handle needed

Raises:
  TranscriptError — at construction, when the transcript directory cannot be created (#87).
  Write errors after that are caught and logged; tool calls must not fail due to I/O

Used by:  tools/*.py (via append_exchange() after each Gemini response)
Imports:  (stdlib only)
"""

import logging
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Optional

_log = logging.getLogger(__name__)


class TranscriptError(Exception):
    """Raised when the transcript directory cannot be created. Message is user-actionable."""


class TranscriptWriter:
    """Writes tool exchanges to a Markdown transcript file for the current server session."""

    def __init__(self, config_transcript_dir: str, startup_time: datetime) -> None:
        # Startup, unlike append(), fails loudly: a path that cannot be used is a config
        # mistake the user must fix, and an unhandled error here reaches Claude Code as
        # nothing but "server failed to connect" (#87).
        #
        # Resolving is inside the guard too (#94): expanduser() raises RuntimeError — not
        # OSError — for an unknown ~user, and resolve() can raise on a pathological path.
        transcript_dir = Path(config_transcript_dir)
        try:
            transcript_dir = transcript_dir.expanduser().resolve()
            transcript_dir.mkdir(parents=True, exist_ok=True)
        except (OSError, RuntimeError, ValueError) as exc:
            reason = getattr(exc, "strerror", None) or exc
            raise TranscriptError(
                f"Transcript directory cannot be used: {config_transcript_dir} ({reason}).\n"
                "Fix: set transcript_dir in ~/.config/gemini-bridge/config.json to a writable "
                "path, or re-run setup.sh."
            ) from exc
        filename = startup_time.strftime("%Y%m%d-%H%M-gemini-bridge-transcript.md")
        self._path = transcript_dir / filename

    @property
    def path(self) -> Path:
        return self._path

    def append(
        self,
        tool_name: str,
        prompt: str,
        response: str,
        thinking: str,
        session: str = "default",
        timestamp: Optional[datetime] = None,
        tool_calls: Sequence[str] = (),
    ) -> None:
        """Append one exchange to the transcript. Never raises — write errors go to stderr.

        `tool_calls` are pre-rendered lines (one per file-tool call Gemini made)."""
        ts = timestamp or datetime.now()
        block = _format_exchange(tool_name, prompt, response, thinking, session, ts, tool_calls)
        try:
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(block)
        except OSError as exc:
            _log.warning("transcript write failed: %s", exc)


def _format_exchange(
    tool_name: str,
    prompt: str,
    response: str,
    thinking: str,
    session: str,
    timestamp: datetime,
    tool_calls: Sequence[str] = (),
) -> str:
    ts_str = timestamp.strftime("%H:%M:%S")
    calls = ""
    if tool_calls:
        calls = "**Tool calls:**\n" + "".join(f"- {line}\n" for line in tool_calls) + "\n"
    return (
        f"\n## [{ts_str}] {tool_name} — thinking: {thinking} | session: {session}\n\n"
        f"**Prompt:**\n{prompt}\n\n"
        f"{calls}"
        f"**Response:**\n{response}\n\n"
        "---\n"
    )
