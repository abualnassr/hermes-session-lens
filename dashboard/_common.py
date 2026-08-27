"""Shared constants, redaction, periods, and read-only database helpers."""

from __future__ import annotations

import asyncio
import copy
import datetime as dt
import fnmatch
import hashlib
import json
import math
import os
import re
import sqlite3
import threading
import time
from collections import Counter, OrderedDict, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional, Tuple
from urllib.parse import urlparse

try:
    from fastapi import APIRouter, HTTPException, Query
except ImportError:  # pragma: no cover - supports dependency-free compatibility tests.
    class _StubRoute:
        def __init__(self, path: str, methods: set):
            self.path = path
            self.methods = methods

    class APIRouter:  # type: ignore[no-redef]
        def __init__(self) -> None:
            self.routes: List[Any] = []

        def get(self, path: str):
            def register(function):
                self.routes.append(_StubRoute(path, {"GET"}))
                return function

            return register

    class HTTPException(Exception):  # type: ignore[no-redef]
        def __init__(self, status_code: int, detail: str):
            super().__init__(detail)
            self.status_code = status_code
            self.detail = detail

    def Query(default: Any, **_constraints: Any) -> Any:  # type: ignore[misc]
        return default

try:
    from ._hermes_compat import *
except ImportError:  # pragma: no cover - direct Hermes file loading
    from _hermes_compat import *

def _plugin_version() -> str:
    try:
        manifest_text = (Path(__file__).resolve().parents[1] / "plugin.yaml").read_text(encoding="utf-8")
        match = re.search(r"(?m)^version:\s*['\"]?([^\s'\"]+)", manifest_text)
        if match:
            return match.group(1)
    except OSError:
        pass
    return "0.18.2"


PLUGIN_VERSION = _plugin_version()
MAX_SESSION_PAGE = 500
MAX_ANALYSIS_EVENTS = 5000
MAX_SEARCH_MATCHES = 2000
MAX_SNIPPET_CHARS = 560
MAX_TRACE_PAGE = 200
MAX_TRACE_CONTENT_CHARS = 6000
MAX_LOG_FILES = 5
MAX_LOG_FILE_BYTES = 6 * 1024 * 1024
AI_USAGE_CACHE_TTL_SECONDS = 300
AI_MODELS_CACHE_TTL_SECONDS = 60
AI_USAGE_PROVIDER_TIMEOUT_SECONDS = 12
DEFAULT_RATE_SAMPLE_THRESHOLD = 20
MAX_ROUTE_MAPPINGS = 200

