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
# Keyed by Hermes home: one backend serves every local profile, and a
# balance belongs to the profile whose .env key produced it.
_services_caches: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_services_cache_lock = threading.Lock()
_services_last_success_by_home: Dict[str, Dict[str, Dict[str, Any]]] = {}


def _services_last_success() -> Dict[str, Dict[str, Any]]:
    return _services_last_success_by_home.setdefault(_account_home_key(), {})


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


# ── MCP server inventory (Tools tab) ──────────────────────────────────────
# Which MCP servers each profile has configured and which tools each offers,
# so a connected server shows on the Tools tab before its first call. Both
# sources are local files Hermes maintains: config.yaml `mcp_servers` and
# the schema cache Hermes writes when it discovers a server's tools
# (cache/mcp_schema_cache.json). Names only; no server is contacted.

_mcp_file_cache: Dict[str, Tuple[Tuple[int, int], Any]] = {}


def _sanitize_mcp_component(value: Any) -> str:
    """Hermes' sanitize_mcp_name_component: every char outside [A-Za-z0-9_] becomes _."""
    return re.sub(r"[^A-Za-z0-9_]", "_", str(value or ""))


def _cached_file_parse(path: Path, parse: Any) -> Any:
    try:
        stat = path.stat()
    except OSError:
        return None
    signature = (stat.st_size, stat.st_mtime_ns)
    key = str(path)
    cached = _mcp_file_cache.get(key)
    if cached and cached[0] == signature:
        return cached[1]
    try:
        value = parse(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        value = None
    _mcp_file_cache[key] = (signature, value)
    return value


def _config_mcp_servers(home: Path) -> Dict[str, Mapping[str, Any]]:
    def parse(text: str) -> Dict[str, Mapping[str, Any]]:
        try:
            import yaml  # Hermes ships PyYAML; CI may not

            data = yaml.safe_load(text) or {}
            servers = data.get("mcp_servers") if isinstance(data, Mapping) else None
        except ImportError:
            servers = _mcp_servers_from_text(text)
        if not isinstance(servers, Mapping):
            return {}
        return {str(name): spec for name, spec in servers.items() if isinstance(spec, Mapping)}

    return _cached_file_parse(home / "config.yaml", parse) or {}


def _mcp_schema_catalog(home: Path) -> Dict[str, List[str]]:
    """server name -> tool names from Hermes' MCP schema cache (empty when absent)."""

    def parse(text: str) -> Dict[str, List[str]]:
        data = json.loads(text)
        catalog: Dict[str, List[str]] = {}
        if not isinstance(data, Mapping):
            return catalog
        for server, entry in data.items():
            tools = entry.get("tools") if isinstance(entry, Mapping) else None
            if isinstance(tools, list):
                catalog[str(server)] = sorted(
                    {str(tool.get("name")) for tool in tools if isinstance(tool, Mapping) and tool.get("name")}
                )
        return catalog

    return _cached_file_parse(home / "cache" / "mcp_schema_cache.json", parse) or {}


def _mcp_enabled(spec: Mapping[str, Any]) -> bool:
    enabled = spec.get("enabled", True)
    return enabled if isinstance(enabled, bool) else str(enabled).strip().lower() not in {"false", "0", "no", "off"}


def _mcp_inventory() -> Dict[str, Dict[str, Any]]:
    """Configured MCP servers across the scope, keyed by their tool-name prefix.

    The key is the server name as Hermes writes it into tool names
    (mcp__<key>__<tool>), so it joins recorded usage directly.
    """
    inventory: Dict[str, Dict[str, Any]] = {}
    for home in _scope_homes():
        servers = _config_mcp_servers(home)
        catalog = _mcp_schema_catalog(home)
        for name, spec in servers.items():
            key = _sanitize_mcp_component(name)
            url = str(spec.get("url") or "").strip()
            entry = inventory.setdefault(
                key,
                {
                    "label": name,
                    "enabled": False,
                    "transport": "http" if url else "stdio",
                    "host": (urlparse(url).hostname or "")[:120] if url else None,
                    "tool_names": set(),
                    "catalogued": False,
                },
            )
            entry["enabled"] = entry["enabled"] or _mcp_enabled(spec)
            if name in catalog:
                entry["catalogued"] = True
                entry["tool_names"].update(catalog[name])
    for entry in inventory.values():
        entry["available_tools"] = len(entry["tool_names"]) if entry["catalogued"] else None
    return inventory


def _registrable_domain(host: Any) -> str:
    parts = [part for part in str(host or "").lower().split(".") if part]
    return ".".join(parts[-2:]) if len(parts) >= 2 else ""


def _service_for_mcp_name(name: str, host: Optional[str] = None) -> Optional[str]:
    """The service an mcp_servers entry belongs to: by a name hint, else by a
    host on the same domain as an adapter's API host (mcp.twilio.com joins
    api.twilio.com)."""
    lowered = str(name or "").lower()
    for adapter in _service_adapters().values():
        if any(hint in lowered for hint in adapter.mcp_hints):
            return adapter.id
    domain = _registrable_domain(host)
    if domain:
        for adapter in _service_adapters().values():
            if any(_registrable_domain(api_host) == domain for api_host in adapter.hosts):
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
                # A registered adapter means Session Lens knows the vendor,
                # even when it has no API to read: no adapter recipe applies.
                "known": adapter is not None,
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
            item["note"] = item["note"] or "Listed from its key name; no Session Lens adapter reads its balance yet."
        else:
            continue
        item["sources"].append(f"env:{name}")

    for server in _mcp_server_entries():
        service_id = _service_for_mcp_name(server["name"], server.get("host")) or server["name"].lower()
        item = entry(service_id, "service" if service_id in adapters else "mcp")
        source = f"mcp:{server['name']}"
        if server.get("host"):
            source += f" ({server['host']})"
        item["sources"].append(source)
        item["mcp"] = {key: server[key] for key in ("transport", "enabled", "tool_count")}
        if item["kind"] == "mcp" and not item["note"]:
            item["note"] = (
                "Runs on this machine with no account behind it; its calls are on the Tools tab."
                if server.get("transport") == "stdio"
                else "Listed from config.yaml; no Session Lens adapter reads a usage API for this MCP server."
            )

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
    last_success = _services_last_success()
    if result.get("status") == "ok":
        last_success[service] = copy.deepcopy(result)
    elif result.get("status") in {"not_configured", "expired", "forbidden"}:
        last_success.pop(service, None)
    elif result.get("status") == "unavailable" and service in last_success:
        message = result.get("message")
        result = copy.deepcopy(last_success[service])
        result.update({"status": "stale", "stale": True, "message": message or "The latest refresh failed; showing the last successful reading."})
    return result


def _inventory_status(item: Mapping[str, Any], card: Optional[Mapping[str, Any]]) -> str:
    if not item.get("adapter"):
        # A local (stdio) MCP server nobody recognises is a tool on this
        # machine, not a service with a usage API missing.
        mcp = item.get("mcp") or {}
        if item.get("kind") == "mcp" and not item.get("known") and mcp.get("transport") == "stdio":
            return "local"
        return "unreadable"
    status = str(card.get("status") if card else "")
    if status in {"ok", "stale"}:
        return "monitored"
    if status in {"expired", "forbidden", "unavailable"}:
        return "attention"
    return "monitorable"


def _services_sync(fresh: bool = False, only_service: Optional[str] = None) -> Dict[str, Any]:
    now = time.time()
    home = _account_home_key()
    with _services_cache_lock:
        entry = _services_caches.get(home)
        if not fresh and entry and now - entry[0] < SERVICES_CACHE_TTL_SECONDS:
            cached = copy.deepcopy(entry[1])
            cached["cached"] = True
            return cached
        base = copy.deepcopy(entry[1]) if entry else None
        cached_at = entry[0] if entry else now

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
            futures = {_submit_in_context(pool, collector): sid for sid, collector in targets.items()}
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
                "local": sum(1 for row in rows if row["status"] == "local"),
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
        _services_caches[home] = (cached_at if only_service and base is not None else time.time(), copy.deepcopy(payload))
        return payload


def _services_cached_payload(max_age_seconds: float = 3600.0) -> Optional[Dict[str, Any]]:
    """A recent cached /services payload WITHOUT triggering collection."""
    with _services_cache_lock:
        entry = _services_caches.get(_account_home_key())
        if entry and time.time() - entry[0] < max_age_seconds:
            return copy.deepcopy(entry[1])
    return None

__all__ = [name for name in globals() if not name.startswith("__")]
