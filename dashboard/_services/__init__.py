"""Non-LLM service discovery and balance adapters.

Discovery reads only what Hermes already knows about: key NAMES in the
profile's .env, `mcp_servers` in config.yaml, and known CLIs on PATH. It
never opens skill folders or credential files kept elsewhere — a service
the user did not hand to Hermes is the user's to declare.

Adapters exist only for vendors whose usage endpoint was verified against
the real API; everything else is listed as configured-but-unreadable with
the reason spelled out, so nothing the user configured is silently absent.

One module per vendor: every module in this package other than ``shared``
is imported below, so a new service is a new file that calls
``register_service(...)``. See ADAPTERS.md at the repository root.
"""

from __future__ import annotations

import importlib
import pkgutil
import shutil

try:
    from .._common import *
    from .._hermes_compat import *
    from .._providers.shared import *
    from .shared import *
except ImportError:  # pragma: no cover
    from _common import *
    from _hermes_compat import *
    from _providers.shared import *
    from _services.shared import *

for _module_info in pkgutil.iter_modules(__path__):
    if _module_info.name.startswith("_") or _module_info.name == "shared":
        continue
    _module = importlib.import_module(f"{__name__}.{_module_info.name}")
    globals().update({_name: _value for _name, _value in vars(_module).items() if not _name.startswith("__")})
globals().pop("_module", None)
globals().pop("_module_info", None)

# Model-provider keys belong to the AI Usage provider cards, not here.
_LLM_ENV_KEYS = {
    "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY", "DEEPSEEK_API_KEY", "KIMI_API_KEY",
    "MOONSHOT_API_KEY", "GLM_API_KEY", "ZAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "NVIDIA_API_KEY",
    "OPENCODE_ZEN_API_KEY", "DASHSCOPE_API_KEY", "XAI_API_KEY", "GROK_API_KEY", "MISTRAL_API_KEY", "GROQ_API_KEY",
    "MINIMAX_API_KEY", "OLLAMA_API_KEY", "NOUS_API_KEY", "XIAOMI_API_KEY", "UPSTAGE_API_KEY", "FIREWORKS_API_KEY",
    "TOGETHER_API_KEY", "PERPLEXITY_API_KEY", "COHERE_API_KEY", "AZURE_OPENAI_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN",
}
_SECRET_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*(_API_KEY|_TOKEN|_SECRET|_API_TOKEN|_ACCESS_KEY)$")

SERVICES_CACHE_TTL_SECONDS = AI_USAGE_CACHE_TTL_SECONDS
_services_cache: Optional[Tuple[float, Dict[str, Any]]] = None
_services_cache_lock = threading.Lock()
_services_last_success: Dict[str, Dict[str, Any]] = {}


# ── Discovery ─────────────────────────────────────────────────────────────


def _mcp_servers_from_text(text: str) -> Dict[str, Dict[str, Any]]:
    """Minimal reader for the top-level `mcp_servers:` block of config.yaml.

    Used only when Hermes' own config loader is unavailable (tests, CI). It
    understands two-space-indented server names and their scalar fields,
    which is all discovery needs; anything fancier is simply ignored.
    """
    servers: Dict[str, Dict[str, Any]] = {}
    lines = text.splitlines()
    try:
        start = next(index for index, line in enumerate(lines) if line.rstrip() == "mcp_servers:")
    except StopIteration:
        return servers
    current: Optional[str] = None
    for line in lines[start + 1 :]:
        if line and not line.startswith(" "):
            break
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        if indent == 2 and stripped.endswith(":"):
            current = stripped[:-1].strip().strip("'\"")
            servers[current] = {}
            continue
        if current and indent == 4 and ":" in stripped:
            key, _, value = stripped.partition(":")
            servers[current][key.strip()] = value.strip().strip("'\"")
    return servers


