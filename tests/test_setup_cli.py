"""Tests for the setup CLI that replaced setup.sh (#102). Ports every setup.sh guarantee:
#85 merge-only-owned-keys, #95 atomic write, #96 timestamped backups, #97 no interpolation,
#99 api_key_env remembered."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from sidekick import setup_cli

# Captured before any test monkeypatches setup_cli._keychain_problem, so tests that need the
# real validation logic (not the autouse no-op below) can restore it.
_REAL_KEYCHAIN_PROBLEM = setup_cli._keychain_problem


@pytest.fixture
def cfg(tmp_path: Path) -> Path:
    return tmp_path / "sidekick" / "config.json"


@pytest.fixture(autouse=True)
def no_real_checks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(setup_cli, "_adc_ok", lambda: True)
    monkeypatch.setattr(setup_cli, "_keychain_problem", lambda s, a: None)


def run(cfg: Path, *args: str) -> tuple[int, dict]:  # type: ignore[type-arg]
    import contextlib
    import io

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = setup_cli.main([args[0], "--config", str(cfg), *args[1:]])
    return code, json.loads(out.getvalue())


def data(cfg: Path) -> dict:  # type: ignore[type-arg]
    return json.loads(cfg.read_text())


class TestFirstRun:
    def test_writes_the_answers(self, cfg: Path) -> None:
        code, out = run(
            cfg,
            "write",
            "--auth-method",
            "api_key",
            "--thinking",
            "high",
            "--default-model",
            "flash-lite",
            "--transcript-dir",
            "~/transcripts",
        )
        assert code == 0 and out["ok"]
        c = data(cfg)
        assert c["auth"] == {"method": "api_key", "api_key_env": "GEMINI_API_KEY"}
        assert c["default_thinking"] == "high"
        assert c["default_model"] == "flash-lite"
        assert c["transcript_dir"] == "~/transcripts"


class TestRerunKeepsHandEditedSettings:
    EXTRAS = {
        "artifacts_dir": "./out",
        "file_tools": {"enabled": False},
        "web_tools": {"enabled": True},
    }

    def test_unasked_fields_survive(self, cfg: Path) -> None:
        cfg.parent.mkdir(parents=True)
        cfg.write_text(json.dumps({**self.EXTRAS, "auth": {"method": "api_key"}}))
        run(cfg, "write", "--auth-method", "api_key")
        for k, v in self.EXTRAS.items():
            assert data(cfg)[k] == v

    def test_omitted_flags_keep_saved_values(self, cfg: Path) -> None:
        run(
            cfg,
            "write",
            "--auth-method",
            "api_key",
            "--thinking",
            "low",
            "--default-model",
            "pro",
            "--api-key-env",
            "MY_GEMINI_KEY",
        )
        run(cfg, "write", "--auth-method", "api_key")
        c = data(cfg)
        assert c["default_thinking"] == "low"
        assert c["default_model"] == "pro"
        assert c["auth"]["api_key_env"] == "MY_GEMINI_KEY"

    def test_dash_clears_the_default_model(self, cfg: Path) -> None:
        run(cfg, "write", "--auth-method", "api_key", "--default-model", "pro")
        run(cfg, "write", "--auth-method", "api_key", "--default-model", "-")
        assert "default_model" not in data(cfg)

    def test_switching_to_adc_keeps_a_saved_project(self, cfg: Path) -> None:
        cfg.parent.mkdir(parents=True)
        cfg.write_text(json.dumps({"project": "my-proj", "auth": {"method": "api_key"}}))
        code, _ = run(cfg, "write", "--auth-method", "adc")
        assert code == 0
        assert data(cfg)["project"] == "my-proj"
        assert data(cfg)["location"] == "global"


class TestApiKeyModeLeavesVertexFieldsAlone:
    def test_project_and_location_survive(self, cfg: Path) -> None:
        cfg.parent.mkdir(parents=True)
        cfg.write_text(json.dumps({"project": "my-proj", "location": "us-east4"}))
        run(cfg, "write", "--auth-method", "api_key")
        assert data(cfg)["project"] == "my-proj"
        assert data(cfg)["location"] == "us-east4"


class TestDurability:
    def test_unparsable_config_is_backed_up_not_lost(self, cfg: Path) -> None:
        cfg.parent.mkdir(parents=True)
        cfg.write_text("{not json")
        code, out = run(cfg, "write", "--auth-method", "api_key")
        assert code == 0 and out["backup"]
        assert Path(out["backup"]).read_text() == "{not json"

    def test_a_second_unparsable_config_does_not_clobber_the_first_backup(self, cfg: Path) -> None:
        cfg.parent.mkdir(parents=True)
        cfg.write_text("{first")
        run(cfg, "write", "--auth-method", "api_key")
        cfg.write_text("{second")
        run(cfg, "write", "--auth-method", "api_key")
        texts = sorted(p.read_text() for p in cfg.parent.glob("*.bak"))
        assert texts == ["{first", "{second"]

    def test_no_temp_file_is_left_behind(self, cfg: Path) -> None:
        run(cfg, "write", "--auth-method", "api_key")
        assert list(cfg.parent.glob("*.tmp")) == []


class TestRejectsBeforeWriting:
    def _seed(self, cfg: Path) -> str:
        cfg.parent.mkdir(parents=True)
        original = json.dumps({"auth": {"method": "api_key"}, "default_thinking": "low"})
        cfg.write_text(original)
        return original

    def test_a_pasted_api_key_is_rejected(self, cfg: Path) -> None:
        original = self._seed(cfg)
        code, out = run(
            cfg,
            "write",
            "--auth-method",
            "api_key",
            "--api-key-env",
            "AIzaSyD3adb33fD3adb33fD3adb33fD3adb33f0",
        )
        assert code == 2 and not out["ok"]
        assert cfg.read_text() == original

    def test_vertex_without_any_project_is_rejected(self, cfg: Path) -> None:
        original = self._seed(cfg)
        code, out = run(cfg, "write", "--auth-method", "adc")
        assert code == 2 and "project" in out["error"]
        assert cfg.read_text() == original

    def test_bad_thinking_level_is_rejected(self, cfg: Path) -> None:
        original = self._seed(cfg)
        with pytest.raises(SystemExit):  # argparse choices
            run(cfg, "write", "--auth-method", "api_key", "--thinking", "extreme")
        assert cfg.read_text() == original

    def test_keychain_is_refused_off_macos(
        self, cfg: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(setup_cli.sys, "platform", "linux")
        code, out = run(cfg, "write", "--auth-method", "keychain", "--project", "p")
        assert code == 2 and "macOS" in out["error"]
        assert not cfg.exists()


class TestValuesAreNeverInterpolated:
    HOSTILE = '~/a "b" $(touch /tmp/sidekick-pwned) `id` \\dir'

    def test_a_hostile_value_round_trips_through_a_real_process(self, cfg: Path) -> None:
        canary = Path("/tmp/sidekick-pwned")
        assert not canary.exists(), "stale canary from an earlier run; delete it"
        subprocess.run(
            [
                sys.executable,
                "-m",
                "sidekick.setup_cli",
                "write",
                "--config",
                str(cfg),
                "--auth-method",
                "api_key",
                "--transcript-dir",
                self.HOSTILE,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        assert data(cfg)["transcript_dir"] == self.HOSTILE
        assert not canary.exists()


class TestStatus:
    def test_no_config_yet(self, cfg: Path) -> None:
        code, out = run(cfg, "status")
        assert code == 0
        assert out["config_exists"] is False and out["config_error"] is None
        assert out["values"]["auth_method"] == "adc"
        assert out["values"]["transcript_dir"] == "./session-summaries"

    def test_reports_saved_values(self, cfg: Path) -> None:
        run(cfg, "write", "--auth-method", "api_key", "--api-key-env", "MY_KEY")
        _, out = run(cfg, "status")
        assert out["values"]["auth_method"] == "api_key"
        assert out["values"]["api_key_env"] == "MY_KEY"

    def test_unparsable_config_is_reported_not_raised(self, cfg: Path) -> None:
        cfg.parent.mkdir(parents=True)
        cfg.write_text("{nope")
        code, out = run(cfg, "status")
        assert code == 0 and out["config_error"]

    @pytest.mark.parametrize(
        "platform,has_keychain", [("darwin", True), ("linux", False), ("win32", False)]
    )
    def test_keychain_offered_only_on_macos(
        self, cfg: Path, monkeypatch: pytest.MonkeyPatch, platform: str, has_keychain: bool
    ) -> None:
        monkeypatch.setattr(setup_cli.sys, "platform", platform)
        _, out = run(cfg, "status")
        assert ("keychain" in out["auth_methods"]) is has_keychain

    def test_api_key_env_set_check(self, cfg: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GEMINI_API_KEY", "x")
        _, out = run(cfg, "status")
        assert out["checks"]["api_key_env_set"] is True


class TestStatusNeverLeaksASavedSecret:
    """Review finding 1: a pasted key surviving in config.json must never reach stdout."""

    def test_a_pasted_key_in_saved_config_is_not_echoed(self, cfg: Path) -> None:
        cfg.parent.mkdir(parents=True)
        secret = "AIzaSyD3adb33fD3adb33fD3adb33fD3adb33f0"
        cfg.write_text(json.dumps({"auth": {"method": "api_key", "api_key_env": secret}}))
        code, out = run(cfg, "status")
        assert code == 0
        assert secret not in json.dumps(out)
        assert out["values"]["api_key_env"] == "GEMINI_API_KEY"
        assert out["checks"]["api_key_env_invalid"] is True


class TestWriteFailureIsReportedNotRaised:
    """Review finding 2: an OSError while writing must become exit-2 JSON, not a traceback."""

    def test_os_error_during_write_is_exit_2_json_with_no_leftover_tmp(
        self, cfg: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _raise(*_args: object, **_kwargs: object) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(setup_cli.os, "replace", _raise)
        code, out = run(cfg, "write", "--auth-method", "api_key")
        assert code == 2 and not out["ok"]
        assert list(cfg.parent.glob("*.tmp")) == []


class TestKeychainProblemCatchesNonServiceAccountJSON:
    """Review finding 3: valid-JSON-but-not-a-service-account must be reported, not crash."""

    def test_a_keychain_item_that_is_not_a_service_account_is_rejected(
        self, cfg: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(setup_cli.sys, "platform", "darwin")
        # Restore the real validator — the autouse fixture stubs it out for every other test.
        monkeypatch.setattr(setup_cli, "_keychain_problem", _REAL_KEYCHAIN_PROBLEM)

        def _raise(_auth_config: object) -> None:
            raise ValueError("not a service account")

        monkeypatch.setattr(setup_cli, "_load_keychain", _raise)
        code, out = run(cfg, "write", "--auth-method", "keychain")
        assert code == 2 and not out["ok"]
