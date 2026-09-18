"""
gemini_bridge/tools/brainstorm.py
----------------------------------
MCP tool: gemini_brainstorm — divergent ideation and devil's advocate thinking.

Responsibilities:
  - Register the gemini_brainstorm MCP tool with the server
  - Accept topic + optional context and thinking level
  - Return Gemini's divergent, challenge-first brainstorming response

Design notes:
  - Single Responsibility: tool registration + brainstorm persona only
  - Open/Closed: system prompt changes do not affect other tools
  - System prompt: unconventional, challenges current direction, plays devil's advocate

Used by:  tools/__init__.py -> register_brainstorm(), server.py (via tools/__init__)
Imports:  tools/base.py (call_gemini), client.py (GeminiClient), transcript.py (TranscriptWriter)
"""

from typing import Annotated, Optional

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from gemini_bridge.client import GeminiClient
from gemini_bridge.config import ThinkingLevel
from gemini_bridge.tools.base import ToolResult, call_gemini, model_param_hint
from gemini_bridge.transcript import TranscriptWriter

_SYSTEM_PROMPT = (
    "You are a creative thinking partner working alongside Claude, another AI. "
    "Push unconventional approaches. Challenge Claude's existing direction. "
    "Play devil's advocate when useful. Offer alternatives even when the current path seems fine. "
    "Be concise."
)

_TOOL_NAME = "gemini_brainstorm"


def register(mcp: FastMCP, client: GeminiClient, transcript: TranscriptWriter) -> None:
    """Register gemini_brainstorm with the MCP server."""
    model_hint = model_param_hint(client)

    @mcp.tool()
    async def gemini_brainstorm(
        topic: Annotated[str, Field(description="The topic or problem to brainstorm about")],
        context: Annotated[
            str,
            Field(
                description="Optional context: what Claude is currently doing or has already considered."
            ),
        ] = "",
        thinking: Annotated[
            Optional[ThinkingLevel],
            Field(
                description="Reasoning depth: none, low, medium, high. Defaults to config setting."
            ),
        ] = None,
        session_name: Annotated[
            str, Field(description="Session name for conversation continuity.")
        ] = "default",
        model: Annotated[Optional[str], Field(description=model_hint)] = None,
    ) -> ToolResult:
        """Ask Gemini for unconventional ideas and alternatives. Gemini will challenge the
        current direction and play devil's advocate."""
        full_prompt = topic if not context else f"{topic}\n\nContext: {context}"
        return await call_gemini(
            client=client,
            transcript=transcript,
            tool_name=_TOOL_NAME,
            session_name=session_name,
            system_instruction=_SYSTEM_PROMPT,
            prompt=full_prompt,
            thinking=thinking,
            model=model,
        )
