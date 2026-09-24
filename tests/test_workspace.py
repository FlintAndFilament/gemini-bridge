"""Tests for sidekick/workspace.py — composition of sandbox, file tools, and registries."""

from pathlib import Path

import pytest

from sidekick.config import Config
from sidekick.workspace import build_workspace


def _config(**kwargs: object) -> Config:
    return Config(auth={"method": "api_key"}, **kwargs)  # type: ignore[arg-type]


def _names(ws: object, write: bool) -> set[str]:
    registry = ws.registry(write=write)  # type: ignore[attr-defined]
    assert registry is not None
    return {d.name for d in registry.declarations}


def test_root_is_cwd(tmp_path: Path) -> None:
    ws = build_workspace(_config(), tmp_path)
    assert ws.sandbox.root == tmp_path.resolve()


def test_read_registry_has_no_write(tmp_path: Path) -> None:
    ws = build_workspace(_config(), tmp_path)
    assert _names(ws, write=False) == {"list_dir", "glob", "grep", "read_file"}


def test_write_registry_adds_write_file(tmp_path: Path) -> None:
    ws = build_workspace(_config(), tmp_path)
    assert "write_file" in _names(ws, write=True)


def test_disabled_gives_no_registry(tmp_path: Path) -> None:
    ws = build_workspace(_config(file_tools={"enabled": False}), tmp_path)
    assert ws.registry(write=True) is None


def test_custom_deny_reaches_sandbox(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("x")
    ws = build_workspace(_config(file_tools={"deny": ["*.secret"]}), tmp_path)
    assert ws.sandbox.resolve(".env") == (tmp_path / ".env").resolve()


def test_artifacts_dir_defaults_inside_root(tmp_path: Path) -> None:
    ws = build_workspace(_config(), tmp_path)
    assert ws.artifacts.directory == tmp_path.resolve() / "sidekick-artifacts"


def test_artifacts_dir_outside_root_rejected(tmp_path: Path) -> None:
    import pytest

    from sidekick.sandbox import SandboxError

    with pytest.raises(SandboxError):
        build_workspace(_config(artifacts_dir="../elsewhere"), tmp_path)


def test_artifacts_dir_in_denied_path_rejected(tmp_path: Path) -> None:
    import pytest

    from sidekick.sandbox import SandboxError

    with pytest.raises(SandboxError):
        build_workspace(_config(artifacts_dir=".git/artifacts"), tmp_path)


def test_artifacts_available_when_tools_disabled(tmp_path: Path) -> None:
    ws = build_workspace(_config(file_tools={"enabled": False}), tmp_path)
    assert ws.artifacts is not None


def test_file_tools_disabled_when_root_is_home(tmp_path: Path, monkeypatch: object) -> None:
    # Review finding #4: launching from $HOME must not expose the home directory.
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))  # type: ignore[attr-defined]
    ws = build_workspace(_config(), tmp_path)
    assert ws.registry(write=False) is None
    assert ws.disabled_reason is not None and "home" in ws.disabled_reason


def test_file_tools_disabled_when_root_is_above_home(tmp_path: Path, monkeypatch: object) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "me"))  # type: ignore[attr-defined]
    ws = build_workspace(_config(), tmp_path)
    assert ws.registry(write=False) is None


def test_file_tools_enabled_in_project_under_home(tmp_path: Path, monkeypatch: object) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))  # type: ignore[attr-defined]
    project = tmp_path / "dev" / "proj"
    project.mkdir(parents=True)
    ws = build_workspace(_config(), project)
    assert ws.registry(write=False) is not None and ws.disabled_reason is None


class TestProjectRoot:
    """Plugin servers get CLAUDE_PROJECT_DIR; the cwd is not a documented contract (#102)."""

    def test_uses_claude_project_dir_when_it_is_a_directory(self, tmp_path: Path) -> None:
        from sidekick.workspace import project_root

        assert project_root({"CLAUDE_PROJECT_DIR": str(tmp_path)}) == tmp_path.resolve()

    def test_falls_back_to_cwd_when_unset(self) -> None:
        from sidekick.workspace import project_root

        assert project_root({}) == Path.cwd().resolve()

    @pytest.mark.parametrize("value", ["", "/no/such/dir/for/sidekick"])
    def test_falls_back_to_cwd_when_empty_or_missing(self, value: str) -> None:
        from sidekick.workspace import project_root

        assert project_root({"CLAUDE_PROJECT_DIR": value}) == Path.cwd().resolve()
