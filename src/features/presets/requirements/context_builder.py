"""Builds a `RequirementContext` from the process's live collaborators - the
one place preset requirements evaluation reaches into the models catalog,
GPU monitor, and backend registry, so checkers themselves never touch a
container.
"""

import sys
from typing import List, Optional

from src.features.backends.backend_registry import BackendRegistry
from src.features.models.collaborators import ModelIndexCollaborators
from src.features.presets.requirements.contracts import RequirementBackendInfo, RequirementContext
from src.features.presets.templates import PresetTemplate
from src.platform.runtime.gpu import GpuMonitor


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
    # The instantiated backend, not just its config - `execution_device` is a
    # class attribute on the backend implementation (see
    # `src.features.backends.base_backend.ExecutionDevice`), not something a
    # config ever carries. `get_backend` can still return `None` (e.g. the
    # config is enabled but the registry hasn't instantiated it yet) - the
    # `getattr` default below then reads "unestablished", same as any other
    # backend that hasn't declared where it executes.
    backend = backend_registry.get_backend(config.id)
    return RequirementBackendInfo(
        id=config.id,
        engine=config.engine,
        driver=config.driver or config.engine,
        config=config,
        name=config.name,
        execution_device=getattr(backend, "execution_device", "unestablished"),
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
            execution_device=getattr(backend, "execution_device", "unestablished"),
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
    # A local VRAM reading applies only when the resolved backend has
    # affirmatively declared it runs inference on this host's own GPU (see
    # `RequirementBackendInfo.execution_device`) - never inferred from the
    # backend's driver *name*. A plugin driver that happens to contain
    # "remote" proves nothing on its own, and a driver name that doesn't
    # contain it is equally not evidence this host's GPU is the one that
    # would run the preset (an in-process backend that only coordinates a
    # pipeline talking to some other server, e.g. the ComfyUI plugin's
    # backend, must not read as local either) - `execution_device` defaults
    # to "unestablished" for exactly that reason.
    executes_on_this_host_gpu = backend is not None and backend.execution_device == "this_host_gpu"
    gpu_total_vram_gb = None
    if gpu_available and executes_on_this_host_gpu:
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
