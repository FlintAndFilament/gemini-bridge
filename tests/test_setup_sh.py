"""Tests for the setup.sh wizard's config write (#85).

The wizard is run for real against a throwaway HOME. api_key mode is used because it is the
one path that needs neither gcloud nor a keychain item, so these tests never touch the
machine's real credentials or config.
"""

import json
import subprocess
from pathlib import Path

import pytest

SETUP = Path(__file__).resolve().parent.parent / "setup.sh"

# auth choice, env var name, thinking, default model, transcript dir — blank takes the default.
DEFAULT_ANSWERS = ["4", "", "", "", ""]


def run_setup(home: Path, answers: list[str] = DEFAULT_ANSWERS) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["bash", str(SETUP)],
        input="\n".join(answers) + "\n",
        capture_output=True,
        text=True,
        env={"HOME": str(home), "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"},
    )
    assert result.returncode == 0, result.stderr
    return result


def config_of(home: Path) -> dict:  # type: ignore[type-arg]
    return json.loads((home / ".config/gemini-bridge/config.json").read_text())


@pytest.fixture
def home(tmp_path: Path) -> Path:
    (tmp_path / ".config/gemini-bridge").mkdir(parents=True)
    return tmp_path


def write_config(home: Path, data: dict) -> None:  # type: ignore[type-arg]
    (home / ".config/gemini-bridge/config.json").write_text(json.dumps(data, indent=2))


class TestFirstRun:
    def test_writes_the_answers(self, home: Path) -> None:
        run_setup(home, ["4", "", "high", "flash-lite", "~/transcripts"])
        config = config_of(home)
        assert config["auth"] == {"method": "api_key", "api_key_env": "GEMINI_API_KEY"}
        assert config["default_thinking"] == "high"
        assert config["default_model"] == "flash-lite"
        assert config["transcript_dir"] == "~/transcripts"


class TestRerunKeepsHandEditedSettings:
    """A re-run used to rewrite config.json from a fixed template, silently dropping every
    field the wizard does not ask about (#85)."""

    EXTRAS = {
        "artifacts_dir": "~/gemini-artifacts",
        "file_tools": {"enabled": True, "deny": ["*.pem"], "max_write_bytes": 262144},
        "web_tools": {"enabled": True},
    }

    def test_unasked_fields_survive(self, home: Path) -> None:
        write_config(home, {"default_thinking": "low", **self.EXTRAS})
        run_setup(home)
        config = config_of(home)
        for key, value in self.EXTRAS.items():
            assert config[key] == value

    def test_answers_still_win_over_the_old_values(self, home: Path) -> None:
        write_config(home, {"default_thinking": "low", **self.EXTRAS})
        run_setup(home, ["4", "", "high", "", ""])
        assert config_of(home)["default_thinking"] == "high"

    def test_dash_clears_the_default_model(self, home: Path) -> None:
        write_config(home, {"default_model": "pro", **self.EXTRAS})
        run_setup(home, ["4", "", "", "-", ""])
        assert "default_model" not in config_of(home)

    def test_unparsable_config_is_backed_up_not_lost(self, home: Path) -> None:
        path = home / ".config/gemini-bridge/config.json"
        path.write_text("{ this is not json")
        run_setup(home)
        assert config_of(home)["auth"]["method"] == "api_key"
        assert (home / ".config/gemini-bridge/config.json.bak").read_text() == "{ this is not json"


class TestClosingText:
    """The wizard prompts for default_model, so its closing text must not deny the field
    exists (#89)."""

    def test_a_chosen_default_model_is_named(self, home: Path) -> None:
        out = run_setup(home, ["4", "", "", "flash-lite", ""]).stdout
        assert "no model in config" not in out
        assert "flash-lite" in out.split("Model selection")[1]

    def test_no_default_model_says_newest_flash(self, home: Path) -> None:
        out = run_setup(home).stdout
        assert "no model in config" not in out
        assert "newest Flash" in out.split("Model selection")[1]
