"""Plumbing every service adapter shares: payload shape, key lookup, HTTP GET, credential outcomes."""

from __future__ import annotations

import os

try:
    from .._common import *
    from .._hermes_compat import *
    from .._providers.shared import *
except ImportError:  # pragma: no cover
    from _common import *
    from _hermes_compat import *
    from _providers.shared import *


def _service_env_keys() -> Dict[str, str]:
    """Env key NAME → service id, from every registered service adapter."""
    return {key: adapter.id for adapter in _service_adapters().values() for key in adapter.env_keys}


def _service_label_from_id(service_id: str) -> str:
    label = _adapter_label(service_id)
    if label:
        return label
    words = re.split(r"[_\-\s]+", str(service_id or "").strip())
    return " ".join(word.capitalize() for word in words if word) or "Service"


def _dotenv_key_names(path: Path) -> List[str]:
    """KEY names assigned in a dotenv file, in file order. Values are never read here."""
    names: List[str] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return names
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:]
        key = line.split("=", 1)[0].strip()
        if key and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) and key not in names:
            names.append(key)
    return names


def _service_for_env_key(name: str) -> Tuple[Optional[str], Optional[str]]:
    """(service id, account suffix) for an env key name; (None, None) when unknown."""
    upper = str(name or "").upper()
    env_keys = _service_env_keys()
    if upper in env_keys:
        return env_keys[upper], None
    for known, service in env_keys.items():
        if upper.startswith(known + "_"):
            suffix = upper[len(known) + 1 :].strip("_").lower()
            return service, suffix or None
    return None, None


def _service_payload(
    service: str,
    *,
    status: str,
    windows: Optional[List[Dict[str, Any]]] = None,
    details: Optional[List[str]] = None,
    message: Optional[str] = None,
    plan: Optional[str] = None,
    partial: bool = False,
    account: Optional[str] = None,
) -> Dict[str, Any]:
    adapter = _service_adapters().get(service)
    label = adapter.label if adapter else _service_label_from_id(service)
    auth_source = adapter.auth_source if adapter else "Hermes .env key"
    return {
        "provider": f"{service}:{account}" if account else service,
        "base_provider": service,
        "service": True,
        "label": f"{label} · {account}" if account else label,
        "account": account,
        "account_extra": bool(account),
        "status": status,
        "auth_source": auth_source,
        "plan": _clean_text(plan, 120) or None,
        "windows": windows or [],
        "details": [_clean_text(item, 320) for item in (details or []) if _clean_text(item, 320)],
        "message": _clean_text(message, 240) or None,
        "partial": bool(partial),
        "stale": False,
        "fetched_at": time.time(),
    }


def _service_secret(*names: str) -> Tuple[str, str]:
    """(value, env name) for the first configured key among `names`.

    Hermes loads the profile's .env into the process environment at startup,
    so the environment is the source of truth; the file is read only as a
    fallback for callers outside a Hermes process.
    """
    for name in names:
        value = str(os.environ.get(name) or "").strip()
        if value:
            return value, name
    try:
        text = (_hermes_home() / ".env").read_text(encoding="utf-8", errors="replace")
    except Exception:
        return "", ""
    wanted = set(names)
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:]
        key, _, value = line.partition("=")
        key = key.strip()
        if key in wanted:
            value = value.strip().strip("'\"")
            if value:
                return value, key
    return "", ""


def _service_get(url: str, headers: Mapping[str, str]) -> Tuple[int, Any, Optional[str]]:
    """(status code, decoded JSON or None, error text). Credentials stay in `headers`."""
    try:
        import httpx
    except ImportError:
        return 0, None, "Hermes HTTP client is unavailable."
    try:
        response = httpx.get(url, headers=dict(headers), timeout=AI_USAGE_PROVIDER_TIMEOUT_SECONDS)
    except Exception as error:
        return 0, None, _provider_message(error)
    try:
        body = response.json()
    except Exception:
        body = None
    return response.status_code, body, None


def _credential_status(service: str, code: int, error: Optional[str], account: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Common credential outcomes; None when the response should be parsed."""
    label = _service_label_from_id(service)
    if error:
        return _service_payload(service, status="unavailable", message=error, account=account)
    if code == 401:
        return _service_payload(service, status="expired", message=f"{label} rejected the configured key.", account=account)
    if code == 403:
        return _service_payload(service, status="forbidden", message=f"{label} denied access to account usage for this key.", account=account)
    if code == 402:
        return _service_payload(service, status="ok", windows=[_usage_window("Credits", kind="balance", remaining=0, unit="credits")], message=f"{label} reports the credit balance is exhausted.", account=account)
    if code != 200:
        return _service_payload(service, status="unavailable", message=f"{label} returned HTTP {code}.", account=account)
    return None

__all__ = [name for name in globals() if not name.startswith("__")]
