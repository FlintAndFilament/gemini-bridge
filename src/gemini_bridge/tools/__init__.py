"""
gemini_bridge/tools/__init__.py
---------------------------------
Tool package root — re-exports all MCP tool registration callables.

Responsibilities:
  - Collect tool modules into a single importable namespace
  - Collect the generating tools' capability rows so server.py can advertise them (#74)

Design notes:
  - Open/Closed: new tools add a module here + one import line — server.py unchanged

Used by:  server.py (registers all tools)
Imports:  tools/ask.py, tools/brainstorm.py, tools/review.py, tools/debug.py, tools/architect.py
"""

from gemini_bridge.tools.architect import CAPABILITY as CAPABILITY_ARCHITECT
from gemini_bridge.tools.architect import register as register_architect
from gemini_bridge.tools.ask import CAPABILITY as CAPABILITY_ASK
from gemini_bridge.tools.ask import register as register_ask
from gemini_bridge.tools.base import ToolCapability
from gemini_bridge.tools.brainstorm import CAPABILITY as CAPABILITY_BRAINSTORM
from gemini_bridge.tools.brainstorm import register as register_brainstorm
from gemini_bridge.tools.debug import CAPABILITY as CAPABILITY_DEBUG
from gemini_bridge.tools.debug import register as register_debug
from gemini_bridge.tools.list_models import LIST_MODELS_TOOL_NAME
from gemini_bridge.tools.list_models import register as register_list_models
from gemini_bridge.tools.review import CAPABILITY as CAPABILITY_REVIEW
from gemini_bridge.tools.review import register as register_review

# Registration order, and the order the capability rows are advertised in.
CAPABILITIES: tuple[ToolCapability, ...] = (
    CAPABILITY_ASK,
    CAPABILITY_BRAINSTORM,
    CAPABILITY_REVIEW,
    CAPABILITY_DEBUG,
    CAPABILITY_ARCHITECT,
)

__all__ = [
    "register_ask",
    "register_brainstorm",
    "register_review",
    "register_debug",
    "register_architect",
    "register_list_models",
    "CAPABILITIES",
    "LIST_MODELS_TOOL_NAME",
]
