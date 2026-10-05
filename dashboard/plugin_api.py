"""Hermes manifest façade for the Session Lens read-only API.

Hermes loads this file on its own, outside any package. The dashboard
directory is therefore loaded once as the uniquely named package
``session_lens_dashboard``, so its modules (``_common``, ``_routes``, …) never
sit on ``sys.path`` under bare names another plugin could also use.
"""

try:
    from ._routes import *  # imported as part of a package (the test suite)
except ImportError:  # Hermes loads this file directly
    import importlib.util
    import sys
    from pathlib import Path

    _PACKAGE = "session_lens_dashboard"
    if _PACKAGE not in sys.modules:
        _here = Path(__file__).resolve().parent
        _spec = importlib.util.spec_from_file_location(
            _PACKAGE, _here / "__init__.py", submodule_search_locations=[str(_here)]
        )
        _module = importlib.util.module_from_spec(_spec)
        sys.modules[_PACKAGE] = _module
        try:
            _spec.loader.exec_module(_module)
        except BaseException:
            sys.modules.pop(_PACKAGE, None)
            raise
    from session_lens_dashboard._routes import *  # noqa: F401,F403
