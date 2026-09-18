"""
gemini_bridge/sandbox.py
-------------------------
Path confinement for every file operation Gemini requests.

Responsibilities:
  - Resolve a model-supplied path to an absolute path inside the sandbox root, or reject it
  - Enforce the deny-list (secrets, .git) on both the requested and the resolved path
  - Walk directories without leaving the root, following symlinked dirs, or entering noise dirs

Design notes:
  - Gemini never touches disk; the bridge executes its tool calls locally. This module is the
    only guard between a model-requested path and the filesystem — there is no provider sandbox
    behind it. Keep it small, pure (no reads/writes of file contents), and heavily tested.
  - Every check runs on the fully resolved path (symlinks followed, '..' collapsed), which
    blocks traversal, absolute paths outside root, and symlink escape with one comparison.
  - Deny patterns match case-insensitively (macOS volumes are usually case-insensitive) against
    the path and every ancestor, so '.git/**' also covers '.git' itself.
  - resolve_for_write() does not follow a symlink at the final component: the caller replaces
    the link atomically instead of writing through it.

Raises:
  SandboxError — path escapes the root, matches the deny-list, or is otherwise unusable

Used by:  file_tools.py, workspace.py
Imports:  stdlib only
"""

import fnmatch
import os
from collections.abc import Iterator, Sequence
from pathlib import Path

DEFAULT_DENY: tuple[str, ...] = (
    ".git/**",
    ".env",
    ".env.*",
    "**/*.pem",
    "**/*.key",
    "**/id_rsa*",
    "**/*credentials*.json",
    "**/*-sa-key.json",
)

# Not traversed by list/glob/grep walks (noise, not secrets) — still readable by direct path.
WALK_SKIP_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        "dist",
        "build",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
    }
)


class SandboxError(Exception):
    """Raised when a requested path is outside the sandbox or denied."""


def _pattern_matches(rel: str, pattern: str) -> bool:
    """Match one lowercase root-relative posix path (no ancestors) against one pattern."""
    pat = pattern.lower()
    while pat.startswith("**/"):
        pat = pat[3:]
    if pat.endswith("/**"):
        prefix = pat[:-3]
        return rel == prefix or fnmatch.fnmatchcase(rel, prefix)
    if "/" not in pat:
        return fnmatch.fnmatchcase(rel.rsplit("/", 1)[-1], pat)
    return fnmatch.fnmatchcase(rel, pat)


class Sandbox:
    """Confines paths to `root` and applies the deny-list."""

    def __init__(self, root: Path, deny: Sequence[str] = DEFAULT_DENY) -> None:
        self.root = root.resolve()
        self._deny = tuple(deny)

    def relative(self, path: Path) -> str:
        """Root-relative posix path; '.' for the root itself."""
        rel = path.relative_to(self.root).as_posix()
        return rel or "."

    def is_denied(self, path: Path) -> bool:
        """True if `path` (absolute, inside root) or any of its ancestors matches the deny-list."""
        parts = path.relative_to(self.root).parts
        for i in range(1, len(parts) + 1):
            rel = "/".join(parts[:i]).lower()
            if any(_pattern_matches(rel, pat) for pat in self._deny):
                return True
        return False

    def _inside(self, path: Path) -> bool:
        return path == self.root or path.is_relative_to(self.root)

    def _lexical(self, path: str) -> Path:
        if not path:
            raise SandboxError("empty path")
        if "\x00" in path:
            raise SandboxError("path contains a NUL byte")
        p = Path(path)
        lexical = Path(os.path.normpath(p if p.is_absolute() else self.root / p))
        if not self._inside(lexical):
            raise SandboxError(f"path escapes repo root: {path}")
        return lexical

    def _check(self, path: Path, requested: str) -> None:
        if not self._inside(path):
            raise SandboxError(f"path escapes repo root: {requested}")
        if self.is_denied(path):
            raise SandboxError(f"path is denied by policy: {requested}")

    def resolve(self, path: str) -> Path:
        """Resolve a path for reading/listing. Symlinks are followed; the result must stay inside
        root and neither the requested nor the resolved path may be denied."""
        lexical = self._lexical(path)
        self._check(lexical, path)
        resolved = lexical.resolve()
        self._check(resolved, path)
        return resolved

    def resolve_for_write(self, path: str) -> Path:
        """Resolve a path for writing. The parent directory is resolved (and must stay inside
        root); the final component is NOT followed, so a planted symlink gets replaced rather
        than written through. Existing directories and the root itself are rejected."""
        lexical = self._lexical(path)
        if lexical == self.root:
            raise SandboxError(f"not a file path: {path}")
        self._check(lexical, path)
        parent = lexical.parent.resolve()
        self._check(parent, path)
        target = parent / lexical.name
        self._check(target, path)
        if target.is_dir() and not target.is_symlink():
            raise SandboxError(f"path is a directory: {path}")
        return target

    def walk(self, start: Path) -> Iterator[Path]:
        """Yield allowed files under `start` (absolute, inside root), sorted by relative path.

        Skips WALK_SKIP_DIRS and denied paths, never descends into symlinked directories, and
        drops file symlinks whose target leaves the root or is denied."""
        if start.is_file():
            if self._allowed_file(start):
                yield start
            return
        found: list[Path] = []
        for dirpath, dirnames, filenames in os.walk(start, followlinks=False):
            here = Path(dirpath)
            dirnames[:] = [
                d
                for d in dirnames
                if d not in WALK_SKIP_DIRS
                and not (here / d).is_symlink()
                and not self.is_denied(here / d)
            ]
            for name in filenames:
                candidate = here / name
                if self._allowed_file(candidate):
                    found.append(candidate)
        yield from sorted(found, key=self.relative)

    def _allowed_file(self, path: Path) -> bool:
        if self.is_denied(path):
            return False
        if path.is_symlink():
            target = path.resolve()
            return self._inside(target) and not self.is_denied(target) and target.is_file()
        return True
