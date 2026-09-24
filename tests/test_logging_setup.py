"""Tests for the logging setup in __main__ (#88).

Importing __main__ configures logging as a side effect and writes into $HOME, so each case
runs in a subprocess with a throwaway HOME rather than touching the real log directory or
leaving handlers attached to the test session's loggers.
"""

import subprocess
import sys
from pathlib import Path

import pytest

PROBE = """
import logging
import sidekick.__main__  # configures logging on import

logging.getLogger("sidekick.probe").info("OWN_PROBE")
logging.getLogger("httpx").warning("LIB_WARNING_PROBE")
logging.getLogger("google").debug("LIB_DEBUG_PROBE")

# What google.auth.transport.requests emits at DEBUG: the payload rides in `extra`.
logging.getLogger("google.auth.transport.requests").debug(
    "PAYLOAD_PROBE",
    extra={"httpRequest": {"headers": {"authorization": "Bearer SECRET_TOKEN_PROBE"}}},
)
logging.getLogger("google.auth.transport.requests").debug(
    "RESPONSE_PAYLOAD_PROBE", extra={"httpResponse": {"body": "SECRET_BODY_PROBE"}}
)
"""


def log_text(home: Path, level: str) -> str:
    result = subprocess.run(
        [sys.executable, "-c", PROBE],
        capture_output=True,
        text=True,
        env={"HOME": str(home), "PATH": "/usr/bin:/bin", "SIDEKICK_LOG_LEVEL": level},
    )
    assert result.returncode == 0, result.stderr
    logs = list((home / ".config/sidekick/logs").glob("*-sidekick.log"))
    assert len(logs) == 1, logs
    return logs[0].read_text()


@pytest.fixture
def home(tmp_path: Path) -> Path:
    return tmp_path


class TestLibraryLogging:
    def test_own_records_are_written(self, home: Path) -> None:
        assert "OWN_PROBE" in log_text(home, "INFO")

    def test_library_warnings_reach_the_log_file(self, home: Path) -> None:
        """Library records reach our file only if our handlers are attached to their loggers;
        otherwise they fall through to Python's last-resort stderr handler."""
        assert "LIB_WARNING_PROBE" in log_text(home, "INFO")

    def test_library_debug_is_suppressed_outside_debug_mode(self, home: Path) -> None:
        assert "LIB_DEBUG_PROBE" not in log_text(home, "INFO")

    def test_debug_mode_includes_library_debug(self, home: Path) -> None:
        assert "LIB_DEBUG_PROBE" in log_text(home, "DEBUG")


class TestCredentialPayloadsAreDropped:
    """google-auth puts the request URL, raw headers and body in record.extra. Today's
    %(message)s formatter happens to discard them; the drop must be deliberate, or switching
    to a structured formatter would start writing tokens into the log file (#98)."""

    def test_a_record_carrying_an_http_payload_is_not_written(self, home: Path) -> None:
        text = log_text(home, "DEBUG")
        assert "PAYLOAD_PROBE" not in text
        assert "RESPONSE_PAYLOAD_PROBE" not in text

    def test_ordinary_library_debug_still_gets_through(self, home: Path) -> None:
        assert "LIB_DEBUG_PROBE" in log_text(home, "DEBUG")
