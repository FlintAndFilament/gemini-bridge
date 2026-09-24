"""
sidekick/__init__.py
--------------------------
Package root for sidekick MCP server.

Responsibilities:
  - Expose package version
  - Nothing else — import side-effects belong in __main__.py

Design notes:
  - Single Responsibility: package identity only

Used by:  pyproject.toml (package discovery)
Imports:  (none)
"""

__version__ = "26.7.1"
