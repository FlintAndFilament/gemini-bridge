# Roadmap

What has shipped, what is open, and what is parked. Rebuilt from git history and GitHub issues on
2026-09-21; everything under **Shipped** is on `main`.

The `26.7.x` labels are the names the July releases went out under. Work after 26.7.4 is grouped
by feature and issue number instead of by version.

The project was called gemini-bridge until 2026-09-24 (#104). Upgrading an older install: **before** `/plugin update sidekick`, run `mv ~/.config/gemini-bridge ~/.config/sidekick`, then update and restart. (After the update, the new server has already created `~/.config/sidekick/`, and `mv` nests the old folder inside it.)

---

## Shipped

### 26.7.1 — Core

**5 tools, ADC + env + Keychain auth, persistent sessions, transcript logging, structured logging,
full docs.**

- `ask`, `brainstorm`, `review`, `debug`, `architect`
- ADC (Application Default Credentials) — one-time setup, SDK auto-refreshes
- Env-file service account auth (`GOOGLE_APPLICATION_CREDENTIALS`, #14)
- Apple Keychain service account auth, macOS only (#16): the SA JSON is read into memory at
  startup via `security(1)` and hex-decoded when the CLI returns it that way (#25)
- Transcript logging to `YYYYMMDD-HHMM-sidekick-transcript.md`, project-local
  `./session-summaries` by default (#33)
- Structured logging to stderr and a daily log file, 4 files kept (#26, #27, #28)
- Interactive `setup.sh` wizard that pre-fills prompts from an existing config (#23, #24)
- Full `docs/` directory + README.md

**Why this scope:** Five tools cover the primary use cases (consult, challenge, critique, debug,
design). Persistent sessions give Gemini context across related calls. ADC requires no
credential management beyond one-time setup.

### 26.7.2 — Google AI Studio API key auth

- `api_key` auth method (#30): no GCP project or `gcloud` needed. `AuthResult` + `build_auth()`
  dispatch in `auth.py`; `project` became optional, required only for the Vertex methods. The
  config stores the env var **name**, never the key.
- CI warnings and failures fixed (#32); transcript files renamed to `sidekick-transcript`
  (#35).
- 26.7.2.1 hotfix (#39): clearer `setup.sh` API key prompt (asks for the variable name and rejects
  a pasted key), and the key no longer leaks into the error log.

### 26.7.3 — Correctness and reliability (#42–#49)

- Tool personas actually reach Gemini: `system_instruction` is sent on every call (#42)
- `thinking="none"` clamped to the minimum budget on Gemini 2.x Pro models (#43)
- Retry with exponential backoff on 503/429 (#45)
- Empty or blocked responses report their `finish_reason` instead of returning nothing (#46)
- Session cache bounded: least recently used session dropped past 50 (#48)
- Thinking-parameter choice driven by a `ModelFamily` enum rather than name sniffing (#49), plus
  an encapsulation cleanup (#47)

### 26.7.4 — Per-call models and fallback

- Per-call `model=` on every generating tool; sessions are keyed per model (#51)
- Backend-aware warnings for model names that likely 404 on the active backend (#44)
- Overload errors carry an actionable hint (try again, or pass another model) (#52)
- Google's `-latest` aliases accepted, with a transparent, disclosed fallback model on a terminal
  503/429 (#53)

### Model discoverability and defaults (#55, #56, #58, #59, #60, #64)

- Each tool's `model` parameter description lists the models valid for the active backend, and
  `list_models` returns the live, chat-only catalog, falling back to a curated shortlist
  when the live list can't be read (#56). Single source of truth in `models.py`.
- `list_models` lists only models the bridge can run and accepts Gemini 3 previews (#60);
  it works on Vertex AI, which reports no `supported_actions` (#64).
- Tool parameter descriptions reach Claude (they were silently dropped before) (#55).
- `default_model` config field (#59).
- Moved off Gemini 2.5 ahead of its 2026-10-16 retirement: fallback and shortlists now use 3.x
  models (#58).

### Setup fix (#66)

`setup.sh` prints `claude mcp add sidekick -s user …` with the server name first. Without the
name, `claude mcp add` registered the server as `python3` and it failed to connect.

### Repository file tools (#68)

Gemini reads the repository itself with `list_dir`, `glob`, `grep` and `read_file`, sandboxed to
the directory Claude Code was launched in, with a deny-list for `.git`, `.env*`, keys and
credentials. `brainstorm`, `review` and `architect` can also `write_file`
(capped by `file_tools.max_write_bytes`). The bridge runs its own tool loop and saves
`architect` / `review` answers (and opted-in `brainstorm` answers) as
artifacts in `artifacts_dir`. `file_tools.enabled=false` turns it off; it also turns itself off
when launched from the home directory or filesystem root. See
[tools.md](tools.md#repository-access).

### Newest-model resolution (#69)

At startup the bridge reads the live catalog and resolves the newest Flash, Flash-Lite and Pro.
Calls without `model=` get the newest Flash; aliases `flash` / `flash-lite` / `pro` (and Google's
`-latest` names, translated so they also work on Vertex) track the newest release; the fallback is
the newest Flash-Lite. Pinned ids are used when the catalog can't be read. The thinking level is
mapped per concrete model, and the bridge learns and remembers which levels a model rejects. See
[configuration.md](configuration.md#choosing-a-model).

### CI security settings (#72, #73)

Workflows declare least-privilege permissions: Semgrep and Trivy get `security-events: write` so
SARIF upload works, Dependency Review gets `contents: read` (#72). The dependency graph was turned
back on after the org move so Dependency Review passes (#73).

### MCP capability metadata (#74)

Each tool's MCP description and annotations say what Gemini can read and write for that tool, the
write cap, and whether artifacts are saved, built from one capability row per tool so they can't
drift from the wiring.

### Web access (#76)

`web=true` (or `web_tools.enabled`) attaches Gemini's server-side `google_search` and
`url_context`. Off by default because grounding is billed per request. Web access and `write_file`
never share a call. Developer API (`api_key`) only: on Vertex, web is dropped and the reply carries
a notice. See [tools.md](tools.md#web-access).

#70 (let Gemini call external MCP tools such as Exa or Firecrawl) was closed as not planned,
superseded by #76.

### Usage help (#77, #78)

- The transcript records the file size for each `write_file` (it had logged the response size),
  the server instructions explain when to use web access and sessions, and every tool shares one
  accurate `session_name` description (#77).
- Server instructions cut to fit under Claude Code's ~2048-character cap; the full guide moved to
  a new `help` tool (#78). The server now registers 7 tools.

### Web sources (#80, #82)

Web answers end with a sources list taken from the grounding metadata (#80). The bridge resolves
Google's grounding redirect links to the real URLs, and Gemini is told not to type URLs itself, so
every listed URL comes from the metadata (#82). Resolving costs no Gemini tokens.

### Sidekick plugin (#102)

Repackaged as a Claude Code plugin, `sidekick`, installed with `/plugin marketplace add` +
`/plugin install` instead of a manual clone and `claude mcp add`. `setup.sh` is gone;
`/sidekick:setup` (a slash command) drives a new `sidekick-setup status|write` CLI that
never parses free text as shell, JSON or Python. The server launches with
`uv run --frozen --project ${CLAUDE_PLUGIN_ROOT} sidekick`, using a venv at
`${CLAUDE_PLUGIN_DATA}/venv` that `uv` builds once and reuses across restarts and version
bumps. Project-relative paths (`transcript_dir`, `artifacts_dir`, the file-tools sandbox root)
now anchor to `CLAUDE_PROJECT_DIR`, falling back to the working directory when Claude Code
doesn't set it. Tools now appear to Claude as `mcp__plugin_sidekick_mcp__*`.

#38 (ask for `-s user` / `project` / `local` scope in `setup.sh`) was closed as not planned,
superseded by #102 — `/plugin install` chooses the install scope itself, so there is no
`setup.sh` scope prompt left to build.

---

## Open

### Named sessions (#15) — possibly partly done

**Already shipped:** every generating tool takes `session_name`. Calls that share a name continue
one Gemini conversation; a new name starts fresh. Sessions are separate per tool and per model,
live in memory until the server restarts, and the least recently used is dropped past 50. The
session name appears in each transcript entry header.

**Not built:** `gemini_new_session(name)` (reset a named session) and `gemini_list_sessions()`
(list active names with turn counts). Worth deciding whether these are still needed, since a new
name already gives a fresh conversation, before closing or narrowing the issue.

### Per-project transcript routing (#17)

`gemini_set_transcript_dir(path)` tool — redirect the transcript file without editing config.json.

The default `transcript_dir` is already project-local (`./session-summaries`, relative to the
project root — #102), so this now matters mainly when `transcript_dir` is set to an absolute
path, or to switch directories mid-session. Changing it today means editing
`~/.config/sidekick/config.json` and restarting the MCP server.

---

## Backlog

### Sliding window context management

Summarize and trim older exchanges when a session approaches the model's context limit.

**Why deferred:** Current Gemini models have a 1M-token context window, and a typical Claude Code
session's tool calls won't approach it. Sessions already end at server restart and the least
recently used one is dropped past 50. This matters only for very long sessions that are never
reset.

### Location-aware model validation

The server does not check at startup that the chosen model is served in the configured Vertex
`location`; a mismatch shows up as a 404 at call time. `"global"` avoids this. No issue filed yet.
