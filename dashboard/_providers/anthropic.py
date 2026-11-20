"""Anthropic Claude quota provider adapter.

Session Lens never holds a Claude credential (catalog rule 11). It asks
Hermes, through the published ``agent.account_usage.fetch_account_usage``,
for the plan limits of whichever Claude route this profile runs:

* a Claude subscription provider plugin (the Claude Subscription DirectSDK
  provider) answers with Claude Code's own plan-limit reading: the 5-hour
  session, the weekly windows and extra usage;
* Hermes' built-in Anthropic reader answers for a full Claude OAuth login.

A Claude setup token on its own has no reading any plugin may make
(Anthropic's usage endpoint refuses it, and its Admin API excludes
subscription usage). Then the card shows what Hermes recorded (tokens and
API-price cost over the trailing 5 hours and 7 days) and says so; the
desktop adds Hermes' own rate-limit state from its model picker.
"""

from __future__ import annotations

from .._common import *
from .._hermes_compat import *
from .shared import *

_ANTHROPIC_LABEL = "Anthropic Claude"
# Hermes provider ids that answer Claude plan limits, most specific first.
_ANTHROPIC_USAGE_ROUTES = (
    ("claude-subscription-directsdk-experimental", "Claude plan limits (via Claude Code)"),
    ("claude-subscription-directsdk", "Claude plan limits (via Claude Code)"),
    ("anthropic", "Claude account limits"),
)
_ANTHROPIC_SUBSCRIPTION_ENV = ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_TOKEN")
_ANTHROPIC_BILLING_KEYS = (
    "anthropic-oauth", "anthropic", "claude-subscription-directsdk-experimental", "claude-subscription-directsdk",
)
_ANTHROPIC_NO_ROUTE = (
    "Hermes could not read Claude plan limits for this profile: Anthropic gives them only to a full Claude login, "
    "never to a setup token. Shown instead: what Hermes recorded on Claude."
)


def _anthropic_credential_names() -> List[str]:
    """Which Anthropic credentials Hermes resolves for this profile (names only)."""
    return [name for name, _value in _secret_kind_present(*_ANTHROPIC_SUBSCRIPTION_ENV, "ANTHROPIC_API_KEY")]


def _snapshot_has_figures(snapshot: Any) -> bool:
    return bool(snapshot is not None and (getattr(snapshot, "windows", None) or getattr(snapshot, "details", None)))


def _collect_anthropic_usage() -> Dict[str, Any]:
    reasons: List[str] = []
    for route, plan in _ANTHROPIC_USAGE_ROUTES:
        snapshot = _hermes_account_usage(route)
        if snapshot is None:
            continue
        if _snapshot_has_figures(snapshot) and not getattr(snapshot, "unavailable_reason", None):
            card = _account_usage_payload("anthropic", snapshot)
            card["plan"] = card.get("plan") or plan
            card["usage_route"] = route
            return card
        reason = _clean_text(getattr(snapshot, "unavailable_reason", None), 200)
        if reason:
            reasons.append(reason)

    names = _anthropic_credential_names()
    recorded = _recorded_recently(_ANTHROPIC_BILLING_KEYS)
    if not names and not recorded:
        return _provider_payload(
            "anthropic", status="not_configured", message=_provider_not_configured_message("anthropic")
        )
    details: List[str] = []
    if names:
        details.append("Credentials Hermes holds: " + ", ".join(names))
    if reasons:
        details.append("Hermes' plan-limit reader said: " + reasons[0])
    card = _provider_payload(
        "anthropic",
        status="ok",
        plan="Recorded by Hermes",
        details=details,
        message=_ANTHROPIC_NO_ROUTE,
        partial=True,
    )
    # Filled per response from the profiles in scope (see _attach_local_usage_records).
    card["local_windows"] = True
    return card


register_provider(
    "anthropic", _ANTHROPIC_LABEL, "Hermes", _collect_anthropic_usage,
    probe=lambda: True,
    not_configured_message="Hermes holds no Anthropic credential and recorded no Claude use in this profile.",
    billing_keys=_ANTHROPIC_BILLING_KEYS,
    registry_ids=(
        "anthropic", "anthropic-oauth", "claude", "claude-subscription-directsdk-experimental",
        "claude-subscription-directsdk",
    ),
    hosts=(), via="hermes", order=20, module=__name__,
)

__all__ = [name for name in globals() if not name.startswith("__")]
