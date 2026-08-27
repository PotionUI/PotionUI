"""Text-to-video generator for the native LTX-2 / 2.3 family.

Consumes the bundle from ``model_loader/ltx`` and the (already DiT-projected,
CPU-resident) conditioning list from the shared ``prompt_encoder`` pipe, and
runs the flow-matching sampler directly (video-only: the audio track is
engine-ready but out of scope here). Single-DiT (no expert pair, unlike Wan
2.2) — 5D latents, true CFG, causal video-VAE decode (VAE un-normalizes
internally, unlike Wan's separate mean/std step), then
``encode_frames_to_mp4`` + ``emit_gallery(videos=)``.

Naming: pipe discovery is two-level (``<pipe>/<variant>``), so the registered
name is ``generator/txt2vid_ltx`` (mode + model encoded in the single variant
segment), matching ``generator/txt2vid_wan22``.

**Two-stage upscale/refine**: this same pipe class is the stage-1 AND stage-2
of the in-flow ``upscale: 1.5x | 2.0x`` recipe -- ``content/presets/marketplace/LTX-2``'s
pipeline.yml calls it twice. Two additive knobs, both no-ops at their defaults:

* ``initial_latent`` (input, optional, one per seed): when given, the
  denoise loop starts from THIS latent (mixed with fresh noise at
  ``sigmas[0]``, the standard flow-matching img2img lerp -- see
  ``denoise()``'s own docstring) instead of from a zero latent + full noise.
  Pair with ``manual_sigmas`` set to a short tail schedule (e.g. the
  Lightricks ``STAGE_2_DISTILLED_SIGMA_VALUES`` suffix,
  ``preset.vars.ltx_stage2_sigma_recipe``) so ``sigmas[0] < 1.0`` and only a
  little noise is re-injected -- a refine, not a fresh generation. Absent ->
  ordinary txt2vid (zero latent, full schedule), unchanged.
* ``decode`` (config, default ``true``): when ``false``, skip the VAE
  decode + mp4 encode and instead emit the raw per-seed latent(s) via the
  ``latent`` output (the ``video`` output is empty in that case) -- lets
  stage 1 hand its latent straight to ``latent_upscaler/ltx`` without a
  wasteful (and lossy) decode/re-encode round trip.

TE eviction: by the time either LTX generator's ``build_context`` runs, the
shared ``prompt_encoder`` pipe upstream has already produced every
conditioning tensor this generation will ever need -- both
``content/presets/marketplace/LTX-2``'s pipelines wire ONE ``prompt_encoder`` node's
output into every downstream generator stage (stage 1, stage 2, and the
standalone-upscale refine all read the SAME ``conditioning``), and neither
stage re-encodes text. Audio generation decodes through the audio VAE +
vocoder (``bundle.audio_vae``/``bundle.vocoder``), not the TE. So the
multi-GB Gemma3-12B TE is dead weight in RAM for the rest of the generation
the moment either generator pipe starts -- :func:`release_idle_te` releases
it via ``bundle.te_cache_key`` + ``models.evict_dead_weight``, the same
mechanism ``generator/qwen``/``generator/krea2`` established first and
``latent_upscaler/ltx/main.py``'s ``_unload_idle_te`` (standalone-upscale
mode) already uses -- this module now owns the shared implementation both
import.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np
import torch

from src.platform.observability.profiling import get_profiler
from src.platform.runtime.native.errors import DecodeNumericsError
from src.platform.runtime.native.memory.residency import free_vram_gb
from src.platform.runtime.native.resolution import snap_frame_count, snap_resolution
from src.platform.runtime.native.sampling import ANCESTRAL_NOISE_SEED_OFFSET, ProgressHook, denoise, make_preview_hook
from src.platform.runtime.native.vae.tiling import causal3d_chunk_frames, chunked_decode_causal3d
from src.pipelines.contracts import logger
from src.pipelines.contracts import IOType, PipeInput, PipeInputSpec, PipeOutputSpec, PipeConfigSpec
from src.pipelines.outputs import Icon
from src.platform.runtime.device import clear_gpu_memory
from src.pipelines.pipes._shared.generation.generator_base import BaseGeneratorPipe, GeneratorContext
from src.pipelines.pipes._shared.vae.ltx_tiled_decode import (
    DECODE_NOISE_SEED_OFFSET,
    decode_with_oom_retry,
)
from src.pipelines.pipes._shared.generation.dit_placement import place_dit_for_sequence
from src.pipelines.pipes._shared.generation.dit_restore import restore_dit_best_effort
from src.pipelines.pipes._shared.generation.freeinit import freeinit_blend, freeinit_config_specs, resolve_freeinit
from src.pipelines.pipes._shared.generation.guidance_options import (
    apg_settings_config_specs,
    apg_settings_overrides,
    build_multimodal_guider_params,
    check_guider_mode_conflict,
    multimodal_guider_config_specs,
    sampler_step_cache_config_specs,
    sampler_step_cache_kwargs,
    schedule_settings_config_specs,
    schedule_settings_overrides,
)
from src.pipelines.pipes._shared.generation.progress import ProgressEmitter
from src.pipelines.pipes._shared.media.pixel_convert import pixels_3thw_to_uint8_frames
from src.pipelines.pipes._shared.media.video_encode import encode_frames_to_mp4, probe_effective_fps
from src.pipelines.pipes.generator.txt2vid_wan22.main import (
    _attach_nag,
    _build_video_output,
    _emit_video_results,
    _to_device,
)

# LTX video VAE downscale (patch_size 4 x three halving blocks = 32 spatial;
# compress_time + 2x compress_all = 8 temporal). 128 latent channels.
_SPATIAL_DOWNSCALE = 32
_TEMPORAL_DOWNSCALE = 8
_LATENT_CHANNELS = 128

# Fallback fps used only when `fps` is left unset (None -- see
# ``get_default_config``) AND there is no ``audio_source`` to sync to. When
# ``audio_source`` IS set, an unset `fps` instead resolves from the source
# (see ``build_context``) rather than this constant -- see module docstring
# on ``_LTXCtx.audio_source`` for why a stale default here would desync
# muxed audio.
_DEFAULT_FPS = 25.0


def parse_explicit_sigmas(raw: str) -> torch.Tensor:
    """Parse a comma-separated descending sigma list VERBATIM.

    Unlike ``manual_sigmas``/``build_sigmas``'s ``schedule="manual"`` mode,
    this does NOT force ``sigmas[0] = 1.0`` / ``sigmas[-1] = 0.0`` -- a
    stage-2 refine's starting sigma is deliberately < 1.0 (only a little
    noise re-injected into an already-upsampled latent). Feed the result to
    ``denoise()``/``denoise_prenoised()``'s own ``sigmas=`` override
    parameter, which uses it as-is.
    """
    values = [float(v.strip()) for v in str(raw).split(",") if v.strip() != ""]
    if len(values) < 2:
        raise ValueError(f"refine sigma schedule needs at least 2 values, got {len(values)}: {raw!r}")
    for a, b in zip(values, values[1:]):
        if b > a:
            raise ValueError(f"refine sigma schedule must be non-increasing, got {values}")
    return torch.tensor(values, dtype=torch.float32)


def validate_ltx_schedule_config(config: Dict[str, Any], pipe_id: str) -> None:
    """Cross-field guard shared by both LTX generator pipes.

    Each of ``sampler``/``schedule``/``manual_sigmas``/``steps`` passes its own
    ``PipeConfigSpec`` individually (non-empty choice, steps >= 1, ...), but a
    UI toggle reaction can leave them in a combination that is individually
    valid yet degenerate together -- e.g. ``schedule='manual'`` with no sigma
    values, a malformed ``manual_sigmas`` string, or ``steps=1`` with none of
    the above set (a bare single flow-matching step, which this family has no
    distilled/turbo recipe for). Those used to reach ``build_sigmas``
    (``sampling/flow_schedule.py``) or the DiT connector as a cryptic
    tensor-shape crash instead of a clean error; this raises the same
    structured ``ValueError`` any other ``PipeConfigSpec`` check would, before
    the pipe -- and the model it needs loaded -- ever runs. Called from each
    pipe's ``validate_config`` classmethod (``BasePipe.validate_config``,
    invoked by ``validate_pipe_configuration``).

    Also rejects ``quality_mode``/``distilled_mode`` both being set (see
    :func:`check_guider_mode_conflict`) -- same pre-run treatment, since it's
    the same class of "individually valid, degenerate together" config.

    ``refine_sigmas`` gets the same non-numeric/length/non-
    increasing checks as ``manual_sigmas`` (they share :func:`parse_
    explicit_sigmas`'s exact contract minus the head/tail forcing), and also
    exempts ``steps`` from its own ``>= 2`` floor below -- when set, ``steps``
    is entirely unused (``denoise()``/``denoise_prenoised()``'s ``sigmas=``
    override replaces the schedule outright).
    """
    check_guider_mode_conflict(config, pipe_id=pipe_id)

    sampler = config.get("sampler")
    if not sampler or not str(sampler).strip():
        raise ValueError(f"{pipe_id}: 'sampler' is required and cannot be empty")

    schedule = str(config.get("schedule") or "").strip()
    manual_sigmas_raw = config.get("manual_sigmas") or ""
    schedule_options = config.get("schedule_options") or {}
    has_manual_sigmas = bool(str(manual_sigmas_raw).strip()) or bool(schedule_options.get("sigmas"))

    if schedule == "manual" and not has_manual_sigmas:
        raise ValueError(
            f"{pipe_id}: schedule='manual' requires 'manual_sigmas' (or "
            f"schedule_options.sigmas) to be set -- got neither"
        )

    if manual_sigmas_raw:
        try:
            values = [float(v.strip()) for v in str(manual_sigmas_raw).split(",") if v.strip() != ""]
        except ValueError:
            raise ValueError(f"{pipe_id}: 'manual_sigmas' contains a non-numeric value: {manual_sigmas_raw!r}")
        if len(values) < 2:
            raise ValueError(f"{pipe_id}: 'manual_sigmas' needs at least 2 sigma values, got {len(values)}")
        for a, b in zip(values, values[1:]):
            if b > a:
                raise ValueError(f"{pipe_id}: 'manual_sigmas' must be non-increasing, got {values}")

    refine_sigmas_raw = str(config.get("refine_sigmas") or "").strip()
    if refine_sigmas_raw:
        try:
            parse_explicit_sigmas(refine_sigmas_raw)
        except ValueError as e:
            raise ValueError(f"{pipe_id}: 'refine_sigmas' is invalid: {e}") from e

    steps = config.get("steps")
    if isinstance(steps, (int, float)) and int(steps) < 2 and not has_manual_sigmas and not refine_sigmas_raw:
        raise ValueError(
            f"{pipe_id}: 'steps' must be >= 2 for the default multi-sigma schedule (got {steps}); "
            f"set 'manual_sigmas'/'refine_sigmas' for an explicit short/single-step recipe if intended"
        )


_warned_snapped_resolution: set[tuple[int, int]] = set()
_warned_snapped_frames: set[int] = set()


def _snap_geometry(width: int, height: int, frames: int) -> tuple[int, int, int]:
    """Snap an LTX request to the model's spatial (32px) + temporal (1+8k) granularity."""
    snapped_w, snapped_h = snap_resolution(width, height, _SPATIAL_DOWNSCALE, 1)
    snapped_frames = snap_frame_count(frames, _TEMPORAL_DOWNSCALE)
    if (snapped_w, snapped_h) != (width, height) and (width, height) not in _warned_snapped_resolution:
        _warned_snapped_resolution.add((width, height))
        logger.warning("[GENERATOR LTX] snapped resolution %dx%d -> %dx%d (granularity %dpx)",
                       width, height, snapped_w, snapped_h, _SPATIAL_DOWNSCALE)
    if snapped_frames != frames and frames not in _warned_snapped_frames:
        _warned_snapped_frames.add(frames)
        logger.warning("[GENERATOR LTX] snapped frames %d -> %d (1 + %d*k)",
                       frames, snapped_frames, _TEMPORAL_DOWNSCALE)
    return snapped_w, snapped_h, snapped_frames


def _decode_video(c: Any, latent: torch.Tensor, seed: int) -> np.ndarray:
    """Decode an LTX video latent to (T, H, W, 3) uint8 frames.

    Unlike Wan's causal-3D VAE, the LTX video VAE un-normalizes internally —
    no separate latent mean/std step here.

    Routed through the same shared ``causal3d_chunk_frames``/
    ``chunked_decode_causal3d`` seam as txt2vid_wan22's ``_decode_video``, for
    consistency and to auto-pick up temporal chunking if a future LTX VAE ever
    grows a ``new_feat_cache``. Today it's a no-op: LTX's VAE has no such
    method, so ``causal3d_chunk_frames`` always returns ``None`` here.

    The plain-decode branch goes through ``decode_with_oom_retry``, which is a
    bare ``.decode()`` for the 2.0/2.3 conv VAE (it self-chunks internally) and
    the whole-clip/tiled VRAM ladder for the 2.5 diffusion decoder, whose
    stage-5 blocks run over the full pixel-token grid with no internal bound.

    ``seed`` is the request seed. The 2.5 diffusion decoder SAMPLES the pixels
    it denoises, so without a seeded stream the same seed decodes differently
    every run; it gets its own generator offset by
    ``DECODE_NOISE_SEED_OFFSET`` so those draws never shift the sampler's.
    The conv VAE is deterministic and ignores it.
    """
    c.vae.move_to(c.device)
    z = latent.to(device=c.device, dtype=c.vae.compute_dtype)
    with torch.no_grad():
        chunk_frames = causal3d_chunk_frames(c.vae.module, z, free_vram_gb_value=free_vram_gb(c.device))
        if chunk_frames is not None:
            # accumulate on CPU so a long clip's per-chunk pixels don't pile up on
            # the GPU (+ cat duplicate) and OOM at assembly — frames go to CPU next.
            pixels = chunked_decode_causal3d(
                c.vae.module, z, chunk_latent_frames=chunk_frames, accumulate_device="cpu",
            )
        else:
            pixels = decode_with_oom_retry(  # (B, 3, T, H, W) in [-1, 1]
                c.vae, z, c.device,
                generator=torch.Generator(device=c.device).manual_seed(
                    int(seed) + DECODE_NOISE_SEED_OFFSET
                ),
                profiler_mark="ltx.decode", log_prefix="[GENERATOR LTX]",
            )
    c.vae.offload()

    # Catch a corrupt decode HERE, before the uint8 cast below silently turns
    # a NaN pixel into a black 0 with no error (torch.clamp leaves NaN
    # unchanged; see DecodeNumericsError). Checked before the pixel-convert
    # cast so this covers both the chunked and single-call decode paths above
    # uniformly, mirroring txt2vid_wan22's identical guard.
    if not torch.isfinite(pixels).all():
        raise DecodeNumericsError()

    # Chunked conversion: the full-tensor chain allocated several clip-sized
    # fp32 transients on the decode device and OOM'd long high-res clips after
    # a successful decode (see pixel_convert).
    return pixels_3thw_to_uint8_frames(pixels[0])  # (T, H, W, 3)


def _te_ram_gb(te_native_model: Any) -> float:
    """Best-effort host-RAM footprint of a TE ``NativeModel``'s parameters, for
    the log field on a TE eviction below. Single Gemma3 module (no T5/CLIP-
    composite duck-typing needed here, unlike the more general
    ``NativeGenerator._te_size_gb`` in engine.py -- that method is
    private/bound to a ``NativeGenerator`` instance the callers of this
    function don't have)."""
    module = getattr(te_native_model, "module", None)
    if module is None or not hasattr(module, "parameters"):
        return 0.0
    try:
        return sum(p.numel() * p.element_size() for p in module.parameters()) / (1 << 30)
    except Exception:
        return 0.0


def release_idle_te(bundle: Any, models: Any, log_prefix: str) -> float:
    """Evict the TE's MODELS cache entry: by the time a generator (or the
    standalone-upscale refine) pipe runs, ``prompt_encoder`` has already
    produced the conditioning every downstream consumer needs -- the
    ~22GB Gemma3-12B TE is pure dead weight in host RAM for the rest of THIS
    generation. Both LTX-2/2.3 preset pipelines (``video`` and ``upscale``)
    call ``prompt_encoder`` exactly once; every generator stage (including a
    stage-2 refine) and the standalone upscale's refine pass all read that
    SAME conditioning output, never re-encoding -- and audio generation
    decodes through the audio VAE + vocoder, not the TE. Shared by
    ``generator/txt2vid_ltx``, ``generator/video_ltx``, and
    ``latent_upscaler/ltx``'s standalone-upscale ``_unload_idle_te`` (that
    pipe runs BEFORE a generator in the standalone-upscale pipeline, so its
    own call is the first to fire there; in the in-flow two-stage video
    pipeline, ``generator_stage1``'s own call now fires first instead, making
    ``latent_upscaler``'s call a harmless no-op fallback) -- one
    implementation instead of three copies.

    Safe when: the bundle carries no ``te_cache_key`` (built outside the
    MODELS cache, e.g. isolated pipe tests -- no-op); ``models`` (the MODELS
    service) wasn't injected -- no-op; or the TE's cache entry is already
    gone (evicted by an earlier pipe in this same generation, or still
    referenced elsewhere -- ``evict_dead_weight`` reports ``False`` and this
    returns 0.0 without raising). Bypassing this generation's own lease is
    safe because the native queue is serial per backend; the accepted cost is
    a later pipe/generation that needs this TE again cache-missing and paying
    a fresh reload from disk -- strictly better than an OOM/earlyoom kill
    this avoids. See ``ModelLifecycle.evict_dead_weight``'s docstring
    for the full argument.

    Returns the estimated GB actually freed (0.0 if nothing was unloaded).
    """
    key = getattr(bundle, "te_cache_key", None)
    if not key or models is None:
        return 0.0
    evict = getattr(models, "evict_dead_weight", None)
    if not callable(evict):
        return 0.0
    te_gb = _te_ram_gb(getattr(bundle, "te", None))
    try:
        unloaded = evict(key)
    except Exception:  # pragma: no cover - best-effort; never fail the pipe over this
        logger.debug("[%s] TE eviction failed for key=%r", log_prefix, key, exc_info=True)
        return 0.0
    # Coarse census mark (once per call, never per-step) for host-RAM
    # accounting: mirrors latent_upscaler/ltx's `_free_room_for_upscale`,
    # the origin of this mechanism, so both eviction sites show up in a
    # generation's profiler trace under the same field names.
    get_profiler().mark(
        "ltx_generator.te_evicted", pipe=log_prefix, key=key,
        unloaded=bool(unloaded), te_gb=round(te_gb, 2) if unloaded else 0.0,
    )
    if unloaded:
        logger.debug("[%s] evicted idle TE cache entry key=%r (~%.2fGB)", log_prefix, key, te_gb)
    return te_gb if unloaded else 0.0


@dataclass
class _LTXCtx:
    bundle: Any
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
    device: str
    dtype: torch.dtype
    spec: Any = None
    # Per-seed seed latents for a refine pass (in place of pure noise) -- see
    # GeneratorLtxTxt2VidPipe's module-level "decode"/"initial_latent" docs
    # below. Empty = ordinary txt2vid.
    initial_latents: list = field(default_factory=list)
    # Standalone-upscale audio passthrough: an existing video's path, muxed
    # into this pipe's own mp4 encode as a fallback when this call has no
    # generated audio of its own (this pipe is video-only -- see module
    # docstring). None = ordinary txt2vid encode, unchanged (byte-identical
    # `-an` argv via encode_frames_to_mp4).
    audio_source: Optional[str] = None
    # Standalone-upscale tail-padding trim: the source video's frame count
    # BEFORE latent_upscaler/ltx's `_pad_frames_to_temporal_grid` repeated its
    # last frame up to the VAE's 1+8k lattice -- this pipe's own decoded
    # `frames` array carries that padding straight through (its own frame
    # count is derived from the padded latent's temporal length), so
    # generate_one drops any frames beyond this count before mux. None = no
    # trim (in-flow two-stage has no padding to begin with).
    trim_to_frame_count: Optional[int] = None

    def release_gpu(self) -> None:
        """Best-effort GPU cleanup on a failed generation: offload the DiT/VAE
        that may be resident mid-sampling. Picked up by
        `BaseGeneratorPipe._release_gpu_on_error` (this dataclass IS
        `ctx.extra`) for whatever the sampling loop's own cleanup doesn't
        cover -- most notably a VAE decode failure. Never raises -- cleanup
        that raised would mask the original generation failure (mirrors
        txt2vid_wan22's `_WanCtx.release_gpu`)."""
        try:
            self.bundle.dit.offload()
        except Exception:
            pass
        try:
            self.vae.offload()
        except Exception:
            pass


class GeneratorLtxTxt2VidPipe(BaseGeneratorPipe):
    name = "generator"
    description = "Native LTX-2/2.3 text-to-video generator (video-only)"

    @classmethod
    def get_default_config(cls) -> Dict[str, Any]:
        return {
            "mode": "txt2vid",
            "steps": 24,
            "cfg": 4.0,
            "sampler": "euler",
            "resolution": "768x512",
            "frames": 49,
            # None-sentinel (see apg_eta's identical idiom in
            # guidance_options.py): lets build_context tell "preset left this
            # unset" apart from "preset explicitly set 25.0" -- an explicit
            # fps always wins, even when `audio_source` is connected (see
            # build_context's fps-resolution ladder). Unset resolves to
            # `_DEFAULT_FPS` normally, or the source's own detected rate when
            # `audio_source` is set.
            "fps": None,
            "quantity": 1,
            "seed": -1,
            "device": "cuda",
            "preview": True,
            "cfg_zero_star": True,
            "zero_init_steps": 0,
            "nag_scale": 1.0,
            "nag_tau": 3.5,
            "nag_alpha": 0.5,
            # None-sentinel: guidance_options.py's *_overrides() only emit a key
            # when non-None, so an unset knob here lets the model's own
            # ModelSpec.sampling_settings survive the merge (see that module's
            # docstring; P5 fix).
            "apg_eta": None,
            "apg_norm_threshold": None,
            "apg_momentum": None,
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
            "quality_mode": False,
            "quality_cfg": None,
            "quality_stg": None,
            "quality_rescale": None,
            "quality_modality": None,
            "quality_stg_blocks": None,
            "quality_distilled_strength": None,
            "decode": True,
            "refine_sigmas": "",
        }

    @classmethod
    def configuration(cls) -> List[PipeConfigSpec]:
        return [
            PipeConfigSpec("mode", str, "txt2vid", "Generation mode", required=True, choices=["txt2vid"]),
            PipeConfigSpec("decode", bool, True,
                           "Decode to video; set false to emit the raw latent "
                           "for a downstream latent_upscaler/refine stage instead", required=False),
            PipeConfigSpec("refine_sigmas", str, "",
                           "Explicit sigma schedule for a refine pass, comma-separated, "
                           "descending, used VERBATIM (unlike manual_sigmas, the head is not forced to "
                           "1.0) -- required when 'initial_latent' is connected", required=False),
            PipeConfigSpec("steps", int, 24, "Denoising steps", required=False, min_value=1, max_value=100),
            PipeConfigSpec("cfg", float, 4.0, "True CFG scale", required=False, min_value=1.0, max_value=20.0),
            PipeConfigSpec("sampler", str, "euler",
                           "Sampler. 'euler_ancestral' is LTX-2.5's stage-1 sampler (stochastic, eta=1.0 "
                           "by default via sampler_options) -- pair it with schedule='ltx_dynamic' for the "
                           "matching resolution-aware sigma shift.", required=False,
                           choices=["euler", "dpmpp_2m", "dpmpp_2m_sde", "dpmpp_3m", "res_multistep", "unipc", "lcm",
                                    "euler_ancestral", "euler_ancestral_cfg_pp", "euler_cfg_pp"]),
            PipeConfigSpec("resolution", str, "768x512", "Resolution (WxH)", required=False),
            PipeConfigSpec("frames", int, 49, "Number of video frames (must be 1 + 8*k; up to ~40s at 25fps)",
                           required=False, min_value=1, max_value=1001),
            PipeConfigSpec("fps", float, None,
                           f"Output frame rate (unset -> {_DEFAULT_FPS}, or the audio_source video's own "
                           "detected rate when one is connected, so muxed audio stays in sync)",
                           required=False, min_value=1.0, max_value=60.0),
            PipeConfigSpec("quantity", int, 1, "Number of videos", required=False, min_value=1, max_value=4),
            PipeConfigSpec("seed", int, -1, "Random seed", required=False, min_value=-1),
            PipeConfigSpec("device", str, "cuda", "Compute device", required=False, choices=["cuda", "cpu"]),
            PipeConfigSpec("preview", bool, True, "Emit live latent previews (frame 0) to the workbench during sampling", required=False),
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
            *apg_settings_config_specs(),
            *sampler_step_cache_config_specs(),
            *freeinit_config_specs(),
            *schedule_settings_config_specs(),
            *multimodal_guider_config_specs(),
        ]

    @classmethod
    def validate_config(cls, config: Dict[str, Any]) -> None:
        validate_ltx_schedule_config(config, pipe_id="generator/txt2vid_ltx")

    @classmethod
    def inputs(cls) -> List[PipeInputSpec]:
        return [
            PipeInputSpec("model", IOType.MODEL, True, "LTX model bundle", is_array=False),
            PipeInputSpec("conditioning", IOType.CONDITIONING, True, "Encoded prompt conditioning", is_array=True),
            PipeInputSpec("seed", IOType.SEED, False, "Random seeds", is_array=True),
            PipeInputSpec("initial_latent", IOType.LATENT, False,
                          "Seed latent(s) for a refine pass, one per seed; absent -> "
                          "ordinary txt2vid from pure noise", is_array=True),
            PipeInputSpec("audio_source", IOType.VIDEO, False,
                          "Existing video whose audio track is preserved into this pipe's own encode "
                          "(standalone upscale mode has no audio latents of its own -- see module "
                          "docstring). A fallback only: ignored if this call ever produces its own "
                          "generated audio", is_array=False),
            PipeInputSpec("source_frame_count", IOType.INT, False,
                          "Original (pre-temporal-padding) frame count of the standalone-upscale source "
                          "video, from latent_upscaler/ltx's 'source_frame_count' output -- used to trim "
                          "the padded duplicate tail frames from this pipe's own decoded output before "
                          "mux. Absent -> no trim (in-flow two-stage, or a 'video' input that was already "
                          "on-grid)", is_array=False),
            PipeInputSpec("MODELS", IOType.SERVICE, False,
                          "Model lifecycle service, to release the idle TE's host RAM", is_array=False),
        ]

    @classmethod
    def outputs(cls) -> List[PipeOutputSpec]:
        return [
            PipeOutputSpec("video", IOType.VIDEO, "Generated videos (empty when decode=false)", is_array=True),
            PipeOutputSpec("latent", IOType.LATENT, "Raw per-seed latents (only populated when decode=false)",
                           is_array=True),
        ]

    # -- context -----------------------------------------------------------

    def build_context(self, pipe_input: PipeInput) -> GeneratorContext:
        bundle = pipe_input.input["model"]
        conditioning = pipe_input.input["conditioning"] or []
        seeds = pipe_input.input.get("seed", [])
        # `or []` is unusable here: a bare Tensor raises on truthiness. The
        # upstream latent_upscaler may deliver a single Tensor or a list.
        raw_initial = pipe_input.input.get("initial_latent")
        if raw_initial is None:
            initial_latents = []
        elif isinstance(raw_initial, (list, tuple)):
            initial_latents = list(raw_initial)
        else:
            initial_latents = [raw_initial]

        audio_source = pipe_input.input.get("audio_source")
        source_frame_count = pipe_input.input.get("source_frame_count")

        if bundle.spec.family != "ltx":
            raise ValueError(
                f"generator/txt2vid_ltx: loaded model '{bundle.spec.family}/{bundle.spec.variant}' "
                f"is not an LTX-2/2.3 checkpoint. Pick an LTX DiT for this preset."
            )

        # TE eviction: this pipe never touches the TE itself (conditioning
        # was already produced by `prompt_encoder`, upstream of every LTX
        # preset pipeline) -- see `release_idle_te`'s docstring.
        release_idle_te(bundle, pipe_input.input.get("MODELS"), "GENERATOR LTX")

        steps = int(self.config.get("steps", 24))
        cfg = float(self.config.get("cfg", 4.0))
        sampler = self.config.get("sampler", "euler")
        quantity = int(self.config.get("quantity", 1))
        frames = int(self.config.get("frames", 49))
        fps_config = self.config.get("fps")
        device = self.config.get("device", "cuda")

        if fps_config is not None:
            # An explicit fps always wins -- even with `audio_source`
            # connected (video-mode contract: unchanged when `fps` is set).
            fps = float(fps_config)
        elif audio_source:
            # Standalone upscale mode has no fps field of its own (see
            # modes/upscale/form.yml), so `fps` is unset here. The source
            # clip's real rate is only knowable at runtime (not at
            # preset-render time), and re-encoding at the wrong fps drifts
            # `audio_source`'s muxed audio out of sync -- so it MUST come from
            # the source rather than silently falling back to a static
            # default. `probe_effective_fps` prefers a duration-derived rate
            # over the nominal one when they materially disagree (robust to
            # VFR/mislabeled containers -- see video_encode.py).
            effective_fps = probe_effective_fps(audio_source)
            if not effective_fps or effective_fps <= 0:
                raise ValueError(
                    f"generator/txt2vid_ltx: could not determine a frame rate for "
                    f"audio_source '{audio_source}' (ffprobe/ffmpeg missing, the file is "
                    f"unreadable/corrupt, or it carries no usable rate metadata) and no "
                    f"explicit 'fps' was configured -- refusing to encode at a guessed "
                    f"default, since that would desync the muxed audio; install "
                    f"ffmpeg/ffprobe or set an explicit fps"
                )
            fps = effective_fps
        else:
            fps = _DEFAULT_FPS

        if initial_latents:
            # A refine pass derives width/height/frames from the SEED LATENT's
            # own shape rather than from `resolution`/`frames` config -- an
            # upstream latent's source is only known at runtime, never at
            # preset-render time, so those two config keys are simply ignored
            # on this path.
            _, _, t_lat0, h_lat0, w_lat0 = initial_latents[0].shape
            width = int(w_lat0) * _SPATIAL_DOWNSCALE
            height = int(h_lat0) * _SPATIAL_DOWNSCALE
            frames = (int(t_lat0) - 1) * _TEMPORAL_DOWNSCALE + 1
        else:
            resolution = str(self.config.get("resolution", "768x512")).split("x")
            width, height = int(resolution[0]), int(resolution[1])
            width, height, frames = _snap_geometry(width, height, frames)

        spec = bundle.spec
        # APG: read straight out of sampling_settings by _make_guidance (see
        # sampling/denoise_loop.py), not a denoise() top-level kwarg. No SLG
        # here -- LTXAVModel.forward has no skip_layers kwarg (Wan-only).
        sampling_settings = {
            **spec.sampling_settings,
            **apg_settings_overrides(self.config),
            **schedule_settings_overrides(self.config),
        }

        logger.info("[GENERATOR LTX] %s: %d frame(s) @ %dx%d, %d steps, cfg %.1f, sampler %s",
                    spec.variant, frames, width, height, steps, cfg, sampler)

        # Stashed for emit_results() below: process() doesn't thread ctx through
        # to emit_results, and the (post-snap) width/height are only known here.
        self._video_resolution = (width, height)

        return GeneratorContext(
            quantity=quantity,
            input_seeds=seeds,
            extra=_LTXCtx(
                bundle=bundle, vae=bundle.vae, sampling_settings=sampling_settings,
                conditioning=conditioning, steps=steps, cfg=cfg, sampler=sampler,
                width=width, height=height, frames=frames, fps=fps,
                device=device, dtype=bundle.dit.compute_dtype, spec=spec,
                initial_latents=initial_latents, audio_source=audio_source,
                trim_to_frame_count=int(source_frame_count) if source_frame_count else None,
            ),
        )

    # -- per-seed generation ----------------------------------------------

    def generate_one(self, ctx: GeneratorContext, index: int, seed: int, progress: ProgressEmitter) -> Any:
        c: _LTXCtx = ctx.extra
        cond_model = c.conditioning[index] if index < len(c.conditioning) else c.conditioning[-1]
        cond = _to_device(cond_model.embeds, c.device, c.dtype)
        uncond = _to_device(cond_model.n_embeds, c.device, c.dtype) if cond_model.n_embeds else None
        # NAG: uncond["context"] is ALREADY the negative prompt run through
        # apply_text_conditioning (ltx_clip.py's LTXClipTextEncoder._project
        # builds n_embeds the same way as embeds), i.e. already the
        # [B,S,v_inner+a_inner] shape LTXAVModel.forward's nag_context expects
        # -- no second projection call needed; _attach_nag (txt2vid_wan22)
        # is family-agnostic (keys off "context" alone) so it's reused as-is.
        cond = _attach_nag(cond, uncond, self.config)

        t_lat = (c.frames - 1) // _TEMPORAL_DOWNSCALE + 1
        h_lat = c.height // _SPATIAL_DOWNSCALE
        w_lat = c.width // _SPATIAL_DOWNSCALE
        shape = (1, _LATENT_CHANNELS, t_lat, h_lat, w_lat)

        gen = torch.Generator(device=c.device).manual_seed(int(seed))
        noise = torch.randn(shape, generator=gen, device=c.device, dtype=c.dtype)
        # euler_ancestral (LTX-2.5 stage-1): a DEDICATED generator, offset from
        # the request seed, so its per-step stochastic draws never overlap the
        # init-noise/FreeInit stream `gen` already drives -- unlike every other
        # STOCHASTIC_SAMPLERS entry, which deliberately reuses `gen` itself (see
        # ensure_sampler_generator's docstring vs. euler_ancestral.py's own).
        sampler_gen = gen
        if c.sampler == "euler_ancestral":
            sampler_gen = torch.Generator(device=c.device).manual_seed(int(seed) + ANCESTRAL_NOISE_SEED_OFFSET)

        # A refine pass seeds `latents` from an upstream (upsampled) latent
        # instead of a zero tensor -- `denoise()`'s own init mix
        # (`sigma0*noise + (1-sigma0)*latents`) then only re-injects
        # as much noise as the schedule's sigmas[0] calls for (< 1.0 when
        # `manual_sigmas`/`schedule` is set to a short tail recipe).
        refine_sigmas: Optional[torch.Tensor] = None
        if c.initial_latents:
            init_latent = c.initial_latents[index] if index < len(c.initial_latents) else c.initial_latents[-1]
            if tuple(init_latent.shape) != shape:
                # width/height/frames were derived from initial_latents[0]'s
                # own shape in build_context -- this only fires when a LATER
                # seed's latent (or the fallback-to-last one) has a different
                # shape than the first, which the caller must not do.
                raise ValueError(
                    f"generator/txt2vid_ltx: initial_latent[{index}] shape {tuple(init_latent.shape)} does not "
                    f"match initial_latents[0]'s shape {shape} -- every seed's refine latent in one call must "
                    f"share the same dimensions"
                )
            latents = init_latent.to(device=c.device, dtype=c.dtype)
            # `manual_sigmas`/build_sigmas's "manual" mode always forces
            # sigmas[0]=1.0 -- that would silently turn this refine back into
            # a full regeneration (mix_initial_noise's init blend ignores
            # `latents` entirely at sigma0=1.0). `refine_sigmas` is required
            # instead: parsed VERBATIM and passed straight to `denoise()`'s
            # `sigmas=` override, bypassing that forcing.
            raw_refine_sigmas = str(self.config.get("refine_sigmas") or "").strip()
            if not raw_refine_sigmas:
                raise ValueError(
                    "generator/txt2vid_ltx: 'initial_latent' is connected but 'refine_sigmas' is empty -- "
                    "set an explicit descending sigma schedule (sigma[0] < 1.0) for the refine pass"
                )
            refine_sigmas = parse_explicit_sigmas(raw_refine_sigmas)
        else:
            latents = torch.zeros(shape, device=c.device, dtype=c.dtype)

        # Sequence-length-aware placement: a plain full-pin
        # move_to leaves a FIXED activation budget that a long clip's growing
        # attention/RoPE/hidden-state activations can exceed and OOM on, even
        # though the same DiT comfortably fits a short clip. See
        # dit_placement.py's module docstring for the full reasoning; short
        # clips see byte-identical behaviour (full pin, same call).
        place_dit_for_sequence(
            c.bundle.dit, c.device, video_tokens=t_lat * h_lat * w_lat,
            own_models=(c.bundle.dit, c.vae),
        )
        dit_module = c.bundle.dit.module
        fps = c.fps

        def model_forward(x: torch.Tensor, sigma: torch.Tensor, conditioning: dict) -> torch.Tensor:
            # x wrapped as a 1-element list = video-only -> forward returns the
            # video velocity tensor alone (see LTXAVModel.forward).
            extra = {}
            nag_context = conditioning.get("nag_context")
            if nag_context is not None:
                extra["nag_context"] = nag_context
                extra["nag"] = conditioning.get("nag")
            # FBCache: read the reserved "step_cache" key (see denoise_loop.py's
            # _CachingGuidance, which always spreads a FRESH
            # {**conditioning, "step_cache": ...} dict rather than mutating the
            # persistent one) only when present/non-None, mirroring
            # _ExpertRouter's skip_layers/step_cache handling -- never pass
            # step_cache=None when the arch forward doesn't accept it yet.
            step_cache = conditioning.get("step_cache")
            if step_cache is not None:
                extra["step_cache"] = step_cache
            # MultiModalGuider hooks: STG (skip self-attn at specified blocks)
            # and modality guidance (disable cross-modal attention).
            stg_skip = conditioning.get("stg_skip_blocks")
            if stg_skip is not None:
                extra["stg_skip_blocks"] = stg_skip
            if conditioning.get("disable_cross_modal"):
                extra["disable_cross_modal"] = True
            return dit_module([x], sigma, conditioning["context"], attention_mask=None, frame_rate=fps, **extra)

        # MultiModalGuider (quality recipe): build the guidance strategy from
        # pipe config; when active, FBCache/NAG are gated off with a log.
        mm_params = build_multimodal_guider_params(self.config)
        guidance_override = None
        if mm_params is not None:
            from src.platform.runtime.native.sampling.multimodal_guider import MultiModalGuidance
            video_params, _audio_params = mm_params
            guidance_override = MultiModalGuidance(video_params)
            logger.debug("[GENERATOR LTX] quality_mode ON (MultiModalGuider): cfg=%.1f stg=%.1f "
                        "rescale=%.2f modality=%.1f stg_blocks=%s",
                        video_params.cfg_scale, video_params.stg_scale,
                        video_params.rescale_scale, video_params.modality_scale,
                        video_params.stg_blocks)

        # FreeInit (roadmap 3.8): iterations=0 (default) is a single plain
        # pass, byte-identical to before FreeInit existed -- see
        # txt2vid_wan22.generate_one for the identical loop shape/reasoning.
        iterations, fi_cutoff, fi_order = resolve_freeinit(self.config)
        total_passes = iterations + 1

        logger.debug("[GENERATOR LTX] video %d/%d, seed %d, latent %s%s", index + 1, ctx.quantity, seed, shape,
                    f", freeinit {iterations} extra pass(es)" if iterations else "")

        init_noise = noise
        latent = None
        for it in range(total_passes):
            # P7 fix: use the hook-reported `total` (the sampler's ACTUAL step
            # count for this pass), not c.steps -- see txt2vid_wan22's
            # generate_one for the identical reasoning.
            def on_progress(_frac, step_index, total, _it=it):
                progress.step(_it * total + step_index + 1, total_passes * total,
                              state="TXT2VID", icon=Icon(name="film", effect="pulse"))

            hooks = [ProgressHook(on_progress)]
            if self.config.get("preview", True):
                preview_hook = make_preview_hook(c.spec, progress.preview)
                if preview_hook is not None:
                    hooks.append(preview_hook)

            latent = denoise(
                model_forward, latents, cond, uncond,
                steps=c.steps, sampler_name=c.sampler,
                sampling_settings=c.sampling_settings, guidance_scale=c.cfg,
                seed_noise=init_noise, hooks=hooks, is_cancelled=ctx.is_cancelled,
                cfg_zero_star=bool(self.config.get("cfg_zero_star", True)),
                zero_init_steps=int(self.config.get("zero_init_steps", 0)),
                guidance_override=guidance_override,
                sigmas=refine_sigmas,
                # image_seq_len: the packed video token count -- only consumed
                # by sampling_settings['schedule'] == 'ltx_dynamic' (see
                # flow_schedule.py's _ltx_dynamic_shift_sigmas); every other
                # schedule mode ignores it, so this is a no-op unless a preset
                # opts in.
                image_seq_len=t_lat * h_lat * w_lat,
                # Seed determinism (stochastic samplers): `sampler_gen` is
                # `gen` itself (the init noise / FreeInit stream) for every
                # sampler except euler_ancestral, which gets its own offset
                # generator instead -- see the comment where it's built above.
                **sampler_step_cache_kwargs(self.config, sampler=c.sampler, generator=sampler_gen),
            )

            if it < iterations:
                renoise_noise = torch.randn(shape, generator=gen, device=c.device, dtype=c.dtype)
                fresh_noise = torch.randn(shape, generator=gen, device=c.device, dtype=c.dtype)
                init_noise = freeinit_blend(latent, renoise_noise, fresh_noise, cutoff=fi_cutoff, order=fi_order)

        c.bundle.dit.offload()
        clear_gpu_memory()

        decode = bool(self.config.get("decode", True))
        if not decode:
            # Stage 1: hand the raw latent to a downstream
            # latent_upscaler/refine stage -- no VAE decode/mp4-encode round
            # trip. `restore_dit_best_effort` still runs below (harmless: the
            # very next pipe in the chain is usually this SAME generator
            # again for stage 2, which will just find the DiT already
            # resident via `place_dit_for_sequence`).
            if index == ctx.quantity - 1:
                restore_dit_best_effort(c.bundle.dit, c.device)
            return latent

        frames = _decode_video(c, latent, seed)
        if c.trim_to_frame_count is not None and 0 < c.trim_to_frame_count < frames.shape[0]:
            # Drop latent_upscaler/ltx's repeated-last-frame temporal padding
            # (see its `_pad_frames_to_temporal_grid`) before it reaches the
            # mux -- this decoded array's length tracks the PADDED latent's
            # temporal size, not the original source's. Trimming after the
            # full array is assembled means this is correct whether or not
            # the decode itself was chunked internally (see _decode_video).
            logger.info(
                "[GENERATOR LTX] refine pass: trimming %d padded tail frame(s) "
                "(decoded %d -> source %d) before mux",
                frames.shape[0] - c.trim_to_frame_count, frames.shape[0], c.trim_to_frame_count)
            frames = frames[: c.trim_to_frame_count]
        out_path = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False).name
        # This pipe never produces audio of its own (video-only), so
        # `audio_source` is the only audio. `encode_frames_to_mp4` probes it
        # for an actual audio stream and degrades to `-an` when there isn't one.
        encode_frames_to_mp4(frames, out_path, fps=c.fps, audio=c.audio_source)

        # Best-effort warm-start for the NEXT generation: only after the LAST
        # seed of THIS invocation (quantity>1 loops call generate_one multiple
        # times per process(), offloading/reloading the DiT between each --
        # restoring here would just be immediately undone by the next seed's
        # own `move_to` above). Positioned after decode + mp4 encode (which
        # already ran the VAE offload inside _decode_video) so wall-clock to
        # this video's own visible result is unchanged; see dit_restore.py.
        if index == ctx.quantity - 1:
            restore_dit_best_effort(c.bundle.dit, c.device)

        return out_path

    def emit_results(self, generation_outputs: callable, results: List[Any], used_seeds: List[int]) -> None:
        if not bool(self.config.get("decode", True)):
            return  # stage 1: an internal latent hand-off, no gallery/seed emission here
        _emit_video_results(generation_outputs, results, used_seeds, resolution=getattr(self, "_video_resolution", None))

    def build_output(self, results: List[Any]) -> Dict[str, Any]:
        if not bool(self.config.get("decode", True)):
            return {"latent": results, "video": []}
        return {**_build_video_output(results), "latent": []}
