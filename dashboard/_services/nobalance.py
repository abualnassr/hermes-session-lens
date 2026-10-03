"""Services Hermes users configure that publish no balance or usage API.

Registered so the inventory names them correctly and says why there is no
card, instead of listing a bare key name.
"""

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
    "typesafe", "TypeSafe", "Hermes .env key",
    env_keys=("TYPESAFE_API_KEY",),
    note="TypeSafe publishes no balance or usage API; each response reports only its own token use.",
    order=85, module=__name__,
)

register_service(
    "reef", "Reef", "Hermes .env key",
    env_keys=("REEF_API_KEY",),
    note="Reef publishes no balance API; calls report only the credits each one charged.",
    order=86, module=__name__,
)

__all__ = [name for name in globals() if not name.startswith("__")]
