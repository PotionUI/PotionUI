"""Text-to-video generator for the native Wan 2.1 / 2.2 family.

Consumes the bundle from ``model_loader/wan22`` and the conditioning list from the
shared ``prompt_encoder`` pipe, and runs the flow-matching sampler directly
(``NativeGenerator`` is image-only): 5D latents, an expert-router ``model_forward``
that switches between the high/low-noise experts at the sampling boundary, true
CFG (Wan's guidance mode), causal-3D VAE decode, then ``encode_frames_to_mp4`` +
``emit_gallery(videos=)``.

Naming: the plan calls this ``generator/txt2vid/wan22`` but pipe discovery is
two-level (``<pipe>/<variant>``), so the registered name is
``generator/txt2vid_wan22`` (mode + model encoded in the single variant segment).
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from typing import Any, Dict, List

import numpy as np
import torch

from src.platform.runtime.native.errors import DecodeNumericsError
from src.platform.runtime.native.memory.residency import free_vram_gb, get_residency_registry
from src.platform.runtime.native.optimizations.compile import maybe_compile_dit
from src.platform.runtime.native.memory.tiering import sampling_headroom_gb
from src.platform.runtime.native.resolution import snap_frame_count, snap_resolution
from src.platform.runtime.native.sampling import ProgressHook, denoise, make_preview_hook
from src.platform.runtime.native.sampling.flow_schedule import build_sigmas
from src.platform.runtime.native.vae.causal_3d import LATENTS_MEAN, LATENTS_STD
from src.platform.runtime.native.vae.tiling import causal3d_chunk_frames, chunked_decode_causal3d
from src.pipelines.contracts import logger
from src.pipelines.contracts import IOType, PipeInput, PipeInputSpec, PipeOutputSpec, PipeConfigSpec
from src.pipelines.outputs import Icon
from src.pipelines.pipes._shared.generation.dit_placement import _dit_lora_delta_gb, _move_resident
from src.pipelines.pipes._shared.generation.generator_base import BaseGeneratorPipe, GeneratorContext, emit_gallery
from src.pipelines.pipes._shared.generation.freeinit import freeinit_blend, freeinit_config_specs, resolve_freeinit
from src.pipelines.pipes._shared.generation.guidance_options import (
    apg_settings_config_specs,
    apg_settings_overrides,
    build_riflex,
    riflex_config_specs,
    sampler_step_cache_config_specs,
    sampler_step_cache_kwargs,
    schedule_settings_config_specs,
    schedule_settings_overrides,
    slg_settings_config_specs,
    slg_settings_overrides,
)
from src.pipelines.pipes._shared.generation.progress import ProgressEmitter
from src.pipelines.pipes._shared.media.pixel_convert import pixels_3thw_to_uint8_frames
from src.pipelines.pipes._shared.media.video_encode import encode_frames_to_mp4

# Wan's causal VAE downsamples time by 4 (1 + 4*(n-1) frame chunking).
_TEMPORAL_DOWNSCALE = 4


_warned_snapped_resolution: set[tuple[int, int]] = set()
_warned_snapped_frames: set[int] = set()


def _snap_geometry(bundle, lf: dict, width: int, height: int, frames: int,
                   *, patch_size=None) -> tuple[int, int, int]:
    """Snap a Wan request to the model's spatial + temporal granularity.

    Spatial: ``spatial_downscale * DiT-spatial-patch`` (16px for Wan21, 32px for
    Wan22-5B). Temporal: the causal VAE chunks frames as ``1 + 4k``. Warns (once
    each) when the request was changed. Shared by the txt2vid and img2vid pipes.
    """
    spatial_downscale = int(lf.get("spatial_downscale", 8))
    patch = patch_size if patch_size is not None else bundle.high_dit.module.patch_size
    spatial_patch = int(patch[-1] if isinstance(patch, (tuple, list)) else patch)
    snapped_w, snapped_h = snap_resolution(width, height, spatial_downscale, spatial_patch)
    snapped_frames = snap_frame_count(frames, _TEMPORAL_DOWNSCALE)
    if (snapped_w, snapped_h) != (width, height) and (width, height) not in _warned_snapped_resolution:
        _warned_snapped_resolution.add((width, height))
        logger.warning("[GENERATOR WAN] snapped resolution %dx%d -> %dx%d (granularity %dpx)",
                       width, height, snapped_w, snapped_h, spatial_downscale * spatial_patch)
    if snapped_frames != frames and frames not in _warned_snapped_frames:
        _warned_snapped_frames.add(frames)
        logger.warning("[GENERATOR WAN] snapped frames %d -> %d (1 + %d*k)",
                       frames, snapped_frames, _TEMPORAL_DOWNSCALE)
    return snapped_w, snapped_h, snapped_frames
# Wan timestep == flow sigma * 1000 (ModelSamplingDiscreteFlow multiplier); the
# DiT's sinusoidal_embedding_1d has no internal factor, unlike Flux.
_TIMESTEP_SCALE = 1000.0


class _ExpertRouter:
    """model_forward that dispatches to the high/low-noise expert by sigma.

    Wan 2.2 14B ships two experts; the high-noise one runs while the flow sigma
    is above ``boundary`` (timestep > boundary*1000), the low-noise one below.
    Single-DiT Wan (2.1 / 5B) has ``low=None`` and always uses ``high``. Experts
    are moved on-device on transition and the previous one offloaded, so at most
    one 14B expert is resident at a time.

    Placement: a Wan 2.2 14B expert is 14-27GB, and the 720p/5s video
    activation working set is tens of GB (``sampling_headroom_gb`` scales with
    the full T*H*W token count) -- the two do NOT co-fit a 32GB card, which is
    why a plain ``move_to`` full-pin OOMs where ComfyUI's block-swap fits. When
    ``latents_shape`` is set, the router places each expert with PARTIAL
    residency (stream the DiT from pinned CPU RAM, keep only what fits the
    activation-aware weights budget resident) -- the identical fit logic
    ``NativeGenerator._stream_dit_to_gpu`` uses for the image path. ``None``
    (unset) keeps the pre-partial-residency full ``move_to`` (single-DiT / low-res / callers
    not yet wired), so this is additive.
    """

    def __init__(self, high, low, boundary: float, device: str, riflex: dict | None = None,
                 latents_shape: tuple | None = None) -> None:
        self.high = high
        self.low = low
        self.boundary = boundary
        self.device = device
        self.active = None
        # 5D ``(1, C, T, H, W)`` latent shape the experts sample, set by the
        # generator's generate_one before denoise. Drives the partial-residency
        # weights budget; ``None`` -> full ``move_to`` (pre-partial-residency behavior).
        self.latents_shape = latents_shape
        # RIFLEx (roadmap 3.8): None (the default) means the ``riflex=`` kwarg
        # is never even passed to ``dit.module(...)`` below — byte-identical
        # to the pre-RIFLEx call shape. Fixed per generation (built once from
        # pipe config), unlike ``skip_layers`` which rides the per-step
        # conditioning dict.
        self.riflex = riflex

    def _select(self, sigma_val: float):
        if self.low is None or sigma_val > self.boundary:
            return self.high
        return self.low

    def _own_experts(self) -> list:
        """Our own experts -- never evicted to make room for ourselves."""
        return [e for e in (self.high, self.low) if e is not None]

    def _weights_budget_gb(self, dit) -> float:
        """VRAM (GB) for RESIDENT expert weights under partial residency: live
        free VRAM (after evicting FOREIGN residents, e.g. a prior image gen's
        DiT) minus the video activation headroom. Mirrors
        ``NativeGenerator._dit_weights_budget_gb`` -- streamed leaves only transit
        VRAM one at a time per forward, so they don't count here."""
        need = float(getattr(dit, "estimated_vram_gb", None) or 0.0) + _dit_lora_delta_gb(dit)
        free = free_vram_gb(self.device)
        if free is None:
            return need
        get_residency_registry().ensure_free(self.device, need, free, exclude=self._own_experts())
        free = free_vram_gb(self.device) or 0.0
        shape = self.latents_shape
        frames = shape[2] if len(shape) == 5 else 1
        headroom = sampling_headroom_gb((shape[-2], shape[-1]), latent_frames=frames)
        return max(0.0, free - headroom)

    def _place_expert(self, dit) -> None:
        """Bring ``dit`` on-device for its sampling phase, offloading the
        previously-active expert first. Full ``move_to`` when the expert fits the
        activation-aware weights budget (or ``latents_shape`` is unset / CPU);
        otherwise PARTIAL residency (``stream_to``) with the same zero-budget
        eviction backstop ``_stream_dit_to_gpu`` uses. See the class docstring.

        A resident placement is also offered to ``maybe_compile_dit`` -- the
        same gated, reversible regional ``torch.compile`` the image path gets
        from ``NativeGenerator._maybe_compile``, which this router (no
        ``NativeGenerator`` instance to call that private method on) has no
        other way to reach. Each expert compiles independently and only once
        per resident placement (the handle lives on the expert's own
        ``NativeModel``, so re-selecting an already-compiled expert is a
        no-op); streamed placements are never offered."""
        if self.active is not None:
            self.active.offload()
        self.active = dit
        if self.latents_shape is None or not str(self.device).startswith("cuda"):
            dit.move_to(self.device)
            maybe_compile_dit(dit, resident=True, is_cuda=str(self.device).startswith("cuda"))
            return
        budget = self._weights_budget_gb(dit)
        need = float(getattr(dit, "estimated_vram_gb", None) or 0.0) + _dit_lora_delta_gb(dit)
        if need and need <= budget:
            mode = _move_resident(dit, self.device, self._own_experts())
            if mode == "resident":
                maybe_compile_dit(dit, resident=True, is_cuda=True)
            return
        try:
            dit.stream_to(self.device, budget)
        except torch.cuda.OutOfMemoryError:
            logger.warning("[GENERATOR WAN] partial-residency expert placement OOM'd "
                           "(budget %.1fGB); evicting foreign residents and streaming fully", budget)
            dit.offload()
            get_residency_registry().offload_all(self.device, exclude=self._own_experts())
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            dit.stream_to(self.device, 0.0)

    def __call__(self, x: torch.Tensor, sigma: torch.Tensor, conditioning: dict) -> torch.Tensor:
        dit = self._select(float(sigma.reshape(-1)[0]))
        if dit is not self.active:
            self._place_expert(dit)
        # NAG (see src/platform/runtime/native/nag.py): the negative context + params ride in
        # the cond dict under reserved keys (EmbeddedGuidance's "guidance" idiom).
        # Both experts share this cond dict, so NAG applies to whichever one runs.
        extra = {}
        nag_context = conditioning.get("nag_context")
        if nag_context is not None:
            extra["nag_context"] = nag_context
            extra["nag"] = conditioning.get("nag")
        # SLG (see src/platform/runtime/native/sampling/cfg.py::SkipLayerGuidance): the
        # degraded-pass conditioning dict carries a reserved "skip_layers" key
        # (the SAME "inject into the conditioning dict" seam NAG/EmbeddedGuidance
        # already use) -- read it here and route it to the arch's own kwarg. We
        # only ever .get() it: SkipLayerGuidance always spreads a FRESH
        # {**cond, "skip_layers": ...} dict for its degraded pass and never
        # mutates/reuses `cond` itself, so there is nothing to pop/clear here --
        # the persistent cond/uncond dicts this router sees on every other call
        # never carry the key. Absent on every normal-pass call, so this is a
        # no-op unless SLG's wrapper is actually active for this step.
        skip_layers = conditioning.get("skip_layers")
        if skip_layers:
            extra["skip_layers"] = skip_layers
        # FBCache (see src/platform/runtime/native/sampling/step_cache.py): same seam --
        # denoise()'s _CachingGuidance injects a reserved "step_cache" key
        # (a FirstBlockCache instance) into a fresh conditioning dict per call
        # the same way; read it and forward it ONLY when present/non-None so a
        # module whose forward doesn't yet accept step_cache (until the arch
        # side lands) never sees the kwarg at all -- never pass
        # step_cache=None explicitly.
        step_cache = conditioning.get("step_cache")
        if step_cache is not None:
            extra["step_cache"] = step_cache
        if self.riflex:
            extra["riflex"] = self.riflex
        return dit.module(x, sigma * _TIMESTEP_SCALE, conditioning["context"], **extra)


