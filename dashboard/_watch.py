"""Runaway watch: what each session is burning right now.

Session totals have no time axis, but Hermes' agent log records every API
call with its session, model, provider and token split. The last hour of
those calls, priced with Hermes' own pricing tables, is each session's
current burn. A session still calling a model that burns past the user's
alert rate is flagged while it runs, instead of being found in next week's
totals. Cash routes are measured in dollars; flat-rate subscription routes
(Claude Pro/Max through Hermes' subscription plugin) cost no cash, so their
burn is measured at API list price — the scale of the subscription use.
"""

from __future__ import annotations

try:
    from ._common import *
    from ._logparse import *
except ImportError:  # pragma: no cover - direct Hermes file loading
    from _common import *
    from _logparse import *

WATCH_WINDOW_SECONDS = 3600
WATCH_ACTIVE_SECONDS = 900
WATCH_DEFAULT_CASH_PER_HOUR = 1.0
WATCH_DEFAULT_LIST_PER_HOUR = 5.0
WATCH_MAX_SESSIONS = 20
_WATCH_RATE_TTL_SECONDS = 3600.0
_watch_rate_cache: Dict[Tuple[str, str], Tuple[float, Optional[Tuple[float, float, float]]]] = {}


def _parse_watch_param(raw: Any) -> Dict[str, float]:
    """`cash:1,list:5` -> alert rates per hour; defaults fill the gaps."""
    thresholds = {"cash_per_hour": WATCH_DEFAULT_CASH_PER_HOUR, "list_per_hour": WATCH_DEFAULT_LIST_PER_HOUR}
    for part in str(raw or "").split(",")[:4]:
        key, _, value = part.partition(":")
        name = {"cash": "cash_per_hour", "list": "list_per_hour"}.get(key.strip().lower())
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if name and number == number and 0 < number <= 100_000:
            thresholds[name] = number
    return thresholds


def _is_subscription_route(*values: Any) -> bool:
    text = " ".join(str(value or "").lower() for value in values)
    return any(pattern.strip("%") in text for pattern in SUBSCRIPTION_ROUTE_PATTERNS)


def _call_rates(model: str, provider: str) -> Optional[Tuple[float, float, float]]:
    """(input, output, cache-read) USD per token from Hermes' pricing tables, or None.

    A subscription route has no price of its own; its model is priced as
    Anthropic lists it, which is what Hermes itself records as the usage's
    list-price equivalent.
    """
    key = (str(model or ""), str(provider or ""))
    now = time.time()
    cached = _watch_rate_cache.get(key)
    if cached and now - cached[0] < _WATCH_RATE_TTL_SECONDS:
        return cached[1]
    rates: Optional[Tuple[float, float, float]] = None
    try:
        from agent.usage_pricing import get_pricing_entry
    except Exception:
        get_pricing_entry = None
    if get_pricing_entry is not None:
        candidates = [(key[0], key[1])]
        if _is_subscription_route(key[1]):
            candidates += [(key[0], "anthropic"), (key[0].split("[", 1)[0], "anthropic")]
        for candidate_model, candidate_provider in candidates:
            try:
                entry = get_pricing_entry(candidate_model, candidate_provider, None)
            except Exception:
                entry = None
            input_rate = getattr(entry, "input_cost_per_million", None) if entry is not None else None
            if input_rate is None:
                continue
            output_rate = getattr(entry, "output_cost_per_million", None)
            cache_rate = getattr(entry, "cache_read_cost_per_million", None)
            rates = (
                float(input_rate) / 1e6,
                float(output_rate if output_rate is not None else input_rate) / 1e6,
                float(cache_rate if cache_rate is not None else input_rate) / 1e6,
            )
            break
    _watch_rate_cache[key] = (now, rates)
    return rates


def _call_cost(event: Mapping[str, Any], rates: Tuple[float, float, float]) -> float:
    input_rate, output_rate, cache_rate = rates
    prompt = _integer(event.get("input_tokens"))
    cached = min(_integer(event.get("cache_read_tokens")), prompt)
    return (prompt - cached) * input_rate + cached * cache_rate + _integer(event.get("output_tokens")) * output_rate


