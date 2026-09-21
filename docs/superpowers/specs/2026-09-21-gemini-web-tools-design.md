# Gemini Web Tools — Design Spec

**Issue:** #76 · **Date:** 2026-09-21 · **Branch:** `feat/gemini-web-tools` · **Status:** approved, building
**Supersedes:** #70 (bridge as MCP client) — closed as not planned; the API provides this natively.
**Builds on:** #68 (tool loop, file tools), #74 (capability metadata).

## 1. Goal

Let Gemini search the web and fetch URLs during a call, using the Gemini API's own server-side tools, so its answers reflect current reality rather than its training cutoff.

**Mental model, and how it differs from #68.** With file tools, Gemini *requests* a call and the bridge executes it locally — the bridge is the enforcement point. With web tools, **the API executes them server-side**; the bridge only decides whether to attach them. There is no handler to write and no sandbox to enforce, which is why this is far smaller than #70 assumed. What the bridge does own is the *consequence*: attacker-controlled text now enters a call that may also hold `write_file`.

## 2. Decisions (locked)

| # | Decision |
|---|---|
| D1 | Use the API's built-in `google_search` and `url_context`. No MCP client, no external CLI, no new dependency. |
| D2 | Both tools ride **one** flag. Splitting them would mean two switches for one trust decision. |
| D3 | All five generating tools may use web access. No per-use-case or per-persona restriction. |
| D4 | **Web access and `write_file` are mutually exclusive within a call.** If web is on, `write_file` is not offered to Gemini at all. |
| D5 | `web_tools.enabled` config sets the default; shipped **`false`**. Every tool takes `web: bool \| None = None`, where `None` resolves to the config default — the same tri-state as `thinking`. |
| D6 | `tool_config.include_server_side_tool_invocations=True` whenever web tools are attached — Developer API only (D11). Without it the API rejects built-in tools alongside our `function_declarations` with a 400. |
| D7 | One function, `resolve_capabilities()`, owns D4. Both the runtime and the #74 capability metadata call it, so the advertised text cannot disagree with what the call actually does. |
| D8 | Search queries and grounding sources are recorded in the transcript beside file-tool calls. |
| D9 | Not a `breaking-change`: tools only gain an optional parameter, and the default is off. |
| D10 | Server-side tool parts are stripped from the turn committed to session history, so retrieved page text does not outlive the call that fetched it. Without this, D4 was defeatable across two calls in one session (code review). |
| D11 | Web access is unavailable on Vertex; it is dropped with a visible notice rather than failing the call. |

### Why D4, stated plainly

A web page is attacker-controlled text. Once it is in the context of a call that can write to the repository, "write this to `setup.sh`" is a plausible instruction for the model to follow. The #68 sandbox blocks `.git`, `.env` and key files, but a CI workflow, a test file or a shell script inside the repo are all writable and all consequential.

The cost of exclusion is low because **the bridge writes artifacts itself** (D4 of the #68 spec). A web-enabled `gemini_review` still produces its artifact; what it loses is Gemini writing directly into the tree during that same call. The workflow when both are wanted is two calls: one web-enabled to research, one web-free to write.

## 3. Architecture

### New module

| Module | Responsibility | Depends on |
|---|---|---|
| `web_tools.py` | `WebToolsConfig` resolution and `web_tool_set() -> list[types.Tool]`; `resolve_capabilities(write, web_requested, default) -> Capabilities(web, write)` implementing D4. Pure policy and construction — no I/O, no handlers. | `google-genai` types |

`web_tools.py` is deliberately thin. There is no `WebTools` class mirroring `FileTools`, because `FileTools` exists to *execute* calls and here the API executes them. A class with no handlers would be ceremony.

### Modified modules

| Module | Change |
|---|---|
| `config.py` | `WebToolsConfig(enabled: bool = False)`; `Config.web_tools`. |
| `client.py` | `build_config(..., web: bool = False)` attaches the web tools and sets `include_server_side_tool_invocations`. |
| `tools/base.py` | `call_gemini(..., web)` resolves capabilities once, passes the resulting `write` to `workspace.registry()`, and extends `capability_hint()` with the web row. |
| `tools/{ask,debug,brainstorm,review,architect}.py` | The `web` parameter; `_WEB` is not needed — every tool may use it (D3). |
| `server.py` | Server instructions gain the web row, including the exclusion rule. |
| `transcript.py` / `tool_loop.py` | Record grounding queries and sources (D8). |

### What does *not* change

`tool_loop` already dispatches only on `p.function_call` (`tool_loop.py:166`), so the server-side `tool_call` / `tool_response` parts are ignored for dispatch — no loop change is needed to stay safe. Text extraction already filters `p.text and not p.thought`, which remains correct.

What *did* need changing: those parts were being committed to session history along with the answer, because the API returns them inside the same content object. `_answer_only()` now strips them (D10). Text parts keep their `thought_signature`.

## 4. Data flow

```
gemini_review(content=..., web=true)
  │
  ├─ resolve_capabilities(write=True, web_requested=True, default=cfg)
  │     → Capabilities(web=True, write=False)          ← D4 applied once, here
  │
  ├─ workspace.registry(write=False)                   → read tools only
  ├─ capability_hint(..., web=True, write=False)       → description says both facts
  └─ client.build_config(..., declarations, web=True)
        → tools = [Tool(google_search), Tool(url_context), Tool(function_declarations)]
        → tool_config.include_server_side_tool_invocations = True
```

## 5. Verified behavior (live probe, 2026-09-20, Developer API)

| Combination | Result |
|---|---|
| `google_search` alone | OK |
| `url_context` alone | OK |
| `function_declarations` alone | OK |
| built-in + functions, no flag | **400 INVALID_ARGUMENT** — "Please enable tool_config.include_server_side_tool_invocations" |
| built-in + functions, flag set | OK on `gemini-3.8-flash`, `gemini-3.1-pro-preview`, `gemini-3.5-flash-lite`; `grounding_metadata` populated |

Response parts when a server-side tool runs:

```
part[0] ['thought_signature', 'tool_call']
part[1] ['thought_signature', 'tool_response']
part[2] ['text', 'thought_signature']

grounding_metadata.web_search_queries → ['latest python stable release version']
grounding_metadata.grounding_chunks   → 2
```

## 6. Testing

| Area | Test |
|---|---|
| D4 invariant | `resolve_capabilities` never returns `web=True, write=True`, exhaustively over the input space. |
| D4 end to end | A web-enabled call to each write-capable tool declares no `write_file` to the API. |
| Tri-state | `web=None` follows config; `web=True/False` overrides it either way. |
| Config flag | `include_server_side_tool_invocations` is set when and only when web tools are attached. |
| Metadata (#74) | The description and server instructions state the web row and the exclusion, in both config states, cross-checked against what the call actually declares. |
| Regression | With `web` unset and `enabled: false`, the request is byte-identical to today's. |

## 7. Out of scope

- `exa_ai_search`, `mcp_servers`, `code_execution`, `file_search` — available on the same `types.Tool`, deliberately not enabled here. One trust decision at a time.
- Per-request spend caps. The API governs server-side invocation counts; we do not control them.
- **Vertex is not supported — now known, not assumed.** Code review found that `google-genai` raises `ValueError` converting `include_server_side_tool_invocations` for Vertex; the flag is Developer-API only. The bridge gates on `client.web_supported`, drops web access there, and says so in the reply and in both metadata channels. Enabling it needs an upstream change, not a test on the work laptop.
