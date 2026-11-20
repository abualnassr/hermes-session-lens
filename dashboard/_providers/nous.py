"""nous quota provider adapter.

Reads the Nous Portal balance through Hermes' published Nous credits view
(agent.account_usage.nous_credits_view) with the stored sign-in only, never
refreshing it; Session Lens never sees the token.
"""

from __future__ import annotations

import re

from .._common import *
from .._hermes_compat import *
from .shared import *

_NOUS_MONEY_RE = re.compile(r"^(Subscription credits|Top-up credits|Total usable|Rollover):\s*\$?([-0-9.,]+)")
_NOUS_PERCENT_RE = re.compile(r"^(?P<label>.+?)(?::|\s{2,}).*?(?P<used>[0-9.]+)%\s*used")
_NOUS_RESET_RE = re.compile(r"resets?\s+(?P<when>.+)$")


def _nous_lines() -> List[str]:
    try:
        from agent.account_usage import nous_credits_view
    except Exception:
        return []
    try:
        lines, _window = nous_credits_view(nous_route=False)
    except Exception:
        return []
    return [str(line).strip() for line in (lines or []) if str(line).strip()]


def _nous_payload_from_lines(lines: List[str]) -> Dict[str, Any]:
    """Turn Hermes' rendered Nous credits block back into a card."""
    plan: Optional[str] = None
    money: Dict[str, float] = {}
    windows: List[Dict[str, Any]] = []
    details: List[str] = []
    renews: Optional[str] = None
    for line in lines:
        clean = line.lstrip("📈").strip().strip("*").strip()
        if not clean or clean in {"Account limits", "Nous Portal"} or clean.startswith(("Top up", "(or run")):
            continue
        provider_match = re.match(r"^Provider:\s*\S+\s*(?:\((?P<plan>[^)]+)\))?", clean)
        if provider_match:
            plan = provider_match.group("plan") or plan
            continue
        money_match = _NOUS_MONEY_RE.match(clean)
        if money_match:
            try:
                money[money_match.group(1)] = float(money_match.group(2).replace(",", ""))
            except ValueError:
                details.append(clean)
            continue
        if clean.startswith("Renews:"):
            renews = clean.split(":", 1)[1].strip()
            continue
        percent_match = _NOUS_PERCENT_RE.match(clean)
        if percent_match:
            label = re.sub(r"[▀-▟■-◿]+", "", percent_match.group("label")).strip() or "Usage"
            reset = _NOUS_RESET_RE.search(clean)
            windows.append(_usage_window(label, used_percent=float(percent_match.group("used")),
                                         detail=reset.group(0) if reset else None))
            continue
        details.append(clean)
    total = money.get("Total usable")
    if total is not None:
        parts = []
        if "Subscription credits" in money:
            parts.append(f"${money['Subscription credits']:,.2f} subscription")
        if "Top-up credits" in money:
            parts.append(f"${money['Top-up credits']:,.2f} top-up")
        if renews:
            parts.append(f"renews {renews}")
        windows.insert(0, _usage_window("Usable credits", kind="balance", remaining=total, unit="USD",
                                        detail=" · ".join(parts) or None))
    elif renews:
        details.append(f"Renews {renews}")
    if "Rollover" in money:
        details.append(f"Rollover: ${money['Rollover']:,.2f}")
    if not windows and not details:
        return _provider_payload("nous", status="unavailable", message="Hermes returned no Nous Portal figures.")
    return _provider_payload("nous", status="ok", plan=plan, windows=windows, details=details)


def _collect_nous_usage() -> Dict[str, Any]:
    lines = _nous_lines()
    if not lines:
        return _provider_payload(
            "nous", status="not_configured",
            message="Hermes has no Nous Portal sign-in it can read in this profile (or its login stopped working).",
        )
    return _nous_payload_from_lines(lines)


register_provider(
    "nous", "Nous Research Portal", "Hermes sign-in", _collect_nous_usage,
    probe=lambda: True,
    not_configured_message="No Nous Portal login was found.",
    billing_keys=("nous",),
    registry_ids=("nous",),
    hosts=("portal.nousresearch.com",), via="hermes", order=30, module=__name__,
)

__all__ = [name for name in globals() if not name.startswith("__")]
