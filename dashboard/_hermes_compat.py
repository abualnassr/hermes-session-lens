"""Compatibility boundary for Hermes private and version-sensitive APIs."""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional, Tuple

try:
    from hermes_constants import get_hermes_home
except ImportError:  # pragma: no cover
    get_hermes_home = None  # type: ignore[assignment]

# Hermes before 0.23 searched through hermes_state.SessionDB; 0.23 made it a
# deprecated stand-in and v0.27 removes it. Resolved lazily, only when the
# published reader is missing, so a current Hermes never sees the import.
SessionDB: Any = None


def _legacy_session_db() -> Any:
    if SessionDB is not None:
        return SessionDB
    try:
        from hermes_state import SessionDB as legacy
    except Exception:
        return None
    return legacy

_CAPABILITIES: Dict[str, str] = {
    "database": "available",
    "hermes_home": "available" if get_hermes_home is not None else "fallback",
    "key_resolution": "unknown",
    "provider_state": "unknown",
}


def _hermes_home() -> Path:
    if get_hermes_home is not None:
        try:
            return Path(get_hermes_home())
        except Exception:
            _CAPABILITIES["hermes_home"] = "fallback"
    configured = os.environ.get("HERMES_HOME")
    if configured:
        return Path(configured)
    return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "hermes"


# ── Profile scope ─────────────────────────────────────────────────────────
# A request may ask to read one, several, or all Hermes profiles instead of
# the profile this backend was launched under. The scope is thread-local:
# routes set it for the duration of one synchronous payload build and every
# _database() / _scope_homes() consumer picks it up without new plumbing.

_profile_scope = threading.local()


def _hermes_root() -> Path:
    """The Hermes root that owns all profiles, even when serving one of them."""
    home = _hermes_home()
    parts = [part.lower() for part in home.parts]
    if "profiles" in parts:
        index = parts.index("profiles")
        return Path(*home.parts[:index])
    return home


def _profile_home_path(name: str) -> Path:
    root = _hermes_root()
    return root if name == "default" else root / "profiles" / name


def _discovered_profiles() -> List[str]:
    """Profile names with a state.db, 'default' first."""
    root = _hermes_root()
    names: List[str] = []
    if (root / "state.db").exists():
        names.append("default")
    profiles_root = root / "profiles"
    try:
        if profiles_root.exists():
            for entry in sorted(path for path in profiles_root.iterdir() if path.is_dir()):
                if (entry / "state.db").exists():
                    names.append(entry.name)
    except OSError:
        pass
    return names


def _set_profile_scope(names: Optional[List[str]]) -> None:
    _profile_scope.names = list(names) if names else None


def _get_profile_scope() -> Optional[List[str]]:
    return getattr(_profile_scope, "names", None)


def _scope_homes() -> List[Path]:
    """Home directories the active scope covers; the serving home when unset."""
    names = _get_profile_scope()
    if not names:
        return [_hermes_home()]
    return [_profile_home_path(name) for name in names]


def _scope_db_paths() -> List[Tuple[str, Path]]:
    names = _get_profile_scope()
    if not names:
        return []
    paths = [(name, _profile_home_path(name) / "state.db") for name in names]
    return [(name, path) for name, path in paths if path.exists()]


def _scoped_secret(name: str) -> str:
    """A credential by env-var name, as the profile this request serves sees it.

    Hermes Desktop now serves every local profile from one backend and binds a
    per-request secret scope; os.environ there holds the LAUNCH profile's
    values, so reading it directly would show one profile's balances under
    another. Hermes' get_secret honours the scope and fails closed when none
    is bound — treated as unset, never borrowed. Outside Hermes (tests, the
    standalone smoke runs) the process environment is the only source.
    """
    try:
        from agent.secret_scope import get_secret
    except Exception:
        return str(os.environ.get(name) or "").strip()
    try:
        return str(get_secret(name, "") or "").strip()
    except Exception:
        return ""


