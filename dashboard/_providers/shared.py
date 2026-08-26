"""Shared provider window and payload normalization."""

from __future__ import annotations

try:
    from .._common import *
    from .._hermes_compat import *
except ImportError:  # pragma: no cover
    from _common import *
    from _hermes_compat import *

_AI_USAGE_PROVIDER_META = {
    "codex": {"label": "OpenAI Codex", "auth_source": "Hermes OAuth"},
    "anthropic": {"label": "Anthropic Claude", "auth_source": "Hermes OAuth"},
    "nous": {"label": "Nous Research Portal", "auth_source": "Hermes OAuth"},
    "openrouter": {"label": "OpenRouter", "auth_source": "Hermes API key"},
    "deepseek": {"label": "DeepSeek", "auth_source": "Hermes API key"},
    "grok": {"label": "Grok", "auth_source": "Hermes xAI OAuth"},
    "kimi": {"label": "Kimi Code Plan", "auth_source": "Hermes API key"},
    "zai": {"label": "Z.AI GLM Coding Plan", "auth_source": "Hermes API key"},
}
_AI_USAGE_PROVIDER_ORDER = tuple(_AI_USAGE_PROVIDER_META)


def _usage_number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _usage_percent(value: Any) -> Optional[float]:
    number = _usage_number(value)
    return None if number is None else max(0.0, min(100.0, number))


def _usage_iso(value: Any) -> Optional[str]:
    if value in (None, ""):
        return None
    if isinstance(value, dt.datetime):
        moment = value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)
        return moment.isoformat()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            timestamp = float(value)
            if abs(timestamp) >= 10_000_000_000:
                timestamp /= 1000.0
            return dt.datetime.fromtimestamp(timestamp, tz=dt.timezone.utc).isoformat()
        except (OSError, OverflowError, ValueError):
            return None
    text = _clean_text(value, 120)
    if not text:
        return None
    if re.fullmatch(r"-?\d+(?:\.\d+)?", text):
        return _usage_iso(float(text))
    try:
        moment = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=dt.timezone.utc)
        return moment.isoformat()
    except ValueError:
        return text


def _provider_message(error: BaseException) -> str:
    response = getattr(error, "response", None)
    status_code = getattr(response, "status_code", None)
    if status_code:
        return f"HTTP {status_code}"
    return _clean_text(f"{type(error).__name__}: {error}", 200) or type(error).__name__


def _provider_payload(
    provider: str,
    *,
    status: str,
    plan: Optional[str] = None,
    windows: Optional[List[Dict[str, Any]]] = None,
    details: Optional[List[str]] = None,
    message: Optional[str] = None,
    partial: bool = False,
) -> Dict[str, Any]:
    meta = _AI_USAGE_PROVIDER_META[provider]
    return {
        "provider": provider,
        "label": meta["label"],
        "status": status,
        "auth_source": meta["auth_source"],
        "plan": _clean_text(plan, 120) or None,
        "windows": windows or [],
        "details": [_clean_text(item, 320) for item in (details or []) if _clean_text(item, 320)],
        "message": _clean_text(message, 240) or None,
        "partial": bool(partial),
        "stale": False,
        "fetched_at": time.time(),
    }


