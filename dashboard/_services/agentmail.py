"""AgentMail cumulative usage counters."""

from __future__ import annotations

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


_AGENTMAIL_USAGE_TYPES = ("message_count", "thread_count", "inbox_count", "storage_bytes")


def _agentmail_payload(body: Any) -> Dict[str, Any]:
    if not isinstance(body, Mapping):
        return _service_payload("agentmail", status="unavailable", message="AgentMail returned an invalid response.")
    latest: Dict[str, float] = {}
    for usage_type in _AGENTMAIL_USAGE_TYPES:
        series = body.get(usage_type)
        if isinstance(series, list) and series:
            value = _usage_number((series[0] or {}).get("value") if isinstance(series[0], Mapping) else None)
            if value is not None:
                latest[usage_type] = value
    if not latest:
        return _service_payload("agentmail", status="unavailable", message="AgentMail returned no usage counters.")
    parts = []
    if "message_count" in latest:
        parts.append(f"{latest['message_count']:,.0f} messages")
    if "thread_count" in latest:
        parts.append(f"{latest['thread_count']:,.0f} threads")
    if "inbox_count" in latest:
        parts.append(f"{latest['inbox_count']:,.0f} inboxes")
    if "storage_bytes" in latest:
        parts.append(f"{latest['storage_bytes'] / (1024 * 1024):,.1f} MB stored")
    return _service_payload(
        "agentmail",
        status="ok",
        details=["Cumulative usage: " + " · ".join(parts), "AgentMail reports running totals, not a plan quota or balance."],
    )


def _collect_agentmail() -> Dict[str, Any]:
    key, _ = _service_secret("AGENTMAIL_API_KEY")
    if not key:
        return _service_payload("agentmail", status="not_configured", message="No AGENTMAIL_API_KEY is set in Hermes.")
    query = "&".join(f"usage_types={item}" for item in _AGENTMAIL_USAGE_TYPES) + "&limit=1&descending=true"
    headers = {"Authorization": f"Bearer {key}", "Accept": "application/json"}
    try:
        code, body, error = _service_get(f"https://api.agentmail.to/v0/metrics/usage?{query}", headers)
    finally:
        headers.clear()
    return _credential_status("agentmail", code, error) or _agentmail_payload(body)


register_service(
    "agentmail", "AgentMail", "Hermes .env key", collect=_collect_agentmail,
    env_keys=("AGENTMAIL_API_KEY",), mcp_hints=("agentmail",),
    hosts=("api.agentmail.to",), order=30, module=__name__,
)

__all__ = [name for name in globals() if not name.startswith("__")]
