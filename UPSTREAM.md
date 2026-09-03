# Upstream and inspiration

Hermes Session Lens is an original implementation for the native Hermes Desktop Plugin SDK.

Its product behavior was informed by:

- [TokenTelemetry](https://github.com/VasiHemanth/tokentelemetry), by Hemanth Vasi, MIT licensed — broad token, cost, model, and tool telemetry.
- [Hermes Session Analyzer](https://github.com/tomatyss/session-analyzer), by Tom Mulkins, MIT licensed — native session inspection, failure-first workflows, file summaries, FTS search, pagination, and Ask AI behavior.
- [Hermes Agent](https://github.com/NousResearch/hermes-agent), by Nous Research and contributors, MIT licensed — plugin API, database schema, UI components, accounting semantics, and skills telemetry conventions.
- [Hermes LLM Quota Monitor](https://github.com/bnogalski/hermes-llm-quota), by Bartosz Nogalski, MIT licensed — behavior and response-shape reference for the Grok OAuth billing adapter.
- [CodexBar](https://github.com/steipete/CodexBar), by Peter Steinberger and contributors, MIT licensed — Kimi Code Plan response-shape and rolling-window behavior reference.
- [Agentic Usage Meter](https://github.com/prime-radiant-inc/agentic-usage-meter), by Prime Radiant — provider-qualification reference for the Kimi and Z.AI quota surfaces.
- [Hermes ResetWatch](https://github.com/Adolanium/hermes-resetwatch), by Adolanium, MIT licensed — behavior reference for per-account Claude quota cards (walking pooled logins and rendering one card per account).

No upstream logo is reused, and this project does not imply endorsement by any upstream author or Nous Research. Source code should carry file-level notices if a future change copies or substantially adapts an upstream implementation.

## Requests worth raising upstream

- **Hermes Agent: record the last `anthropic-ratelimit-unified-*` and `anthropic-ratelimit-*` response headers per Anthropic credential.** Hermes receives these on every Claude call it makes (its adapter already uses them as a billing-lane oracle in its own diagnostics). If it persisted the latest values — even only in memory, exposed through `agent.account_usage` — Session Lens could read subscription and API-key allowances with zero side effects and retire its one-token probe. Until then the probe is the only readable source for setup tokens and API keys, since `/api/oauth/usage` requires the `user:profile` scope those credentials do not carry.