def _window_id(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", label.strip().lower()).strip("-") or "usage"


def _usage_window(
    label: str,
    *,
    kind: str = "quota",
    used_percent: Any = None,
    reset_at: Any = None,
    detail: Any = None,
    limit: Any = None,
    used: Any = None,
    remaining: Any = None,
    unit: Optional[str] = None,
) -> Dict[str, Any]:
    used_pct = _usage_percent(used_percent)
    return {
        "id": _window_id(label),
        "label": _clean_text(label, 120),
        "kind": kind,
        "percentage_used": used_pct,
        "percentage_remaining": None if used_pct is None else 100.0 - used_pct,
        "reset_at": _usage_iso(reset_at),
        "detail": _clean_text(detail, 240) or None,
        "limit": _usage_number(limit),
        "used": _usage_number(used),
        "remaining": _usage_number(remaining),
        "unit": _clean_text(unit, 40) or None,
    }


def _account_usage_payload(provider: str, snapshot: Any) -> Dict[str, Any]:
    if snapshot is None:
        return _provider_payload(
            provider,
            status="unavailable",
            message="Hermes did not return account-usage data for this provider.",
        )
    unavailable = _clean_text(getattr(snapshot, "unavailable_reason", None), 240)
    if unavailable:
        return _provider_payload(provider, status="unavailable", message=unavailable)
    windows = []
    for raw in tuple(getattr(snapshot, "windows", ()) or ()):
        label = _clean_text(getattr(raw, "label", None), 120) or "Usage"
        windows.append(
            _usage_window(
                label,
                used_percent=getattr(raw, "used_percent", None),
                reset_at=getattr(raw, "reset_at", None),
                detail=getattr(raw, "detail", None),
            )
        )
    details = list(getattr(snapshot, "details", ()) or ())
    if not windows and not details:
        return _provider_payload(
            provider,
            status="unavailable",
            message="The provider returned no quota windows or balance details.",
        )
    return _provider_payload(
        provider,
        status="ok",
        plan=getattr(snapshot, "plan", None),
        windows=windows,
        details=[str(item) for item in details],
    )


def _usage_field(values: Mapping[str, Any], *keys: str) -> Optional[float]:
    for key in keys:
        if key in values:
            number = _usage_number(values.get(key))
            if number is not None:
                return number
    return None


def _amount_quota_window(
    label: str,
    values: Mapping[str, Any],
    *,
    unit: str,
    limit_keys: Tuple[str, ...] = ("limit",),
    used_keys: Tuple[str, ...] = ("used",),
    remaining_keys: Tuple[str, ...] = ("remaining",),
    percent_keys: Tuple[str, ...] = ("percentage", "usedPercent", "used_percent"),
    reset_keys: Tuple[str, ...] = ("resetTime", "reset_at", "resets_at", "nextResetTime"),
    detail: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    limit = _usage_field(values, *limit_keys)
    used = _usage_field(values, *used_keys)
    remaining = _usage_field(values, *remaining_keys)
    if used is None and limit is not None and remaining is not None:
        used = max(0.0, limit - remaining)
    if remaining is None and limit is not None and used is not None:
        remaining = max(0.0, limit - used)
    used_percent = _usage_field(values, *percent_keys)
    if used_percent is None and limit is not None and limit > 0 and used is not None:
        used_percent = (used / limit) * 100.0
    if used_percent is None and limit is None and used is None and remaining is None:
        return None
    reset_at = next((values.get(key) for key in reset_keys if values.get(key) not in (None, "")), None)
    return _usage_window(
        label,
        used_percent=used_percent,
        reset_at=reset_at,
        detail=detail,
        limit=limit,
        used=used,
        remaining=remaining,
        unit=unit,
    )

def _usage_reset_epoch(value: Any) -> Optional[float]:
    text = _usage_iso(value)
    if not text:
        return None
    try:
        return dt.datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except (OverflowError, ValueError):
        return None


def _ai_usage_summary(providers: List[Dict[str, Any]]) -> Dict[str, Any]:
    reset_epochs = [
        epoch
        for provider in providers
        for window in provider.get("windows", [])
        if (epoch := _usage_reset_epoch(window.get("reset_at"))) is not None and epoch > time.time()
    ]
    return {
        "providers": len(providers),
        "connected": sum(1 for item in providers if item.get("status") == "ok"),
        "not_configured": sum(1 for item in providers if item.get("status") == "not_configured"),
        "needs_attention": sum(
            1 for item in providers if item.get("status") in {"expired", "forbidden", "unavailable", "stale"}
        ),
        "stale": sum(1 for item in providers if item.get("status") == "stale"),
        "next_reset_at": _usage_iso(min(reset_epochs)) if reset_epochs else None,
    }

__all__ = [name for name in globals() if not name.startswith("__")]
