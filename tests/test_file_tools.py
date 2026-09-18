"""Tests for gemini_bridge/file_tools.py and the ToolRegistry in tool_loop.py."""

from pathlib import Path
from typing import Any

import pytest
from google.genai import types

from gemini_bridge import file_tools as ft
from gemini_bridge.file_tools import FileTools, build_file_registry
from gemini_bridge.sandbox import Sandbox
from gemini_bridge.tool_loop import ToolCallRecord, ToolRegistry


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = (tmp_path / "repo").resolve()
    (root / "src" / "pkg").mkdir(parents=True)
    (root / "src" / "main.py").write_text("def main():\n    return 1\n")
    (root / "src" / "pkg" / "util.py").write_text("import os\n\ndef helper():\n    pass\n")
    (root / "README.md").write_text("# Title\nhelper docs\n")
    (root / "top.py").write_text("x = 1\n")
    (root / ".venv").mkdir()
    (root / ".venv" / "junk.py").write_text("def helper(): ...\n")
    (root / ".env").write_text("SECRET=1\n")
    (tmp_path / "outside.txt").write_text("outside\n")
    return root


@pytest.fixture
def tools(repo: Path) -> FileTools:
    return FileTools(Sandbox(repo), max_write_bytes=1024)


class TestListDir:
    def test_lists_sorted_with_types(self, tools: FileTools) -> None:
        out = tools.list_dir("src")
        assert out["path"] == "src"
        assert [(e["name"], e["type"]) for e in out["entries"]] == [
            ("main.py", "file"),
            ("pkg", "dir"),
        ]
        assert out["truncated"] is False

    def test_hides_denied_entries(self, tools: FileTools) -> None:
        names = [e["name"] for e in tools.list_dir(".")["entries"]]
        assert ".env" not in names
        assert ".venv" in names  # skip dirs are listed, just not walked

    def test_caps_entries(self, tools: FileTools, repo: Path, monkeypatch: Any) -> None:
        monkeypatch.setattr(ft, "LIST_MAX_ENTRIES", 2)
        out = tools.list_dir(".")
        assert len(out["entries"]) == 2 and out["truncated"] is True

    def test_rejects_escape(self, tools: FileTools) -> None:
        with pytest.raises(Exception, match="escapes"):
            tools.list_dir("..")


class TestGlob:
    def test_double_star_matches_nested_and_top(self, tools: FileTools) -> None:
        assert tools.glob("**/*.py")["matches"] == ["src/main.py", "src/pkg/util.py", "top.py"]

    def test_single_star_is_one_segment(self, tools: FileTools) -> None:
        assert tools.glob("*.py")["matches"] == ["top.py"]
        assert tools.glob("src/*.py")["matches"] == ["src/main.py"]

    def test_skips_noise_dirs(self, tools: FileTools) -> None:
        assert not any(m.startswith(".venv") for m in tools.glob("**/*")["matches"])

    @pytest.mark.parametrize("pattern", ["../*", "src/../../*", "/etc/*"])
    def test_rejects_escaping_patterns(self, tools: FileTools, pattern: str) -> None:
        with pytest.raises(ValueError):
            tools.glob(pattern)

    def test_caps_results(self, tools: FileTools, monkeypatch: Any) -> None:
        monkeypatch.setattr(ft, "GLOB_MAX_RESULTS", 1)
        out = tools.glob("**/*")
        assert len(out["matches"]) == 1 and out["truncated"] is True


class TestGrep:
    def test_finds_matches_with_line_numbers(self, tools: FileTools) -> None:
        out = tools.grep(r"def helper")
        assert out["matches"] == [{"path": "src/pkg/util.py", "line": 3, "text": "def helper():"}]

    def test_glob_filter_on_file_name(self, tools: FileTools) -> None:
        out = tools.grep("helper", glob="*.md")
        assert [m["path"] for m in out["matches"]] == ["README.md"]

    def test_path_scopes_search(self, tools: FileTools) -> None:
        out = tools.grep("def", path="src/main.py")
        assert [m["path"] for m in out["matches"]] == ["src/main.py"]

    def test_skips_binary_and_long_lines(self, tools: FileTools, repo: Path) -> None:
        (repo / "bin.dat").write_bytes(b"helper\x00\x01")
        (repo / "long.txt").write_text("helper" + "x" * 3000 + "\n")
        paths = {m["path"] for m in tools.grep("helper")["matches"]}
        assert "bin.dat" not in paths and "long.txt" not in paths

    def test_match_cap(self, tools: FileTools, repo: Path, monkeypatch: Any) -> None:
        monkeypatch.setattr(ft, "GREP_MAX_MATCHES", 2)
        (repo / "many.txt").write_text("hit\n" * 10)
        out = tools.grep("hit")
        assert len(out["matches"]) == 2 and out["truncated"] is True

    def test_file_budget(self, tools: FileTools, monkeypatch: Any) -> None:
        monkeypatch.setattr(ft, "GREP_MAX_FILES", 1)
        out = tools.grep("zzz-no-match")
        assert out["files_scanned"] == 1 and out["truncated"] is True

    def test_invalid_regex_raises(self, tools: FileTools) -> None:
        with pytest.raises(ValueError, match="invalid regex"):
            tools.grep("(")


