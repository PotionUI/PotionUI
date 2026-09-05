"""Builds a `RequirementContext` from the process's live collaborators - the
one place preset requirements evaluation reaches into the models catalog,
GPU monitor, and backend registry, so checkers themselves never touch a
container.
"""

import sys
from typing import List, Optional, Tuple

from src.features.backends.backend_registry import BackendRegistry
from src.features.backends.base_backend import ExecutionDeviceEvidence
from src.features.models.collaborators import ModelIndexCollaborators
from src.features.presets.requirements.contracts import RequirementBackendInfo, RequirementContext
from src.features.presets.templates import PresetTemplate
from src.platform.runtime.gpu import GpuMonitor

_UNESTABLISHED = ExecutionDeviceEvidence(kind="unestablished")

# The literal `pipe['config']` key `NativeBackend.prepare_pipes` `setdefault`s
# the backend's configured device onto - the same key a preset author can
# set explicitly under a pipe's `configuration:` block in `pipeline.yml`
# to win over it (see docs/presets.md's `vram_min_gb` paragraph).
_PIPE_DEVICE_KEY = "device"


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


def _resolve_gpu_reading(
    evidence: Optional[ExecutionDeviceEvidence],
    gpu_monitor: Optional[GpuMonitor],
    gpu_available: bool,
) -> Tuple[Optional[float], Optional[str]]:
    """`(gpu_total_vram_gb, reason)` from a backend's own resolved
    `ExecutionDeviceEvidence` - never inferred from the backend's driver
    *name* (an in-process backend that only coordinates a pipeline talking
    to some other server, e.g. the ComfyUI plugin's backend, must not read
    as local; that's "unestablished"), and never from "some GPU exists on
    this host" alone. `reason` is `None` when a reading WAS taken, or when
    the generic per-type detail (`VramMinGbRequirementChecker`'s own
    message) already says enough (no backend, remote, unestablished) - it
    carries a specific note only where there's something more precise to
    say about a `"this_host_gpu"` backend that still got no reading.

    The gate is physical IDENTITY, not an index: a `NativeBackend`
    configured for `cuda:1` is not satisfied by a monitor bound to a
    DIFFERENT physical card just because some enumeration index happens to
    match - NVML's enumeration order need not agree with CUDA's own
    (further remappable via `CUDA_VISIBLE_DEVICES`) ordinal numbering, so an
    integer match alone is never treated as proof. Both sides must report a
    `DeviceIdentity` and those identities must be equal (see
    `src.platform.runtime.gpu.DeviceIdentity`)."""
    if evidence is None:
        return None, None
    if evidence.kind == "no_gpu":
        return None, "this backend is explicitly configured with no GPU (device: cpu)"
    if evidence.kind in ("remote", "unestablished"):
        return None, None
    # kind == "this_host_gpu"
    if not gpu_available:
        return None, None
    monitor_identity = getattr(gpu_monitor, "device_identity", None)
    if evidence.identity is None:
        return None, evidence.reason or (
            "this backend's GPU identity could not be established "
            "(torch/CUDA unavailable, or the configured device index is out of range)"
        )
    if monitor_identity is None:
        return None, (
            "this host's GPU identity could not be established "
            "(NVML did not report a UUID for the monitored device)"
        )
    if evidence.identity != monitor_identity:
        return None, "this backend's configured GPU is not the specific GPU this process monitors (identity mismatch)"
    return gpu_monitor.get_total_vram() / 1024.0, None


def _preset_device_override(preset: PresetTemplate, backend_device: Optional[str]) -> Optional[str]:
    """A reason to distrust an otherwise-applicable GPU reading because the
    PRESET's own `pipeline.yml` authoring - not the backend's admin
    configuration - decides which device a pipe actually runs on:
    `NativeBackend.prepare_pipes` only `setdefault`s its configured device
    onto a pipe's config, so an explicit `configuration: {device: ...}` on
    any pipe in any of the preset's modes wins over it
    (`src/features/presets/processor.py`'s `_process_pipes` turns that
    block into `pipe['config']` verbatim, unrendered - see docs/presets.md).

    `None` when nothing overrides `device` at all, or the one literal
    override present already agrees with `backend_device` - checked
    STATICALLY, before any Jinja rendering (this evaluates independent of
    form data), so a templated value is conservatively treated as an
    override this check cannot resolve, never as "probably fine"."""
    if backend_device is None:
        return None
    for mode in preset.modes.values():
        for pipe in mode.pipes:
            if pipe.enabled is False:
                continue
            config = pipe.configuration or {}
            if _PIPE_DEVICE_KEY not in config:
                continue
            value = config[_PIPE_DEVICE_KEY]
            if not isinstance(value, str):
                # A non-string override (a dict/list/int, e.g. an
                # unresolved `@config:`-style indirection or an authoring
                # mistake) is not "no override" - it's a shape this check
                # cannot reason about at all, so it degrades conservatively
                # rather than silently falling through to "no override".
                return f"pipe '{pipe.name}'s device override is not a plain string (unresolvable device override)"
            if "{{" in value or "{%" in value:
                return (
                    f"pipe '{pipe.name}' overrides its device with a template "
                    "this check has no form data to resolve"
                )
            if value != backend_device:
                return (
                    f"pipe '{pipe.name}' overrides its device to '{value}', "
                    f"different from this backend's configured '{backend_device}'"
                )
    return None


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
    evidence = backend.execution_device if backend is not None else None
    gpu_total_vram_gb, gpu_unavailable_reason = _resolve_gpu_reading(evidence, gpu_monitor, gpu_available)

    if gpu_total_vram_gb is not None:
        # A reading would otherwise apply from the backend's own
        # device-identity evidence - but the preset's own pipeline
        # authoring can still put a DIFFERENT device on the pipe that would
        # actually run, in a way this backend-level evidence has no
        # visibility into (see `_preset_device_override`).
        backend_device = getattr(backend.config, "device", None) if backend is not None else None
        override_reason = _preset_device_override(preset, backend_device)
        if override_reason is not None:
            gpu_total_vram_gb = None
            gpu_unavailable_reason = override_reason

    return RequirementContext(
        models=models,
        gpu_available=gpu_available,
        gpu_total_vram_gb=gpu_total_vram_gb,
        backend=backend,
        platform=sys.platform,
        gpu_unavailable_reason=gpu_unavailable_reason,
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
