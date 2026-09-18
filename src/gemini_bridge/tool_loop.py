"""
gemini_bridge/tool_loop.py
---------------------------
The bridge's own function-calling loop and the registry of tools Gemini may call.

Responsibilities:
  - ToolRegistry: map tool names to declarations + async handlers; dispatch without raising
  - ToolCallRecord: one executed call, rendered as a transcript line

Design notes:
  - Open/Closed: a tool source (file tools today, MCP servers in #70) only needs to add
    (declaration, handler) pairs — the loop never changes for a new source
  - dispatch() never raises: failures become {"error": ...} results returned to Gemini so the
    model can correct itself instead of the whole call failing

Used by:  file_tools.py (registry source), client.py (loop), tools/base.py (records)
Imports:  google-genai types
"""

import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from google.genai import types

_log = logging.getLogger(__name__)

Handler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]

_ARG_PREVIEW_CHARS = 60


@dataclass(frozen=True)
class ToolCallRecord:
    """One tool call Gemini made and how it went — rendered into the transcript."""

    name: str
    args: dict[str, Any]
    ok: bool
    summary: str

    def render(self) -> str:
        args = ", ".join(f"{k}={_preview(v)}" for k, v in self.args.items())
        mark = "→" if self.ok else "✗"
        return f"{mark} {self.name}({args}) → {self.summary}"


def _preview(value: Any) -> str:
    text = repr(value)
    if len(text) > _ARG_PREVIEW_CHARS:
        return text[: _ARG_PREVIEW_CHARS - 1] + "…"
    return text


def summarize(result: dict[str, Any]) -> tuple[bool, str]:
    """(ok, short summary) for a handler result: the error text, or the result's size."""
    if "error" in result:
        return False, str(result["error"])
    size = len(json.dumps(result, ensure_ascii=False).encode())
    return True, f"{size / 1024:.1f} KiB" if size >= 1024 else f"{size} B"


class ToolRegistry:
    """Named tools Gemini may call during one request."""

    def __init__(self) -> None:
        self._tools: dict[str, tuple[types.FunctionDeclaration, Handler]] = {}

    def add(self, declaration: types.FunctionDeclaration, handler: Handler) -> None:
        name = declaration.name
        if not name:
            raise ValueError("tool declaration has no name")
        if name in self._tools:
            raise ValueError(f"duplicate tool name: {name}")
        self._tools[name] = (declaration, handler)

    @property
    def declarations(self) -> list[types.FunctionDeclaration]:
        return [decl for decl, _ in self._tools.values()]

    def __len__(self) -> int:
        return len(self._tools)

    async def dispatch(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        """Run a tool by name. Never raises — unknown tools and failures return {"error": ...}."""
        entry = self._tools.get(name)
        if entry is None:
            return {"error": f"unknown tool: {name}"}
        try:
            return await entry[1](args)
        except Exception as exc:
            _log.info("tool %s failed: %s: %s", name, type(exc).__name__, exc)
            return {"error": f"{type(exc).__name__}: {exc}"}
