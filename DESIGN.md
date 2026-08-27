---
name: Hermes Session Lens
description: A native Hermes observatory for provider capacity, session evidence, accounting, and operational health.
colors:
  accent: "var(--ui-accent)"
  accent-soft: "color-mix(in srgb, var(--ui-accent) 14%, transparent)"
  success: "var(--ui-success)"
  success-soft: "color-mix(in srgb, var(--ui-success) 12%, transparent)"
  warning: "var(--ui-warning)"
  warning-soft: "color-mix(in srgb, var(--ui-warning) 12%, transparent)"
  destructive: "var(--destructive)"
  destructive-soft: "color-mix(in srgb, var(--destructive) 12%, transparent)"
  text-primary: "var(--ui-text-primary)"
  text-secondary: "var(--ui-text-secondary)"
  text-tertiary: "var(--ui-text-tertiary)"
  text-quaternary: "var(--ui-text-quaternary)"
  stroke-secondary: "var(--ui-stroke-secondary)"
  surface-secondary: "var(--ui-bg-secondary)"
  surface-tertiary: "var(--ui-bg-tertiary)"
typography:
  page-title:
    fontFamily: "inherit"
    fontSize: "1.0625rem"
    fontWeight: 680
    lineHeight: 1.3
    letterSpacing: "-0.015em"
  section-title:
    fontFamily: "inherit"
    fontSize: "0.9375rem"
    fontWeight: 650
    lineHeight: 1.35
  body:
    fontFamily: "inherit"
    fontSize: "0.75rem"
    fontWeight: 400
    lineHeight: 1.5
  label:
    fontFamily: "inherit"
    fontSize: "0.6875rem"
    fontWeight: 400
    lineHeight: 1.45
  signal:
    fontFamily: "inherit"
    fontSize: "1rem"
    fontWeight: 650
    lineHeight: 1.3
  evidence:
    fontFamily: "var(--font-mono)"
    fontSize: "0.6875rem"
    fontWeight: 400
    lineHeight: 1.55
rounded:
  control: "4px"
  evidence: "5px"
  container: "6px"
  pill: "999px"
spacing:
  xxs: "0.15rem"
  xs: "0.35rem"
  sm: "0.5rem"
  md: "0.75rem"
  lg: "1rem"
  xl: "1.5rem"
components:
  status-pill-neutral:
    backgroundColor: "{colors.surface-tertiary}"
    textColor: "{colors.text-secondary}"
    typography: "{typography.label}"
    rounded: "{rounded.pill}"
    padding: "0.1rem 0.45rem"
  status-pill-accent:
    backgroundColor: "{colors.accent-soft}"
    textColor: "{colors.accent}"
    typography: "{typography.label}"
    rounded: "{rounded.pill}"
    padding: "0.1rem 0.45rem"
  status-pill-danger:
    backgroundColor: "{colors.destructive-soft}"
    textColor: "{colors.destructive}"
    typography: "{typography.label}"
    rounded: "{rounded.pill}"
    padding: "0.1rem 0.45rem"
  view-tab-active:
    backgroundColor: "transparent"
    textColor: "{colors.text-primary}"
    typography: "{typography.body}"
    padding: "0.62rem 0.7rem"
  session-row-selected:
    backgroundColor: "{colors.accent-soft}"
    textColor: "{colors.text-primary}"
    padding: "0.78rem 0.85rem"
  evidence-table:
    backgroundColor: "{colors.surface-secondary}"
    textColor: "{colors.text-primary}"
    typography: "{typography.body}"
    rounded: "{rounded.container}"
---

# Design System: Hermes Session Lens

## Overview

**Creative North Star: "The Native Observatory"**

Hermes Session Lens is an **Operate** surface inside Hermes Desktop: one native observatory connecting provider capacity and session evidence to accounting and operational health. It inherits Hermes typography, controls, codicons, focus behavior, and light/dark theme roles; it does not establish a separate brand skin.

This design document describes Hermes Session Lens `0.12.1`.

