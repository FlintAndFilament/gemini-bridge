"""Tests for gemini_bridge/workspace.py — composition of sandbox, file tools, and registries."""

from pathlib import Path

from gemini_bridge.config import Config
from gemini_bridge.workspace import build_workspace


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
    assert ws.artifacts.directory == tmp_path.resolve() / "gemini-artifacts"


def test_artifacts_dir_outside_root_rejected(tmp_path: Path) -> None:
    import pytest

    from gemini_bridge.sandbox import SandboxError

    with pytest.raises(SandboxError):
        build_workspace(_config(artifacts_dir="../elsewhere"), tmp_path)


def test_artifacts_dir_in_denied_path_rejected(tmp_path: Path) -> None:
    import pytest

    from gemini_bridge.sandbox import SandboxError

    with pytest.raises(SandboxError):
        build_workspace(_config(artifacts_dir=".git/artifacts"), tmp_path)


def test_artifacts_available_when_tools_disabled(tmp_path: Path) -> None:
    ws = build_workspace(_config(file_tools={"enabled": False}), tmp_path)
    assert ws.artifacts is not None
