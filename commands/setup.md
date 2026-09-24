---
description: Set up sidekick (Gemini second opinion): check uv, choose auth, write the config
allowed-tools: Bash(uv --version), Bash(UV_PROJECT_ENVIRONMENT=* uv run --frozen --quiet --project * gemini-bridge-setup *), AskUserQuestion
---

Set up the sidekick plugin. Keep every message short. Never ask for the key
itself — only ask for the NAME of the environment variable that holds it, and
never read, print or store its value.

Run every setup command exactly like this, so it uses the server's own venv
instead of a second one:

    UV_PROJECT_ENVIRONMENT="${CLAUDE_PLUGIN_DATA}/venv" uv run --frozen --quiet --project "${CLAUDE_PLUGIN_ROOT}" gemini-bridge-setup <subcommand> …

1. Run `uv --version`. If uv is missing, use AskUserQuestion once: "Install uv
   (Recommended)" or "Skip". On install, run Astral's official installer:
   - macOS / Linux: `curl -LsSf https://astral.sh/uv/install.sh | sh`
   - Windows: `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`
   If the user skips, print the command for their OS and stop. After
   installing, re-run `uv --version`; if it still fails, tell the user to
   restart Claude Code and run `/sidekick:setup` again, then stop (the
   installer changed PATH, but this session's Bash tool and the MCP server
   still have the old one).
2. Run:

       UV_PROJECT_ENVIRONMENT="${CLAUDE_PLUGIN_DATA}/venv" uv run --frozen --quiet --project "${CLAUDE_PLUGIN_ROOT}" gemini-bridge-setup status

   Read `values` (saved answers), `auth_methods` and `checks`. If
   `checks.api_key_env_invalid` is true, tell the user their saved config has
   an API key stored where a variable NAME belongs — never show the stored
   value — and ask them for the variable name instead. If `config_error` is
   set, tell the user their config couldn't be read and will be backed up.
3. Ask with AskUserQuestion, offering the saved value from `values` as the
   first option each time:
   - Auth method, from `auth_methods` only. `api_key` = Google AI Studio key,
     no GCP project. `adc` = gcloud login. `env` = service-account key file.
     `keychain` = macOS Keychain.
   - api_key: the NAME of the env var holding the key (default
     `values.api_key_env`). Never ask for the key itself, and never read,
     print or store its value.
   - adc/env/keychain: the GCP project, and the location (default
     `values.location`; `global` recommended).
   - keychain: the service and account names (defaults
     `values.keychain_service`, `values.keychain_account`).
   - Default thinking level (`none`/`low`/`medium`/`high`, default
     `values.default_thinking`) and default model (blank keeps the current
     value — none saved = newest Flash; `-` clears it).

   If any free-text answer contains a single quote (`'`), reject it and ask
   again — no legitimate project, location, model, env var name or keychain
   name needs one.
4. Run write with only the answers actually given, quoting every value in
   single quotes:

       UV_PROJECT_ENVIRONMENT="${CLAUDE_PLUGIN_DATA}/venv" uv run --frozen --quiet --project "${CLAUDE_PLUGIN_ROOT}" gemini-bridge-setup write --auth-method '…' [--project '…'] [--location '…'] [--thinking '…'] [--default-model '…'] [--transcript-dir '…'] [--keychain-service '…'] [--keychain-account '…'] [--api-key-env '…']

   On `"ok": false`, show `error` and offer to retry from step 3. Otherwise
   show any `warnings`, and show `backup`'s path if it is non-null.
5. Tell the user to restart Claude Code, or run `/mcp` and reconnect
   `gemini`, so the server reads the new config. For api_key auth, remind
   them the env var must be exported in the shell that starts Claude Code.
