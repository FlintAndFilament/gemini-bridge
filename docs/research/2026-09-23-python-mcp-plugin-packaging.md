---
title: Packaging a Python MCP server (gemini-bridge) as a Claude Code plugin
date: 2026-09-23
question: How do experienced developers package and distribute a Python MCP server as a Claude Code plugin (.claude-plugin/plugin.json + .mcp.json + marketplace), and what is the launched server's working directory?
status: complete
---

## Answer

**Ship the source inside the plugin and launch it with `uv run --directory ${CLAUDE_PLUGIN_ROOT} gemini-bridge`, pointing `UV_PROJECT_ENVIRONMENT` at `${CLAUDE_PLUGIN_DATA}` so the venv survives plugin updates.** This needs no PyPI publish, keeps plugin version and server version as one artifact, and matches the one official recommendation that specifically names Python: `${CLAUDE_PLUGIN_DATA}` is documented for "Python virtual environments." The real-world fallback if you later want a slimmer bundle is `uvx gemini-bridge==<pinned>` after publishing to PyPI (the pattern `serena` uses today, pinned to a git ref rather than a registry). Both paths share the same real failure mode: **`uv`/`uvx` must be on the user's `$PATH`, and it usually isn't by default** — this very research machine (a working Claude Code + gemini-bridge dev box) has no `uv` installed. Plan a `/gemini-bridge:setup` command, modeled on the local `codex` plugin's `commands/setup.md`, that checks for `uv` and offers to install it via `AskUserQuestion` before falling back with a clear error.

On the secondary question: **the MCP server's cwd is the directory Claude Code was launched from (the session's project root / `CLAUDE_PROJECT_DIR`), not the plugin directory.** This is confirmed empirically (below), and official docs tell you not to rely on it anyway — read `CLAUDE_PROJECT_DIR` from the environment instead. gemini-bridge's `./session-summaries` default is fine as-is for the common case (Claude launched from the project root) but should read `CLAUDE_PROJECT_DIR` rather than assume cwd, since cwd will NOT be `${CLAUDE_PLUGIN_ROOT}`.

## Options compared

