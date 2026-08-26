"""FastAPI routes and synchronous read-only payload builders."""

from __future__ import annotations

try:
    from ._common import *
    from ._logparse import *
    from ._classify import *
    from ._reliability import *
    from ._providers import *
except ImportError:  # pragma: no cover - direct Hermes file loading
    from _common import *
    from _logparse import *
    from _classify import *
    from _reliability import *
    from _providers import *

router = APIRouter()

def _fts_query(raw: str) -> str:
    tokens = re.findall(r"[^\W_][\w./\\:-]*", raw, flags=re.UNICODE)
    safe = []
    for token in tokens[:16]:
        cleaned = token.replace('"', "").strip("*:-")
        if cleaned:
            safe.append(f'"{cleaned}"*')
    return " ".join(safe)


def _search_hits(db: Any, query: str) -> Dict[str, str]:
    fts = _fts_query(query)
    if not fts:
        return {}
    try:
        matches = db.search_messages(
            query=fts,
            limit=MAX_SEARCH_MATCHES,
            fields=("session_id", "snippet", "role"),
        )
    except Exception:
        return {}
    snippets: Dict[str, str] = {}
    for match in matches:
        session_id = str(match.get("session_id") or "")
        if session_id and session_id not in snippets:
            snippets[session_id] = _clean_text(match.get("snippet"), 260)
    return snippets


def _list_sessions_sync(
    *,
    days: int,
    start_at: Optional[float] = None,
    end_at: Optional[float] = None,
    query: str,
    sort: str,
    failures_only: bool,
    include_archived: bool,
    limit: int,
    offset: int,
) -> Dict[str, Any]:
    period_start, period_end = _period_bounds(days, start_at, end_at)
    period_sql, period_params = _period_sql("s.started_at", period_start, period_end)
    with _database() as db:
        snippets = _search_hits(db, query) if query else {}
        params: List[Any] = list(period_params)
        where = [period_sql, "coalesce(s.hidden, 0) = 0"]
        if not include_archived:
            where.append("coalesce(s.archived, 0) = 0")
        from_where = f"""
            FROM sessions s
            WHERE {' AND '.join(where)}
        """
        select_sql = """
            SELECT s.id, s.source, s.display_name, s.model, s.started_at, s.ended_at,
                   s.end_reason, s.parent_session_id,
                   s.message_count, s.tool_call_count, s.input_tokens, s.output_tokens,
                   s.cache_read_tokens, s.cache_write_tokens, s.reasoning_tokens,
                   s.cwd, s.git_branch, s.git_repo_root, s.billing_provider,
                   s.billing_mode, s.estimated_cost_usd, s.actual_cost_usd,
                   s.cost_status, s.cost_source, s.title, s.last_activity_at,
                   s.last_activity_description, s.api_call_count, s.profile_name,
                   s.archived, s.pinned
            """
        connection = _db_connection(db)
        if query:
            like = f"%{query.strip().lower()}%"
            text_filters = [
                "lower(s.id) LIKE ?",
                "lower(coalesce(s.title, '')) LIKE ?",
                "lower(coalesce(s.model, '')) LIKE ?",
                "lower(coalesce(s.cwd, '')) LIKE ?",
                "lower(coalesce(s.source, '')) LIKE ?",
            ]
            rows_by_id = {
                str(row["id"]): row
                for row in connection.execute(
                    select_sql + from_where + " AND (" + " OR ".join(text_filters) + ")",
                    tuple(params + [like] * len(text_filters)),
                ).fetchall()
            }
            snippet_ids = list(snippets)
            for chunk_start in range(0, len(snippet_ids), 900):
                chunk = snippet_ids[chunk_start : chunk_start + 900]
                placeholders = ",".join("?" for _ in chunk)
                for row in connection.execute(
                    select_sql + from_where + f" AND s.id IN ({placeholders})",
                    tuple(params + chunk),
                ).fetchall():
                    rows_by_id[str(row["id"])] = row
            rows = list(rows_by_id.values())
        else:
            rows = connection.execute(select_sql + from_where, tuple(params)).fetchall()
        failure_counts = _confirmed_failure_counts(
            _db_connection(db), " AND ".join(where), params
        )
        materials = []
        for row in rows:
            material = _row_dict(row)
            material["failure_count"] = failure_counts.get(str(material.get("id")), 0)
            if failures_only and not material["failure_count"]:
                continue
            materials.append(material)

        def sort_key(material: Mapping[str, Any]) -> Tuple[Any, ...]:
            started = _number(material.get("started_at"), 0)
            recent = _number(material.get("last_activity_at"), started)
            if sort == "recent":
                return (recent,)
            if sort == "cost":
                actual = material.get("actual_cost_usd")
                estimated = material.get("estimated_cost_usd")
                cost = actual if actual is not None else (estimated if estimated is not None else -1)
                return (_number(cost, -1), started)
            if sort == "tokens":
                return (
                    sum(
                        _integer(material.get(key))
                        for key in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")
                    ),
                    started,
                )
            if sort == "tools":
                return (_integer(material.get("tool_call_count")), started)
            return (_integer(material.get("failure_count")), recent)

        materials.sort(key=sort_key, reverse=True)
        total = len(materials)
        sessions = []
        for material in materials[offset : offset + limit]:
            item = _session_payload(material)
            item["search_snippet"] = snippets.get(str(item.get("id")))
            sessions.append(item)
        return {
            "sessions": sessions,
            "pagination": {
                "total": int(total),
                "limit": limit,
                "offset": offset,
                "returned": len(sessions),
                "has_more": offset + len(sessions) < total,
            },
            "filters": {
                "days": days,
                "start_at": period_start or None,
                "end_at": period_end,
                "query": query,
                "sort": sort,
                "failures_only": failures_only,
                "include_archived": include_archived,
            },
            "generated_at": time.time(),
        }


@router.get("/health")
async def health() -> Dict[str, Any]:
    def read() -> Dict[str, Any]:
        with _database() as db:
            version_row = _db_connection(db).execute("SELECT version FROM schema_version").fetchone()
            _db_connection(db).execute("SELECT 1 FROM sessions LIMIT 1").fetchone()
            return {
                "ok": True,
                "plugin_version": PLUGIN_VERSION,
                "schema_version": _integer(version_row[0] if version_row else 0),
                "read_only": bool(getattr(db, "read_only", False)),
            }

    return await asyncio.to_thread(read)


@router.get("/sessions")
async def sessions(
    days: int = Query(30, ge=0, le=3650),
    start_at: Optional[float] = Query(None, ge=0),
    end_at: Optional[float] = Query(None, ge=0),
    q: str = Query("", max_length=240),
    sort: str = Query("failures"),
    failures_only: bool = False,
    include_archived: bool = False,
    limit: int = Query(50, ge=1, le=MAX_SESSION_PAGE),
    offset: int = Query(0, ge=0),
) -> Dict[str, Any]:
    return await asyncio.to_thread(
        _list_sessions_sync,
        days=days,
        start_at=start_at,
        end_at=end_at,
        query=q.strip(),
        sort=sort,
        failures_only=failures_only,
        include_archived=include_archived,
        limit=limit,
        offset=offset,
    )


