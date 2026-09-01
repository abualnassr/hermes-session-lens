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


_ANTHROPIC_MAX_EXTRA_ACCOUNTS = 4


def _anthropic_account_cards(primary_tokens: List[str]) -> List[Dict[str, Any]]:
    """One card per additional pooled Claude login, resetwatch-style.

    Skips any account whose token the primary card already consumed, so the
    same login never renders twice. Each card keeps its own status — an
    expired second account shows as expired without hiding the healthy one.
    """
    cards: List[Dict[str, Any]] = []
    try:
        accounts = _anthropic_pool_oauth_accounts()
    except Exception:
        return cards
    index = 0
    for account in accounts:
        token = str(account.get("token") or "")
        if not token or token in primary_tokens:
            continue
        index += 1
        if index > _ANTHROPIC_MAX_EXTRA_ACCOUNTS:
            break
        card = _collect_anthropic_direct(token)
        label = str(account.get("label") or "").strip() or f"account {index}"
        card["provider"] = f"anthropic:{index}"
        card["base_provider"] = "anthropic"
        card["account"] = label
        card["account_extra"] = True
        card["label"] = f"{card.get('label') or 'Anthropic Claude'} · {label}"
        cards.append(card)
    return cards


def _collect_anthropic_usage() -> Dict[str, Any]:
    result = _collect_anthropic_primary()
    extras = _anthropic_account_cards(list(result.pop("_tokens_used", []) or []))
    if extras:
        result["extra_accounts"] = extras
    return result


def _collect_anthropic_primary() -> Dict[str, Any]:
    token = ""
    tried_tokens: List[str] = []

    def _with_tokens(result: Dict[str, Any]) -> Dict[str, Any]:
        used = [item for item in [token, *tried_tokens] if item]
        result["_tokens_used"] = used
        return result

    try:
        token, oauth = _resolve_anthropic_oauth()
        if token and oauth:
            from agent.account_usage import fetch_account_usage

            snapshot = fetch_account_usage("anthropic")
            if snapshot is None:
                return _with_tokens(
                    _provider_payload(
                        "anthropic",
                        status="unavailable",
                        message="Anthropic did not return account-usage data for the current OAuth login.",
                    )
                )
            return _with_tokens(_account_usage_payload("anthropic", snapshot))
        # The resolver returned an API key (or nothing): an explicit
        # ANTHROPIC_API_KEY shadows saved OAuth logins in Hermes, but account
        # limits only exist for OAuth — so read stored logins directly.
        # Claude Code's own token first: it is refreshed by everyday Claude
        # Code use, while an unused Hermes pool login goes stale.
        fallback_results: List[Dict[str, Any]] = []
        for resolve in (_resolve_anthropic_claude_code_oauth, _resolve_anthropic_pool_oauth):
            fallback_token = resolve()
            if not fallback_token or fallback_token in tried_tokens:
                continue
            tried_tokens.append(fallback_token)
            result = _collect_anthropic_direct(fallback_token)
            if result.get("status") == "ok":
                return _with_tokens(result)
            fallback_results.append(result)
        if fallback_results:
            return _with_tokens(fallback_results[0])
        if token:
            return _with_tokens(
                _provider_payload(
                    "anthropic",
                    status="not_configured",
                    message=(
                        "Hermes resolves an Anthropic API key, and account limits require an "
                        "OAuth-backed Claude account. Sign in with Claude in Hermes to add one."
                    ),
                )
            )
        return _with_tokens(
            _provider_payload(
                "anthropic",
                status="not_configured",
                message="No Hermes Anthropic OAuth login was found.",
            )
        )
    except Exception as error:
        return _with_tokens(
            _provider_payload("anthropic", status="unavailable", message=_provider_message(error))
        )
    finally:
        token = ""
        tried_tokens.clear()

__all__ = [name for name in globals() if not name.startswith("__")]
