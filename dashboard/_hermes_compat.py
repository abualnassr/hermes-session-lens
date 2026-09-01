"""Compatibility boundary for Hermes private and version-sensitive APIs."""

from __future__ import annotations

import hashlib
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

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


@contextmanager
def _database(db_path: Optional[Path] = None) -> Iterator[Any]:
    if SessionDB is None:
        _CAPABILITIES["database"] = "unavailable"
        raise RuntimeError("Hermes SessionDB is unavailable in this process")
    db = SessionDB(db_path=db_path, read_only=True) if db_path else SessionDB(read_only=True)
    try:
        yield db
    finally:
        db.close()


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


def _resolve_anthropic_oauth() -> Tuple[str, bool]:
    try:
        from agent import anthropic_adapter

        token = str(anthropic_adapter.resolve_anthropic_token() or "").strip()
        token_check = getattr(anthropic_adapter, "_is_oauth_token", None)
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
        from agent import anthropic_adapter

        token = str(anthropic_adapter._resolve_anthropic_pool_token() or "").strip()
        if token and anthropic_adapter._is_oauth_token(token):
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
        from agent import anthropic_adapter
        from agent.credential_pool import AUTH_TYPE_OAUTH, load_pool

        pool = load_pool("anthropic")
        entries, _pending = pool._available_entries(clear_expired=False, refresh=False)
        for entry in entries:
            if getattr(entry, "auth_type", None) != AUTH_TYPE_OAUTH:
                continue
            token = str(getattr(entry, "access_token", "") or "").strip()
            if not token or not anthropic_adapter._is_oauth_token(token):
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
