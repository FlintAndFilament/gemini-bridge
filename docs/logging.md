# Logging

gemini-bridge writes structured logs to a daily file. Claude Code swallows MCP server
stderr — the file is the only way to see what the server is doing.

## Log file location

```
~/.config/gemini-bridge/logs/YYYYMMDD-gemini-bridge.log
```

- One file per calendar day, named by the day the server started
- Multiple Claude Code sessions on the same day append to the same file
- Startup entries appear each time the server process starts (when Claude Code launches it)
- At startup, all but the four newest log files are deleted, then today's file is opened — so
  there are four or five files at any time
- The same lines also go to stderr, which is useful only when running the server by hand
- Only the bridge's own loggers (`gemini_bridge.*`) are written; third-party library logs are not

## Tail live

```bash
tail -f ~/.config/gemini-bridge/logs/$(ls -t ~/.config/gemini-bridge/logs/*.log | head -1 | xargs basename)
```

## Log levels

Set via `GEMINI_BRIDGE_LOG_LEVEL` environment variable before starting Claude Code
(`DEBUG`, `INFO`, `WARNING`, `ERROR`; case-insensitive). Default: `INFO`; an unrecognized value
also means `INFO`.

| Level | What you see | When to use |
|---|---|---|
| `INFO` | Startup: backend, default and fallback model, resolved latest models, transcript path, file-tools state with sandbox root and artifacts directory. Also a file tool that raised, and a grounding redirect that did not resolve | Default — daily use |
| `WARNING` | Retries on 503/429 and falling back to the fallback model; thinking-level adjustments; web access requested on Vertex; sandbox rejections; `grep` timeouts; tool-loop round cap; malformed function calls; empty Gemini responses; latest-model resolution failures; transcript write failures | Included at INFO |
| `ERROR` | Startup failures (config, auth with method and source, `artifacts_dir`); inference failures (with tool + session); fallback also failed; overload after a write (not retried); artifact not saved; `models.list` failures | Included at INFO |
| `DEBUG` | Per call: tool, session, model, thinking level; request model, prompt length, number of file tools, web on/off; response length; credential loading; session-cache evictions | Troubleshooting |

## Example log output

**Normal startup (INFO), api_key backend:**
```
[gemini-bridge] 23:28:31 INFO     gemini_bridge.__main__: starting — auth=api_key default_thinking=medium default_model=gemini-3.8-flash fallback_model=gemini-3.5-flash-lite
[gemini-bridge] 23:28:31 INFO     gemini_bridge.__main__: latest models: flash=gemini-3.8-flash, pro=gemini-3.1-pro-preview, flash-lite=gemini-3.5-flash-lite
[gemini-bridge] 23:28:31 INFO     gemini_bridge.__main__: transcript → /Users/you/dev/my-project/session-summaries/20260921-2328-gemini-bridge-transcript.md
[gemini-bridge] 23:28:31 INFO     gemini_bridge.__main__: file tools enabled — sandbox root /Users/you/dev/my-project — artifacts → /Users/you/dev/my-project/gemini-artifacts
```

On a Vertex backend the first line reads `auth=<method> location=<location>` instead (e.g.
`auth=keychain location=global`). If the model catalog can't be read, the second line reads
`latest models: unresolved (pinned)`. With file tools off, the last line reads
`file tools DISABLED (<reason>)`.

**Auth failure (ERROR):**
```
[gemini-bridge] 17:50:10 ERROR    gemini_bridge.auth: keychain item not found (service='gemini-bridge', account='vertex-sa')
[gemini-bridge] 17:50:10 ERROR    gemini_bridge.__main__: startup failed — auth error: …
```

**Debug mode (DEBUG):**
```
[gemini-bridge] 17:50:15 DEBUG    gemini_bridge.tools.base: gemini_brainstorm session='default' model=default thinking=medium
[gemini-bridge] 17:50:15 DEBUG    gemini_bridge.client: ask: model=gemini-3.8-flash thinking=medium prompt_len=142 tools=5 web=False
[gemini-bridge] 17:50:19 DEBUG    gemini_bridge.client: response_len=847
[gemini-bridge] 17:50:19 DEBUG    gemini_bridge.tools.base: gemini_brainstorm session='default' OK
```

`model=default` on the first line means the call omitted `model`; the `ask:` line shows the
concrete model it resolved to. `tools=` counts the file tools offered to Gemini (4 read-only,
5 with `write_file`, 0 when file tools are off).

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
  captures Gemini exchange content (prompts, tool calls, responses). See
  [transcripts.md](transcripts.md).
