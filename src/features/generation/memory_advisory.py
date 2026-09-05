"""
Request memory advisory: a non-blocking, best-effort estimate of the GPU
memory ONE generation request would need, computed without starting anything
- no enqueue, no hook run, no model load, no GPU allocation. See
`GenerationOrchestrator.preview_memory` (the `/api/generations/memory-preview`
route) for the operation that calls this module, and
`docs/user/hardware-requirements.md` for how this fits alongside a preset's
static `requires:` badge and a backend's configured `gpu_max_vram`.

Deliberately conservative in what it claims: `estimate_request_memory` never
returns a fit verdict, only a checkpoint-based estimate plus the
coverage/uncertainty evidence a caller needs to judge it - never "a lower
bound" or "at least", since quantization/streaming can bring real residency
below it and a preset-pinned component outside the active loader walk can
push real usage above it. The weight-margin/activation-term
constants below are also what `GenerationOrchestrator._estimate_generation_vram_gb`
(the `generation.before_start` hook payload) uses - they live here, and that
function imports them, rather than the reverse, so this module never has to
import the orchestrator back.
"""

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from src.features.generation.model_identity import _LOADER_PIPE_FAMILIES
from src.features.models.form_refs import collect_model_ids
from src.platform.runtime.gpu import effective_vram_budget_gb

ModelLookup = Callable[[str], Optional[Any]]

# Loaded weights run a little over their on-disk size (allocator slack, dtype
# staging). The resolution-scaled activation spike is priced separately below,
# so this stays a small weight-only overhead rather than a flat blanket margin.
_WEIGHT_LOAD_MARGIN = 1.1

# Sampling-phase activation spike over resident weights, per VAE-latent pixel.
# Mirrors `_SAMPLING_MB_PER_LATENT_PX` in
# src/platform/runtime/native/memory/tiering.py (0.1 MB/latent-px, calibrated
# as the total peak-over-weights). Reproduced here rather than imported: that
# module pulls in vendor.gpl/torch, too heavy for the submission/preview path.
# At 1024^2 this is ~1.6 GB, the right order of magnitude for the DiT forward
# spike.
_SAMPLING_ACT_MB_PER_LATENT_PX = 0.1
_VAE_SPATIAL_DOWNSCALE = 8
# Video latents compress in time too (Wan 4x, LTX 8x); 4 is a nominal middle so
# the term is monotone in frame count without pretending to per-model accuracy.
_NOMINAL_TEMPORAL_DOWNSCALE = 4

# `BaseBackend.execution_device` class-level declarations (REQ-01,
# src/features/backends/base_backend.py): "this_host_gpu" for NativeBackend,
# "remote" for RemoteNativeBackend, "unestablished" for InProcessBackend and
# anything that hasn't opted in (e.g. a plugin backend like ComfyUI's, whose
# actual host is admin-configured and not something a driver NAME can tell
# you - a `"remote" in driver` substring check is wrong for exactly that
# backend, since its driver is literally "comfyui"). Read with a `getattr`
# default until the attribute lands on every backend.
_EXECUTION_DEVICE_THIS_HOST = "this_host_gpu"
_EXECUTION_DEVICE_REMOTE = "remote"


def _parse_resolution(form_data: Dict[str, Any]) -> Optional[tuple]:
    """(width, height) from the form's `resolution` "WxH" string or explicit
    width/height ints, or None when neither is present."""
    res = form_data.get("resolution")
    if isinstance(res, str) and "x" in res.lower():
        try:
            w_str, h_str = res.lower().split("x")
            return int(w_str.strip()), int(h_str.strip())
        except (ValueError, TypeError):
            pass
    w, h = form_data.get("width"), form_data.get("height")
    if isinstance(w, int) and isinstance(h, int) and w > 0 and h > 0:
        return w, h
    return None


def _frame_count(form_data: Dict[str, Any]) -> int:
    """Requested frame count from whichever key a preset uses, or 1 (still image).
    Video presets that don't expose a frame field fall through to 1 - their
    (large) model weights dominate the estimate regardless."""
    for key in ("num_frames", "frames", "video_length", "length", "frame_count"):
        value = form_data.get(key)
        if isinstance(value, int) and value > 0:
            return value
    return 1


def _activation_headroom_gb(form_data: Dict[str, Any]) -> float:
    """Resolution/frames-scaled sampling activation spike, in GB. 0 when the form
    carries no resolution."""
    wh = _parse_resolution(form_data)
    if wh is None:
        return 0.0
    width, height = wh
    latent_px = (width / _VAE_SPATIAL_DOWNSCALE) * (height / _VAE_SPATIAL_DOWNSCALE)
    latent_frames = max(1, round(_frame_count(form_data) / _NOMINAL_TEMPORAL_DOWNSCALE))
    return latent_px * latent_frames * _SAMPLING_ACT_MB_PER_LATENT_PX / 1024.0


