# Hermes Session Lens

Hermes Session Lens is a native observability page for Hermes Desktop. It appears in the left sidebar and explains what each session consumed and did, shows current provider allowances and balances, then connects that evidence to runtime health, profiles, and schedules.

It is a unified Hermes plugin—one install contains the native Desktop page and its namespaced Python API. There is no iframe, separate dashboard, Node server, or third-party telemetry service.

This documentation describes Hermes Session Lens `0.22.0`.

## What it includes

- A project rollup on Overview ("Where the spend goes"): sessions grouped by git repository, then working directory, then source for sessions with no recorded directory — sessions, tokens, recorded cost with unpriced counts, confirmed failures, top models, last activity.
- An agent run-health scoreboard on Operations → Schedules: cron sessions grouped per job by title, with a latest-runs strip (completed/failed/cancelled/running), failure counts, streaks, average duration and cost per run, and click-through to the job's sessions.
- A context-compression distress readout on Operations → Health: sessions with recorded fallback streaks, ineffective passes, compression failures, or active cooldowns, with the top offenders listed; a single quiet line when nothing is distressed.
- Profile provenance: `/health` names the profile whose records the backend serves, and the page header shows it as a "data: <profile> profile" chip. Hermes plugins and telemetry are per-profile, and the desktop can silently fall back to another profile's gateway when the active one is not running — the chip keeps the data source honest.
- A machine-consumable digest at `GET /api/plugins/session-lens/digest?days=7`: period totals with prior-period deltas (sessions, tokens, recorded cost, failure events, tool failures), the attention list, top models by requests with work-reliability evidence, over-pace quota windows with exhaustion forecasts, and a ready-made `markdown` field — built for cron agents, notification pipelines, or any automation that reads the plugin API. Read-only like every other route.
- Quota exhaustion forecasts: when a timed OAuth quota window is burning faster than its billing period elapses, both the AI Usage window card and the AI Models quota meter state when the window runs out at the current pace ("At this pace, empty ~Sat, Aug 29"). Forecasts are linear extrapolations, suppressed during the first 10% of a period, for on-pace windows, and for windows without a known duration.
- An attention banner above the session list flags runaway work: sessions open past 24 hours that are either still active (destructive severity) or idle with five million tokens or more, and reaped or expired sessions (`startup_orphan_reap`, `max_runtime`, `timeout`) at the same token threshold. Each flagged row jumps to that session in the list and can be dismissed individually or all at once (persisted locally, restorable); quiet histories render no banner.
- Failure-first session browser with full-text search snippets. SQL performs only a coarse candidate scan; the shared Python signature confirms tool-result text before list, detail, overview, tool, or AI Models metrics count it. Recorded Hermes finish/effect states remain authoritative.
- Recorded actual/estimated cost provenance, with **Included** and **Unpriced** states instead of a false `$0`.
- Input, output, cache-read, cache-write, reasoning, and per-model usage.
- Dedicated failed-call inspector with bounded, redacted result snippets.
- Tool volume, failure rate, last use, and per-session call evidence.
- Explicit skill invocation from recorded `skill_view` and `skill_manage` calls; available skills are not mislabelled as used.
- Files-observed summary from tool path arguments and bounded command-path extraction.
- Async delegation summaries.
- A chronological Trace tab for user, assistant, reasoning, tool-call, and tool-result evidence. System prompts are excluded and displayed content is redacted and bounded.
- Conservative session outcomes that preserve Hermes' raw end reason.
- Local agent-log telemetry for model latency, cache-hit ratio, and tool duration. The ten most recently used log paths are cached until a source log changes, and displayed log-window bounds come from all parseable lines rather than only model-attributed events.
- An **AI Models** tab that keeps an automatic all-time inventory of every recorded model while requests, token mix, cost, shared OAuth quota burn, observed failures, retry/model-switch sessions, total latency, and seven-day request trends honor the selected period. Each row leads with a one-sentence verdict fusing API health and work evidence. Its ten sortable columns are Model, Route, Requests, Tokens in/out/cached, Cost · quota (weekly), Fail rate, Retry/switch, Work evidence, Total latency, and Trend; the default sort is total tokens descending. Rates carry their own denominators in the cell ("0.4% · of 1,306 logged calls"), samples below the configured floor render as plain fractions instead of percentages, and the bounded-log window is declared as a chip on the section header. Rows expand into a two-pane evidence card — an API layer pane (request mix in call units, error counts of N logged, tool failures with their tool-call denominator, total latency) and a work ledger pane (Wilson-bound headline, per-task-type Eligible/Completed/Clean/Recovered fractions, by-route breakdown, per-accepted-task efficiency) — closed by a compact provenance block.
- Cross-profile session, token, cost, model, and outcome totals.
- Gateway and platform health for the default and named profiles.
- Schedule status, next/last run, delivery errors, and failure streaks without exposing schedule prompts.
- Overview, Operations, Tools (with skill invocations), System, AI Usage, and AI Models views. Aggregates on AI Models drill through to the filtered Sessions view.
- Live account-level usage for OpenAI Codex, Anthropic Claude, Nous Research Portal, OpenRouter, DeepSeek, Grok, Kimi Code Plan, and Z.AI GLM Coding Plan using credentials already configured in Hermes.
- Five-minute in-memory provider cache, explicit partial/stale states, and a manual fresh refresh.
- AI Models caches immutable closed-session classification facts in memory and reuses unchanged period payloads for 60 seconds. The Desktop polls every five minutes; manual refresh bypasses the payload cache immediately. No cache file is written.
- Failure-first, recent, cost, token, and tool-call sorting; pagination grows to a 500-session safety limit.
- Tools aggregation scans at most the latest 50,000 assistant rows and explicitly reports truncation. Search snippet IDs are queried in chunks of at most 900 parameters for older SQLite builds.
- Click-to-sort headers on every evidence table, with visible direction and keyboard-accessible controls.
- Persistent 7/30/90-day, all-time, or custom inclusive start/end date filtering for historical analytics.

