"""Anthropic quota provider adapter.

Two Anthropic products can be configured in Hermes at the same time, and they
answer different questions, so each becomes its own card:

* a Claude **subscription** login — a ``sk-ant-oat`` setup token in
  ``CLAUDE_CODE_OAUTH_TOKEN``/``ANTHROPIC_TOKEN``, a Claude Code login, or a
  Hermes pool login — has 5-hour and 7-day allowance windows;
* a Console **API key** (``sk-ant-api``) is pay-per-token with per-minute
  request and token rate limits.

Anthropic's account-usage endpoint (``/api/oauth/usage``) answers only OAuth
logins that carry the ``user:profile`` scope. Setup tokens carry
``user:inference`` alone and API keys are not OAuth at all, so for them the
only readable source is the ``anthropic-ratelimit-*`` headers Anthropic
attaches to every message response. The collector therefore sends ONE
one-token message to Claude Haiku per credential, at most once per
``ANTHROPIC_PROBE_TTL_SECONDS`` (a manual refresh bypasses the cache), and
reads the headers. That is the only inference request Session Lens makes: the
adapter declares it as ``request_kind="inference_probe"``, the README Trust
section states it, and ``anthropic_usage_probe: false`` in the plugin
settings turns it off (the usage endpoint is still tried for full logins).

Nothing here refreshes, rotates, or writes a credential.
"""

from __future__ import annotations

import copy
import hashlib
import threading
import time

try:
    from .._common import *
    from .._hermes_compat import *
    from .shared import *
except ImportError:  # pragma: no cover
    from _common import *
    from _hermes_compat import *
    from _providers.shared import *

_ANTHROPIC_USAGE_WINDOWS = (
    ("five_hour", "Current session"),
    ("seven_day", "Current week"),
    ("seven_day_opus", "Opus week"),
    ("seven_day_sonnet", "Sonnet week"),
)

_ANTHROPIC_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
_ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"
_ANTHROPIC_PROBE_MODEL = "claude-haiku-4-5-20251001"
_ANTHROPIC_MAX_CREDENTIALS = 6
_ANTHROPIC_CLAUDE_CODE_SYSTEM_PREFIX = "You are Claude Code, Anthropic's official CLI for Claude."
_ANTHROPIC_CLAUDE_CODE_VERSION_FALLBACK = "2.1.74"
_ANTHROPIC_OAUTH_BETAS_FALLBACK = (
    "interleaved-thinking-2025-05-14",
    "fine-grained-tool-streaming-2025-05-14",
    "claude-code-20250219",
    "oauth-2025-04-20",
)
_ANTHROPIC_ENV_SOURCES = {
    "ANTHROPIC_TOKEN": "OAuth token (ANTHROPIC_TOKEN)",
    "CLAUDE_CODE_OAUTH_TOKEN": "OAuth token (CLAUDE_CODE_OAUTH_TOKEN)",
    "ANTHROPIC_API_KEY": "API key (ANTHROPIC_API_KEY)",
}
_ANTHROPIC_SUBSCRIPTION_LABEL = "Anthropic Claude"
_ANTHROPIC_API_LABEL = "Anthropic API (console)"
_ANTHROPIC_PROBE_OFF_MESSAGE = (
    "The Anthropic usage probe is turned off (plugins.entries.session-lens.settings.anthropic_usage_probe). "
    "Session Lens reads this credential's allowance from the headers of a one-token message; turn the probe on to read it."
)
_ANTHROPIC_RATE_LIMITS = (
    ("requests", "Requests per minute", "requests"),
    ("input-tokens", "Input tokens per minute", "tokens"),
    ("output-tokens", "Output tokens per minute", "tokens"),
    ("tokens", "Tokens per minute", "tokens"),
)


# ── Credential inventory (read-only) ──────────────────────────────────────


def _anthropic_is_oauth(token: str) -> bool:
    """Hermes' own token-shape rule, mirrored when Hermes is not importable."""
    try:
        from agent import anthropic_credentials

        return bool(anthropic_credentials._is_oauth_token(token))
    except Exception:
        pass
    if not token or token.startswith("sk-ant-api"):
        return False
    return token.startswith("sk-ant-") or token.startswith("eyJ") or token.startswith("cc-")


def _anthropic_credential_kind(token: str) -> Optional[str]:
    """subscription | api_key | admin, or None for a token that is not Anthropic's."""
    if not token:
        return None
    if token.startswith("sk-ant-admin"):
        return "admin"
    if token.startswith("sk-ant-api"):
        return "api_key"
    if _anthropic_is_oauth(token):
        return "subscription"
    return None


