"""Model-provider adapters, one module per vendor.

Every module in this package other than ``shared`` is imported here, so a new
provider is a new file that calls ``register_provider(...)`` — nothing else
needs editing. See ADAPTERS.md at the repository root.
"""

import importlib
import pkgutil

from .shared import *  # noqa: F401,F403  (registry, payload helpers)

for _module_info in pkgutil.iter_modules(__path__):
    if _module_info.name.startswith("_") or _module_info.name == "shared":
        continue
    _module = importlib.import_module(f"{__name__}.{_module_info.name}")
    globals().update({_name: _value for _name, _value in vars(_module).items() if not _name.startswith("__")})
globals().pop("_module", None)
globals().pop("_module_info", None)

__all__ = [name for name in globals() if not name.startswith("__")]
