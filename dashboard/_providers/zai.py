"""zai quota provider adapter."""

from __future__ import annotations

try:
    from .._common import *
    from .._hermes_compat import *
    from .shared import *
except ImportError:  # pragma: no cover
    from _common import *
    from _hermes_compat import *
    from _providers.shared import *

def _zai_limits(payload: Mapping[str, Any]) -> List[Mapping[str, Any]]:
    candidates: List[Any] = [payload, payload.get("data")]
    data = payload.get("data")
    if isinstance(data, list):
        candidates.extend(data)
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            continue
        limits = candidate.get("limits")
        if isinstance(limits, list):
            return [item for item in limits if isinstance(item, Mapping)]
    return []


def _zai_payload(payload: Mapping[str, Any]) -> Dict[str, Any]:
    code = _usage_number(payload.get("code"))
    if code is not None and code != 200:
        msg = _clean_text(payload.get("msg") or payload.get("message"), 160)
        # "当前用户不存在coding plan" — the key is valid but the account has no
        # Coding Plan, and Z.AI's usage API only reports Coding Plan quotas.
        # That is "nothing to monitor", not a fault.
        if msg and "coding plan" in msg.lower():
            return _provider_payload(
                "zai",
                status="not_configured",
                message=(
                    "Z.AI reports this account has no Coding Plan subscription; "
                    "its usage API only exposes Coding Plan quotas, so there is nothing to monitor."
                ),
            )
        return _provider_payload(
            "zai",
            status="unavailable",
            message=f"Z.AI usage service returned code {code:g}." + (f" {msg}" if msg else ""),
        )
    windows: List[Dict[str, Any]] = []
    for raw in _zai_limits(payload):
        if str(raw.get("type") or "").upper() != "TOKENS_LIMIT":
            continue
        unit = _usage_number(raw.get("unit"))
        number = _usage_number(raw.get("number"))
        if unit == 3 and number == 5:
            label = "5-hour rolling"
        elif unit == 6 and number == 1:
            label = "Weekly quota"
        else:
            continue
        window = _amount_quota_window(
            label,
            raw,
            unit="tokens",
            limit_keys=("usage", "limit"),
            used_keys=("currentValue", "used"),
        )
        if window:
            windows.append(window)
    if not windows:
        return _provider_payload(
            "zai",
            status="unavailable",
            message="Z.AI returned no recognized five-hour or weekly token windows.",
        )
    return _provider_payload(
        "zai",
        status="ok",
        windows=windows,
    )


def _collect_zai_usage() -> Dict[str, Any]:
    token = ""
    headers: Dict[str, str] = {}
    try:
        token, base_url = _resolve_hermes_api_key("zai")
        if not token:
            return _provider_payload(
                "zai", status="not_configured", message="No Hermes Z.AI API key was found."
            )
        parsed = urlparse(base_url)
        if parsed.scheme != "https" or (parsed.hostname or "").lower() != "api.z.ai":
            return _provider_payload(
                "zai",
                status="not_configured",
                message="Session Lens currently supports only international Z.AI Coding Plan keys on api.z.ai.",
            )
        import httpx

        url = "https://api.z.ai/api/monitor/usage/quota/limit"
        response = None
        with httpx.Client(timeout=AI_USAGE_PROVIDER_TIMEOUT_SECONDS) as client:
            for authorization in (token, f"Bearer {token}"):
                headers = {"Authorization": authorization, "Accept": "application/json"}
                response = client.get(url, headers=headers)
                if response.status_code not in {401, 403}:
                    break
        if response is None:
            return _provider_payload("zai", status="unavailable", message="Z.AI usage did not return a response.")
        if response.status_code == 401:
            return _provider_payload("zai", status="expired", message="Z.AI rejected the configured API key.")
        if response.status_code == 403:
            return _provider_payload("zai", status="forbidden", message="Z.AI denied access to Coding Plan usage.")
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, Mapping):
            return _provider_payload("zai", status="unavailable", message="Z.AI returned an invalid response.")
        return _zai_payload(payload)
    except ImportError:
        return _provider_payload("zai", status="unavailable", message="Hermes HTTP client is unavailable.")
    except Exception as error:
        return _provider_payload("zai", status="unavailable", message=_provider_message(error))
    finally:
        token = ""
        headers.clear()

__all__ = [name for name in globals() if not name.startswith("__")]
