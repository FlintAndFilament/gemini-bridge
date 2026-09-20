#!/usr/bin/env bash
# setup.sh — gemini-bridge interactive configuration wizard
# Run once after "python3 -m pip install -e ." to create ~/.config/gemini-bridge/config.json
# Safe to re-run: overwrites existing config with new values. Existing values
# are read and used as prompt defaults so you only change what you want.

set -euo pipefail

CONFIG_DIR="$HOME/.config/gemini-bridge"
CONFIG_FILE="$CONFIG_DIR/config.json"

info()  { printf '\033[0;32m%s\033[0m\n' "$*"; }
warn()  { printf '\033[0;33mWARN: %s\033[0m\n' "$*" >&2; }
error() { printf '\033[0;31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

ask() {
    local prompt="$1" default="${2:-}"
    if [[ -n "$default" ]]; then
        printf '%s [%s]: ' "$prompt" "$default" >&2
    else
        printf '%s: ' "$prompt" >&2
    fi
    read -r REPLY
    echo "${REPLY:-$default}"
}

# --- load defaults from existing config ---
# Variables set here are used as prompt defaults throughout the wizard.
PREV_AUTH_METHOD="adc"
PREV_PROJECT=""
PREV_LOCATION="global"
PREV_THINKING="medium"
PREV_DEFAULT_MODEL=""
PREV_TRANSCRIPT_DIR="./session-summaries"
PREV_KEYCHAIN_SERVICE="gemini-bridge"
PREV_KEYCHAIN_ACCOUNT="vertex-sa"

if [[ -f "$CONFIG_FILE" ]]; then
    # shlex.quote ensures config values with spaces or special chars are safe to eval
    eval "$(python3 -c "
import json, shlex, sys
try:
    with open(sys.argv[1]) as f:
        d = json.load(f)
    auth = d.get('auth', {})
    fields = [
        ('PREV_AUTH_METHOD',      auth.get('method', 'adc')),
        ('PREV_PROJECT',          d.get('project', '')),
        ('PREV_LOCATION',         d.get('location', 'global')),
        ('PREV_THINKING',         d.get('default_thinking', 'medium')),
        ('PREV_DEFAULT_MODEL',    d.get('default_model', '')),
        ('PREV_TRANSCRIPT_DIR',   d.get('transcript_dir', './session-summaries')),
        ('PREV_KEYCHAIN_SERVICE', auth.get('keychain_service', 'gemini-bridge')),
        ('PREV_KEYCHAIN_ACCOUNT', auth.get('keychain_account', 'vertex-sa')),
    ]
    for k, v in fields:
        print(f'{k}={shlex.quote(str(v))}')
except Exception:
    pass
" "$CONFIG_FILE" 2>/dev/null || true)"
fi

# Map existing auth method to menu choice number
case "$PREV_AUTH_METHOD" in
    "adc")      PREV_AUTH_CHOICE="1" ;;
    "env")      PREV_AUTH_CHOICE="2" ;;
    "keychain") PREV_AUTH_CHOICE="3" ;;
    "api_key")  PREV_AUTH_CHOICE="4" ;;
    *)          PREV_AUTH_CHOICE="1" ;;
esac

echo
info "=== gemini-bridge setup ==="
[[ -f "$CONFIG_FILE" ]] && info "(existing config loaded as defaults)"
echo

# --- auth method ---
echo "Auth method:"
echo "  1) adc      — Application Default Credentials (recommended)"
echo "  2) env      — GOOGLE_APPLICATION_CREDENTIALS env var (service account key file)"
echo "  3) keychain — Service account JSON stored in Apple Keychain (macOS only)"
echo "  4) api_key  — Google AI Studio API key (no GCP project needed)"
AUTH_CHOICE=$(ask "Choice" "$PREV_AUTH_CHOICE")
case "$AUTH_CHOICE" in
    1|adc)      AUTH_METHOD="adc" ;;
    2|env)      AUTH_METHOD="env" ;;
    3|keychain) AUTH_METHOD="keychain" ;;
    4|api_key)  AUTH_METHOD="api_key" ;;
    *) error "Invalid choice: $AUTH_CHOICE" ;;
esac

if [[ "$AUTH_METHOD" == "adc" ]]; then
    info "Checking ADC..."
    if ! gcloud auth application-default print-access-token &>/dev/null; then
        error "ADC not configured. Run: gcloud auth application-default login"
    fi
    info "ADC OK"
fi

if [[ "$AUTH_METHOD" == "env" ]]; then
    if [[ -z "${GOOGLE_APPLICATION_CREDENTIALS:-}" ]]; then
        warn "GOOGLE_APPLICATION_CREDENTIALS is not set in this shell."
        echo "  Set it before starting Claude Code:"
        echo "    export GOOGLE_APPLICATION_CREDENTIALS=/path/to/sa-key.json"
        echo "  (Proceeding — you can set the env var later)"
    elif [[ ! -f "${GOOGLE_APPLICATION_CREDENTIALS}" ]]; then
        error "GOOGLE_APPLICATION_CREDENTIALS is set but file not found: ${GOOGLE_APPLICATION_CREDENTIALS}"
    else
        info "GOOGLE_APPLICATION_CREDENTIALS OK: ${GOOGLE_APPLICATION_CREDENTIALS}"
    fi
