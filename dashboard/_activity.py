"""Activity calendar and hour-of-week punchcard.

The calendar is a fixed twelve months, whatever the page period says, so a
glance shows the shape of a year: one cell per local calendar day, four
measures (tokens, cost, sessions, failures). Sessions land on the day they
were last active — the same rule as the Overview's daily bars — because
Hermes records usage per session, not per call. Failures land on the day the
failing tool result was written.

The punchcard follows the page period: messages, tool results and session
starts by local weekday and hour, read from the message and session
timestamps themselves, so a long bot session spreads across the hours it
actually ran.

Inspired by hermes-activity (MIT, github.com/hassan-kamel/hermes-activity);
the code here is Session Lens' own.
"""

from __future__ import annotations

from ._common import *

ACTIVITY_CALENDAR_DAYS = 365


def _activity_midnight(day: dt.date) -> float:
    return dt.datetime(day.year, day.month, day.day).timestamp()


def _activity_calendar_sync(now: Optional[float] = None) -> Dict[str, Any]:
    today = dt.datetime.fromtimestamp(now if now is not None else time.time()).date()
    first = today - dt.timedelta(days=ACTIVITY_CALENDAR_DAYS - 1)
    start_at = _activity_midnight(first)
    end_at = _activity_midnight(today + dt.timedelta(days=1))
    session_sql, session_params = _period_sql("started_at", start_at, end_at)
    joined_sql, joined_params = _period_sql("s.started_at", start_at, end_at)
    by_day: Dict[str, Dict[str, Any]] = {}
    with _database() as db:
        connection = _db_connection(db)
        for row in connection.execute(
            f"""
            SELECT date(coalesce(last_activity_at, ended_at, started_at), 'unixepoch', 'localtime') AS day,
                   COUNT(*) AS sessions,
                   coalesce(SUM(input_tokens + output_tokens + cache_read_tokens + cache_write_tokens),0) AS total_tokens,
                   coalesce(SUM(CASE
                       WHEN actual_cost_usd > 0 THEN actual_cost_usd
                       WHEN estimated_cost_usd > 0 THEN estimated_cost_usd
                       ELSE 0 END),0) AS cost_usd
            FROM ({_accounted_sessions_sql(connection)}) sessions
            WHERE {session_sql}
            GROUP BY day
            """,
            tuple(session_params),
        ).fetchall():
            item = _row_dict(row)
            day = str(item.get("day") or "")
            if not day:
                continue
            by_day[day] = {
                "day": day,
                "sessions": _integer(item.get("sessions")),
                "total_tokens": _integer(item.get("total_tokens")),
                "cost_usd": round(_number(item.get("cost_usd")), 6),
                "failures": 0,
            }
        _check_route_budget()
        first_key, last_key = first.isoformat(), today.isoformat()
        for failure in _confirmed_failure_rows(connection, joined_sql, joined_params):
            stamp = _number(failure.get("timestamp"), 0)
            if stamp <= 0:
                continue
            day = dt.datetime.fromtimestamp(stamp).date().isoformat()
            if day < first_key or day > last_key:
                continue
            entry = by_day.setdefault(
                day, {"day": day, "sessions": 0, "total_tokens": 0, "cost_usd": 0.0, "failures": 0}
            )
            entry["failures"] += 1
    days = [by_day[key] for key in sorted(by_day) if first.isoformat() <= key <= today.isoformat()]
    totals = {
        "sessions": sum(item["sessions"] for item in days),
        "total_tokens": sum(item["total_tokens"] for item in days),
        "cost_usd": round(sum(item["cost_usd"] for item in days), 6),
        "failures": sum(item["failures"] for item in days),
        "active_days": sum(1 for item in days if item["sessions"] > 0),
    }
    return {
        "start": first.isoformat(),
        "end": today.isoformat(),
        "days": days,
        "totals": totals,
        "generated_at": time.time(),
    }


def _activity_hours_sync(
    days: int,
    start_at: Optional[float] = None,
    end_at: Optional[float] = None,
) -> Dict[str, Any]:
    """Messages, tool results and session starts by local weekday (0 = Sunday) and hour."""
    period_start, period_end = _period_bounds(days, start_at, end_at)
    cells: Dict[Tuple[int, int], Dict[str, int]] = {}

    def cell(weekday: int, hour: int) -> Dict[str, int]:
        return cells.setdefault(
            (weekday, hour),
            {"weekday": weekday, "hour": hour, "messages": 0, "tool_calls": 0, "sessions": 0},
        )

    message_clauses = ["coalesce(active,1)=1", "timestamp >= ?"]
    message_params: List[float] = [period_start]
    session_clauses = ["started_at >= ?"]
    session_params: List[float] = [period_start]
    if period_end is not None:
        message_clauses.append("timestamp < ?")
        message_params.append(period_end)
        session_clauses.append("started_at < ?")
        session_params.append(period_end)
    with _database() as db:
        connection = _db_connection(db)
        for row in connection.execute(
            f"""
            SELECT CAST(strftime('%w', timestamp, 'unixepoch', 'localtime') AS INTEGER) AS weekday,
                   CAST(strftime('%H', timestamp, 'unixepoch', 'localtime') AS INTEGER) AS hour,
                   SUM(CASE WHEN role IN ('user','assistant') THEN 1 ELSE 0 END) AS messages,
                   SUM(CASE WHEN role = 'tool' THEN 1 ELSE 0 END) AS tool_calls
            FROM messages
            WHERE {' AND '.join(message_clauses)}
            GROUP BY weekday, hour
            """,
            tuple(message_params),
        ).fetchall():
            item = _row_dict(row)
            if item.get("weekday") is None or item.get("hour") is None:
                continue
            target = cell(_integer(item["weekday"]), _integer(item["hour"]))
            target["messages"] += _integer(item.get("messages"))
            target["tool_calls"] += _integer(item.get("tool_calls"))
        _check_route_budget()
        for row in connection.execute(
            f"""
            SELECT CAST(strftime('%w', started_at, 'unixepoch', 'localtime') AS INTEGER) AS weekday,
                   CAST(strftime('%H', started_at, 'unixepoch', 'localtime') AS INTEGER) AS hour,
                   COUNT(*) AS sessions
            FROM sessions
            WHERE {' AND '.join(session_clauses)}
            GROUP BY weekday, hour
            """,
            tuple(session_params),
        ).fetchall():
            item = _row_dict(row)
            if item.get("weekday") is None or item.get("hour") is None:
                continue
            cell(_integer(item["weekday"]), _integer(item["hour"]))["sessions"] += _integer(item.get("sessions"))
    rows = [cells[key] for key in sorted(cells)]
    totals = {
        key: sum(item[key] for item in rows) for key in ("messages", "tool_calls", "sessions")
    }
    return {
        "period": _period_payload(days, period_start, period_end),
        "cells": rows,
        "totals": totals,
        "generated_at": time.time(),
    }


__all__ = [name for name in globals() if not name.startswith("__")]