def resolve_expert_boundary(spec, config: dict, sampling_settings: dict, steps: int,
                            *, default: float = 0.900, log_tag: str = "GENERATOR WAN") -> float:
    """Resolve the high/low-noise expert switch boundary (a flow sigma in [0,1]).

    Precedence: an explicit ``expert_switch_step`` wins -- converted to that
    step's flow sigma via the SAME ``build_sigmas`` schedule the denoise loop
    builds from ``sampling_settings`` + ``steps``, so it can't drift from the
    run's actual sigmas; then a numeric ``expert_boundary`` override; then the
    model spec's per-family default. Shared by all three Wan generators.
    """
    boundary = spec.sampling_settings.get("expert_boundary", default)

    switch_step = config.get("expert_switch_step")
    if switch_step not in (None, "", "None"):
        try:
            step_idx = int(switch_step)
        except (TypeError, ValueError):
            logger.warning("[%s] ignoring non-integer expert_switch_step %r", log_tag, switch_step)
            step_idx = None
        if step_idx is not None and step_idx > 0:
            sigmas = build_sigmas(
                int(steps),
                shift=sampling_settings.get("shift"),
                base_shift=sampling_settings.get("base_shift"),
                max_shift=sampling_settings.get("max_shift"),
                dynamic_shift=sampling_settings.get("dynamic_shift"),
                schedule=sampling_settings.get("schedule"),
                schedule_options=sampling_settings.get("schedule_options"),
                detail_strength=sampling_settings.get("detail_strength", 0.0),
                detail_start=sampling_settings.get("detail_start", 0.1),
                detail_end=sampling_settings.get("detail_end", 0.9),
            )
            # sigmas descend 1->0 over steps+1 entries; the sigma AT step_idx is
            # the threshold that makes steps 0..step_idx-1 run the high expert and
            # step_idx.. the low one. Clamp past-the-end to the final (0.0) sigma.
            return float(sigmas[min(step_idx, len(sigmas) - 1)])
        # An invalid / non-positive step is not a real switch point -- fall
        # through to the boundary picker rather than forcing the spec default.

    override = config.get("expert_boundary")
    if override not in (None, "", "None"):
        try:
            return float(override)
        except (TypeError, ValueError):
            logger.warning("[%s] ignoring non-numeric expert_boundary %r", log_tag, override)
    return boundary


