"""nous quota provider adapter."""

from __future__ import annotations

import re
import time

try:
    from .._common import *
    from .._hermes_compat import *
    from .shared import *
except ImportError:  # pragma: no cover
    from _common import *
    from _hermes_compat import *
    from _providers.shared import *

def _collect_nous_usage() -> Dict[str, Any]:
    try:
        from agent.account_usage import build_nous_credits_snapshot
        from hermes_cli.nous_account import get_nous_portal_account_info

        account = get_nous_portal_account_info(force_fresh=True)
        if account is None or not getattr(account, "logged_in", False):
            return _provider_payload("nous", status="not_configured", message="No Nous Portal login was found.")
        return _nous_with_balance(_account_usage_payload("nous", build_nous_credits_snapshot(account)), account)
    except Exception as error:
        return _provider_payload("nous", status="unavailable", message=_provider_message(error))


def _nous_with_balance(payload: Dict[str, Any], account: Any) -> Dict[str, Any]:
    """Give the card a headline balance window from the portal's credit figures.

    Hermes' snapshot lists the credits as detail lines and a raw top-up URL;
    the usable total is what the reader wants first, so it becomes a balance
    window like DeepSeek's, with the split and the renewal date as its
    detail. The URL line is muted by the payload builder.
    """
    if payload.get("status") != "ok":
        return payload
    access = getattr(account, "paid_service_access_info", None)
    total = _usage_number(getattr(access, "total_usable_credits", None)) if access is not None else None
    if total is None:
        return payload
    parts: List[str] = []
    subscription = _usage_number(getattr(access, "subscription_credits_remaining", None))
    purchased = _usage_number(getattr(access, "purchased_credits_remaining", None))
    if subscription is not None:
        parts.append(f"${subscription:,.2f} subscription")
    if purchased is not None:
        parts.append(f"${purchased:,.2f} top-up")
    sub = getattr(account, "subscription", None)
    renews = _usage_iso(getattr(sub, "current_period_end", None)) if sub is not None else None
    renew_epoch = _usage_reset_epoch(renews) if renews else None
    if renew_epoch:
        parts.append("renews " + time.strftime("%b %d, %Y", time.localtime(renew_epoch)))
    window = _usage_window(
        "Usable credits",
        kind="balance",
        remaining=total,
        unit="USD",
        detail=" · ".join(parts) or None,
    )
    payload["windows"] = [window] + [item for item in payload.get("windows", []) if item.get("id") != window["id"]]
    # The split now lives on the window; the magnitude lines would repeat it.
    payload["details"] = [
        item for item in payload.get("details", [])
        if not re.match(r"^(Subscription credits|Top-up credits|Total usable|Renews):", str(item))
    ]
    return payload


def _probe_nous() -> bool:
    try:
        from hermes_cli.nous_account import get_nous_portal_account_info
    except ImportError:
        return True
    account = get_nous_portal_account_info(force_fresh=False)
    return bool(getattr(account, "logged_in", False))


register_provider(
    "nous", "Nous Research Portal", "Hermes OAuth", _collect_nous_usage,
    probe=_probe_nous,
    not_configured_message="No Nous Portal login was found.",
    billing_keys=("nous",),
    registry_ids=("nous",),
    hosts=("portal.nousresearch.com",), via="hermes", order=30, module=__name__,
)

__all__ = [name for name in globals() if not name.startswith("__")]
