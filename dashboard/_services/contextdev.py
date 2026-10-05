"""Context.dev credit balance (read from the free logs endpoint's key metadata)."""

from __future__ import annotations

from .._common import *
from .._hermes_compat import *
from .._providers.shared import *
from .shared import *


def _contextdev_payload(body: Any) -> Dict[str, Any]:
    meta = body.get("key_metadata") if isinstance(body, Mapping) else None
    if not isinstance(meta, Mapping):
        return _service_payload("contextdev", status="unavailable", message="Context.dev returned no credit figures.")
    remaining = _usage_number(meta.get("credits_remaining"))
    if remaining is None:
        return _service_payload("contextdev", status="unavailable", message="Context.dev returned no credit figures.")
    windows = [_usage_window("Credits", kind="balance", remaining=remaining, unit="credits")]
    consumed = _usage_number(meta.get("credits_consumed"))
    details = [f"Used by this key: {consumed:,.0f} credits"] if consumed else []
    return _service_payload("contextdev", status="ok", windows=windows, details=details)


def _collect_contextdev() -> Dict[str, Any]:
    key, _ = _service_secret("CONTEXT_DEV_API_KEY")
    if not key:
        return _service_payload("contextdev", status="not_configured", message="No CONTEXT_DEV_API_KEY is set in Hermes.")
    headers = {"Authorization": f"Bearer {key}", "Accept": "application/json"}
    try:
        # Listing one log line costs no credits and carries the org balance.
        code, body, error = _service_get("https://api.context.dev/v1/org/logs?limit=1", headers)
    finally:
        headers.clear()
    if code == 403:
        return _service_payload(
            "contextdev", status="forbidden", message="This Context.dev key lacks the logs:read permission the balance read needs."
        )
    return _credential_status("contextdev", code, error) or _contextdev_payload(body)


register_service(
    "contextdev", "Context.dev", "Hermes .env key", collect=_collect_contextdev,
    env_keys=("CONTEXT_DEV_API_KEY",),
    mcp_hints=("context-dev", "context_dev", "contextdev"),
    hosts=("api.context.dev",), order=66, module=__name__,
)

__all__ = [name for name in globals() if not name.startswith("__")]
