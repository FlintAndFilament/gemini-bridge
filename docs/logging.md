# Logging

gemini-bridge writes structured logs to a daily rotating file. Claude Code swallows MCP
server stderr — the file is the only way to see what the server is doing.

## Log file location

```
~/.config/gemini-bridge/logs/YYYYMMDD-gemini-bridge.log
```

- One file per calendar day
- Multiple Claude Code sessions on the same day append to the same file
- Startup entries appear when Claude Code launches (fresh `claude` invocation, not `/resume`)
- Four most recent files retained; older files pruned at startup

## Tail live

```bash
tail -f ~/.config/gemini-bridge/logs/$(ls -t ~/.config/gemini-bridge/logs/*.log | head -1 | xargs basename)
```

## Log levels

Set via `GEMINI_BRIDGE_LOG_LEVEL` environment variable before starting Claude Code.
Default: `INFO`.

| Level | What you see | When to use |
|---|---|---|
| `INFO` | Startup: auth method, model, location, transcript path | Default — daily use |
| `WARNING` | Transcript write failures; empty Gemini responses | Included at INFO |
| `ERROR` | Auth failures (with method/source); inference failures (with tool + session) | Included at INFO |
| `DEBUG` | Per-call: tool name, session, thinking level, prompt/response length | Troubleshooting |

## Example log output

**Normal startup (INFO):**
```
[gemini-bridge] 17:50:10 INFO     gemini_bridge.__main__: starting — auth=keychain location=global default_thinking=medium default_model=gemini-3.8-flash fallback_model=gemini-3.5-flash-lite
[gemini-bridge] 17:50:10 INFO     gemini_bridge.__main__: transcript → ~/session-summaries/20260702-1750-gemini-bridge-transcript.md
```

**Auth failure (ERROR):**
```
[gemini-bridge] 17:50:10 ERROR    gemini_bridge.auth: keychain item not found (service='gemini-bridge-mcp', account='bg-gemini-mcp-svc')
```

**Debug mode (DEBUG):**
```
[gemini-bridge] 17:50:15 DEBUG    gemini_bridge.tools.base: gemini_brainstorm session='default' thinking=medium
[gemini-bridge] 17:50:17 DEBUG    gemini_bridge.client: ask: thinking=medium prompt_len=142
[gemini-bridge] 17:50:19 DEBUG    gemini_bridge.client: response_len=847
[gemini-bridge] 17:50:19 DEBUG    gemini_bridge.tools.base: gemini_brainstorm session='default' OK
```

## Enabling debug mode

Add to your shell before launching Claude Code:
```bash
export GEMINI_BRIDGE_LOG_LEVEL=DEBUG
claude
```

## Notes

- stdout is the MCP JSON-RPC protocol channel — any non-protocol byte on stdout silently
  corrupts the stream. All logging goes to stderr and the file, never stdout.
- The log file and transcript file are separate: the log captures server events; the transcript
  captures Gemini exchange content (prompts and responses).
