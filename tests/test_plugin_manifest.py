"""The plugin's manifests are part of the product: pin the facts users depend on (#102)."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load(rel: str) -> dict:  # type: ignore[type-arg]
    return json.loads((ROOT / rel).read_text())


def test_plugin_is_named_sidekick() -> None:
    plugin = load(".claude-plugin/plugin.json")
    assert plugin["name"] == "sidekick"
    assert plugin["version"]


def test_marketplace_ships_main_never_develop() -> None:
    market = load(".claude-plugin/marketplace.json")
    (entry,) = market["plugins"]
    assert entry["name"] == "sidekick"
    assert entry["source"] == {
        "source": "github",
        "repo": "FlintAndFilament/gemini-bridge",
        "ref": "main",
    }


def test_server_launches_frozen_with_the_venv_in_plugin_data() -> None:
    server = load(".mcp.json")["gemini"]
    assert server["command"] == "uv"
    assert server["args"][:2] == ["run", "--frozen"]
    assert "${CLAUDE_PLUGIN_ROOT}" in server["args"]
    assert server["args"][-1] == "gemini-bridge"
    assert server["env"]["UV_PROJECT_ENVIRONMENT"] == "${CLAUDE_PLUGIN_DATA}/venv"


def test_lockfile_is_committed() -> None:
    assert (ROOT / "uv.lock").is_file()


def test_setup_command_drives_the_cli_and_never_asks_for_the_key() -> None:
    text = (ROOT / "commands" / "setup.md").read_text()
    assert "gemini-bridge-setup status" in text
    assert "gemini-bridge-setup write" in text
    assert "AskUserQuestion" in text
    assert "UV_PROJECT_ENVIRONMENT" in text  # same venv as the server, not a second one
    assert "never ask for the key" in text.lower()