The interface is compact, quiet, and evidence-first. A user finds a session, verifies trace and accounting, then inspects runtime, profile, or schedule state without leaving Hermes. Progressive disclosure carries the density, while explicit provenance, bounded excerpts, and read-only language make the trust boundary visible.

**Key Characteristics:**

- Native Hermes controls, theme roles, and codicons.
- Compact borders, tabular data, and independent scrolling regions.
- Failure-first ordering with restrained, labelled status color.
- Stable navigation from fleet-level signals to session-level evidence.
- Redacted, bounded, read-only presentation of local records.

## Colors

The palette is wholly theme-owned: Hermes accent and neutral roles carry structure, while the destructive role is reserved for failure evidence.

### Primary

- **Hermes Accent:** Active tabs, selected-state emphasis, positive/running status, lightweight daily bars, and invoked-skill markers.
- **Hermes Accent Wash:** Selected rows and compact accent pills without creating a separate card layer.

### Neutral

- **Primary Text:** Titles, metric values, active labels, and table values.
- **Secondary Text:** Evidence content and secondary status text.
- **Tertiary Text:** Labels, descriptions, inactive navigation, and supporting metadata.
- **Quaternary Text:** Timestamps and the quietest contextual details.
- **Secondary Stroke:** The one-pixel separator used throughout the workspace.
- **Secondary Surface:** Table headers, trace notes, and monospace evidence blocks.
- **Tertiary Surface:** Neutral pills and native select surfaces.

### Semantic status

- **Hermes Destructive:** Failed outcomes, detected failure counts, failed tool events, and caution copy.
- **Hermes Destructive Wash:** The bounded failure-inspector notice and failure pills.

**The Theme Owns Color Rule.** Every color resolves from Hermes theme variables; do not add hardcoded light- or dark-mode colors.

**The Label Before Hue Rule.** Status always includes readable text and, where useful, a codicon; color never carries meaning alone.

## Typography

**Display Font:** Hermes inherited UI family
**Body Font:** Hermes inherited UI family
**Label/Mono Font:** Hermes `--font-mono` role for recorded evidence

**Character:** Typography is deliberately compact and native, with modest weight changes rather than oversized headings. Numerals carry operational authority through tabular alignment; monospace is limited to paths, identifiers, prompts, and tool evidence.

### Hierarchy

- **Page title:** Compact route identity in the header.
- **Section title:** Dense workspace and table-section headings.
- **Body:** Tabs, table rows, labels, and explanatory content.
- **Label:** Metadata, timestamps, field names, status pills, and supporting descriptions.
- **Signal:** The four top-line accounting values and detail metrics.
- **Evidence:** Read-only recorded content, paths, IDs, tool results, and prompt previews.

**The Numbers Line Up Rule.** Tokens, costs, counts, percentages, and durations use tabular numerals in metrics, tables, and definitions.

**The Evidence Stays Evidence Rule.** Monospace identifies recorded or generated material; it is not a decorative type choice.

## Layout

The page fills its Hermes route and uses a compact header, a four-signal strip, seven stable view tabs, then a dense scroll-managed workspace. Session views establish recorded cost, tokens, sessions, and failures; AI Usage swaps those signals for connected providers, attention, next reset, and refresh freshness; AI Models shows distinct models, requests, total tokens, and known API cost.

An attention banner precedes the Sessions toolbar when runaway work exists: a warning-wash strip listing up to five sessions that are open past the 24-hour threshold (still-active ones always; idle ones only with five-million-plus tokens) or were reaped with five-million-plus tokens, each row a button that reveals that session in the list. Severity distinguishes still-active runaways (destructive text) from idle never-closed sessions (primary text). Each note carries a close control; dismissals persist in plugin storage (capped at one hundred ids) and a quiet restore link brings them back — dismissing all collapses the banner to a one-line dismissed count. The banner renders nothing when no session qualifies.

Sessions uses a wide master/detail split with a minimum 20rem list and 28rem detail, weighted 0.82/1.38. List and detail scroll independently. The detail proceeds through its own header, four metrics, Summary/Trace/Tools/Failures/Files tabs, then a single content scroller.

