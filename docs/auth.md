# Authentication

gemini-bridge supports four auth methods, set by `auth.method` in
`~/.config/gemini-bridge/config.json` (see [configuration.md](configuration.md)):

| Method | Backend | Needs a GCP project | Web access (`web=true`) |
|---|---|---|---|
| `adc` (default) | Vertex AI | yes | not available |
| `env` | Vertex AI | yes | not available |
| `keychain` (macOS) | Vertex AI | yes | not available |
| `api_key` | Gemini Developer API (Google AI Studio) | no | available |

The three Vertex methods only differ in where the credentials come from; they share the same
models, `project`/`location` settings and IAM role (`roles/aiplatform.user`). **Web access needs
`api_key`**: on Vertex the bridge drops `web=true`, answers without the web and says so in a
`[gemini-bridge notice]` (the Gemini API flag that lets web tools share a request with the
bridge's file tools is Developer-API only).

Credentials are loaded once, at server startup. A failure stops the server and is written to the
log (see [logging.md](logging.md)) with the fix; the messages are listed in
[Troubleshooting](#troubleshooting).

**Registering the server with Claude Code.** The server name comes first, then the scope:

```bash
# Vertex methods (adc, env, keychain)
claude mcp add gemini-bridge -s user -- python3 -m gemini_bridge

# api_key — the -e flag puts the key into the server's own environment
claude mcp add gemini-bridge -s user -e GEMINI_API_KEY="$GEMINI_API_KEY" -- python3 -m gemini_bridge
```

If you leave the name out, `claude mcp add` takes `python3` as the name and the server fails to
connect. `setup.sh` prints the right command for your method at the end.

---

## The critical distinction: `gcloud auth login` vs ADC

These are **separate credential stores** and **separate re-auth timelines**:

| Command | Authenticates | Stored at | Expiry |
|---|---|---|---|
| `gcloud auth login` | gcloud CLI only | `~/.config/gcloud/credentials.db` | Short-lived; org session-timeout policy |
| `gcloud auth application-default login` | SDKs and APIs | `~/.config/gcloud/application_default_credentials.json` | Refresh token; typically months |

With `auth.method = "adc"` the MCP server uses **ADC only**. When your gcloud CLI session
expires and you re-auth with `gcloud auth login`, the MCP server is **unaffected**. You only need to re-run
`gcloud auth application-default login` if ADC itself expires (rare on a personal machine).

**Verify ADC is working:**
```bash
gcloud auth application-default print-access-token
```
If this returns a token, the MCP server will authenticate successfully.

---

## Method 1: ADC (Default, Recommended)

**One-time setup:**
```bash
gcloud auth application-default login
gcloud auth application-default set-quota-project YOUR_PROJECT_ID
```

The first command opens a browser and stores a refresh token at
`~/.config/gcloud/application_default_credentials.json`. The SDK auto-refreshes access tokens
from this refresh token — no repeated re-auth.

The second command is required when using **user credentials** (as opposed to a service account).
User credentials span all your GCP projects, so the Vertex AI API needs to know which project's
quota to charge. Without it you will receive a `403 PERMISSION_DENIED: quota project not set`
error when calling the API. See the [Troubleshooting](#troubleshooting) table for the fix if you
already have ADC set up.

**Run setup.sh and select option 1.** The wizard checks
`gcloud auth application-default print-access-token` and stops if ADC is not configured.

**Config:**
```json
{"project": "YOUR_PROJECT_ID", "auth": {"method": "adc"}}
```

**Minimum GCP permissions:** `roles/aiplatform.user` on the project.

---

## Method 2: Env File SA

Use a service account key file on disk. Set the environment variable **before starting Claude Code** — the server reads it at startup:

```bash
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/sa-key.json
```

The server reads `GOOGLE_APPLICATION_CREDENTIALS` itself and loads that file explicitly. It
never falls back to your ADC user credentials: an unset variable or a missing file stops
startup with a message naming the problem (#86), so this method can no longer run the server
as you instead of the service account. To be sure the variable reaches the server, pass it at
registration:
`claude mcp add gemini-bridge -s user -e GOOGLE_APPLICATION_CREDENTIALS=/path/to/sa-key.json -- python3 -m gemini_bridge`.

**Setup steps:**
1. Create or download a service account key in GCP IAM → Service Accounts → Keys
2. Grant the SA `roles/aiplatform.user` on your project
3. Store the JSON file in a secure location (e.g. `~/.config/gemini-bridge/sa-key.json`, mode 600)
4. Export the env var in your shell profile or before starting Claude Code
5. Run `bash setup.sh` and select option 2

**Auth block written by setup.sh** (alongside `project` and `location`):
```json
{"auth": {"method": "env"}}
```

`setup.sh` checks the env var in the setup shell. If it is set but the file does not exist, setup
stops with an error. If it is not set, it warns but continues — you can set it later before
starting Claude Code.

**Caution:** The key file persists on disk. Use only on full-disk-encrypted machines with restricted file permissions (`chmod 600`).

---

## Method 3: Apple Keychain (macOS only)

Store the service account JSON in Apple Keychain. The server reads it once at startup into memory — the raw JSON is never written to disk after the initial store, and disappears when the process exits.

**Why Keychain over a disk file:** A SA JSON on disk is a persistent secret at rest. Keychain provides OS-managed storage with ACL enforcement. This is the preferred service account path for DLP-sensitive environments.

**One-time setup — store the SA JSON:**
```bash
security add-generic-password \
  -s "gemini-bridge" \
  -a "vertex-sa" \
  -w "$(cat /path/to/sa-key.json)"
rm /path/to/sa-key.json   # remove disk copy immediately
```

**Run setup.sh and select option 3.** The wizard prompts for the service and account names, then verifies the item exists and contains valid JSON before writing config.

**Auth block written by setup.sh** (alongside `project` and `location`):
```json
{
  "auth": {
    "method": "keychain",
    "keychain_service": "gemini-bridge",
    "keychain_account": "vertex-sa"
  }
}
```

**Minimum SA role:** `roles/aiplatform.user` (Vertex AI User) on the project. It covers the
`generateContent` calls and the model listing the bridge does at startup. It grants more than
prediction, so if you need tighter scope, build a custom role and test it with
`gemini_list_models` and one `gemini_ask` call.

The server reads the item with `security find-generic-password -s <service> -a <account> -w`.
macOS returns a multi-line value (like SA JSON) as a hex string; the server detects and decodes
that before parsing.

macOS only. The `security` CLI is not available on Linux, and `setup.sh` refuses option 3 there.

---

## Method 4: API Key (Google AI Studio)

The simplest path — no GCP project, no `gcloud` setup, no service account. Get a key from
[aistudio.google.com/apikey](https://aistudio.google.com/apikey) and set an environment variable.

**One-time setup:**
```bash
export GEMINI_API_KEY=AIza...
```

A shell export alone (even in `~/.zshrc`) does not reliably reach the MCP server process, so also
pass the key when you register the server. The server name `gemini-bridge` must come before the
flags:
```bash
claude mcp add gemini-bridge -s user -e GEMINI_API_KEY="$GEMINI_API_KEY" -- python3 -m gemini_bridge
```
Keep the quotes: AI Studio keys can contain characters your shell would otherwise interpret.

**Run setup.sh and select option 4.** The wizard asks for the **name** of the env var (default:
`GEMINI_API_KEY`), re-prompts if what you typed looks like a key instead of a name (starts with
`AIza`, contains lowercase letters, or is over 40 characters), warns if the variable is not set in
the current shell, and prints the exact `claude mcp add` command at the end. The server applies
the same check at startup and refuses to start if `api_key_env` looks like a key.

**Config written by setup.sh:**
```json
{
  "default_thinking": "medium",
  "transcript_dir": "./session-summaries",
  "auth": {
    "method": "api_key",
    "api_key_env": "GEMINI_API_KEY"
  }
}
```

`project` and `location` are omitted — they are not required by the Google AI Studio
(Developer API) endpoint. `default_model` is added only if you entered one.

**Positioning:**
- Use this method for personal use, quick setup, or when you don't have a GCP project
- ADC and Keychain methods remain first-class for team/enterprise use — they support larger
  quota tiers, org-managed access control, and service account rotation
- The API key is read from the env var at server startup and held in memory; it is never
  written to `config.json`
- This is the only method with web access (`web=true`, `web_tools.enabled`); see
  [tools.md](tools.md#web-access)

**Quota:** AI Studio rate limits depend on the model and on whether billing is enabled for the
key; check the current limits in AI Studio. Limits are generally lower than Vertex AI. When a
model is overloaded or out of quota (503/429), the bridge retries with backoff and then falls back
once to the newest Flash-Lite (see [configuration.md](configuration.md#choosing-a-model)).

---

## Troubleshooting

| Error message | Cause | Fix |
|---|---|---|
| `no ADC credentials found` | ADC not configured | `gcloud auth application-default login` |
| Token refresh / `invalid_grant` errors on a call | ADC refresh token expired or revoked | `gcloud auth application-default login` |
| `403 PERMISSION_DENIED: quota project not set` | ADC user credentials have no quota project | `gcloud auth application-default set-quota-project YOUR_PROJECT_ID` |
| `auth method is 'env' but GOOGLE_APPLICATION_CREDENTIALS is not set` / `points at a file that does not exist` / `file could not be loaded` | `env` method: the variable is unset, names a missing file, or names a file google-auth cannot read. ADC is never used as a fallback | `export GOOGLE_APPLICATION_CREDENTIALS=...` or pass it with `-e` at registration; check the path and the file's contents |
| `Keychain item not found` | Secret not stored | Re-run the `security add-generic-password` command |
| `not valid service account JSON` | Keychain value corrupted | Re-store the SA JSON key |
| `'security' CLI not found` | Not macOS | Keychain method is macOS-only |
| `'GEMINI_API_KEY' is not set or is empty` | API key env var missing in the server's environment | Re-register with `-e GEMINI_API_KEY="$GEMINI_API_KEY"` (see above) |
| `'api_key_env' in config.json contains what looks like an API key` | The key itself was put in `api_key_env` | Set `api_key_env` to the variable name, e.g. `"GEMINI_API_KEY"` |
| `'project' is required for Vertex AI auth methods` | `project` missing with `adc`/`env`/`keychain` | Add `project` to config.json, or switch to `api_key` |
| `Web access was requested but is unavailable` | `web=true` on a Vertex method | Use `api_key` for web access |
