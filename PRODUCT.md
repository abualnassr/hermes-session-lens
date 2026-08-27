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

The plugin appears in Hermes Desktop's left sidebar as **Session Lens** and opens a full native route. It reads local Hermes state, runtime logs, gateway status, and schedule metadata in read-only mode; it supports dark and light Hermes themes and must remain usable across routine Hermes application updates.

## Capabilities and Constraints

- Native route `/session-lens` and `SIDEBAR_NAV_AREA` contribution; no iframe or standalone web server.
- Session list and detail views for tokens, cost, model, provider, tools, skills, failures, files, and delegations.
- Overview, Operations, Tools, Skills, System, AI Usage, and AI Models views.
- An all-time model inventory discovered dynamically from distinct session accounting records, with selected-period requests, token mix, cost, quota burn, reliability, retry/switch, latency, and seven-day trend evidence plus expandable task-type diagnostics. Ten columns are sortable and default to total tokens descending.
- Current Codex, Anthropic Claude, Nous Portal, OpenRouter, DeepSeek, Grok, Kimi Code Plan, and Z.AI GLM Coding Plan account allowances or balances through Hermes-resolved credentials.
- Chronological, paginated session trace for active user, assistant, reasoning, tool-call, and tool-result rows; system prompts excluded.
- Conservative session outcomes that preserve Hermes' recorded end reason.
- Cached local-log telemetry for latency, cache efficiency, request-failure observations, and tool duration; time-to-first-token remains explicitly unavailable because Hermes does not record it.
- Cross-profile accounting, gateway/platform health, and prompt-safe schedules.
- Failure-first sorting, a dedicated failed-call inspector, Hermes FTS search with snippets, and cursor-style pagination up to 500 rows.
- An Ask Lens workflow that produces a privacy-conscious, session-grounded prompt for analysis in Hermes.
- Read-only database access. The plugin must not mutate, archive, delete, or rewrite sessions.
- Cost precedence: recorded actual cost, then recorded estimate, then a clearly labelled included or unpriced state.
- AI Models treats OAuth quota as a shared provider-account limit, delays pace judgments until 10% of the billing period has elapsed, requires ten valid accepted tasks before showing per-task efficiency, distinguishes unavailable cache reporting from a recorded zero, separates bounded-log API-attempt failures from recorded tool-call failures, and does not estimate unavailable TTFT. Expanded work reliability measures main-role completed, clean, recovered, and terminally failed model/API task outcomes; ambiguous or uncovered evidence never becomes success. Comparable models require the configurable sample floor and rank by the lowest 95% Wilson upper failure bound. Acceptance is task-specific, and retry/switch excludes cross-role model routing. Rate metrics expose sample sizes, neutralize and demote samples below a configurable confidence floor, and suppress bounded-log failure/latency values on zero-request rows. Unknown routes resolve through explicit model-id globs and distinct historical model/family routes before becoming actionable unmapped states.
- Skills must distinguish recorded invocation from merely available capabilities.
- Local-first operation with no third-party telemetry upload. AI Usage makes direct authenticated quota requests only to the configured providers and never returns credentials to JavaScript.
- Operational readers load only when their view is opened and never poll faster than every 30 seconds.

## Brand Commitments

- Product name: **Hermes Session Lens**.
- Sidebar label: **Session Lens**.
- Repository name: `hermes-session-lens`.
- Plugin id: `session-lens`.
- Current documented release: `0.15.0`.
- The interface inherits Hermes Desktop's native components, typography, spacing, and theme variables.
- Upstream inspiration is credited transparently; no upstream logo or endorsement is implied.

## Evidence on Hand

- Hermes Agent v0.20.5 database schema v26, verified on Windows on 2026-08-23.
- Installed Hermes Desktop Plugin SDK and bundled `hermes-desktop-plugins` instructions.
- Hermes `sessions`, `messages`, `session_model_usage`, FTS, and `async_delegations` tables.
- TokenTelemetry and Hermes Session Analyzer are product-behavior references; Hermes LLM Quota Monitor informs the Grok OAuth adapter. Compatibility claims remain bounded to verified Hermes versions and provider responses.

## Product Principles

- Evidence before estimates.
- Failures should be easier to find than successes are to admire.
- Dense information remains scan-friendly through progressive disclosure.
- Privacy is the default: local reads, bounded snippets, no third-party telemetry, and direct provider quota checks that never expose credentials to the Desktop UI.
- The plugin should feel maintained by Hermes, while remaining independently installable.

## Accessibility & Inclusion

Keyboard-reachable controls, visible focus states, semantic buttons and tables, text alternatives for status color, reduced-motion-safe transitions, and layouts that remain usable in narrow Hermes windows.
