"""
gemini_bridge/artifacts.py
---------------------------
Persist a tool's final answer as a timestamped Markdown file.

Responsibilities:
  - Name artifacts <YYYYMMDD-HHMM>-<tool>-<slug>.md, adding -2, -3 … instead of overwriting
  - Write a small header (tool, model, session, time) followed by the answer

Design notes:
  - Written by the bridge, not by Gemini: the deliverable exists whether or not the model
    decides to call write_file, and survives context limits, compaction, /clear, new sessions
  - Files are created with exclusive-create mode, so concurrent saves never clobber each other
  - The directory is validated against the sandbox once at startup (workspace.py)

Used by:  workspace.py, tools/base.py
Imports:  stdlib only
"""

import re
import unicodedata
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

_MAX_COLLISIONS = 1000


def slugify(text: str, max_words: int = 6, max_len: int = 48) -> str:
    """Lowercase ASCII kebab-case of the first `max_words` words; 'untitled' if nothing is left."""
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    words = re.findall(r"[a-z0-9]+", ascii_text.lower())[:max_words]
    slug = "-".join(words)[:max_len].rstrip("-")
    return slug or "untitled"


class ArtifactStore:
    """Saves tool answers under one directory."""

    def __init__(self, directory: Path, clock: Callable[[], datetime] = datetime.now) -> None:
        self.directory = directory
        self._clock = clock

    def save(self, tool_name: str, topic: str, content: str, *, model: str, session: str) -> Path:
        """Write the artifact and return its path. Raises OSError on failure."""
        now = self._clock()
        stem = f"{now:%Y%m%d-%H%M}-{tool_name.replace('_', '-')}-{slugify(topic)}"
        title = topic.strip().splitlines()[0][:80] if topic.strip() else "untitled"
        body = (
            f"# {tool_name} — {title}\n\n"
            f"- tool: {tool_name}\n"
            f"- model: {model}\n"
            f"- session: {session}\n"
            f"- saved: {now:%Y-%m-%d %H:%M}\n\n"
            "---\n\n"
            f"{content.rstrip()}\n"
        )
        self.directory.mkdir(parents=True, exist_ok=True)
        for n in range(1, _MAX_COLLISIONS + 1):
            path = self.directory / (f"{stem}.md" if n == 1 else f"{stem}-{n}.md")
            try:
                with path.open("x", encoding="utf-8") as fh:
                    fh.write(body)
                return path
            except FileExistsError:
                continue
        raise OSError(f"too many artifacts named {stem}*.md")
