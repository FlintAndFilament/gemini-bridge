# Development Guide

## Setup

```bash
git clone git@github.com:FlintAndFilament/gemini-bridge.git
cd gemini-bridge
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m pip install pre-commit
.venv/bin/pre-commit install
.venv/bin/pre-commit install --hook-type commit-msg
```

The `dev` extra installs pytest, pytest-asyncio, ruff, mypy, bandit and types-regex.
Python 3.11 or newer is required.

### The live server uses system Python, not `.venv`

The MCP server that Claude Code runs is an editable install in the system Python 3.13
(`/Library/Frameworks/Python.framework/Versions/3.13/bin/python3` on this machine). It is
registered as the `gemini-bridge` console script. Editable means code changes take effect on
the next Claude Code restart, but **new dependencies do not**: when you add one to
`pyproject.toml`, install it there too:

```bash
python3 -m pip install -e .
```

Otherwise the tests pass in `.venv` and the live server fails at import.

## Running tests

```bash
.venv/bin/python -m pytest -q
```

611 tests across 15 modules in `tests/`. All mocked: no network calls, runs in about
2 seconds.

## Running the server locally

```bash
# Configure first
bash setup.sh

# Start the server (MCP stdio transport — reads protocol from stdin)
python3 -m gemini_bridge
```

To register with Claude Code and use tools interactively:
```bash
claude mcp add -s user gemini-bridge -- python3 -m gemini_bridge
claude mcp list
```

Note the `--` separator — without it, `-m` is parsed as a `claude` option. With
`api_key` auth, pass the key's environment variable too (`-e GEMINI_API_KEY="$GEMINI_API_KEY"`);
`setup.sh` prints the exact command at the end.

Launch Claude Code from the repository you want Gemini to read: the sandbox root is the
directory the server starts in. File tools switch off when that directory is your home
directory, `/`, or an ancestor of home.

After a restart, check the tool list with `gemini_help` or `gemini_list_models`. A resumed
Claude Code conversation can show tool schemas from before the restart.

## Watching logs

The server writes to a daily log file (the 4 most recent are kept):

```bash
tail -f ~/.config/gemini-bridge/logs/$(ls -t ~/.config/gemini-bridge/logs/*.log | head -1 | xargs basename)
```

Set `GEMINI_BRIDGE_LOG_LEVEL=DEBUG` before starting Claude Code for per-call detail. See [logging.md](logging.md) for full reference.

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

1. Create `src/gemini_bridge/tools/{name}.py` following an existing tool file. Required: the
   file header docstring, a system prompt, `_WRITE` and `_ARTIFACTS` values, a `CAPABILITY`
   row, and `register(mcp, client, transcript, workspace)`. The body calls `call_gemini()`.

2. In `src/gemini_bridge/tools/__init__.py`, import its `register` and `CAPABILITY`, add the
   capability to `CAPABILITIES`, and add the register function to `__all__`.

3. Call `register_{name}(mcp, client, transcript, workspace)` in
   `src/gemini_bridge/server.py`.

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
