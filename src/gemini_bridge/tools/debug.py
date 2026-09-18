"""
gemini_bridge/tools/debug.py
-----------------------------
MCP tool: gemini_debug — hypothesis generation for bugs and failures.

Responsibilities:
  - Register the gemini_debug MCP tool with the server
  - Accept error description + optional context and thinking level
  - Return Gemini's evidence-driven root cause hypotheses and diagnostic steps

Design notes:
  - Single Responsibility: tool registration + debug persona only
  - Open/Closed: system prompt changes do not affect other tools
  - System prompt: evidence-based, not speculative — generates hypotheses from what's shown

Used by:  tools/__init__.py -> register_debug(), server.py (via tools/__init__)
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
    "You are a systematic debugging assistant working alongside Claude, another AI. "
    "Generate root cause hypotheses from the evidence provided. Reason through failure modes. "
    "Suggest specific diagnostic steps. Don't guess without basis — reason from what's shown."
)

_TOOL_NAME = "gemini_debug"


def register(mcp: FastMCP, client: GeminiClient, transcript: TranscriptWriter) -> None:
    """Register gemini_debug with the MCP server."""
    model_hint = model_param_hint(client)

    @mcp.tool()
    async def gemini_debug(
        error: Annotated[
            str, Field(description="The error message, stack trace, or failure description")
        ],
        context: Annotated[
            str,
            Field(
                description="Optional context: relevant code, recent changes, environment details."
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
        """Ask Gemini for root cause hypotheses and diagnostic steps. Provide the error
        and any relevant context (code, recent changes, environment)."""
        full_prompt = error if not context else f"{error}\n\nContext:\n{context}"
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
