"""Adapter registry: one entry per vendor Session Lens can inventory or read.

Two kinds of adapter exist, and both are declared the same way:

* **provider** — a model provider whose account allowance or balance appears
  on AI Usage (OpenRouter credits, Anthropic OAuth windows, ...). It carries a
  local credential probe, a collector, the Hermes registry ids it covers, and
  the ``billing_provider`` keys that join local records to its windows.
* **service** — a non-model service (Firecrawl, Monid, ...) discovered from
  key names in the Hermes ``.env``, ``mcp_servers`` in ``config.yaml``, or a
  CLI on PATH. A service may have no collector at all; it is then listed as
  configured-but-unreadable with the reason spelled out.

Vendor modules register themselves at import time (``_providers/<vendor>.py``
and ``_services/<vendor>.py``); the packages import every module they hold, so
adding a vendor is adding a file, never editing a dispatcher. Every adapter
declares the hosts it contacts, and ``GET /adapters`` publishes the whole
registry so the trust boundary stays inspectable at runtime.
"""

from __future__ import annotations

import itertools
from typing import Any, Callable, Dict, List, Optional, Tuple

_registration_sequence = itertools.count()


class Adapter:
    """One vendor Session Lens knows how to inventory, probe, or read."""

    __slots__ = (
        "kind", "id", "label", "auth_source", "collect", "probe", "not_configured_message",
        "billing_keys", "registry_ids", "hosts", "via", "env_keys", "mcp_hints", "cli", "note",
        "order", "sequence", "module", "request_kind",
    )

    def __init__(
        self,
        kind: str,
        id: str,
        label: str,
        auth_source: str,
        *,
        collect: Optional[Callable[[], Dict[str, Any]]] = None,
        probe: Optional[Callable[[], bool]] = None,
        not_configured_message: Optional[str] = None,
        billing_keys: Tuple[str, ...] = (),
        registry_ids: Tuple[str, ...] = (),
        hosts: Tuple[str, ...] = (),
        via: str = "direct",
        env_keys: Tuple[str, ...] = (),
        mcp_hints: Tuple[str, ...] = (),
        cli: Optional[str] = None,
        note: Optional[str] = None,
        order: int = 100,
        module: Optional[str] = None,
        request_kind: str = "usage_endpoint",
    ) -> None:
        if kind not in {"provider", "service"}:
            raise ValueError(f"unknown adapter kind {kind!r}")
        if not id or id != id.strip().lower():
            raise ValueError(f"adapter id must be a lowercase slug, got {id!r}")
        if via not in {"direct", "hermes", "cli", "none"}:
            raise ValueError(f"unknown adapter via {via!r}")
        if request_kind not in {"usage_endpoint", "inference_probe"}:
            raise ValueError(f"unknown adapter request_kind {request_kind!r}")
        if request_kind == "inference_probe" and not note:
            raise ValueError(f"adapter {id!r} sends an inference probe and must say so in note")
        self.kind = kind
        self.id = id
        self.label = label
        self.auth_source = auth_source
        self.collect = collect
        self.probe = probe
        self.not_configured_message = not_configured_message
        self.billing_keys = tuple(billing_keys)
        self.registry_ids = tuple(registry_ids)
        self.hosts = tuple(hosts)
        self.via = via if collect is not None else "none"
        self.env_keys = tuple(str(key).upper() for key in env_keys)
        self.mcp_hints = tuple(str(hint).lower() for hint in mcp_hints)
        self.cli = cli
        self.note = note
        self.order = int(order)
        self.sequence = next(_registration_sequence)
        self.module = module
        self.request_kind = request_kind if collect is not None else "usage_endpoint"

    @property
    def readable(self) -> bool:
        return self.collect is not None

    def describe(self) -> Dict[str, Any]:
        """The public, credential-free description served by ``GET /adapters``."""
        item: Dict[str, Any] = {
            "id": self.id,
            "kind": self.kind,
            "label": self.label,
            "auth_source": self.auth_source,
            "readable": self.readable,
            "via": self.via,
            "hosts": list(self.hosts),
            "module": self.module,
            "request_kind": self.request_kind,
        }
        if self.kind == "provider":
            item["registry_ids"] = list(self.registry_ids)
            item["billing_keys"] = list(self.billing_keys)
        else:
            item["env_keys"] = list(self.env_keys)
            item["mcp_hints"] = list(self.mcp_hints)
            item["cli"] = self.cli
        if self.note:
            item["note"] = self.note
        return item


_PROVIDERS: Dict[str, Adapter] = {}
_SERVICES: Dict[str, Adapter] = {}


def _register(table: Dict[str, Adapter], adapter: Adapter) -> Adapter:
    table[adapter.id] = adapter
    ordered = sorted(table.values(), key=lambda item: (item.order, item.sequence))
    table.clear()
    for item in ordered:
        table[item.id] = item
    return adapter


