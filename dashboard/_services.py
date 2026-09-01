"""Non-LLM service discovery and balance adapters.

Discovery reads only what Hermes already knows about: key NAMES in the
profile's .env, `mcp_servers` in config.yaml, and known CLIs on PATH. It
never opens skill folders or credential files kept elsewhere — a service
the user did not hand to Hermes is the user's to declare.

Adapters exist only for vendors whose usage endpoint was verified against
the real API; everything else is listed as configured-but-unreadable with
the reason spelled out, so nothing the user configured is silently absent.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess

try:
    from ._common import *
    from ._hermes_compat import *
    from ._providers.shared import *
except ImportError:  # pragma: no cover
    from _common import *
    from _hermes_compat import *
    from _providers.shared import *

_SERVICE_META: Dict[str, Dict[str, Any]] = {
    "firecrawl": {"label": "Firecrawl", "auth_source": "Hermes .env key", "adapter": True},
    "scrapecreators": {"label": "ScrapeCreators", "auth_source": "Hermes .env key", "adapter": True},
    "agentmail": {"label": "AgentMail", "auth_source": "Hermes .env key", "adapter": True},
    "brightdata": {"label": "Bright Data", "auth_source": "Hermes .env key", "adapter": True},
    "monid": {"label": "Monid", "auth_source": "Monid CLI", "adapter": True},
    "brave": {
        "label": "Brave Search",
        "auth_source": "Hermes .env key",
        "adapter": False,
        "note": "Brave exposes no balance endpoint; its rate-limit headers appear only on a paid query, which Session Lens will not spend.",
    },
    "telegram": {"label": "Telegram bot", "auth_source": "Hermes .env token", "adapter": False, "note": "A bot token has nothing to meter."},
    "herenow": {
        "label": "here.now",
        "auth_source": "Hermes .env key",
        "adapter": False,
        "note": "here.now's API publishes sites but reports no plan, quota, or usage figure.",
    },
}
_SERVICE_ORDER = tuple(_SERVICE_META)

_SERVICE_ENV_KEYS: Dict[str, str] = {
    "FIRECRAWL_API_KEY": "firecrawl",
    "SCRAPECREATORS_API_KEY": "scrapecreators",
    "SCRAPE_CREATORS_API_KEY": "scrapecreators",
    "AGENTMAIL_API_KEY": "agentmail",
    "MCP_BRIGHTDATA_API_KEY": "brightdata",
    "BRIGHTDATA_API_KEY": "brightdata",
    "BRIGHT_DATA_API_KEY": "brightdata",
    "BRAVE_SEARCH_API_KEY": "brave",
    "BRAVE_API_KEY": "brave",
    "TELEGRAM_BOT_TOKEN": "telegram",
    "HERE_NOW_API_KEY": "herenow",
    "HERENOW_API_KEY": "herenow",
    "MONID_API_KEY": "monid",
}
# Model-provider keys belong to the AI Usage provider cards, not here.
_LLM_ENV_KEYS = {
    "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY", "DEEPSEEK_API_KEY", "KIMI_API_KEY",
    "MOONSHOT_API_KEY", "GLM_API_KEY", "ZAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "NVIDIA_API_KEY",
    "OPENCODE_ZEN_API_KEY", "DASHSCOPE_API_KEY", "XAI_API_KEY", "GROK_API_KEY", "MISTRAL_API_KEY", "GROQ_API_KEY",
    "MINIMAX_API_KEY", "OLLAMA_API_KEY", "NOUS_API_KEY", "XIAOMI_API_KEY", "UPSTAGE_API_KEY", "FIREWORKS_API_KEY",
    "TOGETHER_API_KEY", "PERPLEXITY_API_KEY", "COHERE_API_KEY", "AZURE_OPENAI_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN",
}
_SECRET_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*(_API_KEY|_TOKEN|_SECRET|_API_TOKEN|_ACCESS_KEY)$")
_MCP_NAME_HINTS: Tuple[Tuple[str, str], ...] = (
    ("firecrawl", "firecrawl"),
    ("bright", "brightdata"),
    ("scrape-creators", "scrapecreators"),
    ("scrapecreators", "scrapecreators"),
    ("scrape_creators", "scrapecreators"),
    ("agentmail", "agentmail"),
    ("brave", "brave"),
    ("monid", "monid"),
)
_SERVICE_CLIS: Tuple[Tuple[str, str], ...] = (("monid", "monid"),)

SERVICES_CACHE_TTL_SECONDS = AI_USAGE_CACHE_TTL_SECONDS
_services_cache: Optional[Tuple[float, Dict[str, Any]]] = None
_services_cache_lock = threading.Lock()
_services_last_success: Dict[str, Dict[str, Any]] = {}


# ── Discovery ─────────────────────────────────────────────────────────────


def _service_label_from_id(service_id: str) -> str:
    meta = _SERVICE_META.get(service_id)
    if meta:
        return str(meta["label"])
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
    if upper in _SERVICE_ENV_KEYS:
        return _SERVICE_ENV_KEYS[upper], None
    for known, service in _SERVICE_ENV_KEYS.items():
        if upper.startswith(known + "_"):
            suffix = upper[len(known) + 1 :].strip("_").lower()
            return service, suffix or None
    return None, None


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
    for hint, service in _MCP_NAME_HINTS:
        if hint in lowered:
            return service
    return None


def _services_inventory() -> Dict[str, Dict[str, Any]]:
    """Every non-LLM service Hermes is configured with, keyed by service id."""
    inventory: Dict[str, Dict[str, Any]] = {}

    def entry(service_id: str, kind: str) -> Dict[str, Any]:
        meta = _SERVICE_META.get(service_id) or {}
        return inventory.setdefault(
            service_id,
            {
                "id": service_id,
                "label": _service_label_from_id(service_id),
                "kind": kind,
                "sources": [],
                "adapter": bool(meta.get("adapter")),
                "note": meta.get("note"),
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
        item = entry(service_id, "service" if service_id in _SERVICE_META else "mcp")
        source = f"mcp:{server['name']}"
        if server.get("host"):
            source += f" ({server['host']})"
        item["sources"].append(source)
        item["mcp"] = {key: server[key] for key in ("transport", "enabled", "tool_count")}
        if item["kind"] == "mcp" and not item["note"]:
            item["note"] = "MCP server with no usage API Session Lens knows how to read."

    for command, service_id in _SERVICE_CLIS:
        path = shutil.which(command)
        if path:
            item = entry(service_id, "service")
            item["sources"].append(f"cli:{command}")
            item["cli_path"] = path

    return inventory


# ── Shared adapter plumbing ───────────────────────────────────────────────


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
    meta = _SERVICE_META.get(service) or {"label": _service_label_from_id(service), "auth_source": "Hermes .env key"}
    label = str(meta["label"])
    return {
        "provider": f"{service}:{account}" if account else service,
        "base_provider": service,
        "service": True,
        "label": f"{label} · {account}" if account else label,
        "account": account,
        "account_extra": bool(account),
        "status": status,
        "auth_source": meta.get("auth_source"),
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


# ── Firecrawl ─────────────────────────────────────────────────────────────

_FIRECRAWL_CLOUD_HOST = "api.firecrawl.dev"


def _firecrawl_payload(body: Any, account: Optional[str] = None) -> Dict[str, Any]:
    data = body.get("data") if isinstance(body, Mapping) else None
    if not isinstance(data, Mapping):
        return _service_payload("firecrawl", status="unavailable", message="Firecrawl returned no credit data.", account=account)
    remaining = _usage_number(data.get("remainingCredits", data.get("remaining_credits")))
    plan = _usage_number(data.get("planCredits", data.get("plan_credits")))
    period_end = data.get("billingPeriodEnd", data.get("billing_period_end"))
    if remaining is None:
        return _service_payload("firecrawl", status="unavailable", message="Firecrawl returned no remaining-credit figure.", account=account)
    detail_parts = []
    used = None
    used_percent = None
    if plan:
        detail_parts.append(f"Plan {plan:,.0f} credits per period")
        if remaining <= plan:
            used = plan - remaining
            used_percent = (used / plan) * 100.0
        else:
            detail_parts.append("balance includes top-ups beyond the plan")
    window = _usage_window(
        "Credits",
        kind="balance",
        remaining=remaining,
        limit=plan,
        used=used,
        used_percent=used_percent,
        unit="credits",
        reset_at=period_end,
        detail=" · ".join(detail_parts) or None,
    )
    return _service_payload("firecrawl", status="ok", windows=[window], account=account)


def _collect_firecrawl() -> Dict[str, Any]:
    base = str(os.environ.get("FIRECRAWL_API_URL") or "https://api.firecrawl.dev").strip().rstrip("/")
    host = (urlparse(base).hostname or "").lower()
    if host and host != _FIRECRAWL_CLOUD_HOST:
        return _service_payload(
            "firecrawl",
            status="unavailable",
            message="FIRECRAWL_API_URL points at a self-hosted endpoint; credit usage exists only on Firecrawl cloud.",
        )
    primary, _name = _service_secret("FIRECRAWL_API_KEY")
    if not primary:
        return _service_payload("firecrawl", status="not_configured", message="No FIRECRAWL_API_KEY is set in Hermes.")

    def fetch(key: str, account: Optional[str]) -> Dict[str, Any]:
        headers = {"Authorization": f"Bearer {key}", "Accept": "application/json"}
        try:
            code, body, error = _service_get(f"https://{_FIRECRAWL_CLOUD_HOST}/v2/team/credit-usage", headers)
        finally:
            headers.clear()
        return _credential_status("firecrawl", code, error, account) or _firecrawl_payload(body, account)

    result = fetch(primary, None)
    extras: List[Dict[str, Any]] = []
    seen = {primary}
    for name in _dotenv_key_names(_hermes_home() / ".env"):
        service_id, suffix = _service_for_env_key(name)
        if service_id != "firecrawl" or not suffix:
            continue
        key, _ = _service_secret(name)
        if not key or key in seen:
            continue
        seen.add(key)
        extras.append(fetch(key, suffix))
        if len(extras) >= 4:
            break
    if extras:
        result["extra_accounts"] = extras
    return result


# ── ScrapeCreators ────────────────────────────────────────────────────────


def _scrapecreators_payload(body: Any) -> Dict[str, Any]:
    if not isinstance(body, Mapping):
        return _service_payload("scrapecreators", status="unavailable", message="ScrapeCreators returned an invalid response.")
    remaining = _usage_number(body.get("credits_remaining", body.get("creditCount")))
    if remaining is None:
        return _service_payload("scrapecreators", status="unavailable", message="ScrapeCreators returned no credit figure.")
    window = _usage_window("Credits", kind="balance", remaining=remaining, unit="credits")
    return _service_payload("scrapecreators", status="ok", windows=[window])


def _collect_scrapecreators() -> Dict[str, Any]:
    key, _ = _service_secret("SCRAPECREATORS_API_KEY", "SCRAPE_CREATORS_API_KEY")
    if not key:
        return _service_payload("scrapecreators", status="not_configured", message="No SCRAPECREATORS_API_KEY is set in Hermes.")
    headers = {"x-api-key": key, "Accept": "application/json"}
    try:
        code, body, error = _service_get("https://api.scrapecreators.com/v1/account/credit-balance", headers)
    finally:
        headers.clear()
    return _credential_status("scrapecreators", code, error) or _scrapecreators_payload(body)


# ── AgentMail ─────────────────────────────────────────────────────────────

_AGENTMAIL_USAGE_TYPES = ("message_count", "thread_count", "inbox_count", "storage_bytes")


def _agentmail_payload(body: Any) -> Dict[str, Any]:
    if not isinstance(body, Mapping):
        return _service_payload("agentmail", status="unavailable", message="AgentMail returned an invalid response.")
    latest: Dict[str, float] = {}
    for usage_type in _AGENTMAIL_USAGE_TYPES:
        series = body.get(usage_type)
        if isinstance(series, list) and series:
            value = _usage_number((series[0] or {}).get("value") if isinstance(series[0], Mapping) else None)
            if value is not None:
                latest[usage_type] = value
    if not latest:
        return _service_payload("agentmail", status="unavailable", message="AgentMail returned no usage counters.")
    parts = []
    if "message_count" in latest:
        parts.append(f"{latest['message_count']:,.0f} messages")
    if "thread_count" in latest:
        parts.append(f"{latest['thread_count']:,.0f} threads")
    if "inbox_count" in latest:
        parts.append(f"{latest['inbox_count']:,.0f} inboxes")
    if "storage_bytes" in latest:
        parts.append(f"{latest['storage_bytes'] / (1024 * 1024):,.1f} MB stored")
    return _service_payload(
        "agentmail",
        status="ok",
        details=["Cumulative usage: " + " · ".join(parts), "AgentMail reports running totals, not a plan quota or balance."],
    )


def _collect_agentmail() -> Dict[str, Any]:
    key, _ = _service_secret("AGENTMAIL_API_KEY")
    if not key:
        return _service_payload("agentmail", status="not_configured", message="No AGENTMAIL_API_KEY is set in Hermes.")
    query = "&".join(f"usage_types={item}" for item in _AGENTMAIL_USAGE_TYPES) + "&limit=1&descending=true"
    headers = {"Authorization": f"Bearer {key}", "Accept": "application/json"}
    try:
        code, body, error = _service_get(f"https://api.agentmail.to/v0/metrics/usage?{query}", headers)
    finally:
        headers.clear()
    return _credential_status("agentmail", code, error) or _agentmail_payload(body)


# ── Bright Data ───────────────────────────────────────────────────────────


def _brightdata_payload(body: Any) -> Dict[str, Any]:
    if not isinstance(body, Mapping):
        return _service_payload("brightdata", status="unavailable", message="Bright Data returned an invalid response.")
    balance = _usage_number(body.get("balance"))
    details = []
    for key, value in body.items():
        number = _usage_number(value)
        if key != "balance" and number is not None:
            details.append(f"{str(key).replace('_', ' ')}: {number:,.2f}")
    if balance is None:
        if not details:
            return _service_payload("brightdata", status="unavailable", message="Bright Data returned no balance figure.")
        return _service_payload("brightdata", status="ok", details=details)
    window = _usage_window("Account balance", kind="balance", remaining=balance, unit="USD")
    return _service_payload("brightdata", status="ok", windows=[window], details=details)


def _collect_brightdata() -> Dict[str, Any]:
    key, _ = _service_secret("MCP_BRIGHTDATA_API_KEY", "BRIGHTDATA_API_KEY", "BRIGHT_DATA_API_KEY")
    if not key:
        return _service_payload("brightdata", status="not_configured", message="No Bright Data API key is set in Hermes.")
    headers = {"Authorization": f"Bearer {key}", "Accept": "application/json"}
    try:
        code, body, error = _service_get("https://api.brightdata.com/customer/balance", headers)
    finally:
        headers.clear()
    if code == 403:
        return _service_payload(
            "brightdata",
            status="forbidden",
            message="The Bright Data token lacks account-balance permission. Grant it under Settings → Users on brightdata.com.",
        )
    return _credential_status("brightdata", code, error) or _brightdata_payload(body)


# ── Monid ─────────────────────────────────────────────────────────────────

# The CLI aborts above ~100 runs per page on Windows; one page is plenty for a month.
_MONID_RUNS_LIMIT = 100
_MONID_TIMEOUT_SECONDS = 20


def _monid_command(args: List[str]) -> Tuple[Any, Optional[str]]:
    """Run a read-only `monid` subcommand with --json; (parsed JSON, error)."""
    executable = shutil.which("monid")
    if not executable:
        return None, "The monid CLI is not on PATH."
    try:
        completed = subprocess.run(
            [executable, *args, "--json"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_MONID_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return None, "The monid CLI did not answer within 20 seconds."
    except Exception as error:
        return None, _provider_message(error)
    output = (completed.stdout or "").strip()
    # The CLI's Node runtime sometimes aborts on exit (a libuv assertion on
    # Windows) after the JSON has already been written; the output is what
    # counts, the exit status only matters when there is none.
    if output and "{" in output:
        try:
            return json.loads(output[output.index("{") :]), None
        except ValueError:
            pass
    if completed.returncode != 0 or not output:
        stderr = re.sub(r"\x1b\[[0-9;]*m", "", (completed.stderr or "").strip()).splitlines()
        return None, (stderr[0] if stderr else f"monid exited with status {completed.returncode}.")[:200]
    try:
        return json.loads(output[output.index("{") :]), None
    except ValueError:
        return None, "The monid CLI returned output that is not JSON."


def _monid_month_start(now: float) -> float:
    moment = dt.datetime.fromtimestamp(now)
    return moment.replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp()


def _monid_payload(balance: Any, runs: Any, now: Optional[float] = None) -> Dict[str, Any]:
    if not isinstance(balance, Mapping) or not isinstance(balance.get("balance"), Mapping):
        return _service_payload("monid", status="unavailable", message="monid balance returned no balance figure.")
    now = now if now is not None else time.time()
    amount = _usage_number(balance["balance"].get("value"))
    currency = _clean_text(balance["balance"].get("currency"), 12) or "USD"
    held_raw = balance.get("held")
    held = _usage_number(held_raw.get("value")) if isinstance(held_raw, Mapping) else None
    if amount is None:
        return _service_payload("monid", status="unavailable", message="monid balance returned no balance figure.")
    detail = f"{held:,.2f} {currency} held by in-flight runs" if held else None
    windows = [_usage_window("Workspace balance", kind="balance", remaining=amount, unit=currency, detail=detail)]

    month_start = _monid_month_start(now)
    week_start = now - 7 * 86400.0
    day_start = now - 86400.0
    spend = {"monthly": 0.0, "weekly": 0.0, "daily": 0.0}
    month_runs = 0
    by_provider: Dict[str, float] = {}
    items = runs.get("items") if isinstance(runs, Mapping) else None
    for run in items if isinstance(items, list) else []:
        if not isinstance(run, Mapping):
            continue
        cost_raw = run.get("cost")
        cost = _usage_number(cost_raw.get("value")) if isinstance(cost_raw, Mapping) else None
        created = _usage_reset_epoch(run.get("createdAt"))
        if cost is None or created is None:
            continue
        if created >= month_start:
            spend["monthly"] += cost
            month_runs += 1
            name = _clean_text(run.get("providerName") or run.get("provider"), 60) or "unknown"
            by_provider[name] = by_provider.get(name, 0.0) + cost
        if created >= week_start:
            spend["weekly"] += cost
        if created >= day_start:
            spend["daily"] += cost
    details: List[str] = []
    if items is not None:
        details.append(f"Month to date: {spend['monthly']:,.2f} {currency} across {month_runs} run{'s' if month_runs != 1 else ''}")
        if by_provider:
            top = sorted(by_provider.items(), key=lambda item: -item[1])[:3]
            details.append("Top: " + " · ".join(f"{name} {cost:,.2f}" for name, cost in top))
        if isinstance(items, list) and len(items) >= _MONID_RUNS_LIMIT:
            details.append(f"Spend covers the latest {_MONID_RUNS_LIMIT} runs only.")
    for note in balance.get("notes") or []:
        text = _clean_text(note, 200)
        if text:
            details.append(text)
    payload = _service_payload("monid", status="ok", windows=windows, details=details)
    if items is not None:
        payload["account_spend"] = {**{key: round(value, 4) for key, value in spend.items()}, "unit": currency}
    return payload


def _collect_monid() -> Dict[str, Any]:
    if not shutil.which("monid"):
        return _service_payload("monid", status="not_configured", message="The monid CLI is not installed.")
    balance, error = _monid_command(["balance"])
    if error:
        lowered = error.lower()
        if "no active api key" in lowered or "unauthorized" in lowered:
            return _service_payload("monid", status="expired", message=f"monid: {error}")
        return _service_payload("monid", status="unavailable", message=f"monid: {error}")
    runs, runs_error = _monid_command(["runs", "list", "-l", str(_MONID_RUNS_LIMIT)])
    if runs_error is None and not (isinstance(runs, Mapping) and isinstance(runs.get("items"), list)):
        runs_error = _clean_text((runs or {}).get("error") if isinstance(runs, Mapping) else None, 160) or "unexpected response"
    payload = _monid_payload(balance, runs if not runs_error else None)
    if runs_error:
        payload["partial"] = True
        payload["message"] = f"Run history unavailable: {runs_error}"
    return payload


# ── Collection ────────────────────────────────────────────────────────────

_SERVICE_COLLECTORS: Dict[str, Any] = {
    "firecrawl": _collect_firecrawl,
    "scrapecreators": _collect_scrapecreators,
    "agentmail": _collect_agentmail,
    "brightdata": _collect_brightdata,
    "monid": _collect_monid,
}


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
    if only_service and base is not None and only_service in _SERVICE_COLLECTORS:
        targets = {only_service: _SERVICE_COLLECTORS[only_service]}
    else:
        targets = {sid: collector for sid, collector in _SERVICE_COLLECTORS.items() if sid in inventory}
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
        for sid in sorted(inventory, key=lambda key: (_SERVICE_ORDER.index(key) if key in _SERVICE_ORDER else 99, key)):
            if sid not in _SERVICE_COLLECTORS:
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
        for sid in sorted(inventory, key=lambda key: (0 if key in _SERVICE_COLLECTORS else 1, _service_label_from_id(key).lower())):
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
