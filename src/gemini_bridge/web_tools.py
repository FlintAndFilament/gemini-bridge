"""
gemini_bridge/web_tools.py
---------------------------
Gemini's server-side web tools, and the rule that keeps them away from write access.

Responsibilities:
  - Build the built-in tool set (google_search + url_context) attached to a request
  - Own resolve_capabilities(): the single place deciding whether a call gets web, write, both
    or neither — it is never both (#76 D4)

Design notes:
  - Unlike file_tools.py, nothing here executes anything. The Gemini API runs these tools
    server-side; the bridge only decides whether to attach them. So there is no class, no
    handler and no sandbox — a WebTools type mirroring FileTools would be empty ceremony.
  - Single Responsibility: policy and construction only. Callers apply the result.
  - resolve_capabilities is deliberately pure and total, so the invariant can be tested
    exhaustively over its whole input space rather than sampled through the call path.

Raises:
  (none) — pure functions over plain values

Used by:  tools/base.py (call_gemini, capability_hint), client.py (build_config),
          server.py (instructions)
Imports:  google-genai types
"""

from dataclasses import dataclass
from typing import Optional

from google.genai import types

# What Gemini can reach when web access is on. Named for the advertised text and the docs.
WEB_TOOL_NAMES: tuple[str, ...] = ("google_search", "url_context")


@dataclass(frozen=True)
class Capabilities:
    """What one call actually gets, after the exclusion rule."""

    web: bool
    write: bool


def resolve_capabilities(*, write: bool, requested: Optional[bool], default: bool) -> Capabilities:
    """Decide a call's web and write capabilities. They are never both true (#76 D4).

    `write` is the tool's own capability row (#68); `requested` is the call's `web` argument,
    where None means "use the configured default"; `default` is `web_tools.enabled`.

    Web wins over write when both would apply. The caller asked for web on this specific call —
    explicitly, or by configuring it as the default — and honouring that while silently keeping
    write would leave attacker-controlled text in a call that can edit the repository, which is
    the whole risk this rule exists to remove. The workflow for wanting both is two calls.
    """
    web = default if requested is None else requested
    return Capabilities(web=web, write=write and not web)


def web_tool_set() -> list[types.Tool]:
    """The built-in tools attached when web access is on.

    Both ride one flag: splitting search from fetch would be two switches for one trust
    decision. Other built-ins the SDK offers (code_execution, mcp_servers, exa_ai_search,
    file_search) are deliberately left out — one trust decision at a time.
    """
    return [
        types.Tool(google_search=types.GoogleSearch()),
        types.Tool(url_context=types.UrlContext()),
    ]