def _attach_nag(cond: dict, uncond: dict | None, config: dict) -> dict:
    """Attach NAG's negative context + params to the cond dict, read back by
    ``_ExpertRouter`` above (see ``src/platform/runtime/native/nag.py``). No-op (returns
    ``cond`` unchanged) when ``nag_scale <= 1.0`` or there's no negative
    conditioning — keeps the disabled path byte-identical / free of the extra
    negative-attention pass. Shared by both the txt2vid and img2vid generators.
    """
    nag_scale = float(config.get("nag_scale", 1.0))
    if nag_scale <= 1.0 or uncond is None:
        return cond
    return {
        **cond,
        "nag_context": uncond["context"],
        "nag": {
            "scale": nag_scale,
            "tau": float(config.get("nag_tau", 3.5)),
            "alpha": float(config.get("nag_alpha", 0.5)),
        },
    }


def _emit_video_results(
        generation_outputs: callable,
        results: List[str],
        used_seeds: List[int],
        resolution: "tuple[int, int] | None" = None,
) -> None:
    """Shared by every pipe that imports this helper (txt2vid_ltx, video_ltx,
    img2vid_wan22 as well as this file). `resolution` is optional and defaults
    to None so callers that haven't been wired to pass their own (width,
    height) yet keep their prior no-live-dimensions behavior."""
    emit_gallery(generation_outputs, images=[], seeds=used_seeds, videos=results, video_resolution=resolution)


