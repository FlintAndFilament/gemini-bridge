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
        backups = list((home / ".config/gemini-bridge").glob("config.json.*.bak"))
        assert len(backups) == 1
        assert backups[0].read_text() == "{ this is not json"


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


class TestDurability:
    """The merge must not be able to lose a config it was asked to preserve (#95, #96)."""

    def test_a_second_unparsable_config_does_not_clobber_the_first_backup(self, home: Path) -> None:
        path = home / ".config/gemini-bridge/config.json"
        path.write_text("{ first broken config")
        run_setup(home)
        path.write_text("{ second broken config")
        run_setup(home)
        backups = sorted((home / ".config/gemini-bridge").glob("config.json*.bak"))
        kept = {b.read_text() for b in backups}
        assert kept == {"{ first broken config", "{ second broken config"}

    def test_no_temp_file_is_left_behind(self, home: Path) -> None:
        run_setup(home)
        leftovers = list((home / ".config/gemini-bridge").glob("*.tmp"))
        assert leftovers == []


class TestAnswersAreNeverInterpolated:
    """setup.sh passes answers to python as environment variables precisely so that no answer
    is ever parsed as shell, python or JSON. Nothing asserted that until now (#97)."""

    HOSTILE = '~/a "b" $(touch /tmp/gemini-bridge-pwned) `id` \\dir'

    def test_a_hostile_answer_round_trips_unchanged(self, home: Path, tmp_path: Path) -> None:
        canary = Path("/tmp/gemini-bridge-pwned")
        assert not canary.exists(), "stale canary from an earlier run; delete it"
        run_setup(home, ["4", "", "", "", self.HOSTILE])
        assert config_of(home)["transcript_dir"] == self.HOSTILE
        assert not canary.exists()

    def test_it_survives_being_read_back_as_a_prompt_default(self, home: Path) -> None:
        """The second run re-reads config.json through `eval "$(python3 … shlex.quote …)"`,
        which is the other place an answer could be re-parsed."""
        run_setup(home, ["4", "", "", "", self.HOSTILE])
        run_setup(home)  # every answer blank: the stored value comes back as the default
        assert config_of(home)["transcript_dir"] == self.HOSTILE
        assert not Path("/tmp/gemini-bridge-pwned").exists()


class TestApiKeyModeLeavesVertexFieldsAlone:
    def test_project_and_location_survive(self, home: Path) -> None:
        write_config(home, {"project": "my-proj", "location": "us-east4"})
        run_setup(home)
        config = config_of(home)
        assert config["project"] == "my-proj"
        assert config["location"] == "us-east4"
        assert config["auth"]["method"] == "api_key"


class TestApiKeyEnvDefault:
    """The saved api_key_env must come back as the prompt default on a re-run (#99: it is now
    read by the same loader as every other PREV_* value, not by a second python3 call)."""

    def test_saved_env_var_name_is_offered_again(self, home: Path) -> None:
        run_setup(home, ["4", "MY_GEMINI_KEY", "", "", ""])
        assert config_of(home)["auth"]["api_key_env"] == "MY_GEMINI_KEY"
        result = run_setup(home)  # blank answer takes the default
        assert config_of(home)["auth"]["api_key_env"] == "MY_GEMINI_KEY"
        assert "MY_GEMINI_KEY" in result.stdout + result.stderr

    def test_config_json_is_read_by_exactly_one_python_invocation(self) -> None:
        """A second reader existed only for api_key_env, and it interpolated $CONFIG_FILE into
        python source. The keychain validator stays: it reads a piped secret, not the config."""
        source = SETUP.read_text()
        assert "pathlib.Path('$CONFIG_FILE')" not in source
        # Two `python3 -c` blocks remain: the PREV_* loader and the keychain JSON validator.
        assert source.count("python3 -c") == 2
