# Hermes Session Lens

Hermes Session Lens is a native, read-only observability page for Hermes Desktop. It appears in the left sidebar and explains what each session consumed and did, then connects that evidence to runtime health, profiles, schedules, and Kanban execution.

It is a unified Hermes plugin—one install contains the native Desktop page and its namespaced Python API. There is no iframe, separate dashboard, Node server, or telemetry upload.

## What it includes

- Failure-first session browser with full-text search snippets.
- Recorded actual/estimated cost provenance, with **Included** and **Unpriced** states instead of a false `$0`.
- Input, output, cache-read, cache-write, reasoning, and per-model usage.
- Dedicated failed-call inspector with bounded, redacted result snippets.
- Tool volume, failure rate, last use, and per-session call evidence.
- Explicit skill invocation from recorded `skill_view` and `skill_manage` calls; available skills are not mislabelled as used.
- Files-observed summary from tool path arguments and bounded command-path extraction.
- Async delegation summaries.
- A chronological Trace tab for user, assistant, reasoning, tool-call, and tool-result evidence. System prompts are excluded and displayed content is redacted and bounded.
- Conservative session outcomes that preserve Hermes' raw end reason.
- Local agent-log telemetry for model latency, cache-hit ratio, and tool duration, cached until a source log changes.
- Cross-profile session, token, cost, model, and outcome totals.
- Gateway and platform health for the default and named profiles.
- Schedule status, next/last run, delivery errors, and failure streaks without exposing schedule prompts.
- Shared Kanban task and run status with bounded failure evidence.
- Overview, Operations, Tools, Skills, and System views.
- Ask Lens: builds a grounded analysis prompt locally, copies it, and opens a new Hermes chat.
- Failure-first, recent, cost, token, and tool-call sorting; pagination grows to a 500-session safety limit.

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

## Privacy and safety

- Session stores use Hermes' `SessionDB(read_only=True)` contract. Kanban stores use SQLite `mode=ro`; dormant profile stores may use SQLite immutable mode only when no WAL exists.
- The API defines no archive, rename, delete, import, export, or other mutation route.
- Tool results, transcript events, schedule errors, gateway errors, and Kanban evidence are secret-redacted and length-bounded before reaching the Desktop page.
- Schedule and system prompts are never returned by the API.
- Runtime logs are parsed locally with per-file memory caching; no new cache file is written.
- No session content or usage telemetry is sent over the internet.
- Ask Lens copies a locally generated prompt; the user decides whether to paste and submit it in Hermes.

Failure detection combines Hermes' recorded finish/effect states with conservative signatures in tool results. The evidence is shown so users can verify it. File paths are observed evidence, not a guaranteed audit of every filesystem operation.

## Development

The Desktop entry is uncompiled ESM. It may import only `@hermes/plugin-sdk`, `react`, and `react/jsx-runtime`, and must use `jsx()`/`jsxs()` rather than JSX syntax.

Run the compatibility tests with the Python environment used by Hermes:

```text
python -m unittest discover -s tests -v
node --check desktop/plugin.js
```

## Credits and license

Hermes Session Lens is MIT licensed. See [UPSTREAM.md](UPSTREAM.md) and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for transparent behavior-level credit to TokenTelemetry, Hermes Session Analyzer, and Hermes Agent.