def _active_loader_pipes(pipes: List[Dict[str, Any]]):
    """Enabled pipes belonging to a loader family - the same
    enabled+family filter `model_identity.resolve_model_identity` applies, so
    a disabled loader's unused selection never counts as "active"."""
    for pipe in pipes:
        if not pipe.get("enabled"):
            continue
        name = pipe.get("name") or ""
        family = name.split("/", 1)[0]
        if family in _LOADER_PIPE_FAMILIES:
            yield pipe


def active_model_ids(pipes: List[Dict[str, Any]]) -> List[str]:
    """Every `model:<id>` reference on an ENABLED loader-family pipe, in
    first-seen order. Unlike `model_identity.resolve_model_identity`, which
    keeps only checkpoint-class ids for its affinity signature, every
    reference counts here (checkpoint, LoRA, VAE, text encoder alike) - each
    one occupies real VRAM once loaded."""
    ids: List[str] = []
    seen = set()
    for pipe in _active_loader_pipes(pipes):
        for model_id in collect_model_ids(pipe.get("config") or {}):
            if model_id not in seen:
                seen.add(model_id)
                ids.append(model_id)
    return ids


def active_loader_settings(pipes: List[Dict[str, Any]]) -> List[str]:
    """`<pipe>.dtype=<value>` / `<pipe>.quant*=<value>` strings for every
    ENABLED loader-family pipe - the same load-relevant settings
    `model_identity.resolve_model_identity` folds into its affinity
    signature, surfaced here as coverage uncertainty instead."""
    settings: List[str] = []
    for pipe in _active_loader_pipes(pipes):
        config = pipe.get("config") or {}
        pipe_key = pipe.get("id") or pipe.get("name") or ""
        for key, value in config.items():
            if key == "dtype" or key.lower().startswith("quant"):
                settings.append(f"{pipe_key}.{key}={value}")
    return settings


@dataclass
class PipeVramHint:
    """One ENABLED pipe's own `vram_limit_gb`, keyed by pipe id/name - plural
    because these are per-STAGE hints (e.g. a tiled detailer's own tile
    budget), never a single figure for the whole request. A preview build
    never reaches `NativeBackend.prepare_pipes` (that injection happens
    downstream, at execution time), so a value found here can only be one a
    pipe/preset declared itself - e.g. `tiled_detailer/sdxl`'s own default -
    never a backend's cap."""
    pipe: str
    hint_gb: float


def active_pipe_vram_hints(pipes: List[Dict[str, Any]]) -> List[PipeVramHint]:
    """Every ENABLED pipe's own explicit `vram_limit_gb`, one entry per pipe.
    Never collapse this into a single number and present it as THE request
    budget - see `resolve_budget_evidence`'s docstring for why."""
    hints: List[PipeVramHint] = []
    for pipe in pipes:
        if not pipe.get("enabled"):
            continue
        hint = (pipe.get("config") or {}).get("vram_limit_gb")
        if hint is None:
            continue
        try:
            hints.append(PipeVramHint(pipe=pipe.get("id") or pipe.get("name") or "", hint_gb=float(hint)))
        except (TypeError, ValueError):
            continue
    return hints


@dataclass
class KnownModel:
    ref: str
    size_gb: float

    def to_dict(self) -> Dict[str, Any]:
        return {"ref": self.ref, "size_gb": self.size_gb}


@dataclass
class Coverage:
    known: List[KnownModel]
    unknown: List[str]
    active_set_resolved: bool
    pinned_components_uncounted: bool
    uncertainty: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "known": [k.to_dict() for k in self.known],
            "unknown": list(self.unknown),
            "active_set_resolved": self.active_set_resolved,
            "pinned_components_uncounted": self.pinned_components_uncounted,
            "uncertainty": list(self.uncertainty),
        }


@dataclass
class Estimate:
    # A CHECKPOINT-BASED estimate, never framed as a guaranteed minimum:
    # quantization/streaming can bring real residency below it, and a
    # component the preset pins outside a form picker can push real usage
    # above it. See `estimate_request_memory`'s docstring.
    checkpoint_estimate_gb: Optional[float]
    weights_gb: float
    activation_gb: float
    margin: float
    basis: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "checkpoint_estimate_gb": self.checkpoint_estimate_gb,
            "weights_gb": self.weights_gb,
            "activation_gb": self.activation_gb,
            "margin": self.margin,
            "basis": self.basis,
        }


@dataclass
class MemoryEstimateResult:
    estimate: Estimate
    coverage: Coverage

    def to_dict(self) -> Dict[str, Any]:
        return {"estimate": self.estimate.to_dict(), "coverage": self.coverage.to_dict()}