At the Hermes 700px narrow threshold, Sessions becomes a list-to-detail drill-in, not a vertical stack. The list and search/filter toolbar occupy the page until selection; selection replaces them with detail, and a labelled Back control returns to the list. The selected session and detail tab remain stable through the drill-in.

Top-level tabs, detail tabs, and four-column signal strips scroll horizontally when needed. Search and filters wrap before they clip. Evidence tables retain a 34rem minimum and scroll horizontally rather than collapsing columns into ambiguous cards. Aggregate views center their content within measured 68–84rem bounds.

**The One Evidence Pane Rule.** Narrow mode shows either the session list or the session detail, never compressed halves or stacked duplicates.

## Elevation & Depth

The system is flat and uses no decorative shadows or gradients. Depth comes from Hermes secondary and tertiary surfaces, one-pixel secondary strokes, selected-row washes, and the two-pixel active-tab underline.

**The Stroke, Not Shadow Rule.** Separate operational regions with theme strokes and tonal surfaces; shadows would make this native evidence workspace feel like a separate dashboard.

## Shapes

The form language is compact and lightly softened. Native/select controls use restrained corners, evidence blocks are slightly rounded, table containers use the largest small-radius step, and pills are fully rounded. Daily bars use only a subtle two-pixel top radius. Large decorative cards and ornamental silhouettes are absent.

## Components

### Native controls

- **Buttons, inputs, textareas, and segmented controls:** Use Hermes SDK components so hover, focus, disabled, keyboard, and theme behavior remain native.
- **Selects:** Native selects match Hermes surfaces, secondary strokes, compact padding, and the accent outline.
- **Icons:** Codicons remain baseline-aligned with labels; icon-only actions carry accessible names and titles.

### Header and signals

- **Header:** Compact title and grounding sentence on the left; preset/custom time range and explicit refresh on the right. Custom ranges expose labelled native start/end date fields and treat the end date as inclusive. AI Usage replaces historical controls with a labelled live-quota state and makes refresh bypass the provider cache. AI Models keeps the historical range and refreshes both local model evidence and shared account quotas.
- **Refresh cadence:** AI Models reuses unchanged backend payloads for 60 seconds and polls from the Desktop every five minutes. Closed-session classification facts remain in memory for the process lifetime; open sessions are recomputed. Manual refresh passes `fresh=true` and updates the visible query immediately.
- **Signal strip:** Four equal accounting cells—Recorded cost, Tokens, Sessions, and Failures detected—with a small provenance/detail line below each value.
- **Partial-known cost:** When some sessions are unpriced, preserve the sum of known cost and show the unpriced session count; do not replace known accounting with a false zero.

### Navigation

- **View tabs:** Sessions, Overview, Operations, Tools, System, AI Usage, and AI Models remain stable and horizontally scrollable. Active state uses stronger text and an accent underline. Skill invocations render as a section inside Tools.
- **Operations segments:** Health, Profiles, and Schedules use the Hermes segmented control.
- **Detail segments:** Summary, Trace, Tools, Failures, and Files preserve their order and surface counts when known.

### Session rows and status pills

- **Session row:** Title and outcome lead; timestamp/source, tokens, tool calls, cost, model, and optional bounded search snippet follow in descending emphasis. Selection uses the accent wash.
- **Pills:** Neutral, accent, and destructive tones are compact and fully rounded. Outcome pills pair label and codicon; failure counts remain visibly distinct.

### AI Usage

- **Provider grid:** All configured sources—Codex, Anthropic Claude, Nous Portal, OpenRouter, DeepSeek, Grok, Kimi Code Plan, and Z.AI GLM Coding Plan—appear together under Supported providers. Connected providers lead while every other provider retains its configured order. Each provider uses a compact bordered section in a two-column wide layout and a one-column narrow layout; these are operational groupings, not decorative cards.
- **Quota windows:** Labels and tabular remaining values lead; progress bars carry `progressbar` semantics and never communicate state by color alone. Monetary balances retain their unit and never become a fabricated percentage when no denominator exists.
- **Provider states:** Connected, not configured, expired or rejected, forbidden, unavailable, partial, and stale are stated in text. Stale data names the failed refresh while preserving the last successful reading.
- **Trust boundary:** The view states that credentials stay in the Python backend, browser cookies are not read, and only normalized account usage reaches JavaScript.

