"""Shared provider window and payload normalization."""

from __future__ import annotations

try:
    from .._common import *
    from .._hermes_compat import *
    from .._adapters import *
except ImportError:  # pragma: no cover
    from _common import *
    from _hermes_compat import *
    from _adapters import *

_collect_state: Dict[str, bool] = {"fresh": False}


def _set_collect_fresh(flag: bool) -> None:
    """Tell collectors whether the current pass is a manual refresh (bypass their own caches)."""
    _collect_state["fresh"] = bool(flag)


def _collect_is_fresh() -> bool:
    return bool(_collect_state.get("fresh"))


def _provider_meta(provider: str) -> Dict[str, str]:
    """Label and auth source for a registered provider id (KeyError otherwise)."""
    adapter = _provider_adapters().get(provider)
    if adapter is None:
        raise KeyError(provider)
    return {"label": adapter.label, "auth_source": adapter.auth_source}


def _provider_not_configured_message(provider: str) -> Optional[str]:
    adapter = _provider_adapters().get(provider)
    return adapter.not_configured_message if adapter else None


def _default_key_probe(provider: str) -> bool:
    """Probe for key-based providers: does Hermes resolve an API key for this id?"""
    token, _base_url = _resolve_hermes_api_key(provider)
    return bool(token)


def _probe_usage_provider(provider: str) -> bool:
    """Local-only check whether Hermes holds credentials for a provider.

    Gate for the network collectors: False means the local credential store
    positively shows nothing is configured, so the collector (and its outbound
    request) is skipped. Conservative by design — any uncertainty (missing
    Hermes modules, probe errors) returns True so the collector still runs and
    reports its own status; a probe must never hide a configured provider.

    Each adapter supplies its own probe (OAuth logins, portal accounts); the
    default resolves a Hermes API key for the provider id.
    """
    adapter = _provider_adapters().get(provider)
    try:
        if adapter is not None and adapter.probe is not None:
            return bool(adapter.probe())
        return _default_key_probe(provider)
    except Exception:
        return True


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
    links: Optional[List[Mapping[str, Any]]] = None,
) -> Dict[str, Any]:
    meta = _provider_meta(provider)
    kept_details, muted_details = _split_noise_details(
        [_clean_text(item, 320) for item in (details or []) if _clean_text(item, 320)]
    )
    safe_links: List[Dict[str, str]] = []
    for link in links or []:
        if not isinstance(link, Mapping):
            continue
        label = _clean_text(link.get("label"), 80)
        url = str(link.get("url") or "").strip()
        if label and url.startswith("https://"):
            safe_links.append({"label": label, "url": url})
    return {
        "provider": provider,
        "label": meta["label"],
        "status": status,
        "auth_source": meta["auth_source"],
        "plan": _clean_text(plan, 120) or None,
        "windows": windows or [],
        "details": kept_details,
        "details_muted": muted_details,
        "message": _clean_text(message, 240) or None,
        "partial": bool(partial),
        "stale": False,
        "fetched_at": time.time(),
        "links": safe_links,
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
    # Reset countdowns consider every card, but the provider counts only the
    # base providers — a second pooled Claude account must not turn the
    # "Connected 6/7" stat into 7/8.
    # The next reset is the soonest among windows with something used in
    # them; an untouched window resetting in 46 minutes tells the reader
    # nothing. Untouched windows are the fallback when nothing is in use.
    resets: List[Tuple[float, bool, str, str]] = []
    for provider in providers:
        for window in provider.get("windows", []):
            epoch = _usage_reset_epoch(window.get("reset_at"))
            if epoch is None or epoch <= time.time():
                continue
            in_use = (_usage_number(window.get("percentage_used")) or 0) > 0
            resets.append((epoch, in_use, str(provider.get("label") or provider.get("provider") or ""), str(window.get("label") or "")))
    used_resets = [item for item in resets if item[1]]
    next_reset = min(used_resets or resets, key=lambda item: item[0]) if resets else None
    reset_epochs = [item[0] for item in resets]
    base = [item for item in providers if not item.get("account_extra")]
    not_configured = sum(1 for item in base if item.get("status") == "not_configured")
    # A window on pace to run out is something to act on, so it counts here
    # exactly when its forecast line shows on the card.
    forecasts = sum(
        1 for item in base for window in item.get("windows", []) if window.get("forecast")
    )
    return {
        "providers": len(base),
        "configured": len(base) - not_configured,
        "connected": sum(1 for item in base if item.get("status") == "ok"),
        "not_configured": not_configured,
        "needs_attention": sum(
            1 for item in base if item.get("status") in {"expired", "forbidden", "unavailable", "stale"}
        ) + forecasts,
        "forecasts": forecasts,
        "stale": sum(1 for item in base if item.get("status") == "stale"),
        "next_reset_at": _usage_iso(next_reset[0]) if next_reset else None,
        "next_reset_provider": next_reset[2] if next_reset else None,
        "next_reset_window": next_reset[3] if next_reset else None,
        "next_reset_in_use": bool(next_reset[1]) if next_reset else False,
    }


def _usage_card_rank(card: Mapping[str, Any]) -> Tuple[int, float]:
    """Sort key for provider cards: what needs a look first.

    0 credential trouble · 1 a window on pace to run out (soonest first) ·
    2 quota windows in use (fullest first) · 3 balances and pay-as-you-go
    (most local spend first) · 4 untouched or rate-limit-only cards ·
    5 not configured.
    """
    status = str(card.get("status") or "")
    if status in {"expired", "forbidden", "unavailable"}:
        return (0, 0.0)
    if status == "not_configured":
        return (5, 0.0)
    windows = [window for window in card.get("windows", []) if isinstance(window, Mapping)]
    forecasts = [window["forecast"]["exhaust_at"] for window in windows if isinstance(window.get("forecast"), Mapping)]
    if forecasts:
        return (1, min(_usage_number(item) or 0.0 for item in forecasts))
    used = [
        _usage_number(window.get("percentage_used")) or 0.0
        for window in windows
        if window.get("kind") == "quota" and (_usage_number(window.get("percentage_used")) or 0.0) > 0
    ]
    if used:
        return (2, -max(used))
    if any(window.get("kind") == "balance" for window in windows):
        recorded = card.get("recorded_local") or {}
        return (3, -(_usage_number(recorded.get("cost_usd")) or 0.0))
    return (4, 0.0)


def _order_usage_cards(providers: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Base cards by rank, each followed by its extra account cards."""
    base = [card for card in providers if not card.get("account_extra")]
    extras: Dict[str, List[Dict[str, Any]]] = {}
    for card in providers:
        if card.get("account_extra"):
            extras.setdefault(str(card.get("base_provider") or ""), []).append(card)
    ordered: List[Dict[str, Any]] = []
    for index, card in sorted(enumerate(base), key=lambda pair: (_usage_card_rank(pair[1]), pair[0])):
        ordered.append(card)
        ordered.extend(extras.get(str(card.get("provider") or ""), []))
    return ordered

__all__ = [name for name in globals() if not name.startswith("__")]
