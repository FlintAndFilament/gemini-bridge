"""
gemini_bridge/__main__.py
--------------------------
Entry point: python -m gemini_bridge

Responsibilities:
  - Configure structured logging (file + stderr) before any other import runs
  - Load config from ~/.config/gemini-bridge/config.json
  - Build credentials via auth.build_auth()
  - Instantiate GeminiClient and TranscriptWriter
  - Build and run the MCP server
  - Report startup errors with actionable messages

Design notes:
  - Single Responsibility: startup orchestration only; all logic lives in imported modules
  - Dependency Inversion: passes ready-made Config, Credentials, Client, Transcript to server
  - Logging is configured first so all downstream modules inherit the handler/formatter

Log files:
  ~/.config/gemini-bridge/logs/YYYYMMDD-gemini-bridge.log
  Daily files; 4 most recent kept; multiple sessions same day append to same file.
  Tail: tail -f ~/.config/gemini-bridge/logs/$(ls -t ~/.config/gemini-bridge/logs/*.log | head -1 | xargs basename)

Environment variables:
  GEMINI_BRIDGE_LOG_LEVEL  — DEBUG | INFO | WARNING | ERROR (default: INFO)

Raises:
  SystemExit(1) — on config or auth failure at startup

Used by:  pyproject.toml [project.scripts], MCP registration (python -m gemini_bridge)
Imports:  config.py, auth.py, client.py, transcript.py, server.py
"""

import logging
import os
import sys
from datetime import datetime
from pathlib import Path

_LOG_DIR = Path.home() / ".config" / "gemini-bridge" / "logs"
_MAX_LOG_FILES = 4
# Third-party loggers worth routing into our log file: the SDK, its transport, and httpx
# (used by sources.py to resolve grounding redirects).
_LIBRARY_LOGGERS = ("google", "google_genai", "httpx", "httpcore", "urllib3")


def _setup_log_file(startup_time: datetime) -> logging.FileHandler:
    """Create today's log file, prune files beyond _MAX_LOG_FILES."""
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = _LOG_DIR / f"{startup_time.strftime('%Y%m%d')}-gemini-bridge.log"
    # Prune oldest files, keeping _MAX_LOG_FILES most recent
    existing = sorted(_LOG_DIR.glob("*-gemini-bridge.log"), reverse=True)
    for old in existing[_MAX_LOG_FILES:]:
        try:
            old.unlink()
        except OSError:
            pass
    return logging.FileHandler(log_file, mode="a", encoding="utf-8")


class _DropHttpPayloads(logging.Filter):
    """Drop records carrying a google-auth HTTP payload (#98).

    `google.auth.transport.requests` logs the request URL, the **raw** headers and the body
    under `extra={"httpRequest"/"httpResponse": ...}` at DEBUG. It hashes access and refresh
    tokens but not a service-account assertion JWT, and headers carry bearer tokens verbatim.
    Our `%(message)s` formatter renders no extras, so none of it reaches the log file today —
    but that is incidental, and a structured formatter would silently start writing credential
    material into a file that lives for days. Dropping the records makes the property explicit.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        return not (hasattr(record, "httpRequest") or hasattr(record, "httpResponse"))


def _configure_logging(startup_time: datetime) -> None:
    """Set up file + stderr logging before any module that calls getLogger() is imported."""
    raw_level = os.environ.get("GEMINI_BRIDGE_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, raw_level, logging.INFO)
    formatter = logging.Formatter(
        fmt="[gemini-bridge] %(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    # On the handlers, not the loggers: a logger's filters do not run for records that
    # propagate up from its children, so google.auth.transport.requests would bypass one
    # attached to "google".
    payload_filter = _DropHttpPayloads()
    root = logging.getLogger("gemini_bridge")
    root.setLevel(level)
    # File handler — primary; visible via tail
    file_handler = _setup_log_file(startup_time)
    file_handler.setFormatter(formatter)
    file_handler.addFilter(payload_filter)
    root.addHandler(file_handler)
    # Stderr handler — useful when running server manually; swallowed by Claude Code
    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setFormatter(formatter)
    stderr_handler.addFilter(payload_filter)
    root.addHandler(stderr_handler)
    # Third-party loggers need our handlers attached explicitly (#88): the handlers above sit
    # on the "gemini_bridge" logger, which their records never pass through, so without this
    # a library record reaches nothing but Python's last-resort stderr handler — DEBUG mode
    # showed no library output, and setting their level alone did nothing. Below DEBUG they
    # stay at WARNING: httpx logs every request at INFO.
    library_level = level if level <= logging.DEBUG else logging.WARNING
    for name in _LIBRARY_LOGGERS:
        library = logging.getLogger(name)
        library.setLevel(library_level)
        library.addHandler(file_handler)
        library.addHandler(stderr_handler)


_startup_time = datetime.now()
_configure_logging(_startup_time)

# Use explicit package path — __name__ is "__main__" when run via python3 -m gemini_bridge,
# which is not a child of "gemini_bridge" and would bypass our file handler.
_log = logging.getLogger("gemini_bridge.__main__")


def main() -> None:
    startup_time = _startup_time

    from gemini_bridge.auth import AuthError, build_auth
    from gemini_bridge.client import GeminiClient
    from gemini_bridge.config import ConfigError, load_config
    from gemini_bridge.sandbox import SandboxError
    from gemini_bridge.server import build_server
    from gemini_bridge.transcript import TranscriptError, TranscriptWriter
    from gemini_bridge.workspace import build_workspace

    try:
        config = load_config()
    except ConfigError as exc:
        _log.error("startup failed — config error: %s", exc)
        sys.exit(1)

    try:
        auth_result = build_auth(config.auth)
    except AuthError as exc:
        _log.error("startup failed — auth error: %s", exc)
        sys.exit(1)

    client = GeminiClient(config, credentials=auth_result.credentials, api_key=auth_result.api_key)
    latest = client.refresh_latest()  # one models.list call; pinned defaults if it fails
    backend = (
        "auth=api_key"
        if config.auth.method == "api_key"
        else f"auth={config.auth.method} location={config.location}"
    )
    _log.info(
        "starting — %s default_thinking=%s default_model=%s fallback_model=%s",
        backend,
        config.default_thinking,
        client.default_model,
        client.fallback_model,
    )
    _log.info(
        "latest models: %s",
        ", ".join(f"{family}={mid}" for family, mid in latest.items()) or "unresolved (pinned)",
    )
    try:
        transcript = TranscriptWriter(config.transcript_dir, startup_time)
    except TranscriptError as exc:
        _log.error("startup failed — %s", exc)
        sys.exit(1)

    _log.info("transcript → %s", transcript.path)

    try:
        workspace = build_workspace(config, Path.cwd())
    except SandboxError as exc:
        _log.error("startup failed — artifacts_dir %r: %s", config.artifacts_dir, exc)
        sys.exit(1)
    _log.info(
        "file tools %s — sandbox root %s — artifacts → %s",
        "enabled" if workspace.tools_enabled else f"DISABLED ({workspace.disabled_reason})",
        workspace.sandbox.root,
        workspace.artifacts.directory,
    )

    server = build_server(client, transcript, workspace)
    server.run()


if __name__ == "__main__":
    main()
