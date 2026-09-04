"""grok quota provider adapter."""

from __future__ import annotations

try:
    from .._common import *
    from .._hermes_compat import *
    from .shared import *
except ImportError:  # pragma: no cover
    from _common import *
    from _hermes_compat import *
    from _providers.shared import *

def _grok_windows_from_payloads(
    weekly_payload: Optional[Mapping[str, Any]],
    monthly_payload: Optional[Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    windows: List[Dict[str, Any]] = []
    weekly_config = weekly_payload.get("config") if isinstance(weekly_payload, Mapping) else None
    if isinstance(weekly_config, Mapping):
        used_pct = _usage_percent(weekly_config.get("creditUsagePercent"))
        period = weekly_config.get("currentPeriod")
        period_type = str(period.get("type") or "") if isinstance(period, Mapping) else ""
        reset_at = period.get("end") if isinstance(period, Mapping) else None
        reset_at = reset_at or weekly_config.get("billingPeriodEnd")
        detail = "Shared across Grok products"
        if used_pct is None and period_type == "USAGE_PERIOD_TYPE_WEEKLY" and reset_at:
            # xAI serialises this config from protobuf, which omits zero-valued
            # fields: no creditUsagePercent inside a confirmed weekly period means
            # 0% used, not unknown. xAI's own Grok Build client reads it the same
            # way (credit_usage_percent None -> 0.0), verified against the live
            # API on 2026-09-04 for a unified-billing account.
            used_pct = 0.0
            detail = "No usage recorded in this period yet · shared across Grok products"
        if used_pct is not None:
            windows.append(
                _usage_window(
                    "Weekly allowance",
                    used_percent=used_pct,
                    reset_at=reset_at,
                    detail=detail,
                )
            )
        prepaid = weekly_config.get("prepaidBalance")
        prepaid_value = _usage_number(prepaid.get("val")) if isinstance(prepaid, Mapping) else None
        if prepaid_value is not None and prepaid_value > 0:
            windows.append(
                _usage_window(
                    "Prepaid credits",
                    kind="balance",
                    remaining=prepaid_value / 100.0,
                    unit="credits",
                    detail="Purchased credits beyond the allowance",
                )
            )

    monthly_config = monthly_payload.get("config") if isinstance(monthly_payload, Mapping) else None
    if isinstance(monthly_config, Mapping):
        raw_limit = monthly_config.get("monthlyLimit")
        raw_used = monthly_config.get("used")
        limit_value = _usage_number(raw_limit.get("val")) if isinstance(raw_limit, Mapping) else None
        used_value = _usage_number(raw_used.get("val")) if isinstance(raw_used, Mapping) else None
        if limit_value is not None and limit_value > 0 and used_value is not None:
            limit = limit_value / 100.0
            used = max(0.0, used_value / 100.0)
            remaining = max(0.0, limit - used)
            windows.append(
                _usage_window(
                    "Extra usage credits",
                    kind="balance",
                    used_percent=(used / limit) * 100.0,
                    reset_at=monthly_config.get("billingPeriodEnd"),
                    limit=limit,
                    used=used,
                    remaining=remaining,
                    unit="credits",
                )
            )
    return windows


def _collect_grok_usage() -> Dict[str, Any]:
    # Billing response mapping is informed by the MIT-licensed
    # bnogalski/hermes-llm-quota project; see UPSTREAM.md.
    try:
        from hermes_cli.auth import resolve_xai_oauth_runtime_credentials

        credentials = resolve_xai_oauth_runtime_credentials(refresh_if_expiring=True) or {}
        token = str(credentials.get("api_key") or "").strip()
    except Exception as error:
        return _provider_payload("grok", status="unavailable", message=_provider_message(error))
    if not token:
        return _provider_payload("grok", status="not_configured", message="No Hermes xAI OAuth login was found.")

    try:
        import httpx
    except ImportError:
        return _provider_payload("grok", status="unavailable", message="Hermes HTTP client is unavailable.")

    url = "https://cli-chat-proxy.grok.com/v1/billing"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "User-Agent": "xai-grok-cli",
        "X-Xai-Token-Auth": "xai-grok-cli",
        "x-grok-client-identifier": "grok-cli",
        "x-grok-client-version": "0.2.103",
    }
    payloads: Dict[str, Mapping[str, Any]] = {}
    errors: List[str] = []
    statuses: List[int] = []
    try:
        with httpx.Client(timeout=AI_USAGE_PROVIDER_TIMEOUT_SECONDS) as client:
            for name, params in (("weekly", {"format": "credits"}), ("monthly", None)):
                try:
                    response = client.get(url, params=params, headers=headers)
                    statuses.append(response.status_code)
                    if response.status_code == 200:
                        payload = response.json()
                        if isinstance(payload, Mapping):
                            payloads[name] = payload
                        else:
                            errors.append(f"{name}: invalid response")
                    else:
                        errors.append(f"{name}: HTTP {response.status_code}")
                except Exception as error:
                    errors.append(f"{name}: {_provider_message(error)}")
    finally:
        token = ""
        headers.clear()

    if not payloads:
        if statuses and all(code == 401 for code in statuses):
            return _provider_payload("grok", status="expired", message="The xAI OAuth login has expired.")
        if statuses and all(code == 403 for code in statuses):
            return _provider_payload("grok", status="forbidden", message="xAI rejected access to account usage.")
        return _provider_payload(
            "grok",
            status="unavailable",
            message="; ".join(errors) or "Grok usage is temporarily unavailable.",
        )

    windows = _grok_windows_from_payloads(payloads.get("weekly"), payloads.get("monthly"))
    if not windows:
        return _provider_payload(
            "grok",
            status="unavailable",
            message="Grok returned neither a usage percentage nor a weekly billing period; the billing response shape may have changed.",
            partial=bool(errors),
        )
    return _provider_payload(
        "grok",
        status="ok",
        windows=windows,
        details=["Private xAI billing surface; response compatibility may change."],
        message="; ".join(errors) if errors else None,
        partial=bool(errors),
    )


def _probe_grok() -> bool:
    try:
        from hermes_cli.auth import AuthError, resolve_xai_oauth_runtime_credentials
    except ImportError:
        return True
    try:
        credentials = resolve_xai_oauth_runtime_credentials(refresh_if_expiring=False) or {}
    except AuthError:
        return False
    return bool(str(credentials.get("api_key") or "").strip())


register_provider(
    "grok", "Grok", "Hermes xAI OAuth", _collect_grok_usage,
    probe=_probe_grok,
    not_configured_message="No Hermes xAI OAuth login was found.",
    billing_keys=("xai-oauth",),
    registry_ids=("xai", "xai-oauth", "grok"),
    hosts=("cli-chat-proxy.grok.com",), order=60, module=__name__,
)

__all__ = [name for name in globals() if not name.startswith("__")]
