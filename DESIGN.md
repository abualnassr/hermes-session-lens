---
name: Hermes Session Lens
description: A native Hermes observatory for session evidence, accounting, and operational health.
colors:
  accent: "var(--ui-accent)"
  accent-soft: "color-mix(in srgb, var(--ui-accent) 14%, transparent)"
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

Hermes Session Lens is an **Operate** surface inside Hermes Desktop: one native observatory connecting session evidence to accounting and operational health. It inherits Hermes typography, controls, codicons, focus behavior, and light/dark theme roles; it does not establish a separate brand skin.

The interface is compact, quiet, and evidence-first. A user finds a session, verifies trace and accounting, then inspects runtime, profile, schedule, or Kanban state without leaving Hermes. Progressive disclosure carries the density, while explicit provenance, bounded excerpts, and read-only language make the trust boundary visible.

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

The page fills its Hermes route and uses a compact header, a four-signal strip, six stable view tabs, then a dense scroll-managed workspace. The first viewport establishes recorded cost, tokens, sessions, failures, and the active view before deeper evidence begins.

Sessions uses a wide master/detail split with a minimum 20rem list and 28rem detail, weighted 0.82/1.38. List and detail scroll independently. The detail proceeds through its own header, four metrics, Summary/Trace/Tools/Failures/Files/Ask Lens tabs, then a single content scroller.

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

- **Header:** Compact title and grounding sentence on the left; time range and explicit refresh on the right.
- **Signal strip:** Four equal accounting cells—Recorded cost, Tokens, Sessions, and Failures detected—with a small provenance/detail line below each value.
- **Partial-known cost:** When some sessions are unpriced, preserve the sum of known cost and show the unpriced session count; do not replace known accounting with a false zero.

### Navigation

- **View tabs:** Sessions, Overview, Operations, Tools, Skills, and System remain stable and horizontally scrollable. Active state uses stronger text and an accent underline.
- **Operations segments:** Health, Profiles, Schedules, and Kanban use the Hermes segmented control.
- **Detail segments:** Summary, Trace, Tools, Failures, Files, and Ask Lens preserve their order and surface counts when known.

### Session rows and status pills

- **Session row:** Title and outcome lead; timestamp/source, tokens, tool calls, cost, model, and optional bounded search snippet follow in descending emphasis. Selection uses the accent wash.
- **Pills:** Neutral, accent, and destructive tones are compact and fully rounded. Outcome pills pair label and codicon; failure counts remain visibly distinct.

### Evidence tables and trace

- **Tables:** Secondary-surface headers, one-pixel row separators, right-aligned numeric columns, and horizontal overflow preserve dense comparisons.
- **Trace:** Chronological user, assistant, reasoning, tool-call, and tool-result rows. Reasoning is collapsed by default; tool results use bounded, scrollable monospace blocks.
- **Failure inspector:** Begins with a destructive-wash notice stating how many failures are shown versus detected and directs the user to review recorded evidence before concluding.
- **Trust note:** Trace copy explicitly excludes system and scheduled-job scaffold prompts and states that content is secret-redacted and bounded to 6,000 characters per event.

### Ask Lens and states

- **Ask Lens:** Uses a native textarea, collapsed prompt preview, and primary/outline copy actions. Copy explains that prompt construction is local and opens a new Hermes chat only after successful clipboard write.
- **Loading, empty, error, unpriced, partial, and truncated states:** Use Hermes SDK states or quiet inline notices; uncertainty is stated directly rather than hidden.

## Do's and Don'ts

### Do:

- **Do** preserve Hermes SDK controls, theme variables, codicons, and native focus behavior.
- **Do** keep the four-signal strip, stable tabs, and dense evidence workspace visible in the first viewport.
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