class TestReadFile:
    def test_reads_full(self, tools: FileTools) -> None:
        out = tools.read_file("src/main.py")
        assert out["content"] == "     1\tdef main():\n     2\t    return 1\n"
        assert out["start_line"] == 1 and out["total_lines"] == 2 and out["truncated"] is False

    def test_offset_and_limit(self, tools: FileTools) -> None:
        out = tools.read_file("src/pkg/util.py", offset=2, limit=1)
        assert out["content"] == "     3\tdef helper():\n"
        assert out["start_line"] == 3 and out["total_lines"] == 4

    def test_binary_rejected(self, tools: FileTools, repo: Path) -> None:
        (repo / "img.bin").write_bytes(b"\x89PNG\x00\x00")
        with pytest.raises(ValueError, match="binary"):
            tools.read_file("img.bin")

    def test_truncates_large(self, tools: FileTools, repo: Path, monkeypatch: Any) -> None:
        monkeypatch.setattr(ft, "READ_MAX_BYTES", 10)
        out = tools.read_file("README.md")
        assert len(out["content"].encode()) <= 10 and out["truncated"] is True

    def test_denied(self, tools: FileTools) -> None:
        with pytest.raises(Exception, match="denied"):
            tools.read_file(".env")


class TestWriteFile:
    def test_creates_parents(self, tools: FileTools, repo: Path) -> None:
        out = tools.write_file("docs/new/x.md", "hello")
        assert out == {"path": "docs/new/x.md", "bytes_written": 5}
        assert (repo / "docs" / "new" / "x.md").read_text() == "hello"

    def test_overwrites(self, tools: FileTools, repo: Path) -> None:
        tools.write_file("top.py", "y = 2\n")
        assert (repo / "top.py").read_text() == "y = 2\n"

    def test_size_cap(self, tools: FileTools) -> None:
        with pytest.raises(ValueError, match="exceeds"):
            tools.write_file("big.txt", "x" * 2048)

    def test_replaces_planted_symlink(self, tools: FileTools, repo: Path) -> None:
        outside = repo.parent / "outside.txt"
        (repo / "link.txt").symlink_to(outside)
        tools.write_file("link.txt", "new")
        assert outside.read_text() == "outside\n"
        assert not (repo / "link.txt").is_symlink()
        assert (repo / "link.txt").read_text() == "new"

    def test_no_temp_files_left(self, tools: FileTools, repo: Path) -> None:
        tools.write_file("a.txt", "1")
        assert not [p for p in repo.iterdir() if p.name.startswith(".gb-")]


class TestRegistry:
    def test_read_only_has_four_tools(self, tools: FileTools) -> None:
        names = {d.name for d in build_file_registry(tools, write=False).declarations}
        assert names == {"list_dir", "glob", "grep", "read_file"}

    def test_write_adds_write_file(self, tools: FileTools) -> None:
        reg = build_file_registry(tools, write=True)
        assert len(reg) == 5
        assert "write_file" in {d.name for d in reg.declarations}

    async def test_dispatch_success(self, tools: FileTools) -> None:
        reg = build_file_registry(tools, write=False)
        out = await reg.dispatch("read_file", {"path": "top.py"})
        assert out["content"] == "     1\tx = 1\n"

    async def test_dispatch_sandbox_rejection_is_error_dict(self, tools: FileTools) -> None:
        reg = build_file_registry(tools, write=False)
        out = await reg.dispatch("read_file", {"path": "../outside.txt"})
        assert out["error"].startswith("rejected: path escapes repo root")

    async def test_dispatch_bad_args_is_error_dict(self, tools: FileTools) -> None:
        reg = build_file_registry(tools, write=False)
        out = await reg.dispatch("read_file", {"nope": 1})
        assert "error" in out

    async def test_dispatch_unknown_tool(self) -> None:
        assert await ToolRegistry().dispatch("nope", {}) == {"error": "unknown tool: nope"}

    def test_duplicate_name_rejected(self) -> None:
        reg = ToolRegistry()
        decl = types.FunctionDeclaration(name="t", description="d")

        async def h(args: dict[str, Any]) -> dict[str, Any]:
            return {}

        reg.add(decl, h)
        with pytest.raises(ValueError):
            reg.add(decl, h)


class TestRecordRender:
    def test_ok(self) -> None:
        rec = ToolCallRecord("read_file", {"path": "a.py"}, ok=True, summary="1.2 KiB")
        assert rec.render() == "→ read_file(path='a.py') → 1.2 KiB"

    def test_error(self) -> None:
        rec = ToolCallRecord("read_file", {"path": "../x"}, ok=False, summary="rejected: nope")
        assert rec.render() == "✗ read_file(path='../x') → rejected: nope"
