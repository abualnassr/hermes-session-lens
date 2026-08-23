# Design Direction

## Inherited world

Hermes Session Lens is an **Operate** surface inside Hermes Desktop. It inherits Hermes' typography, native controls, spacing rhythm, codicons, focus behavior, and every `--ui-*` theme variable. It does not introduce a separate brand skin.

## Structure

The selected structure is a telemetry observatory: a compact health strip establishes the current accounting picture, then five stable views let the user move from fleet-level signal to session evidence.

1. **Sessions** — default, failure-first master/detail workspace.
2. **Overview** — time, model, source, token, and cost patterns.
3. **Tools** — call volume and failure rate by tool.
4. **Skills** — recorded `skill_view` and `skill_manage` invocations.
5. **System** — data freshness, schema, database, and privacy posture.

The session detail progressively reveals Summary, Tools, Failures, Files, and Ask Lens. The left list remains scannable; the right detail pane carries density.

## Visual rules

- Restrained color strategy: Hermes neutrals plus the active theme accent.
- Failure status uses Hermes destructive tokens and always includes text or an icon; color is never the only signal.
- Token, cost, and count values use tabular numerals.
- Containers are separated with `--ui-stroke-secondary`, not decorative shadows.
- No charts rely on canvas; lightweight bars remain legible when panes resize.
- No hardcoded colors, external fonts, decorative animation, or dashboard-style gradients.

## Responsive behavior

At wide widths, Sessions is a master/detail split. In a narrow Hermes window, it becomes a vertical flow with the list above the detail. Aggregate tables scroll horizontally only when their data cannot collapse responsibly.

## Interaction rules

- Default ordering is failure-first, then recent activity.
- Search uses Hermes FTS and exposes a bounded matching snippet.
- Refresh is explicit, with a quiet 60-second data heartbeat through React Query.
- Ask Lens copies a grounded prompt and opens a fresh Hermes chat; it never sends session content to a third party.
- Empty, loading, backend-disabled, unpriced, and truncated states are explicit.