## Compatibility

The initial release is verified with:

- Hermes Agent `0.20.5`
- Hermes state schema `26`
- Hermes Desktop Plugin SDK from the 2026-08-19 release
- Windows 11

The plugin uses Hermes' public Desktop SDK and `SessionDB(read_only=True)`. The System view shows the active schema and data source so compatibility is visible after an update.

## Install from a local checkout

1. Place this repository at `$HERMES_HOME/plugins/session-lens`.
2. Enable its backend:

   ```text
   hermes plugins enable session-lens
   ```

3. Restart Hermes Desktop so its embedded backend mounts `dashboard/plugin_api.py`.
4. If only `desktop/plugin.js` changes during development, open the command palette and run **Reload desktop plugins**; backend changes still require a Desktop restart.

The Desktop half is enabled by default once the trusted local package is present. It can be disabled live in **Settings → Plugins**.

## Install from GitHub

After this project has a public GitHub repository, Hermes can use its confirmation-based install link:

```text
hermes://plugin/install?repo=OWNER/hermes-session-lens&enable=1
```

Replace `OWNER` with the repository owner. Hermes shows the source and components for confirmation before installing anything.

## Updates

Hermes Agent updates do not remove this plugin because it lives under `$HERMES_HOME/plugins/session-lens`, outside the Hermes Agent source checkout. Update Session Lens separately by replacing that folder with a newer release, then restart Hermes Desktop.

AI Models settings live under the plugin's namespaced Hermes configuration. The sample threshold defaults to 20. Route fallback starts with distinct historical routes already recorded for the same model or model family; explicit model-id glob mappings override those inferred defaults:

```yaml
plugins:
  entries:
    session-lens:
      settings:
        rate_sample_threshold: 20
        model_route_mappings:
          "gpt-5.6-*": "OpenAI OAuth"
```

## Privacy and safety