def _build_video_output(results: List[str]) -> Dict[str, Any]:
    return {"video": results}


def _to_device(cond: dict, device, dtype) -> dict:
    return {
        k: (v.to(device=device, dtype=dtype) if torch.is_floating_point(v) else v.to(device=device))
        for k, v in cond.items()
    }


def _decode_video(c: Any, latent: torch.Tensor) -> np.ndarray:
    """Un-normalize the Wan21 latent and decode to (T, H, W, 3) uint8 frames.

    ``c`` is duck-typed on ``.vae``/``.device``/``.latent_channels`` — shared by
    both the txt2vid (``_WanCtx``) and img2vid (``_Ctx``) generation contexts.

    Long clips route through the SAME temporal-chunked decode primitive the
    engine's image-family path uses (:func:`~src.platform.runtime.native.vae.tiling.
    causal3d_chunk_frames` sizes it, :func:`~src.platform.runtime.native.vae.tiling.
    chunked_decode_causal3d` does the actual chunked decode with a carried
    ``feat_cache`` -- mathematically identical to one whole-clip decode, see
    that function's docstring). Whole-clip-fits (or VRAM can't be queried,
    e.g. CPU) is a byte-identical single ``.decode()`` call, unchanged from
    before chunking existed; a clip whose single FRAME doesn't even fit falls
    back to that same single call too (no spatial tiling here -- out of scope,
    matches this pipe's pre-existing OOM behavior).
    """
    c.vae.move_to(c.device)
    z = latent.to(device=c.device, dtype=c.vae.compute_dtype)
    # Wan21 (16ch) latent format un-normalizes per-channel (z*std + mean). The
    # Wan22 (5B, 48ch) format is a no-op BY DESIGN — ComfyUI's Wan22 latent
    # format sets scale_factor=1.0 with NO per-channel mean/std, so the skip is
    # correct; do not add invented 48ch constants here.
    if c.latent_channels == len(LATENTS_MEAN):
        mean = torch.tensor(LATENTS_MEAN, device=z.device, dtype=z.dtype).view(1, -1, 1, 1, 1)
        std = torch.tensor(LATENTS_STD, device=z.device, dtype=z.dtype).view(1, -1, 1, 1, 1)
        z = z * std + mean

    with torch.no_grad():
        chunk_frames = causal3d_chunk_frames(c.vae.module, z, free_vram_gb_value=free_vram_gb(c.device))
        if chunk_frames is not None:
            # accumulate on CPU so a long clip's per-chunk pixels don't pile up on
            # the GPU (+ cat duplicate) and OOM at assembly — the frames go to CPU
            # for encoding anyway.
            pixels = chunked_decode_causal3d(
                c.vae.module, z, chunk_latent_frames=chunk_frames, accumulate_device="cpu",
            )
        else:
            pixels = c.vae.module.decode(z)  # (B, 3, T, H, W) in [-1, 1]
    c.vae.offload()

    # Catch a corrupt decode HERE, before the uint8 cast below silently turns
    # a NaN pixel into a black 0 with no error (torch.clamp leaves NaN
    # unchanged; see DecodeNumericsError). Checked pre-chunking-cast so this
    # covers both the chunked and single-call decode paths above uniformly.
    if not torch.isfinite(pixels).all():
        raise DecodeNumericsError()

    # Chunked conversion: the full-tensor chain allocated several clip-sized
    # fp32 transients on the decode device and OOM'd long high-res clips after
    # a successful decode (see pixel_convert).
    return pixels_3thw_to_uint8_frames(pixels[0])  # (T, H, W, 3)