_ERROR_FINISH_REASONS = {"error", "agent_error", "content_filter"}
_ERROR_EFFECTS = {"blocked", "denied", "error", "failed", "failure"}
_FAILURE_RE = re.compile(
    r"(?im)(?:^|[\r\n])\s*(?:error(?!-free\b)|failed|failure|fatal|traceback|exception)\b"
    r"|\bpermission denied\b|\baccess is denied\b|\btimed? out\b"
    r"|\beconnrefused\b|\beaddrinuse\b|\bcommand not found\b"
    r"|\bno such file or directory\b|\bprocess exited with (?:code\s*)?[1-9]\d*\b"
    r"|\bexit[_ ]code[\"']?\s*[:=]\s*[1-9]\d*\b"
    r"|\"(?:error|errors)\"\s*:\s*(?!\s|null\b|false\b|0\b|\"\")",
)
_FAILURE_LINE_RE = re.compile(
    r"(?im)^[^\S\r\n]*(?:error(?!-free\b)|failed|failure|fatal|traceback|exception)\b"
)
_FAILURE_PHRASE_RE = re.compile(
    r"(?i)\b(?:permission denied|access is denied|time(?:d)? out|econnrefused|eaddrinuse|"
    r"command not found|no such file or directory)\b"
)
_FAILURE_PROCESS_EXIT_RE = re.compile(
    r"(?i)\bprocess exited with (?:code\s*)?[1-9]\d*\b"
)
_FAILURE_EXIT_CODE_RE = re.compile(
    r"(?i)\bexit[_ ]code[\"']?\s*[:=]\s*[1-9]\d*\b"
)
_FAILURE_JSON_RE = re.compile(
    r"(?i)\"(?:error|errors)\"\s*:\s*(?!\s|null\b|false\b|0\b|\"\")"
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
_API_ERROR_RE = re.compile(
    r"(?:Error during (?:local message processing after )?OpenAI-compatible API call"
    r"|Outer loop error in API call) #(?P<call>\d+)(?::\s*(?P<detail>.*))?",
    re.IGNORECASE,
)
_API_ATTEMPT_ERROR_RE = re.compile(
    r"API call failed\s*\(attempt\s+\d+/\d+\).*?provider=(?P<provider>\S+)"
    r".*?model=(?P<model>\S+)(?:\s+summary=(?P<detail>.*))?",
    re.IGNORECASE,
)
_TOOL_METRIC_RE = re.compile(
    r"tool (?P<tool>[\w.:-]+) (?P<status>completed|failed|cancelled) "
    r"\((?P<duration>[\d.]+)s(?:, (?P<chars>\d+) chars)?\)"
)
_GIT_COMMIT_RE = re.compile(r"\bgit(?:\.exe)?\s+(?:-[^\s]+\s+)*commit\b", re.IGNORECASE)
_NO_FILE_CHANGE_RE = re.compile(
    r"\bnothing to commit\b|\bno changes?\b|\bpatch did not apply\b",
    re.IGNORECASE,
)
_SESSION_TASK_TYPES = ("Orchestration", "Coding", "Writing", "Analysis", "General")
_SESSION_TASK_TYPE_SET = set(_SESSION_TASK_TYPES)
_CODE_FILE_EXTENSIONS = {
    ".bash",
    ".c",
    ".cc",
    ".cjs",
    ".cpp",
    ".css",
    ".go",
    ".gradle",
    ".h",
    ".hpp",
    ".html",
    ".java",
    ".js",
    ".json",
    ".jsx",
    ".kt",
    ".lock",
    ".mjs",
    ".ps1",
    ".py",
    ".rs",
    ".scss",
    ".sh",
    ".sql",
    ".svelte",
    ".swift",
    ".toml",
    ".ts",
    ".tsx",
    ".vue",
    ".xml",
    ".yaml",
    ".yml",
}
_WRITING_FILE_EXTENSIONS = {
    ".csv",
    ".docx",
    ".markdown",
    ".md",
    ".pdf",
    ".pptx",
    ".rtf",
    ".txt",
    ".xls",
    ".xlsx",
}
_FILE_REFERENCE_RE = re.compile(
    r"(?i)(?P<path>(?:[a-z]:)?(?:[^\s\"'<>|;&]+[\\/])*[^\s\"'<>|;&]+\."
    r"(?:bash|c|cc|cjs|cpp|css|csv|docx|go|gradle|h|hpp|html|java|js|json|jsx|kt|lock|"
    r"markdown|md|mjs|pdf|pptx|ps1|py|rs|rtf|scss|sh|sql|svelte|swift|toml|ts|tsx|txt|"
    r"vue|xls|xlsx|xml|yaml|yml))\b"
)
_PATCH_FILE_RE = re.compile(r"(?im)^\*\*\* (?:add|delete|update) file:\s*(?P<path>.+?)\s*$")
_CODE_RUNNER_RE = re.compile(
    r"(?ix)(?:^|\s)(?:"
    r"pytest(?:\.exe)?|(?:python\s+-m\s+)?pytest|"
    r"npm\s+(?:run|test|build|install|ci)|"
    r"(?:pnpm|yarn)\s+(?:run|test|build|install)|"
    r"npx\s+(?:tsc|jest|vitest|mocha|eslint|ruff|prettier|biome)|"
    r"cargo\s+(?:build|test|run|install)|go\s+(?:build|test|install|run)|"
    r"(?:gradle|gradlew|mvn|mvnw|make|cmake|ninja|tsc|jest|vitest|mocha|tox|nox)\b|"
    r"(?:pip|pipx|uv)\s+(?:install|sync|run)|"
    r"(?:ruff|eslint)\b[^\r\n;&|]*--fix\b|"
    r"(?:prettier|biome)\b[^\r\n;&|]*(?:--write|--fix)\b"
    r")"
)
_WORKER_COMMAND_RE = re.compile(
    r"(?i)(?:^|[;&|]\s*)(?:(?:npx\s+)?(?:claude(?:\s+code)?|codex(?:\.exe)?|openhands))\b"
)

_log_file_cache: OrderedDict[str, Tuple[Tuple[int, int], Dict[str, Any]]] = OrderedDict()
_ai_usage_cache_lock = threading.Lock()
_ai_usage_cache: Optional[Tuple[float, Dict[str, Any]]] = None
_ai_usage_last_success: Dict[str, Dict[str, Any]] = {}
_ai_models_cache_lock = threading.Lock()
_ai_models_cache: Dict[Tuple[Any, ...], Tuple[float, Dict[str, Any]]] = {}
_session_classification_cache_lock = threading.Lock()
_session_classification_cache: Dict[Tuple[str, Any, int], Dict[str, Any]] = {}
_session_failure_cache_lock = threading.Lock()
_session_failure_cache: OrderedDict[
    str, Tuple[Tuple[Any, int], Tuple[Dict[str, Any], ...]]
] = OrderedDict()
_SESSION_FAILURE_CACHE_MAX = 2000

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
    if not text:
        return False
    lowered = text.lower()
    if _FAILURE_LINE_RE.search(text) or _FAILURE_PHRASE_RE.search(text):
        return True
    if "process exited with" in lowered and _FAILURE_PROCESS_EXIT_RE.search(text):
        return True
    if ("exit_code" in lowered or "exit code" in lowered) and _FAILURE_EXIT_CODE_RE.search(text):
        return True
    if ('"error"' in lowered or '"errors"' in lowered) and _FAILURE_JSON_RE.search(text):
        return True
    return False


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
                    OR lower(substr(coalesce({alias}.content, ''), 1, 12000)) GLOB '*failure*'
                    OR lower(substr(coalesce({alias}.content, ''), 1, 12000)) GLOB '*fatal*'
                    OR lower(substr(coalesce({alias}.content, ''), 1, 12000)) GLOB '*traceback*'
                    OR lower(substr(coalesce({alias}.content, ''), 1, 12000)) GLOB '*exception*'
                    OR lower(substr(coalesce({alias}.content, ''), 1, 12000)) GLOB '*permission denied*'
                    OR lower(substr(coalesce({alias}.content, ''), 1, 12000)) GLOB '*access is denied*'
                    OR lower(substr(coalesce({alias}.content, ''), 1, 12000)) GLOB '*timed out*'
                    OR lower(substr(coalesce({alias}.content, ''), 1, 12000)) GLOB '*timeout*'
                    OR lower(substr(coalesce({alias}.content, ''), 1, 12000)) GLOB '*econnrefused*'
                    OR lower(substr(coalesce({alias}.content, ''), 1, 12000)) GLOB '*eaddrinuse*'
                    OR lower(substr(coalesce({alias}.content, ''), 1, 12000)) GLOB '*command not found*'
                    OR lower(substr(coalesce({alias}.content, ''), 1, 12000)) GLOB '*no such file or directory*'
                    OR lower(substr(coalesce({alias}.content, ''), 1, 12000)) GLOB '*process exited with*'
                    OR lower(substr(coalesce({alias}.content, ''), 1, 12000)) GLOB '*exit_code*'
                    OR lower(substr(coalesce({alias}.content, ''), 1, 12000)) GLOB '*exit code*'
                )
            )
        )
    """


def _confirmed_failure_rows(
    connection: sqlite3.Connection,
    session_where_sql: str,
    params: Iterable[Any] = (),
) -> List[Dict[str, Any]]:
    """Return confirmed failures, rescanning only sessions whose evidence changed."""
    session_rows = connection.execute(
        f"""
        SELECT s.id, s.last_activity_at, s.message_count
        FROM sessions s
        WHERE ({session_where_sql})
        """,
        tuple(params),
    ).fetchall()
    fingerprints = {
        str(row["id"]): (row["last_activity_at"], _integer(row["message_count"]))
        for row in session_rows
    }
    if not fingerprints:
        return []

    with _session_failure_cache_lock:
        missing_ids = [
            session_id
            for session_id, fingerprint in fingerprints.items()
            if session_id not in _session_failure_cache
            or _session_failure_cache[session_id][0] != fingerprint
        ]
        for chunk_start in range(0, len(missing_ids), 900):
            chunk = missing_ids[chunk_start : chunk_start + 900]
            placeholders = ",".join("?" for _ in chunk)
            candidates = connection.execute(
                f"""
                SELECT m.id, m.session_id, m.role, substr(m.content,1,12000) AS content,
                       m.tool_name, m.finish_reason, m.effect_disposition, m.timestamp
                FROM messages m
                WHERE coalesce(m.active,1)=1
                  AND m.session_id IN ({placeholders})
                  AND {_failure_sql('m')}
                """,
                tuple(chunk),
            ).fetchall()
            confirmed_by_session: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
            for material in (_row_dict(row) for row in candidates):
                if _is_failure(
                    role=material.get("role"),
                    content=material.get("content"),
                    finish_reason=material.get("finish_reason"),
                    effect_disposition=material.get("effect_disposition"),
                ):
                    confirmed_by_session[str(material.get("session_id") or "")].append(material)
            for session_id in chunk:
                _session_failure_cache[session_id] = (
                    fingerprints[session_id],
                    tuple(confirmed_by_session.get(session_id, ())),
                )
                _session_failure_cache.move_to_end(session_id)
        while len(_session_failure_cache) > _SESSION_FAILURE_CACHE_MAX:
            _session_failure_cache.popitem(last=False)

        rows: List[Dict[str, Any]] = []
        for session_id in fingerprints:
            cached = _session_failure_cache.get(session_id)
            if cached is None:
                continue
            _session_failure_cache.move_to_end(session_id)
            rows.extend(dict(row) for row in cached[1])
        return rows


def _confirmed_failure_counts(
    connection: sqlite3.Connection,
    session_where_sql: str,
    params: Iterable[Any] = (),
) -> Dict[str, int]:
    counts: Counter[str] = Counter()
    for row in _confirmed_failure_rows(connection, session_where_sql, params):
        counts[str(row.get("session_id") or "")] += 1
    return dict(counts)

__all__ = [name for name in globals() if not name.startswith("__")]
