"""
gemini_bridge/workspace.py
---------------------------
Composition of everything a generating tool needs to act on the repository.

Responsibilities:
  - Build the Sandbox (rooted at the Claude Code launch directory) and FileTools from config
  - Hand each tool a ToolRegistry matching its capability row (read-only or read+write)
  - Provide the ArtifactStore, with artifacts_dir validated against the sandbox

Design notes:
  - Built once at startup by __main__.py and injected into tool registration; tools never
    construct sandboxes themselves
  - The kill switch (file_tools.enabled=false) yields no registry at all, so no tools are
    declared to Gemini. Artifacts are bridge-written and stay available either way.

Raises:
  SandboxError — artifacts_dir is outside the sandbox root or on the deny-list

Used by:  __main__.py (build_workspace), tools/base.py (Workspace)
Imports:  config.py, sandbox.py, file_tools.py, tool_loop.py, artifacts.py
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from gemini_bridge.artifacts import ArtifactStore
from gemini_bridge.config import Config
from gemini_bridge.file_tools import FileTools, build_file_registry
from gemini_bridge.sandbox import Sandbox
from gemini_bridge.tool_loop import ToolRegistry


@dataclass(frozen=True)
class Workspace:
    sandbox: Sandbox
    file_tools: FileTools
    tools_enabled: bool
    artifacts: ArtifactStore

    def registry(self, *, write: bool) -> Optional[ToolRegistry]:
        """The file tools for one call, or None when the capability is switched off."""
        if not self.tools_enabled:
            return None
        return build_file_registry(self.file_tools, write=write)


def build_workspace(config: Config, cwd: Path) -> Workspace:
    """Build the workspace rooted at `cwd` (the directory Claude Code launched the bridge in)."""
    sandbox = Sandbox(cwd, deny=config.file_tools.deny)
    return Workspace(
        sandbox=sandbox,
        file_tools=FileTools(sandbox, max_write_bytes=config.file_tools.max_write_bytes),
        tools_enabled=config.file_tools.enabled,
        artifacts=ArtifactStore(sandbox.resolve(config.artifacts_dir)),
    )
