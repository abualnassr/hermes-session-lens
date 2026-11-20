"""Grok (xAI) quota provider adapter.

Hermes has no published route for an xAI sign-in's billing, and catalog
rule 11 forbids a plugin from holding or refreshing that login itself, so
Session Lens asks Hermes' account-usage API first (it answers as soon as
Hermes or a provider plugin ships an xAI reader) and otherwise shows what
Hermes recorded on Grok: tokens and API-price cost over the trailing 5 hours
and 7 days. The desktop adds Hermes' own rate-limit state from its model
picker.
"""

from __future__ import annotations

from .._common import *
from .._hermes_compat import *
from .shared import *

_GROK_BILLING_KEYS = ("xai-oauth", "xai")
_GROK_NO_ROUTE = (
    "Hermes has no reader for an xAI sign-in's allowance yet, and a plugin may not read the login itself. "
    "Shown instead: what Hermes recorded on Grok."
)


def _collect_grok_usage() -> Dict[str, Any]:
    for route in ("xai-oauth", "xai"):
        snapshot = _hermes_account_usage(route)
        if snapshot is not None and (getattr(snapshot, "windows", None) or getattr(snapshot, "details", None)):
            card = _account_usage_payload("grok", snapshot)
            card["usage_route"] = route
            return card
    if not _recorded_recently(_GROK_BILLING_KEYS):
        return _provider_payload("grok", status="not_configured", message=_provider_not_configured_message("grok"))
    card = _provider_payload(
        "grok",
        status="ok",
        plan="Recorded by Hermes",
        message=_GROK_NO_ROUTE,
        partial=True,
    )
    # Filled per response from the profiles in scope (see _attach_local_usage_records).
    card["local_windows"] = True
    return card


register_provider(
    "grok", "Grok", "Hermes", _collect_grok_usage,
    probe=lambda: True,
    not_configured_message="Hermes recorded no Grok use in this profile in the last 30 days.",
    billing_keys=_GROK_BILLING_KEYS,
    registry_ids=("xai", "xai-oauth", "grok"),
    hosts=(), via="hermes", order=60, module=__name__,
)

__all__ = [name for name in globals() if not name.startswith("__")]
