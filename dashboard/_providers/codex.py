"""codex quota provider adapter."""

from __future__ import annotations

try:
    from .._common import *
    from .._hermes_compat import *
    from .shared import *
except ImportError:  # pragma: no cover
    from _common import *
    from _hermes_compat import *
    from _providers.shared import *

def _collect_codex_usage() -> Dict[str, Any]:
    try:
        from agent.account_usage import fetch_account_usage

        return _account_usage_payload("codex", fetch_account_usage("openai-codex"))
    except Exception as error:
        return _provider_payload("codex", status="unavailable", message=_provider_message(error))

__all__ = [name for name in globals() if not name.startswith("__")]
