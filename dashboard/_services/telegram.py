"""Telegram bot token: inventoried, nothing to meter."""

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
    "telegram", "Telegram bot", "Hermes .env token",
    env_keys=("TELEGRAM_BOT_TOKEN",),
    note="A bot token has nothing to meter.",
    order=70, module=__name__,
)

__all__ = [name for name in globals() if not name.startswith("__")]
