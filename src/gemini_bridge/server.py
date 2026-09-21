"""
gemini_bridge/server.py
------------------------
MCP server construction and tool registration.

Responsibilities:
  - Build the FastMCP server instance
  - Accept pre-constructed GeminiClient, TranscriptWriter, and Workspace (injected by __main__.py)
  - Register all 7 tools by calling each tool module's register() function
  - Send the short instructions from guide.py and wire gemini_help to its full detail (#74, #78)
  - Return the configured server instance for running

Design notes:
  - Single Responsibility: server wiring only; no config loading, no credential logic
  - Open/Closed: add a new tool by importing its register() + one function call — nothing else changes
  - Dependency Inversion: depends on GeminiClient and TranscriptWriter abstractions, not concrete init

Raises:
  (none) — startup errors surface from __main__.py where they are caught and reported

Used by:  __main__.py -> build_server()
Imports:  client.py (GeminiClient), guide.py, transcript.py (TranscriptWriter),
          tools/__init__.py, workspace.py (Workspace)
"""

from typing import Optional

from mcp.server.fastmcp import FastMCP

from gemini_bridge.client import GeminiClient
from gemini_bridge.guide import HELP_TOPICS, help_text, server_instructions
from gemini_bridge.tools import (
    register_architect,
    register_ask,
    register_brainstorm,
    register_debug,
    register_help,
    register_list_models,
    register_review,
)
from gemini_bridge.transcript import TranscriptWriter
from gemini_bridge.workspace import Workspace

__all__ = ["build_server", "server_instructions"]

_SERVER_NAME = "gemini-bridge"


def build_server(
    client: GeminiClient,
    transcript: TranscriptWriter,
    workspace: Optional[Workspace] = None,
) -> FastMCP:
    """Construct and return the configured MCP server with all tools registered.

    `workspace` gives the generating tools repo file access; None disables it entirely."""
    mcp = FastMCP(
        _SERVER_NAME,
        instructions=server_instructions(
            workspace, transcript, client.web_default, client.web_supported
        ),
    )
    register_ask(mcp, client, transcript, workspace)
    register_brainstorm(mcp, client, transcript, workspace)
    register_review(mcp, client, transcript, workspace)
    register_debug(mcp, client, transcript, workspace)
    register_architect(mcp, client, transcript, workspace)
    register_list_models(mcp, client, transcript)
    register_help(
        mcp,
        lambda topic: help_text(
            workspace, transcript, client.web_default, client.web_supported, topic=topic
        ),
        HELP_TOPICS,
    )
    return mcp
