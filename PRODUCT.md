# Hermes Session Lens

<!-- impeccable:product-schema 1 -->

## Platform

adaptive

## Stack

Hermes unified plugin package: a plain JavaScript ESM desktop plugin using `@hermes/plugin-sdk`, plus a Python/FastAPI backend using Hermes-bundled dependencies for read-only session inspection and direct provider usage checks.

## Users

Hermes Desktop users who need to understand provider allowances plus the activity, token consumption, cost, tools, skills, failures, files, and delegated work behind each agent session without leaving the desktop app.

## Product Purpose

Make Hermes activity and account capacity explainable at a glance and inspectable in depth. Success means a user can check what remains on configured AI accounts, find a session, understand what it consumed and did, trace the recorded evidence, and inspect surrounding runtime and orchestration health from one native Hermes page.

## Positioning

Session Lens combines broad token and cost telemetry with failure-first, session-level operational inspection in a native Hermes sidebar page. It uses Hermes' own accounting and provenance fields, and labels unavailable pricing as unpriced instead of presenting a false zero.

## Operating Context

The plugin appears in Hermes Desktop's left sidebar as **Session Lens** and opens a full native route. It reads local Hermes state, runtime logs, gateway status, schedule metadata, and Kanban stores in read-only mode; it supports dark and light Hermes themes and must remain usable across routine Hermes application updates.

## Capabilities and Constraints

- Native route `/session-lens` and `SIDEBAR_NAV_AREA` contribution; no iframe or standalone web server.
- Session list and detail views for tokens, cost, model, provider, tools, skills, failures, files, and delegations.
- Overview, Operations, Tools, Skills, System, and AI Usage views.
- Current Codex, Anthropic Claude, Nous Portal, OpenRouter, and DeepSeek account allowances or balances through Hermes-resolved credentials.
- Grok, Kimi Code Plan, and Z.AI GLM Coding Plan quota adapters are explicitly experimental and visually separated from supported sources.
- Chronological, paginated session trace for active user, assistant, reasoning, tool-call, and tool-result rows; system prompts excluded.
- Conservative session outcomes that preserve Hermes' recorded end reason.
- Cached local-log telemetry for latency, cache efficiency, and tool duration.
- Cross-profile accounting, gateway/platform health, prompt-safe schedules, and shared Kanban execution status.
- Failure-first sorting, a dedicated failed-call inspector, Hermes FTS search with snippets, and cursor-style pagination up to 500 rows.
- An Ask Lens workflow that produces a privacy-conscious, session-grounded prompt for analysis in Hermes.
- Read-only database access. The plugin must not mutate, archive, delete, or rewrite sessions.
- Cost precedence: recorded actual cost, then recorded estimate, then a clearly labelled included or unpriced state.
- Skills must distinguish recorded invocation from merely available capabilities.
- Local-first operation with no third-party telemetry upload. AI Usage makes direct authenticated quota requests only to the configured providers and never returns credentials to JavaScript.
- Operational readers load only when their view is opened and never poll faster than every 30 seconds.

## Brand Commitments

- Product name: **Hermes Session Lens**.
- Sidebar label: **Session Lens**.
- Repository name: `hermes-session-lens`.
- Plugin id: `session-lens`.
- The interface inherits Hermes Desktop's native components, typography, spacing, and theme variables.
- Upstream inspiration is credited transparently; no upstream logo or endorsement is implied.

## Evidence on Hand

- Hermes Agent v0.20.5 database schema v26, verified on Windows on 2026-08-23.
- Installed Hermes Desktop Plugin SDK and bundled `hermes-desktop-plugins` instructions.
- Hermes `sessions`, `messages`, `session_model_usage`, FTS, and `async_delegations` tables.
- TokenTelemetry and Hermes Session Analyzer are product-behavior references; Hermes LLM Quota Monitor informs the experimental Grok adapter. Compatibility claims remain bounded to verified Hermes versions and provider responses.

## Product Principles

- Evidence before estimates.
- Failures should be easier to find than successes are to admire.
- Dense information remains scan-friendly through progressive disclosure.
- Privacy is the default: local reads, bounded snippets, no third-party telemetry, and direct provider quota checks that never expose credentials to the Desktop UI.
- The plugin should feel maintained by Hermes, while remaining independently installable.

## Accessibility & Inclusion

Keyboard-reachable controls, visible focus states, semantic buttons and tables, text alternatives for status color, reduced-motion-safe transitions, and layouts that remain usable in narrow Hermes windows.
