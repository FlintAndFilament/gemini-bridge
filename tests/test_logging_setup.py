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
import gemini_bridge.__main__  # configures logging on import

logging.getLogger("gemini_bridge.probe").info("OWN_PROBE")
logging.getLogger("httpx").warning("LIB_WARNING_PROBE")
logging.getLogger("google").debug("LIB_DEBUG_PROBE")
"""


def log_text(home: Path, level: str) -> str:
    result = subprocess.run(
        [sys.executable, "-c", PROBE],
        capture_output=True,
        text=True,
        env={"HOME": str(home), "PATH": "/usr/bin:/bin", "GEMINI_BRIDGE_LOG_LEVEL": level},
    )
    assert result.returncode == 0, result.stderr
    logs = list((home / ".config/gemini-bridge/logs").glob("*-gemini-bridge.log"))
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
