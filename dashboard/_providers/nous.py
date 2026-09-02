"""nous quota provider adapter."""

from __future__ import annotations

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
        return _account_usage_payload("nous", build_nous_credits_snapshot(account))
    except Exception as error:
        return _provider_payload("nous", status="unavailable", message=_provider_message(error))


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