def _watch_sync(thresholds: Optional[Mapping[str, float]] = None, now: Optional[float] = None) -> Dict[str, Any]:
    thresholds = dict(thresholds or _parse_watch_param(""))
    now = time.time() if now is None else now
    window_start = now - WATCH_WINDOW_SECONDS
    burns: Dict[str, Dict[str, Any]] = {}
    for index, event in enumerate(_runtime_events().get("api", [])):
        if index % 2000 == 0:
            _check_route_budget()
        timestamp = _number(event.get("timestamp"), 0)
        session_id = str(event.get("session_id") or "")
        if not session_id or timestamp < window_start or timestamp > now + 60:
            continue
        burn = burns.setdefault(
            session_id,
            {"calls": 0, "tokens": 0, "cash": 0.0, "list": 0.0, "unpriced_calls": 0, "last_call_at": 0.0,
             "first_call_at": timestamp, "model": None, "provider": None},
        )
        burn["calls"] += 1
        burn["tokens"] += _integer(event.get("input_tokens")) + _integer(event.get("output_tokens"))
        if timestamp >= burn["last_call_at"]:
            burn["last_call_at"] = timestamp
            burn["model"], burn["provider"] = event.get("model"), event.get("provider")
        burn["first_call_at"] = min(burn["first_call_at"], timestamp)
        rates = _call_rates(str(event.get("model") or ""), str(event.get("provider") or ""))
        if rates is None:
            burn["unpriced_calls"] += 1
        elif _is_subscription_route(event.get("provider")):
            burn["list"] += _call_cost(event, rates)
        else:
            burn["cash"] += _call_cost(event, rates)

    metadata: Dict[str, Dict[str, Any]] = {}
    if burns:
        ids = sorted(burns)
        with _database() as db:
            connection = _db_connection(db)
            profile_column = ", s.__profile AS profile" if getattr(db, "union_profiles", None) else ""
            columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(sessions)").fetchall()}
            list_price = "s.list_price_usd" if "list_price_usd" in columns else "NULL"
            for start in range(0, len(ids), 400):
                chunk = ids[start : start + 400]
                placeholders = ",".join("?" for _ in chunk)
                for row in connection.execute(
                    f"""
                    SELECT s.id, s.title, s.source, s.started_at, s.ended_at, s.end_reason,
                           s.estimated_cost_usd, s.actual_cost_usd, s.cost_status, s.cost_source,
                           {list_price} AS list_price_usd{profile_column}
                    FROM ({_accounted_sessions_sql(connection)}) s
                    WHERE s.id IN ({placeholders})
                    """,
                    tuple(chunk),
                ).fetchall():
                    material = _row_dict(row)
                    metadata[str(material.get("id"))] = material

    rows: List[Dict[str, Any]] = []
    for session_id, burn in burns.items():
        meta = metadata.get(session_id, {})
        running = now - burn["last_call_at"] <= WATCH_ACTIVE_SECONDS
        started = _number(meta.get("started_at"), burn["first_call_at"])
        cost = _cost_view(meta) if meta else {"display_cost_usd": None, "cost_kind": "unpriced"}
        cash, listed = round(burn["cash"], 4), round(burn["list"], 4)
        over_cash = running and cash >= thresholds["cash_per_hour"]
        over_list = running and listed >= thresholds["list_per_hour"]
        reason = ""
        if over_cash:
            reason = f"${cash:,.2f} in the last hour (alert at ${thresholds['cash_per_hour']:,.2f}/h)"
        elif over_list:
            reason = (
                f"≈ ${listed:,.2f} of subscription use at list price in the last hour "
                f"(alert at ${thresholds['list_per_hour']:,.2f}/h)"
            )
        if reason:
            reason += f" · {burn['calls']} calls · running {max(0.0, now - started) / 3600:.1f} h"
        rows.append(
            {
                "id": session_id,
                "title": _clean_text(meta.get("title"), 120) or session_id,
                "profile": meta.get("profile"),
                "source": meta.get("source"),
                "model": burn["model"],
                "provider": burn["provider"],
                "route": "subscription" if _is_subscription_route(burn["provider"]) else "cash",
                "running": running,
                "calls_last_hour": burn["calls"],
                "tokens_last_hour": burn["tokens"],
                "cash_last_hour_usd": cash,
                "list_last_hour_usd": listed,
                "unpriced_calls": burn["unpriced_calls"],
                "last_call_at": burn["last_call_at"],
                "started_at": started,
                "ended_at": meta.get("ended_at"),
                "cost_so_far_usd": cost.get("display_cost_usd"),
                "cost_kind": cost.get("cost_kind"),
                "list_price_so_far_usd": meta.get("list_price_usd"),
                "over": bool(over_cash or over_list),
                "reason": reason or None,
            }
        )
    rows.sort(key=lambda row: (not row["over"], not row["running"], -(row["cash_last_hour_usd"] + row["list_last_hour_usd"])))
    running_rows = [row for row in rows if row["running"]]
    return {
        "sessions": rows[:WATCH_MAX_SESSIONS],
        "totals": {
            "sessions_last_hour": len(rows),
            "running": len(running_rows),
            "over": sum(1 for row in rows if row["over"]),
            "cash_last_hour_usd": round(sum(row["cash_last_hour_usd"] for row in rows), 4),
            "list_last_hour_usd": round(sum(row["list_last_hour_usd"] for row in rows), 4),
        },
        "thresholds": thresholds,
        "window_seconds": WATCH_WINDOW_SECONDS,
        "active_seconds": WATCH_ACTIVE_SECONDS,
        "definition": (
            "Calls from Hermes' agent logs in the last hour, priced with Hermes' pricing tables. A session is "
            "running when its last call was under 15 minutes ago. Subscription routes cost no cash and are "
            "measured at API list price."
        ),
        "generated_at": now,
    }


def _watch_attention_notes(thresholds: Mapping[str, float]) -> List[Dict[str, Any]]:
    """Strip notes for running sessions past the alert rate; one id per session per day."""
    try:
        watch = _watch_sync(thresholds)
    except RouteBudgetExceeded:
        raise
    except Exception:
        return []
    notes = []
    for row in watch["sessions"]:
        if not row["over"]:
            continue
        day = time.strftime("%Y-%m-%d", time.localtime(row["last_call_at"]))
        notes.append(
            {
                "id": f"runaway:{row['id']}:{day}",
                "kind": "runaway",
                "session_id": row["id"],
                "severity": "danger",
                "provider_label": row["title"],
                "window_label": "burning fast",
                "reason": row["reason"],
                "as_of": watch["generated_at"],
            }
        )
    return notes


__all__ = [name for name in globals() if not name.startswith("__")]
