"""Model comparison: up to five models side by side, on measures that compare fairly.

Your models do different work — a cheap model answers Telegram chat while a
frontier model rebuilds a codebase — so a single ranking would reward whoever
got the easy jobs. Each measure is compared on its own terms instead:

* cost for the same work: one recorded token mix priced on every model with
  Hermes' pricing tables (the selected models' combined work, or any one
  model's own work), assuming the same cache hit rate;
* reliability: the AI Models work-ledger failure bound, only above the
  sample floor;
* speed: median total latency from the bounded agent logs;
* cache hit rate: prompt tokens read from cache over all prompt tokens.

Each measure names its own leader, and the workload each model actually saw
(context per call, where it ran, what kind of task) is set beside them, with
a plain warning when the workloads differ too much to compare. History
records whether tasks finished without model/API failures, not whether the
answers were right; that limit is stated with the result.
"""

from __future__ import annotations

try:
    from ._common import *
    from ._savings import SAVINGS_RELIABILITY_MARGIN, _mix_rates, _price_mix
    from ._watch import _is_subscription_route
except ImportError:  # pragma: no cover - direct Hermes file loading
    from _common import *
    from _savings import SAVINGS_RELIABILITY_MARGIN, _mix_rates, _price_mix
    from _watch import _is_subscription_route

COMPARE_MAX_MODELS = 5
COMPARE_CONTEXT_GAP = 3.0
COMPARE_COMBINED = "combined"
_MIX_FIELDS = ("input_tokens", "cache_read_tokens", "cache_write_tokens", "output_tokens")
_COMPARE_VENDORS = (
    (re.compile(r"^(anthropic/)?claude", re.I), "anthropic"),
    (re.compile(r"^(openai/)?(gpt|o\d)", re.I), "openai"),
    (re.compile(r"^(x-ai/)?grok", re.I), "xai"),
    (re.compile(r"^(google/)?gemini", re.I), "google"),
)
_CONTEXT_SUFFIX_RE = re.compile(r"\[[^\]]*\]$")


def _list_rates(model: str, provider: str) -> Optional[Tuple[float, float, float, float]]:
    """List-price rates for a model, also when its own route bills through a plan.

    A subscription route has no per-token price of its own, so the model's
    vendor list price (or OpenRouter's) stands in; the result is flagged as
    included in the plan wherever it is shown.
    """
    names = [model]
    bare = _CONTEXT_SUFFIX_RE.sub("", model)
    if bare != model:
        names.append(bare)
    providers = [provider]
    providers += [vendor for pattern, vendor in _COMPARE_VENDORS if pattern.search(bare)]
    providers.append("openrouter")
    for name in names:
        for candidate in providers:
            rates = _mix_rates(name, candidate)
            if rates and rates[0] > 0:
                return rates
    return None


def _share_list(counter: Counter, total: int, limit: int = 3) -> List[Dict[str, Any]]:
    return [
        {"name": name, "share": round(count / total, 4) if total else 0.0, "count": count}
        for name, count in counter.most_common(limit)
    ]


