# Gemini File Tools — Design Spec

**Issue:** #68 · **Date:** 2026-09-17 · **Branch:** `feat/gemini-file-tools` · **Status:** approved design, pending implementation plan
**Follow-on:** #70 (external MCP tools via the same loop) — out of scope here, but this design must not block it.

## 1. Goal

Give Gemini sandboxed read access to the repository for every generating tool, write access for the personas that produce deliverables, and guaranteed on-disk artifacts for `gemini_architect` and `gemini_review` — so Gemini's answers are grounded in the actual code and its deliverables survive context limits, compaction, `/clear`, and new sessions.

**Mental model:** Gemini never touches disk. It *requests* tool calls; the bridge executes them locally. The sandbox in this spec is therefore the only guard.

## 2. Decisions (locked)

| # | Decision |
|---|---|
| D1 | Read tools for **all** generating tools: `list_dir`, `glob`, `grep`, `read_file`. |
| D2 | Write (`write_file`) for `gemini_architect`, `gemini_review`, `gemini_brainstorm`. Not for `gemini_ask`, `gemini_debug`. |
| D3 | `gemini_architect` / `gemini_review` **always** persist an artifact; per-call `write_artifact: bool = True` opts out. `gemini_brainstorm` has the same parameter defaulting to `False`. |
| D4 | The **bridge** writes the artifact from the final response — not Gemini via `write_file`. Guaranteed, not model-dependent. Tool reply includes the artifact path. |
| D5 | Artifacts go to configurable `artifacts_dir`, default `./gemini-artifacts/`. Built in this issue. |
| D6 | Our own **async** tool loop (not SDK automatic function calling). |
| D7 | The bridge owns conversation history per session (replaces SDK `chats`). Snapshot before a call, commit only on success — and commit only the user prompt + final answer, not the intermediate tool turns (keeps sessions lean; signatures are only needed within a turn). |
| D8 | Sandbox root = Claude Code launch directory (CWD), resolved once at startup. No other root option. |
| D9 | `grep` is pure-Python regex. **No subprocess, ever.** |
| D10 | Default deny-list (see §5). Configurable. |
| D11 | Not a `breaking-change`: tool signatures only gain optional parameters. |
| D12 | MCP-ready: `ToolRegistry` accepts additional async tool sources so #70 adds MCP without reworking the loop. |

## 3. Architecture

### New modules