def register_provider(
    id: str,
    label: str,
    auth_source: str,
    collect: Callable[[], Dict[str, Any]],
    *,
    probe: Optional[Callable[[], bool]] = None,
    not_configured_message: Optional[str] = None,
    billing_keys: Tuple[str, ...] = (),
    registry_ids: Tuple[str, ...] = (),
    hosts: Tuple[str, ...] = (),
    via: str = "direct",
    order: int = 100,
    module: Optional[str] = None,
    request_kind: str = "usage_endpoint",
    note: Optional[str] = None,
) -> Adapter:
    """Declare a model-provider adapter.

    ``probe`` is a local-only credential check; None means "resolve a Hermes
    API key for this provider id". ``registry_ids`` are the Hermes provider
    registry ids (and aliases) this adapter covers, so they never appear in
    the "configured but unreadable" list. ``billing_keys`` are the
    ``billing_provider`` values local records use for this account.
    ``request_kind`` is ``usage_endpoint`` unless the adapter has to send a
    token-sized inference request to read rate-limit headers, in which case
    it is ``inference_probe`` and ``note`` must say what is sent and how often.
    """
    return _register(
        _PROVIDERS,
        Adapter(
            "provider", id, label, auth_source, collect=collect, probe=probe,
            not_configured_message=not_configured_message or f"No Hermes {label} credential was found.",
            billing_keys=billing_keys, registry_ids=tuple(registry_ids) or (id,), hosts=hosts, via=via,
            order=order, module=module, request_kind=request_kind, note=note,
        ),
    )


def register_service(
    id: str,
    label: str,
    auth_source: str,
    *,
    collect: Optional[Callable[[], Dict[str, Any]]] = None,
    env_keys: Tuple[str, ...] = (),
    mcp_hints: Tuple[str, ...] = (),
    cli: Optional[str] = None,
    note: Optional[str] = None,
    hosts: Tuple[str, ...] = (),
    via: str = "direct",
    order: int = 100,
    module: Optional[str] = None,
) -> Adapter:
    """Declare a non-model service adapter.

    ``env_keys`` are the exact key NAMES that mark the service as configured
    (a ``<KEY>_<suffix>`` variant counts as an extra account); ``mcp_hints``
    are substrings of an ``mcp_servers`` entry name; ``cli`` is an executable
    on PATH. Without ``collect`` the service is inventoried and ``note`` must
    say why its balance cannot be read.
    """
    if collect is None and not note:
        raise ValueError(f"service adapter {id!r} has no collector and no note explaining why")
    return _register(
        _SERVICES,
        Adapter(
            "service", id, label, auth_source, collect=collect, env_keys=env_keys, mcp_hints=mcp_hints,
            cli=cli, note=note, hosts=hosts, via=via, order=order, module=module,
        ),
    )


def _provider_adapters() -> Dict[str, Adapter]:
    return _PROVIDERS


def _service_adapters() -> Dict[str, Adapter]:
    return _SERVICES


def _provider_ids() -> Tuple[str, ...]:
    return tuple(_PROVIDERS)


def _service_ids() -> Tuple[str, ...]:
    return tuple(_SERVICES)


def _adapter_for(adapter_id: str) -> Optional[Adapter]:
    key = str(adapter_id or "").strip().lower()
    return _PROVIDERS.get(key) or _SERVICES.get(key)


def _adapter_label(adapter_id: str) -> Optional[str]:
    adapter = _adapter_for(adapter_id)
    return adapter.label if adapter else None


def _usage_billing_keys() -> Dict[str, Tuple[str, ...]]:
    return {adapter.id: adapter.billing_keys for adapter in _PROVIDERS.values() if adapter.billing_keys}


def _usage_covered_provider_ids() -> set:
    covered: set = set()
    for adapter in _PROVIDERS.values():
        covered.add(adapter.id)
        covered.update(adapter.registry_ids)
    return covered


def _inference_probe_adapters() -> List[Dict[str, Any]]:
    """Adapters that send a token-sized inference request instead of calling a usage endpoint."""
    return [
        {"id": adapter.id, "label": adapter.label, "hosts": list(adapter.hosts), "note": adapter.note}
        for adapter in list(_PROVIDERS.values()) + list(_SERVICES.values())
        if adapter.request_kind == "inference_probe"
    ]


def _adapter_hosts() -> List[str]:
    hosts: List[str] = []
    for adapter in list(_PROVIDERS.values()) + list(_SERVICES.values()):
        for host in adapter.hosts:
            if host not in hosts:
                hosts.append(host)
    return sorted(hosts)


def _adapters_catalog() -> Dict[str, Any]:
    """Everything registered, credential-free, for ``GET /adapters``."""
    return {
        "providers": [adapter.describe() for adapter in _PROVIDERS.values()],
        "services": [adapter.describe() for adapter in _SERVICES.values()],
        "hosts": _adapter_hosts(),
        "definition": (
            "Every vendor Session Lens can inventory or read, as declared by its adapter module. "
            "'hosts' are the only external hosts the backend contacts, each with the credential Hermes already holds; "
            "'via: hermes' means Hermes' own account-usage code makes the request, 'cli' means a local vendor CLI does. "
            "Credentials never reach the desktop and nothing here is written back."
        ),
    }


__all__ = [name for name in globals() if not name.startswith("__")]
