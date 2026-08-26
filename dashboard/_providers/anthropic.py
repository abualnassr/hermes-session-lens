"""anthropic quota provider adapter."""

from __future__ import annotations

try:
    from .._common import *
    from .._hermes_compat import *
    from .shared import *
except ImportError:  # pragma: no cover
    from _common import *
    from _hermes_compat import *
    from _providers.shared import *

def _collect_anthropic_usage() -> Dict[str, Any]:
    token = ""
    try:
        from agent.account_usage import fetch_account_usage

        token, oauth = _resolve_anthropic_oauth()
        if not token:
            return _provider_payload(
                "anthropic",
                status="not_configured",
                message="No Hermes Anthropic OAuth login was found.",
            )
        if not oauth:
            return _provider_payload(
                "anthropic",
                status="not_configured",
                message="Anthropic account limits require an OAuth-backed Claude account, not an API key.",
            )
        snapshot = fetch_account_usage("anthropic")
        if snapshot is None:
            return _provider_payload(
                "anthropic",
                status="unavailable",
                message="Anthropic did not return account-usage data for the current OAuth login.",
            )
        return _account_usage_payload("anthropic", snapshot)
    except Exception as error:
        return _provider_payload("anthropic", status="unavailable", message=_provider_message(error))
    finally:
        token = ""

__all__ = [name for name in globals() if not name.startswith("__")]
