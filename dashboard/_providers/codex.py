"""codex quota provider adapter.

Hermes' own account-usage code reads the Codex sign-in (published plugin API
agent.account_usage); Session Lens never sees the token.
"""

from __future__ import annotations

from .._common import *
from .._hermes_compat import *
from .shared import *


def _collect_codex_usage() -> Dict[str, Any]:
    snapshot = _hermes_account_usage("openai-codex")
    if snapshot is None:
        return _provider_payload(
            "codex", status="not_configured",
            message="Hermes has no OpenAI Codex sign-in it can read usage for in this profile.",
        )
    return _account_usage_payload("codex", snapshot)


register_provider(
    "codex", "OpenAI Codex", "Hermes sign-in", _collect_codex_usage,
    probe=lambda: True,
    not_configured_message="No Hermes OpenAI Codex sign-in was found.",
    billing_keys=("openai-codex",),
    registry_ids=("openai-codex", "codex"),
    hosts=("chatgpt.com",), via="hermes", order=10, module=__name__,
)

__all__ = [name for name in globals() if not name.startswith("__")]