def _mcp_server_entries() -> List[Dict[str, Any]]:
    raw: Mapping[str, Any] = {}
    try:
        from hermes_cli.config import load_config_readonly

        config = load_config_readonly() or {}
        candidate = config.get("mcp_servers") if isinstance(config, Mapping) else None
        raw = candidate if isinstance(candidate, Mapping) else {}
    except Exception:
        try:
            raw = _mcp_servers_from_text((_hermes_home() / "config.yaml").read_text(encoding="utf-8", errors="replace"))
        except Exception:
            raw = {}
    entries: List[Dict[str, Any]] = []
    for name, spec in raw.items():
        if not isinstance(spec, Mapping):
            continue
        url = str(spec.get("url") or "").strip()
        enabled = spec.get("enabled", True)
        enabled = enabled if isinstance(enabled, bool) else str(enabled).strip().lower() not in {"false", "0", "no"}
        tools = spec.get("tools")
        included = tools.get("include") if isinstance(tools, Mapping) else None
        entries.append(
            {
                "name": str(name),
                "transport": "http" if url else "stdio",
                "host": (urlparse(url).hostname or "")[:120] if url else None,
                "enabled": enabled,
                "tool_count": len(included) if isinstance(included, list) else None,
            }
        )
    return entries


def _service_for_mcp_name(name: str) -> Optional[str]:
    lowered = str(name or "").lower()
    for adapter in _service_adapters().values():
        if any(hint in lowered for hint in adapter.mcp_hints):
            return adapter.id
    return None


def _services_inventory() -> Dict[str, Dict[str, Any]]:
    """Every non-LLM service Hermes is configured with, keyed by service id."""
    inventory: Dict[str, Dict[str, Any]] = {}
    adapters = _service_adapters()

    def entry(service_id: str, kind: str) -> Dict[str, Any]:
        adapter = adapters.get(service_id)
        return inventory.setdefault(
            service_id,
            {
                "id": service_id,
                "label": _service_label_from_id(service_id),
                "kind": kind,
                "sources": [],
                "adapter": bool(adapter and adapter.readable),
                "note": adapter.note if adapter else None,
                "accounts": [],
            },
        )

    for name in _dotenv_key_names(_hermes_home() / ".env"):
        upper = name.upper()
        if upper in _LLM_ENV_KEYS:
            continue
        service_id, suffix = _service_for_env_key(upper)
        if service_id:
            item = entry(service_id, "service")
            if suffix and suffix not in item["accounts"]:
                item["accounts"].append(suffix)
        elif _SECRET_NAME_RE.match(upper):
            generic = re.sub(r"(_API_KEY|_API_TOKEN|_TOKEN|_SECRET|_ACCESS_KEY)$", "", upper).lower()
            if not generic or generic in {"hermes", "session_lens"}:
                continue
            item = entry(generic, "key")
            item["note"] = item["note"] or "No Session Lens adapter reads this service yet."
        else:
            continue
        item["sources"].append(f"env:{name}")

    for server in _mcp_server_entries():
        service_id = _service_for_mcp_name(server["name"]) or server["name"].lower()
        item = entry(service_id, "service" if service_id in adapters else "mcp")
        source = f"mcp:{server['name']}"
        if server.get("host"):
            source += f" ({server['host']})"
        item["sources"].append(source)
        item["mcp"] = {key: server[key] for key in ("transport", "enabled", "tool_count")}
        if item["kind"] == "mcp" and not item["note"]:
            item["note"] = "MCP server with no usage API Session Lens knows how to read."

    for adapter in adapters.values():
        if not adapter.cli:
            continue
        path = shutil.which(adapter.cli)
        if path:
            item = entry(adapter.id, "service")
            item["sources"].append(f"cli:{adapter.cli}")
            item["cli_path"] = path

    return inventory


# ── Collection ────────────────────────────────────────────────────────────

def _service_collectors() -> Dict[str, Any]:
    """service id → collector for every registered adapter that can read a balance."""
    return {adapter.id: adapter.collect for adapter in _service_adapters().values() if adapter.collect is not None}


def _fold_service_last_success(service: str, result: Dict[str, Any]) -> Dict[str, Any]:
    """Same memory rule as provider cards: transient failures re-serve the last good reading as stale."""
    if result.get("status") == "ok":
        _services_last_success[service] = copy.deepcopy(result)
    elif result.get("status") in {"not_configured", "expired", "forbidden"}:
        _services_last_success.pop(service, None)
    elif result.get("status") == "unavailable" and service in _services_last_success:
        message = result.get("message")
        result = copy.deepcopy(_services_last_success[service])
        result.update({"status": "stale", "stale": True, "message": message or "The latest refresh failed; showing the last successful reading."})
    return result