### AI Models

- **Automatic inventory:** One row per all-time distinct model ID from `session_model_usage`, with a session-row fallback for older accounting. No model list is hardcoded, so first-time routes appear automatically; requests, tokens, cost, reliability, latency, task mix, and trend values honor the selected period.
- **Routing summary:** A compact card above the table states the best current evidence per task type — Coding, Writing, Analysis, General — each naming the model with the lowest 95% Wilson upper bound at that task type (gate-passing models first), its completed/eligible fraction, and an explicit "below the N-task floor" caveat while provisional. Model names drill through to the Sessions view.
- **Verdict-first rows:** Under each model name the row states a one-sentence verdict fused from the two evidence layers—API health from bounded logs and work evidence from eligible tasks (for example, "API steady. Too little finished work to rank — 4 of 20 tasks; true failure could reach 49%."). The verdict is generated only from already-computed values and never asserts a judgment the data does not support.
- **Comparison table:** All ten headers—Model, Route, Requests, Tokens in/out/cached, Cost · quota (weekly), Fail rate, Retry/switch, Work evidence, Total latency, and Trend—are keyboard-sortable. The default is total tokens descending; the wide evidence table scrolls horizontally and retains input/output/cached token alignment. Cost and quota burn share one column: the recorded cost value above the quota meter. Work evidence is a dedicated column showing eligible tasks against the ranking gate ("4 / 20 tasks", or rank when achieved), a progress meter toward the gate, and the 95% Wilson upper bound as "risk ≤ Y%"; unrankable rows sort after rankable ones.
- **Two denominators, two panes:** A rate never sits next to a sample it does not describe. Fail and retry/switch cells render the percentage above its own denominator ("0.4% · of 1,306 logged calls"); any sample below the configured floor renders as a plain neutral fraction ("2/3") instead of a percentage. The bounded-log window is declared once as a chip on the section header. The word "requests" never appears next to a task count.
- **Expanded evidence card:** Opens with a warning-toned banner when the model is below the task floor ("Not rankable yet — X of 20 eligible tasks. True failure rate could be anywhere up to Y%."), so a task-completion percentage can never read as the headline on a tiny sample. Below it sit two explicitly labeled panes. "API layer — N calls in period · logs <window>" holds the request mix by session type in call units, rate-limit/timeout/API-error counts each shown "of N logged", tool failures with their denominator ("X of Y tool calls · Z%"), and total latency. "Work ledger — N eligible tasks" holds the rank and Wilson headline when rankable, a per-task-type table with Eligible / Completed / Clean / Recovered columns as fractions (unscored types state "not scored by design" or the exclusion reason), a by-route breakdown when needed, and per-accepted-task efficiency after the ten-acceptance floor. Acceptance bases surface as row tooltips. Session classification itself is unchanged: each session receives one primary type in first-match order—Orchestration, Coding, Writing, Analysis, General—from recorded tool calls, arguments, code-mutating commands, and artifact paths; transport and schedule sources never determine type, read-only Git/GitHub inspection does not imply Coding, and auxiliary jobs remain separate and unscored.
- **Narrow expansion:** Expanded model evidence becomes a normal `width: 100%` block inside its table row, with one-column internal layout and no sticky positioning or viewport-width calculation.
- **Quota burn:** OAuth subscription routes reuse provider-account quota windows. The fill states cap used and a one-pixel tick marks inferred period elapsed; the first 10% of a period is labelled `early in period` without a pace judgment, then text labels accompany success, warning, and destructive theme roles. Over-pace windows add a one-line forecast in the pace tone — "at this pace, empty ~<date>" — computed by linear extrapolation and shown only when the window would run out before its reset. Pay-as-you-go routes say `pay-go`.
- **Evidence drill-through:** Aggregates link to their evidence. The tool-failure count and the work-ledger task count in the expanded card are accent-colored links that open the Sessions view pre-filtered to that model (failures-only for the failure link), so no number on the page is a dead end.
- **Provenance block:** Each expanded card ends with one compact provenance block—routes and mapping source, the log window and the TTFT-not-recorded caveat, work-ledger exclusions and the ranking rule, and the classification order with acceptance bases—replacing scattered multi-line footnotes.
- **Honest gaps:** Cached tokens become a dash when the route has not demonstrated cache reporting. Retry/switch includes rewinds, same-model near-identical prompt resends within five minutes, and same-role model changes while excluding cross-role routing. The table failure rate counts bounded-log API attempts that ended in errors, timeouts, or rate limits; recorded tool-call failures are shown separately with their tool-call denominator for reconciliation. Work reliability treats a completed task after an observed API failure as recovered and counts an unrecovered failure only when the final model-role ends failed with no later successful API event. Open, cancelled, orchestration, auxiliary, ambiguous, switched-away, and uncovered runs are explicit exclusions, never implicit successes. Comparable models require the configured sample floor and rank by the lowest 95% Wilson upper failure bound. A row with no selected-period requests suppresses bounded-log fail rate and latency with an explanatory tooltip. Unknown routes use configured model-id globs, then distinct historical model/family routes, and finally the actionable `Unmapped (edit in config)` state. Total latency remains a bounded-log observation; TTFT is not recorded by Hermes, which the provenance block states. General and Analysis acceptance uses the conservative first-attempt proxy; Coding requires a resolved session with a successful code artifact save or commit; Writing requires a resolved session with a successful non-code artifact write; Orchestration says `n/a`.