def _session_detail_sync(session_id: str) -> Dict[str, Any]:
    with _database() as db:
        sid = db.resolve_session_id(session_id)
        if not sid:
            raise HTTPException(status_code=404, detail="Session not found")
        row = _db_connection(db).execute(
            "SELECT s.* FROM sessions s WHERE s.id = ?",
            (sid,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Session not found")

        session_material = _row_dict(row)
        session_material["failure_count"] = _confirmed_failure_counts(
            _db_connection(db), "s.id = ?", (sid,)
        ).get(str(sid), 0)

        usage_rows = [
            _row_dict(item)
            for item in _db_connection(db).execute(
                """
                SELECT model, billing_provider, billing_mode, task, api_call_count,
                       input_tokens, output_tokens, cache_read_tokens,
                       cache_write_tokens, reasoning_tokens, estimated_cost_usd,
                       actual_cost_usd, cost_status, cost_source, first_seen, last_seen
                FROM session_model_usage
                WHERE session_id = ?
                ORDER BY (input_tokens + output_tokens + cache_read_tokens + cache_write_tokens) DESC
                """,
                (sid,),
            ).fetchall()
        ]
        for usage in usage_rows:
            usage["total_tokens"] = sum(
                _integer(usage.get(key))
                for key in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")
            )
            usage.update(_cost_view(usage))

        event_rows = _db_connection(db).execute(
            """
            SELECT id, session_id, role, tool_call_id, tool_calls, tool_name,
                   effect_disposition, timestamp, finish_reason, content
            FROM messages
            WHERE session_id = ? AND coalesce(active, 1) = 1
              AND (tool_calls IS NOT NULL OR role = 'tool' OR finish_reason IN ('error','agent_error','content_filter'))
            ORDER BY id DESC LIMIT ?
            """,
            (sid, MAX_ANALYSIS_EVENTS),
        ).fetchall()
        analysis = _analyze_events(reversed([_row_dict(item) for item in event_rows]))

        role_counts = {
            role: count
            for role, count in _db_connection(db).execute(
                "SELECT role, COUNT(*) FROM messages WHERE session_id = ? AND coalesce(active,1)=1 GROUP BY role",
                (sid,),
            ).fetchall()
        }
        delegations = [
            {
                "delegation_id": item["delegation_id"],
                "state": item["state"],
                "dispatched_at": item["dispatched_at"],
                "completed_at": item["completed_at"],
                "updated_at": item["updated_at"],
                "delivery_state": item["delivery_state"],
                "parent_session_id": item["parent_session_id"],
            }
            for item in _db_connection(db).execute(
                """
                SELECT delegation_id, state, dispatched_at, completed_at, updated_at,
                       delivery_state, parent_session_id
                FROM async_delegations
                WHERE origin_session = ? OR parent_session_id = ?
                ORDER BY dispatched_at DESC LIMIT 200
                """,
                (sid, sid),
            ).fetchall()
        ]

        return {
            "session": _session_payload(session_material),
            "models": usage_rows,
            "message_roles": role_counts,
            "tools": analysis["events"],
            "failures": analysis["failures"],
            "files": analysis["files"],
            "skills": analysis["skills"],
            "delegations": delegations,
            "analysis": {
                "event_limit": MAX_ANALYSIS_EVENTS,
                "events_analyzed": len(event_rows),
                "truncated": len(event_rows) >= MAX_ANALYSIS_EVENTS,
                "failure_detection": "recorded error state plus conservative tool-result signatures",
                "file_detection": "recorded tool path arguments and bounded command-path extraction",
            },
            "generated_at": time.time(),
        }


@router.get("/sessions/{session_id}")
async def session_detail(session_id: str) -> Dict[str, Any]:
    return await asyncio.to_thread(_session_detail_sync, session_id)


def _trace_sync(session_id: str, limit: int, offset: int) -> Dict[str, Any]:
    with _database() as db:
        sid = db.resolve_session_id(session_id)
        if not sid:
            raise HTTPException(status_code=404, detail="Session not found")
        total = _db_connection(db).execute(
            """
            SELECT COUNT(*) FROM messages
            WHERE session_id=? AND coalesce(active,1)=1 AND role!='system'
            """,
            (sid,),
        ).fetchone()[0]
        rows = _db_connection(db).execute(
            """
            SELECT id, role, content, tool_call_id, tool_calls, tool_name,
                   effect_disposition, timestamp, token_count, finish_reason,
                   reasoning_content, compacted, display_kind
            FROM messages
            WHERE session_id=? AND coalesce(active,1)=1 AND role!='system'
            ORDER BY id ASC LIMIT ? OFFSET ?
            """,
            (sid, limit, offset),
        ).fetchall()

    events: List[Dict[str, Any]] = []
    for raw in rows:
        row = _row_dict(raw)
        role = str(row.get("role") or "unknown").lower()
        base = {
            "message_id": row.get("id"),
            "timestamp": row.get("timestamp"),
            "finish_reason": row.get("finish_reason"),
            "token_count": _integer(row.get("token_count")),
            "compacted": bool(row.get("compacted")),
            "display_kind": row.get("display_kind"),
        }
        reasoning = _clean_content(row.get("reasoning_content"))
        if role == "assistant" and reasoning:
            events.append({**base, "id": f"{row.get('id')}:reasoning", "kind": "reasoning", "content": reasoning})

        raw_content = str(row.get("content") or "")
        schedule_scaffolding = role == "user" and raw_content.lstrip().startswith(
            "[IMPORTANT: You are running as a scheduled cron job."
        )
        content = "" if schedule_scaffolding else _clean_content(raw_content)
        if content:
            kind = "tool_result" if role == "tool" else role
            event = {**base, "id": f"{row.get('id')}:{kind}", "kind": kind, "content": content}
            if role == "tool":
                failed = _is_failure(
                    role=role,
                    content=row.get("content"),
                    finish_reason=row.get("finish_reason"),
                    effect_disposition=row.get("effect_disposition"),
                )
                event.update(
                    {
                        "tool_name": row.get("tool_name") or "tool",
                        "tool_call_id": row.get("tool_call_id"),
                        "status": "failed" if failed else "completed",
                    }
                )
            events.append(event)

        for index, call in enumerate(_iter_tool_calls(row.get("tool_calls"))):
            events.append(
                {
                    **base,
                    "id": f"{row.get('id')}:tool:{index}",
                    "kind": "tool_call",
                    "tool_name": call["name"],
                    "tool_call_id": call.get("call_id"),
                    "content": _argument_summary(call["arguments"]),
                }
            )

    return {
        "session_id": sid,
        "events": events,
        "pagination": {
            "total_messages": int(total),
            "limit": limit,
            "offset": offset,
            "returned_messages": len(rows),
            "has_more": offset + len(rows) < total,
        },
        "privacy": {
            "system_prompts_included": False,
            "schedule_prompts_included": False,
            "content_redacted": True,
            "content_limit_chars": MAX_TRACE_CONTENT_CHARS,
        },
        "generated_at": time.time(),
    }


@router.get("/sessions/{session_id}/trace")
async def session_trace(
    session_id: str,
    limit: int = Query(100, ge=1, le=MAX_TRACE_PAGE),
    offset: int = Query(0, ge=0),
) -> Dict[str, Any]:
    return await asyncio.to_thread(_trace_sync, session_id, limit, offset)


@router.get("/telemetry")
async def telemetry(
    days: int = Query(30, ge=0, le=3650),
    start_at: Optional[float] = Query(None, ge=0),
    end_at: Optional[float] = Query(None, ge=0),
    session_id: str = Query("", max_length=240),
) -> Dict[str, Any]:
    return await asyncio.to_thread(
        _telemetry_sync,
        days,
        session_id.strip(),
        start_at,
        end_at,
    )


def _overview_sync(
    days: int,
    start_at: Optional[float] = None,
    end_at: Optional[float] = None,
) -> Dict[str, Any]:
    period_start, period_end = _period_bounds(days, start_at, end_at)
    session_period_sql, session_period_params = _period_sql(
        "started_at", period_start, period_end
    )
    joined_period_sql, joined_period_params = _period_sql(
        "s.started_at", period_start, period_end
    )
    with _database() as db:
        totals_row = _db_connection(db).execute(
            f"""
            SELECT COUNT(*) AS sessions,
                   coalesce(SUM(message_count),0) AS messages,
                   coalesce(SUM(tool_call_count),0) AS tool_calls,
                   coalesce(SUM(input_tokens),0) AS input_tokens,
                   coalesce(SUM(output_tokens),0) AS output_tokens,
                   coalesce(SUM(cache_read_tokens),0) AS cache_read_tokens,
                   coalesce(SUM(cache_write_tokens),0) AS cache_write_tokens,
                   coalesce(SUM(reasoning_tokens),0) AS reasoning_tokens,
                   coalesce(SUM(api_call_count),0) AS api_calls,
                   coalesce(SUM(CASE
                       WHEN actual_cost_usd IS NOT NULL AND actual_cost_usd > 0 THEN actual_cost_usd
                       WHEN lower(coalesce(cost_status,'')) IN ('included','subscription','free') THEN 0
                       WHEN estimated_cost_usd IS NOT NULL AND estimated_cost_usd > 0 THEN estimated_cost_usd
                       ELSE 0 END),0) AS display_cost_usd,
                   coalesce(SUM(CASE WHEN actual_cost_usd > 0 THEN 1 ELSE 0 END),0) AS actual_cost_sessions,
                   coalesce(SUM(CASE WHEN coalesce(actual_cost_usd,0) <= 0 AND estimated_cost_usd > 0 THEN 1 ELSE 0 END),0) AS estimated_cost_sessions,
                   coalesce(SUM(CASE WHEN lower(coalesce(cost_status,'')) IN ('included','subscription','free') THEN 1 ELSE 0 END),0) AS included_cost_sessions,
                    coalesce(SUM(CASE WHEN coalesce(actual_cost_usd,0) <= 0 AND coalesce(estimated_cost_usd,0) <= 0 AND lower(coalesce(cost_status,'')) NOT IN ('included','subscription','free') THEN 1 ELSE 0 END),0) AS unpriced_sessions
            FROM sessions
            WHERE {session_period_sql} AND coalesce(hidden,0)=0
            """,
            tuple(session_period_params),
        ).fetchone()
        totals = _row_dict(totals_row)
        totals["failures"] = sum(
            _confirmed_failure_counts(
                _db_connection(db),
                joined_period_sql + " AND coalesce(s.hidden,0)=0",
                joined_period_params,
            ).values()
        )
        totals["total_tokens"] = sum(
            _integer(totals.get(key))
            for key in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")
        )
        priced_sessions = _integer(totals.get("actual_cost_sessions")) + _integer(
            totals.get("estimated_cost_sessions")
        )
        included_sessions = _integer(totals.get("included_cost_sessions"))
        unpriced_sessions = _integer(totals.get("unpriced_sessions"))
        totals["cost_kind"] = (
            "estimated"
            if priced_sessions > 0
            else ("included" if included_sessions > 0 and unpriced_sessions == 0 else "unpriced")
        )

        daily = [
            _row_dict(row)
            for row in _db_connection(db).execute(
                f"""
                SELECT date(started_at, 'unixepoch', 'localtime') AS day,
                       COUNT(*) AS sessions,
                       coalesce(SUM(input_tokens + output_tokens + cache_read_tokens + cache_write_tokens),0) AS total_tokens,
                       coalesce(SUM(tool_call_count),0) AS tool_calls,
                       coalesce(SUM(CASE
                           WHEN actual_cost_usd > 0 THEN actual_cost_usd
                           WHEN estimated_cost_usd > 0 THEN estimated_cost_usd
                           ELSE 0 END),0) AS cost_usd
                FROM sessions
                WHERE {session_period_sql} AND coalesce(hidden,0)=0
                GROUP BY day ORDER BY day
                """,
                tuple(session_period_params),
            ).fetchall()
        ]
        models = [
            _row_dict(row)
            for row in _db_connection(db).execute(
                f"""
                SELECT u.model, u.billing_provider, COUNT(DISTINCT u.session_id) AS sessions,
                       SUM(u.api_call_count) AS api_calls,
                       SUM(u.input_tokens) AS input_tokens,
                       SUM(u.output_tokens) AS output_tokens,
                       SUM(u.cache_read_tokens) AS cache_read_tokens,
                       SUM(u.cache_write_tokens) AS cache_write_tokens,
                       SUM(u.reasoning_tokens) AS reasoning_tokens,
                       SUM(u.input_tokens + u.output_tokens + u.cache_read_tokens + u.cache_write_tokens) AS total_tokens,
                       SUM(CASE WHEN u.actual_cost_usd > 0 THEN u.actual_cost_usd ELSE u.estimated_cost_usd END) AS cost_usd,
                       SUM(CASE WHEN lower(coalesce(u.cost_status,'')) IN ('included','subscription','free') THEN 1 ELSE 0 END) AS included_rows
                FROM session_model_usage u
                JOIN sessions s ON s.id = u.session_id
                WHERE {joined_period_sql} AND coalesce(s.hidden,0)=0
                GROUP BY u.model, u.billing_provider
                ORDER BY total_tokens DESC LIMIT 30
                """,
                tuple(joined_period_params),
            ).fetchall()
        ]
        for model in models:
            model["cost_kind"] = (
                "estimated"
                if _number(model.get("cost_usd")) > 0
                else ("included" if _integer(model.get("included_rows")) > 0 else "unpriced")
            )
        sources = [
            _row_dict(row)
            for row in _db_connection(db).execute(
                f"""
                SELECT coalesce(source,'unknown') AS source, COUNT(*) AS sessions,
                       SUM(input_tokens + output_tokens + cache_read_tokens + cache_write_tokens) AS total_tokens,
                       SUM(tool_call_count) AS tool_calls
                FROM sessions
                WHERE {session_period_sql} AND coalesce(hidden,0)=0
                GROUP BY source ORDER BY sessions DESC
                """,
                tuple(session_period_params),
            ).fetchall()
        ]
        outcome_counts: Counter[str] = Counter()
        for row in _db_connection(db).execute(
            f"""
            SELECT end_reason, ended_at, last_activity_at, started_at
            FROM sessions
            WHERE {session_period_sql} AND coalesce(hidden,0)=0
            """,
            tuple(session_period_params),
        ).fetchall():
            outcome_counts[_session_outcome(_row_dict(row))["outcome"]] += 1
        return {
            "period_days": days,
            "period": _period_payload(days, period_start, period_end),
            "totals": totals,
            "daily": daily,
            "models": models,
            "sources": sources,
            "outcomes": [
                {"outcome": name, "sessions": count}
                for name, count in sorted(outcome_counts.items(), key=lambda item: item[1], reverse=True)
            ],
            "generated_at": time.time(),
        }


@router.get("/overview")
async def overview(
    days: int = Query(30, ge=0, le=3650),
    start_at: Optional[float] = Query(None, ge=0),
    end_at: Optional[float] = Query(None, ge=0),
) -> Dict[str, Any]:
    return await asyncio.to_thread(_overview_sync, days, start_at, end_at)


_MODEL_ROUTE_META: Dict[str, Dict[str, Any]] = {
    "openai-codex": {"label": "OpenAI OAuth", "provider": "OpenAI", "oauth": True, "quota_provider": "codex"},
    "xai-oauth": {"label": "SuperGrok OAuth", "provider": "xAI", "oauth": True, "quota_provider": "grok"},
    "anthropic-oauth": {"label": "Anthropic OAuth", "provider": "Anthropic", "oauth": True, "quota_provider": "anthropic"},
    "openrouter": {"label": "OpenRouter API", "provider": "OpenRouter"},
    "nous": {"label": "Nous Portal", "provider": "Nous Research", "quota_provider": "nous"},
    "deepseek": {"label": "DeepSeek API", "provider": "DeepSeek"},
    "nvidia": {"label": "NVIDIA NIM API", "provider": "NVIDIA"},
    "opencode": {"label": "OpenCode Zen API", "provider": "OpenCode"},
    "kimi-coding": {"label": "Kimi Code Plan API", "provider": "Kimi", "quota_provider": "kimi"},
    "kimi-coding-cn": {"label": "Kimi Code Plan API", "provider": "Kimi", "quota_provider": "kimi"},
    "zai": {"label": "Z.AI API", "provider": "Z.AI", "quota_provider": "zai"},
    "zai-coding": {"label": "Z.AI Coding Plan API", "provider": "Z.AI", "quota_provider": "zai"},
    "alibaba": {"label": "Qwen Cloud API", "provider": "Qwen"},
    "alibaba-coding-plan": {"label": "Qwen Coding Plan API", "provider": "Qwen"},
    "qwen-oauth": {"label": "Qwen OAuth", "provider": "Qwen", "oauth": True},
}

_MODEL_ORIGIN_NAMES = {
    "anthropic": "Anthropic",
    "deepseek": "DeepSeek",
    "google": "Google",
    "meta-llama": "Meta",
    "mistralai": "Mistral AI",
    "moonshotai": "Moonshot AI",
    "nvidia": "NVIDIA",
    "openai": "OpenAI",
    "qwen": "Qwen",
    "x-ai": "xAI",
    "z-ai": "Z.AI",
}


def _plugin_settings() -> Dict[str, Any]:
    try:
        from hermes_cli.config import load_config_readonly

        config = load_config_readonly() or {}
    except (ImportError, OSError, ValueError):
        return {}
    plugins = config.get("plugins") if isinstance(config, Mapping) else None
    entries = plugins.get("entries") if isinstance(plugins, Mapping) else None
    entry = entries.get("session-lens") if isinstance(entries, Mapping) else None
    if not isinstance(entry, Mapping):
        return {}
    settings = entry.get("settings")
    if isinstance(settings, Mapping):
        return dict(settings)
    legacy = entry.get("config")
    return dict(legacy) if isinstance(legacy, Mapping) else {}


def _rate_sample_threshold(settings: Optional[Mapping[str, Any]] = None) -> int:
    value = (settings if settings is not None else _plugin_settings()).get(
        "rate_sample_threshold", DEFAULT_RATE_SAMPLE_THRESHOLD
    )
    return max(1, min(10000, _integer(value, DEFAULT_RATE_SAMPLE_THRESHOLD)))


def _configured_route_mappings(settings: Optional[Mapping[str, Any]] = None) -> Dict[str, str]:
    raw = (settings if settings is not None else _plugin_settings()).get("model_route_mappings", {})
    if not isinstance(raw, Mapping):
        return {}
    mappings: Dict[str, str] = {}
    for pattern, label in list(raw.items())[:MAX_ROUTE_MAPPINGS]:
        clean_pattern = _clean_text(pattern, 240).strip()
        clean_label = _clean_text(label, 120).strip()
        if clean_pattern and clean_label:
            mappings[clean_pattern] = clean_label
    return mappings


def _table_columns(connection: Any, table: str) -> set[str]:
    try:
        return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}
    except sqlite3.OperationalError:
        return set()


def _humanize_identifier(value: Any) -> str:
    text = _clean_text(value, 160).strip(" /_-.")
    if not text:
        return "Unknown"
    words = re.split(r"[-_\s]+", text)
    acronyms = {
        "ai": "AI",
        "api": "API",
        "codex": "Codex",
        "glm": "GLM",
        "gpt": "GPT",
        "nim": "NIM",
        "oauth": "OAuth",
        "xai": "xAI",
    }
    return " ".join(acronyms.get(word.lower(), word if any(char.isupper() for char in word) else word.title()) for word in words)


def _model_display_name(model_id: str) -> str:
    leaf = str(model_id or "unknown").rsplit("/", 1)[-1]
    return _humanize_identifier(leaf)


def _model_origin_label(model_id: str, billing_provider: str) -> str:
    model = str(model_id or "").lower()
    if "/" in model:
        namespace = model.split("/", 1)[0]
        return _MODEL_ORIGIN_NAMES.get(namespace, _humanize_identifier(namespace))
    family_names = (
        (("claude",), "Anthropic"),
        (("gpt", "o1", "o3", "o4", "codex"), "OpenAI"),
        (("grok",), "xAI"),
        (("kimi", "k2", "k3"), "Kimi"),
        (("glm",), "Z.AI"),
        (("deepseek",), "DeepSeek"),
        (("qwen",), "Qwen"),
        (("gemini",), "Google"),
    )
    for prefixes, label in family_names:
        if model.startswith(prefixes):
            return label
    provider_key = str(billing_provider or "").strip().lower()
    meta = _MODEL_ROUTE_META.get(provider_key)
    return str(meta.get("provider")) if meta else _humanize_identifier(provider_key or "unknown")


def _route_descriptor(provider: Any, base_url: Any, billing_mode: Any) -> Dict[str, Any]:
    provider_key = str(provider or "").strip().lower() or "unknown"
    mode = str(billing_mode or "").strip().lower()
    meta = dict(_MODEL_ROUTE_META.get(provider_key) or {})
    oauth = bool(meta.get("oauth")) or "oauth" in provider_key or "oauth" in mode
    mode_subscription = any(marker in mode for marker in ("subscription", "included"))
    if provider_key == "anthropic" and mode_subscription:
        meta = {"label": "Anthropic OAuth", "provider": "Anthropic", "quota_provider": "anthropic"}
        oauth = True
    subscription = oauth and provider_key not in {"nous"}
    provider_label = str(meta.get("provider") or _humanize_identifier(provider_key))
    route_label = str(meta.get("label") or f"{provider_label} {'OAuth' if oauth else 'API'}")
    parsed = urlparse(str(base_url or ""))
    return {
        "key": "\u001f".join((provider_key, parsed.hostname or "", mode)),
        "provider": provider_key,
        "provider_label": provider_label,
        "label": route_label,
        "host": (parsed.hostname or "")[:160] or None,
        "billing_mode": mode or None,
        "oauth": oauth,
        "subscription": subscription,
        "quota_provider": meta.get("quota_provider"),
    }


def _route_needs_mapping(route: Mapping[str, Any]) -> bool:
    return str(route.get("provider") or "").lower() in {"", "unknown"} or str(
        route.get("label") or ""
    ).lower() in {"unknown", "unknown api", "unknown route"}


def _model_family_glob(model_id: str) -> Optional[str]:
    match = re.match(r"^(?P<prefix>.*?-\d+(?:\.\d+)?)(?:-|$)", str(model_id or ""), re.IGNORECASE)
    return f"{match.group('prefix')}-*" if match else None


def _historical_route_mappings(models: Mapping[str, Mapping[str, Any]]) -> Dict[str, str]:
    exact: Dict[str, str] = {}
    family_labels: Dict[str, set[str]] = defaultdict(set)
    for model_id, model in models.items():
        labels = {
            str(route.get("label") or "").strip()
            for route in (model.get("routes_map") or {}).values()
            if not _route_needs_mapping(route) and str(route.get("label") or "").strip()
        }
        if len(labels) != 1:
            continue
        label = next(iter(labels))
        exact[model_id] = label
        family = _model_family_glob(model_id)
        if family:
            family_labels[family].add(label)
    for family, labels in family_labels.items():
        if len(labels) == 1:
            exact.setdefault(family, next(iter(labels)))
    return exact


def _apply_route_mapping(
    model_id: str,
    route: Mapping[str, Any],
    configured: Mapping[str, str],
    historical: Mapping[str, str],
) -> Dict[str, Any]:
    resolved = dict(route)
    if not _route_needs_mapping(resolved):
        resolved["mapping_source"] = "recorded"
        return resolved
    model_key = str(model_id or "").lower()
    for source, mappings in (("config", configured), ("historical", historical)):
        for pattern, label in mappings.items():
            if fnmatch.fnmatchcase(model_key, str(pattern).lower()):
                resolved["label"] = label
                resolved["mapping_pattern"] = pattern
                resolved["mapping_source"] = source
                for provider_key, meta in _MODEL_ROUTE_META.items():
                    if str(meta.get("label") or "").casefold() != str(label).casefold():
                        continue
                    semantics = _route_descriptor(provider_key, "", "")
                    for field in ("provider", "provider_label", "oauth", "subscription", "quota_provider"):
                        resolved[field] = semantics.get(field)
                    break
                return resolved
    resolved["label"] = "Unmapped (edit in config)"
    resolved["mapping_source"] = "unmapped"
    return resolved


def _auxiliary_task_label(task: str) -> str:
    labels = {
        "approval": "Approval",
        "background_review": "Review",
        "compression": "Compression",
        "title_generation": "Title",
        "vision": "Vision",
        "web_extract": "Web extract",
    }
    normalised = str(task or "").strip().lower()
    label = labels.get(normalised, _humanize_identifier(normalised))
    return f"{label} job" if label in _SESSION_TASK_TYPE_SET else label

def _ai_models_payload_sync(
    days: int,
    start_at: Optional[float] = None,
    end_at: Optional[float] = None,
    *,
    settings: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    settings = dict(settings or {})
    rate_sample_threshold = _rate_sample_threshold(settings)
    configured_route_mappings = _configured_route_mappings(settings)
    period_start, period_end = _period_bounds(days, start_at, end_at)
    period_sql, period_params = _period_sql("s.started_at", period_start, period_end)
    now = time.time()
    trend_end = max(period_start, (period_end if period_end is not None else now) - 0.001)
    trend_end_day = dt.datetime.fromtimestamp(trend_end).date()
    trend_days = [(trend_end_day - dt.timedelta(days=offset)).isoformat() for offset in range(6, -1, -1)]

    with _database() as db:
        connection = _db_connection(db)
        session_columns = _table_columns(connection, "sessions")
        usage_columns = _table_columns(connection, "session_model_usage")
        rewind_expr = "coalesce(s.rewind_count,0)" if "rewind_count" in session_columns else "0"
        session_rows = [
            _row_dict(row)
            for row in connection.execute(
                f"""
                SELECT s.id, s.source, s.model, s.started_at, s.ended_at, s.end_reason,
                       s.last_activity_at, s.input_tokens, s.output_tokens,
                       s.cache_read_tokens, s.cache_write_tokens, s.reasoning_tokens,
                       s.api_call_count, s.billing_provider, s.billing_base_url,
                       s.billing_mode, s.estimated_cost_usd, s.actual_cost_usd,
                       s.cost_status, s.cost_source, s.message_count,
                       {rewind_expr} AS rewind_count
                FROM sessions s
                WHERE {period_sql} AND coalesce(s.hidden,0)=0
                """,
                tuple(period_params),
            ).fetchall()
        ]
        sessions_by_id = {str(row["id"]): row for row in session_rows}

        usage_rows: List[Dict[str, Any]] = []
        inventory_rows: List[Dict[str, Any]] = []
        if usage_columns:
            base_url_expr = "u.billing_base_url" if "billing_base_url" in usage_columns else "''"
            task_expr = "u.task" if "task" in usage_columns else "''"
            usage_rows = [
                _row_dict(row)
                for row in connection.execute(
                    f"""
                    SELECT u.session_id, u.model, u.billing_provider,
                           {base_url_expr} AS billing_base_url, u.billing_mode,
                           {task_expr} AS task, u.api_call_count, u.input_tokens,
                           u.output_tokens, u.cache_read_tokens, u.cache_write_tokens,
                           u.reasoning_tokens, u.estimated_cost_usd, u.actual_cost_usd,
                           u.cost_status, u.cost_source, u.first_seen, u.last_seen
                    FROM session_model_usage u
                    JOIN sessions s ON s.id=u.session_id
                    WHERE {period_sql} AND coalesce(s.hidden,0)=0
                    """,
                    tuple(period_params),
                ).fetchall()
            ]
            inventory_rows = [
                _row_dict(row)
                for row in connection.execute(
                    f"""
                    SELECT u.model, u.billing_provider,
                           {base_url_expr} AS billing_base_url, u.billing_mode,
                           MAX(coalesce(u.last_seen, s.last_activity_at, s.started_at)) AS last_seen
                    FROM session_model_usage u
                    JOIN sessions s ON s.id=u.session_id
                    WHERE coalesce(s.hidden,0)=0
                    GROUP BY u.model, u.billing_provider, {base_url_expr}, u.billing_mode
                    """
                ).fetchall()
            ]

        inventoried_models = {str(row.get("model") or "unknown") for row in inventory_rows}
        session_inventory_rows = [
            _row_dict(row)
            for row in connection.execute(
                """
                SELECT model, billing_provider, billing_base_url, billing_mode,
                       MAX(coalesce(last_activity_at, started_at)) AS last_seen
                FROM sessions
                WHERE coalesce(hidden,0)=0 AND model IS NOT NULL AND trim(model) != ''
                GROUP BY model, billing_provider, billing_base_url, billing_mode
                """
            ).fetchall()
        ]
        for session in session_inventory_rows:
            model_id = str(session.get("model") or "").strip()
            if not model_id or model_id in inventoried_models:
                continue
            inventory_rows.append(
                {
                    "model": model_id,
                    "billing_provider": session.get("billing_provider") or "",
                    "billing_base_url": session.get("billing_base_url") or "",
                    "billing_mode": session.get("billing_mode") or "",
                    "last_seen": session.get("last_seen"),
                }
            )
            inventoried_models.add(model_id)

        accounted_sessions = {str(row.get("session_id")) for row in usage_rows}
        for session in session_rows:
            session_id = str(session.get("id"))
            if session_id in accounted_sessions or not session.get("model"):
                continue
            usage_rows.append(
                {
                    "session_id": session_id,
                    "model": session.get("model") or "unknown",
                    "billing_provider": session.get("billing_provider") or "",
                    "billing_base_url": session.get("billing_base_url") or "",
                    "billing_mode": session.get("billing_mode") or "",
                    "task": "",
                    "api_call_count": session.get("api_call_count") or 0,
                    "input_tokens": session.get("input_tokens") or 0,
                    "output_tokens": session.get("output_tokens") or 0,
                    "cache_read_tokens": session.get("cache_read_tokens") or 0,
                    "cache_write_tokens": session.get("cache_write_tokens") or 0,
                    "reasoning_tokens": session.get("reasoning_tokens") or 0,
                    "estimated_cost_usd": session.get("estimated_cost_usd") or 0,
                    "actual_cost_usd": session.get("actual_cost_usd") or 0,
                    "cost_status": session.get("cost_status"),
                    "cost_source": session.get("cost_source"),
                    "first_seen": session.get("started_at"),
                    "last_seen": session.get("last_activity_at") or session.get("started_at"),
                    "fallback": True,
                }
            )

        confirmed_failure_rows = (
            _confirmed_failure_rows(
                connection,
                period_sql + " AND coalesce(s.hidden,0)=0",
                period_params,
            )
            if session_rows
            else []
        )
        failure_counts: Counter[str] = Counter(
            str(row.get("session_id") or "") for row in confirmed_failure_rows
        )

        message_rows_by_session: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        tool_failure_rows = [
            row
            for row in confirmed_failure_rows
            if str(row.get("role") or "").lower() == "tool"
        ]
        tool_call_rows = (
            [
                _row_dict(row)
                for row in connection.execute(
                    f"""
                    SELECT m.session_id, m.timestamp
                    FROM messages m
                    JOIN sessions s ON s.id=m.session_id
                    WHERE {period_sql} AND coalesce(s.hidden,0)=0
                      AND coalesce(m.active,1)=1 AND m.role='tool'
                    """,
                    tuple(period_params),
                ).fetchall()
            ]
            if session_rows
            else []
        )
        cached_facts_by_session: Dict[str, Dict[str, Any]] = {}
        classification_session_ids: List[str] = []
        for session_id, session in sessions_by_id.items():
            cached = _cached_classification_facts(session)
            if cached is None:
                classification_session_ids.append(session_id)
            else:
                cached_facts_by_session[session_id] = cached
        for chunk_start in range(0, len(classification_session_ids), 900):
            chunk = classification_session_ids[chunk_start : chunk_start + 900]
            placeholders = ",".join("?" for _ in chunk)
            for row in connection.execute(
                f"""
                SELECT m.id, m.session_id, m.role, m.content, m.tool_call_id,
                       m.tool_calls, m.tool_name, m.effect_disposition,
                       m.timestamp, m.finish_reason
                FROM messages m
                WHERE m.session_id IN ({placeholders})
                  AND coalesce(m.active,1)=1
                  AND (m.role IN ('user','tool') OR m.tool_calls IS NOT NULL)
                ORDER BY m.timestamp ASC, m.id ASC
                """,
                tuple(chunk),
            ).fetchall():
                material = _row_dict(row)
                message_rows_by_session[str(material.get("session_id") or "")].append(material)

    role_models_by_session: Dict[str, Dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    usage_by_session: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    cache_reporting_providers: set[str] = set()
    for usage in usage_rows:
        session_id = str(usage.get("session_id") or "")
        usage_by_session[session_id].append(usage)
        role_models_by_session[session_id][_task_role(usage.get("task"))].add(
            str(usage.get("model") or "unknown")
        )
        if _integer(usage.get("cache_read_tokens")) or _integer(usage.get("cache_write_tokens")):
            cache_reporting_providers.add(str(usage.get("billing_provider") or "").strip().lower())

    retry_switch_models_by_session: Dict[str, set[str]] = defaultdict(set)
    for session_id in classification_session_ids:
        cached_facts_by_session[session_id] = _classification_facts(
            sessions_by_id.get(session_id, {}),
            message_rows_by_session.get(session_id, []),
        )
    for session_id, roles in role_models_by_session.items():
        for role, role_models in roles.items():
            if len(role_models) > 1:
                retry_switch_models_by_session[session_id].update(role_models)
        main_models = roles.get("main", set())
        session = sessions_by_id.get(session_id, {})
        if _integer(session.get("rewind_count")) > 0:
            retry_switch_models_by_session[session_id].update(main_models)
        elif len(main_models) == 1 and cached_facts_by_session.get(session_id, {}).get(
            "near_identical_prompt_retry"
        ):
            retry_switch_models_by_session[session_id].update(main_models)

    session_facts: Dict[str, Dict[str, Any]] = {}
    for session_id, session in sessions_by_id.items():
        cached_facts = cached_facts_by_session.get(session_id, {})
        outcome = str(cached_facts.get("outcome") or "unknown")
        closed = session.get("ended_at") is not None and outcome not in {"open", "running"}
        resolved = closed and outcome not in {"failed", "cancelled"}
        eligible_proxy = resolved
        proxy_accepted = (
            eligible_proxy
            and not retry_switch_models_by_session.get(session_id)
            and failure_counts.get(session_id, 0) == 0
        )
        session_facts[session_id] = {
            "outcome": outcome,
            "closed": closed,
            "resolved": resolved,
            "eligible_proxy": eligible_proxy,
            "proxy_accepted": proxy_accepted,
            "coding_change": cached_facts.get("coding_change"),
            "writing_change": cached_facts.get("writing_change"),
            "task_type": cached_facts.get("task_type", "General"),
        }

    tool_failures_by_model: Counter[str] = Counter()
    unattributed_tool_failures = 0
    for failure in tool_failure_rows:
        session_id = str(failure.get("session_id") or "")
        model_id = _model_for_session_event(
            usage_by_session.get(session_id, []),
            _number(failure.get("timestamp")),
        )
        if model_id:
            tool_failures_by_model[model_id] += 1
        else:
            unattributed_tool_failures += 1

    tool_calls_by_model: Counter[str] = Counter()
    unattributed_tool_calls = 0
    for call in tool_call_rows:
        session_id = str(call.get("session_id") or "")
        model_id = _model_for_session_event(
            usage_by_session.get(session_id, []),
            _number(call.get("timestamp")),
        )
        if model_id:
            tool_calls_by_model[model_id] += 1
        else:
            unattributed_tool_calls += 1

    models: Dict[str, Dict[str, Any]] = {}
    for usage in usage_rows:
        model_id = str(usage.get("model") or "unknown")
        session_id = str(usage.get("session_id") or "")
        session = sessions_by_id.get(session_id, {})
        provider_key = str(usage.get("billing_provider") or "").strip().lower()
        route = _route_descriptor(provider_key, usage.get("billing_base_url"), usage.get("billing_mode"))
        task_name = str(usage.get("task") or "").strip()
        auxiliary = bool(task_name)
        task_type = _auxiliary_task_label(task_name) if auxiliary else session_facts.get(session_id, {}).get("task_type", "General")
        input_tokens = _integer(usage.get("input_tokens"))
        output_tokens = _integer(usage.get("output_tokens"))
        cache_tokens = _integer(usage.get("cache_read_tokens")) + _integer(usage.get("cache_write_tokens"))
        requests = _integer(usage.get("api_call_count"))
        cost_view = _cost_view(usage)
        row_cost = max(0.0, _number(cost_view.get("display_cost_usd")))
        row_cost_kind = str(cost_view.get("cost_kind") or "unpriced")
        last_used = _number(usage.get("last_seen") or session.get("last_activity_at") or session.get("started_at"), 0)

        model = models.setdefault(
            model_id,
            {
                "model_id": model_id,
                "display_name": _model_display_name(model_id),
                "provider_label": "",
                "last_used_at": None,
                "requests": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_tokens_raw": 0,
                "reasoning_tokens": 0,
                "total_tokens": 0,
                "cost_usd": 0.0,
                "actual_cost": False,
                "estimated_cost": False,
                "included_cost": False,
                "free_api_cost": False,
                "sessions_set": set(),
                "acceptance_sessions_set": set(),
                "accepted_sessions_set": set(),
                "retry_sessions_set": set(),
                "routes_map": {},
                "task_types_map": {},
                "auxiliary_tasks_map": {},
                "trend_map": {day: 0 for day in trend_days},
            },
        )
        model["last_used_at"] = max(_number(model.get("last_used_at"), 0), last_used) or None
        model["requests"] += requests
        model["input_tokens"] += input_tokens
        model["output_tokens"] += output_tokens
        model["cache_tokens_raw"] += cache_tokens
        model["reasoning_tokens"] += _integer(usage.get("reasoning_tokens"))
        model["total_tokens"] += input_tokens + output_tokens + cache_tokens
        model["cost_usd"] += row_cost
        model["actual_cost"] = bool(model["actual_cost"] or row_cost_kind == "actual")
        model["estimated_cost"] = bool(model["estimated_cost"] or row_cost_kind == "estimated")
        model["included_cost"] = bool(model["included_cost"] or route["subscription"])
        model["free_api_cost"] = bool(
            model["free_api_cost"]
            or (not route["subscription"] and row_cost_kind == "included")
        )
        model["sessions_set"].add(session_id)
        facts = session_facts.get(session_id, {})
        acceptance_valid, accepted = _acceptance_for_task(task_type, facts)
        if acceptance_valid:
            model["acceptance_sessions_set"].add(session_id)
        if accepted:
            model["accepted_sessions_set"].add(session_id)
        if model_id in retry_switch_models_by_session.get(session_id, set()):
            model["retry_sessions_set"].add(session_id)

        route_row = model["routes_map"].setdefault(
            route["key"],
            {**route, "requests": 0, "input_tokens": 0, "output_tokens": 0, "cache_tokens": 0, "cost_usd": 0.0, "last_used_at": None},
        )
        route_row["requests"] += requests
        route_row["input_tokens"] += input_tokens
        route_row["output_tokens"] += output_tokens
        route_row["cache_tokens"] += cache_tokens
        route_row["cost_usd"] += row_cost
        route_row["last_used_at"] = max(_number(route_row.get("last_used_at"), 0), last_used) or None

        task_map = model["auxiliary_tasks_map"] if auxiliary else model["task_types_map"]
        task = task_map.setdefault(
            task_type,
            {
                "task_type": task_type,
                "requests": 0,
                "sessions_set": set(),
                "eligible_sessions_set": set(),
                "accepted_sessions_set": set(),
            },
        )
        task["requests"] += requests
        task["sessions_set"].add(session_id)
        if acceptance_valid:
            task["eligible_sessions_set"].add(session_id)
        if accepted:
            task["accepted_sessions_set"].add(session_id)

        if last_used:
            day = dt.datetime.fromtimestamp(last_used).date().isoformat()
            if day in model["trend_map"]:
                model["trend_map"][day] += requests

    for inventory in inventory_rows:
        model_id = str(inventory.get("model") or "unknown")
        route = _route_descriptor(
            inventory.get("billing_provider"),
            inventory.get("billing_base_url"),
            inventory.get("billing_mode"),
        )
        last_used = _number(inventory.get("last_seen"), 0)
        model = models.setdefault(
            model_id,
            {
                "model_id": model_id,
                "display_name": _model_display_name(model_id),
                "provider_label": "",
                "last_used_at": None,
                "requests": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_tokens_raw": 0,
                "reasoning_tokens": 0,
                "total_tokens": 0,
                "cost_usd": 0.0,
                "actual_cost": False,
                "estimated_cost": False,
                "included_cost": False,
                "free_api_cost": False,
                "sessions_set": set(),
                "acceptance_sessions_set": set(),
                "accepted_sessions_set": set(),
                "retry_sessions_set": set(),
                "routes_map": {},
                "task_types_map": {},
                "auxiliary_tasks_map": {},
                "trend_map": {day: 0 for day in trend_days},
            },
        )
        model["last_used_at"] = max(_number(model.get("last_used_at"), 0), last_used) or None
        route_row = model["routes_map"].setdefault(
            route["key"],
            {**route, "requests": 0, "input_tokens": 0, "output_tokens": 0, "cache_tokens": 0, "cost_usd": 0.0, "last_used_at": None},
        )
        route_row["last_used_at"] = max(_number(route_row.get("last_used_at"), 0), last_used) or None

    historical_route_mappings = _historical_route_mappings(models)
    runtime = _runtime_events()
    known_models = set(models)
    latency_by_model: Dict[str, List[float]] = defaultdict(list)
    successes_by_model: Counter[str] = Counter()
    failures_by_model: Dict[str, Counter[str]] = defaultdict(Counter)
    reliability_events_by_session_model: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
    log_timestamps = [
        _number(timestamp)
        for timestamp in runtime.get("timestamps", [])
        if _number(timestamp) >= period_start
        and (period_end is None or _number(timestamp) < period_end)
    ]
    for event in runtime.get("api", []):
        timestamp = _number(event.get("timestamp"), 0)
        if timestamp < period_start or (period_end is not None and timestamp >= period_end):
            continue
        model_id = _model_match(event.get("model"), known_models)
        if not model_id:
            continue
        successes_by_model[model_id] += 1
        latency_by_model[model_id].append(_number(event.get("latency_seconds")))
        session_id = str(event.get("session_id") or "")
        if session_id:
            reliability_events_by_session_model[(session_id, model_id)].append(
                {"timestamp": timestamp, "status": "success"}
            )

    unattributed_failures = 0
    for event in runtime.get("errors", []):
        timestamp = _number(event.get("timestamp"), 0)
        if timestamp < period_start or (period_end is not None and timestamp >= period_end):
            continue
        session_id = str(event.get("session_id") or "")
        candidates = [row for row in usage_by_session.get(session_id, []) if not str(row.get("task") or "").strip()]
        model_id: Optional[str] = _model_match(event.get("model"), known_models)
        if len({str(row.get("model") or "unknown") for row in candidates}) == 1 and candidates:
            model_id = model_id or str(candidates[0].get("model") or "unknown")
        elif candidates and not model_id:
            model_id = _model_for_session_event(candidates, timestamp)
        if model_id not in known_models:
            unattributed_failures += 1
            continue
        failures_by_model[model_id][str(event.get("category") or "error")] += 1
        if session_id:
            reliability_events_by_session_model[(session_id, model_id)].append(
                {
                    "timestamp": timestamp,
                    "status": "failure",
                    "category": str(event.get("category") or "error"),
                }
            )

    work_runs_by_model: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for session_id, session in sessions_by_id.items():
        main_usage = [
            row
            for row in usage_by_session.get(session_id, [])
            if _task_role(row.get("task")) == "main"
        ]
        if not main_usage:
            continue

        usage_by_model: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for usage in main_usage:
            usage_by_model[str(usage.get("model") or "unknown")].append(usage)

        last_seen_by_model = {
            model_id: max(
                _number(row.get("last_seen") or row.get("first_seen"), 0)
                for row in rows
            )
            for model_id, rows in usage_by_model.items()
        }
        latest_seen = max(last_seen_by_model.values(), default=0)
        final_model_candidates = {
            model_id
            for model_id, last_seen in last_seen_by_model.items()
            if last_seen == latest_seen
        }
        recorded_session_model = str(session.get("model") or "").strip()
        if len(final_model_candidates) > 1 and recorded_session_model in final_model_candidates:
            final_model_candidates = {recorded_session_model}
        final_model = next(iter(final_model_candidates)) if len(final_model_candidates) == 1 else None
        facts = session_facts.get(session_id, {})
        task_type = str(facts.get("task_type") or "General")
        outcome = str(facts.get("outcome") or "open")

        for model_id, model_usage_rows in usage_by_model.items():
            latest_usage = max(
                model_usage_rows,
                key=lambda row: (
                    _number(row.get("last_seen") or row.get("first_seen"), 0),
                    _number(row.get("first_seen"), 0),
                ),
            )
            route = _route_descriptor(
                latest_usage.get("billing_provider"),
                latest_usage.get("billing_base_url"),
                latest_usage.get("billing_mode"),
            )
            first_seen = min(
                (_number(row.get("first_seen"), 0) for row in model_usage_rows),
                default=0,
            )
            last_seen = max(
                (
                    _number(row.get("last_seen") or row.get("first_seen"), 0)
                    for row in model_usage_rows
                ),
                default=first_seen,
            )
            events = sorted(
                (
                    event
                    for event in reliability_events_by_session_model.get((session_id, model_id), [])
                    if (not first_seen or _number(event.get("timestamp")) >= first_seen - 300)
                    and (not last_seen or _number(event.get("timestamp")) <= last_seen + 300)
                ),
                key=lambda event: _number(event.get("timestamp")),
            )
            if task_type == "Orchestration":
                status = "excluded"
                reason = "orchestration ownership is not scored"
            elif final_model is None:
                status = "unknown"
                reason = "final main-role model is ambiguous"
            elif model_id != final_model:
                status = "switched_away"
                reason = "same task role finished on another model"
            elif outcome not in {"completed", "failed"}:
                status = "excluded"
                reason = f"session outcome {outcome} is not eligible"
            elif not events:
                status = "unknown"
                reason = "no attributable API event is available in bounded logs"
            elif outcome == "completed":
                if any(event.get("status") == "failure" for event in events):
                    status = "recovered"
                    reason = "task completed after an observed API failure on the same final model-role"
                elif model_id in retry_switch_models_by_session.get(session_id, set()):
                    status = "completed"
                    reason = "task completed with a rewind, resend, or same-role switch"
                else:
                    status = "clean"
                    reason = "task completed without an observed API failure, retry, or same-role switch"
            else:
                last_failure = max(
                    (_number(event.get("timestamp")) for event in events if event.get("status") == "failure"),
                    default=0,
                )
                last_success = max(
                    (_number(event.get("timestamp")) for event in events if event.get("status") == "success"),
                    default=0,
                )
                if last_failure and last_failure >= last_success:
                    status = "unrecovered"
                    reason = "session failed with no later successful API event on the final model-role"
                else:
                    status = "unknown"
                    reason = "failed session is not attributable to an unrecovered model/API event"

            work_runs_by_model[model_id].append(
                {
                    "session_id": session_id,
                    "task_type": task_type,
                    "route_key": route["key"],
                    "status": status,
                    "reason": reason,
                }
            )

    final_models: List[Dict[str, Any]] = []
    for model_id, model in models.items():
        routes = sorted(
            (
                _apply_route_mapping(
                    model_id,
                    route,
                    configured_route_mappings,
                    historical_route_mappings,
                )
                for route in model.pop("routes_map").values()
            ),
            key=lambda item: _number(item.get("last_used_at")),
            reverse=True,
        )
        primary_route = routes[0] if routes else _route_descriptor("unknown", "", "")
        route_labels = {
            str(route.get("key") or ""): str(route.get("label") or "Unmapped (edit in config)")
            for route in routes
        }
        work_reliability = _work_reliability_payload(
            work_runs_by_model.get(model_id, []),
            route_labels,
            rate_sample_threshold,
        )
        route_providers = {str(route.get("provider") or "") for route in routes}
        reporting_routes = route_providers & cache_reporting_providers
        if model["cache_tokens_raw"] > 0 or reporting_routes:
            cache_tokens: Optional[int] = _integer(model["cache_tokens_raw"])
            cache_coverage = "recorded" if reporting_routes == route_providers else "partial"
        else:
            cache_tokens = None
            cache_coverage = "unavailable"

        included = bool(model.pop("included_cost")) or any(
            bool(route.get("subscription")) for route in routes
        )
        free_api = bool(model.pop("free_api_cost"))
        actual = bool(model.pop("actual_cost"))
        estimated = bool(model.pop("estimated_cost"))
        has_metered_cost = model["cost_usd"] > 0
        if included and has_metered_cost:
            cost_kind = "mixed"
        elif included:
            cost_kind = "subscription"
        elif free_api and not has_metered_cost:
            cost_kind = "free"
        elif actual:
            cost_kind = "actual"
        elif estimated:
            cost_kind = "estimated"
        else:
            cost_kind = "unpriced"

        sessions_set = model.pop("sessions_set")
        acceptance_sessions = model.pop("acceptance_sessions_set")
        accepted_sessions = model.pop("accepted_sessions_set")
        retry_sessions = model.pop("retry_sessions_set")
        retry_switch_rate = len(retry_sessions) / len(sessions_set) if sessions_set else None
        task_types = []
        for task in model.pop("task_types_map").values():
            eligible_sessions = task.pop("eligible_sessions_set")
            accepted_task_sessions = task.pop("accepted_sessions_set")
            task["sessions"] = len(task.pop("sessions_set"))
            task["eligible_sessions"] = len(eligible_sessions)
            task["accepted_sessions"] = len(accepted_task_sessions)
            task["first_attempt_acceptance_rate"] = (
                len(accepted_task_sessions) / len(eligible_sessions) if eligible_sessions else None
            )
            if task["task_type"] == "Coding":
                task["acceptance_basis"] = "resolved session with a recorded successful code artifact save or commit"
            elif task["task_type"] == "Writing":
                task["acceptance_basis"] = "resolved session with a recorded successful non-code artifact write"
            elif task["task_type"] in {"General", "Analysis"}:
                task["acceptance_basis"] = "eligible closed session without a retry, same-role switch, or detected recorded failure"
            else:
                task["acceptance_basis"] = "unavailable for this task type"
            task_types.append(task)
        task_types.sort(key=lambda item: (item["requests"], item["sessions"]), reverse=True)

        auxiliary_tasks = []
        for task in model.pop("auxiliary_tasks_map").values():
            task.pop("eligible_sessions_set")
            task.pop("accepted_sessions_set")
            task["sessions"] = len(task.pop("sessions_set"))
            task["eligible_sessions"] = 0
            task["accepted_sessions"] = 0
            task["first_attempt_acceptance_rate"] = None
            task["acceptance_basis"] = "auxiliary job; acceptance is not scored"
            auxiliary_tasks.append(task)
        auxiliary_tasks.sort(key=lambda item: (item["requests"], item["sessions"]), reverse=True)

        failure_counts_by_type = failures_by_model.get(model_id, Counter())
        observed_failures = sum(failure_counts_by_type.values())
        observed_successes = successes_by_model.get(model_id, 0)
        failure_samples = observed_successes + observed_failures
        failure_rate = observed_failures / failure_samples if failure_samples else None
        latencies = latency_by_model.get(model_id, [])
        latency_p50 = _percentile(latencies, 0.50)
        retry_switch_samples = len(sessions_set)
        has_in_period_requests = _integer(model.get("requests")) > 0
        if (
            work_reliability["rank_eligible"]
            and work_reliability["unrecovered_failure_rate"] is not None
            and work_reliability["unrecovered_failure_rate"] > 0.05
        ):
            insight = (
                f"{work_reliability['unrecovered_failure_rate'] * 100:.1f}% of eligible main-role tasks ended with "
                "an unrecovered model/API failure."
            )
        elif (
            has_in_period_requests
            and failure_samples >= rate_sample_threshold
            and failure_rate is not None
            and failure_rate > 0.05
        ):
            insight = f"Observed API/request failures reached {failure_rate * 100:.1f}% in the bounded log window."
        elif (
            retry_switch_samples >= rate_sample_threshold
            and retry_switch_rate is not None
            and retry_switch_rate > 0.05
        ):
            insight = (
                f"{retry_switch_rate * 100:.1f}% of recorded sessions used a rewind, resent a near-identical prompt "
                "within five minutes, or switched models within the same task role."
            )
        elif has_in_period_requests and latency_p50 is not None and latency_p50 > 10:
            insight = f"Median recorded response latency is {latency_p50:.1f}s."
        else:
            insight = None

        model.update(
            {
                "provider_label": _model_origin_label(model_id, primary_route.get("provider")),
                "route_label": primary_route.get("label"),
                "route_mapping_source": primary_route.get("mapping_source"),
                "route_mapping_pattern": primary_route.get("mapping_pattern"),
                "routes": routes,
                "route_count": len(routes),
                "cache_tokens": cache_tokens,
                "cache_coverage": cache_coverage,
                "cost_kind": cost_kind,
                "sessions": len(sessions_set),
                "acceptance_samples": len(acceptance_sessions),
                "accepted_tasks": len(accepted_sessions),
                "retry_switch_sessions": len(retry_sessions),
                "retry_switch_samples": retry_switch_samples,
                "retry_switch_rate": retry_switch_rate,
                "work_reliability": work_reliability,
                "task_types": task_types,
                "auxiliary_tasks": auxiliary_tasks,
                "failures": {
                    "rate": failure_rate,
                    "rate_limits": failure_counts_by_type.get("rate_limit", 0),
                    "timeouts": failure_counts_by_type.get("timeout", 0),
                    "errors": failure_counts_by_type.get("error", 0),
                    "tool_failures": tool_failures_by_model.get(model_id, 0),
                    "tool_calls": tool_calls_by_model.get(model_id, 0),
                    "observed_successes": observed_successes,
                    "observed_failures": observed_failures,
                    "samples": failure_samples,
                    "coverage": "bounded_logs" if observed_successes + observed_failures else "unavailable",
                },
                "latency": {
                    "ttft_p50_seconds": None,
                    "total_p50_seconds": latency_p50,
                    "total_p95_seconds": _percentile(latencies, 0.95),
                    "samples": len(latencies),
                    "coverage": "bounded_logs" if latencies else "unavailable",
                },
                "trend": [{"day": day, "requests": model["trend_map"][day]} for day in trend_days],
                "insight": insight,
            }
        )
        model.pop("cache_tokens_raw", None)
        model.pop("trend_map", None)
        final_models.append(model)

    comparable_models = sorted(
        (
            item
            for item in final_models
            if item.get("work_reliability", {}).get("rank_eligible")
            and item.get("work_reliability", {}).get("failure_rate_upper_bound_95") is not None
        ),
        key=lambda item: (
            _number(item["work_reliability"].get("failure_rate_upper_bound_95"), 1),
            _number(item["work_reliability"].get("unrecovered_failure_rate"), 1),
            -_integer(item["work_reliability"].get("eligible_tasks")),
            str(item.get("display_name") or item.get("model_id") or "").lower(),
        ),
    )
    for rank, item in enumerate(comparable_models, start=1):
        item["work_reliability"]["rank"] = rank
    for item in final_models:
        item["work_reliability"]["ranked_models"] = len(comparable_models)

    final_models.sort(key=lambda item: item["total_tokens"], reverse=True)
    active_models = sum(1 for item in final_models if _integer(item.get("sessions")) > 0)
    known_cost_models = sum(
        1
        for item in final_models
        if _integer(item.get("sessions")) > 0
        and item["cost_kind"] in {"actual", "estimated", "free", "mixed"}
    )
    return {
        "period_days": days,
        "period": _period_payload(days, period_start, period_end),
        "models": final_models,
        "summary": {
            "models": len(final_models),
            "inventory_models": len(final_models),
            "active_models": active_models,
            "requests": sum(_integer(item.get("requests")) for item in final_models),
            "total_tokens": sum(_integer(item.get("total_tokens")) for item in final_models),
            "cost_usd": sum(_number(item.get("cost_usd")) for item in final_models),
            "known_cost_models": known_cost_models,
            "subscription_models": sum(
                1
                for item in final_models
                if _integer(item.get("sessions")) > 0
                and item["cost_kind"] in {"subscription", "mixed"}
            ),
            "reliability_ranked_models": len(comparable_models),
        },
        "coverage": {
            "model_source": "all-time distinct model IDs from Hermes session_model_usage with session-row fallback; metrics honor the selected period",
            "task_types": (
                "Session Lens classifies each session once as Orchestration, Coding, Writing, Analysis, or General, in that order, "
                "from recorded tool calls, arguments, code-mutating commands, and artifact paths; sources are not task types and auxiliary jobs are separate"
            ),
            "first_attempt_acceptance": (
                "General and Analysis use the eligible-closed-session proxy; Coding requires a resolved session plus a recorded successful "
                "code artifact save or commit; Writing requires a resolved session plus a recorded successful non-code artifact write; "
                "Orchestration and auxiliary jobs are unavailable"
            ),
            "retry_switch": (
                "rewinds, near-identical prompts resent to the same model within five minutes, or model changes within the same task role; "
                "different models on different roles are excluded"
            ),
            "failure_latency": (
                "fail rate counts API/request errors, timeouts, and rate limits from bounded local Hermes agent logs; "
                "tool-call failures are counted separately from session records"
            ),
            "work_reliability": (
                "main-role completed tasks are clean, recovered, or completed with intervention; a failed task is unrecovered only when its last "
                "attributable bounded-log model event remains an API failure; orchestration, auxiliary, open, cancelled, ambiguous, and uncovered "
                "runs are excluded from rates, and comparable models rank by the 95% Wilson upper bound after the sample floor"
            ),
            "ttft_available": False,
            "cache": "a zero is shown only when the route has demonstrated cache reporting in the selected period; otherwise unavailable is returned",
            "log_start_at": min(log_timestamps) if log_timestamps else None,
            "log_end_at": max(log_timestamps) if log_timestamps else None,
            "unattributed_log_failures": unattributed_failures,
            "recorded_failure_events": sum(failure_counts.values()),
            "recorded_tool_failures": len(tool_failure_rows),
            "attributed_tool_failures": sum(tool_failures_by_model.values()),
            "unattributed_tool_failures": unattributed_tool_failures,
            "recorded_tool_calls": len(tool_call_rows),
            "attributed_tool_calls": sum(tool_calls_by_model.values()),
            "unattributed_tool_calls": unattributed_tool_calls,
            "rate_sample_threshold": rate_sample_threshold,
            "configured_route_mappings": len(configured_route_mappings),
            "historical_route_mappings": len(historical_route_mappings),
            "route_mapping_config_path": "plugins.entries.session-lens.settings.model_route_mappings",
        },
        "generated_at": time.time(),
    }


def _ai_models_database_revision() -> Tuple[Any, ...]:
    with _database() as db:
        last_activity = _db_connection(db).execute(
            "SELECT MAX(coalesce(last_activity_at, started_at)) FROM sessions"
        ).fetchone()[0]
    database_path = _hermes_home() / "state.db"
    wal_path = Path(str(database_path) + "-wal")

    def signature(path: Path) -> Tuple[int, int]:
        try:
            stat = path.stat()
            return (stat.st_mtime_ns, stat.st_size)
        except OSError:
            return (0, 0)

    return (_number(last_activity, 0), *signature(database_path), *signature(wal_path))


def _ai_models_sync(
    days: int,
    start_at: Optional[float] = None,
    end_at: Optional[float] = None,
    fresh: bool = False,
) -> Dict[str, Any]:
    settings = _plugin_settings()
    _period_bounds(days, start_at, end_at)
    settings_hash = hashlib.sha256(
        json.dumps(settings, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    cache_key = (
        days,
        _number(start_at) if start_at is not None else None,
        _number(end_at) if end_at is not None else None,
        *_ai_models_database_revision(),
        settings_hash,
    )
    now = time.time()
    with _ai_models_cache_lock:
        if not fresh:
            cached = _ai_models_cache.get(cache_key)
            if cached and now - cached[0] < AI_MODELS_CACHE_TTL_SECONDS:
                payload = copy.deepcopy(cached[1])
                payload["cached"] = True
                return payload

    payload = _ai_models_payload_sync(
        days,
        start_at,
        end_at,
        settings=settings,
    )
    payload["cached"] = False
    payload["cache_ttl_seconds"] = AI_MODELS_CACHE_TTL_SECONDS
    with _ai_models_cache_lock:
        for key, (cached_at, _) in list(_ai_models_cache.items()):
            if now - cached_at >= AI_MODELS_CACHE_TTL_SECONDS:
                _ai_models_cache.pop(key, None)
        _ai_models_cache[cache_key] = (time.time(), copy.deepcopy(payload))
    return payload


@router.get("/ai-models")
async def ai_models(
    days: int = Query(30, ge=0, le=3650),
    start_at: Optional[float] = Query(None, ge=0),
    end_at: Optional[float] = Query(None, ge=0),
    fresh: bool = False,
) -> Dict[str, Any]:
    return await asyncio.to_thread(_ai_models_sync, days, start_at, end_at, fresh)


def _tools_sync(
    days: int,
    start_at: Optional[float] = None,
    end_at: Optional[float] = None,
) -> Dict[str, Any]:
    period_start, period_end = _period_bounds(days, start_at, end_at)
    period_sql, period_params = _period_sql("s.started_at", period_start, period_end)
    with _database() as db:
        # Match Hermes Insights' double-count protection: calls may be
        # represented on both the assistant envelope and the tool-result row,
        # so take the higher count per tool rather than summing both sources.
        # Parsing only the compact JSON envelope is cheap; only coarse failure
        # candidates bring bounded result bodies into Python for confirmation.
        assistant_rows = _db_connection(db).execute(
            f"""
            SELECT m.session_id, m.tool_calls, m.timestamp
            FROM messages m
            JOIN sessions s ON s.id=m.session_id
            WHERE {period_sql} AND coalesce(s.hidden,0)=0
              AND coalesce(m.active,1)=1 AND m.role='assistant'
              AND m.tool_calls IS NOT NULL
            ORDER BY m.id DESC LIMIT 50001
            """,
            tuple(period_params),
        ).fetchall()
        truncated = len(assistant_rows) > 50000
        assistant_rows = assistant_rows[:50000]
        assistant: Dict[str, Dict[str, Any]] = {}
        for row in assistant_rows:
            for call in _iter_tool_calls(row["tool_calls"]):
                name = call["name"]
                entry = assistant.setdefault(
                    name,
                    {"calls": 0, "sessions": set(), "last_used_at": None},
                )
                entry["calls"] += 1
                entry["sessions"].add(row["session_id"])
                timestamp = _number(row["timestamp"], 0)
                if timestamp and (not entry["last_used_at"] or timestamp > entry["last_used_at"]):
                    entry["last_used_at"] = timestamp

        result_rows = _db_connection(db).execute(
            f"""
            SELECT m.tool_name AS name, COUNT(*) AS calls,
                   COUNT(DISTINCT m.session_id) AS sessions,
                   MAX(m.timestamp) AS last_used_at
            FROM messages m
            JOIN sessions s ON s.id=m.session_id
            WHERE {period_sql} AND coalesce(s.hidden,0)=0
              AND coalesce(m.active,1)=1 AND m.role='tool'
              AND m.tool_name IS NOT NULL
            GROUP BY m.tool_name
            """,
            tuple(period_params),
        ).fetchall()
        results = {row["name"]: _row_dict(row) for row in result_rows}
        failures_by_tool: Counter[str] = Counter()
        for failure in _confirmed_failure_rows(
            _db_connection(db),
            period_sql + " AND coalesce(s.hidden,0)=0",
            period_params,
        ):
            tool_name = str(failure.get("tool_name") or "").strip()
            if tool_name:
                failures_by_tool[tool_name] += 1

        tools = []
        for name in set(assistant) | set(results):
            assistant_entry = assistant.get(name, {})
            result_entry = results.get(name, {})
            calls = max(
                _integer(assistant_entry.get("calls")),
                _integer(result_entry.get("calls")),
            )
            failures = failures_by_tool.get(name, 0)
            assistant_sessions = len(assistant_entry.get("sessions", set()))
            result_sessions = _integer(result_entry.get("sessions"))
            last_used_at = max(
                _number(assistant_entry.get("last_used_at"), 0),
                _number(result_entry.get("last_used_at"), 0),
            ) or None
            tools.append(
                {
                    "name": name,
                    "calls": calls,
                    "failures": failures,
                    "sessions": max(assistant_sessions, result_sessions),
                    "last_used_at": last_used_at,
                    "failure_rate": failures / calls if calls else 0,
                }
            )
        tools.sort(key=lambda item: (item["failures"], item["calls"], item["name"]), reverse=True)
        return {
            "period_days": days,
            "period": _period_payload(days, period_start, period_end),
            "tools": tools,
            "totals": {
                "calls": sum(item["calls"] for item in tools),
                "failures": sum(item["failures"] for item in tools),
                "distinct_tools": len(tools),
            },
            "truncated": truncated,
            "generated_at": time.time(),
        }


@router.get("/tools")
async def tools(
    days: int = Query(30, ge=0, le=3650),
    start_at: Optional[float] = Query(None, ge=0),
    end_at: Optional[float] = Query(None, ge=0),
) -> Dict[str, Any]:
    return await asyncio.to_thread(_tools_sync, days, start_at, end_at)


def _skills_sync(
    days: int,
    start_at: Optional[float] = None,
    end_at: Optional[float] = None,
) -> Dict[str, Any]:
    period_start, period_end = _period_bounds(days, start_at, end_at)
    period_sql, period_params = _period_sql("s.started_at", period_start, period_end)
    with _database() as db:
        rows = _db_connection(db).execute(
            f"""
            SELECT m.session_id, m.tool_calls, m.timestamp
            FROM messages m
            JOIN sessions s ON s.id=m.session_id
            WHERE {period_sql} AND coalesce(s.hidden,0)=0
              AND m.role='assistant' AND m.tool_calls IS NOT NULL
              AND (instr(m.tool_calls,'skill_view') > 0 OR instr(m.tool_calls,'skill_manage') > 0)
            ORDER BY m.timestamp DESC
            """,
            tuple(period_params),
        ).fetchall()
        skills: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            for call in _iter_tool_calls(row["tool_calls"]):
                if call["name"] not in {"skill_view", "skill_manage"}:
                    continue
                name = call["arguments"].get("name")
                if not isinstance(name, str) or not name.strip():
                    continue
                key = name.strip()
                entry = skills.setdefault(
                    key,
                    {
                        "name": key,
                        "view_count": 0,
                        "manage_count": 0,
                        "sessions": set(),
                        "last_used_at": None,
                        "evidence": "recorded invocation",
                    },
                )
                entry["view_count" if call["name"] == "skill_view" else "manage_count"] += 1
                entry["sessions"].add(row["session_id"])
                timestamp = _number(row["timestamp"], 0)
                if timestamp and (not entry["last_used_at"] or timestamp > entry["last_used_at"]):
                    entry["last_used_at"] = timestamp
        result = []
        for entry in skills.values():
            entry["sessions"] = len(entry["sessions"])
            entry["total_actions"] = entry["view_count"] + entry["manage_count"]
            result.append(entry)
        result.sort(key=lambda item: (item["total_actions"], item["name"]), reverse=True)
        return {
            "period_days": days,
            "period": _period_payload(days, period_start, period_end),
            "skills": result,
            "totals": {
                "loads": sum(item["view_count"] for item in result),
                "management_actions": sum(item["manage_count"] for item in result),
                "distinct_skills": len(result),
            },
            "definition": "Invoked means a recorded skill_view or skill_manage tool call; availability alone is not counted.",
            "generated_at": time.time(),
        }


@router.get("/skills")
async def skills(
    days: int = Query(30, ge=0, le=3650),
    start_at: Optional[float] = Query(None, ge=0),
    end_at: Optional[float] = Query(None, ge=0),
) -> Dict[str, Any]:
    return await asyncio.to_thread(_skills_sync, days, start_at, end_at)


def _profile_db_paths(home: Path) -> List[Tuple[str, Path]]:
    paths: List[Tuple[str, Path]] = []
    root_db = home / "state.db"
    if root_db.exists():
        paths.append(("default", root_db))
    profiles_root = home / "profiles"
    if profiles_root.exists():
        for profile_dir in sorted(path for path in profiles_root.iterdir() if path.is_dir()):
            db_path = profile_dir / "state.db"
            if db_path.exists():
                paths.append((profile_dir.name, db_path))
    return paths


def _profile_summary(
    name: str,
    path: Path,
    start_at: float,
    end_at: Optional[float],
) -> Dict[str, Any]:
    period_sql, period_params = _period_sql("started_at", start_at, end_at)

    def read(connection: sqlite3.Connection) -> Dict[str, Any]:
        totals = _row_dict(
            connection.execute(
                f"""
                SELECT COUNT(*) AS sessions,
                       coalesce(SUM(message_count),0) AS messages,
                       coalesce(SUM(tool_call_count),0) AS tool_calls,
                       coalesce(SUM(input_tokens + output_tokens + cache_read_tokens + cache_write_tokens),0) AS total_tokens,
                       coalesce(SUM(CASE
                           WHEN actual_cost_usd > 0 THEN actual_cost_usd
                           WHEN estimated_cost_usd > 0 THEN estimated_cost_usd
                           ELSE 0 END),0) AS recorded_cost_usd,
                       MAX(coalesce(last_activity_at, started_at)) AS last_activity_at
                FROM sessions WHERE {period_sql} AND coalesce(hidden,0)=0
                """,
                tuple(period_params),
            ).fetchone()
        )
        outcomes: Counter[str] = Counter()
        for row in connection.execute(
            f"""
            SELECT end_reason, ended_at, last_activity_at, started_at
            FROM sessions WHERE {period_sql} AND coalesce(hidden,0)=0
            """,
            tuple(period_params),
        ).fetchall():
            outcomes[_session_outcome(_row_dict(row))["outcome"]] += 1
        models = [
            {"model": row["model"] or "unknown", "sessions": _integer(row["sessions"])}
            for row in connection.execute(
                f"""
                SELECT model, COUNT(*) AS sessions FROM sessions
                WHERE {period_sql} AND coalesce(hidden,0)=0
                GROUP BY model ORDER BY sessions DESC LIMIT 5
                """,
                tuple(period_params),
            ).fetchall()
        ]
        return {"totals": totals, "outcomes": outcomes, "models": models}

    try:
        with _database(path) as db:
            material = read(_db_connection(db))
    except sqlite3.OperationalError:
        # A dormant profile can retain WAL journal mode without writable
        # sidecars. If there is no WAL to reconcile, SQLite immutable mode is
        # a safe read-only fallback that neither creates nor updates files.
        if Path(str(path) + "-wal").exists():
            raise
        connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            material = read(connection)
        finally:
            connection.close()
    totals = material["totals"]
    outcomes = material["outcomes"]
    models = material["models"]
    stat = path.stat()
    return {
        "name": name,
        "is_default": name == "default",
        "sessions": _integer(totals.get("sessions")),
        "messages": _integer(totals.get("messages")),
        "tool_calls": _integer(totals.get("tool_calls")),
        "total_tokens": _integer(totals.get("total_tokens")),
        "recorded_cost_usd": _number(totals.get("recorded_cost_usd")),
        "last_activity_at": totals.get("last_activity_at"),
        "outcomes": dict(outcomes),
        "models": models,
        "database_size_bytes": stat.st_size,
        "database_updated_at": stat.st_mtime,
    }


def _profiles_sync(
    days: int,
    start_at: Optional[float] = None,
    end_at: Optional[float] = None,
) -> Dict[str, Any]:
    period_start, period_end = _period_bounds(days, start_at, end_at)
    profiles = []
    errors = []
    for name, path in _profile_db_paths(_hermes_home()):
        try:
            profiles.append(_profile_summary(name, path, period_start, period_end))
        except Exception as error:
            errors.append({"profile": name, "error": _clean_text(error, 240)})
    profiles.sort(key=lambda item: _number(item.get("last_activity_at")), reverse=True)
    return {
        "period_days": days,
        "period": _period_payload(days, period_start, period_end),
        "profiles": profiles,
        "totals": {
            "profiles": len(profiles),
            "sessions": sum(item["sessions"] for item in profiles),
            "total_tokens": sum(item["total_tokens"] for item in profiles),
            "recorded_cost_usd": sum(item["recorded_cost_usd"] for item in profiles),
        },
        "errors": errors,
        "generated_at": time.time(),
    }


@router.get("/profiles")
async def profiles(
    days: int = Query(30, ge=0, le=3650),
    start_at: Optional[float] = Query(None, ge=0),
    end_at: Optional[float] = Query(None, ge=0),
) -> Dict[str, Any]:
    return await asyncio.to_thread(_profiles_sync, days, start_at, end_at)


def _read_json_file(path: Path, max_bytes: int = 2 * 1024 * 1024) -> Any:
    if not path.exists() or path.stat().st_size > max_bytes:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None


def _profile_file_paths(home: Path, relative: Path) -> List[Tuple[str, Path]]:
    paths = [("default", home / relative)]
    profiles_root = home / "profiles"
    if profiles_root.exists():
        paths.extend(
            (profile_dir.name, profile_dir / relative)
            for profile_dir in sorted(path for path in profiles_root.iterdir() if path.is_dir())
        )
    return [(name, path) for name, path in paths if path.exists()]


def _gateway_sync() -> Dict[str, Any]:
    gateways = []
    for profile, path in _profile_file_paths(_hermes_home(), Path("gateway_state.json")):
        state = _read_json_file(path)
        if not isinstance(state, dict):
            continue
        platforms = []
        raw_platforms = state.get("platforms")
        if isinstance(raw_platforms, dict):
            for name, value in raw_platforms.items():
                value = value if isinstance(value, dict) else {"state": value}
                platforms.append(
                    {
                        "name": str(name),
                        "state": _clean_text(value.get("state"), 80),
                        "needs_attention": bool(value.get("needs_attention")),
                        "error_code": _clean_text(value.get("error_code"), 120) or None,
                        "error_message": _clean_text(value.get("error_message"), 320) or None,
                        "updated_at": value.get("updated_at"),
                    }
                )
        gateways.append(
            {
                "profile": profile,
                "state": _clean_text(state.get("gateway_state") or state.get("state"), 80) or "unknown",
                "kind": _clean_text(state.get("kind"), 80) or None,
                "pid": _integer(state.get("pid")) or None,
                "start_time": state.get("start_time"),
                "updated_at": state.get("updated_at") or path.stat().st_mtime,
                "exit_reason": _clean_text(state.get("exit_reason"), 240) or None,
                "restart_requested": bool(state.get("restart_requested")),
                "active_agents": _integer(state.get("active_agents")),
                "code_version": _clean_text(state.get("code_version"), 80) or None,
                "platforms": platforms,
            }
        )
    return {"gateways": gateways, "generated_at": time.time()}


@router.get("/gateway")
async def gateway() -> Dict[str, Any]:
    return await asyncio.to_thread(_gateway_sync)


def _schedules_sync() -> Dict[str, Any]:
    schedules = []
    for profile, path in _profile_file_paths(_hermes_home(), Path("cron") / "jobs.json"):
        document = _read_json_file(path)
        jobs = document.get("jobs") if isinstance(document, dict) else document
        if not isinstance(jobs, list):
            continue
        for job in jobs:
            if not isinstance(job, dict):
                continue
            schedule = job.get("schedule") if isinstance(job.get("schedule"), dict) else {}
            state = job.get("state") if isinstance(job.get("state"), dict) else {}
            skills = job.get("skills") if isinstance(job.get("skills"), list) else []
            schedules.append(
                {
                    "profile": profile,
                    "id": _clean_text(job.get("id"), 160),
                    "name": _clean_text(job.get("name"), 180) or "Untitled schedule",
                    "enabled": bool(job.get("enabled")),
                    "schedule": _clean_text(job.get("schedule_display") or schedule.get("display") or schedule.get("expr") or schedule.get("run_at"), 180),
                    "schedule_kind": _clean_text(schedule.get("kind"), 80),
                    "model": _clean_text(job.get("model"), 160) or None,
                    "provider": _clean_text(job.get("provider"), 120) or None,
                    "skills": [_clean_text(item, 120) for item in skills[:20]],
                    "no_agent": bool(job.get("no_agent")),
                    "next_run_at": job.get("next_run_at") or state.get("next_run_at"),
                    "last_run_at": job.get("last_run_at") or state.get("last_run_at"),
                    "last_status": _clean_text(job.get("last_status") or state.get("last_status"), 80) or None,
                    "last_error": _clean_text(job.get("last_error") or state.get("last_error"), 320) or None,
                    "last_delivery_error": _clean_text(job.get("last_delivery_error") or state.get("last_delivery_error"), 320) or None,
                    "failure_streak": _integer(job.get("failure_streak") or state.get("failure_streak")),
                }
            )
    schedules.sort(key=lambda item: (not item["enabled"], _number(item.get("next_run_at"), float("inf")), item["name"]))
    return {
        "schedules": schedules,
        "totals": {
            "jobs": len(schedules),
            "enabled": sum(1 for item in schedules if item["enabled"]),
            "failing": sum(1 for item in schedules if item["failure_streak"] or item["last_error"]),
        },
        "privacy": {"prompts_included": False},
        "generated_at": time.time(),
    }


@router.get("/schedules")
async def schedules() -> Dict[str, Any]:
    return await asyncio.to_thread(_schedules_sync)


def _ai_usage_sync(fresh: bool = False) -> Dict[str, Any]:
    global _ai_usage_cache
    now = time.time()
    with _ai_usage_cache_lock:
        if not fresh and _ai_usage_cache and now - _ai_usage_cache[0] < AI_USAGE_CACHE_TTL_SECONDS:
            cached = copy.deepcopy(_ai_usage_cache[1])
            cached["cached"] = True
            return cached

    collectors = {
        "codex": _collect_codex_usage,
        "anthropic": _collect_anthropic_usage,
        "nous": _collect_nous_usage,
        "openrouter": _collect_openrouter_usage,
        "deepseek": _collect_deepseek_usage,
        "grok": _collect_grok_usage,
        "kimi": _collect_kimi_usage,
        "zai": _collect_zai_usage,
    }
    results: Dict[str, Dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=len(collectors), thread_name_prefix="session-lens-usage") as pool:
        futures = {pool.submit(collector): provider for provider, collector in collectors.items()}
        for future in as_completed(futures):
            provider = futures[future]
            try:
                result = future.result()
            except Exception as error:
                result = _provider_payload(provider, status="unavailable", message=_provider_message(error))
            results[provider] = result

    with _ai_usage_cache_lock:
        for provider in _AI_USAGE_PROVIDER_ORDER:
            result = results.get(provider) or _provider_payload(
                provider,
                status="unavailable",
                message="Provider collector returned no result.",
            )
            if result.get("status") == "ok":
                _ai_usage_last_success[provider] = copy.deepcopy(result)
            elif result.get("status") in {"not_configured", "expired", "forbidden"}:
                _ai_usage_last_success.pop(provider, None)
            elif result.get("status") == "unavailable" and provider in _ai_usage_last_success:
                current_status = result.get("status")
                current_message = result.get("message")
                result = copy.deepcopy(_ai_usage_last_success[provider])
                result.update(
                    {
                        "status": "stale",
                        "stale": True,
                        "last_error_status": current_status,
                        "message": current_message or "The latest refresh failed; showing the last successful reading.",
                    }
                )
            results[provider] = result

        providers = [results[provider] for provider in _AI_USAGE_PROVIDER_ORDER]
        payload = {
            "providers": providers,
            "summary": _ai_usage_summary(providers),
            "generated_at": time.time(),
            "cached": False,
            "cache_ttl_seconds": AI_USAGE_CACHE_TTL_SECONDS,
            "privacy": {
                "credentials_returned_to_desktop": False,
                "browser_cookies_read": False,
                "external_requests": "Direct authenticated quota requests to the configured providers only",
            },
        }
        _ai_usage_cache = (time.time(), copy.deepcopy(payload))
        return payload


@router.get("/ai-usage")
async def ai_usage(fresh: bool = False) -> Dict[str, Any]:
    return await asyncio.to_thread(_ai_usage_sync, fresh)


def _system_sync() -> Dict[str, Any]:
    with _database() as db:
        path = Path(getattr(db, "db_path", _hermes_home() / "state.db"))
        schema_row = _db_connection(db).execute("SELECT version FROM schema_version").fetchone()
        counts = _db_connection(db).execute(
            """
            SELECT (SELECT COUNT(*) FROM sessions) AS sessions,
                   (SELECT COUNT(*) FROM messages) AS messages,
                   (SELECT COUNT(*) FROM session_model_usage) AS model_usage_rows,
                   (SELECT COUNT(*) FROM async_delegations) AS delegations
            """
        ).fetchone()
        fts_names = [
            row[0]
            for row in _db_connection(db).execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'messages_fts%' ORDER BY name"
            ).fetchall()
        ]
        stat = path.stat() if path.exists() else None
        wal = Path(str(path) + "-wal")
        return {
            "plugin": {"name": "Hermes Session Lens", "id": "session-lens", "version": PLUGIN_VERSION},
            "database": {
                "available": path.exists(),
                "read_only": bool(getattr(db, "read_only", False)),
                "schema_version": _integer(schema_row[0] if schema_row else 0),
                "path": str(path),
                "size_bytes": stat.st_size if stat else 0,
                "last_modified_at": stat.st_mtime if stat else None,
                "wal_size_bytes": wal.stat().st_size if wal.exists() else 0,
                "fts_enabled": any(name == "messages_fts" for name in fts_names),
                "trigram_fts_enabled": any(name == "messages_fts_trigram" for name in fts_names),
            },
            "counts": _row_dict(counts),
            "privacy": {
                "network_upload": False,
                "provider_usage_requests": True,
                "provider_credentials_returned_to_desktop": False,
                "mutation_endpoints": 0,
                "snippets_redacted_and_bounded": True,
                "database_connection": "Hermes SessionDB(read_only=True)",
            },
            "capabilities": _compat_capabilities(),
            "limits": {
                "session_page": MAX_SESSION_PAGE,
                "session_event_analysis": MAX_ANALYSIS_EVENTS,
                "search_matches": MAX_SEARCH_MATCHES,
            },
            "generated_at": time.time(),
        }


@router.get("/system")
async def system() -> Dict[str, Any]:
    return await asyncio.to_thread(_system_sync)

__all__ = [name for name in globals() if not name.startswith("__")]