def _secret_names() -> List[str]:
    """Env-var NAMES that hold a secret for the profile this request serves.

    Discovery only. Candidates come from Hermes' published secret scope
    (agent.secret_scope.current_secret_scope: the profile's secrets as Hermes
    loaded them) plus the process environment, and a name is kept only when
    Hermes' own get_secret resolves it for this profile, so another profile's
    launch-time variables never leak in under multiplexing. Session Lens never
    opens .env itself (catalog rule 11).
    """
    candidates: set = set()
    try:
        from agent.secret_scope import current_secret_scope

        scope = current_secret_scope()
        if scope:
            candidates.update(str(name) for name in scope.keys())
    except Exception:
        pass
    candidates.update(str(name) for name in os.environ.keys())
    return sorted(
        name for name in candidates if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) and _scoped_secret(name)
    )


def _quote_identifier(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def _is_union_scope(handle: Any) -> bool:
    """True when `handle` (a db or its connection) reads the multi-profile union views."""
    if getattr(handle, "union_profiles", None):
        return True
    execute = getattr(handle, "execute", None)
    if not callable(execute) or not isinstance(handle, sqlite3.Connection):
        return False
    try:
        # Only the union scope shadows `messages`; every connection now gets
        # a `sessions` view for subscription accounting.
        return execute(
            "SELECT 1 FROM sqlite_temp_master WHERE type='view' AND name='messages'"
        ).fetchone() is not None
    except sqlite3.Error:
        return False


def _same_profile(handle: Any, left: str, right: str) -> str:
    """Join term that keeps a union-scope join inside one profile.

    SQLite flattens `messages m JOIN sessions s` over two UNION ALL views into
    one arm per (messages profile x sessions profile) pair — a hundred full
    scans of messages with ten profiles (Tools took 29 s, 2026-10). Matching
    __profile turns every cross-profile arm into a constant-false term SQLite
    skips before scanning. Empty outside the union scope.
    """
    if not _is_union_scope(handle):
        return ""
    return f" AND {left}.__profile = {right}.__profile"


# ── Subscription accounting ─────────────────────────────────────────────
# A model-provider plugin can drive a flat-rate subscription through a local
# CLI — Hermes' claude-subscription-directsdk drives a Claude Pro/Max plan
# through Claude Code. Hermes records the API list-price equivalent of that
# usage as an estimated cost ("native API list-price equivalent; not
# subscription invoice"), and summed as dollars it made one 51-hour Opus
# session 58% of a month's "spend" that was never billed (2026-10). Rows on
# such a route are presented as included: cash cost 0, cost_status
# 'included', and the list-price figure kept as list_price_usd. The rewrite
# lives in the TEMP views every connection reads through, so every reader —
# overview, sessions, budgets, attribution, AI Models — agrees.

SUBSCRIPTION_ROUTE_PATTERNS = ("%subscription%",)
_COST_TABLES = ("sessions", "session_model_usage")


def _subscription_route_sql(present: Iterable[str]) -> str:
    """SQL predicate: this row's billing route is a flat-rate subscription."""
    columns = set(present)
    terms = []
    for column in ("billing_provider", "billing_base_url"):
        if column in columns:
            for pattern in SUBSCRIPTION_ROUTE_PATTERNS:
                literal = pattern.replace("'", "''")
                terms.append(f"lower(coalesce({_quote_identifier(column)},'')) LIKE '{literal}'")
    return "(" + " OR ".join(terms) + ")" if terms else "0"


def _accounting_select_list(table: str, ordered: List[str], present: Iterable[str]) -> List[str]:
    """Select expressions for `ordered` columns of `table`, NULL where absent,
    with subscription-routed cost presented as included."""
    present = set(present)
    rewrite = table in _COST_TABLES
    route = _subscription_route_sql(present) if rewrite else "0"
    expressions = []
    for column in ordered:
        name = _quote_identifier(column)
        if column not in present:
            expressions.append(f"NULL AS {name}")
        elif rewrite and column in ("estimated_cost_usd", "actual_cost_usd"):
            expressions.append(f"CASE WHEN {route} THEN 0 ELSE {name} END AS {name}")
        elif rewrite and column == "cost_status":
            expressions.append(f"CASE WHEN {route} THEN 'included' ELSE {name} END AS {name}")
        else:
            expressions.append(name)
    if rewrite:
        actual = '"actual_cost_usd"' if "actual_cost_usd" in present else "NULL"
        estimated = '"estimated_cost_usd"' if "estimated_cost_usd" in present else "NULL"
        expressions.append(
            f"CASE WHEN {route} THEN coalesce(nullif({actual},0), {estimated}) END AS \"list_price_usd\""
        )
    return expressions


def _install_accounting_views(connection: Any) -> None:
    """Shadow sessions and session_model_usage on a single-profile connection
    with TEMP views carrying the subscription rewrite. TEMP objects live only
    on this private connection and vanish when it closes; a failure leaves
    the raw tables in place (costs then read as Hermes recorded them)."""
    for table in _COST_TABLES:
        try:
            exists = connection.execute(
                "SELECT 1 FROM sqlite_temp_master WHERE type='view' AND name=?", (table,)
            ).fetchone()
            if exists:
                continue
            columns = [str(row[1]) for row in connection.execute(f"PRAGMA main.table_info({table})").fetchall()]
            if not columns:
                continue
            select = ", ".join(_accounting_select_list(table, columns, columns))
            connection.execute(f"CREATE TEMP VIEW {table} AS SELECT {select} FROM main.{table}")
        except sqlite3.Error:
            continue


def _read_only_uri(path: Path) -> str:
    """`file:` URI for a mode=ro open. Hermes publishes this helper; the local
    fallback matches it (as_uri() percent-encodes '?'/'#' in a home path, which
    a raw f"file:{path}?mode=ro" would truncate at)."""
    try:
        from sqlite_support import read_only_db_uri

        return str(read_only_db_uri(path))
    except Exception:
        return Path(path).resolve().as_uri() + "?mode=ro"


def _default_db_path() -> Path:
    """The serving profile's state.db, located the way Hermes itself does."""
    try:
        from persistence_assembly import profile_scope, store_location

        located = store_location(profile_scope())
        if located:
            return Path(located)
    except Exception:
        pass
    return _hermes_home() / "state.db"


def _search_one_store(path: Path, query: str, limit: int, fields: Tuple[str, ...]) -> List[Dict[str, Any]]:
    """Full-text hits from one state.db through Hermes' own search, which owns
    the FTS schema. Hermes 0.23+ publishes a read-only store handle; older
    builds expose the same search on SessionDB."""
    try:
        from persistence_assembly import open_persistence_reader
    except ImportError:
        open_persistence_reader = None  # type: ignore[assignment]
    if open_persistence_reader is not None:
        with open_persistence_reader(Path(path)) as store:
            hits = store.search.search_messages(query, limit=limit, fields=fields or None)
            rows: List[Dict[str, Any]] = []
            for hit in hits or []:
                rows.append({
                    name: (getattr(hit, "message_id", None) if name == "id" else getattr(hit, name, None))
                    for name in (fields or ("session_id", "snippet", "role"))
                })
            return rows
    legacy = _legacy_session_db()
    if legacy is None:
        return []
    db = legacy(db_path=path, read_only=True)
    try:
        return list(db.search_messages(query=query, limit=limit, fields=fields) or [])
    finally:
        try:
            db.close()
        except Exception:
            pass


class _DirectDB:
    """Read-only connection of this plugin's own to one profile's state.db.

    Hermes 0.23 turned `hermes_state.SessionDB` into a deprecated stand-in over
    its storage interface with no SQL connection behind it (removed in v0.27).
    Every reader here is SQL, so the plugin opens the file itself with mode=ro,
    as the multi-profile union always has. It never writes.
    """

    read_only = True

    def __init__(self, path: Path):
        self.db_path = Path(path)
        self._named_paths: List[Tuple[str, Path]] = [("", self.db_path)]
        self._conn = sqlite3.connect(_read_only_uri(self.db_path), uri=True)
        self._conn.row_factory = sqlite3.Row

    def resolve_session_id(self, session_id: Any) -> Optional[str]:
        sid = str(session_id or "").strip()
        if not sid:
            return None
        row = self._conn.execute("SELECT id FROM sessions WHERE id = ?", (sid,)).fetchone()
        if row:
            return str(row["id"])
        rows = self._conn.execute(
            "SELECT DISTINCT id FROM sessions WHERE id LIKE ? LIMIT 2", (sid + "%",)
        ).fetchall()
        return str(rows[0]["id"]) if len(rows) == 1 else None

    def search_messages(self, *, query: str, limit: int = 20, fields: Tuple[str, ...] = ()) -> List[Any]:
        results: List[Any] = []
        for _name, path in self._named_paths:
            try:
                hits = _search_one_store(path, query, limit, tuple(fields))
            except Exception:
                continue
            for row in hits:
                results.append(row)
                if len(results) >= limit:
                    return results
        return results

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass


class _UnionDB(_DirectDB):
    """Read-only view over several profiles' state.db files.

    Attaches every database with mode=ro and shadows the shared tables with
    TEMP views that UNION ALL across profiles, so the existing single-profile
    SQL keeps working unchanged. Each view row carries an extra __profile
    column naming its source profile.
    """

    _UNION_TABLES = ("sessions", "messages", "session_model_usage", "async_delegations")
    read_only = True

    def __init__(self, named_paths: List[Tuple[str, Path]]):
        self._named_paths = list(named_paths)
        self.union_profiles = [name for name, _path in named_paths]
        self.db_path = named_paths[0][1]
        self._conn = sqlite3.connect(_read_only_uri(named_paths[0][1]), uri=True)
        self._conn.row_factory = sqlite3.Row
        aliases = ["main"]
        for index, (_name, path) in enumerate(named_paths[1:], start=1):
            alias = f"p{index}"
            self._conn.execute(f"ATTACH DATABASE ? AS {alias}", (_read_only_uri(path),))
            aliases.append(alias)
        for table in self._UNION_TABLES:
            # UNION ALL matches columns by position, and two state.db files
            # rarely agree on position: a column Hermes adds with ALTER TABLE
            # lands at the end of a migrated database but inline in a
            # database created later. Project every profile onto one named
            # column list (NULL where a profile lacks the column) so a row's
            # started_at is always its started_at.
            columns_by_alias: Dict[str, List[str]] = {}
            for alias in aliases:
                if self._table_exists(alias, table):
                    columns_by_alias[alias] = self._table_columns(alias, table)
            ordered: List[str] = []
            for columns in columns_by_alias.values():
                for column in columns:
                    if column not in ordered:
                        ordered.append(column)
            selects = []
            for alias, (name, _path) in zip(aliases, named_paths):
                columns = columns_by_alias.get(alias)
                if columns is None:
                    continue
                projected = ", ".join(_accounting_select_list(table, ordered, columns))
                literal = str(name).replace("'", "''")
                selects.append(f"SELECT {projected}, '{literal}' AS __profile FROM {alias}.{table}")
            if selects:
                self._conn.execute(f"CREATE TEMP VIEW {table} AS " + " UNION ALL ".join(selects))

    def _table_exists(self, alias: str, table: str) -> bool:
        try:
            row = self._conn.execute(
                f"SELECT name FROM {alias}.sqlite_master WHERE type IN ('table','view') AND name = ?",
                (table,),
            ).fetchone()
        except sqlite3.Error:
            return False
        return row is not None

    def _table_columns(self, alias: str, table: str) -> List[str]:
        try:
            rows = self._conn.execute(f"PRAGMA {alias}.table_info({table})").fetchall()
        except sqlite3.Error:
            return []
        return [str(row[1]) for row in rows]


# ── Route time budget ─────────────────────────────────────────────────────
# One slow payload build must never take Hermes down with it. The desktop
# backend is a single process, and a thread that grinds for minutes starves
# every request behind it — the gateway indicator included (2026-09-06, a
# backtracking regex in the classifier). Every payload builder therefore
# runs under a deadline: SQLite statements are interrupted through a
# progress handler, long Python loops call _check_route_budget() between
# units of work, and the route answers 503 with a sentence that names the
# budget and how to change it. Read-only throughout; nothing is cancelled
# except our own work.

DEFAULT_ROUTE_BUDGET_SECONDS = 25.0


class RouteBudgetExceeded(RuntimeError):
    def __init__(self, limit: float, elapsed: float) -> None:
        super().__init__(f"route budget of {limit:g}s exceeded after {elapsed:.1f}s")
        self.limit = limit
        self.elapsed = elapsed


_route_budget = threading.local()


def _route_budget_seconds(settings: Optional[Mapping[str, Any]] = None) -> float:
    """Seconds one payload build may take; 0 disables the budget."""
    raw = (settings if settings is not None else _plugin_settings()).get(
        "route_budget_seconds", DEFAULT_ROUTE_BUDGET_SECONDS
    )
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = DEFAULT_ROUTE_BUDGET_SECONDS
    if value != value:  # NaN
        value = DEFAULT_ROUTE_BUDGET_SECONDS
    return max(0.0, value)


@contextmanager
def _route_budget_scope(seconds: float) -> Iterator[None]:
    previous = (
        getattr(_route_budget, "deadline", None),
        getattr(_route_budget, "limit", 0.0),
        getattr(_route_budget, "started", None),
    )
    now = time.monotonic()
    _route_budget.started = now
    _route_budget.limit = float(seconds)
    _route_budget.deadline = now + seconds if seconds > 0 else None
    try:
        yield
    finally:
        _route_budget.deadline, _route_budget.limit, _route_budget.started = previous


def _route_budget_elapsed() -> float:
    started = getattr(_route_budget, "started", None)
    return time.monotonic() - started if started is not None else 0.0


def _route_budget_exhausted() -> bool:
    deadline = getattr(_route_budget, "deadline", None)
    return deadline is not None and time.monotonic() > deadline


def _check_route_budget() -> None:
    """Call between units of work in a long Python loop."""
    if _route_budget_exhausted():
        raise RouteBudgetExceeded(getattr(_route_budget, "limit", 0.0), _route_budget_elapsed())


def _route_budget_interrupted(exc: BaseException) -> bool:
    """True when `exc` is SQLite reporting the interrupt our progress handler raised."""
    return (
        isinstance(exc, sqlite3.OperationalError)
        and "interrupt" in str(exc).lower()
        and _route_budget_exhausted()
    )


def _arm_route_budget(connection: Any) -> bool:
    deadline = getattr(_route_budget, "deadline", None)
    handler_setter = getattr(connection, "set_progress_handler", None)
    if deadline is None or not callable(handler_setter):
        return False

    def interrupt_when_late() -> int:
        return 1 if time.monotonic() > deadline else 0

    try:
        handler_setter(interrupt_when_late, 20_000)
    except Exception:
        return False
    return True


def _disarm_route_budget(connection: Any) -> None:
    handler_setter = getattr(connection, "set_progress_handler", None)
    if callable(handler_setter):
        try:
            handler_setter(None, 0)
        except Exception:
            pass


@contextmanager
def _database(db_path: Optional[Path] = None) -> Iterator[Any]:
    if db_path is None:
        scoped = _scope_db_paths()
        if len(scoped) > 1:
            union = _UnionDB(scoped)
            armed = _arm_route_budget(union._conn)
            try:
                yield union
            finally:
                if armed:
                    _disarm_route_budget(union._conn)
                union.close()
            return
        db_path = scoped[0][1] if scoped else _default_db_path()
    if not Path(db_path).exists():
        # mode=ro never creates a database; say so plainly instead of
        # surfacing sqlite's "unable to open database file".
        _CAPABILITIES["database"] = "unavailable"
        raise RuntimeError(f"No Hermes session database at {db_path}")
    db = _DirectDB(Path(db_path))
    _CAPABILITIES["database"] = "available"
    connection = db._conn
    _install_accounting_views(connection)
    armed = _arm_route_budget(connection)
    try:
        yield db
    finally:
        if armed:
            _disarm_route_budget(connection)
        db.close()


_ACCOUNTED_TOKEN_COLUMNS = (
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "reasoning_tokens",
)


def _accounted_sessions_sql(connection: Any) -> str:
    """A SELECT over `sessions` whose token, call and cost columns are the
    higher of Hermes' two records per session.

    Hermes keeps a running total on the session row and one row per model in
    session_model_usage. On long sessions the session row lags — a bot
    session read $1.38 there and $1.98 in its usage rows — so every view
    that reads session-level accounting takes the larger figure, column by
    column. All other columns pass through unchanged, so a query can swap
    `FROM sessions` for `FROM (<this>) alias` and keep its SQL. Read-only.
    """
    try:
        columns = [str(row[1]) for row in connection.execute("PRAGMA table_info(sessions)").fetchall()]
    except Exception:
        columns = []
    if not columns:
        return "SELECT * FROM sessions"
    has_usage = True
    try:
        has_usage = bool(connection.execute("PRAGMA table_info(session_model_usage)").fetchall())
    except Exception:
        has_usage = False
    overridden = set(_ACCOUNTED_TOKEN_COLUMNS) | {"api_call_count", "actual_cost_usd", "estimated_cost_usd"}
    passthrough = ", ".join(f's."{column}"' for column in columns if column not in overridden)
    if not has_usage:
        return "SELECT * FROM sessions"
    computed = []
    for column in _ACCOUNTED_TOKEN_COLUMNS:
        if column in columns:
            computed.append(f'max(coalesce(s."{column}",0), coalesce(u."{column}",0)) AS "{column}"')
    if "api_call_count" in columns:
        computed.append('max(coalesce(s."api_call_count",0), coalesce(u."api_call_count",0)) AS "api_call_count"')
    for column in ("actual_cost_usd", "estimated_cost_usd"):
        if column in columns:
            computed.append(
                f'CASE WHEN coalesce(u."{column}",0) > coalesce(s."{column}",0) THEN u."{column}" '
                f'ELSE s."{column}" END AS "{column}"'
            )
    usage_totals = (
        "SELECT session_id, "
        + ", ".join(f'SUM(coalesce("{column}",0)) AS "{column}"' for column in _ACCOUNTED_TOKEN_COLUMNS)
        + ', SUM(coalesce("api_call_count",0)) AS "api_call_count"'
        + ', SUM(CASE WHEN "actual_cost_usd" > 0 THEN "actual_cost_usd" ELSE 0 END) AS "actual_cost_usd"'
        + ', SUM(CASE WHEN "estimated_cost_usd" > 0 THEN "estimated_cost_usd" ELSE 0 END) AS "estimated_cost_usd"'
        + " FROM session_model_usage GROUP BY session_id"
    )
    return (
        f"SELECT {passthrough}, {', '.join(computed)} FROM sessions s "
        f"LEFT JOIN ({usage_totals}) u ON u.session_id = s.id"
    )


def _db_connection(db: Any) -> Any:
    connection = getattr(db, "_conn", None)
    if connection is None:
        _CAPABILITIES["database"] = "degraded"
        raise RuntimeError("This database handle has no SQL connection")
    return connection


# ── Credentials: only through Hermes' published routes (catalog rule 11) ──
# Every value is read with agent.secret_scope.get_secret (via _scoped_secret),
# which honours the profile a request serves. Subscription sign-ins (Codex,
# Claude, Nous, xAI) are never held here: Hermes' own account-usage code
# (agent.account_usage) or Hermes' gateway (model.options, called by the
# desktop) reads them. Session Lens never opens .env, auth.json or another
# tool's login, and never refreshes, rotates or writes a credential.

# Hermes' API-key providers: id -> the env names Hermes reads for it. Mirrors
# Hermes' provider table (hermes_cli.auth is not a published API) so the
# "configured in Hermes, not monitorable" line can name them. The names a
# Session Lens adapter actually reads are declared under optional_env.
_HERMES_KEY_PROVIDERS: Dict[str, Tuple[str, ...]] = {
    "openai-api": ('OPENAI_API_KEY',),
    "lmstudio": ('LM_API_KEY',),
    "gemini": ('GOOGLE_API_KEY', 'GEMINI_API_KEY'),
    "zai": ('GLM_API_KEY', 'ZAI_API_KEY', 'Z_AI_API_KEY'),
    "kimi-coding": ('KIMI_API_KEY', 'KIMI_CODING_API_KEY'),
    "kimi-coding-cn": ('KIMI_CN_API_KEY',),
    "stepfun": ('STEPFUN_API_KEY',),
    "arcee": ('ARCEEAI_API_KEY',),
    "gmi": ('GMI_API_KEY',),
    "actual": ('ACTUAL_API_KEY',),
    "minimax": ('MINIMAX_API_KEY',),
    "anthropic": ('ANTHROPIC_API_KEY', 'ANTHROPIC_TOKEN', 'CLAUDE_CODE_OAUTH_TOKEN'),
    "alibaba": ('DASHSCOPE_API_KEY',),
    "alibaba-coding-plan": ('ALIBABA_CODING_PLAN_API_KEY', 'DASHSCOPE_API_KEY'),
    "minimax-cn": ('MINIMAX_CN_API_KEY',),
    "deepseek": ('DEEPSEEK_API_KEY',),
    "xai": ('XAI_API_KEY',),
    "nvidia": ('NVIDIA_API_KEY',),
    "ai-gateway": ('AI_GATEWAY_API_KEY',),
    "opencode-zen": ('OPENCODE_ZEN_API_KEY',),
    "opencode-go": ('OPENCODE_GO_API_KEY',),
    "kilocode": ('KILOCODE_API_KEY',),
    "huggingface": ('HF_TOKEN',),
    "xiaomi": ('XIAOMI_API_KEY',),
    "tencent-tokenhub": ('TOKENHUB_API_KEY',),
    "tencent-tokenplan": ('TOKENPLAN_API_KEY',),
    "ollama-cloud": ('OLLAMA_API_KEY',),
    "azure-foundry": ('AZURE_FOUNDRY_API_KEY',),
    "alibaba-token-plan": ('ALIBABA_TOKEN_PLAN_API_KEY',),
    "alibaba-token-plan-cn": ('ALIBABA_TOKEN_PLAN_CN_API_KEY', 'ALIBABA_TOKEN_PLAN_API_KEY'),
    "alibaba-coding-plan-cn": ('ALIBABA_CODING_PLAN_CN_API_KEY', 'ALIBABA_CODING_PLAN_API_KEY', 'DASHSCOPE_API_KEY'),
    "commandcode": ('COMMANDCODE_API_KEY',),
    "deepinfra": ('DEEPINFRA_API_KEY',),
    "fireworks": ('FIREWORKS_API_KEY',),
    "meta-ai": ('MODEL_API_KEY', 'META_API_KEY', 'META_MODEL_API_KEY'),
    "nebius": ('NEBIUS_API_KEY', 'NEBIUS_TOKEN_FACTORY_API_KEY'),
    "novita": ('NOVITA_API_KEY',),
    "ramp-router": ('RAMP_ROUTER_API_KEY', 'ROUTER_API_KEY'),
    "upstage": ('UPSTAGE_API_KEY',),
    "openrouter": ('OPENROUTER_API_KEY',),
}

# The API-key providers Session Lens reads a balance for: (base-URL override
# env name, default base URL), as Hermes resolves them.
_API_KEY_PROVIDER_BASES: Dict[str, Tuple[str, str]] = {
    "deepseek": ("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
    "kimi-coding": ("KIMI_BASE_URL", "https://api.moonshot.ai/v1"),
    "zai": ("GLM_BASE_URL", "https://api.z.ai/api/paas/v4"),
    "openrouter": ("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
}
_KIMI_CODE_BASE_URL = "https://api.kimi.com/coding/v1"


def _resolve_hermes_api_key(provider_id: str) -> Tuple[str, str]:
    """(key, base URL) for an API-key provider, through Hermes' secret scope only."""
    names = _HERMES_KEY_PROVIDERS.get(provider_id)
    base_spec = _API_KEY_PROVIDER_BASES.get(provider_id)
    if not names or base_spec is None:
        _CAPABILITIES["key_resolution"] = "unavailable"
        raise RuntimeError(f"Session Lens does not read a key for provider {provider_id!r}.")
    token = next((value for value in (_scoped_secret(name) for name in names) if value), "")
    override_env, default_url = base_spec
    override = _scoped_secret(override_env)
    base_url = override or default_url
    # Hermes routes sk-kimi- (Kimi Code) keys to api.kimi.com unless KIMI_BASE_URL says otherwise.
    if provider_id == "kimi-coding" and not override and token.startswith("sk-kimi-"):
        base_url = _KIMI_CODE_BASE_URL
    _CAPABILITIES["key_resolution"] = "available"
    return token, base_url.strip().rstrip("/")


def _plugin_settings() -> Dict[str, Any]:
    """This plugin's ``settings`` block from Hermes' config, read-only ({} outside Hermes)."""
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


def _secret_kind_present(*names: str) -> List[Tuple[str, str]]:
    """(env name, value) for each of `names` Hermes resolves for this profile."""
    found: List[Tuple[str, str]] = []
    for name in names:
        value = _scoped_secret(name)
        if value:
            found.append((name, value))
    return found


def _hermes_configured_provider_ids() -> List[str]:
    """API-key provider ids whose key Hermes resolves for this profile."""
    return [provider_id for provider_id, names in _HERMES_KEY_PROVIDERS.items() if any(_scoped_secret(name) for name in names)]


def _hermes_provider_credential_keys() -> Dict[str, str]:
    """provider id -> the credential it reads, so several endpoints on one key count once."""
    return {provider_id: ",".join(sorted(names)) for provider_id, names in _HERMES_KEY_PROVIDERS.items()}


def _hermes_account_usage(provider: str) -> Any:
    """Hermes' own account-usage snapshot for `provider`, or None.

    agent.account_usage is a published plugin API: Hermes resolves and holds
    the credential, makes the request, and caches the result for its own
    model picker. read_only=True keeps a failed read from rotating or
    repairing a pooled credential. None when Hermes cannot read the provider.
    """
    try:
        from agent.account_usage import fetch_account_usage
    except Exception:
        return None
    try:
        return fetch_account_usage(provider, read_only=True)
    except TypeError:
        try:
            return fetch_account_usage(provider)
        except Exception:
            return None
    except Exception:
        return None


def _compat_capabilities() -> Dict[str, str]:
    return dict(_CAPABILITIES)


__all__ = [name for name in globals() if not name.startswith("__")]
