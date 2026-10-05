"""Hive (thehive.ai): no balance API, so the card shows the figure kept in .env."""

from __future__ import annotations

from .._common import *
from .._hermes_compat import *
from .._providers.shared import *
from .shared import *

HIVE_MANUAL_KEY = "HIVE_CREDIT_REMAINING"


def _collect_hive() -> Dict[str, Any]:
    """Hive answers 405 when the balance runs out but offers no way to read
    it. When the user keeps the remaining credit in HIVE_CREDIT_REMAINING
    the card shows that number, labelled as kept by hand; no request is made.
    """
    key, _ = _service_secret("HIVE_API_KEY")
    manual, _ = _service_secret(HIVE_MANUAL_KEY)
    figure = _usage_number(manual.replace(",", "").replace("$", "")) if manual else None
    if figure is None:
        return _service_payload(
            "hive",
            status="not_configured" if not key else "ok",
            message=(
                "Hive has no balance API. Set HIVE_CREDIT_REMAINING in the Hermes .env to track it here."
                if key else "No HIVE_API_KEY is set in Hermes."
            ),
        )
    window = _usage_window("Credit (kept in .env)", kind="balance", remaining=figure, unit="USD")
    return _service_payload("hive", status="ok", windows=[window])


register_service(
    "hive", "Hive", "Hermes .env key", collect=_collect_hive,
    env_keys=("HIVE_API_KEY", HIVE_MANUAL_KEY),
    mcp_hints=("thehive",),
    hosts=(), via="none", order=69, module=__name__,
)

__all__ = [name for name in globals() if not name.startswith("__")]