fi

# Keychain auth: prompt for service/account names and verify the item exists
KEYCHAIN_SERVICE="$PREV_KEYCHAIN_SERVICE"
KEYCHAIN_ACCOUNT="$PREV_KEYCHAIN_ACCOUNT"

if [[ "$AUTH_METHOD" == "keychain" ]]; then
    if [[ "$(uname)" != "Darwin" ]]; then
        error "Keychain auth requires macOS. Use adc or env on Linux."
    fi
    echo
    echo "Keychain item to read at server startup:"
    KEYCHAIN_SERVICE=$(ask "Keychain service name" "$PREV_KEYCHAIN_SERVICE")
    KEYCHAIN_ACCOUNT=$(ask "Keychain account name" "$PREV_KEYCHAIN_ACCOUNT")
    # Reject chars that would break JSON string interpolation in config.json
    if [[ "$KEYCHAIN_SERVICE" =~ [\"\\] ]] || [[ "$KEYCHAIN_ACCOUNT" =~ [\"\\] ]]; then
        error "Keychain service/account names must not contain quotes or backslashes."
    fi
    echo
    info "Verifying keychain item..."
    if ! SECRET=$(security find-generic-password -s "$KEYCHAIN_SERVICE" -a "$KEYCHAIN_ACCOUNT" -w 2>/dev/null); then
        echo "  Keychain item not found."
        echo "  Store your service account JSON first:"
        echo "    security add-generic-password \\"
        echo "      -s \"$KEYCHAIN_SERVICE\" \\"
        echo "      -a \"$KEYCHAIN_ACCOUNT\" \\"
        echo "      -w \"\$(cat /path/to/sa-key.json)\""
        echo "    rm /path/to/sa-key.json   # remove disk copy"
        error "Keychain item '$KEYCHAIN_SERVICE'/'$KEYCHAIN_ACCOUNT' not found. Store it first, then re-run setup."
    fi
    # Validate JSON — pipe directly from security to avoid echo variable-expansion issues.
    # macOS stores multi-line values as binary and returns them hex-encoded via -w; decode first.
    if ! security find-generic-password -s "$KEYCHAIN_SERVICE" -a "$KEYCHAIN_ACCOUNT" -w 2>/dev/null | \
        python3 -c "
import sys, json
raw = sys.stdin.read().strip()
if raw and all(c in '0123456789abcdef' for c in raw):
    raw = bytes.fromhex(raw).decode('utf-8')
json.loads(raw)
" 2>/dev/null; then
        error "Keychain item found but is not valid JSON. Re-store the service account key."
    fi
    info "Keychain item OK"
fi

# API key auth: prompt for env var name and warn if not currently set
API_KEY_ENV="GEMINI_API_KEY"
if [[ "$AUTH_METHOD" == "api_key" ]]; then
    echo
    echo "Get a key at: https://aistudio.google.com/apikey"
    echo
    echo "Enter the NAME of the environment variable that will hold your API key."
    echo "  >>> Do NOT paste the key here — enter a variable name like GEMINI_API_KEY <<<"
    PREV_API_KEY_ENV=$(python3 -c "
import json, pathlib, sys
p = pathlib.Path('$CONFIG_FILE')
if p.exists():
    d = json.loads(p.read_text())
    print(d.get('auth', {}).get('api_key_env', 'GEMINI_API_KEY'))
else:
    print('GEMINI_API_KEY')
" 2>/dev/null || echo "GEMINI_API_KEY")
    # Validate: must look like an env var name — uppercase/underscores, not an API key
    while true; do
        API_KEY_ENV=$(ask "Env var name (e.g. GEMINI_API_KEY)" "$PREV_API_KEY_ENV")
        [[ -z "$API_KEY_ENV" ]] && API_KEY_ENV="GEMINI_API_KEY"
        # Reject if it looks like an actual key: starts with AIza, contains lowercase, or is very long
        if [[ "$API_KEY_ENV" =~ ^AIza ]] || [[ "$API_KEY_ENV" =~ [a-z] ]] || [[ ${#API_KEY_ENV} -gt 40 ]]; then
            warn "That looks like an API key value, not a variable name."
            echo "  Enter a variable name like GEMINI_API_KEY, not the key itself."
            PREV_API_KEY_ENV="GEMINI_API_KEY"
            continue
        fi
        break
    done
    if [[ -z "${!API_KEY_ENV:-}" ]]; then
        warn "$API_KEY_ENV is not set in this shell."
        echo "  Export it (keep the quotes — keys can contain special characters):"
        echo "    export $API_KEY_ENV=\"your-key-here\""
        echo "  The exact 'claude mcp add' command is printed at the end of setup."
        echo "  (Proceeding — you can set the env var later)"
    else
        info "$API_KEY_ENV is set"
    fi
fi

# --- project (skipped for api_key mode) ---
PROJECT=""
if [[ "$AUTH_METHOD" != "api_key" ]]; then
# Prefer gcloud active project; fall back to existing config value
DEFAULT_PROJECT="${PREV_PROJECT:-$(gcloud config get-value project 2>/dev/null || true)}"
PROJECT=$(ask "GCP project" "$DEFAULT_PROJECT")
[[ -z "$PROJECT" ]] && error "GCP project is required."
fi

# --- location (Vertex only; api_key uses Developer API endpoint, location unused) ---
LOCATION="global"
if [[ "$AUTH_METHOD" != "api_key" ]]; then
    echo
    echo "Location (Vertex AI endpoint; 'global' is recommended and works for all models):"
    echo "  global — recommended; routes to lowest-latency region automatically"
    echo "  us-central1, us-east4, europe-west1, asia-northeast1, etc."
    LOCATION=$(ask "Location" "$PREV_LOCATION")
    [[ -z "$LOCATION" ]] && error "Location is required."
fi

# --- thinking level ---
echo
echo "Default thinking level:"
echo "  none   — no extended thinking (fastest)"
echo "  low    — light reasoning"
echo "  medium — balanced (recommended)"
echo "  high   — deep reasoning"
THINKING=$(ask "Level" "$PREV_THINKING")
case "$THINKING" in
    none|low|medium|high) ;;
    *) error "Invalid thinking level: $THINKING" ;;
esac

# --- default model (optional; blank = newest Flash, resolved at server start) ---
echo
echo "Default model for calls that omit an explicit model (blank = newest Flash, found at startup):"
echo "  aliases that track new releases: flash · flash-lite · pro — or pin an id, e.g. gemini-3.5-flash"
echo "  (a per-call model= always overrides this; run gemini_list_models later for the full list)"
DEFAULT_MODEL=$(ask "Default model (blank for newest Flash)" "$PREV_DEFAULT_MODEL")

# --- transcript dir ---
echo
TRANSCRIPT_DIR=$(ask "Transcript directory" "$PREV_TRANSCRIPT_DIR")

# --- write config ---
mkdir -p "$CONFIG_DIR"

# Build auth JSON block — varies by method
if [[ "$AUTH_METHOD" == "keychain" ]]; then
    AUTH_JSON="\"auth\": {
    \"method\": \"keychain\",
    \"keychain_service\": \"$KEYCHAIN_SERVICE\",
    \"keychain_account\": \"$KEYCHAIN_ACCOUNT\"
  }"
elif [[ "$AUTH_METHOD" == "api_key" ]]; then
    AUTH_JSON="\"auth\": {
    \"method\": \"api_key\",
    \"api_key_env\": \"$API_KEY_ENV\"
  }"
else
    AUTH_JSON="\"auth\": {
    \"method\": \"$AUTH_METHOD\"
  }"
fi

# Optional default_model line — included only when the user supplied a value.
DEFAULT_MODEL_JSON=""
if [[ -n "$DEFAULT_MODEL" ]]; then
    DEFAULT_MODEL_JSON=$'  "default_model": "'"$DEFAULT_MODEL"$'",\n'
fi

# api_key mode: project and location are not needed (Developer API endpoint)
if [[ "$AUTH_METHOD" == "api_key" ]]; then
cat > "$CONFIG_FILE" <<EOF
{
  "default_thinking": "$THINKING",
$DEFAULT_MODEL_JSON  "transcript_dir": "$TRANSCRIPT_DIR",
  $AUTH_JSON
}
EOF
else
cat > "$CONFIG_FILE" <<EOF
{
  "project": "$PROJECT",
  "location": "$LOCATION",
  "default_thinking": "$THINKING",
$DEFAULT_MODEL_JSON  "transcript_dir": "$TRANSCRIPT_DIR",
  $AUTH_JSON
}
EOF
fi

info "Config written to $CONFIG_FILE"
echo
info "=== Next steps ==="
echo
echo "  python3 -m pip install -e ."
if [[ "$AUTH_METHOD" == "api_key" ]]; then
    echo
    echo "  # 1. Export your API key. Keep the quotes — AI Studio keys can contain"
    echo "  #    characters your shell would otherwise interpret."
    echo "  export $API_KEY_ENV=\"your-key-here\""
    echo
    echo "  # 2. Register the server. The -e flag injects the key into the server's"
    echo "  #    own environment (a plain shell export does NOT reliably reach the MCP"
    echo "  #    subprocess). The server name 'gemini-bridge' MUST come first — omit it"
    echo "  #    and 'claude mcp add' treats 'python3' as the name and fails to connect."
    echo "  claude mcp add gemini-bridge -s user -e $API_KEY_ENV=\"\$$API_KEY_ENV\" -- python3 -m gemini_bridge"
else
    echo "  claude mcp add gemini-bridge -s user -- python3 -m gemini_bridge"
fi
echo "  claude mcp list"
echo
info "Model selection is per-call (no model in config):"
echo "  • Default model: newest Flash, resolved at server start (falls back to newest Flash-Lite on overload)"
echo "  • Pass model='<id>' to any tool to override; call gemini_list_models to see valid ids"
echo "  • Aliases flash / flash-lite / pro track the newest release on every backend"
echo
info "Done. Restart Claude Code to activate gemini-bridge."
