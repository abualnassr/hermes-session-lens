"""Voximplant account balance (GetAccountInfo with a service-account JWT)."""

from __future__ import annotations

import time

from .._common import *
from .._hermes_compat import *
from .._providers.shared import *
from .shared import *

VOXIMPLANT_API = "https://api.voximplant.com/platform_api/GetAccountInfo/"


def _voximplant_token(account_id: str, key_id: str, private_key: str) -> str:
    """A short-lived service-account JWT, built the way Voximplant's own
    apiclient-python does: RS256, iss = account id, kid = key id."""
    import jwt  # PyJWT ships with Hermes

    now = int(time.time())
    pem = private_key.replace("\\n", "\n").strip()
    token = jwt.encode(
        {"iss": str(account_id), "iat": now - 5, "exp": now + 60}, pem, algorithm="RS256", headers={"kid": key_id}
    )
    return token.decode("ascii") if isinstance(token, bytes) else str(token)


def _voximplant_payload(body: Any) -> Dict[str, Any]:
    if not isinstance(body, Mapping):
        return _service_payload("voximplant", status="unavailable", message="Voximplant returned an invalid response.")
    error = body.get("error")
    if isinstance(error, Mapping):
        message = _clean_text(error.get("msg"), 200) or "Voximplant reported an error."
        status = "expired" if str(error.get("code")) in {"100", "101", "102"} else "unavailable"
        return _service_payload("voximplant", status=status, message=message)
    info = body.get("result") if isinstance(body.get("result"), Mapping) else {}
    balance = _usage_number(info.get("live_balance", info.get("balance")))
    if balance is None:
        return _service_payload("voximplant", status="unavailable", message="Voximplant returned no balance figure.")
    currency = str(info.get("currency") or "USD").upper()
    windows = [_usage_window("Account balance", kind="balance", remaining=balance, unit=currency)]
    details: List[str] = []
    credit = _usage_number(info.get("credit_limit"))
    if credit:
        details.append(f"Credit limit: {credit:,.2f} {currency}")
    if info.get("frozen") is True:
        details.append("Account frozen")
    return _service_payload("voximplant", status="ok", windows=windows, details=details)


def _collect_voximplant() -> Dict[str, Any]:
    account_id, _ = _service_secret("VOXIMPLANT_ACCOUNT_ID")
    key_id, _ = _service_secret("VOXIMPLANT_KEY_ID")
    private_key, _ = _service_secret("VOXIMPLANT_PRIVATE_KEY")
    if not (account_id and key_id and private_key):
        return _service_payload(
            "voximplant",
            status="not_configured",
            message="VOXIMPLANT_ACCOUNT_ID, VOXIMPLANT_KEY_ID and VOXIMPLANT_PRIVATE_KEY are all needed.",
        )
    try:
        token = _voximplant_token(account_id, key_id, private_key)
    except Exception as error:
        return _service_payload("voximplant", status="unavailable", message=f"Could not sign the Voximplant request: {_provider_message(error)}")
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    try:
        code, body, error = _service_post(VOXIMPLANT_API, headers, form={"return_live_balance": "true"})
    finally:
        headers.clear()
        token = ""
    return _credential_status("voximplant", code, error) or _voximplant_payload(body)


register_service(
    "voximplant", "Voximplant", "Hermes .env service-account key", collect=_collect_voximplant,
    env_keys=("VOXIMPLANT_ACCOUNT_ID", "VOXIMPLANT_KEY_ID", "VOXIMPLANT_PRIVATE_KEY"),
    mcp_hints=("voximplant",),
    hosts=("api.voximplant.com",), order=65, module=__name__,
)

__all__ = [name for name in globals() if not name.startswith("__")]
