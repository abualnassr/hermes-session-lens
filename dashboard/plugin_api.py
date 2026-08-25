"""Read-only analytics API for Hermes Session Lens.

Mounted by Hermes at ``/api/plugins/session-lens``. Every database handle is
opened through Hermes' own ``SessionDB(read_only=True)`` contract; this module
contains no mutation endpoint and never opens a writable SQLite connection.
"""

from __future__ import annotations

import asyncio
import copy
import datetime as dt
import hashlib
import json
import math
import os
import re
import sqlite3
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional, Tuple
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Query

try:
    from hermes_constants import get_hermes_home
    from hermes_state import SessionDB
except ImportError:  # pragma: no cover - makes isolated editor/test imports clear.
    get_hermes_home = None  # type: ignore[assignment]
    SessionDB = None  # type: ignore[assignment,misc]


router = APIRouter()

PLUGIN_VERSION = "0.5.0"
MAX_SESSION_PAGE = 500
MAX_ANALYSIS_EVENTS = 5000
MAX_SEARCH_MATCHES = 2000
MAX_SNIPPET_CHARS = 560
MAX_TRACE_PAGE = 200
MAX_TRACE_CONTENT_CHARS = 6000
MAX_LOG_FILES = 5
MAX_LOG_FILE_BYTES = 6 * 1024 * 1024
AI_USAGE_CACHE_TTL_SECONDS = 300
AI_USAGE_PROVIDER_TIMEOUT_SECONDS = 12

_ERROR_FINISH_REASONS = {"error", "agent_error", "content_filter"}
_ERROR_EFFECTS = {"blocked", "denied", "error", "failed", "failure"}
_FAILURE_RE = re.compile(
    r"(?im)(?:^|[\r\n])\s*(?:error|failed|failure|fatal|traceback|exception)\b"
    r"|\bpermission denied\b|\baccess is denied\b|\btimed? out\b"
    r"|\beconnrefused\b|\beaddrinuse\b|\bcommand not found\b"
    r"|\bno such file or directory\b|\bprocess exited with (?:code\s*)?[1-9]\d*\b"
    r"|\bexit[_ ]code[\"']?\s*[:=]\s*[1-9]\d*\b"
    r"|\"(?:error|errors)\"\s*:\s*(?!null\b|false\b|0\b|\"\")",
)
_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_SECRET_RE = re.compile(
    r"(?i)\b(api[_-]?key|authorization|access[_-]?token|refresh[_-]?token|secret|password)"
    r"\b(\s*[=:]\s*)([^\s,;]+)"
)
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/=-]{8,}")
_WINDOWS_PATH_RE = re.compile(r"(?<![\w])([A-Za-z]:\\[^\r\n\t\"'<>|]{2,260})")
_UNIX_PATH_RE = re.compile(
    r"(?<![\w:])((?:/|\./|\.\./|~/)[^\s\"'<>|]{2,260})"
)
_PATH_ARGUMENT_KEYS = {
    "cwd",
    "destination",
    "directory",
    "file",
    "file_path",
    "filename",
    "output",
    "output_path",
    "path",
    "target",
    "workdir",
}
_FILE_TOOL_HINTS = {
    "apply_patch",
    "edit_file",
    "read_file",
    "replace_in_file",
    "view_image",
    "write_file",
}

_FAILED_END_REASONS = {
    "agent_error",
    "content_filter",
    "error",
    "failed",
    "failure",
    "max_runtime",
    "timeout",
}
_CANCELLED_END_REASONS = {"cancelled", "canceled", "interrupted", "user_cancelled"}
_COMPLETED_END_REASONS = {
    "agent_close",
    "cli_close",
    "complete",
    "completed",
    "cron_complete",
    "user_exit",
    "webhook_complete",
}

_LOG_LINE_RE = re.compile(
    r"^(?P<stamp>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})\s+\w+"
    r"(?: \[(?P<session>[^\]]+)\])?\s+(?P<logger>[\w.]+):\s+(?P<message>.*)$"
)
_API_METRIC_RE = re.compile(
    r"API call #(?P<call>\d+): model=(?P<model>\S+) provider=(?P<provider>\S+) "
    r"in=(?P<input>\d+) out=(?P<output>\d+) total=(?P<total>\d+) "
    r"latency=(?P<latency>[\d.]+)s"
    r"(?: cache=(?P<cache_read>\d+)/(?P<prompt>\d+) \((?P<cache_pct>\d+)%\))?"
)
_TOOL_METRIC_RE = re.compile(
    r"tool (?P<tool>[\w.:-]+) (?P<status>completed|failed|cancelled) "
    r"\((?P<duration>[\d.]+)s(?:, (?P<chars>\d+) chars)?\)"
)

_log_file_cache: Dict[str, Tuple[Tuple[int, int], Dict[str, Any]]] = {}
_ai_usage_cache_lock = threading.Lock()
_ai_usage_cache: Optional[Tuple[float, Dict[str, Any]]] = None
_ai_usage_last_success: Dict[str, Dict[str, Any]] = {}


def _hermes_home() -> Path:
    if get_hermes_home is not None:
        return Path(get_hermes_home())
    configured = os.environ.get("HERMES_HOME")
    if configured:
        return Path(configured)
    return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "hermes"


@contextmanager
def _database(db_path: Optional[Path] = None) -> Iterator[Any]:
    if SessionDB is None:
        raise RuntimeError("Hermes SessionDB is unavailable in this process")
    db = SessionDB(db_path=db_path, read_only=True) if db_path else SessionDB(read_only=True)
    try:
        yield db
    finally:
        db.close()


def _row_dict(row: Any) -> Dict[str, Any]:
    return dict(row) if row is not None else {}


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value if value is not None else default)
    except (TypeError, ValueError):
        return default


def _integer(value: Any, default: int = 0) -> int:
    try:
        return int(value if value is not None else default)
    except (TypeError, ValueError):
        return default


def _period_bounds(
    days: int,
    start_at: Optional[float] = None,
    end_at: Optional[float] = None,
) -> Tuple[float, Optional[float]]:
    if start_at is None and end_at is None:
        return (time.time() - days * 86400 if days > 0 else 0), None
    start = max(0, _number(start_at, 0))
    end = _number(end_at) if end_at is not None else None
    if end is not None and end <= start:
        raise HTTPException(status_code=422, detail="end_at must be later than start_at")
    return start, end


def _period_sql(
    column: str,
    start_at: float,
    end_at: Optional[float],
) -> Tuple[str, List[float]]:
    clauses = [f"{column} >= ?"]
    params = [start_at]
    if end_at is not None:
        clauses.append(f"{column} < ?")
        params.append(end_at)
    return " AND ".join(clauses), params


def _period_payload(
    days: int,
    start_at: float,
    end_at: Optional[float],
) -> Dict[str, Any]:
    return {
        "days": days,
        "start_at": start_at or None,
        "end_at": end_at,
        "custom": end_at is not None,
    }


