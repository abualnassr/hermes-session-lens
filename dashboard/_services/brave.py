"""Brave Search: inventoried, not readable."""

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


register_service(
    "brave", "Brave Search", "Hermes .env key",
    env_keys=("BRAVE_SEARCH_API_KEY", "BRAVE_API_KEY"), mcp_hints=("brave",),
    note="Brave exposes no balance endpoint; its rate-limit headers appear only on a paid query, which Session Lens will not spend.",
    order=60, module=__name__,
)

__all__ = [name for name in globals() if not name.startswith("__")]
