"""Public API for the native engine: NativeEngineLoader + NativeGenerator.

This is the assembly layer. It owns no model math — it orchestrates the
foundation (loading, detection, registry, ops, placement) and the arch / text
encoder / VAE / sampling modules the other slices built:

    loader.load(path, kind)
        load_torch_file -> detect_* -> match_model_spec (DiT) ->
        pick_dtypes + detect_quant_format -> ops per PlacementPlan.ops_mode ->
        Model.from_config -> load_into_module -> NativeModel

    NativeGenerator(dit, te, vae, device_plan)
        encode_prompt -> te.encode -> conditioning dict(s)
        sample        -> denoise(model_forward adapter, ...) -> clean latent
        decode        -> latent_format inverse + vae.decode -> uint8 HWC images

Adapter contract (the one subtle correctness point): the sampler calls
``model_forward(x, sigma, conditioning)`` with ``sigma`` a ``(batch,)`` tensor.
For flow-matching Flux, the DiT timestep IS sigma — ComfyUI's
``ModelSamplingFlux.timestep(sigma)`` returns sigma unchanged, and both
ComfyUI's and this repo's ``timestep_embedding`` apply the x1000 ``time_factor``
internally. So the adapter passes ``sigma`` straight through as ``timestep`` —
no scaling. See the module docstring in ``vendor/gpl/comfyui/flux/layers.py``
(time_factor) and ``comfy/model_sampling.py`` (ModelSamplingFlux).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Literal, Sequence

import numpy as np
import torch

from .base import load_into_module
from .detect.registry import ModelSpec, match_model_spec
from .detect.unet_detect import detect_unet_config
from .errors import HostMemoryExhaustedError, NativeEngineUnsupportedError
from .io.safetensors_loader import load_torch_file, load_torch_file_prefixed
from .io.state_dict_utils import detect_prefix, strip_prefix, weight_dtype
from .memory.device_plan import DevicePlan, make_device_plan
from .memory.residency import (
    free_vram_gb,
    total_vram_gb,
    get_residency_manager,
    minimum_inference_memory_gb,
)
from .memory.partial import ModuleStreamer, plan_residency_split
from src.platform.runtime.system_memory import get_system_memory
from src.platform.observability.profiling import get_profiler, profiling_enabled, read_process_rss_gb
from .memory.tiering import (
    PlacementPlan,
    activation_headroom_gb,
    plan_placement,
    ref_latents_headroom_gb,
    sampling_headroom_gb,
)
from .ops.dtype import is_mixed_precision, pick_dtypes
from .ops.fp8_quant import estimate_fp8_gb, quantize_state_dict_to_fp8, should_quantize_fp8
from vendor.gpl.comfyui.ops import (
    QUANT_FP8_SCALED,
    detect_quant_format,
    disable_weight_init,
    fp8_ops,
    manual_cast,
    pick_operations,
)
from .sampling.denoise_loop import denoise, ensure_sampler_generator
from .text_encoders.base import NativeTextEncoder
from .text_encoders.loader import load_text_encoder
from .detect.vae_detect import (
    detect_causal3d_v2_vae_config,
    detect_causal3d_vae_config,
    detect_ltx_audio_vae_config,
    detect_ltx_diffusion_vae_config,
    detect_ltx_video_vae_config,
    detect_ltx_vocoder_config,
    detect_minimax_h3_audio_vae_config,
    detect_minimax_h3_video_vae_config,
    detect_minimax_music3_dav_config,
    detect_seedvr2_vae_config,
    detect_vae_config,
)
from .vae.ae_2d import AutoEncoder2D
from .vae.loader import (
    load_causal3d_v2_vae,
    load_causal3d_vae,
    load_ltx_audio_vae,
    load_ltx_diffusion_video_vae,
    load_ltx_latent_upsampler,
    load_ltx_video_vae,
    load_ltx_vocoder,
    load_minimax_h3_audio_vae,
    load_minimax_h3_video_vae,
    load_minimax_music3_dav,
    load_seedvr2_vae,
    load_vae,
)
from .vae.tiling import (
    auto_tile_size,
    causal3d_chunk_frames,
    chunked_decode_causal3d,
    tiled_decode,
    tiled_decode_causal3d,
    tiled_encode,
)

logger = logging.getLogger(__name__)

# Resolutions already warned about in snap_resolution(): a fixed preset/UI default
# that doesn't meet a family's pixel granularity would otherwise re-log the same
# warning on every single generation - keyed by (family, variant, requested,
# snapped) so a genuinely different snap still gets its own warning.
_warned_snapped_resolutions: set[tuple[str, str, int, int, int, int]] = set()

Kind = Literal[
    "diffusion_model", "text_encoder", "vae", "audio_vae", "vocoder", "latent_upscaler", "duration_head",
]

# Candidate prefixes a DiT checkpoint might be wrapped in.
_DIT_PREFIXES = ["model.diffusion_model.", "diffusion_model."]
_BYTES_PER_GB = 1024 ** 3
_VAE_SPATIAL_DOWNSCALE = 8

# Smallest causal-3D decode tile (in *latent* px) the shrink-on-OOM loop will
# fall to before giving up -- 16 latent px = 128 output px. Below this the seam
# overhead dominates and a genuine capacity limit should surface as an OOM.
_MIN_DECODE_TILE_LATENT = 16

# fp32 3D-conv decode spike per latent pixel for the causal-3D VAE (Qwen/Wan),
# measured well above the 2D AE's 0.6 (see memory/tiering.py). Used both for
# the tiled-vs-untiled fit test and to size the first tile from live free VRAM.
_CAUSAL3D_DECODE_MB_PER_LATENT_PX = 1.2

# Transient-peak estimate per latent pixel for the causal-3D ref-image ENCODE.
# Calibrated from a live trace: a ~10GB single conv3d alloc at
# 1072x1920 (~32.2k latent px @ //8), so ~0.32 MB/latent-px for that one alloc;
# rounded UP to 0.5 to cover the fuller working set (input + stacked activation
# buffers), keeping it conservative so a big edit offloads the parked DiT before
# encoding rather than OOMing against it. Encode runs in compute-dtype (bf16),
# so it is cheaper than the fp32 decode's 1.2 -- deliberately less than half.
_CAUSAL3D_ENCODE_MB_PER_LATENT_PX = 0.5

# Fraction of live free VRAM the first tile guess may claim: leaves slack for
# allocator fragmentation and the assembled fp32 output pixels.
_DECODE_TILE_VRAM_FRACTION = 0.75

# Per-generation option-dict keys that ``denoise()`` reads out of
# ``sampling_settings`` but that don't live on the ModelSpec — surfaced to presets
# through ``sample()``'s ``guidance_options`` / ``schedule_settings`` and merged in.
# Whitelisted (not a blanket dict spread) so a preset can't inject arbitrary
# ModelSpec overrides through these dicts, and so ``slg_*`` is deliberately absent
# from the image path (no image arch has skip_layers; a silently-inert knob is
# worse than none — the video pipes merge slg_* themselves).
_APG_SETTINGS_KEYS = ("apg_eta", "apg_norm_threshold", "apg_momentum")
# `fixed_mu`/`dynamic_shift` let a preset swap a ModelSpec's
# mu-shift SOURCE per generation -- e.g. Krea-2's turbo ModelSpec pins a fixed
# mu (distilled-at-mu=1.15); a raw/base checkpoint (same architecture, so no
# separate ModelSpec) instead wants the resolution-anchored dynamic-mu
# interpolation. `build_sigmas` already reads both straight off
# `sampling_settings` (see `sampling/flow_schedule.py`), so whitelisting them
# here is the only change needed.
_SCHEDULE_SETTINGS_KEYS = (
    "schedule", "schedule_options", "detail_strength", "detail_start", "detail_end",
    "fixed_mu", "dynamic_shift",
)


def _validate_explicit_sigmas(sigmas) -> torch.Tensor:
    """Validate a caller-supplied sigma schedule for :meth:`NativeGenerator.sample`.

    ``denoise()`` itself uses ``sigmas`` as-is with no shape checking (an
    internal contract shared with ``denoise_prenoised``); ``sample()`` is the
    public boundary, so malformed input is rejected loudly here rather than
    surfacing as a confusing sampler crash or a silently-wrong image.
    """
    t = torch.as_tensor(sigmas, dtype=torch.float32)
    if t.ndim != 1:
        raise ValueError(f"sigmas must be 1-D, got shape {tuple(t.shape)}")
    if t.numel() < 2:
        raise ValueError(f"sigmas must have at least 2 values (head+tail), got {t.numel()}")
    if not bool(torch.all(t[:-1] > t[1:])):
        raise ValueError(f"sigmas must be strictly decreasing, got {t.tolist()}")
    if float(t[0]) > 1.0:
        raise ValueError(f"sigmas[0] must be <= 1.0, got {float(t[0])}")
    if float(t[-1]) != 0.0:
        raise ValueError(f"sigmas[-1] must be 0.0, got {float(t[-1])}")
    return t


def _estimated_gb(sd: dict[str, torch.Tensor]) -> float:
    return sum(t.numel() * t.element_size() for t in sd.values()) / _BYTES_PER_GB


def _estimate_text_encoder_gb(encoder: Any) -> float | None:
    """Best-effort weight footprint of a loaded ``NativeTextEncoder``, in GB.

    ``_load_te`` used to leave ``NativeModel.estimated_vram_gb`` as ``None`` for
    every text encoder (no per-kind byte count was ever computed, unlike every
    other loader in this file). That silently disabled every SIZE-GATED glibc
    trim in ``NativeModel.move_to``/``offload`` (both check ``(self.
    estimated_vram_gb or 0.0) > 2.0`` before calling ``trim_host_allocator()``)
    for THIS component specifically -- and the text encoder is routinely the
    single largest CPU-resident thing in the process (Gemma3-12B, ~22-24GB).
    A capture showed the TE's eviction
    (``models.evict freed_gb=22.705 unloaded=True``, a clean unload —
    the referrer diagnostic found no lingering Python reference) reclaim
    ZERO measured RSS; with no size estimate, nothing in the codebase had ever
    asked glibc to give that freed heap back.

    Handles both a single encoder (``.module`` -- Gemma3/Qwen3/T5XXL/CLIP-L,
    all named this way per ``text_encoders/base.py``'s duck-type contract, the
    same one ``model_lifecycle.manager._measure_value_ram_gb`` relies on) and
    the Flux1 composite (``FluxTextEncoder`` -- ``.t5``/``.clip_l``, each with
    its own ``.module``). Returns ``None`` (never 0.0) when no module/no
    parameters are found, so a genuinely-unmeasurable encoder still reads as
    "unknown" rather than as "definitely tiny" to any caller checking
    ``estimated_vram_gb or 0.0``.
    """
    modules: list[torch.nn.Module] = []
    direct = getattr(encoder, "module", None)
    if isinstance(direct, torch.nn.Module):
        modules.append(direct)
    for attr in ("t5", "clip_l"):
        sub_module = getattr(getattr(encoder, attr, None), "module", None)
        if isinstance(sub_module, torch.nn.Module):
            modules.append(sub_module)
    if not modules:
        return None

    total_bytes = 0
    for module in modules:
        try:
            for p in module.parameters():
                total_bytes += p.numel() * p.element_size()
            for b in module.buffers():
                if b is not None:
                    total_bytes += b.numel() * b.element_size()
        except Exception:  # pragma: no cover - best-effort sizing only
            continue
    return (total_bytes / _BYTES_PER_GB) if total_bytes > 0 else None


def _latent_frames(shape) -> int:
    """Temporal extent (T) of a latent ``shape``/tensor: T for 5D ``(B,C,T,H,W)``
    (the causal-3D VAE families), 1 for a 4D ``(B,C,H,W)`` still-image latent.
    Feeds the headroom functions' ``latent_frames`` so a multi-frame video
    latent reserves activation/decode headroom scaled by T instead of being
    silently priced as a single frame (the trailing-dims-only slice used to
    drop T entirely)."""
    return int(shape[2]) if len(shape) == 5 else 1


class NativeModel:
    """An evictable wrapper around one loaded component.

    Carries the shape ``ModelLifecycleManager._best_effort_unload`` expects
    (a callable ``.unload()``) plus ``.spec`` / ``.module`` / ``.estimated_vram_gb``
    and the ``move_to`` / ``offload`` pair the generator uses to sequence phases.
    """

    # A partial-residency teardown that vacated at least this much page-locked
    # host RAM releases CUDA's cached pinned pool (which ``trim_host_allocator``
    # cannot touch) back to the OS. Below it, the warm pinned pool is kept for a
    # cheap re-pin next phase — only the co-tenant-OOM degrade (pins ~the whole
    # DiT) and other near-whole-model streamings clear the bar.
    _PINNED_RELEASE_FLOOR_GB = 4.0
    # Free host RAM (beyond the streamed weights themselves) required before
    # pinning a streamed set. Covers the teardown transient that materialises the
    # unpinned resting weights while the pinned copies are still cached.
    _STREAM_HOST_RESERVE_GB = 2.0

    def __init__(
        self,
        kind: Kind,
        module: Any,
        *,
        spec: ModelSpec | None = None,
        estimated_vram_gb: float | None = None,
        compute_dtype: torch.dtype = torch.bfloat16,
        quant_format: str | None = None,
        device: str = "cpu",
    ) -> None:
        self.kind = kind
        self.module = module
        self.spec = spec
        self.estimated_vram_gb = estimated_vram_gb
        self.compute_dtype = compute_dtype
        self.quant_format = quant_format
        self.device = device
        # Set when this component is placed with PARTIAL residency (some leaves on
        # the GPU, the rest streamed from pinned CPU RAM). ``None`` = all-or-nothing
        # residency via ``move_to``. See ``memory/partial.py``.
        self._streamer: ModuleStreamer | None = None
        # Set when this DiT's blocks are regionally ``torch.compile``-d for a
        # resident placement (see ``optimizations/compile.py``); carries the undo
        # handle. Restored whenever the module leaves the GPU so the RAM-cached
        # copy never holds a compiled graph. ``None`` = not compiled.
        self._compiled = None

    def _reclaim_host_after_teardown(self, pinned_gb: float) -> None:
        """Return the host pages a partial-residency teardown vacated to the OS.

        ``teardown`` moves every leaf back to plain CPU RAM, allocating fresh
        unpinned copies of the streamed leaves (there is no retained CPU source to
        point back to — ``apply`` reassigned each ``p.data`` to the pinned/GPU
        tensor, dropping the original). Those unpinned weights are the module's
        legitimate resting state; the amplifier is that the vacated *pinned* pool
        stays cached by CUDA's host allocator, which ``trim_host_allocator``
        (glibc-only) cannot release. Left alone it stacks ~model-sized page-locked
        RAM on top of the fresh weights, turning a DiT-move VRAM OOM into a
        host-RAM OOM-kill. Release it here, size-gated so a steady-state modest
        streamed tail keeps its warm pinned pool for a cheap re-pin next phase."""
        if (self.estimated_vram_gb or 0.0) <= 2.0:
            return
        from src.platform.runtime.model_lifecycle.manager import (
            empty_pinned_host_cache,
            trim_host_allocator,
        )

        self._reclaim_with_marks("teardown", "trim_host_allocator", trim_host_allocator)
        if pinned_gb >= self._PINNED_RELEASE_FLOOR_GB:
            empty_pinned_host_cache()
            get_profiler().mark("host.pinned_release", kind=self.kind, pinned_gb=pinned_gb)

    def _reclaim_with_marks(self, site: str, op_name: str, op) -> None:
        """Run one host-memory reclaim primitive (``trim_host_allocator`` or
        ``empty_pinned_host_cache`` -- both no-arg callables) bracketed by an
        RSS read on each side.

        Each primitive either releases real host pages back to the OS or does
        nothing (nothing was actually freed by Python's own refcounting yet,
        or there was nothing to reclaim) -- a profile alone could not
        previously tell those two apart, only that the call happened, and the
        eventual RSS drop (if any) had to be inferred from whichever mark
        happened to follow. ``site``/``op_name`` let one shared ``host.reclaim``
        event distinguish which call at which call site produced a given row.
        The two extra RSS reads only happen while profiling is enabled
        (checked here, not left to `mark()`'s own internal check, since the
        reads themselves -- not just the write -- are the cost to avoid when
        profiling is off)."""
        if not profiling_enabled():
            op()
            return
        rss_before_reclaim_gb = read_process_rss_gb()
        op()
        rss_after_reclaim_gb = read_process_rss_gb()
        get_profiler().mark(
            "host.reclaim", kind=self.kind, site=site, op=op_name,
            rss_before_reclaim_gb=rss_before_reclaim_gb, rss_after_reclaim_gb=rss_after_reclaim_gb,
        )

    def _guard_host_ram_for_streaming(self, plan) -> None:
        """Refuse to pin a streamed set host RAM cannot survive.

        Pinning the streamed leaves adds ~``plan.streamed_gb`` of page-locked host
        RAM, and the eventual teardown transiently needs about that much again to
        materialise the unpinned resting weights before the pinned pool is
        released. When free host RAM can't cover that, the process is one step
        from an OS OOM-kill — the co-tenant-OOM degrade that pins ~the whole DiT
        is exactly this case. Fail the generation cleanly instead of degrading
        into an un-survivable state. Only trips when the streamed set nearly
        exhausts free RAM, so healthy steady-state partial residency (a few-GB
        streamed tail against tens of GB free) never sees it."""
        streamed_gb = plan.streamed_gb
        if streamed_gb <= 0.0:
            return
        available_gb = get_system_memory().available_gb
        if streamed_gb + self._STREAM_HOST_RESERVE_GB > available_gb:
            raise HostMemoryExhaustedError(
                f"partial-residency streaming needs ~{streamed_gb:.1f}GB pinned host "
                f"RAM (+{self._STREAM_HOST_RESERVE_GB:.0f}GB teardown headroom) but only "
                f"{available_gb:.1f}GB host RAM is free; refusing to degrade into a "
                f"host-memory OOM-kill."
            )

    @property
    def is_streaming(self) -> bool:
        """True while this component is placed with an ACTIVE partial-residency
        streamer (leaves pinned in host RAM, streamed per-forward). The ground
        truth for "the pinned host pool is live", independent of any stale
        PlacementPlan flag — see the end of ``NativeGenerator.sample``."""
        return self._streamer is not None and self._streamer.active

    def move_to(self, device: str | torch.device) -> None:
        if self.module is None:
            return
        # Leaving the GPU: undo any regional torch.compile first, so the module
        # that lands on the CPU (and is kept in the lifecycle RAM cache) is plain —
        # no dynamo graph / CUDA guard state survives an offload/reload cycle.
        if self._compiled is not None and str(device) == "cpu":
            from .optimizations.compile import restore_compiled

            restore_compiled(self)
        # A prior partial-residency placement must be torn down before a plain
        # full move, or its streamed leaves would stay pinned on the CPU and its
        # forced cast flags would linger. Teardown is itself a big CPU-side churn
        # (each resident/pinned-streamed leaf materialises a fresh CPU copy — see
        # memory/partial.py's ``_move_own_tensors``), up to the component's full
        # weight size in transients. That churn happens regardless of destination,
        # so the glibc trim must NOT be gated on "destination is cpu" the way the
        # plain offload case below is, or a partial->full-GPU restore ratchets RSS.
        teardown_from_streamer = self._streamer is not None and self._streamer.active
        if teardown_from_streamer:
            pinned_gb = self._streamer.pinned_gb
            self._streamer.teardown()
            self._reclaim_host_after_teardown(pinned_gb)
        self.module.to(device)
        self.device = str(device)
        get_profiler().mark(
            "native.move_to", kind=self.kind, device=str(device),
            estimated_vram_gb=self.estimated_vram_gb,
        )
        # Track VRAM residency so a later phase (e.g. a text-encoder encode) can
        # free room by offloading this component when it is no longer the active
        # one. note_resident on a non-cuda device de-registers, so this pair keeps
        # the registry honest for both directions.
        manager = get_residency_manager()
        if str(device).startswith("cuda"):
            manager.note_resident(self, device, self.estimated_vram_gb or 0.0)
            # A multi-GB device move frees thousands of layer-sized CPU chunks
            # in both directions (the old CPU copy on the way up, transients on
            # the way down -- see the comment on the offload branch below);
            # glibc keeps those pages in its arenas unless asked, which reads
            # as inflated RSS for as long as this component stays GPU-resident
            # (i.e. for most of a generation's wall-clock -- see
            # `trim_host_allocator`'s docstring: a warm LTX move_to(cuda)
            # dropped RSS by only 0.87GB where ~23GB of CPU weights were
            # released). `stream_to` already trims on ITS to-cuda path; this
            # mirrors that for the plain full-pin move. Trim only for big
            # components (same >2GB gate as the offload branch) -- costs tens
            # of ms on a large heap, paid once per placement, not per step.
            if (self.estimated_vram_gb or 0.0) > 2.0:
                from src.platform.runtime.model_lifecycle.manager import trim_host_allocator

                trim_host_allocator()
        else:
            manager.note_offloaded(self)
            # A multi-GB device move frees thousands of layer-sized CPU chunks
            # in both directions (the old CPU copy on the way up, transients on
            # the way down); glibc keeps those pages in its arenas unless asked,
            # which reads as a permanent RSS climb per offload cycle. Trim only
            # for big components — it costs tens of ms on a large heap. (The
            # streamer-teardown case above trims separately if it applied.)
            if (self.estimated_vram_gb or 0.0) > 2.0:
                from src.platform.runtime.model_lifecycle.manager import trim_host_allocator

                trim_host_allocator()

    def stream_to(self, device: str | torch.device, resident_budget_gb: float, *, non_blocking: bool = True) -> None:
        """Place the module with PARTIAL residency on ``device``.

        Keeps as many leaves resident on the GPU as fit in ``resident_budget_gb``
        (weights budget; the caller reserves activation headroom first) and streams
        the rest from pinned CPU RAM per forward. Registers the ACTUAL resident size
        (not the full weight size) so cross-family eviction accounting stays honest.
        Falls back to a full move on a non-CUDA device.
        """
        if self.module is None:
            return
        # Switching to partial residency means the streamer swaps leaf weights per
        # forward — incompatible with a compiled graph (guards on weight identity).
        # A DiT compiled during an earlier RESIDENT generation must be un-compiled
        # before it is streamed, or the compiled blocks would see per-forward
        # weight.data swaps + CPU leaves. (move_to("cpu") already does this for the
        # offload path; streaming keeps the module on the GPU so it needs its own.)
        if self._compiled is not None:
            from .optimizations.compile import restore_compiled

            restore_compiled(self)
        if not str(device).startswith("cuda"):
            self.move_to(device)
            return
        from src.platform.runtime.model_lifecycle.manager import (
            empty_pinned_host_cache,
            trim_host_allocator,
        )

        # Release any stale CACHED pinned pool BEFORE this pin burst. `apply()`
        # pins a fresh leaf set sized to THIS `plan`; PyTorch's pinned allocator
        # buckets free blocks by size, so a pool from a differently-split earlier
        # placement can't serve this burst and just sits as extra RSS alongside
        # the new pin. `stream_to` only runs when ENTERING partial residency
        # (never per forward step), so this is a rare, coarse-grained call.
        self._reclaim_with_marks("stream_to", "empty_pinned_host_cache", empty_pinned_host_cache)
        plan = plan_residency_split(self.module, resident_budget_gb)
        # Refuse the placement outright if pinning this streamed set would leave
        # host RAM unable to survive it (and its teardown) — a clean generation
        # failure beats degrading into a state whose teardown OOM-kills the API.
        self._guard_host_ram_for_streaming(plan)
        if self._streamer is None:
            self._streamer = ModuleStreamer(self.module)
        self._streamer.apply(device, plan, non_blocking=non_blocking)
        self.device = str(device)
        get_residency_manager().note_resident(self, device, plan.resident_gb)
        # Mirror `move_to()`'s post-teardown trim: `apply()` above frees the
        # module's prior plain CPU allocations (moving fixed/resident leaves to
        # GPU, re-pinning streamed leaves), but nothing here asks glibc to return
        # those pages to the OS. Gated like `move_to`'s trim (big components only).
        if (self.estimated_vram_gb or 0.0) > 2.0:
            self._reclaim_with_marks("stream_to", "trim_host_allocator", trim_host_allocator)

    def offload(self) -> None:
        """Move back to CPU between phases (keeps the module, frees VRAM)."""
        get_profiler().mark("native.offload", kind=self.kind, estimated_vram_gb=self.estimated_vram_gb)
        if self._streamer is not None and self._streamer.active:
            pinned_gb = self._streamer.pinned_gb
            self._streamer.teardown()
            get_profiler().mark("streamer.teardown", kind=self.kind)
            get_residency_manager().note_offloaded(self)
            self.device = "cpu"
            # This early-return branch bypasses move_to() entirely, so its
            # post-teardown reclaim never ran for a component offloaded straight
            # out of partial residency (the case a generator hits every sampling
            # phase: offload the DiT before VAE decode). Reclaim here too — trim,
            # plus the pinned-pool release when the vacated pin was large.
            self._reclaim_host_after_teardown(pinned_gb)
            return
        self.move_to("cpu")

    def unload(self) -> None:
        """Drop the module (best-effort CPU move first) for lifecycle eviction."""
        get_profiler().mark("native.unload", kind=self.kind, estimated_vram_gb=self.estimated_vram_gb)
        if self._compiled is not None:
            from .optimizations.compile import restore_compiled

            restore_compiled(self)
        get_residency_manager().note_offloaded(self)
        teardown_from_streamer = False
        pinned_gb = 0.0
        if self._streamer is not None and self._streamer.active:
            try:
                pinned_gb = self._streamer.pinned_gb
                self._streamer.teardown()
                get_profiler().mark("streamer.teardown", kind=self.kind)
                teardown_from_streamer = True
            except Exception:  # pragma: no cover - best-effort eviction
                logger.debug("native model streamer teardown failed", exc_info=True)
        if self.module is not None:
            try:
                self.module.to("cpu")
            except Exception:  # pragma: no cover - best-effort eviction
                logger.debug("native model eviction to cpu failed", exc_info=True)
            self.module = None
        # Same gap as offload() above: a component evicted while partially
        # resident tears down through this method, not move_to(), so it needs
        # its own reclaim (trim + large-pin pinned-pool release) too.
        if teardown_from_streamer:
            self._reclaim_host_after_teardown(pinned_gb)


class NativeEngineLoader:
    """Loads a single component (DiT / text encoder / VAE) from a safetensors file."""

    # ~1M elements: quantise only Linears at least this big (skips small
    # projectors / time embedders — matches which layers ship fp8 in real dumps).
    _FP8_MIN_NUMEL = 1 << 20

    def __init__(
        self,
        device: str = "cpu",
        vram_gb: float | None = None,
        fp8_quantize: str | None = None,
    ) -> None:
        self.device = device
        self.vram_gb = vram_gb
        # On-the-fly fp8 quantise-at-load policy: "auto" (quantise a bf16 DiT only
        # when it doesn't fit resident but would as fp8) | "off" | "force". Falls
        # back to the NATIVE_FP8_QUANTIZE env var, then "auto".
        self.fp8_quantize = (fp8_quantize or os.environ.get("NATIVE_FP8_QUANTIZE") or "auto").lower()

    # -- public ------------------------------------------------------------

    def load(self, path: str | Path, kind: Kind, **kwargs: Any) -> NativeModel:
        if kind == "diffusion_model":
            return self._load_dit(path)
        if kind == "text_encoder":
            return self._load_te(path, **kwargs)
        if kind == "vae":
            return self._load_vae(path)
        if kind == "audio_vae":
            return self._load_audio_vae(path)
        if kind == "vocoder":
            return self._load_vocoder(path)
        if kind == "latent_upscaler":
            return self._load_latent_upscaler(path)
        if kind == "duration_head":
            return self._load_duration_head(path)
        raise NativeEngineUnsupportedError(f"unknown load kind: {kind!r}")

    # -- per-kind ----------------------------------------------------------

    def _load_dit(self, path: str | Path) -> NativeModel:
        sd, metadata = load_torch_file(path, device="cpu")
        get_profiler().mark("load.dit.read", est_gb=_estimated_gb(sd))

        prefix = detect_prefix(sd, _DIT_PREFIXES)
        if prefix:
            sd = strip_prefix(sd, prefix)

        config = detect_unet_config(sd, metadata)
        if config is None:
            raise NativeEngineUnsupportedError(
                f"'{Path(path).name}' is not a recognised native DiT "
                "(no Flux signature keys)"
            )
        spec = match_model_spec(config)

        quant_format = detect_quant_format(metadata, sd)
        # storage = TRUE checkpoint dtype (so a fp32 VAE/DiT selects manual_cast
        # rather than a no-cast namespace that would feed fp32 weights to bf16
        # activations); pick_dtypes supplies only the compute dtype.
        sd_dtype = weight_dtype(sd)
        est_gb = _estimated_gb(sd) or spec.memory_cost_gb

        # On-the-fly fp8: quantise a bf16/fp16 DiT to scaled e4m3 so it fits
        # resident (preferred over streaming) — feeds the validated Fp8ScaledLinear
        # path. ``auto`` only fires in the doesn't-fit-bf16-but-fits-fp8 window.
        sd, quant_format, sd_dtype, est_gb = self._maybe_quantize_fp8(
            sd, spec, quant_format, sd_dtype, est_gb,
        )

        _, compute_dtype = pick_dtypes(sd_dtype, self.device, self.vram_gb)
        storage_dtype = sd_dtype or compute_dtype

        ops = self._ops_for("dit", est_gb, storage_dtype, compute_dtype, quant_format, sd)
        # Build empty on the meta device so a multi-GB DiT never allocates real
        # (fp32) throwaway weights — load_into_module assign-loads the real
        # tensors and post_load rebuilds any computed buffers.
        with torch.device("meta"):
            module = spec.resolve_model_class().from_config(config, ops)
        load_into_module(module, sd, spec)
        get_profiler().mark("load.dit.built", est_gb=est_gb, quant_format=quant_format)

        logger.debug("loaded DiT %s/%s (%.2fGB, quant=%s)", spec.family, spec.variant, est_gb, quant_format)
        return NativeModel(
            "diffusion_model", module, spec=spec, estimated_vram_gb=est_gb,
            compute_dtype=compute_dtype, quant_format=quant_format,
        )

    def _maybe_quantize_fp8(self, sd, spec, quant_format, sd_dtype, est_gb):
        """Apply the ``fp8_quantize`` policy to a DiT state dict at load.

        Returns ``(sd, quant_format, sd_dtype, est_gb)`` — either unchanged, or with
        the big Linear weights quantised to scaled e4m3 (``quant_format`` bumped to
        ``fp8_scaled``, ``est_gb`` recomputed to the smaller fp8 footprint so
        placement keeps the DiT resident). The gate (:func:`should_quantize_fp8`)
        skips already-quantised / non-bf16 checkpoints; ``auto`` also needs the fp8
        estimate, computed here only when a quantise is even possible.
        """
        if self.fp8_quantize == "off" or quant_format is not None:
            return sd, quant_format, sd_dtype, est_gb
        if sd_dtype not in (torch.bfloat16, torch.float16, torch.float32):
            return sd, quant_format, sd_dtype, est_gb
        fp8_gb = estimate_fp8_gb(sd, min_numel=self._FP8_MIN_NUMEL)
        # "Can bf16 sit resident during sampling?" must be answered
        # DETERMINISTICALLY: gating on load-moment free VRAM made the decision
        # depend on whatever else sat on the GPU right then (the TE during a
        # bench, a previous generation's cache, a webui) — the same 24.5GB
        # Krea-2 quantised on one run and not the next, and the fp8 artifact
        # then stuck in the MODELS cache (the fingerprint doesn't encode the
        # decision). Total device memory minus a fixed co-tenant/activation
        # reserve is stable: the TE offloads before sampling and the decode
        # spike is tiled, so bf16 fits whenever weights + reserve <= total.
        # Transient co-tenant pressure is the residency planner's job, with
        # the OOM-retry net behind it. A user-set vram_limit still caps.
        gate_vram = self.vram_gb
        if self.device and str(self.device).startswith("cuda"):
            total = total_vram_gb(self.device)
            if total is not None:
                gate_vram = total if gate_vram is None else min(gate_vram, total)
        if not should_quantize_fp8(
            self.fp8_quantize, quant_format=quant_format, sd_dtype=sd_dtype,
            bf16_gb=est_gb, fp8_gb=fp8_gb, vram_gb=gate_vram,
        ):
            return sd, quant_format, sd_dtype, est_gb
        sd, nq = quantize_state_dict_to_fp8(sd, min_numel=self._FP8_MIN_NUMEL)
        if not nq:
            return sd, quant_format, sd_dtype, est_gb
        new_est = _estimated_gb(sd)
        logger.info(
            "[NATIVE] on-the-fly fp8 (%s): %s quantised %d Linear weight(s) %.1fGB -> %.1fGB",
            self.fp8_quantize, spec.family, nq, est_gb, new_est,
        )
        return sd, QUANT_FP8_SCALED, weight_dtype(sd), new_est

    def _load_vae(self, path: str | Path) -> NativeModel:
        # LTX's all-in-one checkpoint carries the DiT (tens of GB) alongside a
        # ``vae.``-prefixed video VAE (a few hundred MB). Read only the
        # ``vae.*`` keys off disk (never materializes the DiT) *before*
        # sizing/ops selection, or the estimate is the whole checkpoint's
        # footprint and poisons placement (streamed the VAE like a 27GB
        # model). Standalone VAE files (Flux/causal3d/LTX) have no ``vae.``
        # keys, so the loader falls back to a full (already component-only) read.
        sd, metadata = load_torch_file_prefixed(path, "vae.", device="cpu")
        get_profiler().mark("load.vae.read", est_gb=_estimated_gb(sd))
        is_ltx_video = (
            detect_ltx_video_vae_config(metadata) is not None
            or detect_ltx_diffusion_vae_config(metadata) is not None
        )
        if is_ltx_video and any(k.startswith("vae.") for k in sd):
            sd = {k[len("vae."):]: v for k, v in sd.items() if k.startswith("vae.")}
        quant_format = detect_quant_format(metadata, sd)
        sd_dtype = weight_dtype(sd)
        _, compute_dtype = pick_dtypes(sd_dtype, self.device, self.vram_gb)
        storage_dtype = sd_dtype or compute_dtype
        est_gb = _estimated_gb(sd)
        ops = self._ops_for("vae", est_gb, storage_dtype, compute_dtype, quant_format, sd)
        module = self._load_vae_module(path, sd, metadata, ops)
        get_profiler().mark("load.vae.built", est_gb=est_gb, quant_format=quant_format)
        return NativeModel(
            "vae", module, estimated_vram_gb=est_gb,
            compute_dtype=compute_dtype, quant_format=quant_format,
        )

    @staticmethod
    def _load_vae_module(path, sd, metadata, ops):
        """Dispatch to the right VAE-family loader by detection.

        The native engine hosts seven VAE shapes: the Flux 2D AE, the Wan-2.1 /
        Qwen-Image causal-3D VAE, the Wan-2.2 causal-3D v2, the SeedVR2
        causal-video VAE (self-normalizing), the LTX-2/2.3 conv video VAE and
        the LTX-2.5 diffusion-decoder one (both metadata-detected, mutually
        exclusive by ``_class_name``), and the MiniMax-H3 video VAE (causal-3D
        encoder + ViT decoder). Callers just say ``kind="vae"``; this routes.
        ``sd``/``metadata`` were already read (and, for LTX, already sliced to
        the ``vae.*`` keys) by ``_load_vae``, so the LTX/H3 branches pass them
        straight through rather than re-reading the file (H3's own file is
        standalone with bare/unprefixed keys, so no slicing applies).
        """
        if detect_vae_config(sd) is not None:
            return load_vae(path, ops, device="cpu")               # Flux 2D AE
        if detect_causal3d_vae_config(sd) is not None:
            return load_causal3d_vae(path, ops, device="cpu")      # Wan 2.1 / Qwen-Image
        if detect_causal3d_v2_vae_config(sd) is not None:
            return load_causal3d_v2_vae(path, ops, device="cpu")   # Wan 2.2
        if detect_seedvr2_vae_config(sd) is not None:
            return load_seedvr2_vae(path, ops, device="cpu")       # SeedVR2 (self-normalizing)
        if detect_ltx_video_vae_config(metadata) is not None:
            return load_ltx_video_vae(path, ops, device="cpu", sd=sd, metadata=metadata)  # LTX-2/2.3
        if detect_minimax_h3_video_vae_config(sd, metadata) is not None:
            return load_minimax_h3_video_vae(path, ops, device="cpu", sd=sd, metadata=metadata)  # MiniMax-H3
        if detect_ltx_diffusion_vae_config(metadata) is not None:
            return load_ltx_diffusion_video_vae(path, ops, device="cpu", sd=sd, metadata=metadata)  # LTX-2.5
        raise NativeEngineUnsupportedError(
            f"'{Path(path).name}' matches no known native VAE family"
        )

    def _load_audio_vae(self, path: str | Path) -> NativeModel:
        """Same slice-before-estimate treatment as ``_load_vae``, for LTX's
        audio VAE. Unlike the video VAE (bare keys when standalone,
        ``vae.``-prefixed only in the all-in-one checkpoint), both the
        standalone LTX audio VAE file and the all-in-one checkpoint always
        carry the ``audio_vae.`` prefix (see ``load_ltx_audio_vae``'s
        docstring) -- so filtering to that prefix is safe unconditionally and,
        unlike the video VAE path, the prefix is kept (not stripped) since the
        loader's own ``strip_prefix`` call is unconditional and expects it.
        Reads only the ``audio_vae.*`` keys off disk (see
        ``load_torch_file_prefixed``), never materializing the DiT from the
        all-in-one checkpoint. MiniMax-H3's audio VAE is a DIFFERENT,
        standalone file with bare/unprefixed keys -- no key in it starts with
        ``audio_vae.``, so ``load_torch_file_prefixed`` falls back to a full
        read for it (see that function's docstring), and detection below
        routes to the right loader.
        """
        sd, metadata = load_torch_file_prefixed(path, "audio_vae.", device="cpu")
        get_profiler().mark("load.audio_vae.read", est_gb=_estimated_gb(sd))
        if detect_ltx_audio_vae_config(metadata) is not None:
            sliced = {k: v for k, v in sd.items() if k.startswith("audio_vae.")}
            if sliced:
                sd = sliced
        quant_format = detect_quant_format(metadata, sd)
        sd_dtype = weight_dtype(sd)
        _, compute_dtype = pick_dtypes(sd_dtype, self.device, self.vram_gb)
        storage_dtype = sd_dtype or compute_dtype
        est_gb = _estimated_gb(sd)
        ops = self._ops_for("vae", est_gb, storage_dtype, compute_dtype, quant_format, sd)
        if detect_minimax_h3_audio_vae_config(sd, metadata) is not None:
            module = load_minimax_h3_audio_vae(path, ops, device="cpu", sd=sd, metadata=metadata)
        elif detect_minimax_music3_dav_config(sd, metadata) is not None:
            module = load_minimax_music3_dav(path, ops, device="cpu", sd=sd, metadata=metadata)
        else:
            module = load_ltx_audio_vae(path, ops, device="cpu", sd=sd, metadata=metadata)
        get_profiler().mark("load.audio_vae.built", est_gb=est_gb, quant_format=quant_format)
        return NativeModel(
            "audio_vae", module, estimated_vram_gb=est_gb,
            compute_dtype=compute_dtype, quant_format=quant_format,
        )

    def _load_vocoder(self, path: str | Path) -> NativeModel:
        """Same slice-before-estimate treatment as ``_load_audio_vae``, for
        LTX's vocoder (always ``vocoder.``-prefixed, standalone or all-in-one
        -- see ``load_ltx_vocoder``'s docstring). Reads only the
        ``vocoder.*`` keys off disk (see ``load_torch_file_prefixed``), never
        materializing the DiT from the all-in-one checkpoint."""
        sd, metadata = load_torch_file_prefixed(path, "vocoder.", device="cpu")
        get_profiler().mark("load.vocoder.read", est_gb=_estimated_gb(sd))
        if detect_ltx_vocoder_config(metadata) is not None:
            sliced = {k: v for k, v in sd.items() if k.startswith("vocoder.")}
            if sliced:
                sd = sliced
        quant_format = detect_quant_format(metadata, sd)
        sd_dtype = weight_dtype(sd)
        _, compute_dtype = pick_dtypes(sd_dtype, self.device, self.vram_gb)
        storage_dtype = sd_dtype or compute_dtype
        est_gb = _estimated_gb(sd)
        ops = self._ops_for("vae", est_gb, storage_dtype, compute_dtype, quant_format, sd)
        module = load_ltx_vocoder(path, ops, device="cpu", sd=sd, metadata=metadata)
        get_profiler().mark("load.vocoder.built", est_gb=est_gb, quant_format=quant_format)
        return NativeModel(
            "vocoder", module, estimated_vram_gb=est_gb,
            compute_dtype=compute_dtype, quant_format=quant_format,
        )

    def _load_latent_upscaler(self, path: str | Path) -> NativeModel:
        """Latent upsampler: a small standalone checkpoint, loaded via the
        same detect-then-build path as the other VAE-family components --
        the LTX-2.3 spatial upsampler (config embedded in its own metadata --
        see ``load_ltx_latent_upsampler``'s docstring). Only acquired by a
        model loader when the preset's upscale option is on, so this never
        runs at zero-extra-cost baseline.
        """
        sd, metadata = load_torch_file(path, device="cpu")
        get_profiler().mark("load.latent_upscaler.read", est_gb=_estimated_gb(sd))
        quant_format = detect_quant_format(metadata, sd)
        sd_dtype = weight_dtype(sd)
        _, compute_dtype = pick_dtypes(sd_dtype, self.device, self.vram_gb)
        storage_dtype = sd_dtype or compute_dtype
        est_gb = _estimated_gb(sd)
        ops = self._ops_for("vae", est_gb, storage_dtype, compute_dtype, quant_format, sd)
        module = load_ltx_latent_upsampler(path, ops, device="cpu", sd=sd, metadata=metadata)
        get_profiler().mark("load.latent_upscaler.built", est_gb=est_gb, quant_format=quant_format)
        return NativeModel(
            "latent_upscaler", module, estimated_vram_gb=est_gb,
            compute_dtype=compute_dtype, quant_format=quant_format,
        )

    def _load_duration_head(self, path: str | Path) -> NativeModel:
        """LTX-2.5 duration head: a few-MB standalone checkpoint whose config
        is derived from weight shapes rather than metadata (see
        ``arch/ltx/duration_head.py``). Acquired only when a preset configures
        the ``duration_head`` slot, so this never runs at baseline.

        The import is function-local for the same reason ``ModelSpec``
        resolves its ``model_class`` lazily: ``arch.ltx``'s package init pulls
        in the 22B DiT arch, which no other load kind needs.
        """
        from .arch.ltx.duration_head import load_ltx_duration_head

        sd, metadata = load_torch_file(path, device="cpu")
        get_profiler().mark("load.duration_head.read", est_gb=_estimated_gb(sd))
        quant_format = detect_quant_format(metadata, sd)
        sd_dtype = weight_dtype(sd)
        _, compute_dtype = pick_dtypes(sd_dtype, self.device, self.vram_gb)
        storage_dtype = sd_dtype or compute_dtype
        est_gb = _estimated_gb(sd)
        ops = self._ops_for("vae", est_gb, storage_dtype, compute_dtype, quant_format, sd)
        module = load_ltx_duration_head(path, ops, device="cpu", sd=sd, metadata=metadata)
        get_profiler().mark("load.duration_head.built", est_gb=est_gb, quant_format=quant_format)
        return NativeModel(
            "duration_head", module, estimated_vram_gb=est_gb,
            compute_dtype=compute_dtype, quant_format=quant_format,
        )

    def _load_te(self, path: str | Path | list, *, vision: bool = False) -> NativeModel:
        # The TE loader owns its own ops selection (fp8/manual_cast/standard) and
        # composite (T5+CLIP) assembly, so we hand it the path(s) directly.
        # ``vision`` (``qwen25_vl`` and ``qwen3vl``; ignored by every other TE
        # type) keeps+loads the checkpoint's vision tower for image-conditioned
        # editing (Qwen-Image-Edit / Krea-2 edit mode) -- see
        # ``load_text_encoder``'s docstring for the fingerprint hazard this must
        # be folded into (a text-only and vision-enabled load of the SAME path
        # build DIFFERENT modules; the caller's model-lifecycle fingerprint must
        # differ or a cache hit can hand back the wrong variant).
        get_profiler().mark("load.text_encoder.read", path=str(path))
        encoder = load_text_encoder(path, device="cpu", vision=vision)
        est_gb = _estimate_text_encoder_gb(encoder)
        get_profiler().mark("load.text_encoder.built", est_gb=est_gb)
        return NativeModel("text_encoder", encoder, estimated_vram_gb=est_gb)

    # -- ops selection -----------------------------------------------------

    def _ops_for(
        self, comp_key: str, est_gb: float,
        storage_dtype: torch.dtype, compute_dtype: torch.dtype, quant_format: str | None,
        sd: dict[str, torch.Tensor],
    ):
        """Pick the ops namespace.

        Priority: fp8 quant -> fp8_ops; mixed-dtype checkpoint (e.g. Krea-2's
        bf16+f32) -> manual_cast (a no-cast namespace would crash on the f32
        peripheral weights); streaming tier (when a VRAM budget is configured)
        -> manual_cast; otherwise dtype-driven via pick_operations.
        """
        if quant_format == QUANT_FP8_SCALED or storage_dtype in {torch.float8_e4m3fn, torch.float8_e5m2}:
            return fp8_ops
        if is_mixed_precision(sd):
            return manual_cast
        if self.vram_gb is not None:
            dp = DevicePlan(self.device, self.device, self.device)
            plan = plan_placement(self.vram_gb, {comp_key: est_gb}, quant_format, dp)
            mode = getattr(plan, "dit" if comp_key == "dit" else comp_key).ops_mode
            if mode == "manual_cast":
                return manual_cast
        return pick_operations(storage_dtype, compute_dtype, quant_format)


class Conditioning:
    """Encoded prompt payload: ``cond`` (+ optional ``uncond``) dicts.

    The dicts are the raw text-encoder output (role-keyed, e.g. ``"context"`` /
    ``"pooled"``); the sampler treats them as opaque and the model_forward
    adapter maps them to the DiT's ``forward`` kwargs.
    """

    def __init__(self, cond: dict[str, torch.Tensor], uncond: dict[str, torch.Tensor] | None = None) -> None:
        self.cond = cond
        self.uncond = uncond


class NativeGenerator:
    """Runs encode -> sample -> decode for one loaded model bundle."""

    def __init__(
        self,
        dit: NativeModel,
        te: NativeTextEncoder,
        vae: NativeModel,
        device_plan: DevicePlan | None = None,
        *,
        placement: PlacementPlan | None = None,
        vram_gb: float | None = None,
    ) -> None:
        self.dit = dit
        self.te = te
        self.vae = vae
        self.device_plan = device_plan or make_device_plan(preferred="cpu", cuda_available=lambda: False)
        # An explicit placement pins behaviour; otherwise it is computed per-run
        # from the VRAM budget (``vram_gb`` override or the live device total).
        self._explicit_placement = placement
        self.placement = placement
        self.vram_gb = vram_gb
        self.spec = dit.spec
        if self.spec is None:
            raise NativeEngineUnsupportedError("NativeGenerator requires a DiT NativeModel carrying a ModelSpec")

    # -- residency / placement helpers -------------------------------------

    def _resident(self, component: str) -> bool:
        if self.placement is None:
            return True
        return getattr(self.placement, component).resident

    def _is_cuda(self) -> bool:
        return str(self.device_plan.dit_device).startswith("cuda")

    def _budget_gb(self) -> float | None:
        """VRAM budget for placement: the ``vram_gb`` override, else the DiT
        device's total memory. ``None`` on CPU / when unknown (-> all resident)."""
        if self.vram_gb is not None:
            return self.vram_gb
        if not self._is_cuda() or not torch.cuda.is_available():
            return None
        # total_vram_gb() (memory/residency.py) rather than a raw mem_get_info
        # call so this is subject to the POTIONUI_VRAM_CAP_GB rig-simulation
        # cap like every other placement decision (see src.platform.runtime.vram_cap).
        return total_vram_gb(self.device_plan.dit_device)

    def _te_size_gb(self) -> float:
        est = getattr(self.te, "estimated_vram_gb", None)
        if est:
            return float(est)
        # NativeTextEncoder wrappers don't carry a size; sum their module params.
        total = 0
        for attr in ("module", "t5", "clip_l"):
            mod = getattr(self.te, attr, None)
            inner = getattr(mod, "module", mod)
            if isinstance(inner, torch.nn.Module):
                total += sum(p.numel() * p.element_size() for p in inner.parameters())
        return total / (1024 ** 3)

    def _build_placement(self, latents_shape, ref_latents=None) -> PlacementPlan | None:
        """Placement for this run: explicit override, else fit-based with the
        SAMPLING-phase headroom. Decode is phase-separated — weights are freed
        before decode and the decode tiles when its spike wouldn't fit — so the
        old decode-spike reservation here only starved the weights budget (at
        1080p the decode term alone exceeded a 31GB card and forced a fully
        streamed DiT). ``None`` when there is no VRAM budget (CPU) -> all resident.

        ``ref_latents``: reference-latent tensors riding the SAME DiT
        forward as the main image (Qwen-Image-Edit's in-context edit path,
        Krea-2's equivalent) — their token count is NOT reflected in
        ``latents_shape`` at all (they arrive via ``conditioning``, a separate
        argument to ``sample()``), so the resolution-only headroom above
        silently under-reserves for them unless folded in here. See
        ``ref_latents_headroom_gb``.
        """
        if self._explicit_placement is not None:
            return self._explicit_placement
        budget = self._budget_gb()
        if budget is None:
            return None
        sizes = {
            "dit": self.dit.estimated_vram_gb or 0.0,
            "text_encoder": self._te_size_gb(),
            "vae": self.vae.estimated_vram_gb or 0.0,
        }
        headroom = sampling_headroom_gb(
            (latents_shape[-2], latents_shape[-1]), latent_frames=_latent_frames(latents_shape)
        )
        headroom += ref_latents_headroom_gb(self._ref_hw_frames(ref_latents))
        return plan_placement(
            budget, sizes, self.dit.quant_format, self.device_plan,
            working_headroom_gb=headroom,
        )

    @staticmethod
    def _ref_hw_frames(ref_latents) -> list[tuple[tuple[int, int], int]]:
        """Normalise a ``ref_latents`` conditioning value into
        ``[((h, w), latent_frames), ...]`` for :func:`ref_latents_headroom_gb`.

        Accepts both calling conventions seen in the wild: a bare tensor (one
        reference — Krea-2's ``cond["ref_latents"] = ref_latent``) or a
        list/tuple of tensors (Qwen-Image's ``ref_latents=[...]``, and Krea-2's
        own multi-source case). ``None``/empty/shape-less entries are skipped
        rather than raising — a malformed conditioning value should degrade
        the estimate, not crash placement.
        """
        if ref_latents is None:
            return []
        items = ref_latents if isinstance(ref_latents, (list, tuple)) else [ref_latents]
        out: list[tuple[tuple[int, int], int]] = []
        for ref in items:
            shape = tuple(getattr(ref, "shape", ()))
            if len(shape) < 2:
                continue
            h, w = int(shape[-2]), int(shape[-1])
            frames = _latent_frames(shape) if len(shape) == 5 else 1
            out.append(((h, w), frames))
        return out

    def _maybe_offload_te(self) -> None:
        """Offload the TE to CPU after encoding — it is dead weight through
        sampling and decode (single encode per generation), so freeing it is the
        biggest peak-VRAM win. No-op on CPU.

        ``self.te`` may be a raw ``NativeTextEncoder`` (has ``.to``), a composite,
        or a ``ClipTextEncoder`` WRAPPER (``Krea2ClipTextEncoder`` etc.) that has
        no ``.to`` — only a ``.encoder``. Duck-type so the decode OOM-retry that
        calls this never itself crashes with an ``AttributeError`` on the recovery
        path (that would turn a recoverable OOM into a hard failure)."""
        if not self._is_cuda():
            return
        target = self.te
        if not hasattr(target, "to") and hasattr(target, "encoder"):
            target = target.encoder  # clip wrapper -> underlying native encoder
        mover = getattr(target, "to", None)
        if callable(mover):
            try:
                mover("cpu")
            except Exception:  # pragma: no cover - best-effort offload
                logger.debug("text-encoder offload to cpu failed", exc_info=True)

    def _own_models(self) -> list:
        """This generation's own NativeModels — never evicted to free room for it.

        Excludes the TE: its module is moved directly (not via ``NativeModel.
        move_to``) and offloaded after encode, so it is never in the residency
        registry to begin with.
        """
        return [self.dit, self.vae]

    def _ensure_room_for(self, need_gb: float, device: str) -> None:
        """Evict FOREIGN GPU-resident components (a prior generation's / another
        family's DiT left resident) to make ``need_gb`` free on ``device``.

        This generation's own DiT/VAE are never evicted. When ``need_gb`` is a
        real estimate the manager frees LRU foreign models until it fits; when the
        estimate is missing/zero (a NativeModel with no ``estimated_vram_gb``) we
        fall back to evicting ALL foreign residents — correctness over cleverness.
        No-op on CPU / when VRAM can't be queried.
        """
        if not str(device).startswith("cuda"):
            return
        free = free_vram_gb(device)
        if free is None:
            return
        manager = get_residency_manager()
        own = self._own_models()
        if need_gb and need_gb > 0.0:
            offloaded = manager.ensure_free(device, need_gb, free, exclude=own)
        else:
            offloaded = manager.offload_all(device, exclude=own)
        if offloaded and torch.cuda.is_available():
            torch.cuda.empty_cache()

    def _move_dit_to_gpu(self, device: str) -> None:
        """Move the DiT onto ``device`` for sampling, making room first.

        Frees foreign residents up front (``_ensure_room_for``); if the move still
        OOMs — e.g. the estimate was optimistic or VRAM is fragmented — evict ALL
        foreign residents and retry once. A persisting OOM is a genuine capacity
        limit and propagates (the DiT must be resident to sample).
        """
        need = float(getattr(self.dit, "estimated_vram_gb", None) or 0.0)
        if need > 0.0:
            need += minimum_inference_memory_gb()
        self._ensure_room_for(need, device)
        try:
            self.dit.move_to(device)
        except torch.cuda.OutOfMemoryError:
            logger.warning("DiT move to %s OOM'd; evicting all foreign residents and retrying", device)
            get_residency_manager().offload_all(device, exclude=self._own_models())
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            try:
                self.dit.move_to(device)
            except torch.cuda.OutOfMemoryError:
                # A co-tenant process can grab VRAM between the placement
                # decision and the move (we can't evict other processes).
                # Degrade to partial residency against what's actually free
                # instead of failing the generation.
                free = free_vram_gb(device) or 0.0
                budget = max(0.0, free - minimum_inference_memory_gb())
                logger.warning(
                    "DiT full move still OOM (co-tenant?); degrading to partial "
                    "residency with %.1fGB weights budget", budget,
                )
                self.dit.offload()
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
                self.dit.stream_to(device, budget)

    def _dit_weights_budget_gb(self, latents_shape, ref_latents=None) -> float:
        """VRAM (GB) available for RESIDENT DiT weights under partial residency.

        Foreign residents are evicted first (``_ensure_room_for``); then the live
        free VRAM minus the resolution-scaled activation headroom (the DiT forward's
        attention/activation spike, same estimate placement uses) is what remains
        for weights. Streamed leaves only transit VRAM one at a time per forward, so
        they don't count here.

        ``ref_latents``: see ``_build_placement``'s docstring — the same
        reference-token headroom must be subtracted here too, or a partial-
        residency budget computed without it would leave too much weight
        resident and OOM on the ref-inflated activation spike.
        """
        device = self.device_plan.dit_device
        self._ensure_room_for(self.dit.estimated_vram_gb or 0.0, device)
        free = free_vram_gb(device) or 0.0
        # Sampling headroom only — decode frees the weights and tiles if needed,
        # so its spike must not be charged against the resident-weights budget.
        headroom = sampling_headroom_gb(
            (latents_shape[-2], latents_shape[-1]), latent_frames=_latent_frames(latents_shape)
        )
        headroom += ref_latents_headroom_gb(self._ref_hw_frames(ref_latents))
        return max(0.0, free - headroom)

    def _stream_dit_to_gpu(self, device: str, latents_shape, ref_latents=None) -> None:
        """Place the DiT with partial residency for sampling.

        Keeps as many leaves resident as fit the weights budget and streams the
        rest from pinned CPU RAM. If even the resident portion OOMs (an optimistic
        free-VRAM read or fragmentation) it evicts ALL foreign residents and retries
        with a zero budget — every streamable leaf on the CPU, only the small fixed
        tensors resident — the guaranteed-to-fit correctness backstop.
        """
        if not str(device).startswith("cuda"):
            self.dit.move_to(device)
            return
        budget = self._dit_weights_budget_gb(latents_shape, ref_latents=ref_latents)
        try:
            self.dit.stream_to(device, budget)
        except torch.cuda.OutOfMemoryError:
            logger.warning("partial-residency DiT placement OOM'd (budget %.1fGB); "
                           "evicting all foreign residents and streaming fully", budget)
            self.dit.offload()
            get_residency_manager().offload_all(device, exclude=self._own_models())
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            self.dit.stream_to(device, 0.0)

    def _maybe_compile(self) -> None:
        """Regionally ``torch.compile`` the resident DiT's blocks if enabled + eligible.

        Delegates every gate (env toggle, resident-only, no runtime LoRA, no
        quantised Linears, discoverable block lists) to
        ``optimizations/compile.py``; a no-op otherwise. The undo handle lives on
        ``self.dit`` and is restored when the DiT leaves the GPU (see
        ``NativeModel.move_to`` / ``unload``)."""
        from .optimizations.compile import maybe_compile_dit

        # "Resident" for compile means FULLY resident: the placement said so AND
        # the DiT didn't degrade to partial-residency streaming (the co-tenant-OOM
        # fallback in ``_move_dit_to_gpu`` can flip a "resident" plan into a live
        # ``_streamer``, which is incompatible with a compiled graph).
        streaming = self.dit._streamer is not None and self.dit._streamer.active
        resident = self._resident("dit") and not streaming
        maybe_compile_dit(self.dit, resident=resident, is_cuda=self._is_cuda())

    # -- public ------------------------------------------------------------

    def _is_causal3d_vae(self) -> bool:
        """True for the 3D causal VAE family (Qwen-Image / Wan / SeedVR2) — 5D
        latents, decoded via the ``decode_image`` convenience API and the
        causal-3D tiling/spike machinery."""
        return hasattr(self.vae.module, "decode_image")

    def _is_self_normalizing_vae(self) -> bool:
        """True when the VAE applies its OWN fixed latent scaling inside
        ``encode``/``decode`` — so the engine must NOT layer the wan21
        per-channel ``latents_mean``/``latents_std`` transform on top.

        SeedVR2's VAE folds a scalar ``0.9152`` factor into its encode/decode
        (see ``vae/seedvr2_causal_video.py``). This mirrors the LTX video VAE,
        whose ``per_channel_statistics`` likewise live inside the module; the
        difference is that LTX simply doesn't expose ``decode_image`` (so
        ``_is_causal3d_vae`` is False and it never enters the wan21 path), while
        SeedVR2 IS a causal-3D ``decode_image`` VAE and must keep that shape /
        tiling / spike handling but skip only the normalization. Keyed off the
        ModelSpec ``latent_format.format`` so no per-module hasattr guessing."""
        return self.spec.latent_format.get("format") == "seedvr2"

    def latent_shape_for(self, width: int, height: int, batch: int = 1) -> tuple[int, ...]:
        """Authoritative VAE-latent shape for an image size.

        Callers must use this instead of hardcoding ``//8`` — neither the
        downscale, the channel count, nor the *rank* is uniform:

          * Flux1: ``(B, 16, H//8, W//8)`` — 16 latent channels, 2D.
          * Flux2/Klein: ``(B, 128, H//16, W//16)`` — flux2 VAE folds an extra 2x
            spatially via pixel_unshuffle (32 z-channels -> 128), 2D.
          * Qwen-Image: ``(B, 16, 1, H//8, W//8)`` — 5D (B,C,T,H,W) because the
            VAE is 3D-causal; T=1 for a still image. 16 wan21 z-channels, //8.

        Flux ``C`` is the DiT's pre-pack input channels (``params.in_channels``);
        the 3D-VAE ``C`` is the VAE's latent channels (the DiT patchifies 2x2
        internally, so its ``in_channels`` is the *post*-pack dim, not this).
        """
        if self._is_causal3d_vae():
            from .vae.causal_3d import LATENTS_MEAN
            return (batch, len(LATENTS_MEAN), 1, height // 8, width // 8)
        dit_ch = int(self.dit.module.params.in_channels)
        downscale = self._spatial_downscale()
        return (batch, dit_ch, height // downscale, width // downscale)

    def _spatial_downscale(self) -> int:
        """Pixel->latent spatial factor: 8 for the causal-3D VAE; 8*fold for the
        Flux 2D AE, where Flux2's VAE folds an extra 2x via pixel_unshuffle."""
        if self._is_causal3d_vae():
            return 8
        dit_ch = int(self.dit.module.params.in_channels)
        vae_z = int(getattr(self.vae.module, "z_channels", dit_ch)) or dit_ch
        fold = max(1, round((dit_ch / vae_z) ** 0.5))
        return 8 * fold

    def pixel_granularity(self) -> int:
        """The multiple width and height must be — ``spatial_downscale`` times the
        DiT patch size (together they map a pixel edge to one token axis; a
        non-multiple breaks the patchify rearrange, e.g. Krea-2 at 1080px)."""
        return self._spatial_downscale() * int(self.dit.module.patch_size)

    def snap_resolution(self, width: int, height: int) -> tuple[int, int]:
        """Snap a requested size to :meth:`pixel_granularity`, warning on change."""
        from .resolution import snap_resolution as _snap
        snapped = _snap(width, height, self._spatial_downscale(), int(self.dit.module.patch_size))
        if snapped != (width, height):
            warn_key = (self.spec.family, self.spec.variant, width, height, snapped[0], snapped[1])
            if warn_key not in _warned_snapped_resolutions:
                _warned_snapped_resolutions.add(warn_key)
                logger.warning(
                    "[NATIVE] %s/%s: snapped resolution %dx%d -> %dx%d (granularity %dpx)",
                    self.spec.family, self.spec.variant, width, height, snapped[0], snapped[1],
                    self.pixel_granularity(),
                )
        return snapped

    def encode_prompt(self, prompt: str, negative: str | None = None) -> Conditioning:
        """Encode ``prompt`` (and ``negative`` for CFG models) into conditioning.

        Convenience for standalone use — the pipe path builds the cond dict from
        a ConditioningModel and calls ``sample`` directly (see ``sample``).
        Normalises the raw TE output to the ``context``/``y``/``attention_mask``
        cond-dict contract.
        """
        te_device = self.device_plan.te_device
        self.te.to(te_device)
        cond = self._normalize_te(self.te.encode([prompt]))
        uncond = None
        # Only classic-CFG models consume a negative; embedded-guidance ignores it.
        if negative is not None and self.spec.sampling_settings.get("guidance") == "cfg":
            uncond = self._normalize_te(self.te.encode([negative]))
        # TE is phase-dead for the rest of the generation -> free it now.
        self._maybe_offload_te()
        return Conditioning(cond, uncond)

    @staticmethod
    def _normalize_te(raw: dict[str, torch.Tensor]) -> dict[str, torch.Tensor]:
        """Map a raw text-encoder dict to the cond-dict contract.

        TE encoders return ``{"context", "pooled"?}`` (Flux1) or
        ``{"context", "attention_mask"?}`` (Klein); normalise both to
        ``{"context", "y", "attention_mask"}`` with ``None`` where absent.
        """
        return {
            "context": raw["context"],
            "y": raw.get("y", raw.get("pooled")),
            "attention_mask": raw.get("attention_mask"),
        }

    def _sampling_settings_for(self, guidance_options: dict | None, schedule_settings: dict | None) -> dict:
        """The ModelSpec ``sampling_settings`` plus any per-generation APG /
        schedule overrides ``denoise()`` reads from that same dict.

        Whitelisted merge (see ``_APG_SETTINGS_KEYS`` / ``_SCHEDULE_SETTINGS_KEYS``):
        APG keys come from ``guidance_options``, schedule keys from
        ``schedule_settings`` -- including ``fixed_mu``/``dynamic_shift``, which let
        a preset swap the mu-shift SOURCE a ModelSpec pins (Krea-2: turbo's fixed
        mu=1.15 vs a raw/base checkpoint's resolution-anchored dynamic mu -- see
        ``generator/krea2/main.py``). Returns the ModelSpec dict UNCHANGED (same
        object) when neither supplies a recognised key, so the denoise path is
        byte-identical unless a preset opts in. ``slg_*`` is not in either
        whitelist — the image arches have no skip_layers.
        """
        extra: dict = {}
        gopts = guidance_options or {}
        for key in _APG_SETTINGS_KEYS:
            if key in gopts:
                extra[key] = gopts[key]
        sopts = schedule_settings or {}
        for key in _SCHEDULE_SETTINGS_KEYS:
            if key in sopts:
                extra[key] = sopts[key]
        if not extra:
            return self.spec.sampling_settings
        return {**self.spec.sampling_settings, **extra}

    def sample(
        self,
        conditioning: "Conditioning | dict | tuple",
        latents_shape: tuple[int, int, int, int],
        steps: int,
        seed: int,
        cfg_scale: float,
        sampler: str = "euler",
        hooks=(),
        is_cancelled=None,
        denoise_strength: float = 1.0,
        noise: torch.Tensor | None = None,
        init_latent: torch.Tensor | None = None,
        guidance_options: dict | None = None,
        sampler_options: dict | None = None,
        step_cache_options: dict | None = None,
        warm_start: bool = False,
        schedule_settings: dict | None = None,
        spectral_progressive: dict | None = None,
        sigmas: "Sequence[float] | torch.Tensor | None" = None,
    ) -> torch.Tensor:
        """Denoise from seeded noise to a clean latent (model-native space).

        ``conditioning`` is an externally-built payload — this is the primary
        (pipe) path: a bare cond ``dict``, a ``(cond, uncond)`` tuple, or a
        :class:`Conditioning`. Each cond dict follows the contract
        ``{"context": [B,S,D], "y": pooled|None, "attention_mask": [B,S]|None}``;
        the sampler injects the ``"guidance"`` key itself for embedded guidance.

        ``noise`` injects an explicit initial-noise tensor (golden-comparison
        against ComfyUI needs the SAME initial noise); when omitted it is drawn
        from a ``torch.Generator`` seeded with ``seed``.

        ``init_latent`` makes this an **img2img** run: the model-native latent of
        an input image (from :meth:`encode_image`), whose shape must equal
        ``latents_shape``. With ``denoise_strength < 1`` the schedule is truncated
        (``sigmas[0] < 1``) and the loop starts from a ``sigma0``-weighted blend of
        noise and this latent (see ``sampling/denoise_loop.py``). Omit it (the
        default zeros) for txt2img — where ``denoise_strength`` is ignored because
        ``sigma0 == 1`` makes ``x == noise`` regardless of the latent.

        ``guidance_options`` surfaces the sampler's TrueCFG knobs to presets:
        ``cfg_zero_star`` (default True) and ``zero_init_steps`` (default 0). They
        only affect the ``"cfg"`` guidance strategy; the defaults preserve current
        behaviour exactly, so an omitted/empty dict is a no-op.

        ``guidance_options`` also carries the APG corrections (``apg_eta``/
        ``apg_norm_threshold``/``apg_momentum``) and ``schedule_settings`` carries
        the schedule-shaping knobs (``schedule``/``schedule_options``/
        ``detail_strength``/``detail_start``/``detail_end``/``fixed_mu``/
        ``dynamic_shift``); both are whitelisted into the ``sampling_settings``
        dict ``denoise()`` reads (see
        :meth:`_sampling_settings_for`). ``slg_*`` is intentionally NOT merged on
        this (image) path. Absent keys leave ``sampling_settings`` untouched, so
        the run is byte-identical unless a preset opts in.

        ``sampler_options``/``step_cache_options`` are forwarded to ``denoise()``
        unchanged (opaque dicts; see :func:`~src.platform.runtime.native.sampling.denoise_loop.denoise`
        for their shape) — ``None`` (the default) for either is a byte-identical
        no-op, same treatment as ``guidance_options``.

        ``sigmas``: an explicit, already-built schedule (sequence of floats or a
        1-D tensor), forwarded to ``denoise()`` AS-IS -- bypasses every
        schedule-derivation input (``steps``, ``schedule_settings``, the
        ModelSpec's ``shift``/``base_shift``/``max_shift``/etc.) exactly like
        ``denoise()``'s own ``sigmas`` kwarg. Validated here (the public
        boundary): must be 1-D, at least 2 values, strictly decreasing,
        ``sigmas[0] <= 1.0``, ``sigmas[-1] == 0.0`` -- raises ``ValueError``
        naming the offense otherwise. The effective step count becomes
        ``len(sigmas) - 1`` for every ``steps``-derived decision below (progress
        hooks, warm-start/trajectory-cache keys, spectral-progressive gating);
        the ``steps`` argument itself is ignored when ``sigmas`` is given, same
        as ``denoise_strength`` (an explicit sigma list already encodes its own
        starting noise level via ``sigmas[0]`` -- a second truncation input
        would be ambiguous about which one wins, so ``denoise_strength`` is
        simply inert here, matching ``denoise()``'s own documented contract for
        this kwarg). Spectral-progressive is unconditionally skipped when
        ``sigmas`` is set (that path builds its own schedule and has no way to
        honour an explicit one).
        """
        opts = guidance_options or {}
        sigmas_tensor = _validate_explicit_sigmas(sigmas) if sigmas is not None else None
        effective_steps = int(sigmas_tensor.numel() - 1) if sigmas_tensor is not None else steps
        cond_dict, uncond_dict = self._unwrap_conditioning(conditioning)
        # Reference-latent tokens (Qwen-Image-Edit / Krea-2 in-context edit) ride
        # the same DiT forward as the main image but aren't reflected in
        # `latents_shape` at all — fold them into the placement/budget headroom
        # `cond` and `uncond` always carry the same ref_latents (see
        # qwen_clip.py / krea2-edit's own conditioning build), so cond alone is enough.
        ref_latents = cond_dict.get("ref_latents")
        # Resolution-aware placement drives phase residency (esp. whether the DiT
        # must be offloaded before decode to make room for the decode spike).
        self.placement = self._build_placement(latents_shape, ref_latents=ref_latents)
        device = self.device_plan.dit_device
        dtype = self.dit.compute_dtype
        # Evict a prior generation's / other family's still-resident DiT before
        # claiming VRAM for ours (never our own DiT/VAE), with an OOM-retry net.
        # A DiT that fits is moved whole; one that doesn't is placed with PARTIAL
        # residency (as many leaves resident as fit, the rest streamed from pinned
        # CPU RAM) instead of the old all-or-nothing move that would OOM.
        if self._resident("dit"):
            self._move_dit_to_gpu(device)
            # Resident placement only: regionally torch.compile the DiT blocks
            # (opt-in, gated). Streaming placement is deliberately excluded — the
            # per-forward weight swap is incompatible with a compiled graph.
            self._maybe_compile()
        else:
            self._stream_dit_to_gpu(device, latents_shape, ref_latents=ref_latents)

        if init_latent is not None:
            latents = init_latent.to(device=device, dtype=dtype)
        else:
            latents = torch.zeros(latents_shape, device=device, dtype=dtype)
        if noise is not None:
            seed_noise = noise.to(device=device, dtype=dtype)
            # An explicit noise tensor (golden-comparison callers) has no
            # seed-derived generator to reuse -- seed determinism for a
            # stochastic sampler is already the caller's problem in that case.
            seed_generator = None
        else:
            seed_generator = torch.Generator(device=device).manual_seed(int(seed))
            seed_noise = torch.randn(latents_shape, generator=seed_generator, device=device, dtype=dtype)
        # Seed determinism for stochastic samplers (euler_sde/dpmpp_2m_sde/lcm):
        # reuse the SAME generator object that drew the init noise, continuing
        # its stream, rather than an unseeded global RNG (see
        # ensure_sampler_generator's docstring for why not a second
        # identically-seeded generator).
        sampler_options = ensure_sampler_generator(sampler_options, sampler, seed_generator)

        image_seq_len = self._image_seq_len(latents_shape)
        model_forward = self._make_forward(device, dtype)

        cond = self._move_cond(cond_dict, device, dtype)
        uncond = self._move_cond(uncond_dict, device, dtype) if uncond_dict else None

        merged_settings = self._sampling_settings_for(guidance_options, schedule_settings)

        # Spectral Progressive Diffusion (opt-in prototype): run early steps at a
        # reduced latent resolution and grow. Image families only (4D latent,
        # constant shift); txt2img only (no init_latent). Mutually exclusive with
        # warm-start (a different-resolution trajectory can't be resumed) and with
        # an explicit sigma list (this path builds its own schedule and has no
        # way to honour a caller-supplied one). When the config is absent this
        # whole block is skipped and the run is unchanged.
        sp_config = None
        if sigmas_tensor is None:
            sp_config = self._spectral_progressive_config(
                spectral_progressive, init_latent, latents, merged_settings)
        if sp_config is not None:
            return self._sample_spectral_progressive(
                model_forward, latents, cond, uncond, steps=effective_steps, sampler=sampler,
                merged_settings=merged_settings, cfg_scale=cfg_scale, opts=opts,
                seed_noise=seed_noise, hooks=hooks, is_cancelled=is_cancelled,
                sampler_options=sampler_options, sp_config=sp_config,
            )

        # Trajectory warm-start ("iterate mode"): resume from a cached mid-run
        # latent when the conditioning barely changed. txt2img + euler only (the
        # only path that reproduces a bit-identical tail); an explicit noise tensor
        # (not seed-derived) can't be keyed, so warm-start is disabled there too.
        # An explicit sigma list is folded into the schedule signature (see
        # ``schedule_signature``'s ``explicit_sigmas``) so a cached plan from a
        # derived schedule of the same nominal length can never cross-resume with
        # an explicit-list run, in either direction.
        resume, run_hooks = self._plan_warm_start(
            warm_start and noise is None, sampler, init_latent, cond, uncond, seed,
            latents_shape, effective_steps, image_seq_len, hooks, merged_settings,
            opts, sampler_options, step_cache_options, sigmas=sigmas_tensor,
        )

        latent = denoise(
            model_forward,
            latents,
            cond,
            uncond,
            steps=effective_steps,
            sampler_name=sampler,
            sampling_settings=merged_settings,
            guidance_scale=cfg_scale,
            image_seq_len=image_seq_len,
            hooks=run_hooks,
            is_cancelled=is_cancelled,
            seed_noise=seed_noise,
            denoise_strength=denoise_strength,
            cfg_zero_star=opts.get("cfg_zero_star", True),
            zero_init_steps=opts.get("zero_init_steps", 0),
            sampler_options=sampler_options,
            step_cache_options=step_cache_options,
            resume=resume,
            sigmas=sigmas_tensor,
        )
        self._release_dit_after_sampling()
        return latent

    def _release_dit_after_sampling(self) -> None:
        """Tear down the DiT's sampling placement at the end of a run.

        Offload (which tears down an active streamer, unpinning + reclaiming its
        host pool) whenever the DiT is streamed -- either because placement
        PLANNED partial residency (``not _resident``) OR because a co-tenant-OOM
        degrade in ``_move_dit_to_gpu`` switched a "resident" plan to streaming
        after the fact, leaving ``placement.dit.resident`` stale-True.
        Without the ``is_streaming`` arm that degrade left ~the whole DiT pinned
        in host RAM for the life of the RAM-cached entry. A cleanly fully-resident
        DiT is left on the GPU for cheap reuse, exactly as before.
        """
        if not self._resident("dit") or self.dit.is_streaming:
            self.dit.offload()

    def _plan_warm_start(self, warm_start, sampler, init_latent, cond, uncond, seed,
                         latents_shape, steps, image_seq_len, hooks, settings,
                         guidance_options, sampler_options, step_cache_options,
                         sigmas=None):
        """Decide trajectory resume + install the checkpoint-capture hook.

        Returns ``(resume, hooks)`` where ``resume`` is ``(start_step, latent)``
        or ``None``. Sets ``self.last_warm_start`` to the resume metadata (or
        ``None``). Eligibility: opt-in, ``euler``, txt2img (no ``init_latent``),
        and NOT with stateful APG momentum (its running average makes the resumed
        Euler tail non-identical to the cold run — S6). Ineligible/disabled →
        ``(None, hooks)`` and an untouched run.

        ``steps`` is the caller's EFFECTIVE step count (``len(sigmas) - 1`` when
        ``sigmas`` is an explicit list); ``sigmas`` itself is folded into the
        schedule signature (see ``schedule_signature``) so an explicit-list run
        can never resume from -- or be resumed by -- a derived-schedule entry of
        the same nominal length.
        """
        self.last_warm_start = None
        if not warm_start or sampler != "euler" or init_latent is not None:
            return None, hooks
        if float(settings.get("apg_momentum", 0.0)) != 0.0:
            return None, hooks  # S6: stateful guidance breaks the resume identity
        from .sampling.trajectory_cache import (
            CheckpointCaptureHook, conditioning_fingerprint, decide_resume,
            get_trajectory_cache, schedule_signature, warm_start_settings_signature,
        )
        sched_sig = schedule_signature(steps, image_seq_len, settings, explicit_sigmas=sigmas)
        # Everything else that changes the trajectory but isn't already in the key
        # (E2): guidance/CFG-zero/APG/SLG knobs, sampler options, step-cache
        # options. cfg_scale is DELIBERATELY excluded (documented trade — a CFG
        # nudge should resume deep), but only when all of these match.
        settings_sig = warm_start_settings_signature(
            settings, guidance_options, sampler_options, step_cache_options)
        # Session-scoped model identity: id(module) changes on a reload, so a
        # swapped checkpoint (same family/variant) never resumes a stale latent.
        static_key = (
            self.spec.family, self.spec.variant, id(self.dit.module), int(seed),
            tuple(latents_shape), sampler, int(steps),
            settings.get("guidance"), sched_sig, settings_sig,
        )
        fingerprint = conditioning_fingerprint(cond, uncond)  # S12: incl. negative
        cache = get_trajectory_cache()
        plan = decide_resume(cache.get(static_key), fingerprint, steps, sched_sig)
        entry = cache.get_or_create(static_key, steps, sched_sig)
        entry.cond_fingerprint = fingerprint  # compare the NEXT run against this one
        run_hooks = tuple(hooks) + (CheckpointCaptureHook(entry, steps),)
        if not plan.is_warm:
            return None, run_hooks
        self.last_warm_start = {
            "resume_step": plan.resume_step, "total_steps": steps,
            "steps_skipped": plan.resume_step, "similarity": round(plan.similarity, 4),
        }
        logger.debug(
            "[NATIVE] trajectory warm-start: resumed at step %d/%d (cond similarity %.4f)",
            plan.resume_step, steps, plan.similarity,
        )
        return (plan.resume_step, plan.latent), run_hooks

    def _spectral_progressive_config(self, spectral_progressive, init_latent, latents, settings):
        """Resolve the opt-in spectral-progressive config, or ``None`` (disabled).

        Eligibility (prototype v1): a non-empty config with ``enabled`` truthy,
        txt2img (no ``init_latent``), a 4D image latent, and a CONSTANT-shift
        family. Dynamic-mu families (Flux1's seq-len ``base_shift``/``max_shift``,
        Krea-2's anchored ``dynamic_shift``) are EXCLUDED v1 — mu shifts with the
        per-stage token count and the paper's handling isn't ported yet — so this
        cleanly targets Flux2 (shift 2.02) and Z-Image (shift 3.0). Ineligible/
        absent -> ``None`` (normal path).
        """
        opts = dict(spectral_progressive or {})
        if not opts or not opts.pop("enabled", True):
            return None
        if init_latent is not None or latents.ndim != 4:
            logger.debug("[NATIVE] spectral-progressive ignored (needs txt2img + 4D latent)")
            return None
        if any(settings.get(k) is not None for k in ("base_shift", "max_shift", "dynamic_shift")):
            logger.debug("[NATIVE] spectral-progressive ignored (dynamic-shift family, excluded v1)")
            return None
        from .sampling.spectral_progressive import SpectralProgressiveConfig
        for k in ("scales", "transitions"):
            if isinstance(opts.get(k), list):
                opts[k] = tuple(opts[k])
        return SpectralProgressiveConfig(**opts)

    def _sample_spectral_progressive(self, model_forward, latents, cond, uncond, *,
                                     steps, sampler, merged_settings, cfg_scale, opts,
                                     seed_noise, hooks, is_cancelled, sampler_options, sp_config):
        """Route a sample() call through the staged spectral-progressive orchestrator."""
        from .sampling.denoise_loop import SAMPLERS, make_guidance
        from .sampling.spectral_progressive import denoise_spectral_progressive

        guidance = make_guidance(
            merged_settings, cfg_scale,
            opts.get("cfg_zero_star", True), opts.get("zero_init_steps", 0),
        )
        latent = denoise_spectral_progressive(
            model_forward, latents, cond, uncond, steps=steps,
            sampler=SAMPLERS[sampler], sampler_name=sampler, guidance=guidance,
            shift=float(merged_settings.get("shift", 1.0) or 1.0), cfg=sp_config,
            seed_noise=seed_noise, hooks=hooks, is_cancelled=is_cancelled,
            sampler_options=sampler_options,
            generator=(sampler_options or {}).get("generator"),
            patch_multiple=int(getattr(self.dit.module, "patch_size", 2) or 2),
        )
        self._release_dit_after_sampling()
        return latent

    @torch.no_grad()
    def decode(self, latents: torch.Tensor, *, vram_free_gb: float | None = None) -> np.ndarray:
        """Decode a model-native latent to a ``(B, H, W, 3)`` uint8 image array.

        Runs under ``torch.no_grad`` for the same reason as
        :meth:`encode_image`: the final decode is called outside the sampler's
        guard, so an unguarded graph would transiently double the (already ~15GB
        at 1024²) causal-3D decode spike on the same OOM-prone path.

        The DiT is dead weight during decode; when placement says it must not
        stay resident it is offloaded first to make room for the (fp32) VAE
        decode spike. Foreign residents (a prior family's DiT/VAE) are evicted
        up front to make room for that spike (the causal-3D 1024² spike is ~15GB
        — the exact allocation that OOMs on top of a stale resident). An OOM
        during decode triggers a free-everything-but-our-VAE + empty_cache +
        retry safety net (mirrors ComfyUI's free-before-VAE).

        For the causal-3D VAE the decode spike scales with output pixels and can
        exceed the whole card past ~1024² (eviction can't fix a single spike that
        doesn't fit) — so decode goes SPATIALLY TILED. That engages proactively
        (when the estimate says the untiled spike won't fit live free VRAM, we
        skip the doomed full attempt that only fragments VRAM) and as an OOM-retry
        backstop, both via a shrink-on-OOM tile loop. The untiled path stays
        byte-identical whenever the full spike fits.
        """
        need = self._decode_need_gb(latents)
        device = self.device_plan.vae_device
        # Phase sequencing: drop the DiT before decode when placement flags it.
        if not self._resident("dit"):
            self.dit.offload()
        # Evict FOREIGN residents to fit the (fp32) VAE decode spike; never our VAE.
        self._ensure_room_for(need, device)

        if self._is_causal3d_vae():
            # Keep our DiT resident through decode whenever possible: unloading
            # it here means re-loading 20-26GB of weights over PCIe on the NEXT
            # generation (~10s+), which costs far more than tiling this decode
            # (a few seconds). The fit test judges LIVE free VRAM as-is — with a
            # resident DiT it simply sees less room and tiles; the shrink-on-OOM
            # tile loop plus the cleared-card retry below stay as the safety
            # net for cards where even a small tile can't co-fit.
            # TEMPORAL chunking (exact, no spatial seams) is preferred over spatial
            # tiling whenever a single frame's spatial spike fits and the VAE
            # supports feat_cache chunking (wan v1/v2); the two axes don't compose
            # (see ``chunked_decode_causal3d``). It engages both when the preflight
            # estimate rejects untiled AND when an optimistic untiled attempt
            # actually OOMs (fragmentation) — the latter re-evaluates the chunk
            # budget against the freed live VRAM instead of jumping to tiling.
            chunk_frames = None
            if self._causal3d_decode_fits(latents, device):
                try:
                    return self._decode_once(latents, vram_free_gb=vram_free_gb)
                except torch.cuda.OutOfMemoryError:
                    logger.warning("untiled causal-3D decode OOM — trying temporal chunk, else TILED")
                    if torch.cuda.is_available():
                        torch.cuda.empty_cache()
                    chunk_frames = self._causal3d_chunk_frames(latents, device)
            else:
                chunk_frames = self._causal3d_chunk_frames(latents, device)
                if chunk_frames is None:
                    logger.debug("decode: causal-3D spike won't co-fit with residents — decoding TILED")

            if chunk_frames is not None:
                try:
                    return self._decode_causal3d_chunked(latents, device, chunk_frames)
                except torch.cuda.OutOfMemoryError:
                    logger.warning("temporal-chunked decode OOM — falling back to SPATIAL tiled")
                    self._free_for_decode_retry(device)
            try:
                return self._decode_causal3d_tiled(latents, device)
            except torch.cuda.OutOfMemoryError:
                logger.warning("tiled causal-3D decode OOM — clearing the card and retrying TILED")
                self._free_for_decode_retry(device)
                return self._decode_causal3d_tiled(latents, device)

        # 2D Flux AE: offload our OWN DiT too if the spike won't co-fit — a high-res
        # decode needs the DiT's VRAM even when placement kept it resident for its
        # weight size — then decode (``auto_tile_size`` handles 2D tiling from
        # vram_free_gb). An OOM frees everything-but-our-VAE and retries TILED.
        self._free_own_dit_for_decode(need, device)
        # The caller decodes with no explicit figure, so measure live free VRAM
        # here or the untiled full-res spike is the only path 2D decode ever takes.
        decode_free = vram_free_gb if vram_free_gb is not None else free_vram_gb(device)
        try:
            return self._decode_once(latents, vram_free_gb=decode_free)
        except torch.cuda.OutOfMemoryError:
            logger.warning("decode OOM; offloading our DiT+TE, evicting foreign residents, and retrying TILED")
        # The retry MUST run after the ``except`` block exits: the failed attempt's
        # traceback pins its decode activations (empty_cache cannot reclaim live
        # tensors), so freeing and re-measuring inside the handler would see a card
        # still full of the dead attempt's spike. Post-offload free VRAM sizes the
        # 2D tiling so the retry survives a spike that never fit untiled.
        self._free_for_decode_retry(device)
        return self._decode_once(latents, vram_free_gb=free_vram_gb(device))

    def _free_for_decode_retry(self, device: str) -> None:
        """Free everything but our VAE before a (re)tried decode: offload our DiT
        + TE, evict foreign residents, empty the cache. Shared by the proactive
        and OOM-retry tiled-decode paths (mirrors ComfyUI's free-before-VAE)."""
        self.dit.offload()
        self._maybe_offload_te()
        get_residency_manager().offload_all(device, exclude=self._own_models())
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

    def _causal3d_decode_fits(self, latents: torch.Tensor, device: str) -> bool:
        """True if the untiled causal-3D decode (VAE weights we still need to
        move + the fp32 spike) fits live free VRAM. ``True`` on CPU / when VRAM
        can't be queried (never proactively tile there)."""
        free = free_vram_gb(device)
        if free is None:
            return True
        h, w = latents.shape[-2], latents.shape[-1]
        spike = activation_headroom_gb(
            (h, w), decode_mb_per_latent_px=_CAUSAL3D_DECODE_MB_PER_LATENT_PX,
            latent_frames=_latent_frames(latents.shape),
        )
        vae_gb = 0.0 if self._resident("vae") else float(self.vae.estimated_vram_gb or 0.0)
        return (spike + vae_gb) <= free

    def _decode_causal3d_tiled(self, latents: torch.Tensor, device: str) -> np.ndarray:
        """Spatially tiled causal-3D decode with a shrink-on-OOM tile loop.

        Denormalizes the latent (wan21 per-channel), then decodes H/W-tiled with
        :func:`tiled_decode_causal3d` (temporal axis whole per tile). Starts near
        half the long latent axis and halves the tile on each OOM (snapping to a
        multiple of 8, flooring at ``_MIN_DECODE_TILE_LATENT``); a persisting OOM
        at the floor is a genuine capacity limit and propagates.
        """
        self.vae.move_to(device)
        latents = latents.to(device=device, dtype=self.vae.compute_dtype)
        if latents.ndim == 4:
            latents = latents.unsqueeze(2)                 # (B,C,H,W) -> (B,C,1,H,W)

        if self._is_self_normalizing_vae():
            # SeedVR2 inverts its own scaling in decode — feed the latent as-is.
            denorm = latents
        else:
            from .vae.causal_3d import LATENTS_MEAN, LATENTS_STD

            lf = self.spec.latent_format
            mean = torch.tensor(lf.get("latents_mean", LATENTS_MEAN), device=device, dtype=latents.dtype)
            std = torch.tensor(lf.get("latents_std", LATENTS_STD), device=device, dtype=latents.dtype)
            view = (1, -1, 1, 1, 1)
            denorm = latents * std.view(view) + mean.view(view)

        h, w = denorm.shape[-2], denorm.shape[-1]
        tile = max(_MIN_DECODE_TILE_LATENT, ((max(h, w) + 1) // 2 // 8) * 8)  # ~half long axis, snap
        # Cap the first guess by live free VRAM: a tile window of (t + overlap)²
        # latent px spikes ~_CAUSAL3D_DECODE_MB_PER_LATENT_PX each, so solve for t
        # instead of discovering the ceiling via an OOM-and-halve retry (which
        # costs a wasted decode attempt and cache flush).
        free = free_vram_gb(device)
        if free is not None:
            budget_px = free * _DECODE_TILE_VRAM_FRACTION * 1024.0 / _CAUSAL3D_DECODE_MB_PER_LATENT_PX
            # overlap below is ~tile/8, so the processed window is ~1.125*t wide.
            cap = int((budget_px ** 0.5) / 1.125) // 8 * 8
            tile = max(_MIN_DECODE_TILE_LATENT, min(tile, cap))
        while True:
            overlap = min(tile // 2, max(8, tile // 8))
            try:
                logger.debug("decode: causal-3D tiled decode, tile=%d overlap=%d (latent px)", tile, overlap)
                pixels = tiled_decode_causal3d(self.vae.module, denorm, tile_size=tile, overlap=overlap)
                break
            except torch.cuda.OutOfMemoryError:
                if tile <= _MIN_DECODE_TILE_LATENT:
                    raise
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
                tile = max(_MIN_DECODE_TILE_LATENT, (tile // 2 // 8) * 8)
                logger.warning("decode: tiled decode OOM — shrinking tile to %d latent px", tile)

        if not self._resident("vae"):
            self.vae.offload()
        return self._to_uint8(pixels)

    def _causal3d_chunk_frames(self, latents: torch.Tensor, device: str) -> int | None:
        """Max latent frames per TEMPORAL chunk whose full-spatial decode fits live
        free VRAM, or ``None`` when temporal chunking doesn't apply.

        Temporal chunking (:func:`chunked_decode_causal3d`) bounds decode VRAM by
        clip length instead of decoding the whole temporal axis at once. It applies
        only to the wan v1/v2 feat_cache VAEs (SeedVR2 self-normalizes and exposes
        no ``new_feat_cache``), only to multi-frame latents, and only when a SINGLE
        latent frame's spatial decode fits — if even one frame needs spatial tiling
        the two axes don't compose (see ``chunked_decode_causal3d``) and the caller
        spatial-tiles the whole clip instead. ``None`` in all those cases (and on
        CPU / unqueryable VRAM), so the decode path stays byte-identical wherever
        chunking wouldn't engage.

        The frame budget reuses the shared decode-spike model
        (:func:`activation_headroom_gb`): the marginal per-frame spike and the fixed
        base overhead are read from it directly (as a difference), and the affordable
        frame count is ``(free*fraction - vae_weights - base) / per_frame`` under the
        same ``_DECODE_TILE_VRAM_FRACTION`` margin the spatial tiler uses.

        Thin wrapper: the sizing math itself is the shared
        ``vae.tiling.causal3d_chunk_frames`` primitive (also called directly by
        the Wan/LTX generator pipes' own decode path, which have no
        ``NativeGenerator`` instance to ask for residency/VRAM state) — this
        method's only job is supplying OUR live-queried state to it.
        """
        vae_gb = 0.0 if self._resident("vae") else float(self.vae.estimated_vram_gb or 0.0)
        return causal3d_chunk_frames(
            self.vae.module, latents,
            free_vram_gb_value=free_vram_gb(device),
            vae_resident_gb=vae_gb,
            is_self_normalizing=self._is_self_normalizing_vae(),
            decode_mb_per_latent_px=_CAUSAL3D_DECODE_MB_PER_LATENT_PX,
            vram_fraction=_DECODE_TILE_VRAM_FRACTION,
        )

    def _decode_causal3d_chunked(self, latents: torch.Tensor, device: str, chunk_frames: int) -> np.ndarray:
        """Temporal-chunked causal-3D decode (wan v1/v2) with a shrink-on-OOM loop.

        Bounds decode VRAM by clip length; the shared ``feat_cache`` makes it
        mathematically identical to a single whole-clip decode (see
        :func:`chunked_decode_causal3d`). Each chunk decodes its FULL spatial
        extent — the temporal and spatial bounding axes don't compose, so this path
        is chosen only when one frame's spatial spike already fits
        (:meth:`_causal3d_chunk_frames`).

        On OOM (an optimistic frame budget) the WHOLE clip is re-decoded with a
        halved chunk from a FRESH ``feat_cache``. Restarting rather than resuming
        avoids the partial-cache-advance corruption a mid-clip retry would hit — a
        failed ``decode`` may have advanced the cache through some frames, and
        re-feeding those frames would double-count causal state — and needs no cache
        snapshot (so no extra VRAM). Correctness holds because chunking is exact for
        ANY chunk size; the cost is re-decoding the frames before the OOM, rare
        given the conservative budget.
        """
        self.vae.move_to(device)
        latents = latents.to(device=device, dtype=self.vae.compute_dtype)
        if latents.ndim == 4:
            latents = latents.unsqueeze(2)                 # (B,C,H,W) -> (B,C,1,H,W)
        # wan21 per-channel denormalize ONCE on the full latent. It is per-channel,
        # so it commutes with the primitive's temporal slicing — every chunk sees
        # consistently denormalized input. Self-normalizing VAEs never reach here
        # (gated out in _causal3d_chunk_frames), so the wan21 transform is always right.
        from .vae.causal_3d import LATENTS_MEAN, LATENTS_STD

        lf = self.spec.latent_format
        mean = torch.tensor(lf.get("latents_mean", LATENTS_MEAN), device=device, dtype=latents.dtype)
        std = torch.tensor(lf.get("latents_std", LATENTS_STD), device=device, dtype=latents.dtype)
        view = (1, -1, 1, 1, 1)
        denorm = latents * std.view(view) + mean.view(view)

        chunk = max(1, int(chunk_frames))
        while True:
            try:
                logger.debug(
                    "decode: causal-3D temporal chunk, %d latent frame(s)/chunk (T=%d)",
                    chunk, denorm.shape[2],
                )
                pixels = chunked_decode_causal3d(
                    self.vae.module, denorm, chunk_latent_frames=chunk, accumulate_device="cpu",
                )
                break
            except torch.cuda.OutOfMemoryError:
                if chunk <= 1:
                    raise
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
                chunk = max(1, chunk // 2)
                logger.warning("decode: temporal-chunked decode OOM — shrinking to %d frame(s)/chunk", chunk)

        if not self._resident("vae"):
            self.vae.offload()
        return self._to_uint8(pixels)

    def _free_own_dit_for_decode(self, need_gb: float, device: str) -> None:
        """Offload our OWN DiT before decode when the spike won't co-fit with it.

        Ties the own-DiT offload to the ACTUAL decode requirement rather than only
        the placement ``resident`` flag, so a high-resolution decode reliably frees
        the DiT's VRAM. No-op on CPU or when the DiT is already off the GPU.
        """
        if not str(device).startswith("cuda") or str(getattr(self.dit, "device", "")) == "cpu":
            return
        free = free_vram_gb(device)
        if free is not None and free < need_gb:
            logger.debug(
                "decode: offloading own DiT — spike ~%.1fGB won't fit in %.1fGB free", need_gb, free,
            )
            self.dit.offload()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()

    def _decode_need_gb(self, latents: torch.Tensor) -> float:
        """Estimated VRAM the decode needs: VAE weights + the fp32 decode spike.

        The spike scales with latent resolution (``activation_headroom_gb``); the
        causal-3D (Qwen/Wan) fp32 3D-conv decode spikes ~2x the 2D AE per pixel,
        matching the per-px constant used by ``_build_placement``.
        """
        h, w = latents.shape[-2], latents.shape[-1]
        decode_mb = 1.2 if self._is_causal3d_vae() else 0.6
        spike = activation_headroom_gb(
            (h, w), decode_mb_per_latent_px=decode_mb, latent_frames=_latent_frames(latents.shape)
        )
        return (self.vae.estimated_vram_gb or 0.0) + spike

    def _decode_once(self, latents: torch.Tensor, *, vram_free_gb: float | None) -> np.ndarray:
        device = self.device_plan.vae_device
        self.vae.move_to(device)
        latents = latents.to(device=device, dtype=self.vae.compute_dtype)

        if self._is_causal3d_vae():
            pixels = self._decode_causal3d(self.vae.module, latents, device)
        else:
            pixels = self._decode_2d(self.vae.module, latents, vram_free_gb)

        if not self._resident("vae"):
            self.vae.offload()
        return self._to_uint8(pixels)

    def _decode_2d(self, module: AutoEncoder2D, latents: torch.Tensor, vram_free_gb) -> torch.Tensor:
        # Flux 2D AE: scalar latent-format scale/shift, then (optionally tiled) decode.
        lf = self.spec.latent_format
        vae_latent = latents / lf.get("scale_factor", 1.0) + lf.get("shift_factor", 0.0)
        tile = auto_tile_size(vram_free_gb, (vae_latent.shape[-2], vae_latent.shape[-1]))
        if tile is not None:
            return tiled_decode(module, vae_latent, tile_size=tile)
        return module.decode(vae_latent)

    def _decode_causal3d(self, module, latents: torch.Tensor, device) -> torch.Tensor:
        # Self-normalizing VAE (SeedVR2): the module inverts its own scaling in
        # decode — pass the latent straight through, no wan21 mean/std.
        if self._is_self_normalizing_vae():
            if latents.ndim == 5:
                latents = latents.squeeze(2)           # (B,C,1,H,W) -> (B,C,H,W)
            return module.decode_image(latents)        # (B,3,H*8,W*8) in [-1,1]

        # Wan21 latent format: per-channel denormalize (latent * std + mean), then
        # decode. The 3D VAE takes (B,C,T,H,W); a still image is T=1, so we use
        # decode_image on the squeezed 4D latent.
        from .vae.causal_3d import LATENTS_MEAN, LATENTS_STD

        lf = self.spec.latent_format
        mean = torch.tensor(lf.get("latents_mean", LATENTS_MEAN), device=device, dtype=latents.dtype)
        std = torch.tensor(lf.get("latents_std", LATENTS_STD), device=device, dtype=latents.dtype)
        if latents.ndim == 5:
            latents = latents.squeeze(2)               # (B,C,1,H,W) -> (B,C,H,W)
        view = (1, -1, 1, 1)
        denorm = latents * std.view(view) + mean.view(view)
        return module.decode_image(denorm)             # (B,3,H*8,W*8) in [-1,1]

    def release_gpu(self) -> None:
        """Release this generation's GPU VRAM: offload our DiT/VAE to CPU, offload
        the TE, empty the cache.

        Called on the generator **error path** (see ``BaseGeneratorPipe``) so a
        FAILED generation doesn't leave the DiT (~26GB for Krea-2 at high res)
        resident — the reported symptom was the app holding ~30GB after a decode
        OOM. Weights survive on CPU (the ``NativeModel``s are cached by
        ``ModelLifecycleManager`` and moved back on the next generation); only
        VRAM is freed. Best-effort and never raises — a cleanup that raised would
        mask the original generation failure.
        """
        for name, model in (("dit", self.dit), ("vae", self.vae)):
            try:
                model.offload()
            except Exception:  # pragma: no cover - best-effort cleanup
                logger.debug("release_gpu: %s offload failed", name, exc_info=True)
        try:
            self._maybe_offload_te()
        except Exception:  # pragma: no cover - best-effort cleanup
            logger.debug("release_gpu: te offload failed", exc_info=True)
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

    # -- encode (img2img) --------------------------------------------------

    @torch.no_grad()
    def encode_image(
        self, image: "np.ndarray | torch.Tensor", *, vram_free_gb: float | None = None
    ) -> torch.Tensor:
        """Encode a pixel image to a model-native latent — the inverse of :meth:`decode`.

        Runs under ``torch.no_grad``: this is called OUTSIDE the sampler's
        own ``@torch.no_grad`` guard (directly from the edit pipes), so without it
        the returned latent keeps a ``grad_fn`` pinning the WHOLE encoder activation
        graph on-device. The edit pipes hold that latent as ``ref_latents`` for the
        entire sampling run, so at 2MP the retained graph was ~16.8GB of live CUDA
        tensors co-tenant with the DiT — the reported edit-mode OOM.

        This is the img2img entry point: the returned latent has the shape
        :meth:`latent_shape_for` produces (5D ``(B,C,1,H/8,W/8)`` for the causal-3D
        VAE, 4D ``(B,C,H,W)`` for the Flux 2D AE), so it drops straight into
        ``sample(init_latent=...)``.

        ``image`` is either a uint8 HWC array (``(H,W,3)`` or ``(B,H,W,3)``) exactly
        as :meth:`decode` returns, or a float ``(B,3,H,W)``/``(3,H,W)`` tensor
        already in ``[-1, 1]``. The latent-format normalization matches ``decode``'s
        inverse: 2D applies ``(vae.encode(px) - shift) * scale``; causal-3D applies
        ``(vae.encode_image(px) - mean) / std``. The VAE is offloaded afterwards (it
        is dead weight through sampling; ``sample``/``decode`` move it back).

        Placement-aware: the encode's transient conv peak (a
        ~10GB alloc at 2MP for the causal-3D VAE) runs from ``generate_one``
        BEFORE this generation's DiT placement, so a prior run's kept-resident DiT
        (the round-2 warm-reuse policy) is still parked in VRAM. ``_ensure_room_for_encode``
        offloads it up front ONLY when the encode won't otherwise fit (small edits
        / txt2img keep the DiT warm); an OOM anyway triggers the same free-all-but-
        VAE + retry backstop ``decode`` uses.
        """
        device = self.device_plan.vae_device
        self.vae.move_to(device)
        pixels = self._image_to_pixels(image).to(device=device, dtype=self.vae.compute_dtype)
        self._ensure_room_for_encode(pixels, device)
        try:
            latent = self._encode_dispatch(pixels, device, vram_free_gb)
        except torch.cuda.OutOfMemoryError:
            logger.warning(
                "ref-image encode OOM'd; freeing our DiT/TE + evicting foreign residents and retrying"
            )
            self._free_for_decode_retry(device)
            latent = self._encode_dispatch(pixels, device, vram_free_gb)
        # VAE is dead weight until decode; free VRAM for the DiT sampling phase.
        if str(device).startswith("cuda"):
            self.vae.offload()
        return latent

    def _encode_dispatch(self, pixels: torch.Tensor, device, vram_free_gb) -> torch.Tensor:
        if self._is_causal3d_vae():
            return self._encode_causal3d(self.vae.module, pixels, device)
        return self._encode_2d(self.vae.module, pixels, vram_free_gb)

    def _encode_need_gb(self, pixels: torch.Tensor) -> float:
        """Estimated VRAM the ref-image encode needs: VAE weights (if not already
        resident) + the transient conv-activation peak, scaled from input pixels.

        Mirrors ``_decode_need_gb``'s shape with the encode-calibrated per-latent-
        px coefficient (``_CAUSAL3D_ENCODE_MB_PER_LATENT_PX``; 2D falls back to the
        tiering default via ``activation_headroom_gb``)."""
        h, w = pixels.shape[-2], pixels.shape[-1]
        latent_hw = (h // _VAE_SPATIAL_DOWNSCALE, w // _VAE_SPATIAL_DOWNSCALE)
        if self._is_causal3d_vae():
            spike = activation_headroom_gb(
                latent_hw, decode_mb_per_latent_px=_CAUSAL3D_ENCODE_MB_PER_LATENT_PX
            )
        else:
            spike = activation_headroom_gb(latent_hw)
        vae_gb = 0.0 if self._resident("vae") else float(self.vae.estimated_vram_gb or 0.0)
        return spike + vae_gb

    def _ensure_room_for_encode(self, pixels: torch.Tensor, device) -> None:
        """Free VRAM for the ref-image encode's transient peak BEFORE it runs.

        Foreign residents first (``_ensure_room_for``, never our own VAE/DiT).
        If the encode still won't fit live free VRAM, our OWN DiT is the room: a
        prior generation's kept-resident DiT (warm-reuse) parked
        here is what the encode OOMs against, and the failed run's post-generation
        ``models.cleanup`` (``empty_cache``) is exactly what freed just enough for
        the NEXT run — the reported every-second-run alternation. Offload it (and
        empty the fragmentation the alternation rode on); ``sample`` re-places the
        DiT at step 0 regardless. No-op on CPU / when the encode already fits, so
        small edits and txt2img (no ref encode) keep the DiT warm."""
        if not str(device).startswith("cuda"):
            return
        need = self._encode_need_gb(pixels)
        self._ensure_room_for(need, device)
        free = free_vram_gb(device)
        if free is None or free >= need:
            return
        if str(getattr(self.dit, "device", "cpu")).startswith("cuda") or self.dit.is_streaming:
            logger.debug(
                "ref-image encode needs ~%.1fGB but only %.1fGB free; offloading the parked DiT first",
                need, free,
            )
            self.dit.offload()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()

    def _encode_2d(self, module: AutoEncoder2D, pixels: torch.Tensor, vram_free_gb) -> torch.Tensor:
        # Flux 2D AE: (optionally tiled) encode, then the inverse of _decode_2d's
        # scalar latent-format transform.
        lf = self.spec.latent_format
        latent_hw = (pixels.shape[-2] // _VAE_SPATIAL_DOWNSCALE, pixels.shape[-1] // _VAE_SPATIAL_DOWNSCALE)
        tile = auto_tile_size(vram_free_gb, latent_hw)
        if tile is not None:
            vae_latent = tiled_encode(module, pixels, tile_size=tile * _VAE_SPATIAL_DOWNSCALE)
        else:
            vae_latent = module.encode(pixels)
        return (vae_latent - lf.get("shift_factor", 0.0)) * lf.get("scale_factor", 1.0)

    def _encode_causal3d(self, module, pixels: torch.Tensor, device) -> torch.Tensor:
        # Self-normalizing VAE (SeedVR2): the module applies its own scaling in
        # encode — no wan21 per-channel normalize; just lift to 5D (B,C,1,H,W).
        if self._is_self_normalizing_vae():
            return module.encode_image(pixels).unsqueeze(2)

        # Wan21 latent format: encode then per-channel normalize (the inverse of
        # _decode_causal3d), returning a 5D (B,C,1,H,W) latent to match
        # latent_shape_for so it aligns with sample()'s seed_noise.
        from .vae.causal_3d import LATENTS_MEAN, LATENTS_STD

        lf = self.spec.latent_format
        mean = torch.tensor(lf.get("latents_mean", LATENTS_MEAN), device=device, dtype=pixels.dtype)
        std = torch.tensor(lf.get("latents_std", LATENTS_STD), device=device, dtype=pixels.dtype)
        raw = module.encode_image(pixels)              # (B,16,H/8,W/8) in wan21 z-space
        view = (1, -1, 1, 1)
        norm = (raw - mean.view(view)) / std.view(view)
        return norm.unsqueeze(2)                       # (B,C,1,H,W)

    @staticmethod
    def _image_to_pixels(image: "np.ndarray | torch.Tensor") -> torch.Tensor:
        """Normalize an input image to a ``(B, 3, H, W)`` float tensor in ``[-1, 1]``.

        Accepts a uint8 HWC array (the shape :meth:`decode` emits) or a tensor
        already in ``[-1, 1]``.
        """
        if isinstance(image, np.ndarray):
            arr = image[None] if image.ndim == 3 else image     # HWC -> BHWC
            t = torch.from_numpy(np.ascontiguousarray(arr)).float()
            t = t.permute(0, 3, 1, 2).contiguous()              # BHWC -> BCHW
            return t / 127.5 - 1.0
        t = image if image.ndim == 4 else image.unsqueeze(0)
        return t

    # -- adapters ----------------------------------------------------------

    def _make_forward(self, device, dtype):
        """Build the sampler-side ``model_forward(x, sigma, conditioning)``.

        sigma -> timestep is identity for flow-matching Flux (see module docstring).
        """
        module = self.dit.module

        def model_forward(x: torch.Tensor, sigma: torch.Tensor, conditioning: dict) -> torch.Tensor:
            context = conditioning["context"]
            # Flux1 pooled vector -> y; Klein has none. Accept either key name so
            # a raw TE dict ("pooled") or a pipe-built dict ("y") both work.
            y = conditioning.get("y")
            if y is None:
                y = conditioning.get("pooled")
            guidance = conditioning.get("guidance")           # injected by EmbeddedGuidance
            attention_mask = conditioning.get("attention_mask")  # Klein padding mask; None for Flux1
            # FBCache step cache (denoise() injects it per guidance branch); only
            # forwarded when present so archs without the kwarg stay unaffected.
            extra = {}
            step_cache = conditioning.get("step_cache")
            if step_cache is not None:
                extra["step_cache"] = step_cache
            # Krea-2 Identity Edit: a plugin-built conditioning dict may
            # carry pre-fit, pre-normalized source latent(s) for the in-context
            # edit sequence. Same conditional-forwarding idiom as step_cache --
            # only Krea2.forward declares the kwarg, every other arch is
            # unaffected when it's absent.
            ref_latents = conditioning.get("ref_latents")
            if ref_latents is not None:
                extra["ref_latents"] = ref_latents
            # Krea-2 edit ref_boost: scalar reference-fidelity dials the
            # krea2-edit pipe puts in the cond dict. Same conditional-forwarding
            # idiom -- only Krea2.forward declares these kwargs (defaults 1.0 =
            # no bias), every other arch is unaffected when they're absent.
            # ref_boost_mask rides the same seam: an optional region
            # tensor, already moved to device/dtype by _move_cond like any
            # other cond value.
            for _boost_key in ("ref_boost", "ref_boost_a", "ref_boost_mask"):
                _boost = conditioning.get(_boost_key)
                if _boost is not None:
                    extra[_boost_key] = _boost
            # Krea-2 NAG: the cond dict may carry the negative prompt's
            # raw TE hidden states + NAG params (attached by the generator pipe
            # when nag_scale > 1.0 -- see FlowMatchGeneratorPipe.generate_one).
            # Same conditional-forwarding idiom as ref_boost -- only Krea2.forward
            # declares these kwargs, every other arch is unaffected when absent.
            nag_context = conditioning.get("nag_context")
            if nag_context is not None:
                extra["nag_context"] = nag_context
                extra["nag"] = conditioning.get("nag")
                nag_attention_mask = conditioning.get("nag_attention_mask")
                if nag_attention_mask is not None:
                    extra["nag_attention_mask"] = nag_attention_mask
            return module(x, sigma, context, y=y, guidance=guidance, attention_mask=attention_mask, **extra)

        return model_forward

    @staticmethod
    def _unwrap_conditioning(conditioning) -> tuple[dict, dict | None]:
        """Accept a Conditioning, a bare cond dict, or a (cond, uncond) tuple."""
        if isinstance(conditioning, Conditioning):
            return conditioning.cond, conditioning.uncond
        if isinstance(conditioning, tuple):
            cond, uncond = conditioning
            return cond, uncond
        if isinstance(conditioning, dict):
            return conditioning, None
        raise NativeEngineUnsupportedError(
            f"conditioning must be Conditioning | dict | (cond, uncond); got {type(conditioning).__name__}"
        )

    def _move_cond(self, cond: dict, device, dtype) -> dict:
        def move(v):
            return v.to(device=device, dtype=dtype) if torch.is_floating_point(v) else v.to(device=device)

        out: dict = {}
        for k, v in cond.items():
            if v is None:
                continue
            # ref_latents (and any future multi-image conditioning) is a list of
            # tensors by wire contract; non-tensor scalars pass through as-is.
            if isinstance(v, (list, tuple)):
                out[k] = type(v)(move(t) if torch.is_tensor(t) else t for t in v)
            elif torch.is_tensor(v):
                out[k] = move(v)
            else:
                out[k] = v
        return out

    def _image_seq_len(self, latents_shape) -> int:
        """Packed image-token count (drives Flux1's dynamic-mu schedule).

        Uses the trailing two dims so it works for both 4D (flux) and 5D
        (qwen/wan ``B,C,T,H,W``) latent shapes.
        """
        h, w = latents_shape[-2], latents_shape[-1]
        patch = self.dit.module.patch_size
        h_len = (h + (patch // 2)) // patch
        w_len = (w + (patch // 2)) // patch
        return h_len * w_len

    @staticmethod
    def _to_uint8(pixels: torch.Tensor) -> np.ndarray:
        if pixels.ndim == 5:
            # (B, C, T, H, W) still image -> take the single frame.
            pixels = pixels[:, :, 0]
        pixels = pixels.detach().float().clamp(-1.0, 1.0)
        pixels = (pixels + 1.0) * 127.5
        pixels = pixels.round().to(torch.uint8)
        # (B, C, H, W) -> (B, H, W, C)
        pixels = pixels.permute(0, 2, 3, 1).contiguous()
        return pixels.cpu().numpy()
