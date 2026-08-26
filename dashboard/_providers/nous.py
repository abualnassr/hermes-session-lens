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

__all__ = [name for name in globals() if not name.startswith("__")]
