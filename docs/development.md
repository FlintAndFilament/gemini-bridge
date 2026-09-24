# Development Guide

## Setup

```bash
git clone git@github.com:FlintAndFilament/sidekick.git
cd sidekick
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m pip install pre-commit
.venv/bin/pre-commit install
.venv/bin/pre-commit install --hook-type commit-msg
```

The `dev` extra installs pytest, pytest-asyncio, ruff, mypy, bandit and types-regex.
Python 3.11 or newer is required.

## Running tests

```bash
.venv/bin/python -m pytest -q
```

667 tests across 19 modules in `tests/`. All mocked: no network calls, runs in about
2 seconds.

## Running the plugin locally

```bash
claude --plugin-dir .
```

Runs the plugin straight from this checkout — no marketplace, no install step. `uv` builds the
plugin's own venv at `${CLAUDE_PLUGIN_DATA}/venv` on first launch and reuses it after. Inside
that session, run `/sidekick:setup` to write `~/.config/sidekick/config.json`, then call
any `gemini_*` tool (or `gemini_help`) to confirm the `gemini` MCP server connected.

Whenever you add or change a dependency in `pyproject.toml`, run `uv lock`. `uv.lock` is
committed and the server launches with `uv run --frozen`, which refuses to resolve a stale
lock file — a dependency added to `pyproject.toml` without a matching `uv lock` is never
installed for the live server, even though `.venv` (installed separately, above) picks it up
fine for tests.

The sandbox root is the project root: `CLAUDE_PROJECT_DIR` when Claude Code sets it, otherwise
the server's working directory (#102). File tools switch off when that directory is your home
directory, `/`, or an ancestor of home.

After a restart, check the tool list with `gemini_help` or `gemini_list_models`. A resumed
Claude Code conversation can show tool schemas from before the restart.

## Watching logs

The server writes to a daily log file (the 4 most recent are kept):

```bash
tail -f ~/.config/sidekick/logs/$(ls -t ~/.config/sidekick/logs/*.log | head -1 | xargs basename)
```

Set `SIDEKICK_LOG_LEVEL=DEBUG` before starting Claude Code for per-call detail. See [logging.md](logging.md) for full reference.

## Code quality

```bash
# Lint + format
.venv/bin/ruff check src tests
.venv/bin/ruff format src tests

# Types (strict mode, configured in pyproject.toml)
.venv/bin/mypy src

# Security scan
.venv/bin/bandit -c pyproject.toml -r src

# All pre-commit hooks
.venv/bin/pre-commit run --all-files
```

Current state: `ruff check` and `ruff format --check` are clean. `mypy src` reports 2
long-standing errors in `auth.py` (`from_service_account_info` is untyped).

The pre-commit hooks run ruff (lint with `--fix`, and format), bandit, semgrep
(`p/python`, `p/secrets`), and the standard safety hooks, including `detect-private-key` and
`no-commit-to-branch` for `main` and `develop`.

## CI

Three GitHub Actions workflows in `.github/workflows/`. All are security scans; none run the
tests.

| Workflow | Runs on | What it does |
|---|---|---|
| `semgrep.yml` | push to `develop` / `main`, every PR | Semgrep OSS (`p/python`, `p/secrets`, `p/owasp-top-ten`), fails on findings, uploads SARIF to the Security tab |
| `trivy.yml` | push to `develop` / `main`, every PR | Trivy filesystem scan for HIGH and CRITICAL CVEs, uploads SARIF to the Security tab |
| `dependency-review.yml` | PRs that change `pyproject.toml`, `requirements*.txt`, `setup.cfg` or `setup.py` | Blocks new dependencies with high-severity advisories |

Each job declares least-privilege permissions (#72).

## Adding a new tool

1. Create `src/sidekick/tools/{name}.py` following an existing tool file. Required: the
   file header docstring, a system prompt, `_WRITE` and `_ARTIFACTS` values, a `CAPABILITY`
   row, and `register(mcp, client, transcript, workspace)`. The body calls `call_gemini()`.

2. In `src/sidekick/tools/__init__.py`, import its `register` and `CAPABILITY`, add the
   capability to `CAPABILITIES`, and add the register function to `__all__`.

3. Call `register_{name}(mcp, client, transcript, workspace)` in
   `src/sidekick/server.py`.

4. Add tests to `tests/test_tools.py`: at minimum, the tool registers, and `call_gemini()`
   errors pass through. `tests/test_capability_metadata.py` and `tests/test_guide.py` cover
   the advertised rows.

5. Document it in `docs/tools.md`.

The server instructions and `gemini_help` pick up the new capability row by themselves:
`guide.py` builds its text from `CAPABILITIES`. Keep the instructions under
`INSTRUCTIONS_BUDGET` (2000 characters); Claude Code drops anything past about 2048.

## Branching workflow

git-flow. There is no `CLAUDE.md` in this repository; the rules are:

- `main` is production, `develop` is integration. No direct commits to either.
- Cut `feat/*`, `fix/*`, `chore/*` or `docs/*` branches off `develop`.
- Curate WIP commits into a few clean ones before landing, then merge into `develop` with
  `git merge --no-ff`. No squash merges, no rebase merges. Delete the branch after.
- No PR for landing on `develop`. PRs are only for `develop → main` promotions (and the rare
  emergency fix cherry-picked onto a branch off `main`), merged with `--no-ff`.
- Never merge `main` back into `develop`. After a promotion, fast-forward local `main` with
  `git checkout main && git pull --ff-only`.
- `--no-verify` is only for the `git merge --no-ff` step, which the `no-commit-to-branch`
  hook would otherwise block.
- Commits are Conventional Commits (`feat:`, `fix:`, `docs:`, `chore:`) and reference their
  issue: `refs #N` on work commits, `closes #N` on the one that finishes it.

## File header standard

Every `.py` file in `src/` begins with a module docstring: file path, one-line description,
Responsibilities, Design notes (SOLID callouts), Raises, Used by, Imports. Copy the shape of
an existing module.
