# Hermes Session Lens

Hermes Session Lens is a native observability page for Hermes Desktop. It appears in the left sidebar and explains what each session consumed and did, shows current provider allowances and balances, then connects that evidence to runtime health, profiles, and schedules.

It is a unified Hermes plugin—one install contains the native Desktop page and its namespaced Python API. There is no iframe, separate dashboard, Node server, or third-party telemetry service.

This documentation describes Hermes Session Lens `0.32.2`.

## What it includes

- A project rollup on Overview ("Where the spend goes"): sessions grouped by git repository, then working directory, then source for sessions with no recorded directory — sessions, tokens, recorded cost with unpriced counts, confirmed failures, top models, last activity.
- An agent run-health scoreboard on Operations → Schedules: cron sessions grouped per job by title, with a latest-runs strip (completed/failed/cancelled/running), failure counts, streaks, average duration and cost per run, and click-through to the job's sessions.
- A context-compression distress readout on Operations → Health: sessions with recorded fallback streaks, ineffective passes, compression failures, or active cooldowns, with the top offenders listed; a single quiet line when nothing is distressed.
- Profile provenance: `/health` names the profile whose records the backend serves, and the page header shows it as a "data: <profile> profile" chip. Hermes plugins and telemetry are per-profile, and the desktop can silently fall back to another profile's gateway when the active one is not running — the chip keeps the data source honest.
- A machine-consumable digest at `GET /api/plugins/session-lens/digest?days=7`: period totals with prior-period deltas (sessions, tokens, recorded cost, failure events, tool failures), the attention list, monthly spend per provider with month-end projections and cap status (`&budgets=openrouter:150,all:300`, the same parameter the desktop sends), top models by requests with work-reliability evidence, quota windows with pace, exhaustion forecasts, and what local records say consumed them, money windows with how much local sessions explain, non-model service balances with the configured/monitored/needs-attention tally, and a ready-made `markdown` field — built for cron agents, notification pipelines, or any automation that reads the plugin API. Read-only like every other route.
- Instruction rules (Rules tab, `GET /rules?rules=<json>` and `GET /rules/templates`): restate an instruction you give your agent as WHEN conditions (the user message contains…, a tool was called, a tool call failed, the user wrote in Arabic/Turkish/English, any tool was used) and THEN expectations (call a tool, at some point or before the final answer, optionally requiring success; never use a tool; try one tool before another; limit tool calls; never repeat an identical call; reply contains / never contains / contains exactly N times; keep the reply short; reply in the user's language or a given language; tool calls never mention…; tools stay inside folders; never call a tool with…; finish with a written answer; start replying within N seconds), each clause negatable, conditions matched all-or-any. Twelve presets (every reply must call a tool, no tool loops, cite when searching, never run a destructive command, …) are pre-filled rules over the same catalog. Tool fields are comboboxes fed by `GET /tool-names`: every tool Hermes can call right now (its live registry, including connected MCP servers) merged with every name the records have seen, ranked by recorded calls, plus family globs such as `browser_*`; free text and globs still work, and an unknown name is flagged, not rejected. Session Lens grades every recorded turn in the selected period against each rule. A turn starts at a user message and runs to the next one; verdicts are pass, fail, or not applicable, each failure links to the turn, and scores group by the session's model and rank by the 95% Wilson upper failure bound above a sample floor. Rules live in the desktop's plugin storage and travel as a query parameter; nothing reads SOUL.md, nothing judges tone, and the backend writes nothing. Rules can be exported and imported as JSON, and enabled rules add an "Instruction rules" section to the digest.
- Export menus on Sessions, Tools, AI Models, AI Usage, and Overview: every data view offers CSV (tables), JSON (the full payload the tab renders), or Markdown (the digest for the selected period), each as a download through the desktop's Save File dialog or a copy to the clipboard. The Sessions export re-reads the list with the live search, sort, and period, up to 500 rows. Exports are assembled in the desktop from the same read-only routes; the backend gains no export route and writes no file.
- Service discovery and balances (`GET /services`): every non-model service Hermes is configured with is inventoried from key *names* in the profile's `.env`, `mcp_servers` in `config.yaml`, and known CLIs on PATH — never from skill folders or credential files elsewhere — and listed under "Everything configured" with how it was found and whether Session Lens can read it. Adapters exist only for vendors whose usage endpoint was verified against the live API: Firecrawl credits per key (extra `FIRECRAWL_API_KEY_*` keys become extra cards), ScrapeCreators credits, AgentMail cumulative counters, Bright Data account balance (a token without the permission is reported as such), and Monid workspace balance plus month-to-date spend from its run ledger via the `monid` CLI, which feeds the monthly budgets. Services with no readable usage API (Brave Search, Telegram, here.now, unknown MCP servers and keys) are listed with the reason, not guessed.
- A pluggable adapter registry (`GET /adapters`): every provider and service above is one module under `dashboard/_providers/` or `dashboard/_services/` that registers itself — label, credential probe, collector, the env key names or MCP server names that mark it configured, and the hosts it contacts. The dispatchers read the registry, so adding a vendor is adding a file (see [ADAPTERS.md](ADAPTERS.md)), and the route publishes the whole registry, credential-free, so anyone can see exactly which hosts the backend can reach.
- Monthly budgets on AI Usage: set a USD cap per provider or for all providers; `GET /budgets?budgets=openrouter:150,all:300` (caps are stored in the desktop's plugin storage and travel as a query parameter, so the backend stays write-free) returns month-to-date spend — the provider's own account figure where it reports one (OpenRouter's key usage), locally recorded session cost otherwise, each labelled with its source — the last seven days' pace, a month-end projection, and the date the cap would be crossed. Over-cap and on-pace-to-exceed budgets join the quota notes in the attention strip on every tab (`/attention?budgets=`).
- Context-weight dollars on the Tools tab: the weight of every tool and MCP server's results (recorded result length ÷ 4 ≈ tokens pushed into model context, honoring the selected period) is priced at each session's billing route through Hermes' own pricing module — direct entry at the input rate, plus a carried estimate for re-sends on later calls at the cache-read rate, stated as an upper bound because compaction is not timestamped. Subscription (OAuth) routes show "quota" instead of dollars, routes without a Hermes pricing entry show "unpriced", and mixed rows state the priced share; no rate is ever guessed.
- Quota burn attribution on AI Usage: every quota window carries a "What consumed this window" block that joins local `session_model_usage` records to the window's span (reset minus the labelled duration; trailing 7 days, declared as such, when no span is readable) and ranks projects, sessions, and models by share of locally recorded tokens, each row drilling through to the filtered Sessions view. Model-specific windows ("Opus week") count only that family. Money windows state how much of the account figure local sessions explain and that the remainder came from other machines, tools, or profiles; shares never claim to explain the provider's own percentage. Extra pooled-account cards carry no attribution because local records name the provider, not the login.
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
- Live account-level usage for OpenAI Codex, Anthropic Claude, Nous Research Portal, OpenRouter, DeepSeek, Grok, Kimi Code Plan, and Z.AI GLM Coding Plan using credentials already configured in Hermes. Anthropic gets one card per product Hermes holds a credential for: the Claude subscription (5-hour and 7-day windows, extra-usage state) and the Console API key (per-minute request and token limits, as its own card), each read from the rate-limit headers of a one-token message when the credential cannot use the account-usage endpoint — see [Trust](#trust) for exactly what is sent and how to turn it off.
- Five-minute in-memory provider cache, explicit partial/stale states, and a manual fresh refresh.
- AI Models caches immutable closed-session classification facts in memory and reuses unchanged period payloads for 60 seconds. The Desktop polls every five minutes; manual refresh bypasses the payload cache immediately. No cache file is written.
- Failure-first, recent, cost, token, and tool-call sorting; pagination grows to a 500-session safety limit.
- Tools aggregation scans at most the latest 50,000 assistant rows and explicitly reports truncation. Search snippet IDs are queried in chunks of at most 900 parameters for older SQLite builds.
- Click-to-sort headers on every evidence table, with visible direction and keyboard-accessible controls.
- Persistent 7/30/90-day, all-time, or custom inclusive start/end date filtering for historical analytics.

## Compatibility

Verified on 2026-09-03 with:

- Hermes Agent `0.21.0` (Hermes Desktop `0.21.0`, commit `0cbc6e3`)
- Hermes state schema `28`
- Hermes Desktop Plugin SDK from the 2026-08-19 release
- Windows 11

The first release was verified on Hermes Agent `0.20.5` with state schema `26`, and every release since has been checked against the Hermes build installed at the time.

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
        anthropic_usage_probe: true
```

`anthropic_usage_probe` controls the one request Session Lens makes that is not a usage or balance endpoint (see [Trust](#trust)). Set it to `false` to stop the one-token Claude message; the Anthropic card then reads only logins that answer the account-usage endpoint, and a setup token or API key shows "Not configured" with the reason.

## Privacy and safety

- Session stores use Hermes' `SessionDB(read_only=True)` contract; dormant profile stores may use SQLite immutable mode only when no WAL exists.
- The API defines no archive, rename, delete, import, or other mutation route. Exports (CSV, JSON, Markdown) are assembled in the desktop from the read-only routes; the backend never writes a file.
- Tool results, transcript events, schedule errors, and gateway errors are secret-redacted and length-bounded before reaching the Desktop page.
- Schedule and system prompts are never returned by the API.
- Runtime logs and AI Models classification/payloads use bounded in-memory caching only; no new cache file is written.
- Session content, prompts, and local telemetry are never sent to a third-party analytics service.
- The AI Usage and Services cards make direct authenticated requests only to the hosts listed under [Trust](#trust) below, each declared by its adapter and published by `GET /adapters`, and only to usage or balance endpoints — with one declared exception, the Anthropic one-token probe described there. Credentials remain in the Python backend and are never returned to the Desktop plugin.
- AI Models reads model IDs, routes, accounting, and session evidence locally. Session Lens—not Hermes—assigns one primary session type using first-match precedence: Orchestration, Coding, Writing, Analysis, then General. Classification uses recorded tool calls and arguments, including code-mutating commands and artifact paths; read-only Git/GitHub inspection does not imply Coding, cron, Telegram, webhook, desktop, and schedule remain sources, and auxiliary jobs retain separate unscored labels. OAuth quota is shared at provider-account level, suppresses pace judgments during the first 10% of a billing period, and only shows per-accepted-task efficiency after ten valid accepted tasks. General and Analysis use the conservative first-attempt proxy; Coding requires a resolved session with a successful code artifact save or commit; Writing requires a resolved session with a successful non-code artifact write; Orchestration and auxiliary jobs show `n/a`. Retry/switch counts rewinds, near-identical same-model prompt resends within five minutes, and same-role model changes. The table's Fail rate remains an API-attempt metric from bounded logs. Expanded work reliability instead scores completed main-role tasks, recovered tasks, clean completions, and terminal model/API failures; open, cancelled, orchestration, auxiliary, ambiguous, switched-away, and uncovered runs cannot improve the rate. Models with at least the configurable sample threshold rank by the lowest 95% Wilson upper failure bound. Fail and retry/switch cells display their own denominators; samples below the threshold render as neutral fractions and sort after adequately sampled rows. Models below the eligible-task floor open with a not-rankable banner stating the 95% Wilson upper bound rather than a headline completion percentage. Zero-request rows suppress bounded-log failure and latency values to avoid mixing windows. Recorded tool-call failures are reconciled separately against the recorded tool-call total. Unknown routes first use explicit mappings, then distinct historical routes for the model or family, and otherwise become actionable `Unmapped (edit in config)` labels. Cost preserves recorded actual, estimated, free, subscription, mixed, or unpriced state; cached tokens show zero only after the route demonstrates cache reporting. TTFT is unavailable because Hermes does not record it.
- Provider checks do not read browser cookies. Anthropic reuses the credentials Hermes already holds (environment tokens, its pool logins, Claude Code's login), Grok reuses Hermes `xai-oauth`, and Kimi/Z.AI reuse Hermes API keys; no provider CLI needs to remain running.
- Usage checks accept provider credentials only when Hermes resolves them for the official provider host. Z.AI credential resolution deliberately avoids Hermes inference probes; the Anthropic adapter is the only one that sends an inference request, and it is declared, cached, and switchable.

Failure detection combines authoritative Hermes finish/effect states with conservative signatures in tool results. SQL signatures only identify candidates; the Python signature confirms content before any API metric counts it. The inspector distinguishes the bounded evidence currently shown from the confirmed full-session total so users can verify it. File paths are observed evidence, not a guaranteed audit of every filesystem operation.

**The text signatures are English-only.** They match words such as `error`, `failed`, `traceback`, `permission denied`, `timed out`, and non-zero exit codes. A tool that reports its failure in another language (or a program with localized error messages) is counted only when Hermes recorded an error finish or effect state, so failure rates on Tools, Sessions, and AI Models are a floor, not a ceiling, for non-English tool output. The System tab states this limit, and `/system` carries it as `failure_signatures_language`.

## Trust

Session Lens is meant to be inspected, not trusted on its word. This is what the backend can and cannot do, and how to check each claim from the code.

**What it reads.** Hermes' session store through `SessionDB(read_only=True)` (dormant profile stores in SQLite immutable mode when no WAL exists), the profile's agent logs, key *names* from the profile's `.env` (never values, except inside an adapter that needs the key for its own request), `mcp_servers` from `config.yaml`, Hermes' own credential resolvers for the provider adapters, and Hermes' tool registry for the tool-name directory. It never opens skill folders, browser profiles or cookies, or credential files outside `$HERMES_HOME`.

**What it writes.** Nothing. There is no mutation route (`/system` reports `mutation_endpoints: 0`), no cache file, no export file — CSV, JSON, and Markdown exports are assembled in the desktop from the read-only routes, and rules, budgets, and dismissals live in the desktop's plugin storage and travel as query parameters. Session Lens itself never rotates or rewrites a Hermes login. Two adapters ask Hermes' own credential resolvers for a current token (OpenAI Codex through Hermes' account-usage module, Grok through Hermes' xAI resolver), and Hermes may refresh an expiring OAuth token through its normal path when asked; the Anthropic adapter deliberately does not, and an expired Anthropic login is reported as expired.

**What it contacts.** Only the hosts below, each with the credential Hermes already holds for that vendor, and only for the vendor's own usage or balance endpoint — with one exception, stated in full in the next paragraph. Every host is declared by its adapter module, published credential-free by `GET /adapters`, shown on the System tab under "External hosts", and this table is checked by the test suite against the registry:

| Adapter | Host | How |
| --- | --- | --- |
| OpenAI Codex | `chatgpt.com` | Hermes' own account-usage code with the Hermes Codex OAuth login |
| Anthropic Claude | `api.anthropic.com` | the account-usage endpoint for full OAuth logins; otherwise a one-token message whose response headers carry the allowance (see below) |
| Nous Research Portal | `portal.nousresearch.com` | Hermes' own portal client with the Hermes Nous login |
| OpenRouter | `openrouter.ai` | Hermes OpenRouter API key |
| DeepSeek | `api.deepseek.com` | Hermes DeepSeek API key |
| Grok | `cli-chat-proxy.grok.com` | Hermes xAI OAuth credentials |
| Kimi Code Plan | `api.kimi.com` | Hermes Kimi API key |
| Z.AI GLM Coding Plan | `api.z.ai` | Hermes Z.AI API key |
| Firecrawl | `api.firecrawl.dev` | `FIRECRAWL_API_KEY` from the Hermes `.env` (cloud only; a self-hosted URL is never called) |
| ScrapeCreators | `api.scrapecreators.com` | `SCRAPECREATORS_API_KEY` from the Hermes `.env` |
| AgentMail | `api.agentmail.to` | `AGENTMAIL_API_KEY` from the Hermes `.env` |
| Bright Data | `api.brightdata.com` | `BRIGHTDATA_API_KEY` (or the MCP key) from the Hermes `.env` |
| Monid | none directly | the local `monid` CLI, which talks to Monid itself |

No request is made for a provider whose local credential probe shows nothing configured, no request carries session content, prompts, or telemetry, and nothing is sent to any analytics or telemetry service. Brave Search, Telegram, here.now, and unknown keys or MCP servers are inventoried by name and never contacted.

**The one inference request: the Anthropic probe.** Anthropic's account-usage endpoint answers only OAuth logins that carry the `user:profile` scope. A Claude setup token (`CLAUDE_CODE_OAUTH_TOKEN` / `ANTHROPIC_TOKEN`) carries `user:inference` alone and an API key is not OAuth at all, so for those credentials the only readable source is the `anthropic-ratelimit-*` headers Anthropic attaches to every message response. Session Lens therefore sends one message to Claude Haiku — the single character `.`, `max_tokens: 1`, no session content — per Anthropic credential, reads the headers, and discards the reply. For a subscription token that yields the 5-hour and 7-day allowance and whether extra usage is on; for a Console API key it yields the per-minute request and token limits, and each becomes its own card. The cost is one request against the subscription window or a fraction of a cent on the key, and the call appears in the Anthropic Console request log. Every outcome is cached for 15 minutes per credential (a manual refresh re-probes), a full login is tried against the usage endpoint first and probed only if that fails, the adapter is registered with `request_kind: inference_probe` so `GET /adapters` and the System tab ("Inference probes") name it, and `anthropic_usage_probe: false` in the plugin settings turns it off. Subscription tokens are sent with the same Claude Code identity headers Hermes itself uses for its OAuth inference; API keys are sent plain. Nothing is refreshed, rotated, or written.

**What reaches the desktop.** Normalized usage figures, redacted and length-bounded snippets, and never a credential (`/system` reports `provider_credentials_returned_to_desktop: false`). System prompts and schedule prompts are never returned.

**Where the limits are.** Failure signatures are English-only (above). Quota attribution explains only what local records can explain and says so. Cost is recorded actual or estimated cost, or an explicit unpriced state, never a guess. Rules are deterministic checks over recorded turns; nothing reads SOUL.md and no model judges anything.

**How to verify.** `grep -rn "https://" dashboard/` finds every vendor URL in the backend, and each one lives in an adapter module under `dashboard/_providers/` or `dashboard/_services/` (the only other hit is a URL-prefix check in `_common.py`). `GET /adapters` lists the registry at runtime. `python -m unittest discover -s tests` runs the checks that keep this section, the System tab, and the registry in agreement.

## Development

The Desktop entry is uncompiled ESM. It may import only `@hermes/plugin-sdk`, `react`, and `react/jsx-runtime`, and must use `jsx()`/`jsxs()` rather than JSX syntax.

Provider and service adapters are one module each under `dashboard/_providers/` and `dashboard/_services/`; the packages import every module they contain, and each module registers itself with `register_provider(...)` or `register_service(...)`. [ADAPTERS.md](ADAPTERS.md) is the recipe, including the rule that an adapter is added only after its usage endpoint was verified against the live API.

Run the compatibility tests with the Python environment used by Hermes:

```text
python -m unittest discover -s tests -v
node --input-type=module --check < desktop/plugin.js   # plain `node --check` skips .js files that contain `import`
```

## Credits and license

Hermes Session Lens is MIT licensed. See [UPSTREAM.md](UPSTREAM.md) and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for transparent credit to TokenTelemetry, Hermes Session Analyzer, Hermes Agent, and the Grok quota adapter informed by Hermes LLM Quota Monitor.
