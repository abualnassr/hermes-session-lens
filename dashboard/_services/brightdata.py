"""Bright Data account balance (needs an Admin-permission key)."""

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


def _brightdata_payload(body: Any) -> Dict[str, Any]:
    if not isinstance(body, Mapping):
        return _service_payload("brightdata", status="unavailable", message="Bright Data returned an invalid response.")
    balance = _usage_number(body.get("balance"))
    details = []
    for key, value in body.items():
        number = _usage_number(value)
        if key != "balance" and number is not None:
            details.append(f"{str(key).replace('_', ' ')}: {number:,.2f}")
    if balance is None:
        if not details:
            return _service_payload("brightdata", status="unavailable", message="Bright Data returned no balance figure.")
        return _service_payload("brightdata", status="ok", details=details)
    window = _usage_window("Account balance", kind="balance", remaining=balance, unit="USD")
    return _service_payload("brightdata", status="ok", windows=[window], details=details)


def _collect_brightdata() -> Dict[str, Any]:
    # A dedicated key first: the balance endpoint needs an Admin-permission
    # token, and the scraping MCP should keep its least-privileged User key.
    key, _ = _service_secret("BRIGHTDATA_API_KEY", "BRIGHT_DATA_API_KEY", "MCP_BRIGHTDATA_API_KEY")
    if not key:
        return _service_payload("brightdata", status="not_configured", message="No Bright Data API key is set in Hermes.")
    headers = {"Authorization": f"Bearer {key}", "Accept": "application/json"}
    try:
        code, body, error = _service_get("https://api.brightdata.com/customer/balance", headers)
    finally:
        headers.clear()
    if code == 403:
        return _service_payload(
            "brightdata",
            status="forbidden",
            message="This Bright Data key has User permission; the balance endpoint needs an Admin key. Create one under Account Settings → Users and API keys and set it as BRIGHTDATA_API_KEY in the Hermes .env (keep the MCP key as it is).",
        )
    return _credential_status("brightdata", code, error) or _brightdata_payload(body)


register_service(
    "brightdata", "Bright Data", "Hermes .env key", collect=_collect_brightdata,
    env_keys=("MCP_BRIGHTDATA_API_KEY", "BRIGHTDATA_API_KEY", "BRIGHT_DATA_API_KEY"), mcp_hints=("bright",),
    hosts=("api.brightdata.com",), order=40, module=__name__,
)

__all__ = [name for name in globals() if not name.startswith("__")]