def _anthropic_credentials() -> List[Dict[str, str]]:
    """Every Anthropic credential Hermes holds, deduplicated by token value.

    Order decides which credential becomes the base card: the environment
    tokens Hermes itself resolves for inference first, then Claude Code's
    login, then the Hermes pool logins. Each item is
    ``{"token", "kind", "source", "account", "usage_endpoint"}`` where
    ``usage_endpoint`` marks logins that may carry the ``user:profile`` scope
    (pool and Claude Code logins) and are therefore worth one try against
    ``/api/oauth/usage`` before the header probe.
    """
    found: List[Dict[str, Any]] = []
    seen: set = set()

    def add(token: Any, source: str, *, account: str = "", usage_endpoint: bool = False) -> None:
        value = str(token or "").strip()
        if not value or value in seen:
            return
        kind = _anthropic_credential_kind(value)
        if kind is None:
            return
        seen.add(value)
        found.append(
            {"token": value, "kind": kind, "source": source, "account": account[:60], "usage_endpoint": usage_endpoint}
        )

    for name, value in _anthropic_env_credentials():
        add(value, _ANTHROPIC_ENV_SOURCES.get(name, name))
    resolved, _is_oauth = _resolve_anthropic_oauth()
    add(resolved, "Hermes credential")
    add(_resolve_anthropic_claude_code_oauth(), "Claude Code OAuth login", usage_endpoint=True)
    for entry in _anthropic_pool_oauth_accounts():
        add(entry.get("token"), "Hermes OAuth login", account=str(entry.get("label") or ""), usage_endpoint=True)
    return found[:_ANTHROPIC_MAX_CREDENTIALS]


def _anthropic_probe_enabled() -> bool:
    try:
        value = _plugin_settings().get("anthropic_usage_probe", True)
    except Exception:
        return True
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "off"}
    return bool(value)


# ── /api/oauth/usage (full OAuth logins only) ─────────────────────────────


def _anthropic_usage_payload(payload: Mapping[str, Any]) -> Dict[str, Any]:
    """Normalize the api.anthropic.com/api/oauth/usage response.

    Mirrors Hermes' agent/account_usage.py parsing so this path reports the
    same windows Hermes' own usage command shows.
    """
    windows: List[Dict[str, Any]] = []
    for key, label in _ANTHROPIC_USAGE_WINDOWS:
        raw = payload.get(key)
        if not isinstance(raw, Mapping):
            continue
        util = _usage_number(raw.get("utilization"))
        if util is None:
            continue
        used = round(util * 100, 6) if util <= 1 else util
        windows.append(_usage_window(label, used_percent=used, reset_at=raw.get("resets_at")))
    details: List[str] = []
    extra = payload.get("extra_usage")
    if isinstance(extra, Mapping) and extra.get("is_enabled"):
        used_credits = _usage_number(extra.get("used_credits"))
        monthly_limit = _usage_number(extra.get("monthly_limit"))
        currency = _clean_text(extra.get("currency"), 12) or "USD"
        if used_credits is not None and monthly_limit is not None:
            details.append(f"Extra usage: {used_credits:.2f} / {monthly_limit:.2f} {currency}")
    if not windows and not details:
        return _provider_payload(
            "anthropic",
            status="unavailable",
            message="Anthropic returned no recognized usage windows.",
        )
    return _provider_payload("anthropic", status="ok", plan="Claude subscription", windows=windows, details=details)


def _collect_anthropic_direct(token: str) -> Dict[str, Any]:
    """Read account usage for a full OAuth login (needs the user:profile scope)."""
    headers: Dict[str, str] = {}
    try:
        import httpx

        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "anthropic-beta": "oauth-2025-04-20",
            "User-Agent": f"claude-code/{_anthropic_claude_code_version()}",
        }
        response = httpx.get(_ANTHROPIC_USAGE_URL, headers=headers, timeout=AI_USAGE_PROVIDER_TIMEOUT_SECONDS)
        if response.status_code == 401:
            return _provider_payload(
                "anthropic",
                status="expired",
                message="Anthropic rejected the saved OAuth login (401); it has expired. Sign in again to renew it.",
            )
        if response.status_code in (403, 429):
            return _provider_payload(
                "anthropic",
                status="forbidden",
                message=f"The account-usage endpoint answered HTTP {response.status_code} for this login.",
            )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, Mapping):
            return _provider_payload("anthropic", status="unavailable", message="Anthropic returned an invalid response.")
        return _anthropic_usage_payload(payload)
    except ImportError:
        return _provider_payload("anthropic", status="unavailable", message="Hermes HTTP client is unavailable.")
    except Exception as error:
        return _provider_payload("anthropic", status="unavailable", message=_provider_message(error))
    finally:
        headers.clear()