### Evidence tables and trace

- **Tables:** Secondary-surface headers, one-pixel row separators, right-aligned numeric columns, and horizontal overflow preserve dense comparisons. Every header is a keyboard-accessible sort control with a Codicon direction indicator and `aria-sort` state; sorting is local and stable.
- **Trace:** Chronological user, assistant, reasoning, tool-call, and tool-result rows. Reasoning is collapsed by default; tool results use bounded, scrollable monospace blocks.
- **Failure inspector:** Begins with a destructive-wash notice distinguishing failures shown in the bounded event scan from the confirmed full-session total. SQL only prefilters candidate content; the shared Python signature confirms it before any count, while recorded Hermes finish/effect states remain authoritative.
- **Trust note:** Trace copy explicitly excludes system and scheduled-job scaffold prompts and states that content is secret-redacted and bounded to 6,000 characters per event.

### States

- **Loading, empty, error, unpriced, partial, and truncated states:** Use Hermes SDK states or quiet inline notices; uncertainty is stated directly rather than hidden.
- **Tools truncation:** Aggregate tool evidence scans the latest 50,000 assistant rows. When the cap is exceeded, the view states that the aggregate is truncated rather than implying complete history.
- **Log coverage:** Log-window labels use the earliest and latest parseable lines across the bounded source files, even when those lines cannot be attributed to a model. The in-memory parser cache retains only the ten most recently used paths.

## Do's and Don'ts

### Do:

- **Do** preserve Hermes SDK controls, theme variables, codicons, and native focus behavior.
- **Do** keep the context-appropriate four-signal strip, stable tabs, and dense evidence workspace visible in the first viewport.
- **Do** pair failure color with text or a codicon and expose bounded evidence for inspection.
- **Do** preserve known aggregate cost alongside an explicit unpriced-session count.
- **Do** exclude scheduled-job scaffold prompts and system prompts from trace presentation.
- **Do** use the 700px list→detail drill-in with Back for narrow Sessions views.

### Don't:

- **Don't** add a separate brand palette, external font, dashboard gradient, decorative shadow, or ornamental animation.
- **Don't** turn dense tables into card stacks when horizontal scrolling preserves comparison better.
- **Don't** present unavailable pricing as zero, available skills as invoked, or conservative failure signatures as certainty.
- **Don't** expose schedule prompts, secrets, or unbounded recorded content.
- **Don't** show list and detail together below the Hermes narrow threshold.
