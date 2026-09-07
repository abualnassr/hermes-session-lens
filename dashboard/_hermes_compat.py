"""Compatibility boundary for Hermes private and version-sensitive APIs."""

from __future__ import annotations

import hashlib
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Mapping, Optional, Tuple

try:
    from hermes_constants import get_hermes_home
    from hermes_state import SessionDB
except ImportError:  # pragma: no cover
    get_hermes_home = None  # type: ignore[assignment]
    SessionDB = None  # type: ignore[assignment,misc]

_CAPABILITIES: Dict[str, str] = {
    "database": "available" if SessionDB is not None else "unavailable",
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


def _quote_identifier(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


class _UnionDB:
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
        self._conn = sqlite3.connect(f"file:{named_paths[0][1].as_posix()}?mode=ro", uri=True)
        self._conn.row_factory = sqlite3.Row
        aliases = ["main"]
        for index, (_name, path) in enumerate(named_paths[1:], start=1):
            alias = f"p{index}"
            self._conn.execute(f"ATTACH DATABASE ? AS {alias}", (f"file:{path.as_posix()}?mode=ro",))
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
                present = set(columns)
                projected = ", ".join(
                    _quote_identifier(column) if column in present
                    else f"NULL AS {_quote_identifier(column)}"
                    for column in ordered
                )
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
        if SessionDB is None:
            return results
        for _name, path in self._named_paths:
            try:
                db = SessionDB(db_path=path, read_only=True)
            except Exception:
                continue
            try:
                for row in db.search_messages(query=query, limit=limit, fields=fields) or []:
                    results.append(row)
                    if len(results) >= limit:
                        return results
            except Exception:
                pass
            finally:
                try:
                    db.close()
                except Exception:
                    pass
        return results

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass


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

DEFAULT_ROUTE_BUDGET_SECONDS = 30.0


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
    if SessionDB is None:
        _CAPABILITIES["database"] = "unavailable"
        raise RuntimeError("Hermes SessionDB is unavailable in this process")
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
        if scoped:
            db_path = scoped[0][1]
    db = SessionDB(db_path=db_path, read_only=True) if db_path else SessionDB(read_only=True)
    connection = getattr(db, "_conn", None)
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
        raise RuntimeError("This Hermes version does not expose the session connection")
    return connection


def _resolve_hermes_api_key(provider_id: str) -> Tuple[str, str]:
    """Resolve a configured provider key without triggering inference probes."""
    try:
        from hermes_cli import auth as hermes_auth

        pconfig = hermes_auth.PROVIDER_REGISTRY.get(provider_id)
        secret_resolver = getattr(hermes_auth, "_resolve_api_key_provider_secret", None)
        if pconfig is None or not callable(secret_resolver):
            _CAPABILITIES["key_resolution"] = "unavailable"
            raise RuntimeError("This Hermes version does not expose safe API-key resolution.")
        token, _source = secret_resolver(provider_id, pconfig)
        status = hermes_auth.get_api_key_provider_status(provider_id)
        base_url = str(status.get("base_url") or pconfig.inference_base_url or "").strip().rstrip("/")
        _CAPABILITIES["key_resolution"] = "available"

        if provider_id == "zai" and token:
            try:
                load_auth_store = getattr(hermes_auth, "_load_auth_store", None)
                load_provider_state = getattr(hermes_auth, "_load_provider_state", None)
                if not callable(load_auth_store) or not callable(load_provider_state):
                    _CAPABILITIES["provider_state"] = "unavailable"
                else:
                    auth_store = load_auth_store()
                    state = load_provider_state(auth_store, "zai") or {}
                    detected = state.get("detected_endpoint") or {}
                    expected_hash = hashlib.sha256(str(token).encode()).hexdigest()[:16]
                    if detected.get("key_hash") == expected_hash and detected.get("base_url"):
                        base_url = str(detected["base_url"]).strip().rstrip("/")
                    _CAPABILITIES["provider_state"] = "available"
            except Exception:
                _CAPABILITIES["provider_state"] = "degraded"
        return str(token or "").strip(), base_url
    except RuntimeError:
        raise
    except Exception as error:
        _CAPABILITIES["key_resolution"] = "unavailable"
        raise RuntimeError("Hermes API-key resolution is unavailable") from error


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


_ANTHROPIC_ENV_KEYS = ("ANTHROPIC_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY")


def _anthropic_env_credentials() -> List[Tuple[str, str]]:
    """(env name, value) for every Anthropic credential set in the environment.

    Reads through Hermes' profile-scoped secret reader — the same one its
    Anthropic resolver uses — so a multiplexed profile sees exactly what
    Hermes sees; plain os.environ outside Hermes. Read-only.
    """
    getenv = None
    try:
        from agent import anthropic_credentials

        getenv = anthropic_credentials._getenv
    except Exception:
        getenv = None
    found: List[Tuple[str, str]] = []
    for name in _ANTHROPIC_ENV_KEYS:
        try:
            value = str(getenv(name) or "") if getenv is not None else str(os.environ.get(name) or "")
        except Exception:
            value = ""
        value = value.strip()
        if value:
            found.append((name, value))
    return found


def _hermes_anthropic_credentials() -> Any:
    """Hermes' Anthropic credential helpers, wherever this Hermes keeps them.

    Hermes moved resolve_anthropic_token, _resolve_anthropic_pool_token and
    _is_oauth_token out of agent.anthropic_adapter into
    agent.anthropic_credentials; the adapter re-exports the public name with
    a deprecation warning until 2026-09-14 and the private ones not at all.
    Prefer the new module and fall back to the adapter for older builds.
    """
    try:
        from agent import anthropic_credentials

        if hasattr(anthropic_credentials, "resolve_anthropic_token"):
            return anthropic_credentials
    except ImportError:
        pass
    from agent import anthropic_adapter

    return anthropic_adapter


def _resolve_anthropic_oauth() -> Tuple[str, bool]:
    try:
        credentials = _hermes_anthropic_credentials()

        token = str(credentials.resolve_anthropic_token() or "").strip()
        token_check = getattr(credentials, "_is_oauth_token", None)
        if not callable(token_check):
            return token, False
        return token, bool(token_check(token))
    except Exception:
        return "", False


def _resolve_anthropic_pool_oauth() -> str:
    """OAuth token from Hermes' Anthropic credential pool, read directly.

    Hermes' resolver lets an explicit ANTHROPIC_API_KEY shadow saved OAuth
    logins, but the account-usage endpoint only accepts OAuth — so the
    collector needs the pool login even when the resolver returns an API key.
    Empty string when no OAuth login is stored.
    """
    try:
        credentials = _hermes_anthropic_credentials()

        token = str(credentials._resolve_anthropic_pool_token() or "").strip()
        if token and credentials._is_oauth_token(token):
            return token
    except Exception:
        pass
    return ""


def _anthropic_pool_oauth_accounts() -> List[Dict[str, str]]:
    """All Anthropic OAuth logins in Hermes' credential pool, read-only.

    One dict per stored account: {"label", "token"}. Enumerates with
    clear_expired=False, refresh=False — the same contract as
    _resolve_anthropic_pool_oauth — so listing accounts never mutates
    auth.json or triggers a network refresh. Returns [] outside Hermes.
    """
    accounts: List[Dict[str, str]] = []
    try:
        credentials = _hermes_anthropic_credentials()
        from agent.credential_pool import AUTH_TYPE_OAUTH, load_pool

        pool = load_pool("anthropic")
        entries, _pending = pool._available_entries(clear_expired=False, refresh=False)
        for entry in entries:
            if getattr(entry, "auth_type", None) != AUTH_TYPE_OAUTH:
                continue
            token = str(getattr(entry, "access_token", "") or "").strip()
            if not token or not credentials._is_oauth_token(token):
                continue
            label = str(getattr(entry, "label", "") or "").strip() or str(getattr(entry, "id", "") or "")[:8]
            accounts.append({"label": label[:60], "token": token})
    except Exception:
        return accounts
    return accounts


def _resolve_anthropic_claude_code_oauth() -> str:
    """Fresh OAuth token from Claude Code's credential store, read-only.

    Claude Code refreshes its own token during normal use, so on a machine
    where Claude Code runs regularly this is the most reliably fresh
    Anthropic OAuth available. Never refreshes: an expired record returns ""
    rather than racing Hermes or Claude Code for the single-use refresh
    token (a lost race kills the login with refresh_token_reused).
    """
    try:
        from agent import anthropic_credentials

        creds = anthropic_credentials.read_claude_code_credentials()
        if creds and anthropic_credentials.is_claude_code_token_valid(creds):
            token = str(creds.get("accessToken") or "").strip()
            if token and anthropic_credentials._is_oauth_token(token):
                return token
    except Exception:
        pass
    return ""


def _hermes_configured_provider_ids() -> List[str]:
    """Provider ids Hermes holds credentials for, from the live PROVIDER_REGISTRY.

    Model-provider plugins register ProviderProfiles into this registry (see
    the Hermes developer guide), so a third-party provider the user installs
    shows up here without Session Lens code changes. Local-only: API keys via
    the same safe resolver `_resolve_hermes_api_key` uses, OAuth-style
    providers via stored auth state. Returns [] outside Hermes.
    """
    configured: List[str] = []
    try:
        from hermes_cli import auth as hermes_auth

        registry = getattr(hermes_auth, "PROVIDER_REGISTRY", None) or {}
        secret_resolver = getattr(hermes_auth, "_resolve_api_key_provider_secret", None)
        load_auth_store = getattr(hermes_auth, "_load_auth_store", None)
        load_provider_state = getattr(hermes_auth, "_load_provider_state", None)
        auth_store = None
        if callable(load_auth_store):
            try:
                auth_store = load_auth_store()
            except Exception:
                auth_store = None
        for provider_id, pconfig in registry.items():
            try:
                if str(getattr(pconfig, "auth_type", "") or "") == "api_key":
                    if not callable(secret_resolver):
                        continue
                    token, _source = secret_resolver(provider_id, pconfig)
                    if str(token or "").strip():
                        configured.append(str(provider_id))
                elif auth_store is not None and callable(load_provider_state):
                    if load_provider_state(auth_store, provider_id):
                        configured.append(str(provider_id))
            except Exception:
                continue
    except Exception:
        return []
    return configured


def _compat_capabilities() -> Dict[str, str]:
    return dict(_CAPABILITIES)


__all__ = [name for name in globals() if not name.startswith("__")]
