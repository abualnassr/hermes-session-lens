"""deepseek quota provider adapter."""

from __future__ import annotations

try:
    from .._common import *
    from .._hermes_compat import *
    from .shared import *
except ImportError:  # pragma: no cover
    from _common import *
    from _hermes_compat import *
    from _providers.shared import *

def _deepseek_payload(payload: Mapping[str, Any]) -> Dict[str, Any]:
    windows: List[Dict[str, Any]] = []
    balances = payload.get("balance_infos")
    if isinstance(balances, list):
        for raw in balances:
            if not isinstance(raw, Mapping):
                continue
            currency = (_clean_text(raw.get("currency"), 12) or "USD").upper()
            total = _usage_number(raw.get("total_balance"))
            if total is None:
                continue
            breakdown = []
            granted = _usage_number(raw.get("granted_balance"))
            topped_up = _usage_number(raw.get("topped_up_balance"))
            if granted is not None:
                breakdown.append(f"{granted:,.2f} {currency} granted")
            if topped_up is not None:
                breakdown.append(f"{topped_up:,.2f} {currency} topped up")
            windows.append(
                _usage_window(
                    f"{currency} balance",
                    kind="balance",
                    remaining=total,
                    unit=currency,
                    detail=" · ".join(breakdown) or None,
                )
            )
    if not windows:
        return _provider_payload(
            "deepseek",
            status="unavailable",
            message="DeepSeek returned no recognized balance fields.",
        )
    available = payload.get("is_available")
    return _provider_payload(
        "deepseek",
        status="ok",
        windows=windows,
        message="DeepSeek reports insufficient balance for API calls." if available is False else None,
    )


def _collect_deepseek_usage() -> Dict[str, Any]:
    token = ""
    headers: Dict[str, str] = {}
    try:
        token, base_url = _resolve_hermes_api_key("deepseek")
        if not token:
            return _provider_payload(
                "deepseek", status="not_configured", message="No Hermes DeepSeek API key was found."
            )
        parsed = urlparse(base_url)
        if parsed.scheme != "https" or (parsed.hostname or "").lower() != "api.deepseek.com":
            return _provider_payload(
                "deepseek",
                status="unavailable",
                message="Balance checks require the official https://api.deepseek.com endpoint.",
            )
        import httpx

        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        response = httpx.get(
            "https://api.deepseek.com/user/balance",
            headers=headers,
            timeout=AI_USAGE_PROVIDER_TIMEOUT_SECONDS,
        )
        if response.status_code == 401:
            return _provider_payload("deepseek", status="expired", message="DeepSeek rejected the configured API key.")
        if response.status_code == 403:
            return _provider_payload("deepseek", status="forbidden", message="DeepSeek denied balance access.")
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, Mapping):
            return _provider_payload("deepseek", status="unavailable", message="DeepSeek returned an invalid response.")
        return _deepseek_payload(payload)
    except ImportError:
        return _provider_payload("deepseek", status="unavailable", message="Hermes HTTP client is unavailable.")
    except Exception as error:
        return _provider_payload("deepseek", status="unavailable", message=_provider_message(error))
    finally:
        token = ""
        headers.clear()


register_provider(
    "deepseek", "DeepSeek", "Hermes API key", _collect_deepseek_usage,
    not_configured_message="No Hermes DeepSeek API key was found.",
    billing_keys=("deepseek",),
    registry_ids=("deepseek",),
    hosts=("api.deepseek.com",), order=50, module=__name__,
)

__all__ = [name for name in globals() if not name.startswith("__")]