def _clean_text(value: Any, limit: int = MAX_SNIPPET_CHARS) -> str:
    text = "" if value is None else str(value)
    text = _ANSI_RE.sub("", text)
    text = _CONTROL_RE.sub(" ", text)
    text = _SECRET_RE.sub(lambda match: f"{match.group(1)}{match.group(2)}[redacted]", text)
    text = _BEARER_RE.sub("Bearer [redacted]", text)
    text = re.sub(r"[ \t]+", " ", text).strip()
    if len(text) > limit:
        return text[: max(0, limit - 1)].rstrip() + "…"
    return text


def _clean_content(value: Any, limit: int = MAX_TRACE_CONTENT_CHARS) -> str:
    text = "" if value is None else str(value)
    text = _ANSI_RE.sub("", text)
    text = _CONTROL_RE.sub(" ", text)
    text = _SECRET_RE.sub(lambda match: f"{match.group(1)}{match.group(2)}[redacted]", text)
    text = _BEARER_RE.sub("Bearer [redacted]", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{4,}", "\n\n\n", text).strip()
    if len(text) > limit:
        return text[: max(0, limit - 1)].rstrip() + "…"
    return text


def _session_outcome(row: Mapping[str, Any]) -> Dict[str, str]:
    if row.get("ended_at") is None:
        last_activity = _number(row.get("last_activity_at") or row.get("started_at"), 0)
        if last_activity and time.time() - last_activity < 300:
            return {"outcome": "running", "outcome_label": "Running"}
        return {"outcome": "open", "outcome_label": "Open"}
    reason = str(row.get("end_reason") or "").strip().lower()
    if reason in _FAILED_END_REASONS or any(part in reason for part in ("error", "fail", "timeout")):
        return {"outcome": "failed", "outcome_label": "Failed"}
    if reason in _CANCELLED_END_REASONS or "cancel" in reason or "interrupt" in reason:
        return {"outcome": "cancelled", "outcome_label": "Cancelled"}
    if reason in _COMPLETED_END_REASONS or "complete" in reason:
        return {"outcome": "completed", "outcome_label": "Completed"}
    return {"outcome": "closed", "outcome_label": "Closed"}


def _cost_view(row: Mapping[str, Any]) -> Dict[str, Any]:
    actual_raw = row.get("actual_cost_usd")
    estimated_raw = row.get("estimated_cost_usd")
    actual = _number(actual_raw)
    estimated = _number(estimated_raw)
    status = str(row.get("cost_status") or "").strip().lower()
    source = str(row.get("cost_source") or "").strip() or None
    billing_mode = str(row.get("billing_mode") or "").strip().lower()

    if actual_raw is not None and (
        actual > 0
        or status in {"actual", "billed", "provider", "reported"}
        or source in {"provider", "api"}
    ):
        return {
            "display_cost_usd": actual,
            "cost_kind": "actual",
            "cost_status": status or "actual",
            "cost_source": source,
        }
    if status in {"included", "subscription", "free"} or billing_mode in {
        "included",
        "subscription",
        "free",
    }:
        return {
            "display_cost_usd": 0.0,
            "cost_kind": "included",
            "cost_status": status or "included",
            "cost_source": source,
        }
    if estimated_raw is not None and estimated > 0:
        return {
            "display_cost_usd": estimated,
            "cost_kind": "estimated",
            "cost_status": status or "estimated",
            "cost_source": source,
        }
    return {
        "display_cost_usd": None,
        "cost_kind": "unpriced",
        "cost_status": status or "unknown",
        "cost_source": source,
    }


def _session_payload(row: Mapping[str, Any]) -> Dict[str, Any]:
    payload = dict(row)
    payload.pop("total_count", None)
    input_tokens = _integer(row.get("input_tokens"))
    output_tokens = _integer(row.get("output_tokens"))
    cache_read = _integer(row.get("cache_read_tokens"))
    cache_write = _integer(row.get("cache_write_tokens"))
    started_at = _number(row.get("started_at"), 0)
    ended_at = row.get("ended_at")
    last_activity = _number(row.get("last_activity_at"), started_at)
    now = time.time()

    payload.update(
        {
            "title": row.get("title") or row.get("display_name") or "Untitled session",
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_read_tokens": cache_read,
            "cache_write_tokens": cache_write,
            "reasoning_tokens": _integer(row.get("reasoning_tokens")),
            "total_tokens": input_tokens + output_tokens + cache_read + cache_write,
            "message_count": _integer(row.get("message_count")),
            "tool_call_count": _integer(row.get("tool_call_count")),
            "api_call_count": _integer(row.get("api_call_count")),
            "failure_count": _integer(row.get("failure_count")),
            "archived": bool(row.get("archived")),
            "pinned": bool(row.get("pinned")),
            "is_active": ended_at is None and bool(last_activity) and now - last_activity < 300,
            "duration_seconds": (
                max(0.0, _number(ended_at) - started_at)
                if started_at and ended_at is not None
                else (max(0.0, now - started_at) if started_at else None)
            ),
        }
    )
    payload.update(_session_outcome(row))
    payload.update(_cost_view(row))
    return payload


def _is_failure(
    *,
    role: Any = None,
    content: Any = None,
    finish_reason: Any = None,
    effect_disposition: Any = None,
) -> bool:
    finish = str(finish_reason or "").strip().lower()
    effect = str(effect_disposition or "").strip().lower()
    if finish in _ERROR_FINISH_REASONS or effect in _ERROR_EFFECTS:
        return True
    if str(role or "").strip().lower() != "tool":
        return False
    text = str(content or "")[:12000]
    return bool(text and _FAILURE_RE.search(text))


def _parse_json(value: Any, fallback: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if not isinstance(value, str) or not value.strip():
        return fallback
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError, ValueError):
        return fallback


def _iter_tool_calls(value: Any) -> Iterable[Dict[str, Any]]:
    calls = _parse_json(value, [])
    if not isinstance(calls, list):
        return []
    parsed: List[Dict[str, Any]] = []
    for call in calls:
        if not isinstance(call, dict):
            continue
        function = call.get("function") if isinstance(call.get("function"), dict) else {}
        name = function.get("name") or call.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        arguments = _parse_json(function.get("arguments"), {})
        if not isinstance(arguments, dict):
            arguments = {}
        parsed.append(
            {
                "call_id": call.get("id") or call.get("call_id") or call.get("response_item_id"),
                "name": name.strip(),
                "arguments": arguments,
            }
        )
    return parsed


def _argument_summary(arguments: Mapping[str, Any]) -> str:
    if not arguments:
        return "No recorded arguments"
    priority = [
        "path",
        "file_path",
        "workdir",
        "cwd",
        "command",
        "query",
        "name",
        "action",
        "url",
    ]
    keys = [key for key in priority if key in arguments]
    keys.extend(key for key in sorted(arguments) if key not in keys)
    parts: List[str] = []
    for key in keys[:5]:
        value = arguments.get(key)
        if isinstance(value, (dict, list)):
            try:
                value = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            except (TypeError, ValueError):
                value = type(value).__name__
        parts.append(f"{key}: {_clean_text(value, 150)}")
    return " · ".join(parts)


def _normalise_file_path(value: Any) -> Optional[str]:
    text = _clean_text(value, 320).strip(" \t\r\n\"'`()[]{}.,;")
    if not text or text.startswith(("http://", "https://", "data:")):
        return None
    if len(text) < 2 or text in {"/", "./", "../", "~"}:
        return None
    return text


def _files_from_call(tool_name: str, arguments: Mapping[str, Any]) -> List[Dict[str, str]]:
    files: List[Dict[str, str]] = []
    lower_tool = tool_name.lower()
    if any(token in lower_tool for token in ("write", "edit", "patch", "replace")):
        action = "modified"
    elif any(token in lower_tool for token in ("read", "view", "search", "find")):
        action = "read"
    else:
        action = "referenced"

    for key, value in arguments.items():
        if key.lower() not in _PATH_ARGUMENT_KEYS:
            continue
        values = value if isinstance(value, list) else [value]
        for item in values:
            path = _normalise_file_path(item)
            if path:
                files.append({"path": path, "action": action, "tool": tool_name})

    command = arguments.get("command") or arguments.get("code")
    if isinstance(command, str):
        for pattern in (_WINDOWS_PATH_RE, _UNIX_PATH_RE):
            for match in pattern.finditer(command[:12000]):
                path = _normalise_file_path(match.group(1))
                if path:
                    files.append({"path": path, "action": "referenced", "tool": tool_name})

    if lower_tool in _FILE_TOOL_HINTS and not files:
        candidate = arguments.get("name")
        path = _normalise_file_path(candidate)
        if path and ("/" in path or "\\" in path or "." in Path(path).name):
            files.append({"path": path, "action": action, "tool": tool_name})
    return files


def _analyze_events(rows: Iterable[Mapping[str, Any]]) -> Dict[str, Any]:
    material = [dict(row) for row in rows]
    results_by_call: Dict[str, Dict[str, Any]] = {}
    unbound_results: List[Dict[str, Any]] = []

    for row in material:
        if str(row.get("role") or "").lower() != "tool":
            continue
        result = {
            "id": row.get("id"),
            "session_id": row.get("session_id"),
            "tool_name": row.get("tool_name"),
            "timestamp": row.get("timestamp"),
            "content": row.get("content"),
            "finish_reason": row.get("finish_reason"),
            "effect_disposition": row.get("effect_disposition"),
        }
        call_id = row.get("tool_call_id")
        if call_id:
            results_by_call[str(call_id)] = result
        else:
            unbound_results.append(result)

    events: List[Dict[str, Any]] = []
    files: Dict[str, Dict[str, str]] = {}
    skills: Dict[str, Dict[str, Any]] = {}
    matched_result_ids = set()

    for row in material:
        if str(row.get("role") or "").lower() != "assistant" or not row.get("tool_calls"):
            continue
        for call in _iter_tool_calls(row.get("tool_calls")):
            call_id = str(call.get("call_id") or "")
            result = results_by_call.get(call_id) if call_id else None
            if result and result.get("id") is not None:
                matched_result_ids.add(result["id"])
            failure = (
                _is_failure(
                    role="tool",
                    content=result.get("content") if result else None,
                    finish_reason=result.get("finish_reason") if result else row.get("finish_reason"),
                    effect_disposition=result.get("effect_disposition") if result else None,
                )
                if result
                else False
            )
            event = {
                "call_id": call_id or None,
                "session_id": row.get("session_id"),
                "name": call["name"],
                "timestamp": row.get("timestamp"),
                "argument_summary": _argument_summary(call["arguments"]),
                "status": "failed" if failure else ("completed" if result else "unmatched"),
                "failure": failure,
                "result_snippet": _clean_text(result.get("content"), MAX_SNIPPET_CHARS) if result else None,
            }
            events.append(event)

            for file_info in _files_from_call(call["name"], call["arguments"]):
                current = files.get(file_info["path"])
                if current is None or current["action"] == "referenced":
                    files[file_info["path"]] = file_info

            if call["name"] in {"skill_view", "skill_manage"}:
                skill_name = call["arguments"].get("name")
                if isinstance(skill_name, str) and skill_name.strip():
                    key = skill_name.strip()
                    entry = skills.setdefault(
                        key,
                        {
                            "name": key,
                            "view_count": 0,
                            "manage_count": 0,
                            "last_used_at": None,
                            "evidence": "recorded invocation",
                        },
                    )
                    field = "view_count" if call["name"] == "skill_view" else "manage_count"
                    entry[field] += 1
                    timestamp = _number(row.get("timestamp"), 0)
                    if timestamp and (not entry["last_used_at"] or timestamp > entry["last_used_at"]):
                        entry["last_used_at"] = timestamp

    for result in list(results_by_call.values()) + unbound_results:
        if result.get("id") in matched_result_ids:
            continue
        name = result.get("tool_name")
        if not isinstance(name, str) or not name.strip():
            continue
        failure = _is_failure(
            role="tool",
            content=result.get("content"),
            finish_reason=result.get("finish_reason"),
            effect_disposition=result.get("effect_disposition"),
        )
        events.append(
            {
                "call_id": None,
                "session_id": result.get("session_id"),
                "name": name,
                "timestamp": result.get("timestamp"),
                "argument_summary": "Arguments not recorded on this surface",
                "status": "failed" if failure else "completed",
                "failure": failure,
                "result_snippet": _clean_text(result.get("content"), MAX_SNIPPET_CHARS),
            }
        )

    events.sort(key=lambda item: _number(item.get("timestamp"), 0), reverse=True)
    failures = [event for event in events if event["failure"]]
    file_list = sorted(files.values(), key=lambda item: (item["action"], item["path"].lower()))
    skill_list = sorted(
        skills.values(),
        key=lambda item: (item["view_count"] + item["manage_count"], item["name"]),
        reverse=True,
    )
    return {
        "events": events,
        "failures": failures,
        "files": file_list,
        "skills": skill_list,
    }


def _failure_sql(alias: str = "m") -> str:
    return f"""
        (
            lower(coalesce({alias}.finish_reason, '')) IN ('error', 'agent_error', 'content_filter')
            OR lower(coalesce({alias}.effect_disposition, '')) IN ('blocked', 'denied', 'error', 'failed', 'failure')
            OR (
                {alias}.role = 'tool'
                AND (
                    lower(substr(coalesce({alias}.content, ''), 1, 12000)) GLOB '*error*'
                    OR lower(substr(coalesce({alias}.content, ''), 1, 12000)) GLOB '*failed*'
                    OR lower(substr(coalesce({alias}.content, ''), 1, 12000)) GLOB '*traceback*'
                    OR lower(substr(coalesce({alias}.content, ''), 1, 12000)) GLOB '*permission denied*'
                    OR lower(substr(coalesce({alias}.content, ''), 1, 12000)) GLOB '*timed out*'
                    OR lower(substr(coalesce({alias}.content, ''), 1, 12000)) GLOB '*econnrefused*'
                    OR lower(substr(coalesce({alias}.content, ''), 1, 12000)) GLOB '*eaddrinuse*'
                )
            )
        )
    """


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
        if failures_only:
            where.append("coalesce(f.failure_count, 0) > 0")
        if query:
            like = f"%{query.strip().lower()}%"
            text_filters = [
                "lower(s.id) LIKE ?",
                "lower(coalesce(s.title, '')) LIKE ?",
                "lower(coalesce(s.model, '')) LIKE ?",
                "lower(coalesce(s.cwd, '')) LIKE ?",
                "lower(coalesce(s.source, '')) LIKE ?",
            ]
            params.extend([like] * len(text_filters))
            if snippets:
                placeholders = ",".join("?" for _ in snippets)
                text_filters.append(f"s.id IN ({placeholders})")
                params.extend(snippets.keys())
            where.append("(" + " OR ".join(text_filters) + ")")

        sort_sql = {
            "recent": "coalesce(s.last_activity_at, s.started_at) DESC",
            "cost": "coalesce(s.actual_cost_usd, s.estimated_cost_usd, -1) DESC, s.started_at DESC",
            "tokens": "(coalesce(s.input_tokens,0) + coalesce(s.output_tokens,0) + coalesce(s.cache_read_tokens,0) + coalesce(s.cache_write_tokens,0)) DESC, s.started_at DESC",
            "tools": "coalesce(s.tool_call_count,0) DESC, s.started_at DESC",
            "failures": "coalesce(f.failure_count,0) DESC, coalesce(s.last_activity_at,s.started_at) DESC",
        }.get(sort, "coalesce(f.failure_count,0) DESC, coalesce(s.last_activity_at,s.started_at) DESC")

        failure_sql = _failure_sql("m")
        cte = f"""
            WITH failure_counts AS (
                SELECT m.session_id, COUNT(*) AS failure_count
                FROM messages m
                WHERE coalesce(m.active, 1) = 1 AND {failure_sql}
                GROUP BY m.session_id
            )
        """
        from_where = f"""
            FROM sessions s
            LEFT JOIN failure_counts f ON f.session_id = s.id
            WHERE {' AND '.join(where)}
        """
        total = db._conn.execute(cte + " SELECT COUNT(*) " + from_where, tuple(params)).fetchone()[0]
        rows = db._conn.execute(
            cte
            + """
            SELECT s.id, s.source, s.display_name, s.model, s.started_at, s.ended_at,
                   s.end_reason, s.parent_session_id,
                   s.message_count, s.tool_call_count, s.input_tokens, s.output_tokens,
                   s.cache_read_tokens, s.cache_write_tokens, s.reasoning_tokens,
                   s.cwd, s.git_branch, s.git_repo_root, s.billing_provider,
                   s.billing_mode, s.estimated_cost_usd, s.actual_cost_usd,
                   s.cost_status, s.cost_source, s.title, s.last_activity_at,
                   s.last_activity_description, s.api_call_count, s.profile_name,
                   s.archived, s.pinned, coalesce(f.failure_count, 0) AS failure_count
            """
            + from_where
            + f" ORDER BY {sort_sql} LIMIT ? OFFSET ?",
            tuple(params + [limit, offset]),
        ).fetchall()
        sessions = []
        for row in rows:
            item = _session_payload(_row_dict(row))
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
            version_row = db._conn.execute("SELECT version FROM schema_version").fetchone()
            db._conn.execute("SELECT 1 FROM sessions LIMIT 1").fetchone()
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
        row = db._conn.execute(
            f"""
            SELECT s.*, (
                SELECT COUNT(*) FROM messages m
                WHERE m.session_id = s.id AND coalesce(m.active,1) = 1
                  AND {_failure_sql('m')}
            ) AS failure_count
            FROM sessions s WHERE s.id = ?
            """,
            (sid,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Session not found")

        usage_rows = [
            _row_dict(item)
            for item in db._conn.execute(
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

        event_rows = db._conn.execute(
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
            for role, count in db._conn.execute(
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
            for item in db._conn.execute(
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
            "session": _session_payload(_row_dict(row)),
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
        total = db._conn.execute(
            """
            SELECT COUNT(*) FROM messages
            WHERE session_id=? AND coalesce(active,1)=1 AND role!='system'
            """,
            (sid,),
        ).fetchone()[0]
        rows = db._conn.execute(
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


def _timestamp_from_log(value: str) -> float:
    try:
        return dt.datetime.strptime(value, "%Y-%m-%d %H:%M:%S,%f").astimezone().timestamp()
    except ValueError:
        return 0.0


def _parse_log_file(path: Path) -> Dict[str, Any]:
    stat = path.stat()
    signature = (stat.st_size, stat.st_mtime_ns)
    key = str(path)
    cached = _log_file_cache.get(key)
    if cached and cached[0] == signature:
        return cached[1]

    api_events: List[Dict[str, Any]] = []
    tool_events: List[Dict[str, Any]] = []
    with path.open("rb") as handle:
        if stat.st_size > MAX_LOG_FILE_BYTES:
            handle.seek(stat.st_size - MAX_LOG_FILE_BYTES)
            handle.readline()
        text = handle.read(MAX_LOG_FILE_BYTES).decode("utf-8", errors="replace")
    for line in text.splitlines():
        envelope = _LOG_LINE_RE.match(line)
        if not envelope:
            continue
        timestamp = _timestamp_from_log(envelope.group("stamp"))
        message = envelope.group("message")
        api_match = _API_METRIC_RE.search(message)
        if api_match:
            api_events.append(
                {
                    "timestamp": timestamp,
                    "session_id": envelope.group("session"),
                    "model": api_match.group("model"),
                    "provider": api_match.group("provider"),
                    "input_tokens": _integer(api_match.group("input")),
                    "output_tokens": _integer(api_match.group("output")),
                    "total_tokens": _integer(api_match.group("total")),
                    "latency_seconds": _number(api_match.group("latency")),
                    "cache_read_tokens": _integer(api_match.group("cache_read")),
                    "prompt_tokens": _integer(api_match.group("prompt")),
                }
            )
            continue
        tool_match = _TOOL_METRIC_RE.search(message)
        if tool_match:
            tool_events.append(
                {
                    "timestamp": timestamp,
                    "session_id": envelope.group("session"),
                    "tool": tool_match.group("tool"),
                    "status": tool_match.group("status"),
                    "duration_seconds": _number(tool_match.group("duration")),
                    "output_chars": _integer(tool_match.group("chars")),
                }
            )
    parsed = {"api": api_events, "tools": tool_events}
    _log_file_cache[key] = (signature, parsed)
    return parsed


def _percentile(values: Iterable[float], percentile: float) -> Optional[float]:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    index = max(0, min(len(ordered) - 1, round((len(ordered) - 1) * percentile)))
    return ordered[index]


def _runtime_events() -> Dict[str, Any]:
    log_dir = _hermes_home() / "logs"
    if not log_dir.exists():
        return {"api": [], "tools": [], "files": []}
    candidates = sorted(
        (path for path in log_dir.glob("agent.log*") if path.is_file()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )[:MAX_LOG_FILES]
    api_events: List[Dict[str, Any]] = []
    tool_events: List[Dict[str, Any]] = []
    files = []
    for path in reversed(candidates):
        try:
            parsed = _parse_log_file(path)
        except OSError:
            continue
        api_events.extend(parsed["api"])
        tool_events.extend(parsed["tools"])
        files.append({"name": path.name, "size_bytes": path.stat().st_size, "updated_at": path.stat().st_mtime})
    return {"api": api_events, "tools": tool_events, "files": files}


def _metric_groups(rows: List[Dict[str, Any]], key: str) -> List[Dict[str, Any]]:
    groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(row.get(key) or "unknown")].append(row)
    result = []
    for name, material in groups.items():
        latencies = [item["latency_seconds"] for item in material]
        prompt_tokens = sum(_integer(item.get("prompt_tokens")) for item in material)
        cache_read = sum(_integer(item.get("cache_read_tokens")) for item in material)
        result.append(
            {
                key: name,
                "api_calls": len(material),
                "input_tokens": sum(_integer(item.get("input_tokens")) for item in material),
                "output_tokens": sum(_integer(item.get("output_tokens")) for item in material),
                "latency_avg_seconds": sum(latencies) / len(latencies),
                "latency_p50_seconds": _percentile(latencies, 0.50),
                "latency_p95_seconds": _percentile(latencies, 0.95),
                "cache_read_tokens": cache_read,
                "prompt_tokens": prompt_tokens,
                "cache_hit_ratio": cache_read / prompt_tokens if prompt_tokens else None,
            }
        )
    result.sort(key=lambda item: item["api_calls"], reverse=True)
    return result


def _telemetry_sync(
    days: int,
    session_id: str = "",
    start_at: Optional[float] = None,
    end_at: Optional[float] = None,
) -> Dict[str, Any]:
    runtime = _runtime_events()
    period_start, period_end = _period_bounds(days, start_at, end_at)
    in_period = lambda row: row["timestamp"] >= period_start and (
        period_end is None or row["timestamp"] < period_end
    )
    api_rows = [
        row for row in runtime["api"]
        if in_period(row) and (not session_id or row.get("session_id") == session_id)
    ]
    tool_rows = [
        row for row in runtime["tools"]
        if in_period(row) and (not session_id or row.get("session_id") == session_id)
    ]
    latencies = [row["latency_seconds"] for row in api_rows]
    prompt_tokens = sum(_integer(row.get("prompt_tokens")) for row in api_rows)
    cache_read = sum(_integer(row.get("cache_read_tokens")) for row in api_rows)
    tool_groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in tool_rows:
        tool_groups[row["tool"]].append(row)
    tools = []
    for name, material in tool_groups.items():
        durations = [item["duration_seconds"] for item in material]
        statuses = Counter(item["status"] for item in material)
        tools.append(
            {
                "tool": name,
                "runs": len(material),
                "completed": statuses.get("completed", 0),
                "failed": statuses.get("failed", 0),
                "cancelled": statuses.get("cancelled", 0),
                "duration_avg_seconds": sum(durations) / len(durations),
                "duration_p95_seconds": _percentile(durations, 0.95),
            }
        )
    tools.sort(key=lambda item: (item["failed"], item["runs"]), reverse=True)
    return {
        "period_days": days,
        "period": _period_payload(days, period_start, period_end),
        "session_id": session_id or None,
        "summary": {
            "api_calls": len(api_rows),
            "tool_runs": len(tool_rows),
            "latency_avg_seconds": sum(latencies) / len(latencies) if latencies else None,
            "latency_p50_seconds": _percentile(latencies, 0.50),
            "latency_p95_seconds": _percentile(latencies, 0.95),
            "cache_read_tokens": cache_read,
            "prompt_tokens": prompt_tokens,
            "cache_hit_ratio": cache_read / prompt_tokens if prompt_tokens else None,
        },
        "models": _metric_groups(api_rows, "model"),
        "providers": _metric_groups(api_rows, "provider"),
        "tools": tools,
        "coverage": {
            "source": "local Hermes agent logs",
            "files": runtime["files"],
            "session_attribution": "only log lines carrying a Hermes session id",
        },
        "generated_at": time.time(),
    }


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
    failure_period_sql, failure_period_params = _period_sql(
        "sx.started_at", period_start, period_end
    )
    with _database() as db:
        totals_row = db._conn.execute(
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
                   coalesce(SUM(CASE WHEN coalesce(actual_cost_usd,0) <= 0 AND coalesce(estimated_cost_usd,0) <= 0 AND lower(coalesce(cost_status,'')) NOT IN ('included','subscription','free') THEN 1 ELSE 0 END),0) AS unpriced_sessions,
                   (SELECT COUNT(*) FROM messages m
                    JOIN sessions sx ON sx.id=m.session_id
                     WHERE {failure_period_sql} AND coalesce(sx.hidden,0)=0 AND {_failure_sql('m')}) AS failures
            FROM sessions
            WHERE {session_period_sql} AND coalesce(hidden,0)=0
            """,
            tuple(failure_period_params + session_period_params),
        ).fetchone()
        totals = _row_dict(totals_row)
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
            for row in db._conn.execute(
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
            for row in db._conn.execute(
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
            for row in db._conn.execute(
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
        for row in db._conn.execute(
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
        # Parsing only the compact JSON envelope is cheap; result bodies never
        # leave SQLite for this aggregate view.
        assistant_rows = db._conn.execute(
            f"""
            SELECT m.session_id, m.tool_calls, m.timestamp
            FROM messages m
            JOIN sessions s ON s.id=m.session_id
            WHERE {period_sql} AND coalesce(s.hidden,0)=0
              AND coalesce(m.active,1)=1 AND m.role='assistant'
              AND m.tool_calls IS NOT NULL
            """,
            tuple(period_params),
        ).fetchall()
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

        failure_sql = _failure_sql("m")
        result_rows = db._conn.execute(
            f"""
            SELECT m.tool_name AS name, COUNT(*) AS calls,
                   SUM(CASE WHEN {failure_sql} THEN 1 ELSE 0 END) AS failures,
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

        tools = []
        for name in set(assistant) | set(results):
            assistant_entry = assistant.get(name, {})
            result_entry = results.get(name, {})
            calls = max(
                _integer(assistant_entry.get("calls")),
                _integer(result_entry.get("calls")),
            )
            failures = _integer(result_entry.get("failures"))
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
            "truncated": False,
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
        rows = db._conn.execute(
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
            material = read(db._conn)
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


def _kanban_paths(home: Path) -> List[Tuple[str, Path]]:
    paths: List[Tuple[str, Path]] = []
    default = home / "kanban.db"
    if default.exists():
        paths.append(("default", default))
    boards_root = home / "kanban" / "boards"
    if boards_root.exists():
        paths.extend((path.stem, path) for path in sorted(boards_root.glob("*.db")) if path.is_file())
    return paths


def _kanban_board(name: str, path: Path) -> Dict[str, Any]:
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        table_names = {
            row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        status_counts = {}
        tasks = []
        runs = []
        if "tasks" in table_names:
            status_counts = {
                str(row["status"] or "unknown"): _integer(row["count"])
                for row in connection.execute("SELECT status, COUNT(*) AS count FROM tasks GROUP BY status").fetchall()
            }
            tasks = [
                {
                    "id": row["id"],
                    "title": _clean_text(row["title"], 220),
                    "assignee": _clean_text(row["assignee"], 120) or None,
                    "status": _clean_text(row["status"], 80),
                    "priority": _integer(row["priority"]),
                    "created_at": row["created_at"],
                    "started_at": row["started_at"],
                    "completed_at": row["completed_at"],
                    "consecutive_failures": _integer(row["consecutive_failures"]),
                    "last_failure_error": _clean_text(row["last_failure_error"], 320) or None,
                    "current_run_id": row["current_run_id"],
                    "session_id": row["session_id"],
                }
                for row in connection.execute(
                    """
                    SELECT id,title,assignee,status,priority,created_at,started_at,completed_at,
                           consecutive_failures,last_failure_error,current_run_id,session_id
                    FROM tasks ORDER BY coalesce(completed_at,started_at,created_at) DESC LIMIT 300
                    """
                ).fetchall()
            ]
        if "task_runs" in table_names:
            runs = [
                {
                    "id": row["id"],
                    "task_id": row["task_id"],
                    "profile": _clean_text(row["profile"], 120) or None,
                    "status": _clean_text(row["status"], 80),
                    "started_at": row["started_at"],
                    "ended_at": row["ended_at"],
                    "outcome": _clean_text(row["outcome"], 120) or None,
                    "summary": _clean_text(row["summary"], 320) or None,
                    "error": _clean_text(row["error"], 320) or None,
                }
                for row in connection.execute(
                    """
                    SELECT id,task_id,profile,status,started_at,ended_at,outcome,summary,error
                    FROM task_runs ORDER BY coalesce(ended_at,started_at) DESC LIMIT 300
                    """
                ).fetchall()
            ]
        return {
            "name": name,
            "status_counts": status_counts,
            "tasks": tasks,
            "runs": runs,
            "database_updated_at": path.stat().st_mtime,
        }
    finally:
        connection.close()


def _kanban_sync() -> Dict[str, Any]:
    boards = []
    errors = []
    for name, path in _kanban_paths(_hermes_home()):
        try:
            boards.append(_kanban_board(name, path))
        except (OSError, sqlite3.Error) as error:
            errors.append({"board": name, "error": _clean_text(error, 240)})
    return {
        "boards": boards,
        "totals": {
            "boards": len(boards),
            "tasks": sum(len(board["tasks"]) for board in boards),
            "runs": sum(len(board["runs"]) for board in boards),
        },
        "errors": errors,
        "generated_at": time.time(),
    }


@router.get("/kanban")
async def kanban() -> Dict[str, Any]:
    return await asyncio.to_thread(_kanban_sync)


_AI_USAGE_PROVIDER_META = {
    "codex": {"label": "OpenAI Codex", "auth_source": "Hermes OAuth", "experimental": False},
    "anthropic": {"label": "Anthropic Claude", "auth_source": "Hermes OAuth", "experimental": False},
    "nous": {"label": "Nous Research Portal", "auth_source": "Hermes OAuth", "experimental": False},
    "openrouter": {"label": "OpenRouter", "auth_source": "Hermes API key", "experimental": False},
    "deepseek": {"label": "DeepSeek", "auth_source": "Hermes API key", "experimental": False},
    "grok": {"label": "Grok", "auth_source": "Hermes xAI OAuth", "experimental": True},
    "kimi": {"label": "Kimi Code Plan", "auth_source": "Hermes API key", "experimental": True},
    "zai": {"label": "Z.AI GLM Coding Plan", "auth_source": "Hermes API key", "experimental": True},
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
        "experimental": meta["experimental"],
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


def _collect_codex_usage() -> Dict[str, Any]:
    try:
        from agent.account_usage import fetch_account_usage

        return _account_usage_payload("codex", fetch_account_usage("openai-codex"))
    except Exception as error:
        return _provider_payload("codex", status="unavailable", message=_provider_message(error))


def _collect_anthropic_usage() -> Dict[str, Any]:
    token = ""
    try:
        from agent.account_usage import fetch_account_usage
        from agent.anthropic_adapter import _is_oauth_token, resolve_anthropic_token

        token = str(resolve_anthropic_token() or "").strip()
        if not token:
            return _provider_payload(
                "anthropic",
                status="not_configured",
                message="No Hermes Anthropic OAuth login was found.",
            )
        if not _is_oauth_token(token):
            return _provider_payload(
                "anthropic",
                status="not_configured",
                message="Anthropic account limits require an OAuth-backed Claude account, not an API key.",
            )
        snapshot = fetch_account_usage("anthropic")
        if snapshot is None:
            return _provider_payload(
                "anthropic",
                status="unavailable",
                message="Anthropic did not return account-usage data for the current OAuth login.",
            )
        return _account_usage_payload("anthropic", snapshot)
    except Exception as error:
        return _provider_payload("anthropic", status="unavailable", message=_provider_message(error))
    finally:
        token = ""


def _resolve_hermes_api_key(provider_id: str) -> Tuple[str, str]:
    """Resolve a configured provider key without triggering inference probes."""
    from hermes_cli import auth as hermes_auth

    pconfig = hermes_auth.PROVIDER_REGISTRY.get(provider_id)
    secret_resolver = getattr(hermes_auth, "_resolve_api_key_provider_secret", None)
    if pconfig is None or not callable(secret_resolver):
        raise RuntimeError("This Hermes version does not expose safe API-key resolution.")
    token, _source = secret_resolver(provider_id, pconfig)
    status = hermes_auth.get_api_key_provider_status(provider_id)
    base_url = str(status.get("base_url") or pconfig.inference_base_url or "").strip().rstrip("/")

    # Hermes' public Z.AI runtime resolver may issue model-completion probes.
    # Reuse its cached endpoint decision when present, but never trigger a probe
    # from this read-only usage surface.
    if provider_id == "zai" and token:
        try:
            auth_store = hermes_auth._load_auth_store()
            state = hermes_auth._load_provider_state(auth_store, "zai") or {}
            detected = state.get("detected_endpoint") or {}
            expected_hash = hashlib.sha256(str(token).encode()).hexdigest()[:16]
            if detected.get("key_hash") == expected_hash and detected.get("base_url"):
                base_url = str(detected["base_url"]).strip().rstrip("/")
        except Exception:
            pass
    return str(token or "").strip(), base_url


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


def _deepseek_payload(payload: Mapping[str, Any]) -> Dict[str, Any]:
    windows: List[Dict[str, Any]] = []
    balances = payload.get("balance_infos")
    if isinstance(balances, list):
        for raw in balances:
            if not isinstance(raw, Mapping):
                continue
            currency = (_clean_text(raw.get("currency"), 12) or "USD").upper()
            total = _usage_number(raw.get("total_balance"))
            if total is None:
                continue
            breakdown = []
            granted = _usage_number(raw.get("granted_balance"))
            topped_up = _usage_number(raw.get("topped_up_balance"))
            if granted is not None:
                breakdown.append(f"{granted:,.2f} {currency} granted")
            if topped_up is not None:
                breakdown.append(f"{topped_up:,.2f} {currency} topped up")
            windows.append(
                _usage_window(
                    f"{currency} balance",
                    kind="balance",
                    remaining=total,
                    unit=currency,
                    detail=" · ".join(breakdown) or None,
                )
            )
    if not windows:
        return _provider_payload(
            "deepseek",
            status="unavailable",
            message="DeepSeek returned no recognized balance fields.",
        )
    available = payload.get("is_available")
    return _provider_payload(
        "deepseek",
        status="ok",
        windows=windows,
        message="DeepSeek reports insufficient balance for API calls." if available is False else None,
    )


def _collect_deepseek_usage() -> Dict[str, Any]:
    token = ""
    headers: Dict[str, str] = {}
    try:
        token, base_url = _resolve_hermes_api_key("deepseek")
        if not token:
            return _provider_payload(
                "deepseek", status="not_configured", message="No Hermes DeepSeek API key was found."
            )
        parsed = urlparse(base_url)
        if parsed.scheme != "https" or (parsed.hostname or "").lower() != "api.deepseek.com":
            return _provider_payload(
                "deepseek",
                status="unavailable",
                message="Balance checks require the official https://api.deepseek.com endpoint.",
            )
        import httpx

        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        response = httpx.get(
            "https://api.deepseek.com/user/balance",
            headers=headers,
            timeout=AI_USAGE_PROVIDER_TIMEOUT_SECONDS,
        )
        if response.status_code == 401:
            return _provider_payload("deepseek", status="expired", message="DeepSeek rejected the configured API key.")
        if response.status_code == 403:
            return _provider_payload("deepseek", status="forbidden", message="DeepSeek denied balance access.")
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, Mapping):
            return _provider_payload("deepseek", status="unavailable", message="DeepSeek returned an invalid response.")
        return _deepseek_payload(payload)
    except ImportError:
        return _provider_payload("deepseek", status="unavailable", message="Hermes HTTP client is unavailable.")
    except Exception as error:
        return _provider_payload("deepseek", status="unavailable", message=_provider_message(error))
    finally:
        token = ""
        headers.clear()


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

    details = ["Experimental Kimi usage surface; response compatibility may change."]
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
        return _provider_payload(
            "zai", status="unavailable", message=f"Z.AI usage service returned code {code:g}."
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
        details=["Experimental Z.AI monitoring surface; response compatibility may change."],
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


def _grok_windows_from_payloads(
    weekly_payload: Optional[Mapping[str, Any]],
    monthly_payload: Optional[Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    windows: List[Dict[str, Any]] = []
    weekly_config = weekly_payload.get("config") if isinstance(weekly_payload, Mapping) else None
    if isinstance(weekly_config, Mapping):
        used_pct = _usage_percent(weekly_config.get("creditUsagePercent"))
        period = weekly_config.get("currentPeriod")
        reset_at = period.get("end") if isinstance(period, Mapping) else None
        reset_at = reset_at or weekly_config.get("billingPeriodEnd")
        if used_pct is not None:
            windows.append(
                _usage_window(
                    "Weekly allowance",
                    used_percent=used_pct,
                    reset_at=reset_at,
                    detail="Shared across Grok products",
                )
            )

    monthly_config = monthly_payload.get("config") if isinstance(monthly_payload, Mapping) else None
    if isinstance(monthly_config, Mapping):
        raw_limit = monthly_config.get("monthlyLimit")
        raw_used = monthly_config.get("used")
        limit_value = _usage_number(raw_limit.get("val")) if isinstance(raw_limit, Mapping) else None
        used_value = _usage_number(raw_used.get("val")) if isinstance(raw_used, Mapping) else None
        if limit_value is not None and limit_value > 0 and used_value is not None:
            limit = limit_value / 100.0
            used = max(0.0, used_value / 100.0)
            remaining = max(0.0, limit - used)
            windows.append(
                _usage_window(
                    "Extra usage credits",
                    kind="balance",
                    used_percent=(used / limit) * 100.0,
                    reset_at=monthly_config.get("billingPeriodEnd"),
                    limit=limit,
                    used=used,
                    remaining=remaining,
                    unit="credits",
                )
            )
    return windows


def _collect_grok_usage() -> Dict[str, Any]:
    # Billing response mapping is informed by the MIT-licensed
    # bnogalski/hermes-llm-quota project; see UPSTREAM.md.
    try:
        from hermes_cli.auth import resolve_xai_oauth_runtime_credentials

        credentials = resolve_xai_oauth_runtime_credentials(refresh_if_expiring=True) or {}
        token = str(credentials.get("api_key") or "").strip()
    except Exception as error:
        return _provider_payload("grok", status="unavailable", message=_provider_message(error))
    if not token:
        return _provider_payload("grok", status="not_configured", message="No Hermes xAI OAuth login was found.")

    try:
        import httpx
    except ImportError:
        return _provider_payload("grok", status="unavailable", message="Hermes HTTP client is unavailable.")

    url = "https://cli-chat-proxy.grok.com/v1/billing"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "User-Agent": "xai-grok-cli",
        "X-Xai-Token-Auth": "xai-grok-cli",
        "x-grok-client-identifier": "grok-cli",
        "x-grok-client-version": "0.2.103",
    }
    payloads: Dict[str, Mapping[str, Any]] = {}
    errors: List[str] = []
    statuses: List[int] = []
    try:
        with httpx.Client(timeout=AI_USAGE_PROVIDER_TIMEOUT_SECONDS) as client:
            for name, params in (("weekly", {"format": "credits"}), ("monthly", None)):
                try:
                    response = client.get(url, params=params, headers=headers)
                    statuses.append(response.status_code)
                    if response.status_code == 200:
                        payload = response.json()
                        if isinstance(payload, Mapping):
                            payloads[name] = payload
                        else:
                            errors.append(f"{name}: invalid response")
                    else:
                        errors.append(f"{name}: HTTP {response.status_code}")
                except Exception as error:
                    errors.append(f"{name}: {_provider_message(error)}")
    finally:
        token = ""
        headers.clear()

    if not payloads:
        if statuses and all(code == 401 for code in statuses):
            return _provider_payload("grok", status="expired", message="The xAI OAuth login has expired.")
        if statuses and all(code == 403 for code in statuses):
            return _provider_payload("grok", status="forbidden", message="xAI rejected access to account usage.")
        return _provider_payload(
            "grok",
            status="unavailable",
            message="; ".join(errors) or "Grok usage is temporarily unavailable.",
        )

    windows = _grok_windows_from_payloads(payloads.get("weekly"), payloads.get("monthly"))
    if not windows:
        return _provider_payload(
            "grok",
            status="unavailable",
            message="Grok returned no recognized quota fields.",
            partial=bool(errors),
        )
    return _provider_payload(
        "grok",
        status="ok",
        windows=windows,
        details=["Private xAI billing surface; response compatibility may change."],
        message="; ".join(errors) if errors else None,
        partial=bool(errors),
    )


def _collect_nous_usage() -> Dict[str, Any]:
    try:
        from agent.account_usage import build_nous_credits_snapshot
        from hermes_cli.nous_account import get_nous_portal_account_info

        account = get_nous_portal_account_info(force_fresh=True)
        if account is None or not getattr(account, "logged_in", False):
            return _provider_payload("nous", status="not_configured", message="No Nous Portal login was found.")
        return _account_usage_payload("nous", build_nous_credits_snapshot(account))
    except Exception as error:
        return _provider_payload("nous", status="unavailable", message=_provider_message(error))


def _openrouter_payload(
    key_data: Optional[Mapping[str, Any]],
    credits_data: Optional[Mapping[str, Any]],
    *,
    partial_message: Optional[str] = None,
) -> Dict[str, Any]:
    windows: List[Dict[str, Any]] = []
    details: List[str] = []
    if isinstance(key_data, Mapping):
        limit = _usage_number(key_data.get("limit"))
        remaining = _usage_number(key_data.get("limit_remaining"))
        if limit is not None and limit > 0 and remaining is not None and 0 <= remaining <= limit:
            used = limit - remaining
            reset = _clean_text(key_data.get("limit_reset"), 80)
            windows.append(
                _usage_window(
                    "API key limit",
                    used_percent=(used / limit) * 100.0,
                    detail=f"Resets {reset}" if reset else None,
                    limit=limit,
                    used=used,
                    remaining=remaining,
                    unit="USD",
                )
            )
        usage_parts = []
        for key, label in (
            ("usage_daily", "today"),
            ("usage_weekly", "this week"),
            ("usage_monthly", "this month"),
        ):
            value = _usage_number(key_data.get(key))
            if value is not None:
                usage_parts.append(f"${value:,.2f} {label}")
        if usage_parts:
            details.append("API key usage: " + " · ".join(usage_parts))
    if isinstance(credits_data, Mapping):
        total = _usage_number(credits_data.get("total_credits"))
        used = _usage_number(credits_data.get("total_usage"))
        if total is not None and used is not None:
            remaining = max(0.0, total - used)
            windows.append(
                _usage_window(
                    "Account credits",
                    kind="balance",
                    used_percent=(used / total) * 100.0 if total > 0 else None,
                    limit=total,
                    used=used,
                    remaining=remaining,
                    unit="USD",
                )
            )
    if not windows and not details:
        return _provider_payload(
            "openrouter",
            status="unavailable",
            message=partial_message or "OpenRouter returned no recognized usage fields.",
        )
    return _provider_payload(
        "openrouter",
        status="ok",
        windows=windows,
        details=details,
        message=partial_message,
        partial=bool(partial_message),
    )


def _collect_openrouter_usage() -> Dict[str, Any]:
    try:
        from hermes_cli.runtime_provider import resolve_runtime_provider

        runtime = resolve_runtime_provider(
            requested="openrouter",
            explicit_base_url=None,
            explicit_api_key=None,
        )
        token = str(runtime.get("api_key") or "").strip()
        base_url = str(runtime.get("base_url") or "https://openrouter.ai/api/v1").strip().rstrip("/")
    except Exception as error:
        return _provider_payload("openrouter", status="unavailable", message=_provider_message(error))
    if not token:
        return _provider_payload("openrouter", status="not_configured", message="No Hermes OpenRouter API key was found.")
    parsed = urlparse(base_url)
    if parsed.scheme != "https" or (parsed.hostname or "").lower() != "openrouter.ai":
        return _provider_payload(
            "openrouter",
            status="unavailable",
            message="Usage checks require the official https://openrouter.ai API endpoint.",
        )

    try:
        import httpx
    except ImportError:
        return _provider_payload("openrouter", status="unavailable", message="Hermes HTTP client is unavailable.")

    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    key_data: Optional[Mapping[str, Any]] = None
    credits_data: Optional[Mapping[str, Any]] = None
    errors: List[str] = []
    statuses: List[int] = []
    try:
        with httpx.Client(timeout=AI_USAGE_PROVIDER_TIMEOUT_SECONDS) as client:
            for name, path in (("key", "/key"), ("credits", "/credits")):
                try:
                    response = client.get(base_url + path, headers=headers)
                    statuses.append(response.status_code)
                    if response.status_code == 200:
                        payload = response.json()
                        data = payload.get("data") if isinstance(payload, Mapping) else None
                        if isinstance(data, Mapping):
                            if name == "key":
                                key_data = data
                            else:
                                credits_data = data
                        else:
                            errors.append(f"{name}: invalid response")
                    elif name == "credits" and response.status_code == 403:
                        errors.append("Account credits require an OpenRouter management key")
                    else:
                        errors.append(f"{name}: HTTP {response.status_code}")
                except Exception as error:
                    errors.append(f"{name}: {_provider_message(error)}")
    finally:
        token = ""
        headers.clear()

    if key_data is None and credits_data is None:
        if statuses and all(code == 401 for code in statuses):
            return _provider_payload("openrouter", status="expired", message="OpenRouter rejected the configured API key.")
        if statuses and all(code in {401, 403} for code in statuses):
            return _provider_payload("openrouter", status="forbidden", message="OpenRouter rejected account-usage access.")
    return _openrouter_payload(
        key_data,
        credits_data,
        partial_message="; ".join(errors) if errors else None,
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
        schema_row = db._conn.execute("SELECT version FROM schema_version").fetchone()
        counts = db._conn.execute(
            """
            SELECT (SELECT COUNT(*) FROM sessions) AS sessions,
                   (SELECT COUNT(*) FROM messages) AS messages,
                   (SELECT COUNT(*) FROM session_model_usage) AS model_usage_rows,
                   (SELECT COUNT(*) FROM async_delegations) AS delegations
            """
        ).fetchone()
        fts_names = [
            row[0]
            for row in db._conn.execute(
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
