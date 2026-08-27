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

_ANTHROPIC_USAGE_WINDOWS = (
    ("five_hour", "Current session"),
    ("seven_day", "Current week"),
    ("seven_day_opus", "Opus week"),
    ("seven_day_sonnet", "Sonnet week"),
)


def _anthropic_usage_payload(payload: Mapping[str, Any]) -> Dict[str, Any]:
    """Normalize the api.anthropic.com/api/oauth/usage response.

    Mirrors Hermes' agent/account_usage.py parsing so the direct-fetch path
    below reports the same windows as the delegated path.
    """
    windows: List[Dict[str, Any]] = []
    for key, label in _ANTHROPIC_USAGE_WINDOWS:
        raw = payload.get(key)
        if not isinstance(raw, Mapping):
            continue
        util = _usage_number(raw.get("utilization"))
        if util is None:
            continue
        used = util * 100 if util <= 1 else util
        windows.append(_usage_window(label, used_percent=used, reset_at=raw.get("resets_at")))
    details: List[str] = []
    extra = payload.get("extra_usage")
    if isinstance(extra, Mapping) and extra.get("is_enabled"):
        used_credits = _usage_number(extra.get("used_credits"))
        monthly_limit = _usage_number(extra.get("monthly_limit"))
        currency = _clean_text(extra.get("currency"), 12) or "USD"
        if used_credits is not None and monthly_limit is not None:
            details.append(f"Extra usage: {used_credits:.2f} / {monthly_limit:.2f} {currency}")
    if not windows and not details:
        return _provider_payload(
            "anthropic",
            status="unavailable",
            message="Anthropic returned no recognized usage windows.",
        )
    return _provider_payload("anthropic", status="ok", windows=windows, details=details)


def _collect_anthropic_direct(token: str) -> Dict[str, Any]:
    """Fetch account usage with a pool OAuth token the resolver would shadow."""
    headers: Dict[str, str] = {}
    try:
        import httpx

        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "anthropic-beta": "oauth-2025-04-20",
            "User-Agent": "claude-code/2.1.0",
        }
        response = httpx.get(
            "https://api.anthropic.com/api/oauth/usage",
            headers=headers,
            timeout=AI_USAGE_PROVIDER_TIMEOUT_SECONDS,
        )
        if response.status_code == 401:
            return _provider_payload(
                "anthropic",
                status="expired",
                message="The saved Anthropic OAuth login was rejected (token expired?). Sign in with Claude in Hermes to renew it.",
            )
        if response.status_code == 403:
            return _provider_payload(
                "anthropic", status="forbidden", message="Anthropic denied access to account usage for the saved login."
            )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, Mapping):
            return _provider_payload("anthropic", status="unavailable", message="Anthropic returned an invalid response.")
        return _anthropic_usage_payload(payload)
    except ImportError:
        return _provider_payload("anthropic", status="unavailable", message="Hermes HTTP client is unavailable.")
    except Exception as error:
        return _provider_payload("anthropic", status="unavailable", message=_provider_message(error))
    finally:
        headers.clear()


def _collect_anthropic_usage() -> Dict[str, Any]:
    token = ""
    try:
        token, oauth = _resolve_anthropic_oauth()
        if token and oauth:
            from agent.account_usage import fetch_account_usage

            snapshot = fetch_account_usage("anthropic")
            if snapshot is None:
                return _provider_payload(
                    "anthropic",
                    status="unavailable",
                    message="Anthropic did not return account-usage data for the current OAuth login.",
                )
            return _account_usage_payload("anthropic", snapshot)
        # The resolver returned an API key (or nothing): an explicit
        # ANTHROPIC_API_KEY shadows saved OAuth logins in Hermes, but account
        # limits only exist for OAuth — so read the pool login directly.
        pool_token = _resolve_anthropic_pool_oauth()
        if pool_token:
            return _collect_anthropic_direct(pool_token)
        if token:
            return _provider_payload(
                "anthropic",
                status="not_configured",
                message=(
                    "Hermes resolves an Anthropic API key, and account limits require an "
                    "OAuth-backed Claude account. Sign in with Claude in Hermes to add one."
                ),
            )
        return _provider_payload(
            "anthropic",
            status="not_configured",
            message="No Hermes Anthropic OAuth login was found.",
        )
    except Exception as error:
        return _provider_payload("anthropic", status="unavailable", message=_provider_message(error))
    finally:
        token = ""

__all__ = [name for name in globals() if not name.startswith("__")]
