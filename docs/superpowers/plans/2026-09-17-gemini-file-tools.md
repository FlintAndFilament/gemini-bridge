# Gemini File Tools Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give Gemini sandboxed repo read tools (all generating tools), `write_file` (architect/review/brainstorm), and bridge-written artifacts (architect/review default-on), via our own async tool loop.

**Architecture:** `GeminiClient` goes async and owns per-session history (`Session.history: list[Content]`). `ask()` runs `run_tool_loop()` over a `ToolRegistry`; file tools are one registry source (MCP becomes another in #70). A `Sandbox` resolves every path; `Workspace` bundles sandbox + file tools + artifact store and is injected into tool registration.

**Tech Stack:** Python ≥3.11, google-genai 2.24.0 (`client.aio.models.generate_content`, `types.FunctionDeclaration(parameters_json_schema=...)`), mcp 1.30 FastMCP (async tools), pydantic 2, pytest + pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-09-17-gemini-file-tools-design.md`

## Global Constraints

- No subprocess / exec anywhere in new code (spec D9).
- Every Gemini-requested path goes through `Sandbox`; no direct `open()` on model-supplied paths.
- New modules: zero new `ruff`, `mypy --strict`, `bandit` findings. Baselines: ruff clean; mypy 38 existing errors (client.py/auth.py); bandit 5 existing findings.
- Caps: read 256 KiB returned / 20 MiB raw; list 500; glob 500; grep 200 matches, 5,000 files, 20 MiB, lines >2,000 chars skipped; write `max_write_bytes` default 262144; loop 20 rounds; MALFORMED retry 2.
- Existing tool signatures only gain optional params. `register_x(mcp, client, transcript)` keeps working with no workspace (no file tools, no artifacts).
- Commits: Conventional, `refs #68` on the command line (multiple `-m`), Co-Authored-By + Claude-Session trailers.
- Tests: `.venv/bin/python -m pytest -q`. Baseline 119 passed.

## File Map

| File | Status | Responsibility |
|---|---|---|
| `src/gemini_bridge/errors.py` | create | `ClientError` (moved; re-exported from `client.py`) — breaks the client↔tool_loop import cycle |
| `src/gemini_bridge/client.py` | modify | async; `Session`; `generate()` with retry; `ask()` → tool loop; config builder takes tool declarations |
| `src/gemini_bridge/tool_loop.py` | create | `ToolRegistry`, `ToolCallRecord`, `run_tool_loop()` |
| `src/gemini_bridge/sandbox.py` | create | `Sandbox`, `SandboxError`, `DEFAULT_DENY`, `WALK_SKIP_DIRS` |
| `src/gemini_bridge/file_tools.py` | create | `FileTools` (5 ops) + `build_file_registry()` |
| `src/gemini_bridge/artifacts.py` | create | `ArtifactStore`, `slugify()` |
| `src/gemini_bridge/workspace.py` | create | `Workspace`, `build_workspace()` |
| `src/gemini_bridge/config.py` | modify | `artifacts_dir`, `FileToolsConfig` |
| `src/gemini_bridge/transcript.py` | modify | `append(..., tool_calls: Sequence[str] = ())` |
| `src/gemini_bridge/tools/base.py` | modify | async `call_gemini(..., workspace, write, artifact_topic)` |
| `src/gemini_bridge/tools/{ask,debug,brainstorm,architect,review}.py` | modify | async; capability row; `write_artifact` param |
| `src/gemini_bridge/server.py`, `__main__.py` | modify | build + inject `Workspace` |
| `pyproject.toml` | modify | `dev` extra; pytest asyncio mode |
| `tests/test_{sandbox,file_tools,tool_loop,artifacts,workspace}.py` | create | |
| `tests/test_{client,tools,transcript,config}.py` | modify | async + owned-history mocks |
| `README.md`, `docs/tools.md`, `docs/configuration.md`, `.gitignore` | modify | docs; ignore `gemini-artifacts/` in this repo |

**Deviation from spec §10.1:** tests asserting on SDK `chats.create`/`send_message` cannot survive the removal of `chats` "mechanically" — they are rewritten to assert the same behavior (session identity, LRU, retry counts, error messages) against `aio.models.generate_content`. Error-message assertions stay byte-identical.

---

### Task 1: Async client with owned session history (no behavior change)

**Files:** Create `src/gemini_bridge/errors.py`, `tests/conftest.py`; Modify `client.py`, `tools/base.py`, `tools/{ask,debug,brainstorm,architect,review}.py`, `pyproject.toml`, `tests/test_client.py`, `tests/test_tools.py`.

**Interfaces — Produces:**
```python
# errors.py
class ClientError(Exception): ...

# client.py
@dataclass
class Session:
    model: str
    history: list[Content] = field(default_factory=list)

class GeminiClient:
    def get_or_create_session(self, name: str = "default", model: Optional[str] = None) -> Session
    def build_config(self, thinking: ThinkingLevel, system_instruction: Optional[str] = None,
                     model: Optional[str] = None,
                     declarations: Optional[list[FunctionDeclaration]] = None,
                     allow_tools: bool = True) -> GenerateContentConfig
    async def generate(self, model: str, contents: list[Content],
                       config: GenerateContentConfig) -> GenerateContentResponse  # retry/backoff, raises ClientError
    async def ask(self, session: Session, prompt: str, thinking: Optional[ThinkingLevel] = None,
                  system_instruction: Optional[str] = None,
                  registry: Optional[ToolRegistry] = None,
                  records: Optional[list[ToolCallRecord]] = None) -> str
# _build_generation_config kept as a thin alias of build_config (tests use it).

# tools/base.py
async def call_gemini(client, transcript, tool_name, session_name, system_instruction, prompt,
                      thinking, model=None) -> ToolResult     # Task 4 adds workspace/write/artifact_topic
```

- [ ] **Step 1:** `pyproject.toml`: add `[project.optional-dependencies] dev = ["pytest>=8", "pytest-asyncio>=0.24", "ruff", "mypy", "bandit"]` and `[tool.pytest.ini_options] asyncio_mode = "auto"`.
- [ ] **Step 2:** Rewrite `tests/test_client.py` session + ask tests against the new surface. Helper:
```python
def _text_response(text: str, finish: str = "STOP") -> types.GenerateContentResponse:
    parts = [types.Part.from_text(text=text)] if text else []
    return types.GenerateContentResponse(candidates=[types.Candidate(
        content=types.Content(role="model", parts=parts),
        finish_reason=types.FinishReason(finish))])
```
  Session tests: same name+model → same `Session` object; different name or model → different; LRU evicts oldest at `_MAX_SESSIONS + 1`; no SDK call on session creation. Ask tests (async, `client._raw_client.aio.models.generate_content = AsyncMock(...)`): returns text; empty text → `ClientError("...finish_reason=SAFETY")`; no candidates → `finish_reason=UNKNOWN`; exception → match `"inference failed"`; 503 then success → 2 calls (patch `gemini_bridge.client.asyncio.sleep` with `AsyncMock`); 503 ×4 → match `"after 4 attempt"`; 400 → 1 call, sleep not awaited; `system_instruction` reaches `config`; **new:** success appends exactly `[user prompt, model reply]` to `session.history`; **new:** failure leaves `session.history` unchanged.
- [ ] **Step 3:** Run `.venv/bin/python -m pytest tests/test_client.py -q` → FAIL (no `Session`, `ask` not awaitable).
- [ ] **Step 4:** Implement. `errors.py` holds `ClientError`; `client.py` does `from gemini_bridge.errors import ClientError` (re-export). `generate()` = the existing retry loop with `await asyncio.sleep(delay)` and `await self._raw_client.aio.models.generate_content(model=..., contents=..., config=...)`, identical log/error strings. In this task `ask()` does a single `generate()`, extracts text with the existing empty-text/finish_reason logic, and on success does `session.history.extend([user_content, model_content])`. `build_config` adds, when `declarations`: `tools=[types.Tool(function_declarations=declarations)]`, `automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True)`, and when `not allow_tools`: `tool_config=types.ToolConfig(function_calling_config=types.FunctionCallingConfig(mode="NONE"))`.
- [ ] **Step 5:** Rewrite `tests/test_tools.py::TestCallGemini` to `await call_gemini(...)` with `generate_content` `AsyncMock`; fallback tests use `side_effect=[busy]*4 + [_text_response("fallback answer")]`. Make `call_gemini` async (`await client.ask(...)`), and each tool function `async def` returning `await call_gemini(...)`.
- [ ] **Step 6:** `.venv/bin/python -m pytest -q` → all pass (count ≥ 119 minus replaced + new). `ruff check src tests`; `mypy src` error count ≤ 38.
- [ ] **Step 7:** Commit `refactor(client): async inference with bridge-owned session history`.

### Task 2: Sandbox

**Files:** Create `src/gemini_bridge/sandbox.py`, `tests/test_sandbox.py`.

**Interfaces — Produces:**
```python
DEFAULT_DENY: tuple[str, ...] = (".git/**", ".env", ".env.*", "**/*.pem", "**/*.key",
                                 "**/id_rsa*", "**/*credentials*.json", "**/*-sa-key.json")
WALK_SKIP_DIRS: frozenset[str]  # .git .venv venv node_modules __pycache__ dist build .mypy_cache .ruff_cache .pytest_cache
class SandboxError(Exception): ...
class Sandbox:
    root: Path
    def __init__(self, root: Path, deny: Sequence[str] = DEFAULT_DENY) -> None
    def resolve(self, path: str) -> Path                 # read/list target; symlinks followed
    def resolve_for_write(self, path: str) -> Path       # parent resolved, final component NOT followed
    def is_denied(self, path: Path) -> bool              # absolute path inside root
    def relative(self, path: Path) -> str                # posix, "." for root
    def walk(self, start: Path) -> Iterator[Path]        # files only, sorted, skips WALK_SKIP_DIRS + denied, never follows dir symlinks
```
Deny semantics (case-insensitive, checked on the path **and every ancestor** relative to root): leading `**/` is stripped; a pattern ending `/**` matches a path equal to its prefix; a pattern without `/` matches the path's last component via `fnmatch`; otherwise `fnmatch` on the full relative path.

- [ ] **Step 1:** Write `tests/test_sandbox.py` (real files under `tmp_path`, `tmp_path` resolved). Table-driven `resolve` rejections with `pytest.raises(SandboxError)`: `""`, `"a\x00b"`, `"../x"`, `"a/../../x"`, `"/etc/passwd"`, `".git/config"`, `".GIT/config"`, `".git"`, `".env"`, `"sub/.env.local"`, `"keys/server.pem"`, `"deploy.key"`, `"id_rsa.pub"`, `"gcp-credentials-prod.json"`, `"svc-sa-key.json"`, symlink `out -> /tmp outside` (`out/x`), symlink `notes -> .git/config`. Accepts: `"src/a.py"`, `"./src/a.py"`, absolute path inside root, `"."` → root, `"new/file.md"` (nonexistent ok). `resolve_for_write`: planted symlink `link.md -> ../outside.md` returns `root/link.md` (not the target); parent symlink escaping root rejected; `"."`/directory target rejected. `walk`: skips `.venv/`, `node_modules/`, `.git/`, denied files, symlinked dir pointing outside and inside root; yields sorted files. Custom `deny=["*.secret"]` replaces defaults (`.env` then allowed).
- [ ] **Step 2:** Run → FAIL (module missing).
- [ ] **Step 3:** Implement `sandbox.py` per the interface (stdlib only: `os`, `fnmatch`, `pathlib`). `resolve`: reject empty/NUL → `p = Path(path)`; `lexical = Path(os.path.normpath(p if p.is_absolute() else self.root / p))`; `candidate = lexical.resolve()`; both must equal root or be `is_relative_to(root)` else `SandboxError("path escapes repo root: …")`; deny-check both. `resolve_for_write`: lexical as above; name must not be empty/`.`/`..`; `parent = lexical.parent.resolve()` inside root; `target = parent / lexical.name`; deny-check `target` and `target.resolve()` when it exists; reject existing directory. `walk`: `os.walk(start, followlinks=False)` pruning `dirnames` in place (skip set, denied, symlinks), yield non-symlink-escaping, non-denied files sorted.
- [ ] **Step 4:** Run → PASS. `ruff`, `mypy src/gemini_bridge/sandbox.py --strict` clean, `bandit` clean.
- [ ] **Step 5:** (commit with Task 3)

### Task 3: File tools + registry source

**Files:** Create `src/gemini_bridge/tool_loop.py` (registry part only), `src/gemini_bridge/file_tools.py`, `tests/test_file_tools.py`.

**Interfaces — Produces:**
```python
# tool_loop.py
Handler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
@dataclass(frozen=True)
class ToolCallRecord:
    name: str; args: dict[str, Any]; ok: bool; summary: str
    def render(self) -> str   # "→ read_file(path='src/a.py') → 1.2 KiB" / "✗ read_file(path='../x') → path escapes repo root: ../x"
class ToolRegistry:
    def add(self, declaration: FunctionDeclaration, handler: Handler) -> None   # ValueError on duplicate name
    @property
    def declarations(self) -> list[FunctionDeclaration]
    def __len__(self) -> int
    async def dispatch(self, name: str, args: dict[str, Any]) -> dict[str, Any]  # never raises: unknown/exception → {"error": ...}

# file_tools.py
class FileTools:
    def __init__(self, sandbox: Sandbox, max_write_bytes: int = 262_144) -> None
    def list_dir(self, path: str = ".") -> dict[str, Any]         # {"path", "entries": [{"name","type","size"}], "truncated"}
    def glob(self, pattern: str) -> dict[str, Any]                # {"matches": [rel], "truncated"}
    def grep(self, pattern: str, path: str = ".", glob: Optional[str] = None) -> dict[str, Any]
                                                                  # {"matches": [{"path","line","text"}], "files_scanned", "truncated"}
    def read_file(self, path: str, offset: int = 0, limit: Optional[int] = None) -> dict[str, Any]
                                                                  # {"path","start_line","total_lines","content","truncated"}
    def write_file(self, path: str, content: str) -> dict[str, Any]   # {"path","bytes_written"}
def build_file_registry(tools: FileTools, *, write: bool) -> ToolRegistry
```
Glob syntax: `**/` → any directories (incl. none), `**` → anything, `*` → within one segment, `?` → one char; matched against the root-relative posix path. `grep`'s `glob` filter without `/` matches the file name. Tool errors: `SandboxError` → `{"error": "rejected: <msg>"}` + WARNING log; other `OSError`/`ValueError`/`re.error`/`TypeError` → `{"error": "<Type>: <msg>"}`. Handlers run the sync op via `asyncio.to_thread`.

- [ ] **Step 1:** Write `tests/test_file_tools.py`: list_dir sorts, types dirs/files, caps at 500 (`truncated`), rejects `..`; glob `**/*.py` finds nested + top-level, `*.py` top-level only, skips `.venv`, rejects `..` and absolute patterns, caps at 500; grep finds line numbers, `glob="*.py"` filter, invalid regex → error dict via registry, 200-match cap, skips binary files and >2,000-char lines, file budget via monkeypatched cap; read_file full, `offset/limit` lines with `start_line`/`total_lines`, binary → error, >256 KiB → truncated, denied → rejected; write_file creates parents, overwrites, over-cap → error, planted symlink replaced not followed (outside file unchanged), no temp files left behind; registry: read-only registry has exactly 4 names, write registry 5; dispatch of unknown tool → `{"error": "unknown tool: nope"}`; `ToolCallRecord.render()` both forms.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement. Declarations use `types.FunctionDeclaration(name=…, description=…, parameters_json_schema={"type": "object", "properties": {...}, "required": [...]})`. Descriptions tell the model paths are relative to repo root and name the caps. Write: `fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".gb-")`, write, `os.replace(tmp, target)`, unlink tmp on failure.
- [ ] **Step 4:** Run → PASS; static checks clean.
- [ ] **Step 5:** Commit `feat(sandbox): repo-confined file tools for Gemini (list_dir, glob, grep, read_file, write_file)`.

### Task 4: Tool loop + wiring (capability matrix live)

**Files:** Modify `tool_loop.py`, `client.py`, `transcript.py`, `config.py`, `tools/base.py`, 5 tool modules, `server.py`, `__main__.py`; Create `workspace.py`, `tests/test_tool_loop.py`, `tests/test_workspace.py`; Modify `tests/test_transcript.py`, `tests/test_config.py`, `tests/test_tools.py`.

**Interfaces — Produces:**
```python
# tool_loop.py
GenerateFn = Callable[[list[Content], bool], Awaitable[GenerateContentResponse]]  # (contents, allow_tools)
MAX_ROUNDS = 20; MAX_MALFORMED_RETRIES = 2
BUDGET_EXHAUSTED_PROMPT = "Tool budget exhausted — answer now with what you have."
@dataclass(frozen=True)
class LoopResult: text: str; content: Content
async def run_tool_loop(generate: GenerateFn, contents: list[Content], registry: ToolRegistry,
                        records: list[ToolCallRecord], *, max_rounds: int = MAX_ROUNDS) -> LoopResult

# config.py
class FileToolsConfig(BaseModel):
    enabled: bool = True
    deny: list[str] = Field(default_factory=lambda: list(DEFAULT_DENY))
    max_write_bytes: int = Field(default=262_144, gt=0)
Config.artifacts_dir: str = "./gemini-artifacts"      # consumed in Task 5
Config.file_tools: FileToolsConfig = FileToolsConfig()

# workspace.py
@dataclass(frozen=True)
class Workspace:
    sandbox: Sandbox; file_tools: FileTools; tools_enabled: bool
    artifacts: Optional["ArtifactStore"] = None        # set in Task 5
    def registry(self, *, write: bool) -> Optional[ToolRegistry]   # None when tools disabled
def build_workspace(config: Config, cwd: Path) -> Workspace

# transcript.py
def append(self, tool_name, prompt, response, thinking, session="default", timestamp=None,
           tool_calls: Sequence[str] = ()) -> None    # renders "**Tool calls:**" list when non-empty

# tools/base.py
async def call_gemini(..., model=None, workspace: Optional[Workspace] = None, write: bool = False) -> ToolResult
# every register_x(mcp, client, transcript, workspace: Optional[Workspace] = None)
# build_server(client, transcript, workspace: Optional[Workspace] = None)
```
Capability rows: ask/debug `write=False`; brainstorm/architect/review `write=True`. When a registry is active, `call_gemini` appends a tools preamble to the system instruction: read tools available, paths relative to repo root, read before asserting, prefer one tool call at a time; if write: only write files when the request calls for one, and the final answer is saved automatically — don't write it yourself.

- [ ] **Step 1:** `tests/test_tool_loop.py` with a scripted fake `generate` (list of responses; records `(contents_snapshot, allow_tools)` per call). Helpers `_call_response(*calls)` (model Content of `Part.from_function_call` parts, one also carrying `thought_signature=b"sig"`) and `_text_response`. Cases: text-only → 1 call, `LoopResult.text`; one call → handler invoked with args, second request's contents end with a user Content containing the `FunctionResponse`; two calls in one turn → executed in order, both responses in one Content; the model Content with `thought_signature` is present verbatim in the next request; handler raising → `{"error": ...}` sent back, loop continues, record `ok=False`; unknown tool → error response; MALFORMED ×2 then text → success; MALFORMED ×3 → `ClientError` matching `MALFORMED_FUNCTION_CALL`; cap: `max_rounds=2` with endless calls → third request has `allow_tools=False` and last user part is `BUDGET_EXHAUSTED_PROMPT`; caller's input `contents` list not mutated.
- [ ] **Step 2:** Add to `tests/test_client.py`: `ask()` with a registry and a scripted tool call → `session.history` gains exactly 2 Contents (prompt, final answer); `records` populated; `build_config(declarations=[…])` sets tools + AFC disabled; `allow_tools=False` sets mode NONE; no declarations → `config.tools` is None. `tests/test_transcript.py`: `tool_calls` lines rendered; omitted → format unchanged. `tests/test_config.py`: defaults; `deny` override; `max_write_bytes=0` rejected. `tests/test_workspace.py`: builds from `tmp_path`; `tools_enabled=False` → `registry()` is None; write registry has `write_file`, read one doesn't. `tests/test_tools.py`: ask with workspace → declarations passed are the 4 read tools; architect → 5; no workspace → no tools; tool calls land in transcript; transcript written with records when the call fails after tool calls.
- [ ] **Step 3:** Run → FAIL.
- [ ] **Step 4:** Implement `run_tool_loop` (copy `contents`; per round: generate with `allow_tools=True`, MALFORMED retry, append full candidate Content, collect `function_call` parts; none → return text (empty → existing no-text `ClientError`); else dispatch sequentially, `records.append(...)`, append one user Content of `Part.from_function_response(name=, response=)`; after cap append budget prompt and generate once with `allow_tools=False`). `client.ask` builds `GenerateFn` via `functools.partial`-style closure over `build_config(..., declarations=registry.declarations if registry else None, allow_tools=flag)` and commits `[user_content, result.content]`. Wire `Workspace` through base, tools, server, `__main__` (`build_workspace(config, Path.cwd())`, log root + enabled state).
- [ ] **Step 5:** Full suite PASS; static checks.
- [ ] **Step 6:** Commit `feat(tools): agentic tool loop with per-tool file capabilities`.

### Task 5: Artifacts + docs

**Files:** Create `src/gemini_bridge/artifacts.py`, `tests/test_artifacts.py`; Modify `workspace.py`, `tools/base.py`, `tools/{architect,review,brainstorm}.py`, `tests/test_tools.py`, `tests/test_workspace.py`, `README.md`, `docs/tools.md`, `docs/configuration.md`, `.gitignore`.

**Interfaces — Produces:**
```python
def slugify(text: str, max_words: int = 6, max_len: int = 48) -> str   # "" → "untitled"
class ArtifactStore:
    def __init__(self, directory: Path, clock: Callable[[], datetime] = datetime.now) -> None
    directory: Path
    def save(self, tool_name: str, topic: str, content: str, *, model: str, session: str) -> Path
        # <dir>/YYYYMMDD-HHMM-<tool_name>-<slug>.md ; -2, -3 … on collision; atomic write; header lines
# call_gemini(..., artifact_topic: Optional[str] = None)   # None = don't save
# architect/review: write_artifact: bool = True ; brainstorm: write_artifact: bool = False
```
`build_workspace` validates `artifacts_dir` via `sandbox.resolve()` (must be inside root, not denied) and raises `SandboxError` otherwise; `__main__` logs and exits 1 on it. Reply suffix: `\n\n[gemini-bridge] artifact saved: <relative path>`; failure: `\n\n[gemini-bridge notice] artifact not saved: <reason>`. Artifacts are saved regardless of `file_tools.enabled` (bridge-written).

- [ ] **Step 1:** `tests/test_artifacts.py`: slug (punctuation, unicode → ASCII, word/length caps, empty); filename with fixed clock; collision suffixes; header contains tool/model/session; directory created. `tests/test_tools.py`: architect default saves + suffix; `write_artifact=False` no file; brainstorm default no file, `True` saves; review saves; save failure (dir made read-only / monkeypatched `save` raising `OSError`) → answer + notice; ask has no `write_artifact` param. `tests/test_workspace.py`: `artifacts_dir` outside root → `SandboxError`; inside `.git` → `SandboxError`.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Suite PASS + static checks.
- [ ] **Step 5:** Docs: README (capability matrix, sandbox summary, artifacts, kill switch), `docs/tools.md` (per-tool params incl. `write_artifact`, file tools list with caps), `docs/configuration.md` (`artifacts_dir`, `file_tools`). `.gitignore`: add `gemini-artifacts/`.
- [ ] **Step 6:** Commit `feat(artifacts): persist architect/review output to gemini-artifacts/`.

### Task 6: Live verification + land

- [ ] **Step 1:** Live smoke on the Developer API from this repo (script in scratchpad using `GeminiClient` + `build_workspace` + `call_gemini`): architect question about `client.py` → transcript shows `read_file`/`grep` lines, artifact file exists; ask "read ../../.ssh/config" → transcript `✗ … rejected`; `file_tools.enabled=false` → no tool lines.
- [ ] **Step 2:** Code review of the branch diff (`code-review` skill); fix findings; re-run suite.
- [ ] **Step 3:** Merge `feat/gemini-file-tools` → `develop` with `--no-ff`, delete branch, push `develop`. Comment on #68 with results. (Promotion to `main` only after user confirmation.)