| Module | Responsibility | Depends on |
|---|---|---|
| `sandbox.py` | `Sandbox(root, deny)`: `resolve(path, *, for_write=False) -> Path` or raise `SandboxError`; `is_walk_skipped(dir)`. Pure path logic, no reads/writes. | stdlib |
| `file_tools.py` | The five tools over a `Sandbox`, each returning a JSON-serializable dict; exposes their `FunctionDeclaration`s. Enforces size/result caps. | `sandbox.py` |
| `tool_loop.py` | `ToolRegistry` (name → declaration + async handler; `add_source()` for #70) and `run_tool_loop(...)`. | google-genai types |
| `artifacts.py` | `write_artifact(dir, tool_name, topic, content) -> Path`, timestamp + slug naming, atomic write. | stdlib |

### Modified modules

| Module | Change |
|---|---|
| `client.py` | Async: uses `client.aio.models.generate_content`. Sessions become owned history lists (`list[Content]`) keyed `name:model` (LRU cap unchanged). `ask()` delegates to `run_tool_loop`. Retry-with-backoff unchanged. |
| `tools/base.py` | `call_gemini` becomes `async`; takes a capability set (`read`, `write`); builds the registry; handles fallback by restarting from the snapshot; writes the artifact when requested. |
| `tools/{ask,architect,review,brainstorm,debug}.py` | `async def`; each declares its capability row; architect/review/brainstorm gain `write_artifact`. |
| `config.py` | `artifacts_dir: str = "./gemini-artifacts"`; `file_tools` block (§7). |
| `transcript.py` | `append()` accepts an optional list of tool-call records rendered as lines inside the exchange block. Format otherwise unchanged. |
| `__main__.py` | Builds the `Sandbox` once from CWD + config; passes it to tools. |

Unchanged: `auth.py`, `models.py`, `tools/list_models.py`, `server.py` (FastMCP accepts async tools natively).

### Capability matrix

| Tool | Read | Write tool | Artifact default |
|---|---|---|---|
| `gemini_ask` | ✅ | ❌ | — |
| `gemini_debug` | ✅ | ❌ | — |
| `gemini_brainstorm` | ✅ | ✅ | off (opt-in) |
| `gemini_architect` | ✅ | ✅ | **on** (opt-out) |
| `gemini_review` | ✅ | ✅ | **on** (opt-out) |
| `gemini_list_models` | — | — | — |

## 4. Tool loop

```
snapshot = session.history (copy)
contents = snapshot + [user prompt]
for round in 1..MAX_ROUNDS (20):
    resp = generate_content(contents, config{tools=registry.declarations,
                                             automatic_function_calling.disable=True, thinking, system})
    if finish_reason == MALFORMED_FUNCTION_CALL: retry this round (max 2), else ClientError
    append resp's full model Content to contents   # preserves thought signatures
    calls = resp.function_calls
    if not calls: commit [user prompt, final model Content] to session; return resp.text
    for call in calls (sequential, in order):
        result = await registry.dispatch(call)   # never raises; errors -> {"error": "..."}
        record(call, result) for transcript
    append one user Content with all FunctionResponse parts
# cap reached
send "Tool budget exhausted — answer now with what you have." with tool_config mode=NONE
commit [user prompt, final model Content]; return text
```

- **Parallel calls** in one turn are executed sequentially and answered in a single message. Full model `Content` (incl. `thought_signature`) is kept in history — this is the guard against the Gemini 3.x signature 400 (python-genai #1938).
- **History commit** only on successful completion, and only the user prompt + final answer. Intermediate tool turns (which can carry up to 256 KiB per read) live only for the duration of the call; otherwise every later turn in the session would re-send them. Any exception leaves the session exactly as the snapshot.
- **Kill switch / no capabilities:** registry empty → no `tools` in config → a single-round call, identical to today's behavior.

## 5. Sandbox

**Resolution (every path, every tool):**
1. Reject empty input and NUL bytes.
2. `candidate = (root / path).resolve()` — follows symlinks, collapses `..`. Absolute inputs are accepted only if they resolve inside root.
3. Require `candidate.is_relative_to(root)` → blocks traversal, outside absolutes, symlink escape.
4. Deny-list is matched (case-insensitive) against **both** the requested relative path and `candidate.relative_to(root)`.

**Default deny-list:** `.git/**`, `.env`, `.env.*`, `**/*.pem`, `**/*.key`, `**/id_rsa*`, `**/*credentials*.json`, `**/*-sa-key.json`.

**Walk-skip dirs** (not traversed by `list_dir`/`glob`/`grep`, but still readable directly): `.git`, `.venv`, `venv`, `node_modules`, `__pycache__`, `dist`, `build`, `.mypy_cache`, `.ruff_cache`, `.pytest_cache`. Walks never follow symlinked directories.

**Tool caps:**

| Tool | Cap |
|---|---|
| `read_file(path, offset=0, limit=None)` | 256 KiB returned; binary (NUL in first 8 KiB) → error; line offset/limit for larger files |
| `list_dir(path=".")` | 500 entries |
| `glob(pattern)` | 500 results; pattern containing `..` or absolute → error |
| `grep(pattern, path=".", glob=None)` | 200 matches; scan budget 5,000 files / 20 MiB; lines > 2,000 chars skipped; invalid regex → error |
| `write_file(path, content)` | `max_write_bytes` (256 KiB); creates parent dirs inside root; create/overwrite only |

**Writes are atomic:** temp file in the target directory → `os.replace`. A symlink planted at the target is replaced, never written through.

**Never:** delete, chmod, execute, subprocess.

## 6. Artifacts

- Path: `<artifacts_dir>/YYYYMMDD-HHMM-<tool_name>-<slug>.md`; slug = first ~6 words of the prompt, lowercase kebab, ASCII, ≤ 48 chars; `-2`, `-3` suffix on collision.
- `artifacts_dir` resolved against CWD; must lie inside the sandbox root, else startup error.
- Contents: small header (tool, model, timestamp, session) + final response text.
- Tool reply to Claude: response text followed by `[gemini-bridge] artifact saved: <relative path>`.
- On write failure: response still returned, plus `[gemini-bridge notice] artifact not saved: <reason>`.

## 7. Configuration

```jsonc
"artifacts_dir": "./gemini-artifacts",
"file_tools": {
  "enabled": true,                 // master kill switch — false = no tools declared to Gemini
  "deny": [".git/**", ".env", ".env.*", "**/*.pem", "**/*.key",
           "**/id_rsa*", "**/*credentials*.json", "**/*-sa-key.json"],
  "max_write_bytes": 262144
}
```

`deny` replaces the default list when set (documented). All fields optional.

## 8. Error handling

| Situation | Behavior |
|---|---|
| Tool error (sandbox reject, missing file, cap, bad regex) | `{"error": "..."}` returned to Gemini; loop continues; logged |
| Unknown tool name | Same as tool error |
| `MALFORMED_FUNCTION_CALL` | Retry the round ≤ 2×, then `ClientError` |
| 503/429 after per-request retries | Existing fallback to `FALLBACK_MODEL`; loop restarts from the snapshot on the fallback session |
| Round cap (20) | Forced final no-tools answer |
| Artifact write fails | Answer returned + notice line |
| Any failure | Session history unchanged (snapshot) |

## 9. Transcript

Each exchange block gains tool-call lines, logged even when the call ultimately fails:

```
→ grep("def ask", path="src") → 3 matches
→ read_file("src/gemini_bridge/client.py") → 11.2 KiB
✗ read_file("../../.ssh/config") → rejected: path escapes repo root
```

Rejections are also logged at WARNING.

## 10. Testing

0. **Dev env:** add `[project.optional-dependencies] dev = ["pytest", "pytest-asyncio"]`; `python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'`. Record baseline green count **before** any code change.
1. **Async conversion:** existing suite passes with mechanical async edits only — no assertion changes.
2. **Sandbox** (real files in `tmp_path`, no mocks), table-driven: `../x`, `/etc/passwd`, `a/../../x`, symlink → outside, symlink → `.git`, `.GIT/config`, NUL byte, each deny pattern, symlinked dir during walk, symlink planted at write target.
3. **File tools:** each cap, binary detection, offset/limit, grep budget, invalid regex.
4. **Loop** (scripted fake model): text-only; one call; multiple calls in one turn; cap → forced answer; tool error returned not raised; MALFORMED retry; mid-loop 503 → fallback from snapshot; history committed only on success and contains only prompt + final answer; thought-signature content retained within the turn.
5. **Artifacts:** naming, slug, collision suffix, configured dir, failure notice.
6. **Static:** `ruff check`, `mypy --strict`, `bandit` (all configured in `pyproject.toml`).
7. **Live smoke (Developer API):** architect question about this repo → file reads in transcript + artifact on disk; prompt to read `../../.ssh/config` → rejection in transcript. Vertex deferred (no GCP creds on this machine).
8. **Code review** before landing on `develop` (`--no-ff`).

## 11. Delivery

One branch, three curated commits (each buildable, tests green):
1. `refactor:` async conversion + owned session history + dev extra — no behavior change.
2. `feat:` sandbox + file tools + tool loop + capability matrix.
3. `feat:` artifacts + config + transcript tool lines + docs (`README.md`, `docs/tools.md`, `docs/configuration.md`).

Estimated ~500 LOC source + ~400 LOC tests.

## 12. Out of scope

- External MCP tools (#70).
- Model-default staleness (#69).
- Roots other than CWD; `.gitignore` parsing; delete/move/execute tools.
- Live Vertex verification (deferred to GCP-capable machine).
