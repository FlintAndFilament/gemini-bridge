"""
gemini_bridge/tools/help.py
-----------------------------
MCP tool: gemini_help — the full detail behind the short server instructions (#78).

Responsibilities:
  - Register the gemini_help MCP tool
  - Return one help topic, or all of them, rendered by the injected callable

Design notes:
  - Claude Code truncates server instructions at about 2048 characters, so the instructions
    are an overview and this tool is the "--help" behind them
  - Dependency Inversion: takes a render callable and the topic names rather than importing
    guide.py, which itself imports this package — no import cycle, and the text stays in
    one place
  - Writes nothing and calls nothing remote: no session, no transcript, no backend

Used by:  tools/__init__.py -> register_help(), server.py
Imports:  (FastMCP and pydantic only)
"""

from collections.abc import Callable, Sequence
from typing import Annotated, Optional

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

HELP_TOOL_NAME = "gemini_help"


def register(
    mcp: FastMCP,
    render: Callable[[Optional[str]], str],
    topics: Sequence[str],
) -> None:
    """Register gemini_help. `render(topic)` returns one topic, or every topic for None."""

    @mcp.tool(
        name=HELP_TOOL_NAME,
        description=(
            "Full detail on gemini-bridge, beyond the short server instructions: file access "
            "limits and deny-list, web rules, sessions, what reaches disk, and each tool's "
            "capabilities. Topics: " + ", ".join(topics) + ". Omit topic for everything. "
            "Writes nothing."
        ),
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    def gemini_help(
        topic: Annotated[
            Optional[str],
            Field(description="One of: " + ", ".join(topics) + ". Omit for all topics."),
        ] = None,
    ) -> str:
        return render(topic)
