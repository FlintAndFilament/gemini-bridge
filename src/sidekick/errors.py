"""
sidekick/errors.py
------------------------
Shared exception types.

Responsibilities:
  - Define ClientError, raised for Gemini inference and session failures

Design notes:
  - Lives in its own module so client.py and tool_loop.py can both raise it without an
    import cycle; client.py re-exports it for existing callers

Used by:  client.py, tool_loop.py, tools/base.py (via client re-export)
"""


class ClientError(Exception):
    """Raised when a Gemini inference or session operation fails."""
