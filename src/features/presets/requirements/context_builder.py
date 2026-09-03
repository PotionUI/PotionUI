"""Builds a `RequirementContext` from the process's live collaborators - the
one place preset requirements evaluation reaches into the models catalog,
GPU monitor, and backend registry, so checkers themselves never touch a
container.
"""

import sys
from typing import Optional

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
    return RequirementBackendInfo(
        id=config.id,
        engine=config.engine,
        driver=config.driver or config.engine,
        config=config,
    )


def build_requirement_context(
    preset: PresetTemplate,
    models: Optional[ModelIndexCollaborators],
    gpu_monitor: Optional[GpuMonitor],
    backend_registry: Optional[BackendRegistry],
) -> RequirementContext:
    """Gather everything `evaluate_preset_requirements` needs to check
    `preset.requirements` against this instance, once per evaluation."""
    backend = _resolve_backend(backend_registry, preset.engine)

    gpu_available = bool(gpu_monitor is not None and gpu_monitor.available)
    # No local VRAM reading for a remote backend - the GPU this process can
    # see (if any) isn't the one the preset would actually run on.
    is_remote_backend = backend is not None and "remote" in (backend.driver or "")
    gpu_total_vram_gb = None
    if gpu_available and not is_remote_backend:
        gpu_total_vram_gb = gpu_monitor.get_total_vram() / 1024.0

    return RequirementContext(
        models=models,
        gpu_available=gpu_available,
        gpu_total_vram_gb=gpu_total_vram_gb,
        backend=backend,
        platform=sys.platform,
    )
