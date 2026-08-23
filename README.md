# Hermes Session Lens

Hermes Session Lens is a native, read-only telemetry page for Hermes Desktop. It appears in the left sidebar and explains what each session consumed and did: tokens, recorded cost, models, tools, skills, failures, files, and delegated work.

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
- Overview, Tools, Skills, and System views.
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

3. Restart the Hermes gateway so it mounts `dashboard/plugin_api.py`.
4. If the sidebar entry has not appeared within a few seconds, open the command palette and run **Reload desktop plugins**.

The Desktop half is enabled by default once the trusted local package is present. It can be disabled live in **Settings → Plugins**.

## Install from GitHub

After this project has a public GitHub repository, Hermes can use its confirmation-based install link:

```text
hermes://plugin/install?repo=OWNER/hermes-session-lens&enable=1
```

Replace `OWNER` with the repository owner. Hermes shows the source and components for confirmation before installing anything.

## Updates

Hermes Agent updates do not remove this plugin because it lives under `$HERMES_HOME/plugins/session-lens`, outside the Hermes Agent source checkout. Update Session Lens separately by replacing that folder with a newer release, then restart the gateway.

## Privacy and safety

- Every SQLite handle is opened with Hermes' read-only connection contract.
- The API defines no archive, rename, delete, import, export, or other mutation route.
- Tool-result snippets are secret-redacted and length-bounded before reaching the Desktop page.
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

