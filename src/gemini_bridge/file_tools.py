"""
gemini_bridge/file_tools.py
----------------------------
The file tools Gemini may call: list_dir, glob, grep, read_file, write_file.

Responsibilities:
  - Implement each operation on top of Sandbox, with hard result/size caps
  - Publish the operations to a ToolRegistry (read-only or read+write)

Design notes:
  - Every model-supplied path goes through Sandbox.resolve*/walk — nothing here opens a path
    the sandbox has not approved
  - grep is pure-Python regex; nothing in this module spawns a process
  - Caps are module constants (read at call time) so they are visible in one place
  - Writes are atomic (temp file + os.replace in the target directory), which also replaces a
    planted symlink instead of writing through it

Raises:
  SandboxError, ValueError, OSError — converted to {"error": ...} results by the registry

Used by:  workspace.py
Imports:  sandbox.py, tool_loop.py (ToolRegistry)
"""

import asyncio
import logging
import os
import re
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any, Optional

from google.genai import types

from gemini_bridge.sandbox import Sandbox, SandboxError
from gemini_bridge.tool_loop import ToolRegistry

_log = logging.getLogger(__name__)

READ_MAX_BYTES = 256 * 1024  # returned per read_file call
READ_MAX_RAW_BYTES = 20 * 1024 * 1024  # refuse to load larger files at all
LIST_MAX_ENTRIES = 500
GLOB_MAX_RESULTS = 500
GREP_MAX_MATCHES = 200
GREP_MAX_FILES = 5000
GREP_MAX_BYTES = 20 * 1024 * 1024
GREP_MAX_LINE_CHARS = 2000
BINARY_SNIFF_BYTES = 8192


def _is_binary(data: bytes) -> bool:
    return b"\x00" in data[:BINARY_SNIFF_BYTES]


def _glob_regex(pattern: str) -> "re.Pattern[str]":
    """Translate a glob to a regex over root-relative posix paths.

    '**/' = any directories (incl. none), '**' = anything, '*' = within one segment,
    '?' = one non-slash character.
    """
    out: list[str] = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out))


def _check_glob(pattern: str) -> None:
    if not pattern or pattern.startswith("/") or ".." in pattern.split("/"):
        raise ValueError(f"glob must be a relative pattern inside the repo: {pattern!r}")


class FileTools:
    """Sandboxed file operations exposed to Gemini."""

    def __init__(self, sandbox: Sandbox, max_write_bytes: int = 262_144) -> None:
        self._sandbox = sandbox
        self._max_write_bytes = max_write_bytes

    def list_dir(self, path: str = ".") -> dict[str, Any]:
        target = self._sandbox.resolve(path)
        if not target.is_dir():
            raise ValueError(f"not a directory: {path}")
        entries: list[dict[str, Any]] = []
        truncated = False
        for child in sorted(target.iterdir(), key=lambda p: p.name):
            if self._sandbox.is_denied(child):
                continue
            if len(entries) >= LIST_MAX_ENTRIES:
                truncated = True
                break
            is_dir = child.is_dir()
            entry: dict[str, Any] = {"name": child.name, "type": "dir" if is_dir else "file"}
            if not is_dir:
                entry["size"] = child.lstat().st_size
            entries.append(entry)
        return {"path": self._sandbox.relative(target), "entries": entries, "truncated": truncated}

    def glob(self, pattern: str) -> dict[str, Any]:
        _check_glob(pattern)
        rx = _glob_regex(pattern)
        matches: list[str] = []
        truncated = False
        for file in self._sandbox.walk(self._sandbox.root):
            rel = self._sandbox.relative(file)
            if rx.fullmatch(rel):
                if len(matches) >= GLOB_MAX_RESULTS:
                    truncated = True
                    break
                matches.append(rel)
        return {"matches": matches, "truncated": truncated}

    def grep(self, pattern: str, path: str = ".", glob: Optional[str] = None) -> dict[str, Any]:
        try:
            rx = re.compile(pattern)
        except re.error as exc:
            raise ValueError(f"invalid regex: {exc}") from exc
        name_filter: Optional[Callable[[Path], bool]] = None
        if glob:
            _check_glob(glob)
            grx = _glob_regex(glob)
            if "/" in glob:
                name_filter = lambda p: bool(grx.fullmatch(self._sandbox.relative(p)))  # noqa: E731
            else:
                name_filter = lambda p: bool(grx.fullmatch(p.name))  # noqa: E731
        start = self._sandbox.resolve(path)
        matches: list[dict[str, Any]] = []
        files_scanned = 0
        bytes_scanned = 0
        truncated = False
        for file in self._sandbox.walk(start):
            if name_filter and not name_filter(file):
                continue
            if files_scanned >= GREP_MAX_FILES:
                truncated = True
                break
            size = file.stat().st_size
            if bytes_scanned + size > GREP_MAX_BYTES:
                truncated = True
                break
            files_scanned += 1
            bytes_scanned += size
            data = file.read_bytes()
            if _is_binary(data):
                continue
            rel = self._sandbox.relative(file)
            for lineno, line in enumerate(data.decode("utf-8", "replace").splitlines(), 1):
                if len(line) > GREP_MAX_LINE_CHARS or not rx.search(line):
                    continue
                if len(matches) >= GREP_MAX_MATCHES:
                    truncated = True
                    return {"matches": matches, "files_scanned": files_scanned, "truncated": True}
                matches.append({"path": rel, "line": lineno, "text": line})
        return {"matches": matches, "files_scanned": files_scanned, "truncated": truncated}

    def read_file(self, path: str, offset: int = 0, limit: Optional[int] = None) -> dict[str, Any]:
        if offset < 0 or (limit is not None and limit < 1):
            raise ValueError("offset must be >= 0 and limit >= 1")
        target = self._sandbox.resolve(path)
        if not target.is_file():
            raise ValueError(f"not a file: {path}")
        size = target.stat().st_size
        if size > READ_MAX_RAW_BYTES:
            raise ValueError(f"file too large to read ({size} bytes)")
        data = target.read_bytes()
        if _is_binary(data):
            raise ValueError(f"binary file: {path}")
        lines = data.decode("utf-8", "replace").splitlines(keepends=True)
        end = offset + limit if limit is not None else None
        # Numbered like `cat -n` so Gemini can cite exact lines instead of counting them.
        content = "".join(
            f"{n:>6}\t{line}" for n, line in enumerate(lines[offset:end], start=offset + 1)
        )
        truncated = False
        encoded = content.encode()
        if len(encoded) > READ_MAX_BYTES:
            content = encoded[:READ_MAX_BYTES].decode("utf-8", "ignore")
            truncated = True
        result: dict[str, Any] = {
            "path": self._sandbox.relative(target),
            "start_line": offset + 1,
            "total_lines": len(lines),
            "content": content,
            "truncated": truncated,
        }
        if truncated:
            result["hint"] = f"output capped at {READ_MAX_BYTES} bytes; page with offset/limit"
        return result

    def write_file(self, path: str, content: str) -> dict[str, Any]:
        data = content.encode()
        if len(data) > self._max_write_bytes:
            raise ValueError(
                f"content exceeds max_write_bytes ({len(data)} > {self._max_write_bytes})"
            )
        target = self._sandbox.resolve_for_write(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".gb-")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            os.replace(tmp, target)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        return {"path": self._sandbox.relative(target), "bytes_written": len(data)}


