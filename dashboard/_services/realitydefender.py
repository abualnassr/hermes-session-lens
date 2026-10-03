"""Reality Defender scans this month (no quota API; the media list's total is the count)."""

from __future__ import annotations

import datetime as _dt

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

REALITY_DEFENDER_API = "https://api.prd.realitydefender.xyz"


def _realitydefender_payload(body: Any) -> Dict[str, Any]:
    total = _usage_number(body.get("totalItems")) if isinstance(body, Mapping) else None
    if total is None:
        return _service_payload("realitydefender", status="unavailable", message="Reality Defender returned no scan count.")
    # Reality Defender exposes no quota; the scan count is the readable figure.
    return _service_payload(
        "realitydefender", status="ok", details=[f"{total:,.0f} scan{'' if total == 1 else 's'} this month"]
    )


def _collect_realitydefender() -> Dict[str, Any]:
    key, _ = _service_secret("RealityDefender_API_KEY", "REALITY_DEFENDER_API_KEY", "REALITYDEFENDER_API_KEY")
    if not key:
        return _service_payload("realitydefender", status="not_configured", message="No Reality Defender API key is set in Hermes.")
    today = _dt.date.today()
    url = (
        f"{REALITY_DEFENDER_API}/api/v2/media/users/pages/0"
        f"?startDate={today.replace(day=1).isoformat()}&endDate={today.isoformat()}&size=1"
    )
    headers = {"X-API-KEY": key, "Accept": "application/json"}
    try:
        code, body, error = _service_get(url, headers)
    finally:
        headers.clear()
    return _credential_status("realitydefender", code, error) or _realitydefender_payload(body)


register_service(
    "realitydefender", "Reality Defender", "Hermes .env key", collect=_collect_realitydefender,
    env_keys=("RealityDefender_API_KEY".upper(), "REALITY_DEFENDER_API_KEY"),
    mcp_hints=("realitydefender", "reality-defender"),
    hosts=("api.prd.realitydefender.xyz",), order=68, module=__name__,
)

__all__ = [name for name in globals() if not name.startswith("__")]