# ── The header probe: one one-token message per credential per TTL ────────


def _anthropic_claude_code_version() -> str:
    try:
        from agent import anthropic_adapter

        return str(anthropic_adapter._get_claude_code_version() or "").strip() or _ANTHROPIC_CLAUDE_CODE_VERSION_FALLBACK
    except Exception:
        return _ANTHROPIC_CLAUDE_CODE_VERSION_FALLBACK


def _anthropic_oauth_betas() -> List[str]:
    try:
        from agent import anthropic_adapter

        betas = list(anthropic_adapter._COMMON_BETAS) + list(anthropic_adapter._OAUTH_ONLY_BETAS)
        return betas or list(_ANTHROPIC_OAUTH_BETAS_FALLBACK)
    except Exception:
        return list(_ANTHROPIC_OAUTH_BETAS_FALLBACK)


def _anthropic_probe_headers(token: str, kind: str) -> Dict[str, str]:
    """Request headers for the probe.

    A subscription token is sent exactly the way Hermes sends its own OAuth
    inference — Bearer auth, the OAuth betas, and the Claude Code identity
    headers Anthropic requires for that lane. An API key uses plain x-api-key.
    """
    headers = {
        "anthropic-version": "2023-06-01",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    if kind == "subscription":
        headers.update(
            {
                "Authorization": f"Bearer {token}",
                "anthropic-beta": ",".join(_anthropic_oauth_betas()),
                "user-agent": f"claude-code/{_anthropic_claude_code_version()} (external, cli)",
                "x-app": "cli",
            }
        )
    else:
        headers["x-api-key"] = token
    return headers


def _anthropic_probe_body(kind: str) -> Dict[str, Any]:
    body: Dict[str, Any] = {
        "model": _ANTHROPIC_PROBE_MODEL,
        "max_tokens": 1,
        "messages": [{"role": "user", "content": "."}],
    }
    if kind == "subscription":
        # Same identity block Hermes prepends to every OAuth request, so the
        # probe lands in the same billing lane as Hermes' own calls.
        body["system"] = [{"type": "text", "text": _ANTHROPIC_CLAUDE_CODE_SYSTEM_PREFIX}]
    return body


def _anthropic_probe_request(token: str, kind: str) -> Tuple[int, Dict[str, str], Any]:
    """The single network call of the probe: (status code, lower-cased headers, JSON body or None)."""
    import httpx

    headers = _anthropic_probe_headers(token, kind)
    try:
        response = httpx.post(
            _ANTHROPIC_MESSAGES_URL,
            headers=headers,
            json=_anthropic_probe_body(kind),
            timeout=AI_USAGE_PROVIDER_TIMEOUT_SECONDS,
        )
    finally:
        headers.clear()
    lowered = {str(key).lower(): str(value) for key, value in response.headers.items()}
    try:
        payload = response.json()
    except Exception:
        payload = None
    return int(response.status_code), lowered, payload


_anthropic_probe_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_anthropic_probe_lock = threading.Lock()


def _anthropic_fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]


def _anthropic_unified_windows(headers: Mapping[str, str]) -> List[Dict[str, Any]]:
    windows: List[Dict[str, Any]] = []
    for prefix, label in (("5h", "Current session"), ("7d", "Current week")):
        util = _usage_number(headers.get(f"anthropic-ratelimit-unified-{prefix}-utilization"))
        if util is None:
            continue
        used = round(util * 100, 6) if util <= 1 else util
        windows.append(
            _usage_window(label, used_percent=used, reset_at=headers.get(f"anthropic-ratelimit-unified-{prefix}-reset"))
        )
    return windows