def _schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required}


_PATH_NOTE = "Paths are relative to the repository root; nothing outside it is reachable."

_READ_DECLARATIONS: list[tuple[str, str, dict[str, Any]]] = [
    (
        "list_dir",
        f"List a directory's entries (name, type, size). {_PATH_NOTE} "
        f"At most {LIST_MAX_ENTRIES} entries.",
        _schema({"path": {"type": "string", "description": "Directory; default '.'"}}, []),
    ),
    (
        "glob",
        "Find files by glob pattern over repo-relative paths. '**/' spans directories, "
        "'*' stays within one directory (so '*.py' is top-level only; use '**/*.py'). "
        f"Dependency/cache dirs such as .venv and node_modules are skipped. "
        f"At most {GLOB_MAX_RESULTS} results.",
        _schema({"pattern": {"type": "string"}}, ["pattern"]),
    ),
    (
        "grep",
        "Search file contents with a Python regular expression. Returns path, line number, "
        f"and line text. {_PATH_NOTE} At most {GREP_MAX_MATCHES} matches.",
        _schema(
            {
                "pattern": {"type": "string", "description": "Python regex"},
                "path": {"type": "string", "description": "File or directory; default '.'"},
                "glob": {
                    "type": "string",
                    "description": "Optional file filter, e.g. '*.py' (file name) or 'src/**'",
                },
            },
            ["pattern"],
        ),
    ),
    (
        "read_file",
        f"Read a text file. {_PATH_NOTE} Each line is prefixed with its 1-based line number and "
        "a tab — cite these numbers, but never copy the prefixes into write_file content. "
        f"Returns up to {READ_MAX_BYTES // 1024} KiB; page through larger files with offset "
        "(0-based line) and limit (line count).",
        _schema(
            {
                "path": {"type": "string"},
                "offset": {"type": "integer", "description": "0-based starting line"},
                "limit": {"type": "integer", "description": "Number of lines to return"},
            },
            ["path"],
        ),
    ),
]

_WRITE_DECLARATION: tuple[str, str, dict[str, Any]] = (
    "write_file",
    f"Create or overwrite a text file. {_PATH_NOTE} Parent directories are created. "
    "Cannot delete, rename, or execute anything.",
    _schema({"path": {"type": "string"}, "content": {"type": "string"}}, ["path", "content"]),
)


def _handler(fn: Callable[..., dict[str, Any]], name: str) -> Any:
    async def handle(args: dict[str, Any]) -> dict[str, Any]:
        try:
            return await asyncio.to_thread(fn, **args)
        except SandboxError as exc:
            _log.warning("%s rejected by sandbox: %s", name, exc)
            return {"error": f"rejected: {exc}"}

    return handle


def build_file_registry(tools: FileTools, *, write: bool) -> ToolRegistry:
    """A registry with the read tools, plus write_file when `write` is True."""
    registry = ToolRegistry()
    specs = list(_READ_DECLARATIONS) + ([_WRITE_DECLARATION] if write else [])
    for name, description, schema in specs:
        registry.add(
            types.FunctionDeclaration(
                name=name, description=description, parameters_json_schema=schema
            ),
            _handler(getattr(tools, name), name),
        )
    return registry
