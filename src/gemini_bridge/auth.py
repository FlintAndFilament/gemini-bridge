"""
gemini_bridge/auth.py
---------------------
Credential loading for all supported authentication methods.

Responsibilities:
  - Load Application Default Credentials (ADC) — default method
  - Load service account credentials from GOOGLE_APPLICATION_CREDENTIALS env var
  - Load service account credentials from Apple Keychain (macOS)
  - Read the Google AI Studio API key from the configured env var (api_key method)
  - Build a google.auth.credentials.Credentials instance for any configured method

Design notes:
  - Single Responsibility: credential loading only; SDK client construction is in client.py
  - Open/Closed: add a new method by adding a loader function + one entry in _LOADERS —
    build_credentials() requires no modification
  - Liskov Substitution: all paths return google.auth.credentials.Credentials; callers are agnostic
  - Dependency Inversion: client.py depends on the Credentials abstraction, not concrete loaders

Raises:
  AuthError — wraps all credential failures with actionable messages for Claude to surface

Used by:  __main__.py -> build_auth()
Imports:  config.py (AuthConfig, ConfigError)
"""

import json
import logging
import os
import subprocess
from dataclasses import dataclass
from typing import Callable, Optional

_log = logging.getLogger(__name__)

import google.auth
import google.auth.credentials
import google.auth.exceptions
from google.oauth2 import service_account

from gemini_bridge.config import AuthConfig, ConfigError

_VERTEX_SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]

# All loaders receive the full AuthConfig so parameterized methods (keychain)
# can access their fields without special-casing in build_credentials().
_CredLoader = Callable[[AuthConfig], google.auth.credentials.Credentials]


class AuthError(Exception):
    """Raised when credential loading fails. Message is user-actionable."""


@dataclass
class AuthResult:
    """Unified auth output — exactly one field is non-None after build_auth() succeeds."""

    credentials: Optional[google.auth.credentials.Credentials] = None
    api_key: Optional[str] = None


def _load_adc(auth_config: AuthConfig) -> google.auth.credentials.Credentials:
    try:
        credentials, _ = google.auth.default(scopes=_VERTEX_SCOPES)
        _log.debug("adc credentials loaded")
        return credentials
    except google.auth.exceptions.DefaultCredentialsError as exc:
        _log.error("adc credential load failed: %s", exc)
        raise AuthError(
            "Gemini auth error: no ADC credentials found.\n"
            "Fix: gcloud auth application-default login"
        ) from exc


def _load_env(auth_config: AuthConfig) -> google.auth.credentials.Credentials:
    """Load service account credentials from GOOGLE_APPLICATION_CREDENTIALS env var."""
    try:
        credentials, _ = google.auth.default(scopes=_VERTEX_SCOPES)
        _log.debug("env credentials loaded")
        return credentials
    except google.auth.exceptions.DefaultCredentialsError as exc:
        _log.error("env credential load failed: %s", exc)
        raise AuthError(
            "Gemini auth error: GOOGLE_APPLICATION_CREDENTIALS not set or file unreadable.\n"
            "Fix: export GOOGLE_APPLICATION_CREDENTIALS=/path/to/sa-key.json"
        ) from exc


def _load_keychain(auth_config: AuthConfig) -> google.auth.credentials.Credentials:
    """Load service account JSON from Apple Keychain (macOS only)."""
    # model_validator guarantees non-None when method="keychain"; or-defaults are a safety net
    service = auth_config.keychain_service or "gemini-bridge"
    account = auth_config.keychain_account or "vertex-sa"
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", service, "-a", account, "-w"],
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        _log.error("keychain item not found (service=%r, account=%r)", service, account)
        raise AuthError(
            f"Gemini auth error: Keychain item not found "
            f"(service={service!r}, account={account!r}).\n"
            "Fix: re-run setup.sh to store the service account key."
        ) from exc
    except FileNotFoundError as exc:
        _log.error("security CLI not found — Keychain auth requires macOS")
        raise AuthError(
            "Gemini auth error: 'security' CLI not found. Keychain auth requires macOS."
        ) from exc

    raw = result.stdout.strip()
    # macOS Keychain stores multi-line values (like SA JSON) as binary and returns
    # them as a lowercase hex string via security(1) -w. Detect and decode.
    if raw and all(c in "0123456789abcdef" for c in raw):
        try:
            raw = bytes.fromhex(raw).decode("utf-8")
        except (ValueError, UnicodeDecodeError) as exc:
            _log.error("keychain value hex decode failed: %s", exc)
            raise AuthError(
                "Gemini auth error: Keychain value could not be decoded.\n"
                "Fix: re-run setup.sh to re-store the service account key."
            ) from exc
    try:
        sa_info = json.loads(raw)
    except json.JSONDecodeError as exc:
        _log.error("keychain value is not valid JSON: %s", exc)
        raise AuthError(
            "Gemini auth error: Keychain value is not valid service account JSON.\n"
            "Fix: re-run setup.sh to re-store the service account key."
        ) from exc

    _log.debug("keychain credentials loaded (service=%r, account=%r)", service, account)

    return service_account.Credentials.from_service_account_info(sa_info, scopes=_VERTEX_SCOPES)


def _looks_like_api_key(value: str) -> bool:
    """Return True if value looks like an API key rather than an env var name."""
    return value.startswith("AIza") or not value.replace("_", "").isupper() or len(value) > 40


def _load_api_key(auth_config: AuthConfig) -> str:
    """Read Gemini Developer API key from the configured env var. Raises AuthError if unset."""
    env_var = auth_config.api_key_env or "GEMINI_API_KEY"
    if _looks_like_api_key(env_var):
        _log.error("api_key_env looks like a key value, not a variable name")
        raise AuthError(
            "Gemini config error: 'api_key_env' in config.json contains what looks like "
            "an API key, not an environment variable name.\n"
            'Fix: set api_key_env to the variable name (e.g. "GEMINI_API_KEY"), '
            "then export that variable:\n"
            "  export GEMINI_API_KEY=<your-key>\n"
            "Edit ~/.config/gemini-bridge/config.json or re-run setup.sh."
        )
    key = os.environ.get(env_var, "").strip()
    if not key:
        _log.error("api_key env var not set or empty: %s", env_var)
        raise AuthError(
            f"Gemini auth error: env var {env_var!r} is not set or is empty.\n"
            f"Fix: export {env_var}=<your-Google-AI-Studio-key>\n"
            "Get a key at: https://aistudio.google.com/apikey"
        )
    _log.debug("api_key loaded from %s", env_var)
    return key


_LOADERS: dict[str, _CredLoader] = {
    "adc": _load_adc,
    "env": _load_env,
    "keychain": _load_keychain,
}


def build_credentials(auth_config: AuthConfig) -> google.auth.credentials.Credentials:
    """Build Vertex AI credentials from the given auth config. Raises AuthError on failure."""
    loader = _LOADERS.get(auth_config.method)
    if loader is None:
        raise ConfigError(f"Unknown auth method: {auth_config.method!r}")
    return loader(auth_config)


def build_auth(auth_config: AuthConfig) -> AuthResult:
    """Unified auth dispatch for all methods. Raises AuthError or ConfigError on failure."""
    if auth_config.method == "api_key":
        return AuthResult(api_key=_load_api_key(auth_config))
    loader = _LOADERS.get(auth_config.method)
    if loader is None:
        raise ConfigError(f"Unknown auth method: {auth_config.method!r}")
    return AuthResult(credentials=loader(auth_config))