def _inventory_status(item: Mapping[str, Any], card: Optional[Mapping[str, Any]]) -> str:
    if not item.get("adapter"):
        return "unreadable"
    status = str(card.get("status") if card else "")
    if status in {"ok", "stale"}:
        return "monitored"
    if status in {"expired", "forbidden", "unavailable"}:
        return "attention"
    return "monitorable"


def _services_sync(fresh: bool = False, only_service: Optional[str] = None) -> Dict[str, Any]:
    global _services_cache
    now = time.time()
    with _services_cache_lock:
        if not fresh and _services_cache and now - _services_cache[0] < SERVICES_CACHE_TTL_SECONDS:
            cached = copy.deepcopy(_services_cache[1])
            cached["cached"] = True
            return cached
        base = copy.deepcopy(_services_cache[1]) if _services_cache else None
        cached_at = _services_cache[0] if _services_cache else now

    inventory = _services_inventory()
    collectors = _service_collectors()
    if only_service and base is not None and only_service in collectors:
        targets = {only_service: collectors[only_service]}
    else:
        targets = {sid: collector for sid, collector in collectors.items() if sid in inventory}
        base = None
    results: Dict[str, Dict[str, Any]] = {}
    if targets:
        with ThreadPoolExecutor(max_workers=len(targets), thread_name_prefix="session-lens-services") as pool:
            futures = {pool.submit(collector): sid for sid, collector in targets.items()}
            for future in as_completed(futures):
                sid = futures[future]
                try:
                    results[sid] = future.result()
                except Exception as error:
                    results[sid] = _service_payload(sid, status="unavailable", message=_provider_message(error))

    with _services_cache_lock:
        cards: List[Dict[str, Any]] = []
        previous = {card["provider"]: card for card in (base or {}).get("cards", [])} if base else {}
        order = _service_ids()
        for sid in sorted(inventory, key=lambda key: (order.index(key) if key in order else 99, key)):
            if sid not in collectors:
                continue
            if sid in results:
                result = results[sid]
                extras = result.pop("extra_accounts", None) or []
                cards.append(_fold_service_last_success(sid, result))
                cards.extend(_fold_service_last_success(str(extra.get("provider")), extra) for extra in extras)
            elif base is not None:
                cards.extend(card for card in base.get("cards", []) if card.get("base_provider") == sid)
        by_base: Dict[str, Dict[str, Any]] = {card["provider"]: card for card in cards}
        rows = []
        for sid in sorted(inventory, key=lambda key: (0 if key in collectors else 1, _service_label_from_id(key).lower())):
            item = dict(inventory[sid])
            card = by_base.get(sid)
            item["status"] = _inventory_status(item, card)
            if card and card.get("message") and item["status"] == "attention":
                item["note"] = card.get("message")
            rows.append(item)
        payload = {
            "cards": cards,
            "inventory": rows,
            "summary": {
                "configured": len(rows),
                "monitored": sum(1 for row in rows if row["status"] == "monitored"),
                "attention": sum(1 for row in rows if row["status"] == "attention"),
                "unreadable": sum(1 for row in rows if row["status"] == "unreadable"),
            },
            "generated_at": time.time(),
            "cached": False,
            "cache_ttl_seconds": SERVICES_CACHE_TTL_SECONDS,
            "definition": (
                "Discovered from key names in the Hermes .env, mcp_servers in config.yaml, and known CLIs on PATH. "
                "Balances come from each vendor's own usage endpoint with the configured key; services without a "
                "readable usage API are listed, not guessed."
            ),
        }
        _services_cache = (cached_at if only_service and base is not None else time.time(), copy.deepcopy(payload))
        return payload


def _services_cached_payload(max_age_seconds: float = 3600.0) -> Optional[Dict[str, Any]]:
    """A recent cached /services payload WITHOUT triggering collection."""
    with _services_cache_lock:
        if _services_cache and time.time() - _services_cache[0] < max_age_seconds:
            return copy.deepcopy(_services_cache[1])
    return None

__all__ = [name for name in globals() if not name.startswith("__")]
