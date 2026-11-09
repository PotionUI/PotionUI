"""Contributing an engine.

An *engine* is the protocol a pipeline speaks; a *backend* is one configured
instance of it. A plugin contributes an engine by registering it through the
`backend.register` hook and subclassing `InProcessBackend` to execute a
pipeline against it.

`BaseBackendConfig` declares the connection settings an instance needs - the
admin UI renders its fields, so what you declare is what the admin can set.

If the engine can enumerate the models it holds, return `BackendModel` entries
and run them through `deduplicate`; raise `ModelListingNotSupported` if it
cannot, which is a fact about the engine, not a failure. `BackendModel.confidence`
derives from what you set on `size`/`sha256` - it's one of `CONFIDENCE_VERIFIED`,
`CONFIDENCE_REPORTED` or `CONFIDENCE_NAME_ONLY`.

See docs/backends.md.
"""

from typing import Any, Dict, List, Optional

from src.features.backends.backend_config import (
    BaseBackendConfig,
    BackendHealth,
    BackendStatus,
)
from src.features.backends.in_process_backend import InProcessBackend
from src.features.backends.model_listing import (
    BackendModel,
    CONFIDENCE_CONFLICT,
    CONFIDENCE_NAME_ONLY,
    CONFIDENCE_REPORTED,
    CONFIDENCE_VERIFIED,
    ModelListingNotSupported,
    deduplicate,
)
from src.features.backends.routes import enabled_backends_payload
from src.features.generation.memory_advisory import resolve_device_evidence
from src.features.generation.routing.contracts import Candidate, RoutingRequest, RoutingRule
from src.platform.plugins.hooks import HookContext
from src.platform.plugins.runtime_registries import get_container


def list_backends() -> List[Dict[str, Any]]:
    container = get_container()
    registry = container.backend_registry
    listed = []
    for entry in enabled_backends_payload(registry.backend_config_store):
        backend = registry.get_backend(entry["id"])
        device = resolve_device_evidence(backend, container.gpu_monitor)
        listed.append({
            **entry,
            "execution_device": "unestablished" if backend is None else backend.resolve_execution_device().kind,
            "max_concurrent_runs": None if backend is None else backend.max_concurrent_runs,
            "vram_total_gb": device.total_gb,
            "vram_free_gb": device.free_gb,
        })
    return listed


def register_routing_rule(context: HookContext, rule: RoutingRule, position: Optional[str] = None) -> None:
    """Call from a `backend.register_routing_rules` hook handler to insert
    `rule` into the generation router's chain. `position` is
    `"before:<rule name>"` / `"after:<rule name>"` (an anchor that doesn't
    match any built-in or earlier-registered rule falls back to appending,
    logged) or omitted to append at the end. See docs/generation-routing.md
    "Adding a rule from a plugin"."""
    context.data.setdefault("rules", []).append({"rule": rule, "position": position})


__all__ = [
    "BackendHealth",
    "BackendModel",
    "BackendStatus",
    "BaseBackendConfig",
    "Candidate",
    "CONFIDENCE_CONFLICT",
    "CONFIDENCE_NAME_ONLY",
    "CONFIDENCE_REPORTED",
    "CONFIDENCE_VERIFIED",
    "InProcessBackend",
    "ModelListingNotSupported",
    "RoutingRequest",
    "RoutingRule",
    "deduplicate",
    "list_backends",
    "register_routing_rule",
]
