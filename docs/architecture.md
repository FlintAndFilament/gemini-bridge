# Architecture

## Component Diagram

```mermaid
graph TD
    M["__main__.py"] --> CFG["load_config()<br/>config.py"]
    M --> AUTH["build_auth()<br/>auth.py"]
    M --> CL["GeminiClient(config, creds)<br/>client.py"]
    M --> TR["TranscriptWriter(dir, time)<br/>transcript.py"]
    M --> SRV["build_server(client, transcript)<br/>server.py"]

    CFG --> JSON["~/.config/gemini-bridge/config.json"]
    AUTH --> CRED["google.auth Credentials<br/>or API key"]

    SRV --> MCP["FastMCP + 6 registered tools"]
    MCP --> T1["gemini_ask"]
    MCP --> T2["gemini_brainstorm"]
    MCP --> T3["gemini_review"]
    MCP --> T4["gemini_debug"]
    MCP --> T5["gemini_architect"]
    MCP --> T6["gemini_list_models"]

    T1 & T2 & T3 & T4 & T5 --> BASE["tools/base.py<br/>call_gemini() + model_param_hint()"]
    BASE --> CL
    T6 --> CL
    BASE & T6 --> MOD["models.py<br/>shortlist / schema_hint / is_chat_capable"]
```

The inference tools route through `tools/base.py`; `gemini_list_models` calls the client
directly. Both consult `models.py` — the single source of truth for the model taxonomy
(backend shortlists, the backend-aware schema hint, and the chat-capable filter).

## Data Flow (per inference tool call)

1. Claude Code sends an MCP tool call (e.g. `gemini_brainstorm`) with an optional `model=`
2. `server.py` routes to the registered tool function in `tools/brainstorm.py`
3. Tool calls `call_gemini()` from `tools/base.py` with the session name, system prompt, and prompt
4. `call_gemini()` asks `client.resolve_model()` for the concrete model — the per-call `model`,
   else `default_model`, else the newest Flash; aliases (`flash` / `flash-lite` / `pro`,
   `*-latest`) map to the newest release resolved at startup — and calls
   `client.get_or_create_session()`, one session per `tool:session_name:concrete-model`
5. `client.ask()` builds a `GenerateContentConfig` with the thinking parameter for that model's
   generation (adapting if the model rejects a level) and sends to the active backend
6. On a terminal 503/429, `call_gemini()` retries once on the newest Flash-Lite
   (`client.fallback_model`) and prepends a disclosure notice to the response
7. `transcript.append()` writes the exchange to the Markdown file
8. Tool returns the response string as the MCP tool result to Claude Code

```mermaid
sequenceDiagram
    participant CC as Claude Code
    participant T as Tool module
    participant B as base.call_gemini()
    participant C as client.py<br/>GeminiClient
    participant G as Gemini backend

    CC->>T: tool call (prompt, thinking?, model?)
    T->>B: call_gemini(..., model)
    B->>C: get_or_create_session(tool:name:model)
    B->>C: ask(prompt, thinking, model)
    C->>G: send_message (with GenerateContentConfig)
    alt 503/429 after retries and model != fallback model
        G-->>C: terminal error
        B->>C: ask(prompt, thinking, fallback model)
        C->>G: retry on the newest Flash-Lite
        G-->>C: response
        B-->>T: notice + response
    else success
        G-->>C: response
        B-->>T: response
    end
    B->>B: transcript.append()
    T-->>CC: response text
```

## Session Lifecycle

- **Created:** on first call to any tool, keyed by `tool_name:session_name`
- **Persists:** for the lifetime of the MCP server process (one Claude Code session)
- **Accumulates context:** Gemini's `Chat` object retains message history automatically
- **Destroyed:** when Claude Code restarts (new server process, new sessions)

Each tool uses its own session key (`gemini_ask:default`, `gemini_brainstorm:default`, etc.)
so the system prompt persona is locked for the session.

## Transcript Lifecycle

- **File created:** at server startup, named with the startup timestamp
- **File path:** `{transcript_dir}/YYYYMMDD-HHMM-gemini-bridge-transcript.md`
- **Appended:** after every successful Gemini response
- **Never truncated:** append-only; write errors go to stderr, never break tool calls
- **Per session:** one file per Claude Code process lifetime

## SOLID Notes

| Principle | How it's enforced |
|---|---|
| SRP | Each module owns exactly one concern |
| OCP | New tools: add file + one line in server.py. New auth: add loader + one branch |
| LSP | All auth paths return `Credentials`; all tools return `ToolResult` |
| ISP | Tools receive only `GeminiClient` — not server or config objects |
| DIP | `client.py` depends on `Credentials` abstraction; `server.py` depends on callables |
