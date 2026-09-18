# Tools Reference

Six tools: five inference tools (`gemini_ask`, `gemini_brainstorm`, `gemini_review`,
`gemini_debug`, `gemini_architect`) and one discovery utility (`gemini_list_models`).

The five inference tools share three optional parameters:
- `thinking: "none" | "low" | "medium" | "high"` — reasoning depth; falls back to `default_thinking` in config
- `session_name: str` — session identifier; v1 always uses the default session per tool
- `model: str` — `flash` / `flash-lite` / `pro` for the newest release of a family, or any Gemini
  model id; omit for the server default (the newest Flash, resolved at startup). The
  parameter's description is **backend-aware** (it lists the models valid for your active
  backend). Sessions are keyed by tool + `session_name` + `model`, so switching model starts a
  fresh session. Call [`gemini_list_models`](#gemini_list_models) to discover valid values, and
  see [configuration.md](configuration.md#choosing-a-model) for the recommended set and fallback
  behavior.

---

## Repository access

Gemini can inspect — and for some tools, write to — the repository Claude Code was launched in.
Gemini never touches disk itself: it *requests* a tool call, and the bridge runs it locally
inside a sandbox, then sends the result back. One MCP call can involve up to 20 such rounds.

| Tool | Read tools | `write_file` | Saves answer as artifact |
|---|---|---|---|
| `gemini_ask` | ✅ | — | — |
| `gemini_debug` | ✅ | — | — |
| `gemini_brainstorm` | ✅ | ✅ | opt-in (`write_artifact=true`) |
| `gemini_architect` | ✅ | ✅ | **default** (`write_artifact=false` to skip) |
| `gemini_review` | ✅ | ✅ | **default** (`write_artifact=false` to skip) |

**Tools Gemini can call** (paths are relative to the repo root):

| Tool | Does | Cap |
|---|---|---|
| `list_dir(path=".")` | Directory entries with type and size | 500 entries |
| `glob(pattern)` | Files matching a glob; `**/` spans directories, `*` stays in one | 500 results |
| `grep(pattern, path=".", glob=None)` | Python-regex search; returns path, line number, text | 200 matches; 5,000 files / 20 MiB scanned |
| `read_file(path, offset=0, limit=None)` | Text file contents, pageable by line | 256 KiB per call |
| `write_file(path, content)` | Create or overwrite a text file (parents created) | `max_write_bytes` (256 KiB) |

**Sandbox rules:**
- Every path is fully resolved (symlinks followed, `..` collapsed) and must stay inside the root —
  traversal, absolute paths elsewhere, and symlink escapes are rejected.
- The deny-list (`.git/**`, `.env`, `.env.*`, `*.pem`, `*.key`, `*.p12`, `*.pfx`, `id_rsa*`,
  `id_dsa*`, `id_ecdsa*`, `id_ed25519*`, `*credentials*.json`, `*-sa-key.json`, `.ssh/**`,
  `.aws/**`, `.gnupg/**`, `.netrc`, `.npmrc`, `.pypirc`) is checked case-insensitively, **at any
  depth** (so a nested `vendor/x/.git/` is covered), on both the requested and the resolved path.
- File tools switch themselves off when Claude Code is launched from your home directory, the
  filesystem root, or any folder containing your home directory. The startup log says why.
- `grep` stops after 10 seconds, so a pathological regex returns `timed_out` instead of hanging
  the call.
- Searches skip `.venv`, `venv`, `node_modules`, `__pycache__`, `dist`, `build`, and tool caches, and
  never follow symlinked directories. Those folders are still readable by direct path.
- Writes are atomic, keep the file's existing permissions (e.g. an executable script stays
  executable), and replace a symlink at the target rather than writing through it. There is no
  delete, rename, chmod, or execute — nothing in the bridge spawns a process.
- If the model is overloaded (503/429) **after** Gemini has written a file during the call, the
  bridge returns an error instead of re-running the call on the fallback model, so writes are
  never replayed.
- Every call, including rejections, is listed under **Tool calls** in the transcript entry.

**Artifacts** land in `artifacts_dir` (default `./gemini-artifacts/`) as
`YYYYMMDD-HHMM-<tool>-<topic-slug>.md`, with a header naming the tool, model, session, and time.
The reply ends with `[gemini-bridge] artifact saved: <path>`. If saving fails you still get the
answer, plus a `[gemini-bridge notice] artifact not saved: …` line. Artifacts are saved even when
`file_tools.enabled` is `false`.

---

## gemini_ask

**Persona:** Direct, precise technical assistant. No specialized persona — use when no other tool fits.

**System prompt:**
> You are a knowledgeable technical assistant working alongside Claude, another AI. Answer
> directly and precisely. Prefer concrete examples. When uncertain, say so.

**Parameters:**
| Parameter | Type | Required | Description |
|---|---|---|---|
| `prompt` | string | yes | The question or request |
| `thinking` | string | no | Reasoning depth |
| `session_name` | string | no | Session identifier (default: `"default"`) |
| `model` | string | no | `flash` / `flash-lite` / `pro` or a model id; omit for the server default (newest Flash) |

**When to use:**
- Direct questions with clear answers
- API lookups, syntax questions
- Anything that doesn't fit the specialized tools

**Example:**
```
gemini_ask(prompt="What's the difference between asyncio.gather and asyncio.wait?")
```

---

## gemini_brainstorm

**Persona:** Creative thinking partner. Challenges Claude's direction, pushes unconventional approaches, plays devil's advocate.

**System prompt:**
> You are a creative thinking partner working alongside Claude, another AI. Push unconventional
> approaches. Challenge Claude's existing direction. Play devil's advocate when useful. Offer
> alternatives even when the current path seems fine. Be concise.

**Parameters:**
| Parameter | Type | Required | Description |
|---|---|---|---|
| `topic` | string | yes | The topic or problem to brainstorm about |
| `context` | string | no | What Claude is currently doing or has considered |
| `thinking` | string | no | Reasoning depth |
| `session_name` | string | no | Session identifier |
| `model` | string | no | `flash` / `flash-lite` / `pro` or a model id; omit for the server default (newest Flash) |
| `write_artifact` | bool | no | Save the ideas to `artifacts_dir` (default `false`) |

**When to use:**
- Design decisions where you want a second take
- Stuck on an approach and need alternatives
- Validating a plan by stress-testing it

**Example:**
```
gemini_brainstorm(
    topic="How should we structure retry logic for the Pub/Sub consumer?",
    context="Currently planning exponential backoff with a dead-letter queue"
)
```

---

## gemini_review

**Persona:** Critical technical reviewer. Finds problems, prioritizes by severity, doesn't soften feedback.

**System prompt:**
> You are a critical technical reviewer working alongside Claude, another AI. Find problems,
> risks, and weaknesses in code, designs, and plans. Be direct. Don't soften feedback.
> Prioritize by severity. If something is sound, say so briefly and move on.

**Parameters:**
| Parameter | Type | Required | Description |
|---|---|---|---|
| `content` | string | yes | Code, design, or plan to review |
| `question` | string | no | Specific question to focus the review |
| `thinking` | string | no | Reasoning depth |
| `session_name` | string | no | Session identifier |
| `model` | string | no | `flash` / `flash-lite` / `pro` or a model id; omit for the server default (newest Flash) |
| `write_artifact` | bool | no | Save the answer to `artifacts_dir` (default `true`) |

**When to use:**
- Code review before merging
- Design review for a proposed architecture
- Checking a plan for risks before executing

**Example:**
```
gemini_review(
    content=f"```python\n{code}\n```",
    question="Is there a race condition in the session cleanup logic?"
)
```

---

## gemini_debug

**Persona:** Systematic debugging assistant. Generates evidence-based root cause hypotheses and specific diagnostic steps.

**System prompt:**
> You are a systematic debugging assistant working alongside Claude, another AI. Generate
> root cause hypotheses from the evidence provided. Reason through failure modes. Suggest
> specific diagnostic steps. Don't guess without basis — reason from what's shown.

**Parameters:**
| Parameter | Type | Required | Description |
|---|---|---|---|
| `error` | string | yes | Error message, stack trace, or failure description |
| `context` | string | no | Relevant code, recent changes, environment details |
| `thinking` | string | no | Reasoning depth (use `high` for complex failures) |
| `session_name` | string | no | Session identifier |
| `model` | string | no | `flash` / `flash-lite` / `pro` or a model id; omit for the server default (newest Flash) |

**When to use:**
- Unexplained test failures
- Production errors with unclear root cause
- Intermittent bugs that are hard to reproduce

**Example:**
```
gemini_debug(
    error="ConnectionResetError: [Errno 54] Connection reset by peer",
    context="Happens only on the 3rd request to the Pub/Sub emulator, after a 30s idle period"
)
```

---

## gemini_architect

**Persona:** Software architecture advisor. Opinionated when a clearly better path exists, names tradeoffs explicitly when context-dependent.

**System prompt:**
> You are a software architecture advisor working alongside Claude, another AI. Evaluate
> system designs, suggest patterns, identify scalability and maintainability concerns.
> Be opinionated when a clearly better path exists. Name tradeoffs explicitly when the
> choice is genuinely context-dependent.

**Parameters:**
| Parameter | Type | Required | Description |
|---|---|---|---|
| `description` | string | yes | System design or architecture to evaluate |
| `question` | string | no | Specific architecture question or concern |
| `thinking` | string | no | Reasoning depth (use `high` for complex systems) |
| `session_name` | string | no | Session identifier |
| `model` | string | no | `flash` / `flash-lite` / `pro` or a model id; omit for the server default (newest Flash) |
| `write_artifact` | bool | no | Save the answer to `artifacts_dir` (default `true`) |

**When to use:**
- Choosing between architectural patterns
- Evaluating a design for scalability or maintainability concerns
- Getting an opinion on a technical direction before committing

**Example:**
```
gemini_architect(
    description="We're building a multi-tenant SaaS on GCP. Each tenant gets their own Pub/Sub topic...",
    question="Is per-tenant topic isolation worth the operational overhead at 1000 tenants?"
)
```

---

## gemini_list_models

**Purpose:** Discovery utility — lists the Gemini models available on the bridge's active
backend, so Claude (or you) can pick a valid value for the `model=` parameter. This is a
metadata call, not an inference: it has no `thinking` or `session_name` parameter and is not
written to the transcript.

**Parameters:**
| Parameter | Type | Required | Description |
|---|---|---|---|
| `refresh` | bool | no | Re-fetch the live list, bypassing the per-process cache (default `false`) |

**Behavior:**
- Fetches the live catalog via the backend's `models.list`, then keeps only models the bridge
  can actually run — an **allowlist**: recognized Gemini chat generations (`gemini-2*` /
  `gemini-3*`, dotted or hyphenated) plus `-latest` aliases. Everything else is dropped:
  - media/specialized variants by name marker — `image`, `tts`, `audio`, `embedding`, `live`,
    `computer-use`, `robotics`, `omni` (several of these report `generateContent` too, so a
    name check is required — the action alone is not sufficient);
  - non-Gemini families the bridge can't run — Gemma, Lyria, Nano-Banana, Antigravity,
    Deep Research, etc.
  - **Previews are kept** (e.g. `gemini-3-pro-preview`) — they are valid, usable models.
- Renders a compact table: model id, display name, and `(default)` / `(alias)` markers, headed
  by the active backend name. The default is listed first.
- Caches the result for the process lifetime; `refresh=true` forces a re-fetch.
- **Graceful degradation:** if the live catalog can't be fetched, returns the curated static
  shortlist (from `models.py`) with a clear "live list unavailable" notice instead of failing.

> **Note:** the list is a *discovery aid*, not a hard whitelist — an explicitly-requested valid
> model still runs even if it isn't listed (e.g. a specialized `gemini-2*`/`gemini-3*` variant).

**Example:**
```
gemini_list_models()
gemini_list_models(refresh=True)   # force a fresh fetch
```

**Sample output** (api_key / Developer API backend; abridged — your live list reflects the
current catalog):
```
Gemini chat models on Developer API (Google AI Studio) (17 available):

  gemini-3.8-flash                    (default, latest flash)  — Gemini 3.8 Flash
  gemini-3.1-pro-preview              (latest pro)  — Gemini 3.1 Pro Preview
  gemini-3.5-flash                    — Gemini 3.5 Flash
  gemini-3.5-flash-lite               (latest flash-lite)  — Gemini 3.5 Flash Lite
  gemini-flash-latest                 (alias)  — Gemini Flash Latest
  gemini-pro-latest                   (alias)  — Gemini Pro Latest
  …

Pass model='<id>' to any tool. Omit to use the default (gemini-3.8-flash).
Or pass flash / flash-lite / pro to get the newest release of that family.
```
On a Vertex backend the header names Vertex AI and the `-latest` aliases are absent.

**How it resolves:**

```mermaid
flowchart TD
    A["gemini_list_models(refresh?)"] --> B{cached and not refresh?}
    B -->|yes| C[return cached table]
    B -->|no| D["client.list_models → backend models.list"]
    D --> E{fetch ok?}
    E -->|yes| F["allowlist: keep gemini-2*/gemini-3* + -latest<br/>drop media/specialized + non-Gemini families"]
    F --> G[render table<br/>default first, mark default/alias]
    G --> H[cache + return]
    E -->|no| I[return curated static shortlist<br/>+ 'live unavailable' notice<br/>not cached]
```

See [configuration.md](configuration.md#choosing-a-model) for the recommended models per backend.
