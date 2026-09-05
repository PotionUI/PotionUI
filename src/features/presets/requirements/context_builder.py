"""Builds a `RequirementContext` from the process's live collaborators - the
one place preset requirements evaluation reaches into the models catalog,
GPU monitor, and backend registry, so checkers themselves never touch a
container.
"""

import sys
from typing import List, Optional

from src.features.backends.backend_registry import BackendRegistry
from src.features.backends.base_backend import ExecutionDeviceEvidence
from src.features.models.collaborators import ModelIndexCollaborators
from src.features.presets.requirements.contracts import RequirementBackendInfo, RequirementContext
from src.features.presets.templates import PresetTemplate
from src.platform.runtime.gpu import GpuMonitor

_UNESTABLISHED = ExecutionDeviceEvidence(kind="unestablished")


def _resolve_execution_device(backend) -> ExecutionDeviceEvidence:
    """`backend.resolve_execution_device()` when the (possibly `None`, or
    test-double) instance implements it, else the safe default. Duck-typed
    rather than an `isinstance(backend, BaseBackend)` check, matching this
    module's existing tolerance for backend/test-double shapes that don't
    carry every attribute a real `BaseBackend` does."""
    resolve = getattr(backend, "resolve_execution_device", None)
    return resolve() if resolve is not None else _UNESTABLISHED


def resolve_backend_id(backend_registry: Optional[BackendRegistry], engine: str) -> Optional[str]:
    """The id of the engine's default backend, or `None` if there isn't one
    (no backend configured for this engine yet) - the cheap half of backend
    resolution, used on its own by the preset list/detail endpoints to build
    a `RequirementsCache` lookup key without constructing a full context."""
    if backend_registry is None:
        return None
    config = backend_registry.backend_config_store.get_default_backend(engine)
    return config.id if config is not None else None


def _resolve_backend(backend_registry: Optional[BackendRegistry], engine: str) -> Optional[RequirementBackendInfo]:
    if backend_registry is None:
        return None
    config = backend_registry.backend_config_store.get_default_backend(engine)
    if config is None:
        return None
    # The instantiated backend, not just its config - `resolve_execution_device()`
    # is an instance method on the backend implementation (see
    # `src.features.backends.base_backend.BaseBackend`), not something a
    # config ever carries. `get_backend` can still return `None` (e.g. the
    # config is enabled but the registry hasn't instantiated it yet) - the
    # `_resolve_execution_device` fallback then reads "unestablished", same
    # as any other backend that hasn't declared where it executes.
    backend = backend_registry.get_backend(config.id)
    return RequirementBackendInfo(
        id=config.id,
        engine=config.engine,
        driver=config.driver or config.engine,
        config=config,
        name=config.name,
        execution_device=_resolve_execution_device(backend),
    )


def backend_infos_for_engine(backend_registry: Optional[BackendRegistry], engine: str) -> List[RequirementBackendInfo]:
    """Every enabled, available backend providing `engine`, highest priority
    first - exactly `BackendRegistry.get_backends_for_engine`'s candidate
    set, so a preset's per-backend requirements evaluation always covers the
    backends generation routing could actually pick between. Empty when
    `backend_registry` is `None` (a test/tooling context that never wired
    one up) or no backend of this engine is enabled."""
    if backend_registry is None:
        return []
    return [
        RequirementBackendInfo(
            id=backend.backend_id,
            engine=backend.engine,
            driver=backend.config.driver or backend.engine,
            config=backend.config,
            name=backend.name,
            execution_device=_resolve_execution_device(backend),
        )
        for backend in backend_registry.get_backends_for_engine(engine)
    ]


def build_requirement_context_for_backend(
    preset: PresetTemplate,
    models: Optional[ModelIndexCollaborators],
    gpu_monitor: Optional[GpuMonitor],
    backend: Optional[RequirementBackendInfo],
) -> RequirementContext:
    """Same as `build_requirement_context`, but against an already-resolved
    `backend` (or `None` for a pure host context) rather than looking up the
    engine's default - the building block
    `evaluate_preset_requirements_for_backends` uses to check a preset
    against one specific backend of its engine."""
    gpu_available = bool(gpu_monitor is not None and gpu_monitor.available)
    # A local VRAM reading applies only when the resolved backend's OWN
    # evidence (see `RequirementBackendInfo.execution_device`,
    # `ExecutionDeviceEvidence`) affirmatively says so - never inferred from
    # the backend's driver *name* (an in-process backend that only
    # coordinates a pipeline talking to some other server, e.g. the ComfyUI
    # plugin's backend, must not read as local; that's "unestablished").
    # Nor is "some GPU exists on this host" enough on its own: a
    # `NativeBackend` configured for `cuda:1` is not satisfied by a monitor
    # bound to a DIFFERENT device - `gpu_monitor.device_index` must match
    # the backend's own resolved index, or this stays `unknown` rather than
    # borrowing another GPU's reading. A `NativeBackend` explicitly
    # configured with no GPU at all (`kind="no_gpu"`, e.g. `device="cpu"`)
    # never matches either, for the same reason.
    evidence = backend.execution_device if backend is not None else None
    monitor_device_index = getattr(gpu_monitor, "device_index", 0)
    reading_applies = (
        evidence is not None
        and evidence.kind == "this_host_gpu"
        and evidence.gpu_index == monitor_device_index
    )
    gpu_total_vram_gb = None
    if gpu_available and reading_applies:
        gpu_total_vram_gb = gpu_monitor.get_total_vram() / 1024.0

    return RequirementContext(
        models=models,
        gpu_available=gpu_available,
        gpu_total_vram_gb=gpu_total_vram_gb,
        backend=backend,
        platform=sys.platform,
    )


def build_requirement_context(
    preset: PresetTemplate,
    models: Optional[ModelIndexCollaborators],
    gpu_monitor: Optional[GpuMonitor],
    backend_registry: Optional[BackendRegistry],
) -> RequirementContext:
    """Gather everything `evaluate_preset_requirements` needs to check
    `preset.requirements` against this instance, once per evaluation -
    against the preset's engine's DEFAULT backend (or no backend at all, if
    none is configured). Host-scoped checkers (the only ones this context is
    meant for outside of `evaluate_preset_requirements_for_backends`) never
    read `ctx.backend` for anything but the VRAM-reading's execution-device
    guard above."""
    backend = _resolve_backend(backend_registry, preset.engine)
    return build_requirement_context_for_backend(preset, models, gpu_monitor, backend)