def estimate_request_memory(
    pipes: Optional[List[Dict[str, Any]]],
    model_lookup: ModelLookup,
    form_data: Dict[str, Any],
) -> MemoryEstimateResult:
    """A CHECKPOINT-BASED estimate of the GPU memory a request that has NOT
    started would need: (summed on-disk size of every model reference on an
    ACTIVE loader pipe x weight-load margin) + a resolution/frames-scaled
    sampling activation term.

    Deliberately never called a "lower bound" or "at least" anywhere in this
    module or its callers: quantization/dtype settings and low-VRAM streaming
    can bring REAL residency below this number (see `coverage.uncertainty`),
    while a component the preset pins outside a form picker - never part of
    the active loader walk - can push real usage ABOVE it. It is a
    checkpoint-weight-based estimate, not a guarantee in either direction.

    `pipes` is the built pipeline's processed pipe list (see
    `PipelineBuilder.build_pipeline`), or `None` when the active model set
    could not be resolved at all (the build itself failed, including a
    Video/Music Director document that failed its own preparation) - the
    estimate then carries no known weights and `coverage.active_set_resolved`
    is False.

    `checkpoint_estimate_gb` is `None` only when nothing is resolvable: no
    active model reference, or none of their sizes are indexed - weights are
    the anchor, so an activation term alone would be a meaningless number.
    Coverage/uncertainty always describe what was and wasn't counted, so a
    caller never has to infer confidence from the number alone.
    """
    form_data = form_data or {}
    active_set_resolved = pipes is not None
    model_ids = active_model_ids(pipes) if pipes is not None else []
    settings = active_loader_settings(pipes) if pipes is not None else []

    known: List[KnownModel] = []
    unknown: List[str] = []
    total_bytes = 0
    for model_id in model_ids:
        try:
            model = model_lookup(model_id)
        except Exception:
            model = None
        size = getattr(model, "file_size", None) if model is not None else None
        if size:
            total_bytes += int(size)
            known.append(KnownModel(ref=model_id, size_gb=round(int(size) / (1024 ** 3), 2)))
        else:
            unknown.append(model_id)

    weights_gb = round(total_bytes / (1024 ** 3), 2)
    activation_gb = round(_activation_headroom_gb(form_data), 2)
    checkpoint_estimate_gb = (
        round(weights_gb * _WEIGHT_LOAD_MARGIN + activation_gb, 2) if known else None
    )

    uncertainty: List[str] = [
        "Activation memory is a resolution/frame-count heuristic, not a "
        "measurement, and may over- or under-state the real sampling-phase spike.",
        "A model pinned in the preset's own config (not exposed as a form "
        "picker) is outside the active model references above and is not counted.",
    ]
    if not active_set_resolved:
        uncertainty.append(
            "The active model set could not be resolved for this form "
            "(pipeline build failed), so no model sizes are counted."
        )
    if unknown:
        uncertainty.append(
            f"{len(unknown)} referenced model(s) have no indexed file size "
            "and are excluded from the estimate."
        )
    if settings:
        uncertainty.append(
            "Quantization/dtype settings on active loaders "
            f"({'; '.join(settings)}) may lower actual residency below the "
            "summed on-disk weight sizes."
        )

    return MemoryEstimateResult(
        estimate=Estimate(
            checkpoint_estimate_gb=checkpoint_estimate_gb,
            weights_gb=weights_gb,
            activation_gb=activation_gb,
            margin=_WEIGHT_LOAD_MARGIN,
            basis="sum of known active checkpoint/component weights x load margin + activation term",
        ),
        coverage=Coverage(
            known=known,
            unknown=unknown,
            active_set_resolved=active_set_resolved,
            pinned_components_uncounted=True,
            uncertainty=uncertainty,
        ),
    )


@dataclass
class DeviceEvidence:
    kind: str  # "local" | "remote" | "none" | "unknown"
    free_gb: Optional[float]
    total_gb: Optional[float]
    provenance: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "free_gb": self.free_gb,
            "total_gb": self.total_gb,
            "provenance": self.provenance,
        }


@dataclass
class BudgetEvidence:
    configured_gb: Optional[float]
    source: str
    # Each ACTIVE pipe's own `vram_limit_gb`, composed with `configured_gb`'s
    # same backend-cap/device inputs individually - `{pipe, hint_gb,
    # composed_gb}` per pipe. Deliberately never folded into `configured_gb`
    # itself: these are per-STAGE hints (e.g. a tiled detailer's own tile
    # budget), and a preset can declare several with DIFFERENT values, so no
    # single one of them speaks for the whole request.
    pipe_hints_gb: List[Dict[str, Any]]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "configured_gb": self.configured_gb,
            "source": self.source,
            "pipe_hints_gb": list(self.pipe_hints_gb),
        }


