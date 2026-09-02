# Adding a provider or service adapter

Session Lens reads account allowances and balances through **adapters**. Each vendor is one Python module that registers itself; the dispatchers (`_ai_usage_sync` for model providers, `_services_sync` for everything else) read the registry and never name a vendor. Adding a vendor is adding a file.

`GET /adapters` publishes the registry at runtime, credential-free, and the README's Trust table is checked by the test suite against it. That is the contract an adapter enters: declare the hosts you contact, contact nothing else.

## Ground rules

1. **Verify the endpoint against the live API first.** An adapter exists only for a vendor whose usage or balance endpoint was called with a real key and whose response shape was read. Do not add an adapter from documentation alone; if the endpoint cannot be verified, register the service *without* a collector and say why in `note` (see `dashboard/_services/brave.py`).
2. **One request kind: the vendor's own usage or balance endpoint**, with the credential Hermes already holds. Never spend a paid query to read a rate-limit header, never call an inference endpoint, never read browser cookies or files outside `$HERMES_HOME`.
3. **Declare every host** you contact in `hosts=`. The tests fail if a direct adapter declares none, and the README table must list each one.
4. **Return normalized payloads only.** Credentials must not appear in any returned dict, message, or detail. Clear header dicts after the request (`headers.clear()`), as the existing adapters do.
5. **Never write.** No cache files, no token refresh of your own, no config edits. Ask Hermes' resolvers for credentials; do not store them.
6. **Fail honestly.** `not_configured` when nothing is set, `expired` on 401, `forbidden` on 403, `unavailable` on anything else with the reason in `message`. A definitive credential failure clears the last-good reading; a transient one re-serves it marked stale (the dispatcher does this for you).

## A model provider (AI Usage card)

Create `dashboard/_providers/<vendor>.py`:

```python
"""<Vendor> quota provider adapter."""

from __future__ import annotations

try:
    from .._common import *
    from .._hermes_compat import *
    from .shared import *
except ImportError:  # pragma: no cover
    from _common import *
    from _hermes_compat import *
    from _providers.shared import *


def _collect_vendor_usage() -> Dict[str, Any]:
    token, base_url = _resolve_hermes_api_key("vendor")
    if not token:
        return _provider_payload("vendor", status="not_configured", message="No Hermes <Vendor> API key was found.")
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    try:
        import httpx

        response = httpx.get("https://api.vendor.example/v1/usage", headers=headers, timeout=AI_USAGE_PROVIDER_TIMEOUT_SECONDS)
    except Exception as error:
        return _provider_payload("vendor", status="unavailable", message=_provider_message(error))
    finally:
        headers.clear()
    if response.status_code == 401:
        return _provider_payload("vendor", status="expired", message="<Vendor> rejected the configured key.")
    if response.status_code != 200:
        return _provider_payload("vendor", status="unavailable", message=f"HTTP {response.status_code}")
    body = response.json()
    windows = [_usage_window("Credits", kind="balance", remaining=_usage_number(body.get("remaining")), unit="USD")]
    return _provider_payload("vendor", status="ok", windows=windows)


register_provider(
    "vendor", "<Vendor>", "Hermes API key", _collect_vendor_usage,
    not_configured_message="No Hermes <Vendor> API key was found.",
    billing_keys=("vendor",),            # billing_provider values Hermes records for this account
    registry_ids=("vendor", "vendor-alias"),  # Hermes provider registry ids this adapter covers
    hosts=("api.vendor.example",),
    module=__name__,
)

__all__ = [name for name in globals() if not name.startswith("__")]
```

- `probe=` is optional. The default probe asks Hermes to resolve an API key for the adapter id and skips the network call when there is none. OAuth or portal logins need their own probe (see `codex.py`, `anthropic.py`, `nous.py`, `grok.py`); a probe must return `True` on any uncertainty so it never hides a configured provider.
- `via="hermes"` when Hermes' own code makes the request (`agent.account_usage`), `"direct"` when the adapter does.
- `order=` places the card; built-ins use 10–80, new adapters default to 100 and sort after them by module name.
- `billing_keys` join local `session_model_usage` records to the card ("recorded 7d", quota attribution, budgets). `registry_ids` keep the vendor out of the "configured but unreadable" list.

Window helpers in `_providers/shared.py`: `_usage_window(label, kind="quota"|"balance"|"money", used_percent=, reset_at=, detail=, limit=, used=, remaining=, unit=)`, `_usage_number`, `_usage_percent`, `_usage_iso`, `_amount_quota_window`, `_account_usage_payload` (for Hermes `AccountUsageSnapshot` objects).

## A non-model service (Services card or inventory row)

Create `dashboard/_services/<vendor>.py`:

```python
"""<Vendor> credit balance."""

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


def _collect_vendor() -> Dict[str, Any]:
    key, _ = _service_secret("VENDOR_API_KEY")
    if not key:
        return _service_payload("vendor", status="not_configured", message="No VENDOR_API_KEY is set in Hermes.")
    headers = {"Authorization": f"Bearer {key}", "Accept": "application/json"}
    try:
        code, body, error = _service_get("https://api.vendor.example/v1/balance", headers)
    finally:
        headers.clear()
    outcome = _credential_status("vendor", code, error)
    if outcome:
        return outcome
    remaining = _usage_number(body.get("credits")) if isinstance(body, Mapping) else None
    if remaining is None:
        return _service_payload("vendor", status="unavailable", message="<Vendor> returned no credit figure.")
    return _service_payload("vendor", status="ok", windows=[_usage_window("Credits", kind="balance", remaining=remaining, unit="credits")])


register_service(
    "vendor", "<Vendor>", "Hermes .env key", collect=_collect_vendor,
    env_keys=("VENDOR_API_KEY",),     # exact key NAMES in the Hermes .env that mean "configured"
    mcp_hints=("vendor",),            # substrings of an mcp_servers entry name
    hosts=("api.vendor.example",),
    module=__name__,
)

__all__ = [name for name in globals() if not name.startswith("__")]
```

- Discovery is by **key name**: `VENDOR_API_KEY` marks the service configured; `VENDOR_API_KEY_<SUFFIX>` is an extra account. Return extra cards as `result["extra_accounts"] = [_service_payload(..., account="suffix")]` (see `firecrawl.py`).
- `cli="vendor"` marks a service configured when that executable is on PATH; set `via="cli"` when the CLI, not the adapter, makes the request (see `monid.py`).
- `account_spend={"daily":, "weekly":, "monthly":, "unit": "USD"}` on the payload feeds the monthly budgets.
- No readable endpoint? Register with no `collect=` and a `note=` explaining why; the service is inventoried, shown as unreadable, and never contacted.

Helpers in `_services/shared.py`: `_service_payload`, `_service_secret` (environment first, `.env` fallback), `_service_get` (status, JSON, error), `_credential_status` (401/402/403/non-200 outcomes), `_dotenv_key_names`, `_service_for_env_key`.

## Tests

`tests/test_plugin_api.py` already checks that every module in the two packages registers an adapter, that direct adapters declare hosts, that unreadable services carry a note, and that the README lists every declared host. Add to that:

- a payload test for your parser (`_vendor_payload(body)`) with the real response shape you verified, including the empty and error shapes;
- a dispatcher test if you introduce a new behaviour (extra accounts, account spend), using the `_provider_collectors(...)` / `_service_collectors(...)` helpers to stub collectors on the registry;
- the README Trust table row for each new host.

Run `python -m unittest discover -s tests` with the Python environment Hermes uses.
