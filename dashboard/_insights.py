"""Views built on records Hermes 0.23 started keeping.

* **Turns** — the gateway's per-turn result ledger (``state_meta`` keys
  ``gateway.admission.result.v1.<id>``): tokens, cost, API calls, why the turn
  ended, failures, interruptions, compression. The ledger also stores the
  reply text and the message list; those are never selected.
* **System prompt weight** — ``system_prompts`` (every prompt Hermes sent,
  by hash) joined to ``sessions.system_prompt_hash`` / ``tool_names``: how
  much of each call is the prompt and the tool schemas, what the prompt is
  made of, and which listed skills (``skills/.usage.json``) are rarely used.
* **Prompt cache** — ``sessions.prompt_cache_clock`` (per-session cache tier
  and expiry), ``cache/prompt_cache_routes.json`` (Hermes' learned hit gaps
  per route), and ``task='cache_warm'`` usage rows (keep-alive spend).
* **Cron history** — ``cron/executions.db`` (every fire, including missed and
  cancelled ones, and incidents) and ``cron/usage_audit.jsonl`` (tokens and
  duration per fire).
* **Gateway restarts** — ``gateway_heartbeats``: each gateway start, its last
  heartbeat, and the gaps between runs.

Everything is read-only: SQLite opened with ``mode=ro``, JSON files read.
"""

from __future__ import annotations

import json
import statistics

from ._common import *
from ._logparse import _percentile
from ._hermes_compat import (
    _default_db_path,
    _get_profile_scope,
    _hermes_home,
    _profile_home_path,
    _read_only_uri,
    _scope_db_paths,
)

_LEDGER_PREFIX = "gateway.admission.result.v1."
_TURN_LIST_SIZE = 12
_PROMPT_CHARS_PER_TOKEN = 4.0
_SKILL_STALE_DAYS = 30
_CRON_RECENT = 25


# ── Shared plumbing ───────────────────────────────────────────────────────


def _scope_profiles() -> List[Tuple[str, Path, Path]]:
    """(profile, home, state.db) for every profile in the request's scope."""
    names = _get_profile_scope()
    if names:
        return [(name, _profile_home_path(name), path) for name, path in _scope_db_paths()]
    home = _hermes_home()
    parts = [part.lower() for part in home.parts]
    name = home.name if "profiles" in parts else "default"
    path = _default_db_path()
    return [(name, home, Path(path))] if Path(path).exists() else []


def _ro_connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(_read_only_uri(path), uri=True, timeout=5)
    connection.row_factory = sqlite3.Row
    return connection


def _table_columns(connection: sqlite3.Connection, table: str) -> set:
    try:
        return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}
    except sqlite3.Error:
        return set()


