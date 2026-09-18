"""
gemini_bridge/tools/ask.py
---------------------------
MCP tool: gemini_ask — general-purpose Gemini query.

Responsibilities:
  - Register the gemini_ask MCP tool with the server
  - Handle prompt parameter and optional thinking level
  - Return Gemini's response as a string

Design notes:
  - Single Responsibility: tool registration + prompt routing only; no session/credential logic
  - Open/Closed: changing the system prompt or parameters does not affect other tools
  - System prompt: direct, precise, concrete — no specialized persona

Used by:  tools/__init__.py -> register_ask(), server.py (via tools/__init__)
Imports:  tools/base.py (call_gemini, ThinkingParam), client.py (GeminiClient),
          transcript.py (TranscriptWriter)
"""

from typing import Annotated, Optional

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from gemini_bridge.client import GeminiClient
from gemini_bridge.config import ThinkingLevel
from gemini_bridge.tools.base import ToolResult, call_gemini, model_param_hint
from gemini_bridge.transcript import TranscriptWriter

_SYSTEM_PROMPT = (
    "You are a knowledgeable technical assistant working alongside Claude, another AI. "
    "Answer directly and precisely. Prefer concrete examples. When uncertain, say so."
)

_TOOL_NAME = "gemini_ask"


def register(mcp: FastMCP, client: GeminiClient, transcript: TranscriptWriter) -> None:
    """Register gemini_ask with the MCP server."""
    model_hint = model_param_hint(client)

    @mcp.tool()
    async def gemini_ask(
        prompt: Annotated[str, Field(description="The question or request to send to Gemini")],
        thinking: Annotated[
            Optional[ThinkingLevel],
            Field(
                description="Reasoning depth: none, low, medium, high. Defaults to config setting."
            ),
        ] = None,
        session_name: Annotated[
            str,
            Field(description="Session name for conversation continuity (v1: always 'default')."),
        ] = "default",
        model: Annotated[Optional[str], Field(description=model_hint)] = None,
    ) -> ToolResult:
        """Ask Gemini a general question. Use when no other specialized tool fits."""
        return await call_gemini(
            client=client,
            transcript=transcript,
            tool_name=_TOOL_NAME,
            session_name=session_name,
            system_instruction=_SYSTEM_PROMPT,
            prompt=prompt,
            thinking=thinking,
            model=model,
        )
