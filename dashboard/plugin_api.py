"""Hermes manifest façade for the Session Lens read-only API."""

try:
    from ._routes import *
except ImportError:  # pragma: no cover - Hermes loads this file directly
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _routes import *
