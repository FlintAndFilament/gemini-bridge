# gemini-bridge Documentation

| Document | Contents |
|---|---|
| [architecture.md](architecture.md) | Component diagram, module map, request flow of a generating call, session and transcript lifecycle |
| [auth.md](auth.md) | Setup steps for each auth method (ADC, env, Keychain, API key), troubleshooting, gcloud auth vs ADC |
| [configuration.md](configuration.md) | Full config.json field reference |
| [tools.md](tools.md) | All 7 tools (5 generating + `gemini_list_models` + `gemini_help`): system prompts, parameters, file and web access, examples, when to use each |
| [transcripts.md](transcripts.md) | Transcript format, session boundary behavior, location strategy |
| [logging.md](logging.md) | Log file location, levels, tail command, rotation, debug mode |
| [development.md](development.md) | Dev setup, the system-Python live install, tests, lint and types, CI workflows, adding a new tool, branching workflow |
| [roadmap.md](roadmap.md) | Features by release, with rationale and shipped status |

Design specs and implementation plans for larger features live in
[superpowers/specs/](superpowers/specs/) and [superpowers/plans/](superpowers/plans/).
