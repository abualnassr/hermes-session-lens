"""ScrapeCreators credit balance."""

from __future__ import annotations

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


register_service(
    "scrapecreators", "ScrapeCreators", "Hermes .env key", collect=_collect_scrapecreators,
    env_keys=("SCRAPECREATORS_API_KEY", "SCRAPE_CREATORS_API_KEY"),
    mcp_hints=("scrape-creators", "scrapecreators", "scrape_creators"),
    hosts=("api.scrapecreators.com",), order=20, module=__name__,
)

__all__ = [name for name in globals() if not name.startswith("__")]
