"""Hermes Session Lens agent-plugin entrypoint.

The package's agent-side registration is intentionally empty. Its Python code
is a read-only dashboard/desktop API mounted by Hermes from
``dashboard/plugin_api.py``.
"""


def register(_ctx):
    """Register no agent tools, hooks, middleware, or prompt content."""

