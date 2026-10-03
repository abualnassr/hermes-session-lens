"""Conversation chains: one conversation's cost across the sessions it was split into.

Hermes links a session to the one it continues through parent_session_id.
Most links on a busy install are continuations — a Telegram topic or bot
chat hits session_reset and the next message opens a new session, linked,
within seconds — and a few are subagents. Seen one session at a time, a
long conversation looks cheap; walked to its first session and summed, it
shows what the conversation actually cost.
"""

from __future__ import annotations

try:
    from ._common import *
except ImportError:  # pragma: no cover - direct Hermes file loading
    from _common import *

CHAINS_MAX = 30
CHAIN_MAX_MEMBERS = 60
CHAIN_MAX_DEPTH = 2000


def _chain_link_kind(child: Mapping[str, Any], parent: Optional[Mapping[str, Any]]) -> str:
    if str(child.get("source") or "").lower() == "subagent":
        return "subagent"
    if parent is not None and str(child.get("source") or "") == str(parent.get("source") or ""):
        return "continuation"
    return "branch"


def _chain_sessions() -> Dict[str, Dict[str, Any]]:
    """Every session in the scope with the fields chains need, keyed by id."""
    with _database() as db:
        connection = _db_connection(db)
        columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(sessions)").fetchall()}
        if "parent_session_id" not in columns:
            return {}
        list_price = "s.list_price_usd" if "list_price_usd" in columns else "NULL"
        profile = "s.__profile" if getattr(db, "union_profiles", None) else "NULL"
        rows = connection.execute(
            f"""
            SELECT s.id, s.parent_session_id, s.source, s.title, s.model, s.started_at, s.ended_at,
                   s.last_activity_at, s.end_reason, s.api_call_count,
                   s.input_tokens, s.output_tokens, s.cache_read_tokens, s.cache_write_tokens,
                   s.estimated_cost_usd, s.actual_cost_usd, s.cost_status, s.cost_source,
                   {list_price} AS list_price_usd, {profile} AS profile
            FROM ({_accounted_sessions_sql(connection)}) s
            """
        ).fetchall()
    return {str(row["id"]): _row_dict(row) for row in rows}


def _chain_root(session_id: str, sessions: Mapping[str, Mapping[str, Any]]) -> str:
    """The first session of `session_id`'s conversation.

    A corrupt parent cycle has no first session; every member of the cycle
    then resolves to the same one (the lowest id), so the cycle is one chain.
    """
    current, path = session_id, [session_id]
    while len(path) < CHAIN_MAX_DEPTH:
        parent = str(sessions.get(current, {}).get("parent_session_id") or "")
        if not parent or parent not in sessions:
            return current
        if parent in path:
            return min(path[path.index(parent):])
        path.append(parent)
        current = parent
    return current


def _chain_payload(root: str, members: List[Mapping[str, Any]], sessions: Mapping[str, Mapping[str, Any]]) -> Dict[str, Any]:
    ordered = sorted(members, key=lambda row: _number(row.get("started_at"), 0))
    kinds: Counter = Counter()
    cash = listed = 0.0
    tokens = calls = 0
    for row in ordered:
        if str(row.get("id")) != root:
            kinds[_chain_link_kind(row, sessions.get(str(row.get("parent_session_id") or "")))] += 1
        cash += _number(_cost_view(row).get("display_cost_usd"), 0)
        listed += _number(row.get("list_price_usd"), 0)
        calls += _integer(row.get("api_call_count"))
        tokens += sum(_integer(row.get(key)) for key in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens"))
    first, last = ordered[0], ordered[-1]
    root_row = sessions.get(root, first)
    title = _clean_text(root_row.get("title"), 120)
    if not title or title[:1] in "`{[<":
        # A first message that was code or JSON makes a poor name.
        title = _clean_text(last.get("title"), 120) or title or root
    return {
        "root_id": root,
        "title": title,
        "latest_id": str(last.get("id")),
        "latest_title": _clean_text(last.get("title"), 120) or str(last.get("id")),
        "profile": root_row.get("profile"),
        "source": root_row.get("source"),
        "sessions": len(ordered),
        "links": dict(kinds),
        "started_at": first.get("started_at"),
        "last_activity_at": max(_number(row.get("last_activity_at") or row.get("ended_at") or row.get("started_at"), 0) for row in ordered) or None,
        "cash_usd": round(cash, 4),
        "list_price_usd": round(listed, 4),
        "calls": calls,
        "tokens": tokens,
        "members": [
            {
                "id": str(row.get("id")),
                "title": _clean_text(row.get("title"), 120),
                "started_at": row.get("started_at"),
                "link": None if str(row.get("id")) == root else _chain_link_kind(row, sessions.get(str(row.get("parent_session_id") or ""))),
                "cash_usd": round(_number(_cost_view(row).get("display_cost_usd"), 0), 4),
                "list_price_usd": round(_number(row.get("list_price_usd"), 0), 4) or None,
            }
            for row in ordered[-CHAIN_MAX_MEMBERS:]
        ],
    }


def _chains_sync(days: int, start_at: Optional[float] = None, end_at: Optional[float] = None) -> Dict[str, Any]:
    period_start, period_end = _period_bounds(days, start_at, end_at)
    sessions = _chain_sessions()
    by_root: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for index, (session_id, row) in enumerate(sessions.items()):
        if index % 2000 == 0:
            _check_route_budget()
        by_root[_chain_root(session_id, sessions)].append(row)
    chains = []
    for root, members in by_root.items():
        if len(members) < 2:
            continue
        active = [
            _number(row.get("last_activity_at") or row.get("ended_at") or row.get("started_at"), 0) for row in members
        ]
        if period_start and max(active) < period_start:
            continue
        if period_end is not None and min(_number(row.get("started_at"), 0) for row in members) >= period_end:
            continue
        chains.append(_chain_payload(root, members, sessions))
    chains.sort(key=lambda chain: -(chain["cash_usd"] + chain["list_price_usd"]))
    return {
        "chains": chains[:CHAINS_MAX],
        "totals": {
            "chains": len(chains),
            "sessions": sum(chain["sessions"] for chain in chains),
            "cash_usd": round(sum(chain["cash_usd"] for chain in chains), 4),
            "list_price_usd": round(sum(chain["list_price_usd"] for chain in chains), 4),
            "continuations": sum(chain["links"].get("continuation", 0) for chain in chains),
            "subagents": sum(chain["links"].get("subagent", 0) for chain in chains),
            "branches": sum(chain["links"].get("branch", 0) for chain in chains),
        },
        "period_days": days,
        "period": _period_payload(days, period_start, period_end),
        "definition": (
            "Sessions Hermes linked through parent_session_id, walked to the first one. A continuation is the same "
            "chat on the same surface after a reset; a subagent is a delegated session; a branch moved to another "
            "surface. Totals are the whole conversation, including sessions before the period."
        ),
        "generated_at": time.time(),
    }


def _session_chain_sync(session_id: str) -> Dict[str, Any]:
    sessions = _chain_sessions()
    with _database() as db:
        sid = db.resolve_session_id(session_id)
    if not sid or sid not in sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    root = _chain_root(sid, sessions)
    members = [row for row_id, row in sessions.items() if _chain_root(row_id, sessions) == root]
    payload = _chain_payload(root, members, sessions)
    payload["session_id"] = sid
    payload["position"] = next(
        (index + 1 for index, row in enumerate(sorted(members, key=lambda row: _number(row.get("started_at"), 0))) if str(row.get("id")) == sid),
        None,
    )
    return payload


__all__ = [name for name in globals() if not name.startswith("__")]
