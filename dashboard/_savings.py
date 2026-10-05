"""Savings advisor: the same work on a cheaper route you already trust.

For every model that cost cash in the period, the recorded token mix —
prompt that missed the cache, prompt read from it, cache writes, output —
is priced on the other models this install has actually run, with Hermes'
own pricing tables, and set beside each candidate's work-reliability
evidence from AI Models (the 95% upper bound on its task failure rate).
Candidates without enough evidence are not offered: a saving on a model
nobody has measured is a guess. Subscription routes you already pay for
cost no cash; moving work there spends plan quota instead, and says so.
The estimate assumes the candidate caches as well as the current route —
it is a ranking of options, not a quote.
"""

from __future__ import annotations

from ._common import *
from ._watch import _is_subscription_route

SAVINGS_MIN_SPEND_USD = 0.25
SAVINGS_MIN_SHARE = 0.15
SAVINGS_RELIABILITY_MARGIN = 0.03
SAVINGS_MAX_ALTERNATIVES = 3
_mix_rate_cache: Dict[Tuple[str, str], Tuple[float, Optional[Tuple[float, float, float, float]]]] = {}


def _mix_rates(model: str, provider: str) -> Optional[Tuple[float, float, float, float]]:
    """(input, output, cache read, cache write) USD per token, or None when Hermes has no price."""
    key = (str(model or ""), str(provider or ""))
    now = time.time()
    cached = _mix_rate_cache.get(key)
    if cached and now - cached[0] < 3600:
        return cached[1]
    rates = None
    try:
        from agent.usage_pricing import get_pricing_entry

        entry = get_pricing_entry(key[0], key[1], None)
    except Exception:
        entry = None
    input_rate = getattr(entry, "input_cost_per_million", None) if entry is not None else None
    if input_rate is not None:
        def per(name: str, fallback: Any) -> float:
            value = getattr(entry, name, None)
            return float(value if value is not None else fallback) / 1e6

        rates = (
            float(input_rate) / 1e6,
            per("output_cost_per_million", input_rate),
            per("cache_read_cost_per_million", input_rate),
            per("cache_write_cost_per_million", input_rate),
        )
    _mix_rate_cache[key] = (now, rates)
    return rates


def _price_mix(mix: Mapping[str, Any], rates: Tuple[float, float, float, float]) -> float:
    input_rate, output_rate, cache_read_rate, cache_write_rate = rates
    return (
        _integer(mix.get("input_tokens")) * input_rate
        + _integer(mix.get("cache_read_tokens")) * cache_read_rate
        + _integer(mix.get("cache_write_tokens")) * cache_write_rate
        + _integer(mix.get("output_tokens")) * output_rate
    )


