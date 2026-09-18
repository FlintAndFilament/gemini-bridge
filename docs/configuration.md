# Configuration Reference

**Config file location:** `~/.config/gemini-bridge/config.json`

Created by `bash setup.sh`. Safe to edit by hand.

## Full example

```json
{
  "project": "my-gcp-project",
  "location": "global",
  "default_thinking": "medium",
  "transcript_dir": "./session-summaries",
  "artifacts_dir": "./gemini-artifacts",
  "file_tools": {
    "enabled": true,
    "max_write_bytes": 262144
  },
  "auth": {
    "method": "adc"
  }
}
```

> **Model selection:** set the optional `default_model` field to change the server default; leave
> it unset for the built-in default (`gemini-3.5-flash`). Individual calls always override it
> **per call** via the `model=` parameter. See [Choosing a model](#choosing-a-model).

## Field reference

### `project`

**Type:** string
**Example:** `"my-gcp-project"`
**Required when:** `auth.method` is `adc`, `env`, or `keychain`
**Omit when:** `auth.method = "api_key"` (Developer API does not use a GCP project)

Your GCP project ID. Must have the Vertex AI API enabled and `roles/aiplatform.user`
granted to your ADC credentials or service account.

---

### `location`

**Type:** string
**Default:** `"global"`
**Example:** `"us-central1"`

Vertex AI location. `"global"` works for all models and is the recommended default. Only used
by the Vertex AI backends (`adc`/`env`/`keychain`); ignored in `api_key` mode.

`"global"` routes to the lowest-latency region automatically and is the safest choice —
newer models (e.g. `gemini-3.5-flash`) are frequently **global-only**. Set a specific region
(e.g. `us-central1`, `europe-west1`) only if you have a data-residency requirement, and confirm
your chosen model is offered there; a model not served in your region returns a 404 at call time.

> **Note:** The server does not currently validate location against the chosen model at startup —
> `"global"` is recommended precisely because it sidesteps per-model regional gaps.
> (Backend/location-aware validation is tracked as a future enhancement.)

---

### `default_model`

**Type:** string (optional)
**Default:** unset → the built-in default (`gemini-3.5-flash`)

Sets the default model for tool calls that omit the `model` parameter. Leave it unset to use the
server's built-in default. Individual calls always override it via `model=`. Accepts any model the
bridge can run (`gemini-2*` / `gemini-3*` or a `-latest` alias); an invalid value raises a
`ClientError` at call time (there is no config-load validation yet — see #44). The backend-aware
schema hint and `gemini_list_models` reflect the effective default. See
[Choosing a model](#choosing-a-model).

---

### `default_thinking`

**Type:** string
**Default:** `"medium"`
**Valid values:** `none`, `low`, `medium`, `high`

Used when a tool call omits the `thinking` parameter. Claude overrides this per call when
it judges a different level is appropriate.

---

### `transcript_dir`

**Type:** string (path, `~` and `.` expanded relative to Claude Code's working directory)
**Default:** `"./session-summaries"`

Directory where transcript files are written. Created if it doesn't exist. Transcript files
are named `YYYYMMDD-HHMM-gemini-bridge-transcript.md` using the server startup time.

The default `./session-summaries` resolves relative to the project root where Claude Code
was launched — transcripts land in `your-project/session-summaries/` automatically, one
directory per project. Override with an absolute path (e.g. `"~/gemini-transcripts"`) to
collect transcripts globally instead.

---

### `artifacts_dir`

**Type:** string (path, relative to Claude Code's working directory)
**Default:** `"./gemini-artifacts"`

Where `gemini_architect` and `gemini_review` (and `gemini_brainstorm` with `write_artifact=true`)
save their answers. Created on first save. It **must lie inside the project root and outside the
deny-list** — the server refuses to start otherwise. Add it to `.gitignore` or commit it, per
project.

---

### `file_tools.enabled`

**Type:** boolean
**Default:** `true`

Master switch for Gemini's repository access (`list_dir`, `glob`, `grep`, `read_file`,
`write_file`). `false` declares no tools to Gemini at all — calls behave exactly as before this
feature. Artifacts are unaffected (they are written by the bridge, not by Gemini).

File tools are also switched off automatically when Claude Code is launched from your home
directory, the filesystem root, or any directory containing your home directory — a sandbox
rooted there would expose `~/.ssh`, cloud credentials, and shell history.

---

### `file_tools.deny`

**Type:** list of glob patterns
**Default:** `[".git/**", ".env", ".env.*", "**/*.pem", "**/*.key", "**/*.p12", "**/*.pfx", "**/id_rsa*", "**/id_dsa*", "**/id_ecdsa*", "**/id_ed25519*", "**/*credentials*.json", "**/*-sa-key.json", ".ssh/**", ".aws/**", ".gnupg/**", ".netrc", ".npmrc", ".pypirc"]`

Paths Gemini can never read, list, search, or write, even inside the project root. Matched
case-insensitively **at any depth**: a pattern without `/` matches a file or directory name
anywhere; `dir/**` covers any directory named `dir` and everything in it (so `.git/**` also
covers a nested `vendor/lib/.git/`).
**Setting this replaces the defaults** — copy them into your list if you want to keep them.

---

### `file_tools.max_write_bytes`

**Type:** positive integer
**Default:** `262144` (256 KiB)

Largest content a single `write_file` call may write.

---

### `auth.method`

**Type:** string
**Default:** `"adc"`
**Valid values:** `adc`, `env`, `keychain`, `api_key`

See [auth.md](auth.md) for full setup instructions for each method.

---

### `auth.keychain_service`

**Type:** string
**Default:** `"gemini-bridge"`
**Only used when:** `auth.method = "keychain"`

The service name used in `security find-generic-password -s {service}`.

---

### `auth.keychain_account`

**Type:** string
**Default:** `"vertex-sa"`
**Only used when:** `auth.method = "keychain"`

The account name used in `security find-generic-password -a {account}`.

---

### `auth.api_key_env`

**Type:** string
**Default:** `"GEMINI_API_KEY"`
**Only used when:** `auth.method = "api_key"`

The name of the environment variable holding your Google AI Studio API key. The key itself
is never stored in `config.json` — only the variable name. At server startup, the server
reads the key from this env var and raises an error if it is unset or empty.

Common values: `"GEMINI_API_KEY"` (AI Studio default), `"GOOGLE_API_KEY"` (alternative).
If both are set in your shell, `GOOGLE_API_KEY` takes precedence in the SDK.

---

## Choosing a model

Every tool accepts an optional `model=` parameter; omit it to use the server default.

- **Default:** the `default_model` config field if set, else the built-in `gemini-3.5-flash`
  (`DEFAULT_MODEL`) — this effective default is what omitted-`model` calls use.
- **Fallback:** if the requested/default model returns a terminal overload (503/429) after
  retries, the call is retried once against `gemini-3.1-flash-lite` (`FALLBACK_MODEL`) and the
  response is prefixed with a visible `[gemini-bridge notice]` so the substitution is never silent.
- **Discovery:** the `model` parameter's description is **backend-aware** (it lists the models
  valid for your active backend), and the `gemini_list_models` tool returns the live, chat-only
  catalog. See [tools.md](tools.md#gemini_list_models).

### Recommended models by backend

| Backend (`auth.method`) | Recommended | Notes |
|---|---|---|
| **Developer API** (`api_key`) | `gemini-3.5-flash` (default), `gemini-3.1-flash-lite`, `gemini-flash-latest`, `gemini-pro-latest` | `-latest` aliases auto-track the newest release |
| **Vertex AI** (`adc`/`env`/`keychain`) | `gemini-3.5-flash` (default), `gemini-3.1-flash-lite`, `gemini-3.1-pro-preview`, `gemini-2.5-pro` | No `-latest` aliases — they 404 on Vertex; use versioned names |

> **Gemini 2.5 retires 2026-10-16.** The shortlists above lead with the long-lived Gemini 3.x GA
> models. `gemini-2.5-flash`/`-pro` still work (and appear in `gemini_list_models`) until then;
> `gemini-2.5-pro` is kept as the Vertex stable-Pro option until it retires.

The `-latest` aliases (e.g. `gemini-flash-latest`) are a **Developer-API-only** convention.
On Vertex AI they return 404 — the bridge logs a warning if you pass one under a Vertex backend.

> **Model families and thinking levels:** the generation prefix determines how the `thinking`
> level maps to API parameters — `gemini-2*` (and `-latest` aliases) use a token `thinking_budget`;
> `gemini-3*` uses a `thinking_level` enum. Both dotted (`gemini-3.5-flash`) and hyphenated
> preview (`gemini-3-pro-preview`) forms are recognized, so previews are usable. An unrecognized
> name raises a `ClientError` at call time. See [tools.md](tools.md) for the thinking-level table.

### How a model value is resolved

```mermaid
flowchart TD
    A[Tool call] --> B{model= provided?}
    B -->|yes| C[use requested model]
    B -->|no| D["use effective default<br/>default_model or gemini-3.5-flash"]
    C --> E{auth.method}
    D --> E
    E -->|api_key| F[Developer API<br/>aliases OK]
    E -->|adc / env / keychain| G[Vertex AI<br/>versioned names]
    F --> H[send request]
    G --> H
    H --> I{503 / 429 after retries?}
    I -->|no| J[return response]
    I -->|yes, and model != fallback| K[retry once on<br/>gemini-3.1-flash-lite]
    K --> L[return response<br/>+ notice]
```