- Session stores use Hermes' `SessionDB(read_only=True)` contract; dormant profile stores may use SQLite immutable mode only when no WAL exists.
- The API defines no archive, rename, delete, import, export, or other mutation route.
- Tool results, transcript events, schedule errors, and gateway errors are secret-redacted and length-bounded before reaching the Desktop page.
- Schedule and system prompts are never returned by the API.
- Runtime logs and AI Models classification/payloads use bounded in-memory caching only; no new cache file is written.
- Session content, prompts, and local telemetry are never sent to a third-party analytics service.
- The AI Usage tab makes direct authenticated quota requests only to OpenAI, Anthropic, xAI, Nous Research, OpenRouter, DeepSeek, Kimi, and Z.AI. Credentials remain in the Python backend and are never returned to the Desktop plugin.
- AI Models reads model IDs, routes, accounting, and session evidence locally. Session Lens—not Hermes—assigns one primary session type using first-match precedence: Orchestration, Coding, Writing, Analysis, then General. Classification uses recorded tool calls and arguments, including code-mutating commands and artifact paths; read-only Git/GitHub inspection does not imply Coding, cron, Telegram, webhook, desktop, and schedule remain sources, and auxiliary jobs retain separate unscored labels. OAuth quota is shared at provider-account level, suppresses pace judgments during the first 10% of a billing period, and only shows per-accepted-task efficiency after ten valid accepted tasks. General and Analysis use the conservative first-attempt proxy; Coding requires a resolved session with a successful code artifact save or commit; Writing requires a resolved session with a successful non-code artifact write; Orchestration and auxiliary jobs show `n/a`. Retry/switch counts rewinds, near-identical same-model prompt resends within five minutes, and same-role model changes. The table's Fail rate remains an API-attempt metric from bounded logs. Expanded work reliability instead scores completed main-role tasks, recovered tasks, clean completions, and terminal model/API failures; open, cancelled, orchestration, auxiliary, ambiguous, switched-away, and uncovered runs cannot improve the rate. Models with at least the configurable sample threshold rank by the lowest 95% Wilson upper failure bound. Fail and retry/switch cells display their own denominators; samples below the threshold render as neutral fractions and sort after adequately sampled rows. Models below the eligible-task floor open with a not-rankable banner stating the 95% Wilson upper bound rather than a headline completion percentage. Zero-request rows suppress bounded-log failure and latency values to avoid mixing windows. Recorded tool-call failures are reconciled separately against the recorded tool-call total. Unknown routes first use explicit mappings, then distinct historical routes for the model or family, and otherwise become actionable `Unmapped (edit in config)` labels. Cost preserves recorded actual, estimated, free, subscription, mixed, or unpriced state; cached tokens show zero only after the route demonstrates cache reporting. TTFT is unavailable because Hermes does not record it.
- Provider checks do not read browser cookies. Anthropic reuses Hermes OAuth, Grok reuses Hermes `xai-oauth`, and Kimi/Z.AI reuse Hermes API keys; no provider CLI needs to remain running.
- Usage checks accept provider credentials only when Hermes resolves them for the official provider host. Z.AI credential resolution deliberately avoids Hermes inference probes.

Failure detection combines authoritative Hermes finish/effect states with conservative signatures in tool results. SQL signatures only identify candidates; the Python signature confirms content before any API metric counts it. The inspector distinguishes the bounded evidence currently shown from the confirmed full-session total so users can verify it. File paths are observed evidence, not a guaranteed audit of every filesystem operation.

## Development

The Desktop entry is uncompiled ESM. It may import only `@hermes/plugin-sdk`, `react`, and `react/jsx-runtime`, and must use `jsx()`/`jsxs()` rather than JSX syntax.

Run the compatibility tests with the Python environment used by Hermes:

```text
python -m unittest discover -s tests -v
node --check desktop/plugin.js
```

## Credits and license

Hermes Session Lens is MIT licensed. See [UPSTREAM.md](UPSTREAM.md) and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for transparent credit to TokenTelemetry, Hermes Session Analyzer, Hermes Agent, and the Grok quota adapter informed by Hermes LLM Quota Monitor.