| # | Approach | Real examples found | Version coupling | First-run latency | If `uv`/`npx`/runtime missing | Verdict for gemini-bridge |
|---|---|---|---|---|---|---|
| 1 | PyPI + `uvx <pkg>` (pinned) | Ecosystem convention per `modelcontextprotocol/servers` docs (`uvx mcp-server-git`); no *Claude Code plugin* example pins a Python package version in `.mcp.json` in the local cache | Two artifacts to keep in lockstep (PyPI release + plugin.json version) unless CI automates it | uv resolves + downloads wheels into its tool cache on first call; fast on repeat use, same-process for every session after | Command literally can't spawn — `spawn uv ENOENT` (documented widely, see Unverified/blocked); Claude Code doesn't bundle or auto-install `uv` | Viable later; adds a release step you don't need yet since gemini-bridge isn't on PyPI |
| 2 | Ship source in plugin, launch `uv run --directory ${CLAUDE_PLUGIN_ROOT} ...` | No exact local cache example (`serena` is closest — uvx from git+https, not a bundled directory) but directly matches the official plugin-dev guidance's `${CLAUDE_PLUGIN_ROOT}` pattern and the official `${CLAUDE_PLUGIN_DATA}` "Python virtual environments" use case | One artifact — plugin version *is* the server version | `uv run` creates/reuses a project venv on first invocation, same order of magnitude as (1); persists in `${CLAUDE_PLUGIN_DATA}` across plugin updates if configured, so most updates don't repeat the full resolve | Same `uv` dependency as (1) | **Recommended** — no registry publish required, matches gemini-bridge's current "run from source" workflow |
| 3 | pipx / system pip install, plugin just references the installed command | `pyright-lsp` plugin README recommends `pipx install pyright` (but that's an LSP binary install instructions doc, not a Claude Code plugin-managed launch) | Whatever the user manually keeps updated — no plugin-driven update flow at all | None after install, but install itself is manual and outside the plugin lifecycle | If not installed, command not found — same class of failure, and now it's the user's job to fix, not the plugin's | This is close to gemini-bridge's *current* `setup.sh` + editable-pip-install approach; regresses the packaging goal rather than advancing it |
| 4 | MCPB (bundled runtime, `.mcpb` file) | Official `mcp-server-dev` plugin's `build-mcpb` skill, Anthropic-authored | N/A — separate artifact entirely | Zero (runtime is bundled) | N/A — no external runtime needed | **Wrong tool for this job.** MCPB targets the Claude Desktop / Anthropic Directory connector flow ("Install: drag the `.mcpb` file onto Claude Desktop"), not the Claude Code plugin/marketplace system. The same skill explicitly frames local stdio via `npx`/`uvx` as the correct choice specifically *because* "the only distribution channel is Claude Code plugins" — i.e., MCPB is the alternative for the *other* channel, not a substitute here |
| 5 | Docker | None found in the local plugin cache (`~/.claude/plugins/cache`, `~/.claude/plugins/marketplaces`) except the unrelated `terraform` MCP server (`docker run hashicorp/terraform-mcp-server`) | Image tag = version, clean | Image pull on first run (larger than a wheel download) | Requires Docker Desktop/daemon running — heavier ask than `uv` | Not seen for a Python dev-tool MCP server like this; unnecessary weight |
| 6 | Bootstrap script that installs on first run (SessionStart hook) | Node analog is native and documented (Claude Code auto-installs `package.json` + lockfile deps into the per-version cache dir); the same pattern is the officially suggested workaround for Python, done by hand via `${CLAUDE_PLUGIN_DATA}` | Plugin-driven, same artifact as (2) | Same as (2), just explicit instead of implicit in `uv run` | Still needs `uv` (or `pip`) present to do the install | This *is* effectively option 2's mechanism when you write the hook yourself; only worth doing separately from `uv run` if you want the install to happen at `SessionStart` rather than lazily on first tool call |

## Real-world examples inspected

All read directly from `~/.claude/plugins/marketplaces/` and `~/.claude/plugins/cache/` on this machine (2026-09-23), plus GitHub/PyPI where noted.

- **`serena`** (Python MCP server, launched as a Claude Code plugin) — `external_plugins/serena/.mcp.json`:
  ```json
  {
    "serena": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/oraios/serena", "serena", "start-mcp-server"]
    }
  }
  ```
  Not published to PyPI at all — `uvx --from git+https://...` installs straight from a git ref. No version pin (floats to the ref's HEAD, here the default branch). Source: `/Users/brandon/.claude/plugins/marketplaces/claude-plugins-official/external_plugins/serena/.mcp.json`.

- **`playwright`** (Node, for comparison) — `command: npx`, `args: ["@playwright/mcp@latest"]`. Explicitly unpinned (`@latest`). Source: `external_plugins/playwright/.mcp.json`.

- **`firebase`** (Node) — `npx -y firebase-tools@latest mcp`. Also unpinned. Source: `external_plugins/firebase/.mcp.json`.

- **`laravel-boost`** — `php artisan boost:mcp`, i.e. it assumes the project's own toolchain rather than fetching anything; closest real analog to "just run the already-installed command."

- **`terraform`** — `docker run -i --rm -e TFE_TOKEN=${TFE_TOKEN} hashicorp/terraform-mcp-server:0.4.0`, a pinned image tag — the one example of "docker" and the one example of a genuinely pinned version among the stdio servers inspected.

- **`context7`, `github`, `cloudflare`** — all `type: "http"` remote servers with OAuth or bearer-token headers; not stdio, not applicable to gemini-bridge's local-process model.

- **`codex` (openai-codex plugin)** — has **no `.mcp.json` at all**. It is not an MCP server; it drives the Codex CLI directly via Node scripts (`scripts/codex-companion.mjs`) invoked from slash commands and hooks. Useful as the local example of the **setup-command pattern** (see below), not of MCP packaging. Source: `/Users/brandon/.claude/plugins/marketplaces/openai-codex/plugins/codex/.claude-plugin/plugin.json`, `commands/setup.md`, `hooks/hooks.json`.

- **`modelcontextprotocol/servers`** (the reference MCP servers repo) — its Python servers (e.g. `mcp-server-git`) document `uvx mcp-server-git` as the primary install/run command, with `pip install mcp-server-git` + `python -m mcp_server_git` as the documented fallback, and state `uvx` is recommended "for ease of use and setup." (Verified via WebFetch of `github.com/modelcontextprotocol/servers`, 2026-09-23; this is the standard MCP-ecosystem convention, independent of Claude Code plugins specifically.)

- **Official `plugin-dev` skill** (Anthropic-authored, bundled in `claude-plugins-official`) shows the generic Python pattern for `.mcp.json`:
  ```json
  {
    "python-server": {
      "command": "python",
      "args": ["-m", "my_mcp_server"],
      "env": { "PYTHONUNBUFFERED": "1" }
    }
  }
  ```
  and calls out "Set `PYTHONUNBUFFERED` for Python servers" and "Log to stderr, not stdout (stdout is for MCP protocol)" as best practices. Source: `~/.claude/plugins/marketplaces/claude-plugins-official/plugins/plugin-dev/skills/mcp-integration/references/server-types.md:67-86`.

- **Official `mcp-server-dev` skill** (Anthropic-authored) explicitly frames local stdio via `npx`/`uvx` as *"not recommended for distribution"* in the general MCP-server-for-Claude sense (Desktop, Claude.ai, third-party hosts), but immediately qualifies: *"Fine for personal tools and prototypes... the only distribution channel is Claude Code plugins."* That is, for a server whose sole distribution channel is the Claude Code plugin/marketplace system (gemini-bridge's exact situation), local stdio via `uvx`/`uv run` is the sanctioned path — the skill's caution is about *also* publishing to the Anthropic connector Directory, which is out of scope here. Source: `~/.claude/plugins/marketplaces/claude-plugins-official/plugins/mcp-server-dev/skills/build-mcp-server/SKILL.md:101-105`.

No example in the locally cached marketplaces bundles a Python venv inside `${CLAUDE_PLUGIN_DATA}` — that pattern is documented but I found no plugin in the local cache actually doing it yet; treat it as the officially-endorsed but not yet widely-proven-in-the-wild approach (see Unverified section).

## Setup / auth pattern

Three mechanisms exist; gemini-bridge's existing `setup.sh` wizard maps cleanly onto the first:

1. **A setup slash command that shells out to a script**, exactly like `codex`'s `commands/setup.md`:
   ```markdown
   ---
   description: Check whether the local Codex CLI is ready...
   allowed-tools: Bash(node:*), Bash(npm:*), AskUserQuestion
   ---
   Run:
   node "${CLAUDE_PLUGIN_ROOT}/scripts/codex-companion.mjs" setup --json $ARGUMENTS
   ...
   If the result says Codex is unavailable and npm is available:
   - Use AskUserQuestion exactly once to ask whether Claude should install Codex now.
   ```
   (`~/.claude/plugins/marketplaces/openai-codex/plugins/codex/commands/setup.md`). For gemini-bridge this becomes `commands/setup.md` invoking `"${CLAUDE_PLUGIN_ROOT}/setup.sh"` (or a Python equivalent), with an `AskUserQuestion` fallback offering to install `uv` (`curl -LsSf https://astral.sh/uv/install.sh | sh`) if it's missing — mirroring codex's "offer to `npm install -g @openai/codex`" flow one-for-one.

2. **`userConfig` in `plugin.json`** — Anthropic's supported mechanism for plugin settings, including a `sensitive: true` field type that stores values in the macOS Keychain (or `~/.claude/.credentials.json` as fallback) — i.e., the same storage gemini-bridge's `auth.py` keychain path already targets by hand. `userConfig` values substitute into MCP server `env` as `${user_config.KEY}`. This *could* replace manual config-file auth prompts for simple values (e.g., `project`, `location`, `default_model`), but sensitive credentials still route through the same OS keychain either way — so this is an optional simplification, not a requirement. Caveat: `userConfig` is populated from `~/.claude/settings.json` / global scope only, **not** project-level settings, and shell commands cannot reference `${user_config.*}` directly (must go through the `CLAUDE_PLUGIN_OPTION_<KEY>` env var in hook processes or `${user_config.KEY}` in MCP/LSP configs). Source: WebFetch of `code.claude.com/docs/en/plugins-reference`, 2026-09-23.

3. **SessionStart hook** — used by `codex` to run lifecycle bookkeeping on every session start (`hooks/hooks.json`, `SessionStart` → `session-lifecycle-hook.mjs`), and is the mechanism the official docs point to for installing Node dependencies into `${CLAUDE_PLUGIN_DATA}` on first run/update. The equivalent for gemini-bridge would be a `SessionStart` hook that runs `uv sync --project "${CLAUDE_PLUGIN_ROOT}"` (or checks whether `${CLAUDE_PLUGIN_DATA}/.venv` exists and creates it) before the MCP server needs to spawn — trading "first tool call is slow" for "session start is slightly slower, every tool call is fast." Given `uv run` already does lazy resolution + caching adequately, this is an optimization, not a requirement.

**Recommendation for gemini-bridge specifically:** keep `setup.sh` (it already handles auth method selection, keychain storage, config durability — reviewed in recent PRs #95–#99 per this repo's own git log) and wrap it with a thin `commands/setup.md` slash command that (a) calls it via `${CLAUDE_PLUGIN_ROOT}/setup.sh`, and (b) checks for `uv` first, offering to install it, before the wizard runs — matching the codex precedent closely enough that it should read as idiomatic to anyone who has both plugins installed.

## Secondary question: MCP server working directory

**Confirmed empirically on this machine** (Claude Code v2.1.281, macOS): the plugin-launched stdio MCP server process's `cwd` equals the directory Claude Code was invoked from — the session's project root — **not** `${CLAUDE_PLUGIN_ROOT}`.

Method: built a throwaway plugin (`.claude-plugin/plugin.json` + `.mcp.json` + a `FastMCP`-based Python stdio server with one tool that reports `os.getcwd()` and the `CLAUDE_PLUGIN_ROOT`/`CLAUDE_PROJECT_DIR` env vars) and ran `claude --plugin-dir <probe> -p "call the probe tool"` from three different launch directories:

| Launched from | Tool reported `cwd` | Tool reported `CLAUDE_PROJECT_DIR` |
|---|---|---|
| `/Users/brandon/dev/github/gemini-bridge` | `/Users/brandon/dev/github/gemini-bridge` | same |
| `/Users/brandon/dev/github/gemini-bridge/docs` | `/Users/brandon/dev/github/gemini-bridge/docs` | same |
| `/tmp` | `/private/tmp` | same |

In every case `cwd` tracked the launch directory (and matched `CLAUDE_PROJECT_DIR`), and `CLAUDE_PLUGIN_ROOT` stayed fixed at the plugin's own path regardless of launch directory — confirming the plugin root is never the server's working directory. (Scratch plugin was deleted after the test; not committed anywhere.)

This matches official documentation, quoted verbatim from `code.claude.com/docs/en/mcp` (fetched 2026-09-23):

> "Claude Code sets `CLAUDE_PROJECT_DIR` in the spawned server's environment to the project root, so your server can resolve project-relative paths **without depending on the working directory**. ... `CLAUDE_PROJECT_DIR` is the stable project root and doesn't change when you add or remove working directories mid-session."

**Practical implication for gemini-bridge:** the `./session-summaries` default in `transcript_dir` (`setup.sh`, `PREV_TRANSCRIPT_DIR="./session-summaries"`) will resolve relative to wherever Claude Code was launched, which for normal usage is the project root — so behavior is unchanged in the common case. But the code should not assume this holds in every host/version — it should read `os.environ.get("CLAUDE_PROJECT_DIR")` as the anchor for a relative `transcript_dir`, rather than relying on the process's implicit `cwd`, both because the docs explicitly warn against depending on cwd and because a future Claude Code version could change the spawn behavior without breaking the documented contract on `CLAUDE_PROJECT_DIR`. This is a real, if currently low-priority, follow-up — not something this research session should change, since the task was to research, not modify application code.

## Versioning / update mechanics (official)

From `code.claude.com/docs/en/plugins-reference` (fetched 2026-09-23):

- If `plugin.json`'s `version` field is set, it **pins** the plugin to that version — "users only receive updates when you bump it" (except for `command`-source-in-link-mode or a plugin loaded in place, which always reflects the latest local files).
- Version resolution order: `plugin.json` version → marketplace entry version → release tags (dependency resolution) → latest available.
- **Copied plugins** (the normal marketplace-install case) get a separate directory per version under `~/.claude/plugins/cache/`; `${CLAUDE_PLUGIN_ROOT}` changes on every version bump; the previous version's directory is kept ~14 days (orphan grace period) then swept.
- **Node.js dependencies** (`package.json` + lockfile) are installed automatically into each version's cache directory. **There is no equivalent automatic install for Python** — a Python plugin must do this itself (via `uv run`'s own lazy resolution, or an explicit hook installing into `${CLAUDE_PLUGIN_DATA}`).
- `${CLAUDE_PLUGIN_DATA}` resolves to `~/.claude/plugins/data/{id}/` and **persists across plugin version updates** (unlike `${CLAUDE_PLUGIN_ROOT}`), specifically named for "Python virtual environments," "generated code and caches," and "packages whose lifecycle scripts must run." It is deleted only on full uninstall.

Practical consequence for gemini-bridge: if the venv is created inside `${CLAUDE_PLUGIN_ROOT}` (the default target of `uv run --directory`), every plugin version bump forces a full dependency re-resolve (network round-trip for `google-genai`, `google-auth`, `mcp`, etc.) because the new version gets a fresh cache directory. Pointing `UV_PROJECT_ENVIRONMENT` (or `--project`) at a path under `${CLAUDE_PLUGIN_DATA}` avoids that repeated cost on every release — this is inferred from the documented persistence semantics, not something I ran end-to-end across two real plugin versions (see Unverified).

## Open risks / failure modes

1. **`uv` not installed is a real, common gap, not a hypothetical.** Confirmed on this development machine: `which uv uvx` → not found, despite Claude Code and a working Python MCP dev environment already being present. Any launch strategy that depends on `uv`/`uvx` needs a setup-command check-and-offer-to-install step (see Setup/auth pattern above) or it will silently fail to start for a meaningful fraction of users.

2. **Windows: `uv`-bootstrapped Python MCP servers have a documented, Claude-specific race condition.** Two now-closed GitHub issues in `anthropics/claude-code` describe `uv`/`uvx`-based Python MCP servers failing to start on Windows because Claude's process-spawning leaves an inheritable handle into `uv`'s own build temp directory, causing `os error 32` when `uv` tries to finish installing:
   - [#38266](https://github.com/anthropics/claude-code/issues/38266) — "Windows: Python-based MCP extensions fail to connect due to `uv` venv build race condition," closed 2026-03-27.
   - [#68858](https://github.com/anthropics/claude-code/issues/68858) — "Windows: uv-based (`uvx`) MCP servers fail to start... identical command succeeds when spawned outside Claude's process tree," closed 2026-07-28.
   Both are closed/fixed as of their closing dates, but they establish this is a known class of bug in exactly this launch pattern, on exactly this platform, and worth a smoke test on Windows before shipping if Windows users are in scope. gemini-bridge's `setup.sh` and keychain auth already lean macOS-specific (`security` / macOS Keychain), so Windows support may already be out of scope — worth confirming with the maintainer rather than assuming.

3. **`uvx <pkg>@latest` (unpinned) is common in the wild (`playwright`, `firebase`) but is a real version-drift risk** — the plugin version in `plugin.json` and the actual server code that runs can silently diverge from what was tested. If gemini-bridge ever moves to PyPI + `uvx`, pin the version (`uvx gemini-bridge==${plugin.json version}` or equivalent) rather than following the `@latest` precedent.

4. **No local or online example was found of a Claude Code plugin doing exactly "bundle source + `uv run --directory ${CLAUDE_PLUGIN_ROOT}` + venv redirected to `${CLAUDE_PLUGIN_DATA}`."** This is a synthesis of two independently documented, officially-supported primitives (`uv run --directory`, and `${CLAUDE_PLUGIN_DATA}` named for "Python virtual environments"), not something I found already shipped and proven at scale. Treat it as the recommended design, but pilot it end-to-end (including a version bump, to confirm the venv really does survive) before treating it as load-bearing.

## Unverified / blocked

- **Whether `uv sync`/`uv run` actually reuses a venv placed at `${CLAUDE_PLUGIN_DATA}` across a real plugin version bump** — not tested end-to-end in this session (would require publishing two plugin versions to a real or local-directory marketplace and installing both). The persistence claim rests on official docs describing `${CLAUDE_PLUGIN_DATA}` semantics, not on an observed before/after.
- **Exact error surfaced to the user/Claude when `uv` is entirely missing from `$PATH` for an MCP (not LSP) server.** Docs explicitly describe the LSP case ("Executable not found in $PATH", visible in `/plugin` Errors tab); by analogy the same likely applies to MCP stdio servers, but I did not reproduce a "command not found" MCP spawn failure in this session (the empirical probe used `python3`, which was present) to confirm the exact user-facing message and log location for an MCP server specifically. `claude --debug` and the `/mcp` command are the documented ways to inspect this, per the official mcp-integration skill.
- **PyPI name `gemini-bridge`**: `curl https://pypi.org/pypi/gemini-bridge/json` returned HTTP 200, meaning a package under that exact name **already exists on PyPI** and is not available if you later choose the PyPI+`uvx` path — this needs a name check/negotiation before any PyPI publish, independent of which launch strategy is chosen. (Not investigated further — whose package it is, whether it's related, was out of scope for this session's questions.)
- The `serena` and MCPB examples were read from the local plugin cache/marketplace snapshot on this machine (dated by its `Sep 4`–`Sep 23 2026` directory timestamps) rather than fetched fresh from each project's current repository; if any of those projects changed their `.mcp.json` recently, this report reflects the cached snapshot, not necessarily today's upstream state.

## Sources

- Local plugin cache/marketplace inspection (2026-09-23): `~/.claude/plugins/marketplaces/{cloudflare,openai-codex,claude-plugins-official}/...` — file paths cited inline above.
- `code.claude.com/docs/en/plugins` — plugin structure, quickstart, `.mcp.json`/manifest overview, versioning/sharing guidance (fetched 2026-09-23).
- `code.claude.com/docs/en/plugins-reference` — plugin.json full schema, `${CLAUDE_PLUGIN_ROOT}`/`${CLAUDE_PLUGIN_DATA}`/`${CLAUDE_PROJECT_DIR}`, version management, plugin caching, `userConfig` (fetched 2026-09-23).
- `code.claude.com/docs/en/mcp` — `CLAUDE_PROJECT_DIR` env var for spawned MCP servers, verbatim quote used above (fetched 2026-09-23).
- Empirical test: throwaway probe plugin built and run in this session with `claude --plugin-dir`, Claude Code v2.1.281, macOS (2026-09-23); deleted after use, not committed.
- `github.com/modelcontextprotocol/servers` — Python MCP server install convention (`uvx` primary, `pip`+`python -m` fallback) (fetched 2026-09-23).
- `docs.astral.sh/uv/guides/tools/` — `uvx` first-run/caching/pinning behavior (fetched 2026-09-23).
- `github.com/anthropics/claude-code` issues [#38266](https://github.com/anthropics/claude-code/issues/38266), [#68858](https://github.com/anthropics/claude-code/issues/68858) — Windows `uv`-spawn race condition, both closed (verified via `gh issue view`, 2026-09-23).
- `pypi.org/pypi/gemini-bridge/json` — HTTP 200 (name already taken), checked via `curl` (2026-09-23).
- This repo: `/Users/brandon/dev/github/gemini-bridge/pyproject.toml`, `/Users/brandon/dev/github/gemini-bridge/setup.sh`.
