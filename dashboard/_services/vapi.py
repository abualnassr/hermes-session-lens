"""Vapi call spend this month (Vapi exposes no balance; its analytics query sums call cost)."""

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


def _vapi_month_query(now: Optional[_dt.datetime] = None) -> Dict[str, Any]:
    now = now or _dt.datetime.now(_dt.timezone.utc)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return {
        "queries": [
            {
                "table": "call",
                "name": "spend",
                "timeRange": {"start": start.isoformat(), "end": now.isoformat(), "step": "month", "timezone": "UTC"},
                "operations": [{"operation": "sum", "column": "cost"}, {"operation": "count", "column": "id"}],
            }
        ]
    }


def _vapi_payload(body: Any) -> Dict[str, Any]:
    results = body if isinstance(body, list) else []
    query = next((item for item in results if isinstance(item, Mapping) and item.get("name") == "spend"), None)
    rows = query.get("result") if isinstance(query, Mapping) else None
    if not isinstance(rows, list):
        return _service_payload("vapi", status="unavailable", message="Vapi returned no analytics result.")
    spent = sum(_usage_number(row.get("sumCost")) or 0.0 for row in rows if isinstance(row, Mapping))
    calls = sum(_usage_number(row.get("countId")) or 0.0 for row in rows if isinstance(row, Mapping))
    # Vapi exposes no balance; call spend this month is the readable figure.
    details = [f"${spent:,.2f} call spend this month across {calls:,.0f} call{'' if calls == 1 else 's'}"]
    return _service_payload("vapi", status="ok", details=details)


def _collect_vapi() -> Dict[str, Any]:
    key, _ = _service_secret("VAPI_API_KEY")
    if not key:
        return _service_payload("vapi", status="not_configured", message="No VAPI_API_KEY is set in Hermes.")
    headers = {"Authorization": f"Bearer {key}", "Accept": "application/json"}
    try:
        code, body, error = _service_post("https://api.vapi.ai/analytics", headers, json_body=_vapi_month_query())
    finally:
        headers.clear()
    if code == 201:
        code = 200
    return _credential_status("vapi", code, error) or _vapi_payload(body)


register_service(
    "vapi", "Vapi", "Hermes .env key", collect=_collect_vapi,
    env_keys=("VAPI_API_KEY",),
    mcp_hints=("vapi",),
    hosts=("api.vapi.ai",), order=67, module=__name__,
)

__all__ = [name for name in globals() if not name.startswith("__")]
