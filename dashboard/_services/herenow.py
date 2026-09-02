"""here.now: inventoried, not readable."""

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
    "herenow", "here.now", "Hermes .env key",
    env_keys=("HERE_NOW_API_KEY", "HERENOW_API_KEY"),
    note="here.now's API publishes sites but reports no plan, quota, or usage figure.",
    order=80, module=__name__,
)

__all__ = [name for name in globals() if not name.startswith("__")]
