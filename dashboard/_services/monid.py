"""Monid workspace balance and month-to-date spend, read through the local `monid` CLI."""

from __future__ import annotations

import json
import shutil
import subprocess

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


register_service(
    "monid", "Monid", "Monid CLI", collect=_collect_monid,
    env_keys=("MONID_API_KEY",), mcp_hints=("monid",), cli="monid",
    hosts=(), via="cli", order=50, module=__name__,
)

__all__ = [name for name in globals() if not name.startswith("__")]
