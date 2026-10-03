"""Twilio account balance and this month's spend."""

from __future__ import annotations

import base64

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

TWILIO_API = "https://api.twilio.com/2010-04-01/Accounts"


def _twilio_payload(balance: Any, month: Any) -> Dict[str, Any]:
    if not isinstance(balance, Mapping):
        return _service_payload("twilio", status="unavailable", message="Twilio returned an invalid balance response.")
    remaining = _usage_number(balance.get("balance"))
    if remaining is None:
        return _service_payload("twilio", status="unavailable", message="Twilio returned no balance figure.")
    currency = str(balance.get("currency") or "USD").upper()
    windows = [_usage_window("Account balance", kind="balance", remaining=remaining, unit=currency)]
    details: List[str] = []
    records = month.get("usage_records") if isinstance(month, Mapping) else None
    record = next((item for item in records or [] if isinstance(item, Mapping)), None)
    if record is not None:
        spent = _usage_number(record.get("price"))
        if spent is not None:
            unit = str(record.get("price_unit") or currency).upper()
            details.append(f"Spent this month: {spent:,.2f} {unit}")
    return _service_payload("twilio", status="ok", windows=windows, details=details)


def _collect_twilio() -> Dict[str, Any]:
    sid, _ = _service_secret("TWILIO_ACCOUNT_SID")
    token, _ = _service_secret("TWILIO_AUTH_TOKEN")
    if not sid or not token:
        return _service_payload(
            "twilio", status="not_configured", message="TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN are both needed."
        )
    basic = base64.b64encode(f"{sid}:{token}".encode("utf-8")).decode("ascii")
    headers = {"Authorization": f"Basic {basic}", "Accept": "application/json"}
    try:
        code, balance, error = _service_get(f"{TWILIO_API}/{sid}/Balance.json", headers)
        status = _credential_status("twilio", code, error)
        if status is not None:
            return status
        # Spend is a nice-to-have: a failure here never hides the balance.
        month_code, month, month_error = _service_get(
            f"{TWILIO_API}/{sid}/Usage/Records/ThisMonth.json?Category=totalprice", headers
        )
        if month_error or month_code != 200:
            month = None
    finally:
        headers.clear()
        basic = ""
    return _twilio_payload(balance, month)


register_service(
    "twilio", "Twilio", "Hermes .env key", collect=_collect_twilio,
    env_keys=("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_PHONE_NUMBER", "TWILIO_PHONE_NUMBER_SID"),
    mcp_hints=("twilio",),
    hosts=("api.twilio.com",), order=60, module=__name__,
)

__all__ = [name for name in globals() if not name.startswith("__")]