def _compare_sync(
    model_ids: Iterable[str],
    days: int,
    start_at: Optional[float] = None,
    end_at: Optional[float] = None,
    models_payload: Optional[Mapping[str, Any]] = None,
    sample_floor: Optional[int] = None,
) -> Dict[str, Any]:
    wanted: List[str] = []
    for model_id in model_ids:
        model_id = str(model_id or "").strip()
        if model_id and model_id not in wanted:
            wanted.append(model_id)
    wanted = wanted[:COMPARE_MAX_MODELS]
    period_start, period_end = _period_bounds(days, start_at, end_at)
    floor = int(sample_floor if sample_floor is not None else _number(_plugin_settings().get("rate_sample_threshold"), 20))
    margin = SAVINGS_RELIABILITY_MARGIN
    evidence = {
        str(model.get("model_id") or ""): model
        for model in (models_payload or {}).get("models", [])
        if model.get("model_id")
    }

    rows: List[Dict[str, Any]] = []
    sources: Dict[str, Counter] = defaultdict(Counter)
    if wanted:
        period_sql, period_params = _period_sql("s.started_at", period_start, period_end)
        marks = ", ".join("?" for _ in wanted)
        with _database() as db:
            connection = _db_connection(db)
            rows = [
                _row_dict(row)
                for row in connection.execute(
                    f"""
                    SELECT u.model, u.billing_provider, MAX(coalesce(u.billing_base_url, '')) AS billing_base_url,
                           SUM(u.input_tokens) AS input_tokens, SUM(u.cache_read_tokens) AS cache_read_tokens,
                           SUM(u.cache_write_tokens) AS cache_write_tokens, SUM(u.output_tokens) AS output_tokens,
                           SUM(u.api_call_count) AS calls, COUNT(DISTINCT u.session_id) AS sessions,
                           SUM(CASE WHEN u.actual_cost_usd > 0 THEN u.actual_cost_usd
                                    WHEN u.estimated_cost_usd > 0 THEN u.estimated_cost_usd ELSE 0 END) AS cash_usd,
                           MAX(lower(coalesce(u.cost_status, ''))) AS cost_status
                    FROM session_model_usage u
                    JOIN sessions s ON s.id = u.session_id{_same_profile(db, "s", "u")}
                    WHERE {period_sql} AND coalesce(u.task, '') = '' AND u.model IN ({marks})
                    GROUP BY u.model, u.billing_provider
                    """,
                    tuple(period_params) + tuple(wanted),
                ).fetchall()
            ]
            for row in connection.execute(
                f"""
                SELECT u.model, coalesce(nullif(s.source, ''), 'unknown') AS source, COUNT(DISTINCT u.session_id) AS sessions
                FROM session_model_usage u
                JOIN sessions s ON s.id = u.session_id{_same_profile(db, "s", "u")}
                WHERE {period_sql} AND coalesce(u.task, '') = '' AND u.model IN ({marks})
                GROUP BY u.model, source
                """,
                tuple(period_params) + tuple(wanted),
            ).fetchall():
                item = _row_dict(row)
                sources[str(item.get("model") or "")][str(item.get("source") or "unknown")] += _integer(item.get("sessions"))

    mixes: Dict[str, Dict[str, Any]] = {
        model_id: {**{field: 0 for field in _MIX_FIELDS}, "calls": 0, "sessions": 0, "cash_usd": 0.0, "route": None, "route_calls": -1, "plan": False}
        for model_id in wanted
    }
    for row in rows:
        mix = mixes.get(str(row.get("model") or ""))
        if mix is None:
            continue
        for field in _MIX_FIELDS + ("calls", "sessions"):
            mix[field] += _integer(row.get(field))
        mix["cash_usd"] += _number(row.get("cash_usd"), 0)
        calls = _integer(row.get("calls"))
        if calls > mix["route_calls"]:
            mix["route_calls"] = calls
            mix["route"] = str(row.get("billing_provider") or "")
            mix["plan"] = _is_subscription_route(row.get("billing_provider"), row.get("billing_base_url")) or str(
                row.get("cost_status") or ""
            ) in {"included", "subscription"}

    references: Dict[str, Dict[str, int]] = {
        model_id: {field: mixes[model_id][field] for field in _MIX_FIELDS} for model_id in wanted
    }
    references[COMPARE_COMBINED] = {field: sum(mixes[model_id][field] for model_id in wanted) for field in _MIX_FIELDS}

    models: List[Dict[str, Any]] = []
    for model_id in wanted:
        mix = mixes[model_id]
        info = evidence.get(model_id, {})
        work = info.get("work_reliability") or {}
        latency = info.get("latency") or {}
        failures = info.get("failures") or {}
        plan = bool(mix["plan"]) or str(info.get("cost_kind") or "") == "subscription"
        prompt = mix["input_tokens"] + mix["cache_read_tokens"] + mix["cache_write_tokens"]
        calls = mix["calls"]
        rates = _list_rates(model_id, mix["route"] or "")
        same_work = {
            key: {"cost_usd": round(_price_mix(reference, rates), 4) if rates else None, "included": plan}
            for key, reference in references.items()
            if any(reference.values())
        }
        task_types = Counter(
            {str(task.get("task_type") or "General"): _integer(task.get("sessions")) for task in info.get("task_types") or []}
        )
        eligible = _integer(work.get("eligible_tasks"))
        bound = work.get("failure_rate_upper_bound_95")
        models.append(
            {
                "model_id": model_id,
                "display_name": info.get("display_name") or model_id,
                "route_label": info.get("route_label") or mix["route"] or "",
                "subscription": plan,
                "recorded": calls > 0,
                "cash_usd": round(mix["cash_usd"], 4),
                "workload": {
                    "calls": calls,
                    "sessions": mix["sessions"],
                    "context_per_call": round(prompt / calls) if calls else None,
                    "output_per_call": round(mix["output_tokens"] / calls) if calls else None,
                    "calls_per_session": round(calls / mix["sessions"], 1) if mix["sessions"] else None,
                    "sources": _share_list(sources.get(model_id, Counter()), sum(sources.get(model_id, Counter()).values())),
                    "task_types": _share_list(task_types, sum(task_types.values())),
                },
                "cache_hit_rate": round(mix["cache_read_tokens"] / prompt, 4) if prompt else None,
                "reliability": {
                    "eligible_tasks": eligible,
                    "failure_upper_bound": round(float(bound), 4) if bound is not None else None,
                    "unrecovered_rate": work.get("unrecovered_failure_rate"),
                    "proven": eligible >= floor and bound is not None,
                },
                "api_failures": {"rate": failures.get("rate"), "samples": _integer(failures.get("samples"))},
                "latency": {
                    "p50_seconds": latency.get("total_p50_seconds"),
                    "p95_seconds": latency.get("total_p95_seconds"),
                    "samples": _integer(latency.get("samples")),
                },
                "same_work": same_work,
            }
        )

    def leader(candidates: List[Tuple[float, str]], lowest: bool = True) -> Optional[str]:
        if len(candidates) < 2:
            return None
        return (min if lowest else max)(candidates)[1]

    proven = [model for model in models if model["reliability"]["proven"]]
    cost_leaders = {}
    for key in references:
        priced = [
            (model["same_work"][key]["cost_usd"], model["model_id"])
            for model in models
            if key in model["same_work"] and model["same_work"][key]["cost_usd"] is not None and not model["subscription"]
        ]
        cost_leaders[key] = leader(priced)
    leaders = {
        "cost": cost_leaders,
        "reliability": leader([(model["reliability"]["failure_upper_bound"], model["model_id"]) for model in proven]),
        "speed": leader(
            [
                (model["latency"]["p50_seconds"], model["model_id"])
                for model in models
                if model["latency"]["p50_seconds"] and model["latency"]["samples"] >= floor
            ]
        ),
        "cache": leader(
            [(model["cache_hit_rate"], model["model_id"]) for model in models if model["cache_hit_rate"] is not None and model["workload"]["calls"] >= floor],
            lowest=False,
        ),
    }

    # For each proven model: the cheapest model doing its work that is at least as reliable.
    picks: Dict[str, Optional[Dict[str, Any]]] = {}
    for baseline in proven:
        key = baseline["model_id"]
        if key not in references or not any(references[key].values()):
            continue
        limit = baseline["reliability"]["failure_upper_bound"] + margin
        options = []
        for model in proven:
            if model["model_id"] == key or model["reliability"]["failure_upper_bound"] > limit:
                continue
            price = model["same_work"].get(key, {})
            if model["subscription"]:
                options.append((0.0, model["model_id"], True))
            elif price.get("cost_usd") is not None:
                options.append((price["cost_usd"], model["model_id"], False))
        own = baseline["same_work"].get(key, {})
        own_cost = 0.0 if baseline["subscription"] else own.get("cost_usd")
        options = [option for option in options if own_cost is None or option[0] < own_cost]
        if options:
            cost, model_id, plan = min(options)
            picks[key] = {"model_id": model_id, "cost_usd": round(cost, 4), "uses_plan_quota": plan, "baseline_cost_usd": own_cost}
        else:
            picks[key] = None

    warnings: List[str] = []
    recorded = [model for model in models if model["workload"]["context_per_call"]]
    if len(recorded) >= 2:
        largest = max(recorded, key=lambda model: model["workload"]["context_per_call"])
        smallest = min(recorded, key=lambda model: model["workload"]["context_per_call"])
        ratio = largest["workload"]["context_per_call"] / max(1, smallest["workload"]["context_per_call"])
        if ratio >= COMPARE_CONTEXT_GAP:
            warnings.append(
                f"{largest['display_name']} averaged {_compact_count(largest['workload']['context_per_call'])} tokens of context per call, "
                f"{smallest['display_name']} {_compact_count(smallest['workload']['context_per_call'])} ({ratio:.{1 if ratio < 10 else 0}f}×): they did different-sized "
                "jobs, so reliability and speed are not like-for-like."
            )
        for facet, lead, verb in (
            ("sources", "Different places", "ran mostly on"),
            ("task_types", "Different kinds of work", "was mostly"),
        ):
            dominant = {
                model["display_name"]: model["workload"][facet][0]
                for model in recorded
                if model["workload"][facet] and model["workload"][facet][0]["share"] >= 0.6
            }
            if len({item["name"] for item in dominant.values()}) > 1:
                parts = "; ".join(f"{name} {verb} {item['name']} ({item['share'] * 100:.0f}%)" for name, item in dominant.items())
                warnings.append(f"{lead}: {parts}.")
    missing = [model["display_name"] for model in models if not model["recorded"]]
    if missing:
        warnings.append(f"No main-conversation work recorded in this period for {', '.join(missing)}.")

    return {
        "models": models,
        "references": [COMPARE_COMBINED] + wanted,
        "leaders": leaders,
        "picks": picks,
        "warnings": warnings,
        "sample_floor": floor,
        "reliability_margin": margin,
        "max_models": COMPARE_MAX_MODELS,
        "period_days": days,
        "period": _period_payload(days, period_start, period_end),
        "definition": (
            "Cost for the same work prices one recorded main-conversation token mix on every model with Hermes' pricing tables, "
            "assuming the same cache hit rate; a subscription model shows its list price and is marked as included in the plan. "
            f"Reliability is the AI Models work-ledger failure bound (95% upper bound), shown only above the {floor}-task floor. "
            "Speed is the median total latency from the bounded agent logs. Each measure names its own leader. History shows "
            "whether tasks finished without model or API failures, not whether the answers were right."
        ),
        "generated_at": time.time(),
    }


def _compact_count(value: Any) -> str:
    number = _number(value, 0)
    if number >= 1_000_000:
        return f"{number / 1_000_000:.1f}M"
    if number >= 1_000:
        return f"{number / 1_000:.0f}k"
    return f"{number:.0f}"


__all__ = [name for name in globals() if not name.startswith("__")]