@dataclass
class _WanCtx:
    router: _ExpertRouter
    vae: Any
    sampling_settings: dict
    conditioning: list
    steps: int
    cfg: float
    sampler: str
    width: int
    height: int
    frames: int
    fps: float
    latent_channels: int
    spatial_downscale: int
    device: str
    dtype: torch.dtype
    spec: Any = None

    def release_gpu(self) -> None:
        """Best-effort GPU cleanup on a failed generation: offload whichever
        expert(s)/VAE may be resident. Picked up by
        `BaseGeneratorPipe._release_gpu_on_error` (this dataclass IS
        `ctx.extra`, not a dict wrapping it) for whatever the router's own
        try/finally around the denoise loop doesn't cover - most notably a
        VAE decode failure, which runs after that finally has already
        exited. Never raises - cleanup that raised would mask the original
        generation failure (mirrors chain_video_wan22's `_release_gpu` and
        `NativeGenerator.release_gpu`)."""
        for dit in (self.router.high, self.router.low):
            if dit is None:
                continue
            try:
                dit.offload()
            except Exception:
                pass
        try:
            self.vae.offload()
        except Exception:
            pass


class GeneratorWanTxt2VidPipe(BaseGeneratorPipe):
    name = "generator"
    description = "Native Wan text-to-video generator (dual-expert flow matching)"

    @classmethod
    def get_default_config(cls) -> Dict[str, Any]:
        return {
            "mode": "txt2vid",
            "steps": 30,
            "cfg": 5.0,
            "sampler": "unipc",
            "resolution": "832x480",
            "frames": 81,
            "fps": 16.0,
            "quantity": 1,
            "seed": -1,
            "device": "cuda",
            "preview": True,
            "cfg_zero_star": True,
            "zero_init_steps": 0,
            "nag_scale": 1.0,
            "nag_tau": 3.5,
            "nag_alpha": 0.5,
            "riflex": False,
            "riflex_trained_frames": None,
            # APG/SLG/schedule keys default to None (not the sampling core's literal
            # no-op values) -- this is a None-sentinel: guidance_options.py's
            # *_overrides() builders only emit a key when it's non-None, so an unset
            # knob here lets the model's own ModelSpec.sampling_settings survive the
            # merge instead of being unconditionally clobbered by a "default" value.
            "apg_eta": None,
            "apg_norm_threshold": None,
            "apg_momentum": None,
            "slg_scale": None,
            "slg_layers": None,
            "slg_sigma_start": None,
            "slg_sigma_end": None,
            "sampler_options": {},
            "step_cache": {},
            "freeinit_iterations": 0,
            "freeinit_cutoff": 0.25,
            "freeinit_order": 4,
            "schedule": "",
            "schedule_options": {},
            "manual_sigmas": "",
            "detail_strength": None,
            "detail_start": None,
            "detail_end": None,
        }

    @classmethod
    def configuration(cls) -> List[PipeConfigSpec]:
        return [
            PipeConfigSpec("mode", str, "txt2vid", "Generation mode", required=True, choices=["txt2vid"]),
            PipeConfigSpec("steps", int, 30, "Denoising steps", required=False, min_value=1, max_value=100),
            PipeConfigSpec("cfg", float, 5.0, "True CFG scale", required=False, min_value=1.0, max_value=20.0),
            PipeConfigSpec("sampler", str, "unipc", "Sampler", required=False,
                           choices=["euler", "dpmpp_2m", "dpmpp_2m_sde", "dpmpp_3m", "res_multistep", "unipc", "lcm"]),
            PipeConfigSpec("expert_boundary", float, None, "Dual-expert switch boundary override (sigma fraction)", required=False,
                           min_value=0.0, max_value=1.0),
            PipeConfigSpec("expert_switch_step", int, None, "Switch high->low expert at this step (wins over expert_boundary; converted to that step's sigma)", required=False,
                           min_value=0),
            PipeConfigSpec("cfg_zero_star", bool, True, "CFG-Zero*: rescale the uncond branch onto cond before extrapolation (free quality correction)", required=False),
            PipeConfigSpec("zero_init_steps", int, 0, "CFG-Zero*: return a zero velocity prediction for the first N steps", required=False,
                           min_value=0, max_value=100),
            PipeConfigSpec("nag_scale", float, 1.0, "Normalized Attention Guidance scale (1.0 = off). Injects the negative "
                           "prompt into cross-attention so it's enforced even at cfg=1.0 (single-forward speed) — set "
                           "cfg to 1.0 and nag_scale to ~1.1-1.5 for the speed win; NAG stacks with true CFG if both are on.",
                           required=False, min_value=1.0, max_value=20.0),
            PipeConfigSpec("nag_tau", float, 3.5, "NAG norm-clamp threshold (paper default 3.5)", required=False,
                           min_value=0.1, max_value=20.0),
            PipeConfigSpec("nag_alpha", float, 0.5, "NAG blend-back-toward-positive weight (paper default 0.5)", required=False,
                           min_value=0.0, max_value=1.0),
            *riflex_config_specs(),
            *apg_settings_config_specs(),
            *slg_settings_config_specs(),
            *sampler_step_cache_config_specs(),
            *freeinit_config_specs(),
            *schedule_settings_config_specs(),
            PipeConfigSpec("resolution", str, "832x480", "Resolution (WxH)", required=False),
            PipeConfigSpec("frames", int, 81, "Number of video frames", required=False, min_value=1, max_value=257),
            PipeConfigSpec("fps", float, 16.0, "Output frame rate", required=False, min_value=1.0, max_value=60.0),
            PipeConfigSpec("quantity", int, 1, "Number of videos", required=False, min_value=1, max_value=4),
            PipeConfigSpec("seed", int, -1, "Random seed", required=False, min_value=-1),
            PipeConfigSpec("device", str, "cuda", "Compute device", required=False, choices=["cuda", "cpu"]),
            PipeConfigSpec("preview", bool, True, "Emit live latent previews (frame 0) to the workbench during sampling", required=False),
        ]

    @classmethod
    def inputs(cls) -> List[PipeInputSpec]:
        return [
            PipeInputSpec("model", IOType.MODEL, True, "Wan model bundle", is_array=False),
            PipeInputSpec("conditioning", IOType.CONDITIONING, True, "Encoded prompt conditioning", is_array=True),
            PipeInputSpec("seed", IOType.SEED, False, "Random seeds", is_array=True),
        ]

    @classmethod
    def outputs(cls) -> List[PipeOutputSpec]:
        return [
            PipeOutputSpec("video", IOType.VIDEO, "Generated videos", is_array=True),
        ]

    # -- context -----------------------------------------------------------

    def build_context(self, pipe_input: PipeInput) -> GeneratorContext:
        bundle = pipe_input.input["model"]
        conditioning = pipe_input.input["conditioning"] or []
        seeds = pipe_input.input.get("seed", [])

        dit_in_dim = bundle.high_dit.module.in_dim
        if dit_in_dim == 36:
            raise ValueError(
                f"generator/txt2vid_wan22: loaded model '{bundle.spec.variant}' is an i2v Wan "
                f"checkpoint (in_dim=36) but the pipeline mode is txt2vid, which has no image "
                f"conditioning. Pick a t2v Wan model, or switch this preset's mode to img2vid."
            )

        steps = int(self.config.get("steps", 30))
        cfg = float(self.config.get("cfg", 5.0))
        sampler = self.config.get("sampler", "unipc")
        quantity = int(self.config.get("quantity", 1))
        frames = int(self.config.get("frames", 81))
        fps = float(self.config.get("fps", 16.0))
        device = self.config.get("device", "cuda")

        resolution = str(self.config.get("resolution", "832x480")).split("x")
        width, height = int(resolution[0]), int(resolution[1])

        spec = bundle.spec
        # APG/SLG/schedule knobs are read out of sampling_settings by
        # _make_guidance/build_sigmas (see sampling/denoise_loop.py). Assemble it
        # before the router so resolve_expert_boundary reuses the exact same
        # schedule inputs when converting an 'expert_switch_step' into a sigma.
        sampling_settings = {
            **spec.sampling_settings,
            **apg_settings_overrides(self.config),
            **slg_settings_overrides(self.config),
            **schedule_settings_overrides(self.config),
        }
        boundary = resolve_expert_boundary(spec, self.config, sampling_settings, steps, default=0.875)
        riflex = build_riflex(self.config)
        router = _ExpertRouter(bundle.high_dit, bundle.low_dit, boundary, device, riflex=riflex)
        lf = spec.latent_format

        # Snap to the model's granularity: spatial to spatial_downscale*patch (16px
        # for Wan21, 32px for Wan22-5B), frames to the causal VAE's 1+4k lattice.
        width, height, frames = _snap_geometry(bundle, lf, width, height, frames)

        logger.info("[GENERATOR WAN] %s: %d frame(s) @ %dx%d, %d steps, cfg %.1f, sampler %s, dual_expert=%s",
                    spec.variant, frames, width, height, steps, cfg, sampler, bundle.is_dual_expert)

        # Stashed for emit_results() below: process() doesn't thread ctx through
        # to emit_results, and the (post-snap) width/height are only known here.
        self._video_resolution = (width, height)

        return GeneratorContext(
            quantity=quantity,
            input_seeds=seeds,
            extra=_WanCtx(
                router=router, vae=bundle.vae, sampling_settings=sampling_settings,
                conditioning=conditioning, steps=steps, cfg=cfg, sampler=sampler,
                width=width, height=height, frames=frames, fps=fps,
                latent_channels=lf["latent_channels"], spatial_downscale=lf.get("spatial_downscale", 8),
                device=device, dtype=bundle.high_dit.compute_dtype, spec=spec,
            ),
        )

    # -- per-seed generation ----------------------------------------------

    def generate_one(self, ctx: GeneratorContext, index: int, seed: int, progress: ProgressEmitter) -> str:
        c: _WanCtx = ctx.extra
        cond_model = c.conditioning[index] if index < len(c.conditioning) else c.conditioning[-1]
        cond = _to_device(cond_model.embeds, c.device, c.dtype)
        uncond = _to_device(cond_model.n_embeds, c.device, c.dtype) if cond_model.n_embeds else None
        cond = _attach_nag(cond, uncond, self.config)

        t_lat = (c.frames - 1) // _TEMPORAL_DOWNSCALE + 1
        shape = (1, c.latent_channels, t_lat, c.height // c.spatial_downscale, c.width // c.spatial_downscale)
        # Give the router the sampling shape so it places each expert with
        # activation-aware partial residency instead of a full-pin OOM.
        c.router.latents_shape = shape

        gen = torch.Generator(device=c.device).manual_seed(int(seed))
        noise = torch.randn(shape, generator=gen, device=c.device, dtype=c.dtype)
        latents = torch.zeros(shape, device=c.device, dtype=c.dtype)

        # FreeInit (roadmap 3.8): iterations=0 (default) is a single plain pass,
        # byte-identical to before FreeInit existed -- the loop below always
        # runs range(iterations + 1) == range(1) in that case, one denoise()
        # call with the ORIGINAL seed_noise and the exact same hook math
        # (total_passes=1 -> progress identical to step_index+1, c.steps).
        iterations, fi_cutoff, fi_order = resolve_freeinit(self.config)
        total_passes = iterations + 1

        logger.debug("[GENERATOR WAN] video %d/%d, seed %d, latent %s%s", index + 1, ctx.quantity, seed, shape,
                    f", freeinit {iterations} extra pass(es)" if iterations else "")

        init_noise = noise
        latent = None
        # try/finally: the active expert may be placed with
        # PARTIAL residency (weights pinned in host RAM). An OOM / error mid-
        # sampling would otherwise skip the offload below, leaving the streamer
        # ACTIVE and its pinned host pool alive in the RAM-cached expert -- the
        # krea2-incident shape, and under a repeated-failed-run debug loop it
        # accumulates. The finally tears the streamer down (unpin +
        # reclaim) on every exit, mirroring NativeGenerator._release_dit_after_sampling.
        try:
            for it in range(total_passes):
                # P7 fix: use the hook-reported `total` (the sampler's ACTUAL step
                # count for this pass), not c.steps -- a sampler that reports a
                # different number of callbacks than the configured step count
                # (e.g. euler_restart's extra restart segments) would otherwise
                # desync the progress bar's current/total math.
                def on_progress(_frac, step_index, total, _it=it):
                    progress.step(_it * total + step_index + 1, total_passes * total,
                                  state="TXT2VID", icon=Icon(name="film", effect="pulse"))

                hooks = [ProgressHook(on_progress)]
                if self.config.get("preview", True):
                    preview_hook = make_preview_hook(c.spec, progress.preview)
                    if preview_hook is not None:
                        hooks.append(preview_hook)

                latent = denoise(
                    c.router, latents, cond, uncond,
                    steps=c.steps, sampler_name=c.sampler,
                    sampling_settings=c.sampling_settings, guidance_scale=c.cfg,
                    seed_noise=init_noise, hooks=hooks, is_cancelled=ctx.is_cancelled,
                    cfg_zero_star=bool(self.config.get("cfg_zero_star", True)),
                    zero_init_steps=int(self.config.get("zero_init_steps", 0)),
                    expert_boundary=c.router.boundary if c.router.low is not None else None,
                    # Seed determinism (stochastic samplers): `gen` is the same
                    # generator object the init noise / FreeInit draws above come
                    # from -- see ensure_sampler_generator's docstring for why it
                    # must be the SAME advancing object, not a second one
                    # re-seeded from the same int.
                    **sampler_step_cache_kwargs(self.config, sampler=c.sampler, generator=gen),
                )

                if it < iterations:
                    # Re-noise this pass's clean result and blend it against a
                    # fresh draw in 3D frequency space for the next pass's init
                    # (see freeinit.py). Both draws come from the SAME generator
                    # sequence as the original noise -> the whole multi-pass run
                    # stays deterministic for a fixed seed.
                    renoise_noise = torch.randn(shape, generator=gen, device=c.device, dtype=c.dtype)
                    fresh_noise = torch.randn(shape, generator=gen, device=c.device, dtype=c.dtype)
                    init_noise = freeinit_blend(latent, renoise_noise, fresh_noise, cutoff=fi_cutoff, order=fi_order)
        finally:
            if c.router.active is not None:
                c.router.active.offload()

        frames = _decode_video(c, latent)
        out_path = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False).name
        encode_frames_to_mp4(frames, out_path, fps=c.fps)
        return out_path

    def emit_results(self, generation_outputs: callable, results: List[str], used_seeds: List[int]) -> None:
        _emit_video_results(generation_outputs, results, used_seeds, resolution=getattr(self, "_video_resolution", None))

    def build_output(self, results: List[str]) -> Dict[str, Any]:
        return _build_video_output(results)
