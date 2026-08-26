"""Bounded Hermes runtime-log parsing and telemetry helpers."""

from __future__ import annotations

try:
    from ._common import *
except ImportError:  # pragma: no cover
    from _common import *

def _timestamp_from_log(value: str) -> float:
    try:
        return dt.datetime.strptime(value, "%Y-%m-%d %H:%M:%S,%f").astimezone().timestamp()
    except ValueError:
        return 0.0


def _api_failure_category(detail: Any) -> str:
    text = str(detail or "").lower()
    if any(marker in text for marker in ("rate limit", "rate_limit", "http 429", "status 429", " 429")):
        return "rate_limit"
    if any(
        marker in text
        for marker in (
            "timed out",
            "timeout",
            "apitimeout",
            "http 408",
            "status 408",
            "http 504",
            "status 504",
        )
    ):
        return "timeout"
    return "error"


def _parse_log_file(path: Path) -> Dict[str, Any]:
    stat = path.stat()
    signature = (stat.st_size, stat.st_mtime_ns)
    key = str(path)
    cached = _log_file_cache.get(key)
    if cached and cached[0] == signature:
        _log_file_cache.move_to_end(key)
        return cached[1]

    api_events: List[Dict[str, Any]] = []
    api_errors: List[Dict[str, Any]] = []
    tool_events: List[Dict[str, Any]] = []
    timestamps: List[float] = []
    seen_api_errors: set[Tuple[str, int, int]] = set()
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
        if timestamp:
            timestamps.append(timestamp)
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
        error_match = _API_ERROR_RE.search(message)
        attempt_error_match = _API_ATTEMPT_ERROR_RE.search(message)
        if error_match or attempt_error_match:
            match = error_match or attempt_error_match
            detail = str(match.groupdict().get("detail") or message)
            session_id = envelope.group("session") or ""
            call_number = _integer(match.groupdict().get("call"))
            signature_key = (session_id, call_number, round(timestamp))
            if signature_key not in seen_api_errors:
                seen_api_errors.add(signature_key)
                api_errors.append(
                    {
                        "timestamp": timestamp,
                        "session_id": session_id or None,
                        "call": call_number,
                        "category": _api_failure_category(f"{message} {detail}"),
                        "model": match.groupdict().get("model"),
                        "provider": match.groupdict().get("provider"),
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
    parsed = {"api": api_events, "errors": api_errors, "tools": tool_events, "timestamps": timestamps}
    _log_file_cache[key] = (signature, parsed)
    _log_file_cache.move_to_end(key)
    while len(_log_file_cache) > 10:
        _log_file_cache.popitem(last=False)
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
        return {"api": [], "errors": [], "tools": [], "timestamps": [], "files": []}
    candidates = sorted(
        (path for path in log_dir.glob("agent.log*") if path.is_file()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )[:MAX_LOG_FILES]
    api_events: List[Dict[str, Any]] = []
    api_errors: List[Dict[str, Any]] = []
    tool_events: List[Dict[str, Any]] = []
    timestamps: List[float] = []
    files = []
    for path in reversed(candidates):
        try:
            parsed = _parse_log_file(path)
        except OSError:
            continue
        api_events.extend(parsed["api"])
        api_errors.extend(parsed.get("errors", []))
        tool_events.extend(parsed["tools"])
        timestamps.extend(parsed.get("timestamps", []))
        files.append({"name": path.name, "size_bytes": path.stat().st_size, "updated_at": path.stat().st_mtime})
    return {"api": api_events, "errors": api_errors, "tools": tool_events, "timestamps": timestamps, "files": files}


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

__all__ = [name for name in globals() if not name.startswith("__")]