def resolve_device_evidence(execution_device: str, gpu_monitor: Optional[Any]) -> DeviceEvidence:
    """Physical GPU evidence for the backend a preview would route to -
    never this host's reading for a backend that isn't this host, and never
    a configured cap (see `resolve_budget_evidence` for that).

    Driven by the routed backend's `execution_device` class-level
    declaration (REQ-01), never inferred from its driver NAME: a driver
    string like "comfyui" says nothing about whether that server is this
    host or a remote one (it's admin-configured), so a substring check on it
    would wrongly treat an in-process plugin backend with a remote host as
    local. `"this_host_gpu"` reads NVML through the shared `GpuMonitor`
    (never touches CUDA itself) and only yields `kind: "local"` when the
    monitor actually reports a GPU present; `"remote"` always yields `kind:
    "remote"` with no numbers - that hardware isn't this process's to read;
    `"unestablished"` (the default for anything that hasn't declared, e.g. a
    plugin backend that predates this attribute) yields `kind: "unknown"` -
    genuinely undetermined, not "no GPU".
    """
    if execution_device == _EXECUTION_DEVICE_REMOTE:
        return DeviceEvidence(
            kind="remote", free_gb=None, total_gb=None,
            provenance="not reported by the remote worker",
        )
    if execution_device == _EXECUTION_DEVICE_THIS_HOST:
        if gpu_monitor is not None and getattr(gpu_monitor, "available", False):
            try:
                free_gb = round(gpu_monitor.get_free_vram() / 1024, 2)
                total_gb = round(gpu_monitor.get_total_vram() / 1024, 2)
                return DeviceEvidence(
                    kind="local", free_gb=free_gb, total_gb=total_gb,
                    provenance="this host's GPU monitor",
                )
            except Exception:
                pass
        return DeviceEvidence(
            kind="none", free_gb=None, total_gb=None,
            provenance="no GPU reported for this backend",
        )
    return DeviceEvidence(
        kind="unknown", free_gb=None, total_gb=None,
        provenance="execution device not declared by this backend",
    )


def resolve_budget_evidence(
    backend_cap_gb: Optional[float],
    pipe_hints: List[PipeVramHint],
    device: DeviceEvidence,
) -> BudgetEvidence:
    """The configured model-loading budget for the backend a preview would
    route to (`configured_gb`), and - SEPARATELY - each active pipe's own
    `vram_limit_gb` hint composed the same way (`pipe_hints_gb`).

    These are not the same number and must never be collapsed into one:
    `configured_gb` reflects only the backend's own `gpu_max_vram`, bounded
    by the device's reported free VRAM when there is one (the same
    `effective_vram_budget_gb` - MEM-02 - composition a real run uses, with
    no pipe hint folded in) - the budget for the backend as a whole.
    `pipe_hints_gb` lists each ACTIVE pipe's own per-STAGE hint (e.g. a tiled
    detailer's own tile budget) composed against that SAME backend cap and
    device evidence individually, so each entry shows what that one stage
    would actually be bounded to - never presented as if any single one of
    them were the whole request's budget.

    `configured_gb` is `None` when the backend has no configured cap at all
    (`pipe_hints_gb` can still be non-empty in that case). When device
    evidence is unavailable (a remote/no-GPU/unknown device), a configured
    cap is reported unbounded rather than dropped - it is still real
    configuration, just without hardware to check it against.
    """
    def _compose(hint_gb: Optional[float]) -> Optional[float]:
        if backend_cap_gb is None and hint_gb is None:
            return None
        if device.free_gb is None:
            caps = [c for c in (backend_cap_gb, hint_gb) if c is not None and c > 0]
            return round(min(caps), 2) if caps else None
        return round(effective_vram_budget_gb(backend_cap_gb, hint_gb, device.free_gb), 2)

    configured_gb = _compose(None)
    if backend_cap_gb is None:
        source = "not configured"
    elif device.free_gb is None:
        source = "backend gpu_max_vram; not bounded by device evidence"
    else:
        source = "backend gpu_max_vram + device free VRAM"

    pipe_hints_gb = [
        {"pipe": hint.pipe, "hint_gb": hint.hint_gb, "composed_gb": _compose(hint.hint_gb)}
        for hint in pipe_hints
    ]
    if pipe_hints_gb:
        source += (
            f" ({len(pipe_hints_gb)} per-stage pipe hint(s) reported separately in "
            "pipe_hints_gb, not merged into configured_gb)"
        )

    return BudgetEvidence(configured_gb=configured_gb, source=source, pipe_hints_gb=pipe_hints_gb)
