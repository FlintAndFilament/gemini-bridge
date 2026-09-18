"""Tests for gemini_bridge/sandbox.py — path confinement is the entire security boundary."""

import os
from pathlib import Path

import pytest

from gemini_bridge.sandbox import Sandbox, SandboxError


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A small repo tree plus an 'outside' sibling dir the sandbox must never reach."""
    root = (tmp_path / "repo").resolve()
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("print('a')\n")
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("[core]\n")
    (root / ".env").write_text("SECRET=1\n")
    (root / ".venv" / "lib").mkdir(parents=True)
    (root / ".venv" / "lib" / "x.py").write_text("")
    (root / "node_modules").mkdir()
    (root / "node_modules" / "m.js").write_text("")
    outside = (tmp_path / "outside").resolve()
    outside.mkdir()
    (outside / "secret.txt").write_text("nope\n")
    return root


@pytest.fixture
def sandbox(repo: Path) -> Sandbox:
    return Sandbox(repo)


REJECTED = [
    "",
    "a\x00b",
    "../x",
    "a/../../x",
    "/etc/passwd",
    ".git",
    ".git/config",
    ".GIT/config",
    ".env",
    "sub/.env.local",
    "keys/server.pem",
    "deploy.key",
    "id_rsa.pub",
    "gcp-credentials-prod.json",
    "svc-sa-key.json",
]


class TestResolve:
    @pytest.mark.parametrize("path", REJECTED)
    def test_rejects(self, sandbox: Sandbox, path: str) -> None:
        with pytest.raises(SandboxError):
            sandbox.resolve(path)

    def test_rejects_symlink_escaping_root(self, sandbox: Sandbox, repo: Path) -> None:
        (repo / "out").symlink_to(repo.parent / "outside")
        with pytest.raises(SandboxError, match="escapes"):
            sandbox.resolve("out/secret.txt")

    def test_rejects_symlink_into_denied_path(self, sandbox: Sandbox, repo: Path) -> None:
        (repo / "notes").symlink_to(repo / ".git" / "config")
        with pytest.raises(SandboxError, match="denied"):
            sandbox.resolve("notes")

    def test_rejects_absolute_path_outside_root(self, sandbox: Sandbox, repo: Path) -> None:
        with pytest.raises(SandboxError):
            sandbox.resolve(str(repo.parent / "outside" / "secret.txt"))

    @pytest.mark.parametrize("path", ["src/a.py", "./src/a.py", "src/../src/a.py"])
    def test_accepts_relative(self, sandbox: Sandbox, repo: Path, path: str) -> None:
        assert sandbox.resolve(path) == repo / "src" / "a.py"

    def test_accepts_absolute_inside_root(self, sandbox: Sandbox, repo: Path) -> None:
        assert sandbox.resolve(str(repo / "src" / "a.py")) == repo / "src" / "a.py"

    def test_dot_is_root(self, sandbox: Sandbox, repo: Path) -> None:
        assert sandbox.resolve(".") == repo

    def test_nonexistent_path_is_allowed(self, sandbox: Sandbox, repo: Path) -> None:
        assert sandbox.resolve("new/file.md") == repo / "new" / "file.md"

    def test_custom_deny_replaces_defaults(self, repo: Path) -> None:
        sb = Sandbox(repo, deny=["*.secret"])
        assert sb.resolve(".env") == repo / ".env"
        with pytest.raises(SandboxError):
            sb.resolve("x.secret")

    def test_relative(self, sandbox: Sandbox, repo: Path) -> None:
        assert sandbox.relative(repo) == "."
        assert sandbox.relative(repo / "src" / "a.py") == "src/a.py"


class TestResolveForWrite:
    def test_planted_symlink_is_not_followed(self, sandbox: Sandbox, repo: Path) -> None:
        (repo / "link.md").symlink_to(repo.parent / "outside" / "secret.txt")
        assert sandbox.resolve_for_write("link.md") == repo / "link.md"

    def test_parent_symlink_escaping_root_rejected(self, sandbox: Sandbox, repo: Path) -> None:
        (repo / "out").symlink_to(repo.parent / "outside")
        with pytest.raises(SandboxError, match="escapes"):
            sandbox.resolve_for_write("out/new.txt")

    @pytest.mark.parametrize("path", [".", "src", "../x", ".git/HEAD", ".env", ""])
    def test_rejects(self, sandbox: Sandbox, path: str) -> None:
        with pytest.raises(SandboxError):
            sandbox.resolve_for_write(path)

    def test_new_nested_file_allowed(self, sandbox: Sandbox, repo: Path) -> None:
        assert sandbox.resolve_for_write("docs/new/x.md") == repo / "docs" / "new" / "x.md"


class TestWalk:
    def _rels(self, sandbox: Sandbox, start: Path) -> list[str]:
        return [sandbox.relative(p) for p in sandbox.walk(start)]

    def test_skips_noise_and_denied(self, sandbox: Sandbox, repo: Path) -> None:
        assert self._rels(sandbox, repo) == ["src/a.py"]

    def test_does_not_follow_symlinked_dirs(self, sandbox: Sandbox, repo: Path) -> None:
        (repo / "out").symlink_to(repo.parent / "outside")
        (repo / "loop").symlink_to(repo / "src")
        assert self._rels(sandbox, repo) == ["src/a.py"]

    def test_skips_file_symlink_escaping_root(self, sandbox: Sandbox, repo: Path) -> None:
        (repo / "src" / "leak.txt").symlink_to(repo.parent / "outside" / "secret.txt")
        assert self._rels(sandbox, repo) == ["src/a.py"]

    def test_sorted(self, sandbox: Sandbox, repo: Path) -> None:
        (repo / "b.txt").write_text("")
        (repo / "a.txt").write_text("")
        assert self._rels(sandbox, repo) == ["a.txt", "b.txt", "src/a.py"]

    def test_walk_readable_skip_dir_directly(self, sandbox: Sandbox, repo: Path) -> None:
        # Skip dirs are pruned from walks started above them, but not denied.
        assert sandbox.resolve(".venv/lib/x.py") == repo / ".venv" / "lib" / "x.py"
        assert os.path.exists(sandbox.resolve(".venv/lib/x.py"))