def _anthropic_unified_details(headers: Mapping[str, str]) -> List[str]:
    details: List[str] = []
    overage = _clean_text(headers.get("anthropic-ratelimit-unified-overage-status"), 40)
    reason = _clean_text(headers.get("anthropic-ratelimit-unified-overage-disabled-reason"), 60)
    if reason:
        details.append(f"Extra usage: off ({reason})")
    elif overage:
        details.append("Extra usage: on" if overage.lower() == "allowed" else f"Extra usage: {overage}")
    status = _clean_text(headers.get("anthropic-ratelimit-unified-status"), 40)
    if status and status.lower() not in {"allowed", "ok"}:
        details.append(f"Anthropic status: {status}")
    return details


def _anthropic_rate_limit_windows(headers: Mapping[str, str]) -> List[Dict[str, Any]]:
    windows: List[Dict[str, Any]] = []
    for key, label, unit in _ANTHROPIC_RATE_LIMITS:
        limit = _usage_number(headers.get(f"anthropic-ratelimit-{key}-limit"))
        remaining = _usage_number(headers.get(f"anthropic-ratelimit-{key}-remaining"))
        if limit is None and remaining is None:
            continue
        used = max(0.0, limit - remaining) if limit is not None and remaining is not None else None
        used_percent = (used / limit) * 100.0 if used is not None and limit and limit > 0 else None
        windows.append(
            _usage_window(
                label,
                kind="rate_limit",
                used_percent=used_percent,
                reset_at=headers.get(f"anthropic-ratelimit-{key}-reset"),
                limit=limit,
                used=used,
                remaining=remaining,
                unit=unit,
            )
        )
    return windows


def _anthropic_error_text(payload: Any) -> str:
    if isinstance(payload, Mapping):
        error = payload.get("error")
        if isinstance(error, Mapping):
            return _clean_text(error.get("message") or error.get("type"), 160) or ""
    return ""


def _anthropic_probe_card(kind: str, status_code: int, headers: Mapping[str, str], payload: Any) -> Dict[str, Any]:
    """Turn one probe response into a card; readings come from headers alone."""
    if kind == "subscription":
        windows = _anthropic_unified_windows(headers)
        details = _anthropic_unified_details(headers)
        plan = "Claude subscription"
    else:
        windows = _anthropic_rate_limit_windows(headers)
        details = ["USD spend: needs an Anthropic Admin API key (not read)"]
        plan = "Pay per token"
    if windows:
        if status_code >= 400:
            details.append(f"The probe answered HTTP {status_code}; these readings come from its headers.")
        card = _provider_payload("anthropic", status="ok", plan=plan, windows=windows, details=details)
    elif status_code == 401:
        card = _provider_payload(
            "anthropic",
            status="expired",
            message="Anthropic rejected this credential (401). It has expired or been revoked.",
        )
    elif status_code == 403:
        card = _provider_payload(
            "anthropic",
            status="forbidden",
            message=_anthropic_error_text(payload) or "Anthropic denied the probe (403).",
        )
    elif status_code == 429:
        card = _provider_payload(
            "anthropic",
            status="unavailable",
            message="Anthropic rate-limited the probe (429) and returned no usage headers; retried after the cache expires.",
        )
    else:
        reason = _anthropic_error_text(payload)
        card = _provider_payload(
            "anthropic",
            status="unavailable",
            message=(f"HTTP {status_code}: {reason}" if reason else f"HTTP {status_code}")
            if status_code >= 400
            else "Anthropic answered without rate-limit headers.",
        )
    card["organization_id"] = _clean_text(headers.get("anthropic-organization-id"), 80) or None
    return card


def _anthropic_header_probe(token: str, kind: str) -> Dict[str, Any]:
    """Probe with a cache keyed by credential fingerprint; every outcome is cached for the TTL."""
    key = _anthropic_fingerprint(token)
    now = time.time()
    if not _collect_is_fresh():
        with _anthropic_probe_lock:
            entry = _anthropic_probe_cache.get(key)
        if entry and now - entry[0] < ANTHROPIC_PROBE_TTL_SECONDS:
            card = copy.deepcopy(entry[1])
            card["probe_cached_at"] = entry[0]
            return card
    try:
        status_code, headers, payload = _anthropic_probe_request(token, kind)
        card = _anthropic_probe_card(kind, status_code, headers, payload)
    except ImportError:
        card = _provider_payload("anthropic", status="unavailable", message="Hermes HTTP client is unavailable.")
    except Exception as error:
        card = _provider_payload("anthropic", status="unavailable", message=_provider_message(error))
    with _anthropic_probe_lock:
        _anthropic_probe_cache[key] = (now, copy.deepcopy(card))
    return card


