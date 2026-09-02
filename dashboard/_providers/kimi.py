"""kimi quota provider adapter."""

from __future__ import annotations

try:
    from .._common import *
    from .._hermes_compat import *
    from .shared import *
except ImportError:  # pragma: no cover
    from _common import *
    from _hermes_compat import *
    from _providers.shared import *

def _kimi_plan_label(payload: Mapping[str, Any]) -> Optional[str]:
    user = payload.get("user")
    membership = user.get("membership") if isinstance(user, Mapping) else None
    raw = membership.get("level") if isinstance(membership, Mapping) else None
    label = _clean_text(raw, 80)
    if not label:
        return None
    label = re.sub(r"^LEVEL_", "", label, flags=re.IGNORECASE)
    return label.replace("_", " ").strip().title() or None


def _kimi_window_label(raw: Mapping[str, Any], index: int) -> str:
    name = _clean_text(raw.get("name"), 80)
    if name:
        return name
    window = raw.get("window")
    if isinstance(window, Mapping):
        duration = _usage_number(window.get("duration"))
        unit = _clean_text(window.get("timeUnit") or window.get("unit"), 40).upper()
        if duration == 300 and "MINUTE" in unit:
            return "5-hour rolling"
        if duration == 5 and "HOUR" in unit:
            return "5-hour rolling"
        if (duration == 7 and "DAY" in unit) or (duration == 1 and "WEEK" in unit):
            return "Weekly quota"
        if duration is not None and unit:
            readable_unit = unit.replace("TIME_UNIT_", "").replace("_", " ").lower()
            return f"{duration:g}-{readable_unit} window"
    return f"Quota window {index + 1}"


def _kimi_payload(payload: Mapping[str, Any]) -> Dict[str, Any]:
    windows: List[Dict[str, Any]] = []
    weekly = payload.get("usage") or payload.get("summary")
    if isinstance(weekly, Mapping):
        window = _amount_quota_window("Weekly quota", weekly, unit="requests")
        if window:
            windows.append(window)

    limits = payload.get("limits")
    if isinstance(limits, list):
        for index, raw in enumerate(limits):
            if not isinstance(raw, Mapping):
                continue
            detail = raw.get("detail") if isinstance(raw.get("detail"), Mapping) else raw
            window = _amount_quota_window(
                _kimi_window_label(raw, index),
                detail,
                unit="requests",
            )
            if window and not any(
                existing["label"] == window["label"]
                and existing.get("reset_at") == window.get("reset_at")
                for existing in windows
            ):
                windows.append(window)

    extra = payload.get("extra_usage") or payload.get("extraUsage")
    if isinstance(extra, Mapping):
        balance_cents = _usage_number(_usage_field(extra, "balance_cents", "balanceCents"))
        if balance_cents is not None:
            currency = (_clean_text(extra.get("currency"), 12) or "USD").upper()
            windows.append(
                _usage_window(
                    "Extra usage balance",
                    kind="balance",
                    remaining=balance_cents / 100.0,
                    unit=currency,
                )
            )

    details = []
    parallel = payload.get("parallel")
    if isinstance(parallel, Mapping):
        limit = _usage_number(parallel.get("limit"))
        if limit is not None:
            details.insert(0, f"Maximum parallel requests: {limit:g}")
    if not windows:
        return _provider_payload(
            "kimi",
            status="unavailable",
            message="Kimi returned no recognized quota windows.",
        )
    return _provider_payload(
        "kimi",
        status="ok",
        plan=_kimi_plan_label(payload),
        windows=windows,
        details=details,
    )


def _collect_kimi_usage() -> Dict[str, Any]:
    token = ""
    headers: Dict[str, str] = {}
    try:
        token, base_url = _resolve_hermes_api_key("kimi-coding")
        if not token:
            return _provider_payload(
                "kimi", status="not_configured", message="No Hermes Kimi Code Plan API key was found."
            )
        parsed = urlparse(base_url)
        if parsed.scheme != "https" or (parsed.hostname or "").lower() != "api.kimi.com":
            return _provider_payload(
                "kimi",
                status="not_configured",
                message="Kimi usage requires a Kimi Code Plan key configured for https://api.kimi.com.",
            )
        import httpx

        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        response = httpx.get(
            "https://api.kimi.com/coding/v1/usages",
            headers=headers,
            timeout=AI_USAGE_PROVIDER_TIMEOUT_SECONDS,
        )
        if response.status_code == 401:
            return _provider_payload("kimi", status="expired", message="Kimi rejected the configured Code Plan key.")
        if response.status_code == 403:
            return _provider_payload("kimi", status="forbidden", message="Kimi denied access to Code Plan usage.")
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, Mapping):
            return _provider_payload("kimi", status="unavailable", message="Kimi returned an invalid response.")
        return _kimi_payload(payload)
    except ImportError:
        return _provider_payload("kimi", status="unavailable", message="Hermes HTTP client is unavailable.")
    except Exception as error:
        return _provider_payload("kimi", status="unavailable", message=_provider_message(error))
    finally:
        token = ""
        headers.clear()


register_provider(
    "kimi", "Kimi Code Plan", "Hermes API key", _collect_kimi_usage,
    not_configured_message="No Hermes Kimi Code Plan API key was found.",
    billing_keys=("kimi-coding", "kimi-coding-cn"),
    registry_ids=("kimi", "kimi-coding", "kimi-coding-cn", "moonshot"),
    hosts=("api.kimi.com",), order=70, module=__name__,
)

__all__ = [name for name in globals() if not name.startswith("__")]
