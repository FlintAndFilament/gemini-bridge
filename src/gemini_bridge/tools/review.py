"""
gemini_bridge/tools/review.py
------------------------------
MCP tool: gemini_review — critical code and design review.

Responsibilities:
  - Register the gemini_review MCP tool with the server
  - Accept content (code/design/plan) + optional focused question and thinking level
  - Return Gemini's critical, severity-prioritized review

Design notes:
  - Single Responsibility: tool registration + review persona only
  - Open/Closed: system prompt changes do not affect other tools
  - System prompt: pessimistic, rigorous — finds problems and prioritizes by severity

Used by:  tools/__init__.py -> register_review(), server.py (via tools/__init__)
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
    "You are a critical technical reviewer working alongside Claude, another AI. "
    "Find problems, risks, and weaknesses in code, designs, and plans. "
    "Be direct. Don't soften feedback. Prioritize by severity. "
    "If something is sound, say so briefly and move on."
)

_TOOL_NAME = "gemini_review"


def register(mcp: FastMCP, client: GeminiClient, transcript: TranscriptWriter) -> None:
    """Register gemini_review with the MCP server."""
    model_hint = model_param_hint(client)

    @mcp.tool()
    async def gemini_review(
        content: Annotated[str, Field(description="The code, design, or plan to review")],
        question: Annotated[
            str,
            Field(description="Optional specific question to focus the review on."),
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
        """Ask Gemini to critically review code, a design, or a plan. Gemini will find
        problems and prioritize by severity."""
        full_prompt = content if not question else f"{content}\n\nFocus: {question}"
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