def _savings_sync(
    days: int,
    start_at: Optional[float] = None,
    end_at: Optional[float] = None,
    models_payload: Optional[Mapping[str, Any]] = None,
    sample_floor: Optional[int] = None,
) -> Dict[str, Any]:
    period_start, period_end = _period_bounds(days, start_at, end_at)
    period_sql, period_params = _period_sql("s.started_at", period_start, period_end)
    with _database() as db:
        connection = _db_connection(db)
        rows = [
            _row_dict(row)
            for row in connection.execute(
                f"""
                SELECT u.model, u.billing_provider, coalesce(u.task, '') AS task,
                       SUM(u.input_tokens) AS input_tokens, SUM(u.cache_read_tokens) AS cache_read_tokens,
                       SUM(u.cache_write_tokens) AS cache_write_tokens, SUM(u.output_tokens) AS output_tokens,
                       SUM(u.api_call_count) AS calls, COUNT(DISTINCT u.session_id) AS sessions,
                       SUM(CASE WHEN u.actual_cost_usd > 0 THEN u.actual_cost_usd
                                WHEN u.estimated_cost_usd > 0 THEN u.estimated_cost_usd ELSE 0 END) AS cash_usd,
                       MAX(lower(coalesce(u.cost_status, ''))) AS cost_status,
                       MAX(coalesce(u.billing_base_url, '')) AS billing_base_url
                FROM session_model_usage u
                JOIN sessions s ON s.id = u.session_id{_same_profile(db, "s", "u")}
                WHERE {period_sql}
                GROUP BY u.model, u.billing_provider, coalesce(u.task, '')
                """,
                tuple(period_params),
            ).fetchall()
        ]

    # Each model's main route (the provider that carried most of its calls).
    routes: Dict[str, Tuple[str, int, bool]] = {}
    for row in rows:
        model = str(row.get("model") or "")
        calls = _integer(row.get("calls"))
        plan = _is_subscription_route(row.get("billing_provider"), row.get("billing_base_url")) or row.get("cost_status") in {"included", "subscription"}
        if model and (model not in routes or calls > routes[model][1]):
            routes[model] = (str(row.get("billing_provider") or ""), calls, plan)

    models_payload = models_payload or {"models": []}
    floor = int(sample_floor if sample_floor is not None else _number(_plugin_settings().get("rate_sample_threshold"), 20))
    evidence: Dict[str, Dict[str, Any]] = {}
    for model in (models_payload or {}).get("models", []):
        work = model.get("work_reliability") or {}
        model_id = str(model.get("model_id") or "")
        if not model_id:
            continue
        evidence[model_id] = {
            "eligible": _integer(work.get("eligible_tasks")),
            "upper_bound": work.get("failure_rate_upper_bound_95"),
            "route_label": model.get("route_label"),
            "subscription": str(model.get("cost_kind") or "") in {"subscription", "included"},
        }

    candidates = []
    for model_id, proof in evidence.items():
        if proof["eligible"] < floor or proof["upper_bound"] is None or model_id not in routes:
            continue
        provider, _calls, plan = routes[model_id]
        candidates.append(
            {
                "model": model_id,
                "provider": provider,
                "route_label": proof["route_label"],
                "subscription": bool(plan or proof["subscription"]),
                "rates": None if (plan or proof["subscription"]) else _mix_rates(model_id, provider),
                "eligible": proof["eligible"],
                "upper_bound": proof["upper_bound"],
            }
        )

    # Current cash spend per model on the main conversation. Helper tasks
    # (vision, approvals, titles…) need particular abilities — a text model
    # cannot take over image analysis — so they are left to the per-task
    # view under a session's "Why it cost".
    current: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for row in rows:
        if _is_subscription_route(row.get("billing_provider"), row.get("billing_base_url")):
            continue
        if row.get("task"):
            continue
        key = (str(row.get("model") or ""), str(row.get("billing_provider") or ""))
        entry = current.setdefault(
            key,
            {"input_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0, "output_tokens": 0,
             "calls": 0, "sessions": 0, "cash_usd": 0.0},
        )
        for field in ("input_tokens", "cache_read_tokens", "cache_write_tokens", "output_tokens", "calls", "sessions"):
            entry[field] += _integer(row.get(field))
        entry["cash_usd"] += _number(row.get("cash_usd"), 0)

    advice = []
    for (model, provider), mix in current.items():
        spend = mix["cash_usd"]
        if spend < SAVINGS_MIN_SPEND_USD:
            continue
        own = evidence.get(model, {})
        own_bound = own.get("upper_bound") if own.get("eligible", 0) >= floor else None
        alternatives = []
        for candidate in candidates:
            if candidate["model"] == model:
                continue
            if candidate["subscription"]:
                cost = 0.0
            elif candidate["rates"] is None:
                continue
            else:
                cost = _price_mix(mix, candidate["rates"])
            saving = spend - cost
            if saving < max(spend * SAVINGS_MIN_SHARE, 0.05):
                continue
            if own_bound is None:
                verdict = "proven; the current model is not yet"
            elif candidate["upper_bound"] <= own_bound + SAVINGS_RELIABILITY_MARGIN:
                verdict = "as reliable or better"
            else:
                verdict = "less reliable"
            if candidate["subscription"] and verdict != "as reliable or better":
                # A plan's quota is finite; it is worth spending only on work it does as well.
                continue
            alternatives.append(
                {
                    "model": candidate["model"],
                    "route_label": candidate["route_label"],
                    "subscription": candidate["subscription"],
                    "cost_usd": round(cost, 4),
                    "saving_usd": round(saving, 4),
                    "saving_share": round(saving / spend, 4) if spend else 0.0,
                    "eligible_tasks": candidate["eligible"],
                    "failure_upper_bound": round(float(candidate["upper_bound"]), 4),
                    "verdict": verdict,
                }
            )
        order = {"as reliable or better": 0, "proven; the current model is not yet": 1, "less reliable": 2}
        alternatives.sort(key=lambda item: (order.get(item["verdict"], 3), -item["saving_usd"]))
        advice.append(
            {
                "model": model,
                "provider": provider,
                "cash_usd": round(spend, 4),
                "calls": mix["calls"],
                "sessions": mix["sessions"],
                "mix": {field: mix[field] for field in ("input_tokens", "cache_read_tokens", "cache_write_tokens", "output_tokens")},
                "eligible_tasks": own.get("eligible", 0),
                "failure_upper_bound": round(float(own_bound), 4) if own_bound is not None else None,
                "alternatives": alternatives[:SAVINGS_MAX_ALTERNATIVES],
            }
        )
    advice.sort(key=lambda item: -item["cash_usd"])

    def _best_saving(item: Dict[str, Any], verdict: str) -> float:
        return max((alt["saving_usd"] for alt in item["alternatives"] if alt["verdict"] == verdict), default=0.0)

    # One route per current model: a proven one where it exists, else the
    # best route whose reliability cannot be compared yet.
    proven = sum(_best_saving(item, "as reliable or better") for item in advice)
    unproven = sum(
        _best_saving(item, "proven; the current model is not yet")
        for item in advice
        if not _best_saving(item, "as reliable or better")
    )
    margin_points = round(SAVINGS_RELIABILITY_MARGIN * 100, 1)
    margin_label = f"{margin_points:g}"
    return {
        "models": advice,
        "candidates": len(candidates),
        "sample_floor": floor,
        "totals": {
            "cash_usd": round(sum(item["cash_usd"] for item in advice), 4),
            "best_saving_usd": round(proven + unproven, 4),
            "proven_saving_usd": round(proven, 4),
            "unproven_saving_usd": round(unproven, 4),
        },
        "reliability_margin": SAVINGS_RELIABILITY_MARGIN,
        "period_days": days,
        "period": _period_payload(days, period_start, period_end),
        "definition": (
            "Each model's main-conversation token mix for the period priced on the other models this install has run, with "
            "Hermes' pricing tables, beside their task failure bound from AI Models (95% upper bound, only above the "
            f"{floor}-task sample floor). \"As reliable or better\" allows the new route's bound to sit up to "
            f"{margin_label} percentage points above the current model's. \"Current model unproven\" means the current "
            f"model has fewer than {floor} scored tasks, so the two cannot be compared yet. It assumes the same cache hit "
            "rate on the new route. A subscription route is offered only where it is as reliable, since it spends plan "
            "quota. Helper tasks are excluded. A ranking of options, not a quote."
        ),
        "generated_at": time.time(),
    }


__all__ = [name for name in globals() if not name.startswith("__")]
