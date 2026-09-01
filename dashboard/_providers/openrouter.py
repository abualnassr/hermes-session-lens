"""openrouter quota provider adapter."""

from __future__ import annotations

try:
    from .._common import *
    from .._hermes_compat import *
    from .shared import *
except ImportError:  # pragma: no cover
    from _common import *
    from _hermes_compat import *
    from _providers.shared import *

def _openrouter_payload(
    key_data: Optional[Mapping[str, Any]],
    credits_data: Optional[Mapping[str, Any]],
    *,
    partial_message: Optional[str] = None,
) -> Dict[str, Any]:
    windows: List[Dict[str, Any]] = []
    details: List[str] = []
    account_spend: Optional[Dict[str, Any]] = None
    if isinstance(key_data, Mapping):
        limit = _usage_number(key_data.get("limit"))
        remaining = _usage_number(key_data.get("limit_remaining"))
        if limit is not None and limit > 0 and remaining is not None and 0 <= remaining <= limit:
            used = limit - remaining
            reset = _clean_text(key_data.get("limit_reset"), 80)
            windows.append(
                _usage_window(
                    "API key limit",
                    used_percent=(used / limit) * 100.0,
                    detail=f"Resets {reset}" if reset else None,
                    limit=limit,
                    used=used,
                    remaining=remaining,
                    unit="USD",
                )
            )
        usage_parts = []
        spend: Dict[str, Any] = {}
        for key, label in (
            ("usage_daily", "today"),
            ("usage_weekly", "this week"),
            ("usage_monthly", "this month"),
        ):
            value = _usage_number(key_data.get(key))
            if value is not None:
                usage_parts.append(f"${value:,.2f} {label}")
                spend[key.replace("usage_", "")] = value
        if usage_parts:
            details.append("API key usage: " + " · ".join(usage_parts))
        if spend:
            # Numeric month-to-date spend feeds the budgets view; the detail
            # line above stays for people reading the card.
            account_spend = {**spend, "unit": "USD"}
    if isinstance(credits_data, Mapping):
        total = _usage_number(credits_data.get("total_credits"))
        used = _usage_number(credits_data.get("total_usage"))
        if total is not None and used is not None:
            remaining = max(0.0, total - used)
            windows.append(
                _usage_window(
                    "Account credits",
                    kind="balance",
                    used_percent=(used / total) * 100.0 if total > 0 else None,
                    limit=total,
                    used=used,
                    remaining=remaining,
                    unit="USD",
                )
            )
    if not windows and not details:
        return _provider_payload(
            "openrouter",
            status="unavailable",
            message=partial_message or "OpenRouter returned no recognized usage fields.",
        )
    payload = _provider_payload(
        "openrouter",
        status="ok",
        windows=windows,
        details=details,
        message=partial_message,
        partial=bool(partial_message),
    )
    if account_spend:
        payload["account_spend"] = account_spend
    return payload


def _collect_openrouter_usage() -> Dict[str, Any]:
    try:
        from hermes_cli.runtime_provider import resolve_runtime_provider

        runtime = resolve_runtime_provider(
            requested="openrouter",
            explicit_base_url=None,
            explicit_api_key=None,
        )
        token = str(runtime.get("api_key") or "").strip()
        base_url = str(runtime.get("base_url") or "https://openrouter.ai/api/v1").strip().rstrip("/")
    except Exception as error:
        return _provider_payload("openrouter", status="unavailable", message=_provider_message(error))
    if not token:
        return _provider_payload("openrouter", status="not_configured", message="No Hermes OpenRouter API key was found.")
    parsed = urlparse(base_url)
    if parsed.scheme != "https" or (parsed.hostname or "").lower() != "openrouter.ai":
        return _provider_payload(
            "openrouter",
            status="unavailable",
            message="Usage checks require the official https://openrouter.ai API endpoint.",
        )

    try:
        import httpx
    except ImportError:
        return _provider_payload("openrouter", status="unavailable", message="Hermes HTTP client is unavailable.")

    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    key_data: Optional[Mapping[str, Any]] = None
    credits_data: Optional[Mapping[str, Any]] = None
    errors: List[str] = []
    statuses: List[int] = []
    try:
        with httpx.Client(timeout=AI_USAGE_PROVIDER_TIMEOUT_SECONDS) as client:
            for name, path in (("key", "/key"), ("credits", "/credits")):
                try:
                    response = client.get(base_url + path, headers=headers)
                    statuses.append(response.status_code)
                    if response.status_code == 200:
                        payload = response.json()
                        data = payload.get("data") if isinstance(payload, Mapping) else None
                        if isinstance(data, Mapping):
                            if name == "key":
                                key_data = data
                            else:
                                credits_data = data
                        else:
                            errors.append(f"{name}: invalid response")
                    elif name == "credits" and response.status_code == 403:
                        errors.append("Account credits require an OpenRouter management key")
                    else:
                        errors.append(f"{name}: HTTP {response.status_code}")
                except Exception as error:
                    errors.append(f"{name}: {_provider_message(error)}")
    finally:
        token = ""
        headers.clear()

    if key_data is None and credits_data is None:
        if statuses and all(code == 401 for code in statuses):
            return _provider_payload("openrouter", status="expired", message="OpenRouter rejected the configured API key.")
        if statuses and all(code in {401, 403} for code in statuses):
            return _provider_payload("openrouter", status="forbidden", message="OpenRouter rejected account-usage access.")
    return _openrouter_payload(
        key_data,
        credits_data,
        partial_message="; ".join(errors) if errors else None,
    )

__all__ = [name for name in globals() if not name.startswith("__")]
