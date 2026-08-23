# Hermes Session Lens

<!-- impeccable:product-schema 1 -->

## Platform

adaptive

## Stack

Hermes unified plugin package: a plain JavaScript ESM desktop plugin using `@hermes/plugin-sdk`, plus a dependency-free Python/FastAPI backend that reads Hermes' SQLite session store in read-only mode.

## Users

Hermes Desktop users who need to understand the activity, token consumption, cost, tools, skills, failures, files, and delegated work behind each agent session without leaving the desktop app.

## Product Purpose

Make session telemetry explainable at a glance and inspectable in depth. Success means a user can find a session, understand what it consumed and did, identify failures, and trace the recorded evidence from one native Hermes page.

## Positioning

Session Lens combines broad token and cost telemetry with failure-first, session-level operational inspection in a native Hermes sidebar page. It uses Hermes' own accounting and provenance fields, and labels unavailable pricing as unpriced instead of presenting a false zero.

## Operating Context

The plugin appears in Hermes Desktop's left sidebar as **Session Lens** and opens a full native route. It reads the current profile's local Hermes `state.db`, supports dark and light Hermes themes, and must remain usable across routine Hermes application updates.

## Capabilities and Constraints

- Native route `/session-lens` and `SIDEBAR_NAV_AREA` contribution; no iframe or standalone web server.
- Session list and detail views for tokens, cost, model, provider, tools, skills, failures, files, and delegations.
- Overview, Tools, Skills, and System aggregate views.
- Failure-first sorting, a dedicated failed-call inspector, Hermes FTS search with snippets, and cursor-style pagination up to 500 rows.
- An Ask Lens workflow that produces a privacy-conscious, session-grounded prompt for analysis in Hermes.
- Read-only database access. The plugin must not mutate, archive, delete, or rewrite sessions.
- Cost precedence: recorded actual cost, then recorded estimate, then a clearly labelled included or unpriced state.
- Skills must distinguish recorded invocation from merely available capabilities.
- Local-first operation with no telemetry upload.

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
- TokenTelemetry and Hermes Session Analyzer are product-behavior references; this implementation is original and should not fabricate compatibility claims beyond verified Hermes versions.

## Product Principles

- Evidence before estimates.
- Failures should be easier to find than successes are to admire.
- Dense information remains scan-friendly through progressive disclosure.
- Privacy is the default: local reads, bounded snippets, and no external telemetry.
- The plugin should feel maintained by Hermes, while remaining independently installable.

## Accessibility & Inclusion

Keyboard-reachable controls, visible focus states, semantic buttons and tables, text alternatives for status color, reduced-motion-safe transitions, and layouts that remain usable in narrow Hermes windows.
