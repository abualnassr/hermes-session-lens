"""Why a session cost what it did, and what Hermes' helper tasks cost.

A long agent session's bill is rarely new work: it is the same context
re-read on every call. The anatomy reads the session's API calls from the
agent log (prompt size, cache split, output) and its recorded tool results,
prices each call with Hermes' pricing tables, and states — with the numbers
and the config setting that governs it — where the money went. Read-only.
"""

from __future__ import annotations

import statistics

from ._common import *
from ._logparse import *
from ._watch import _call_cost, _call_rates, _is_subscription_route
from ._services import _cached_file_parse

ANATOMY_MAX_POINTS = 160
ANATOMY_COMPRESSION_DROP = 0.3
ANATOMY_TOP_RESULTS = 5


def _config_for_home(home: Path) -> Dict[str, Any]:
    def parse(text: str) -> Dict[str, Any]:
        try:
            import yaml
        except ImportError:
            return {}
        data = yaml.safe_load(text) or {}
        return data if isinstance(data, dict) else {}

    return _cached_file_parse(home / "config.yaml", parse) or {}


def _downsample(points: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
    """Keep at most `limit` points, always keeping the largest prompt in each stride."""
    if len(points) <= limit:
        return points
    stride = len(points) / limit
    kept = []
    for index in range(limit):
        chunk = points[int(index * stride): int((index + 1) * stride)] or [points[-1]]
        kept.append(max(chunk, key=lambda point: point["prompt"]))
    return kept


def _session_anatomy_sync(session_id: str) -> Dict[str, Any]:
    with _database() as db:
        sid = db.resolve_session_id(session_id)
        if not sid:
            raise HTTPException(status_code=404, detail="Session not found")
        connection = _db_connection(db)
        union = bool(getattr(db, "union_profiles", None))
        columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(sessions)").fetchall()}
        list_price = "s.list_price_usd" if "list_price_usd" in columns else "NULL"
        profile = ", s.__profile AS profile" if union else ""
        session = _row_dict(
            connection.execute(
                f"""
                SELECT s.id, s.title, s.model, s.billing_provider, s.started_at, s.ended_at, s.last_activity_at,
                       s.input_tokens, s.output_tokens, s.cache_read_tokens, s.cache_write_tokens, s.api_call_count,
                       s.estimated_cost_usd, s.actual_cost_usd, s.cost_status, s.cost_source,
                       {list_price} AS list_price_usd{profile}
                FROM ({_accounted_sessions_sql(connection)}) s WHERE s.id = ?
                """,
                (sid,),
            ).fetchone()
        )
        tool_rows = [
            _row_dict(row)
            for row in connection.execute(
                """
                SELECT tool_name, length(content) AS chars, timestamp
                FROM messages
                WHERE session_id = ? AND role = 'tool' AND content IS NOT NULL
                """,
                (sid,),
            ).fetchall()
        ]
        usage_rows = [
            _row_dict(row)
            for row in connection.execute(
                f"""
                SELECT coalesce(task, '') AS task, model, billing_provider, api_call_count,
                       estimated_cost_usd, actual_cost_usd, cost_status,
                       {'list_price_usd' if 'list_price_usd' in {str(r[1]) for r in connection.execute('PRAGMA table_info(session_model_usage)').fetchall()} else 'NULL AS list_price_usd'}
                FROM session_model_usage WHERE session_id = ?
                """,
                (sid,),
            ).fetchall()
        ]

    home = _profile_home_path(session["profile"]) if union and session.get("profile") else _hermes_home()
    config = _config_for_home(home)
    compression = config.get("compression") if isinstance(config.get("compression"), dict) else {}
    auxiliary = config.get("auxiliary") if isinstance(config.get("auxiliary"), dict) else {}

    # ── Per-call series from the agent log ────────────────────────────────
    events = sorted(
        (event for event in _runtime_events().get("api", []) if str(event.get("session_id") or "") == sid),
        key=lambda event: _number(event.get("timestamp"), 0),
    )
    points: List[Dict[str, Any]] = []
    split = {"uncached_input": 0.0, "cache_read": 0.0, "output": 0.0}
    route = "cash"
    unpriced = 0
    compressions: List[Dict[str, Any]] = []
    previous_prompt = 0
    for event in events:
        prompt = _integer(event.get("input_tokens"))
        cached = min(_integer(event.get("cache_read_tokens")), prompt)
        output = _integer(event.get("output_tokens"))
        rates = _call_rates(str(event.get("model") or ""), str(event.get("provider") or ""))
        if _is_subscription_route(event.get("provider")):
            route = "subscription"
        cost = None
        if rates is None:
            unpriced += 1
        else:
            input_rate, output_rate, cache_rate = rates
            split["uncached_input"] += (prompt - cached) * input_rate
            split["cache_read"] += cached * cache_rate
            split["output"] += output * output_rate
            cost = _call_cost(event, rates)
        if previous_prompt and prompt < previous_prompt * (1 - ANATOMY_COMPRESSION_DROP):
            compressions.append({"timestamp": event.get("timestamp"), "from_tokens": previous_prompt, "to_tokens": prompt})
        previous_prompt = prompt
        points.append({"timestamp": event.get("timestamp"), "prompt": prompt, "cached": cached, "output": output, "cost_usd": cost})
    prompts = [point["prompt"] for point in points]
    priced_total = sum(split.values())
    logged = len(points)
    recorded_calls = _integer(session.get("api_call_count"))

    # ── What fed the context ──────────────────────────────────────────────
    by_tool: Dict[str, Dict[str, Any]] = {}
    for row in tool_rows:
        name = str(row.get("tool_name") or "unknown")
        entry = by_tool.setdefault(name, {"tool": name, "results": 0, "chars": 0, "largest_chars": 0})
        chars = _integer(row.get("chars"))
        entry["results"] += 1
        entry["chars"] += chars
        entry["largest_chars"] = max(entry["largest_chars"], chars)
    tool_chars = sum(entry["chars"] for entry in by_tool.values())
    top_tools = sorted(by_tool.values(), key=lambda entry: -entry["chars"])[:8]
    for entry in top_tools:
        entry["tokens_estimate"] = entry["chars"] // 4
        entry["share"] = round(entry["chars"] / tool_chars, 3) if tool_chars else 0.0

    # ── Helper tasks recorded for this session ────────────────────────────
    helpers = []
    for row in usage_rows:
        if not row.get("task"):
            continue
        cash = _cost_view(row).get("display_cost_usd") or 0.0
        helpers.append(
            {
                "task": row["task"],
                "model": row.get("model"),
                "provider": row.get("billing_provider"),
                "calls": _integer(row.get("api_call_count")),
                "cash_usd": round(_number(cash), 4),
                "list_price_usd": round(_number(row.get("list_price_usd")), 4) if row.get("list_price_usd") else None,
            }
        )
    helpers.sort(key=lambda item: -(item["cash_usd"] + (item["list_price_usd"] or 0)))

    # ── Findings: each one states the numbers and the setting behind it ───
    findings: List[Dict[str, Any]] = []
    cache_share = split["cache_read"] / priced_total if priced_total else None
    if prompts and logged >= 20 and cache_share is not None and cache_share >= 0.5:
        threshold = compression.get("threshold_tokens")
        findings.append(
            {
                "kind": "context_rereads",
                "headline": f"{cache_share * 100:.0f}% of this session's cost re-read the same context",
                "detail": (
                    f"{logged} logged calls averaged {statistics.mean(prompts):,.0f} prompt tokens (largest {max(prompts):,}). "
                    "Each call re-sends the whole conversation; a cache read is cheap per token but not across "
                    f"{logged} calls. Compression runs when the context passes "
                    + (f"compression.threshold_tokens = {int(threshold):,}" if threshold else "its threshold")
                    + "; a lower threshold, or a fresh session for a new task, shortens every later call."
                ),
                "setting": "compression.threshold_tokens",
                "current": threshold,
            }
        )
    prompt_total = sum(prompts)
    cached_total = sum(point["cached"] for point in points)
    miss_share = (prompt_total - cached_total) / prompt_total if prompt_total else None
    miss_cost_share = split["uncached_input"] / priced_total if priced_total else None
    if logged >= 20 and miss_cost_share is not None and miss_cost_share >= 0.4 and miss_share is not None:
        findings.append(
            {
                "kind": "cache_misses",
                "headline": f"{miss_cost_share * 100:.0f}% of the cost was prompt that missed the cache",
                "detail": (
                    f"Only {miss_share * 100:.0f}% of prompt tokens missed it, but they are billed at the full input rate "
                    f"(${split['uncached_input']:,.2f} of the ${priced_total:,.2f} priced from the log). The cache serves a "
                    "prompt only while its beginning stays identical; compression, pruning that rewrites earlier results, "
                    "and model or route switches all restart it."
                ),
                "setting": None,
                "current": None,
            }
        )
    if top_tools and tool_chars and top_tools[0]["share"] >= 0.3 and top_tools[0]["tokens_estimate"] >= 20_000:
        top = top_tools[0]
        prune = compression.get("proactive_prune_min_result_chars")
        findings.append(
            {
                "kind": "heavy_tool_results",
                "headline": f"{top['tool']} results were {top['share'] * 100:.0f}% of what tools put into context",
                "detail": (
                    f"{top['results']} results, about {top['tokens_estimate']:,} tokens (largest {top['largest_chars'] // 4:,}). "
                    "Every later call carries them until compression drops them; "
                    + (f"proactive pruning applies to results over compression.proactive_prune_min_result_chars = {int(prune):,} characters."
                       if prune else "proactive pruning of large results is governed by compression.proactive_prune_*.")
                ),
                "setting": "compression.proactive_prune_min_result_chars",
                "current": prune,
            }
        )
    for helper in helpers:
        spend = helper["cash_usd"] + (helper["list_price_usd"] or 0)
        if spend < 0.5:
            continue
        task_config = auxiliary.get(helper["task"]) if isinstance(auxiliary.get(helper["task"]), dict) else {}
        configured = task_config.get("model") or None
        findings.append(
            {
                "kind": "helper_task_cost",
                "headline": f"The {helper['task']} helper ran on {helper['model']} and cost "
                + (f"${helper['cash_usd']:,.2f}" if helper["cash_usd"] else f"≈ ${helper['list_price_usd']:,.2f} of subscription use at list price"),
                "detail": (
                    f"{helper['calls']} calls. Helper tasks follow the main model unless auxiliary.{helper['task']}.model names "
                    "another" + (f" (now: {configured})." if configured else " (not set for this profile).")
                    + " A small, cheap model is usually enough for this task."
                ),
                "setting": f"auxiliary.{helper['task']}.model",
                "current": configured,
            }
        )
    started = _number(session.get("started_at"), 0)
    last = _number(session.get("last_activity_at") or session.get("ended_at"), started)
    span_hours = max(0.0, last - started) / 3600 if started else 0.0
    if span_hours >= 24 and recorded_calls >= 200:
        findings.append(
            {
                "kind": "long_session",
                "headline": f"One session carried {recorded_calls:,} calls over {span_hours:,.0f} hours",
                "detail": "A conversation that changes task keeps paying for the old task's context; starting a new session per task resets it.",
                "setting": None,
                "current": None,
            }
        )

    cost = _cost_view(session)
    return {
        "session_id": sid,
        "title": _clean_text(session.get("title"), 120) or sid,
        "profile": session.get("profile"),
        "model": session.get("model"),
        "route": route,
        "cost": {**cost, "list_price_usd": session.get("list_price_usd")},
        "tokens": {
            "input": _integer(session.get("input_tokens")),
            "cache_read": _integer(session.get("cache_read_tokens")),
            "cache_write": _integer(session.get("cache_write_tokens")),
            "output": _integer(session.get("output_tokens")),
        },
        "calls": {"recorded": recorded_calls, "logged": logged, "unpriced": unpriced},
        "context": {
            "points": _downsample(points, ANATOMY_MAX_POINTS),
            "mean_prompt_tokens": round(statistics.mean(prompts)) if prompts else None,
            "median_prompt_tokens": round(statistics.median(prompts)) if prompts else None,
            "max_prompt_tokens": max(prompts) if prompts else None,
            "first_prompt_tokens": prompts[0] if prompts else None,
            "compressions": compressions,
        },
        "priced_split_usd": {key: round(value, 4) for key, value in split.items()},
        "priced_total_usd": round(priced_total, 4),
        "cache_read_share": round(cache_share, 3) if cache_share is not None else None,
        "cache_miss_share": round(miss_share, 3) if miss_share is not None else None,
        "cache_miss_cost_share": round(miss_cost_share, 3) if miss_cost_share is not None else None,
        "tool_context": {"chars": tool_chars, "tokens_estimate": tool_chars // 4, "top": top_tools},
        "helpers": helpers,
        "findings": findings,
        "span_hours": round(span_hours, 2),
        "definition": (
            "Calls come from Hermes' agent log for this session (prompt size, cache split, output) and are priced "
            "with Hermes' pricing tables; subscription routes at API list price. Tool context is the recorded "
            "result length ÷ 4. A drop of more than 30% in prompt size between calls is counted as compression."
        ),
        "generated_at": time.time(),
    }


def _helper_tasks_sync(days: int, start_at: Optional[float] = None, end_at: Optional[float] = None) -> Dict[str, Any]:
    """Helper-task spend (titles, vision, approvals, reviews, compression…) by task and model."""
    period_start, period_end = _period_bounds(days, start_at, end_at)
    period_sql, period_params = _period_sql("s.started_at", period_start, period_end)
    with _database() as db:
        connection = _db_connection(db)
        union = bool(getattr(db, "union_profiles", None))
        usage_columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(session_model_usage)").fetchall()}
        if "task" not in usage_columns:
            return {"tasks": [], "period_days": days, "generated_at": time.time()}
        list_price = "u.list_price_usd" if "list_price_usd" in usage_columns else "NULL"
        profile = "s.__profile" if union else "NULL"
        rows = [
            _row_dict(row)
            for row in connection.execute(
                f"""
                SELECT u.task, u.model, u.billing_provider, {profile} AS profile,
                       COUNT(DISTINCT u.session_id) AS sessions, SUM(u.api_call_count) AS calls,
                       SUM(CASE WHEN u.actual_cost_usd > 0 THEN u.actual_cost_usd
                                WHEN u.estimated_cost_usd > 0 THEN u.estimated_cost_usd ELSE 0 END) AS cash_usd,
                       SUM(coalesce({list_price}, 0)) AS list_price_usd,
                       SUM(u.input_tokens + u.output_tokens + u.cache_read_tokens) AS tokens
                FROM session_model_usage u
                JOIN sessions s ON s.id = u.session_id{_same_profile(db, "s", "u")}
                WHERE coalesce(u.task, '') != '' AND {period_sql}
                GROUP BY u.task, u.model, u.billing_provider, {profile}
                """,
                tuple(period_params),
            ).fetchall()
        ]
    homes: Dict[Any, Dict[str, Any]] = {}
    tasks = []
    for row in rows:
        key = row.get("profile")
        if key not in homes:
            home = _profile_home_path(key) if key else _hermes_home()
            auxiliary = _config_for_home(home).get("auxiliary")
            homes[key] = auxiliary if isinstance(auxiliary, dict) else {}
        task_config = homes[key].get(row["task"]) if isinstance(homes[key].get(row["task"]), dict) else {}
        tasks.append(
            {
                "task": row["task"],
                "model": row.get("model"),
                "provider": row.get("billing_provider"),
                "profile": row.get("profile"),
                "sessions": _integer(row.get("sessions")),
                "calls": _integer(row.get("calls")),
                "tokens": _integer(row.get("tokens")),
                "cash_usd": round(_number(row.get("cash_usd")), 4),
                "list_price_usd": round(_number(row.get("list_price_usd")), 4),
                "configured_model": task_config.get("model") or None,
                "setting": f"auxiliary.{row['task']}.model",
            }
        )
    tasks.sort(key=lambda item: -(item["cash_usd"] + item["list_price_usd"]))
    return {
        "tasks": tasks,
        "totals": {
            "cash_usd": round(sum(item["cash_usd"] for item in tasks), 4),
            "list_price_usd": round(sum(item["list_price_usd"] for item in tasks), 4),
            "calls": sum(item["calls"] for item in tasks),
        },
        "period_days": days,
        "period": _period_payload(days, period_start, period_end),
        "definition": (
            "Hermes runs helper tasks beside the main conversation — titles, vision, approvals, background reviews, "
            "compression. Each follows the main model unless auxiliary.<task>.model in config.yaml names another."
        ),
        "generated_at": time.time(),
    }


__all__ = [name for name in globals() if not name.startswith("__")]