def _iso_epoch(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _round(value: Optional[float], places: int = 4) -> Optional[float]:
    return None if value is None else round(float(value), places)


def _in_period(at: Optional[float], start: float, end: Optional[float]) -> bool:
    if at is None:
        return False
    return at >= start and (end is None or at < end)


def _chunks(values: List[Any], size: int = 500) -> Iterator[List[Any]]:
    for index in range(0, len(values), size):
        yield values[index:index + size]


# ── Turns: the gateway's per-turn ledger ──────────────────────────────────

_TURN_FIELDS = (
    ("session_id", "$.result.session_id"), ("model", "$.result.model"), ("provider", "$.result.provider"),
    ("input_tokens", "$.result.input_tokens"), ("output_tokens", "$.result.output_tokens"),
    ("cache_read_tokens", "$.result.cache_read_tokens"), ("cache_write_tokens", "$.result.cache_write_tokens"),
    ("reasoning_tokens", "$.result.reasoning_tokens"), ("cost_usd", "$.result.estimated_cost_usd"),
    ("cost_status", "$.result.cost_status"), ("api_calls", "$.result.api_calls"), ("exit_reason", "$.result.turn_exit_reason"),
    ("failure_reason", "$.result.failure_reason"), ("failed", "$.result.failed"), ("completed", "$.result.completed"),
    ("interrupted", "$.result.interrupted"), ("partial", "$.result.partial"), ("compacted", "$.result.compacted_in_place"),
    ("compression_exhausted", "$.result.compression_exhausted"), ("usage_limit", "$.result.usage_limit"),
    ("error", "$.result.error"), ("user_row", "$.result.persisted_turn.user_row_id"),
    ("final_row", "$.result.persisted_turn.final_assistant_row_id"),
)


def _ledger_rows(connection: sqlite3.Connection) -> List[Dict[str, Any]]:
    if "key" not in _table_columns(connection, "state_meta"):
        return []
    select = ", ".join(f"json_extract(value, '{path}') AS {name}" for name, path in _TURN_FIELDS)
    rows = connection.execute(
        # Hermes stores {"result": {...}, "usage": {...}, "_stored": {...}}; only named
        # result fields are extracted, never the reply text or the message list.
        f"SELECT key, {select} FROM state_meta WHERE key LIKE ? AND json_valid(value)",
        (_LEDGER_PREFIX + "%",),
    ).fetchall()
    return [dict(row) for row in rows]


def _message_times(connection: sqlite3.Connection, ids: List[int]) -> Dict[int, float]:
    times: Dict[int, float] = {}
    for chunk in _chunks(sorted({int(value) for value in ids if value})):
        placeholders = ",".join("?" for _ in chunk)
        for row in connection.execute(f"SELECT id, timestamp FROM messages WHERE id IN ({placeholders})", chunk):
            at = _iso_epoch(row[1])
            if at is not None:
                times[int(row[0])] = at
    return times


def _session_meta(connection: sqlite3.Connection, ids: List[str]) -> Dict[str, Dict[str, Any]]:
    meta: Dict[str, Dict[str, Any]] = {}
    for chunk in _chunks(sorted({str(value) for value in ids if value})):
        placeholders = ",".join("?" for _ in chunk)
        for row in connection.execute(
            f"SELECT id, title, source, started_at FROM sessions WHERE id IN ({placeholders})", chunk
        ):
            meta[str(row["id"])] = {"title": row["title"], "source": row["source"], "started_at": row["started_at"]}
    return meta


def _turn_flags(turn: Mapping[str, Any]) -> List[str]:
    flags = []
    if turn.get("failed"):
        flags.append("failed")
    if turn.get("interrupted"):
        flags.append("interrupted")
    if turn.get("partial"):
        flags.append("partial")
    if turn.get("compression_exhausted"):
        flags.append("context full")
    elif turn.get("compacted"):
        flags.append("compacted")
    if turn.get("usage_limit"):
        flags.append("usage limit")
    if str(turn.get("exit_reason") or "").startswith("max_iterations"):
        flags.append("iteration cap")
    return flags


def _turns_sync(days: int = 30, start_at: Optional[float] = None, end_at: Optional[float] = None) -> Dict[str, Any]:
    start, end = _period_bounds(days, start_at, end_at)
    turns: List[Dict[str, Any]] = []
    for profile, _home, path in _scope_profiles():
        _check_route_budget()
        try:
            connection = _ro_connect(path)
        except sqlite3.Error:
            continue
        try:
            rows = _ledger_rows(connection)
            times = _message_times(connection, [row.get("user_row") for row in rows] + [row.get("final_row") for row in rows])
            sessions = _session_meta(connection, [row.get("session_id") for row in rows])
        finally:
            connection.close()
        for row in rows:
            session = sessions.get(str(row.get("session_id") or "")) or {}
            began = times.get(_integer(row.get("user_row")))
            finished = times.get(_integer(row.get("final_row")))
            at = began or finished or _number(session.get("started_at"), None)
            if not _in_period(at, start, end):
                continue
            tokens = sum(_integer(row.get(name)) for name in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens"))
            turn = {
                "id": str(row["key"])[len(_LEDGER_PREFIX):][:64],
                "profile": profile,
                "session_id": row.get("session_id"),
                "title": _clean_text(session.get("title"), 120) or None,
                "source": session.get("source"),
                "at": at,
                "duration_seconds": _round(finished - began, 1) if began and finished and finished >= began else None,
                "model": _clean_text(row.get("model"), 120) or None,
                "provider": _clean_text(row.get("provider"), 80) or None,
                "tokens": tokens,
                "input_tokens": _integer(row.get("input_tokens")),
                "output_tokens": _integer(row.get("output_tokens")),
                "cache_read_tokens": _integer(row.get("cache_read_tokens")),
                "cost_usd": _round(_number(row.get("cost_usd"), 0.0)),
                "cost_status": _clean_text(row.get("cost_status"), 40) or None,
                "api_calls": _integer(row.get("api_calls")),
                "exit_reason": _clean_text(row.get("exit_reason"), 80) or None,
                "failure_reason": _clean_text(row.get("failure_reason"), 160) or None,
                "error": _clean_text(row.get("error"), 200) or None,
                "flags": [],
            }
            turn["flags"] = _turn_flags(row)
            turns.append(turn)

    durations = [turn["duration_seconds"] for turn in turns if turn["duration_seconds"] is not None]
    costs = [turn["cost_usd"] for turn in turns if turn["cost_usd"]]
    exits = Counter(turn["exit_reason"] or "unknown" for turn in turns)
    by_model: Dict[str, Dict[str, Any]] = {}
    for turn in turns:
        key = turn["model"] or "unknown"
        item = by_model.setdefault(key, {"model": key, "provider": turn["provider"], "turns": 0, "problems": 0,
                                         "cost_usd": 0.0, "api_calls": 0, "tokens": 0, "durations": []})
        item["turns"] += 1
        item["problems"] += 1 if set(turn["flags"]) & {"failed", "interrupted", "partial", "context full", "iteration cap"} else 0
        item["cost_usd"] += turn["cost_usd"] or 0.0
        item["api_calls"] += turn["api_calls"]
        item["tokens"] += turn["tokens"]
        if turn["duration_seconds"] is not None:
            item["durations"].append(turn["duration_seconds"])
    models = []
    for item in sorted(by_model.values(), key=lambda value: (-value["turns"], value["model"])):
        durations_m = item.pop("durations")
        models.append({
            **item,
            "cost_usd": round(item["cost_usd"], 4),
            "avg_cost_usd": round(item["cost_usd"] / item["turns"], 4) if item["turns"] else 0.0,
            "avg_api_calls": round(item["api_calls"] / item["turns"], 1) if item["turns"] else 0.0,
            "problem_rate": round(item["problems"] / item["turns"], 4) if item["turns"] else 0.0,
            "duration_p50_seconds": _round(_percentile(durations_m, 0.5), 1),
            "duration_p95_seconds": _round(_percentile(durations_m, 0.95), 1),
        })
    problems = [turn for turn in turns if set(turn["flags"]) & {"failed", "interrupted", "partial", "context full", "usage limit", "iteration cap"}]
    return {
        "summary": {
            "turns": len(turns),
            "failed": sum(1 for turn in turns if "failed" in turn["flags"]),
            "interrupted": sum(1 for turn in turns if "interrupted" in turn["flags"]),
            "partial": sum(1 for turn in turns if "partial" in turn["flags"]),
            "compacted": sum(1 for turn in turns if turn["flags"] and ("compacted" in turn["flags"] or "context full" in turn["flags"])),
            "cost_usd": round(sum(costs), 4),
            "median_cost_usd": _round(statistics.median(costs)) if costs else None,
            "duration_p50_seconds": _round(_percentile(durations, 0.5), 1),
            "duration_p95_seconds": _round(_percentile(durations, 0.95), 1),
            "avg_api_calls": round(sum(turn["api_calls"] for turn in turns) / len(turns), 1) if turns else 0.0,
        },
        "exit_reasons": [{"reason": reason, "turns": count} for reason, count in exits.most_common(8)],
        "models": models[:15],
        "costliest": sorted(turns, key=lambda turn: -(turn["cost_usd"] or 0.0))[:_TURN_LIST_SIZE],
        "slowest": sorted((turn for turn in turns if turn["duration_seconds"] is not None), key=lambda turn: -turn["duration_seconds"])[:8],
        "problems": sorted(problems, key=lambda turn: -(turn["at"] or 0))[:15],
        "available": bool(turns),
        "generated_at": time.time(),
        "definition": (
            "One row per gateway turn from Hermes' turn ledger (Hermes 0.23+): tokens, the cost Hermes estimated, "
            "API calls, and why the turn ended. Times come from the turn's first and last stored messages. Turns "
            "Hermes ran outside the gateway (CLI, some desktop paths) are not in the ledger."
        ),
    }


# ── System prompt weight ──────────────────────────────────────────────────

_BANNER_RE = re.compile(r"^(?:═{6,}\s*\n)?([A-Z][A-Z /&-]{2,40})(?: \(.*\))?.*\n═{6,}", re.M)
_HEADING_RE = re.compile(r"^#{1,2} (.+)$", re.M)
_SKILL_LINE_RE = re.compile(r"^\s*- ([A-Za-z0-9_.:@/-]+):", re.M)


def _prompt_segments(prompt: str) -> List[Dict[str, Any]]:
    """Split a system prompt into labelled parts: headings, banners, the skills index."""
    marks: List[Tuple[int, str]] = [(0, "Identity and preamble")]
    for match in _HEADING_RE.finditer(prompt):
        marks.append((match.start(), match.group(1).strip()[:60]))
    for match in re.finditer(r"^═{6,}\s*\n([A-Z][A-Z /&-]{2,40})", prompt, re.M):
        marks.append((match.start(), match.group(1).strip().title()[:60]))
    for tag, label in (("<available_skills>", "Skills index"), ("</available_skills>", "After the skills index")):
        position = prompt.find(tag)
        if position >= 0:
            marks.append((position, label))
    marks.sort()
    segments: List[Dict[str, Any]] = []
    for index, (position, label) in enumerate(marks):
        following = marks[index + 1][0] if index + 1 < len(marks) else len(prompt)
        size = following - position
        if size <= 0:
            continue
        if segments and segments[-1]["label"] == label:
            segments[-1]["chars"] += size
        else:
            segments.append({"label": label, "chars": size})
    merged: Dict[str, int] = {}
    for segment in segments:
        merged[segment["label"]] = merged.get(segment["label"], 0) + segment["chars"]
    total = max(1, len(prompt))
    return sorted(
        ({"label": label, "chars": chars, "tokens": int(chars / _PROMPT_CHARS_PER_TOKEN),
          "share_percent": round(chars * 100.0 / total, 1)} for label, chars in merged.items()),
        key=lambda item: -item["chars"],
    )


def _skills_index_entries(prompt: str) -> List[Dict[str, Any]]:
    start = prompt.find("<available_skills>")
    stop = prompt.find("</available_skills>")
    if start < 0 or stop <= start:
        return []
    block = prompt[start:stop]
    lines = block.splitlines()
    entries: List[Dict[str, Any]] = []
    for line in lines:
        match = _SKILL_LINE_RE.match(line)
        if match:
            entries.append({"name": match.group(1), "chars": len(line) + 1})
    return entries


def _skill_usage(home: Path) -> Dict[str, Dict[str, Any]]:
    path = home / "skills" / ".usage.json"
    try:
        if path.stat().st_size > 8 * 1024 * 1024:
            return {}
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return {}
    return {str(name): value for name, value in data.items() if isinstance(value, dict)} if isinstance(data, dict) else {}


def _tool_schema_sizes(bundle: str) -> List[Dict[str, Any]]:
    try:
        data = json.loads(bundle)
    except ValueError:
        return []
    tools = data.get("tools") if isinstance(data, dict) else None
    if not isinstance(tools, list):
        return []
    sizes = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        function = tool.get("function") if isinstance(tool.get("function"), dict) else tool
        chars = len(json.dumps(tool, ensure_ascii=False))
        sizes.append({"name": _clean_text(function.get("name"), 80) or "tool", "chars": chars,
                      "tokens": int(chars / _PROMPT_CHARS_PER_TOKEN)})
    return sorted(sizes, key=lambda item: -item["chars"])


def _prompt_weight_sync(days: int = 30, start_at: Optional[float] = None, end_at: Optional[float] = None) -> Dict[str, Any]:
    start, end = _period_bounds(days, start_at, end_at)
    now = time.time()
    sessions: List[Dict[str, Any]] = []
    prompt_chars: Dict[str, int] = {}
    latest: Optional[Dict[str, Any]] = None
    auxiliary: Dict[str, Dict[str, Any]] = {}
    for profile, home, path in _scope_profiles():
        _check_route_budget()
        try:
            connection = _ro_connect(path)
        except sqlite3.Error:
            continue
        try:
            columns = _table_columns(connection, "sessions")
            if "system_prompt_hash" not in columns or not _table_columns(connection, "system_prompts"):
                continue
            tools_column = "tool_names" if "tool_names" in columns else "NULL"
            params: List[Any] = [start]
            end_clause = ""
            if end is not None:
                end_clause = " AND started_at < ?"
                params.append(end)
            rows = connection.execute(
                f"""
                SELECT id, title, source, model, system_prompt_hash AS prompt_hash, {tools_column} AS tools_hash,
                       COALESCE(api_call_count, 0) AS calls,
                       COALESCE(input_tokens, 0) + COALESCE(cache_read_tokens, 0) + COALESCE(cache_write_tokens, 0) AS prompt_side,
                       COALESCE(last_activity_at, ended_at, started_at) AS active_at
                FROM sessions
                WHERE system_prompt_hash IS NOT NULL
                  AND COALESCE(last_activity_at, ended_at, started_at) >= ?{end_clause}
                """,
                params,
            ).fetchall()
            hashes = sorted({str(row["prompt_hash"]) for row in rows} | {str(row["tools_hash"]) for row in rows if row["tools_hash"]})
            sizes: Dict[str, int] = {}
            for chunk in _chunks(hashes):
                placeholders = ",".join("?" for _ in chunk)
                for item in connection.execute(f"SELECT hash, length(prompt) FROM system_prompts WHERE hash IN ({placeholders})", chunk):
                    sizes[str(item[0])] = _integer(item[1])
            for row in rows:
                system_chars = sizes.get(str(row["prompt_hash"]), 0)
                tool_chars = sizes.get(str(row["tools_hash"]), 0) if row["tools_hash"] else 0
                prompt_chars[str(row["prompt_hash"])] = system_chars
                sessions.append({
                    "id": row["id"], "profile": profile, "title": _clean_text(row["title"], 120) or None,
                    "source": row["source"], "model": row["model"], "calls": _integer(row["calls"]),
                    "system_tokens": int(system_chars / _PROMPT_CHARS_PER_TOKEN),
                    "tool_tokens": int(tool_chars / _PROMPT_CHARS_PER_TOKEN),
                    "prompt_side_tokens": _integer(row["prompt_side"]),
                    "active_at": row["active_at"], "prompt_hash": row["prompt_hash"], "tools_hash": row["tools_hash"],
                })
            newest = max(rows, key=lambda row: _number(row["active_at"], 0), default=None)
            if newest is not None and (latest is None or _number(newest["active_at"], 0) > latest["active_at"]):
                prompt_row = connection.execute("SELECT prompt FROM system_prompts WHERE hash = ?", (newest["prompt_hash"],)).fetchone()
                bundle_row = (connection.execute("SELECT prompt FROM system_prompts WHERE hash = ?", (newest["tools_hash"],)).fetchone()
                              if newest["tools_hash"] else None)
                latest = {"active_at": _number(newest["active_at"], 0), "profile": profile, "home": home,
                          "session_id": newest["id"], "title": newest["title"],
                          "prompt": str(prompt_row[0]) if prompt_row else "", "bundle": str(bundle_row[0]) if bundle_row else ""}
            if _table_columns(connection, "session_model_usage") >= {"task", "first_seen"}:
                for item in connection.execute(
                    """
                    SELECT task, COUNT(*) AS rows_, SUM(COALESCE(api_call_count, 0)) AS calls,
                           SUM(COALESCE(input_tokens,0)+COALESCE(output_tokens,0)+COALESCE(cache_read_tokens,0)+COALESCE(cache_write_tokens,0)) AS tokens,
                           SUM(CASE WHEN actual_cost_usd > 0 THEN actual_cost_usd ELSE COALESCE(estimated_cost_usd, 0) END) AS cost
                    FROM session_model_usage
                    WHERE COALESCE(task, '') != '' AND COALESCE(last_seen, first_seen, 0) >= ?
                    GROUP BY task
                    """,
                    (start,),
                ):
                    entry = auxiliary.setdefault(str(item["task"]), {"task": str(item["task"]), "calls": 0, "tokens": 0, "cost_usd": 0.0})
                    entry["calls"] += _integer(item["calls"]) or _integer(item["rows_"])
                    entry["tokens"] += _integer(item["tokens"])
                    entry["cost_usd"] += _number(item["cost"], 0.0)
        finally:
            connection.close()

    resent = sum(min(item["prompt_side_tokens"], (item["system_tokens"] + item["tool_tokens"]) * item["calls"]) for item in sessions)
    prompt_side = sum(item["prompt_side_tokens"] for item in sessions)
    system_sizes = [item["system_tokens"] for item in sessions if item["system_tokens"]]
    heaviest = sorted(sessions, key=lambda item: -((item["system_tokens"] + item["tool_tokens"]) * item["calls"]))[:8]
    breakdown = None
    if latest is not None and latest["prompt"]:
        usage = _skill_usage(latest["home"])
        skills = []
        for entry in _skills_index_entries(latest["prompt"]):
            record = usage.get(entry["name"]) or usage.get(entry["name"].split("/")[-1]) or {}
            last_used = _iso_epoch(record.get("last_used_at"))
            uses = _integer(record.get("use_count"))
            stale = (not uses) or (last_used is None) or (now - last_used > _SKILL_STALE_DAYS * 86400)
            skills.append({
                "name": entry["name"], "chars": entry["chars"], "tokens": max(1, int(entry["chars"] / _PROMPT_CHARS_PER_TOKEN)),
                "use_count": uses, "last_used_at": last_used, "pinned": bool(record.get("pinned")),
                "rarely_used": bool(stale and not record.get("pinned")),
            })
        rarely = [skill for skill in skills if skill["rarely_used"]]
        tools = _tool_schema_sizes(latest["bundle"]) if latest["bundle"] else []
        breakdown = {
            "session_id": latest["session_id"],
            "title": _clean_text(latest["title"], 120) or None,
            "profile": latest["profile"],
            "system_tokens": int(len(latest["prompt"]) / _PROMPT_CHARS_PER_TOKEN),
            "tool_tokens": sum(tool["tokens"] for tool in tools),
            "segments": _prompt_segments(latest["prompt"])[:14],
            "skills": {
                "listed": len(skills),
                "tokens": sum(skill["tokens"] for skill in skills),
                "rarely_used": len(rarely),
                "rarely_used_tokens": sum(skill["tokens"] for skill in rarely),
                "stale_days": _SKILL_STALE_DAYS,
                "rows": sorted(rarely, key=lambda skill: -skill["tokens"])[:25],
                "usage_file_found": bool(usage),
            },
            "tools": {"count": len(tools), "tokens": sum(tool["tokens"] for tool in tools), "largest": tools[:10]},
        }
    return {
        "summary": {
            "sessions": len(sessions),
            "distinct_prompts": len(prompt_chars),
            "avg_system_tokens": int(sum(system_sizes) / len(system_sizes)) if system_sizes else 0,
            "max_system_tokens": max(system_sizes) if system_sizes else 0,
            "calls": sum(item["calls"] for item in sessions),
            "resent_tokens": resent,
            "prompt_side_tokens": prompt_side,
            "resent_share_percent": round(resent * 100.0 / prompt_side, 1) if prompt_side else None,
        },
        "heaviest": [
            {key: item[key] for key in ("id", "profile", "title", "source", "model", "calls", "system_tokens", "tool_tokens")}
            | {"resent_tokens": min(item["prompt_side_tokens"], (item["system_tokens"] + item["tool_tokens"]) * item["calls"])}
            for item in heaviest
        ],
        "latest": breakdown,
        "auxiliary": sorted(
            ({**item, "cost_usd": round(item["cost_usd"], 4)} for item in auxiliary.values()),
            key=lambda item: -item["cost_usd"],
        ),
        "available": bool(sessions),
        "generated_at": time.time(),
        "definition": (
            "Every API call re-sends the system prompt and the tool schemas. Sizes are characters / 4 from the prompts "
            "Hermes stored; 're-sent' is prompt size times API calls, capped at the session's recorded prompt-side "
            "tokens (uncached input + cache reads + cache writes). Most re-sent tokens are cache reads, billed at the "
            "provider's cache rate. A skill counts as rarely used when skills/.usage.json shows no use in "
            f"{_SKILL_STALE_DAYS} days and it is not pinned."
        ),
    }


# ── Prompt cache health ───────────────────────────────────────────────────


def _cache_routes_file(home: Path) -> Dict[str, Any]:
    path = home / "cache" / "prompt_cache_routes.json"
    try:
        if path.stat().st_size > 4 * 1024 * 1024:
            return {}
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _prompt_cache_sync(days: int = 7, start_at: Optional[float] = None, end_at: Optional[float] = None) -> Dict[str, Any]:
    start, end = _period_bounds(days, start_at, end_at)
    now = time.time()
    clocks: List[Dict[str, Any]] = []
    models: Dict[str, Dict[str, Any]] = {}
    keepalive = {"calls": 0, "cost_usd": 0.0, "tokens": 0}
    routes: List[Dict[str, Any]] = []
    for profile, home, path in _scope_profiles():
        _check_route_budget()
        for key, value in _cache_routes_file(home).items():
            if not isinstance(value, dict):
                continue
            gaps = [float(gap) for gap in value.get("hit_gaps") or [] if isinstance(gap, (int, float))]
            parts = str(key).split("|")
            routes.append({
                "profile": profile, "provider": parts[0] if parts else key, "model": parts[2] if len(parts) > 2 else None,
                "samples": _integer(value.get("samples") or value.get("reads")),
                "median_hit_gap_seconds": _round(statistics.median(gaps), 1) if gaps else None,
                "longest_hit_seconds": _round(_number(value.get("longest_hit"), None), 1),
                "updated_at": value.get("updated"),
            })
        try:
            connection = _ro_connect(path)
        except sqlite3.Error:
            continue
        try:
            if "prompt_cache_clock" in _table_columns(connection, "sessions"):
                for row in connection.execute(
                    "SELECT id, title, prompt_cache_clock AS clock, COALESCE(last_activity_at, started_at) AS active_at "
                    "FROM sessions WHERE prompt_cache_clock IS NOT NULL AND COALESCE(last_activity_at, started_at) >= ? "
                    "ORDER BY active_at DESC LIMIT 200",
                    (max(start, now - 7 * 86400),),
                ):
                    try:
                        clock = json.loads(row["clock"])
                    except (TypeError, ValueError):
                        continue
                    if not isinstance(clock, dict):
                        continue
                    route = clock.get("route") if isinstance(clock.get("route"), list) else []
                    until = _number(clock.get("guaranteed_until"), None) or _number(clock.get("estimated_until"), None)
                    clocks.append({
                        "session_id": row["id"], "profile": profile, "title": _clean_text(row["title"], 120) or None,
                        "provider": route[0] if route else None, "model": route[2] if len(route) > 2 else None,
                        "tier": _clean_text(clock.get("tier"), 20) or None,
                        "confidence": _clean_text(clock.get("confidence"), 30) or None,
                        "read_tokens": _integer(clock.get("read_tokens")), "write_tokens": _integer(clock.get("write_tokens")),
                        "sent_at": _number(clock.get("sent_at"), None), "warm_until": until,
                        "warm": bool(until and until > now),
                    })
            if _table_columns(connection, "session_model_usage") >= {"task", "first_seen"}:
                end_clause = " AND COALESCE(last_seen, first_seen, 0) < ?" if end is not None else ""
                params: List[Any] = [start] + ([end] if end is not None else [])
                for row in connection.execute(
                    f"""
                    SELECT model, billing_provider AS provider, COALESCE(task, '') AS task,
                           SUM(COALESCE(input_tokens, 0)) AS fresh, SUM(COALESCE(cache_read_tokens, 0)) AS reads,
                           SUM(COALESCE(cache_write_tokens, 0)) AS writes, SUM(COALESCE(api_call_count, 0)) AS calls,
                           SUM(CASE WHEN actual_cost_usd > 0 THEN actual_cost_usd ELSE COALESCE(estimated_cost_usd, 0) END) AS cost
                    FROM session_model_usage
                    WHERE COALESCE(last_seen, first_seen, 0) >= ?{end_clause}
                    GROUP BY model, billing_provider, COALESCE(task, '')
                    """,
                    params,
                ):
                    if row["task"] == "cache_warm":
                        keepalive["calls"] += _integer(row["calls"]) or 1
                        keepalive["cost_usd"] += _number(row["cost"], 0.0)
                        keepalive["tokens"] += _integer(row["reads"]) + _integer(row["writes"]) + _integer(row["fresh"])
                        continue
                    key = f"{row['provider'] or ''}|{row['model'] or ''}"
                    item = models.setdefault(key, {"model": row["model"], "provider": row["provider"], "fresh": 0, "reads": 0, "writes": 0, "calls": 0})
                    item["fresh"] += _integer(row["fresh"])
                    item["reads"] += _integer(row["reads"])
                    item["writes"] += _integer(row["writes"])
                    item["calls"] += _integer(row["calls"])
        finally:
            connection.close()
    model_rows = []
    for item in models.values():
        total = item["fresh"] + item["reads"] + item["writes"]
        if total <= 0:
            continue
        model_rows.append({**item, "prompt_tokens": total, "hit_ratio": round(item["reads"] / total, 4),
                           "write_ratio": round(item["writes"] / total, 4)})
    model_rows.sort(key=lambda item: -item["prompt_tokens"])
    clocks.sort(key=lambda item: (not item["warm"], -(item["sent_at"] or 0)))
    return {
        "summary": {
            "warm_sessions": sum(1 for item in clocks if item["warm"]),
            "tracked_sessions": len(clocks),
            "keepalive_calls": keepalive["calls"],
            "keepalive_cost_usd": round(keepalive["cost_usd"], 4),
            "keepalive_tokens": keepalive["tokens"],
            "hit_ratio": round(sum(item["reads"] for item in model_rows) / max(1, sum(item["prompt_tokens"] for item in model_rows)), 4) if model_rows else None,
        },
        "sessions": clocks[:15],
        "models": model_rows[:15],
        "routes": sorted(routes, key=lambda item: -(item["samples"] or 0))[:15],
        "available": bool(clocks or model_rows or routes),
        "generated_at": now,
        "definition": (
            "Warm means Hermes' own cache clock for the session (sessions.prompt_cache_clock) says the provider still "
            "holds the cached prefix. Hit ratio is cache-read tokens over all prompt-side tokens Hermes recorded. Hit "
            "gaps are Hermes' learned intervals between cache hits per route (cache/prompt_cache_routes.json); "
            "keep-alive is Hermes' cache_warm calls."
        ),
    }


# ── Cron run history ──────────────────────────────────────────────────────


def _cron_job_names(home: Path) -> Dict[str, str]:
    path = home / "cron" / "jobs.json"
    try:
        document = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return {}
    jobs = document.get("jobs") if isinstance(document, dict) else document
    names: Dict[str, str] = {}
    for job in jobs if isinstance(jobs, list) else []:
        if isinstance(job, dict) and job.get("id"):
            names[str(job["id"])] = _clean_text(job.get("name"), 160) or str(job["id"])
    return names


def _cron_audit(home: Path, start: float, end: Optional[float]) -> Dict[str, Dict[str, Any]]:
    path = home / "cron" / "usage_audit.jsonl"
    totals: Dict[str, Dict[str, Any]] = {}
    try:
        if path.stat().st_size > 32 * 1024 * 1024:
            return totals
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return totals
    for line in lines:
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if not isinstance(record, dict) or not _in_period(_iso_epoch(record.get("ts")), start, end):
            continue
        job = str(record.get("job_id") or "")
        item = totals.setdefault(job, {"fires": 0, "tokens": 0, "duration_ms": [], "errors": 0, "models": Counter()})
        item["fires"] += 1
        item["tokens"] += _integer(record.get("total_tokens"))
        if record.get("duration_ms") is not None:
            item["duration_ms"].append(_number(record.get("duration_ms"), 0.0))
        if record.get("error"):
            item["errors"] += 1
        if record.get("model"):
            item["models"][str(record["model"])] += 1
    return totals


def _cron_history_sync(days: int = 30, start_at: Optional[float] = None, end_at: Optional[float] = None) -> Dict[str, Any]:
    start, end = _period_bounds(days, start_at, end_at)
    jobs: Dict[Tuple[str, str], Dict[str, Any]] = {}
    recent: List[Dict[str, Any]] = []
    incidents: List[Dict[str, Any]] = []
    statuses: Counter = Counter()
    found = False
    for profile, home, _path in _scope_profiles():
        _check_route_budget()
        names = _cron_job_names(home)
        audit = _cron_audit(home, start, end)
        db_path = home / "cron" / "executions.db"
        if not db_path.exists():
            continue
        found = True
        try:
            connection = _ro_connect(db_path)
        except sqlite3.Error:
            continue
        try:
            columns = _table_columns(connection, "executions")
            if {"job_id", "status"} <= columns:
                order = "COALESCE(started_at, claimed_at, scheduled_instant)"
                for row in connection.execute(
                    f"SELECT job_id, status, claimed_at, started_at, finished_at, error, delivery_outcome, scheduled_instant "
                    f"FROM executions ORDER BY {order} DESC LIMIT 5000"
                ):
                    at = _iso_epoch(row["started_at"]) or _iso_epoch(row["claimed_at"]) or _iso_epoch(row["scheduled_instant"])
                    if not _in_period(at, start, end):
                        continue
                    status = str(row["status"] or "unknown").lower()
                    statuses[status] += 1
                    began, finished = _iso_epoch(row["started_at"]), _iso_epoch(row["finished_at"])
                    duration = finished - began if began and finished and finished >= began else None
                    job_id = str(row["job_id"] or "")
                    key = (profile, job_id)
                    item = jobs.setdefault(key, {"profile": profile, "job_id": job_id, "name": names.get(job_id, job_id),
                                                 "runs": 0, "statuses": Counter(), "durations": [], "deliveries": Counter(),
                                                 "last_at": None, "last_status": None, "last_error": None})
                    item["runs"] += 1
                    item["statuses"][status] += 1
                    if duration is not None:
                        item["durations"].append(duration)
                    if row["delivery_outcome"]:
                        item["deliveries"][str(row["delivery_outcome"])] += 1
                    if item["last_at"] is None or at > item["last_at"]:
                        item["last_at"], item["last_status"] = at, status
                        item["last_error"] = _clean_text(row["error"], 200) or None
                    if len(recent) < 400:
                        recent.append({"profile": profile, "job_id": job_id, "name": names.get(job_id, job_id), "status": status,
                                       "at": at, "duration_seconds": _round(duration, 1),
                                       "delivery": _clean_text(row["delivery_outcome"], 40) or None,
                                       "error": _clean_text(row["error"], 200) or None})
            if {"job_id", "state"} <= _table_columns(connection, "cron_incidents"):
                for row in connection.execute(
                    "SELECT job_id, state, failure_type, first_seen_at, last_seen_at, closed_at, error FROM cron_incidents "
                    "ORDER BY last_seen_at DESC LIMIT 200"
                ):
                    last = _iso_epoch(row["last_seen_at"])
                    open_ = str(row["state"] or "").lower() not in {"resolved", "closed"}
                    if not open_ and not _in_period(last, start, end):
                        continue
                    incidents.append({"profile": profile, "job_id": row["job_id"], "name": names.get(str(row["job_id"]), row["job_id"]),
                                      "state": row["state"], "failure_type": row["failure_type"],
                                      "first_seen_at": _iso_epoch(row["first_seen_at"]), "last_seen_at": last,
                                      "closed_at": _iso_epoch(row["closed_at"]), "open": open_,
                                      "error": _clean_text(row["error"], 200) or None})
        finally:
            connection.close()
        for job_id, totals in audit.items():
            key = (profile, job_id)
            item = jobs.setdefault(key, {"profile": profile, "job_id": job_id, "name": names.get(job_id, job_id),
                                         "runs": 0, "statuses": Counter(), "durations": [], "deliveries": Counter(),
                                         "last_at": None, "last_status": None, "last_error": None})
            item["audit"] = totals
    rows = []
    for item in jobs.values():
        audit = item.pop("audit", None) or {}
        durations = item.pop("durations")
        status_counts = item.pop("statuses")
        deliveries = item.pop("deliveries")
        bad = sum(count for status, count in status_counts.items() if status not in {"completed", "running", "claimed", "succeeded", "success", "ok"})
        rows.append({
            **item,
            "statuses": dict(status_counts),
            "problems": bad,
            "duration_p50_seconds": _round(_percentile(durations, 0.5), 1),
            "duration_max_seconds": _round(max(durations), 1) if durations else None,
            "deliveries": dict(deliveries),
            "fires_audited": audit.get("fires", 0),
            "tokens": audit.get("tokens", 0),
            "avg_tokens_per_fire": int(audit["tokens"] / audit["fires"]) if audit.get("fires") else None,
            "model": audit["models"].most_common(1)[0][0] if audit.get("models") else None,
        })
    rows.sort(key=lambda item: (-item["problems"], -item["runs"], str(item["name"])))
    recent.sort(key=lambda item: -(item["at"] or 0))
    return {
        "summary": {
            "runs": sum(statuses.values()),
            "statuses": dict(statuses),
            "jobs": len(rows),
            "open_incidents": sum(1 for item in incidents if item["open"]),
            "tokens": sum(item["tokens"] for item in rows),
        },
        "jobs": rows[:40],
        "recent": recent[:_CRON_RECENT],
        "incidents": incidents[:20],
        "available": found,
        "generated_at": time.time(),
        "definition": (
            "Every scheduled fire Hermes recorded in cron/executions.db, including missed and cancelled ones, with "
            "incidents Hermes opened for repeated failures. Tokens per fire come from cron/usage_audit.jsonl."
        ),
    }


# ── Gateway restarts ──────────────────────────────────────────────────────

_GATEWAY_ALIVE_SECONDS = 300


def _gateway_history_sync(days: int = 30, start_at: Optional[float] = None, end_at: Optional[float] = None) -> Dict[str, Any]:
    start, end = _period_bounds(days, start_at, end_at)
    now = time.time()
    runs: List[Dict[str, Any]] = []
    found = False
    for profile, _home, path in _scope_profiles():
        _check_route_budget()
        try:
            connection = _ro_connect(path)
        except sqlite3.Error:
            continue
        try:
            if not {"started_at", "last_heartbeat"} <= _table_columns(connection, "gateway_heartbeats"):
                continue
            found = True
            for row in connection.execute(
                "SELECT backend_id, pid, started_at, last_heartbeat, profile FROM gateway_heartbeats ORDER BY started_at"
            ):
                began, last = _number(row["started_at"], None), _number(row["last_heartbeat"], None)
                if began is None:
                    continue
                runs.append({"profile": str(row["profile"] or profile), "db_profile": profile, "pid": _integer(row["pid"]) or None,
                             "started_at": began, "last_heartbeat": last})
        finally:
            connection.close()
    runs.sort(key=lambda item: (item["profile"], item["started_at"]))
    by_profile: Dict[str, List[Dict[str, Any]]] = {}
    for run in runs:
        by_profile.setdefault(run["profile"], []).append(run)
    timeline: List[Dict[str, Any]] = []
    per_day: Counter = Counter()
    profiles = []
    for name, items in by_profile.items():
        downtimes = []
        for index, run in enumerate(items):
            following = items[index + 1] if index + 1 < len(items) else None
            run["uptime_seconds"] = _round((run["last_heartbeat"] or run["started_at"]) - run["started_at"], 0)
            run["alive"] = following is None and bool(run["last_heartbeat"] and now - run["last_heartbeat"] < _GATEWAY_ALIVE_SECONDS)
            gap = following["started_at"] - run["last_heartbeat"] if following and run["last_heartbeat"] else None
            run["gap_after_seconds"] = _round(gap, 0) if gap is not None and gap > 0 else None
            if _in_period(run["started_at"], start, end):
                per_day[dt.datetime.fromtimestamp(run["started_at"]).strftime("%Y-%m-%d")] += 1
                timeline.append(run)
                if run["gap_after_seconds"]:
                    downtimes.append(run["gap_after_seconds"])
        in_period = [run for run in items if _in_period(run["started_at"], start, end)]
        current = items[-1] if items else None
        profiles.append({
            "profile": name,
            "starts": len(in_period),
            "starts_all_time": len(items),
            "first_seen": items[0]["started_at"] if items else None,
            "running": bool(current and current.get("alive")),
            "current_uptime_seconds": current["uptime_seconds"] if current and current.get("alive") else None,
            "median_uptime_seconds": _round(statistics.median([run["uptime_seconds"] for run in in_period]), 0) if in_period else None,
            "downtime_seconds": _round(sum(downtimes), 0),
            "longest_gap_seconds": _round(max(downtimes), 0) if downtimes else None,
        })
    profiles.sort(key=lambda item: -item["starts"])
    timeline.sort(key=lambda item: -item["started_at"])
    return {
        "profiles": profiles,
        "timeline": timeline[:40],
        "starts_per_day": [{"day": day, "starts": count} for day, count in sorted(per_day.items())][-45:],
        "available": found and bool(runs),
        "generated_at": now,
        "definition": (
            "Each row of Hermes' gateway_heartbeats table is one gateway run: when it started and its last heartbeat. "
            "The gap after a run is the time until the next start, when no gateway was running."
        ),
    }


__all__ = [name for name in globals() if not name.startswith("__")]
