"""Firecrawl credit usage per key (cloud only; extra FIRECRAWL_API_KEY_* keys become extra cards)."""

from __future__ import annotations

import os

try:
    from .._common import *
    from .._hermes_compat import *
    from .._providers.shared import *
    from .shared import *
except ImportError:  # pragma: no cover
    from _common import *
    from _hermes_compat import *
    from _providers.shared import *
    from _services.shared import *


_FIRECRAWL_CLOUD_HOST = "api.firecrawl.dev"


def _firecrawl_payload(body: Any, account: Optional[str] = None) -> Dict[str, Any]:
    data = body.get("data") if isinstance(body, Mapping) else None
    if not isinstance(data, Mapping):
        return _service_payload("firecrawl", status="unavailable", message="Firecrawl returned no credit data.", account=account)
    remaining = _usage_number(data.get("remainingCredits", data.get("remaining_credits")))
    plan = _usage_number(data.get("planCredits", data.get("plan_credits")))
    period_end = data.get("billingPeriodEnd", data.get("billing_period_end"))
    if remaining is None:
        return _service_payload("firecrawl", status="unavailable", message="Firecrawl returned no remaining-credit figure.", account=account)
    detail_parts = []
    used = None
    used_percent = None
    if plan:
        detail_parts.append(f"Plan {plan:,.0f} credits per period")
        if remaining <= plan:
            used = plan - remaining
            used_percent = (used / plan) * 100.0
        else:
            detail_parts.append("balance includes top-ups beyond the plan")
    window = _usage_window(
        "Credits",
        kind="balance",
        remaining=remaining,
        limit=plan,
        used=used,
        used_percent=used_percent,
        unit="credits",
        reset_at=period_end,
        detail=" · ".join(detail_parts) or None,
    )
    return _service_payload("firecrawl", status="ok", windows=[window], account=account)


def _collect_firecrawl() -> Dict[str, Any]:
    base = str(os.environ.get("FIRECRAWL_API_URL") or "https://api.firecrawl.dev").strip().rstrip("/")
    host = (urlparse(base).hostname or "").lower()
    if host and host != _FIRECRAWL_CLOUD_HOST:
        return _service_payload(
            "firecrawl",
            status="unavailable",
            message="FIRECRAWL_API_URL points at a self-hosted endpoint; credit usage exists only on Firecrawl cloud.",
        )
    primary, _name = _service_secret("FIRECRAWL_API_KEY")
    if not primary:
        return _service_payload("firecrawl", status="not_configured", message="No FIRECRAWL_API_KEY is set in Hermes.")

    def fetch(key: str, account: Optional[str]) -> Dict[str, Any]:
        headers = {"Authorization": f"Bearer {key}", "Accept": "application/json"}
        try:
            code, body, error = _service_get(f"https://{_FIRECRAWL_CLOUD_HOST}/v2/team/credit-usage", headers)
        finally:
            headers.clear()
        return _credential_status("firecrawl", code, error, account) or _firecrawl_payload(body, account)

    result = fetch(primary, None)
    extras: List[Dict[str, Any]] = []
    seen = {primary}
    for name in _dotenv_key_names(_hermes_home() / ".env"):
        service_id, suffix = _service_for_env_key(name)
        if service_id != "firecrawl" or not suffix:
            continue
        key, _ = _service_secret(name)
        if not key or key in seen:
            continue
        seen.add(key)
        extras.append(fetch(key, suffix))
        if len(extras) >= 4:
            break
    if extras:
        result["extra_accounts"] = extras
    return result


register_service(
    "firecrawl", "Firecrawl", "Hermes .env key", collect=_collect_firecrawl,
    env_keys=("FIRECRAWL_API_KEY",), mcp_hints=("firecrawl",),
    hosts=("api.firecrawl.dev",), order=10, module=__name__,
)

__all__ = [name for name in globals() if not name.startswith("__")]