# ── Assembly ──────────────────────────────────────────────────────────────


def _collect_anthropic_credential(credential: Mapping[str, Any], probe_enabled: bool) -> Dict[str, Any]:
    token = str(credential["token"])
    kind = str(credential["kind"])
    if kind == "subscription" and credential.get("usage_endpoint"):
        direct = _collect_anthropic_direct(token)
        if direct.get("status") in {"ok", "expired"}:
            return direct
        # 403 (no user:profile scope), 429, or any other failure: the header
        # probe reads the same login through the lane Hermes actually uses.
    if not probe_enabled:
        return _provider_payload("anthropic", status="not_configured", message=_ANTHROPIC_PROBE_OFF_MESSAGE)
    return _anthropic_header_probe(token, kind)


def _anthropic_card_rank(kind: str, status: str) -> Tuple[int, int]:
    return (0 if kind == "subscription" else 1, 0 if status == "ok" else 1)


def _collect_anthropic_usage() -> Dict[str, Any]:
    credentials = _anthropic_credentials()
    if not credentials:
        return _provider_payload(
            "anthropic", status="not_configured", message=_provider_not_configured_message("anthropic")
        )
    probe_enabled = _anthropic_probe_enabled()
    cards: List[Tuple[Mapping[str, Any], Dict[str, Any]]] = []
    seen_orgs: set = set()
    admin_present = False
    for credential in credentials:
        if credential["kind"] == "admin":
            admin_present = True
            continue
        card = _collect_anthropic_credential(credential, probe_enabled)
        org = card.pop("organization_id", None)
        if org:
            dedupe = (credential["kind"], org)
            if dedupe in seen_orgs:
                continue  # the same organisation reached through a second credential
            seen_orgs.add(dedupe)
        card["auth_source"] = credential["source"]
        card["credential_kind"] = credential["kind"]
        if credential.get("account"):
            card["account"] = credential["account"]
        cards.append((credential, card))
    if not cards:
        message = "Only an Anthropic Admin API key is configured; Session Lens does not read cost reports yet."
        return _provider_payload("anthropic", status="not_configured", message=message if admin_present else None)
    cards.sort(key=lambda item: _anthropic_card_rank(str(item[0]["kind"]), str(item[1].get("status"))))
    _credential, base = cards[0]
    base["provider"] = "anthropic"
    base["label"] = _ANTHROPIC_SUBSCRIPTION_LABEL if base["credential_kind"] == "subscription" else _ANTHROPIC_API_LABEL
    extras: List[Dict[str, Any]] = []
    for index, (_credential, card) in enumerate(cards[1:], start=1):
        label = _ANTHROPIC_SUBSCRIPTION_LABEL if card["credential_kind"] == "subscription" else _ANTHROPIC_API_LABEL
        card["provider"] = f"anthropic:{index}"
        card["base_provider"] = "anthropic"
        card["account_extra"] = True
        card["label"] = f"{label} · {card['account']}" if card.get("account") else label
        extras.append(card)
    if admin_present:
        base.setdefault("details", []).append(
            "An Anthropic Admin API key is also configured; Session Lens does not read cost reports yet."
        )
    if extras:
        base["extra_accounts"] = extras
    return base


def _probe_anthropic() -> bool:
    """Local-only gate: does Hermes hold any Anthropic credential at all?

    Conservative like every probe: outside Hermes (its credential modules
    missing) or on any error it answers True so the collector still runs and
    reports its own status.
    """
    try:
        from agent import anthropic_credentials  # noqa: F401 — presence check only
    except ImportError:
        return True
    try:
        return bool(_anthropic_credentials())
    except Exception:
        return True


register_provider(
    "anthropic", _ANTHROPIC_SUBSCRIPTION_LABEL, "Hermes OAuth", _collect_anthropic_usage,
    probe=_probe_anthropic,
    not_configured_message="No Anthropic credential was found in Hermes (setup token, OAuth login, or API key).",
    billing_keys=("anthropic-oauth", "anthropic"),
    registry_ids=("anthropic", "anthropic-oauth", "claude"),
    hosts=("api.anthropic.com",), order=20, module=__name__,
    request_kind="inference_probe",
    note=(
        "Reads allowances from the rate-limit headers of one one-token Claude Haiku message per credential, "
        "cached 15 minutes; anthropic_usage_probe: false turns it off."
    ),
)

__all__ = [name for name in globals() if not name.startswith("__")]
