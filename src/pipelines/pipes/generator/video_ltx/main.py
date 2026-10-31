"""Video-director generator for the native LTX-2 / 2.3 family.

One generation covers a whole director timeline: first-frame / last-frame /
arbitrary keyframe media conditioning (token overwrite + appended tokens with
their own RoPE coords), IC-LoRA reference videos (appended tokens; the LoRA
itself is applied by ``model_loader/ltx``'s ``loras`` config), and an optional
audio track (generated jointly by the AV DiT, or a user file muxed at encode
time). ``generator/txt2vid_ltx`` stays the plain t2v path; this pipe owns every
conditioned shape.

Sampling runs over ONE packed state tensor ``[1, S_base + N_extra (+ T_a),
128]`` — LTX's video token dim (128 latent channels, patch 1) equals its packed
audio dim (8 ch x 16 mel), so base video tokens, appended conditioning tokens
and audio tokens ride the same sampler state. Conditioned tokens are held to
their ``clean`` values at every model call via two mechanisms (diffusers
``pipeline_ltx2_condition`` reference semantics, see ``ConditionedAVForward``):

1. **Model-input clamp**: before each DiT forward, conditioned positions in the
   sampler's ``x`` are replaced with ``clean``, so the DiT always sees the
   identity anchor at those positions regardless of what noise the sampler
   carried in (critical for ancestral samplers that inject fresh noise into all
   tokens each step — without this, accumulated ancestral noise corrupts the
   DiT's view of positions whose per-token timestep claims sigma=0).
2. **x0-space trajectory blend**: after the DiT forward, the predicted x0 at
   conditioned positions is re-forced to ``clean`` (operating on the sampler's
   ORIGINAL ``x``, not the clamped model input), and the returned velocity is
   recomputed to point from that x toward the forced x0 — this defines the
   correct trajectory update for the sampler's next step.

Per-token timesteps ``sigma * (1 - mask)`` tell the DiT that conditioned tokens
are clean (sigma=0 there). Only single-step samplers with no cross-step
velocity HISTORY are supported (``euler``, ``euler_ancestral``,
``euler_ancestral_cfg_pp``, ``euler_cfg_pp``): both mechanisms are re-derived
fresh each step, so a
multistep integrator that mixes PAST (pre-clamp, pre-blend) velocities into
its update (``dpmpp_2m`` and friends) is unvalidated here and deliberately not
offered.

**Two-stage upscale/refine**: same additive knobs as
``generator/txt2vid_ltx`` (see that pipe's module docstring for the full
reasoning), reusing THIS pipe's own conditioning machinery rather than a
separate mask:

* ``initial_latent`` (input, optional, one per seed): when given, it seeds
  the base-token slice of ``prepared`` (built from ``media_placements`` in
  ``build_context``) instead of noise. Two cases:

  - **No media conditioning** (plain refine): ``prepared`` is the all-zero
    fallback, so the latent wholesale REPLACES ``tokens``/``clean``, mask
    stays all-zero -- no position is clamped clean, every token denoises
    freely from whatever ``sigmas[0]`` the schedule calls for, via the SAME
    ``mix_initial_noise``/x0-blend machinery, just with a uniform strength
    of 0 everywhere. Byte-identical to the prior behavior.
  - **With media conditioning** (image/video keyframes, ``media_placements``):
    ``prepared`` is built from the SAME conditions but at the CALL's own
    (stage-2) resolution/frames -- keyframes are re-VAE-encoded at target
    res rather than upsampled from stage 1's lower-res tokens. The base
    slice is then MERGED, not replaced: masked (keyframe-anchored) positions
    keep the freshly re-encoded keyframe tokens, unmasked positions take the
    upsampled prior latent. ``clean`` is left as ``prepared.clean``
    unmodified -- both conditioning-blend sites in
    ``ConditionedAVForward`` weight ``clean`` by ``mask``, so its value at
    mask=0 positions is never read. ``role="reference"`` (IC-LoRA)
    conditioning and video-sourced conditioning are rejected together with
    ``initial_latent`` (out of scope / unvalidated for a stage-2 refine, see
    ``build_context``).

  Pair with a short tail ``manual_sigmas`` schedule (``sigmas[0] < 1.0``) so
  only a little noise is re-injected -- a refine, not a fresh generation.
  Either way the latent's base token count must match the configured
  resolution/frames (extra/appended tokens are tolerated and preserved).
* ``decode`` (config, default ``true``): ``false`` skips VAE decode/mp4
  encode and emits the raw per-seed latent(s) via the ``latent`` output
  instead (``video`` output empty in that case). Audio is decoded
  independently of this flag whenever ``audio=true`` -- the audio VAE +
  vocoder decode has no dependency on the video VAE round trip, so a
  ``decode=false`` stage-1 call (feeding a ``latent_upscaler``) still
  produces a finished ``audio`` output for a downstream stage-2 refine to
  mux, instead of silently dropping the audio track. See the ``audio``
  output and ``audio_source="passthrough"`` below.
* ``audio`` (output, one per seed): populated whenever ``audio=true``,
  independent of ``decode`` -- ``audio_source="generate"`` decodes the
  sampled audio tokens through the bundle's audio VAE + vocoder into an
  ``AudioTrack``; ``audio_source="file"`` passes the user-supplied audio
  input straight through (str/Path). A stage-2 refine call wires this
  output back into its OWN ``audio`` input with
  ``audio_source="passthrough"``: mux the already-finished track verbatim,
  no re-decode, no re-generation -- see that config's docstring.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass, field, replace
import math
from typing import Any, Dict, List, Optional, Tuple

import torch

from src.platform.runtime.native.lora import temporarily_applied_loras
from src.platform.runtime.native.sampling import (
    ANCESTRAL_NOISE_SEED_OFFSET,
    ProgressHook,
    conditioned_sigmas,
    denoise_prenoised,
    make_preview_hook,
)
from src.platform.runtime.native.sampling.step_profile import StepPlanHook
from src.pipelines.contracts import logger
from src.pipelines.contracts import IOType, PipeInput, PipeInputSpec, PipeOutputSpec, PipeConfigSpec
from src.pipelines.outputs import Icon, ProgressGenerationOutput
from src.platform.runtime.device import clear_gpu_memory
from src.platform.runtime.native.memory.residency import effective_free_vram_gb
from src.pipelines.pipes._shared.generation.generator_base import BaseGeneratorPipe, GeneratorContext
from src.pipelines.pipes._shared.generation.dit_placement import (
    dit_is_fully_resident,
    guard_sampling_oom,
    place_dit_for_sequence,
    sequence_fit,
)
from src.pipelines.pipes._shared.generation.ltx_conditioned_forward import ConditionedAVForward
from src.pipelines.pipes._shared.generation.dit_restore import restore_dit_best_effort
from src.pipelines.pipes._shared.generation.loader_helpers import (
    active_loras as _active_loras,
    load_lora_stack as _load_lora_stack,
)
from src.pipelines.pipes._shared.generation.guidance_options import (
    apg_settings_config_specs,
    apg_settings_overrides,
    build_multimodal_guider_params,
    multimodal_guider_config_specs,
    sampler_step_cache_config_specs,
    sampler_step_cache_kwargs,
    schedule_settings_config_specs,
    schedule_settings_overrides,
)
from src.pipelines.pipes._shared.media.video_encode import encode_frames_to_mp4
from src.pipelines.pipes._shared.vae.ltx_tiled_decode import decode_fits_beside
from src.pipelines.pipes._shared.vae.ltx_tiled_encode import encode_with_oom_retry
from src.pipelines.pipes._shared.generation.nag_inputs import attach_nag
from src.pipelines.pipes._shared.media.inputs import as_flag
from src.pipelines.pipes._shared.video.frames import load_video_frames, probe_video_stream, to_frames_tensor
from src.pipelines.pipes._shared.video.idle_encoder import release_idle_te
from src.pipelines.pipes._shared.video.ltx_common import (
    decode_ltx_video,
    parse_explicit_sigmas,
    snap_ltx_geometry,
    validate_ltx_schedule_config,
)
from src.pipelines.pipes._shared.video.ltx_constants import (
    LTX_LATENT_CHANNELS,
    LTX_SPATIAL_DOWNSCALE,
    LTX_TEMPORAL_DOWNSCALE,
)
from src.pipelines.pipes._shared.video.video_common import build_video_output, emit_video_results, to_device
from src.pipelines.pipes.generator.video_ltx.audio import audio_token_count, decode_generated_audio
from src.pipelines.pipes.generator.video_ltx.ic_lora import resolve_reference_scales
from src.pipelines.pipes.generator.video_ltx.windows import (
    crossfade_windows,
    largest_fitting_frames,
    min_window_frames,
    plan_windows,
    snap_frames_up,
)
from src.pipelines.pipes.generator.video_ltx.conditioning import (
    pack_tokens,
    LTXMediaCondition,
    merge_initial_latent_tokens,
    mix_initial_noise,
    prepare_ltx_conditions,
    PreparedConditioning,
    reference_token_count,
)
from src.pipelines.pipes._shared.generation.config_specs import (
    device_config_spec,
    nag_config_specs,
    preview_config_spec,
    preview_decoder_config_spec,
    seed_config_spec,
)

Tensor = torch.Tensor


def _resolve_latent_index(frame: Any, frames: int) -> int:
    """Placement ``frame`` (pixel index, ``"first"``, ``"last"``) -> latent index
    (0 = first-frame overwrite; -1 = last, resolved by the conditioning builder).

    ``frame`` is computed upstream from an UNSNAPPED duration (e.g. preset
    pipeline.yml's ``at * fps``), while ``frames`` here is the SNAPPED (1+8k)
    count -- so a keyframe placed at or near clip end can land past the
    snapped total (e.g. 123 >= 121 after 125->121 snapping). Clamp to the
    last valid pixel index rather than raising; only a genuinely negative
    index (a caller bug, not a rounding artifact) is still rejected.
    """
    if frame in ("first", 0, "0", None):
        return 0
    if frame == "last":
        return -1
    f = int(frame)
    if f < 0:
        return f  # negative latent indices resolve modulo t_lat downstream
    f = min(f, frames - 1)
    return (f - 1) // LTX_TEMPORAL_DOWNSCALE + 1


MAX_FRAMES = 1001


def _fit_pixel_budget(width: int, height: int, max_pixels: int, grid: int = LTX_SPATIAL_DOWNSCALE) -> Tuple[int, int]:
    if max_pixels <= 0 or width * height <= max_pixels:
        if grid <= LTX_SPATIAL_DOWNSCALE:
            return width, height
        return max(grid, round(width / grid) * grid), max(grid, round(height / grid) * grid)
    scale = math.sqrt(max_pixels / float(width * height))
    return max(grid, int(width * scale) // grid * grid), max(grid, int(height * scale) // grid * grid)


def _end_anchored_latent_index(clip_frames: int, frames: int) -> int:
    t_lat = (frames - 1) // LTX_TEMPORAL_DOWNSCALE + 1
    used = max(1, min(clip_frames, frames))
    span = (used - 1) // LTX_TEMPORAL_DOWNSCALE
    return max(0, t_lat - 1 - span)


def _matched_clip_index(placements: List[dict]) -> Optional[int]:
    clips = [p for p in placements if p.get("source") == "video"]
    clip = next((p for p in clips if p.get("role", "keyframe") == "keyframe"), clips[0] if clips else None)
    return int(clip.get("index", 0)) if clip is not None else None


@dataclass
class _VideoLtxCtx:
    bundle: Any
    sampling_settings: dict
    conditioning: list
    prepared: PreparedConditioning
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
    audio_mode: str = "none"          # "none" | "generate" | "file" | "passthrough"
    audio_file: Optional[Any] = None  # str/Path (file) or an already-decoded AudioTrack (passthrough)
    audio_tokens: int = 0
    t_lat: int = 0
    h_lat: int = 0
    w_lat: int = 0
    # Per-seed seed latents for a stage-2 refine pass. Empty = ordinary
    # video/director generation, unchanged.
    initial_latents: list = field(default_factory=list)
    # Loaded (state_dict, strength) stack for `stage2_loras` -- built once in
    # build_context (not per seed) and applied/removed around each seed's
    # sampling call in generate_one via temporarily_applied_loras. Empty =
    # no stage2_loras configured, or configured without an `initial_latent`
    # (ignored with a warning; see build_context).
    stage2_lora_stack: list = field(default_factory=list)
    scoped_lora_stack: list = field(default_factory=list)
    # True when `prepared` was built from real media conditions (not the
    # all-zero t2v fallback) -- decides whether an `initial_latent` refine
    # MERGES into the base slice (keyframes re-applied at this call's own
    # resolution) or wholesale REPLACES it (plain refine, prior
    # behavior). See generate_one and build_context.
    has_conditions: bool = False
    windows: list = field(default_factory=list)
    total_frames: int = 0

    def release_gpu(self) -> None:
        """Best-effort GPU cleanup on a failed generation: offload whichever
        of the DiT / video VAE / audio VAE / vocoder may be resident
        mid-sampling. Picked up by `BaseGeneratorPipe._release_gpu_on_error`
        (this dataclass IS `ctx.extra`) for whatever the sampling loop's own
        cleanup doesn't cover -- most notably a VAE decode failure. Never
        raises -- cleanup that raised would mask the original generation
        failure (mirrors txt2vid_wan22's `_WanCtx.release_gpu`; this pipe
        additionally carries the audio VAE/vocoder txt2vid_ltx does not)."""
        for model in (self.bundle.dit, self.bundle.vae,
                      getattr(self.bundle, "audio_vae", None), getattr(self.bundle, "vocoder", None)):
            if model is None:
                continue
            try:
                model.offload()
            except Exception:
                pass


class GeneratorLtxVideoPipe(BaseGeneratorPipe):
    name = "generator"
    description = "Native LTX-2/2.3 video-director generator (media conditioning, IC-LoRA references, optional audio)"

    @classmethod
    def configuration(cls) -> List[PipeConfigSpec]:
        return [
            PipeConfigSpec("mode", str, "video", "Generation mode", required=True, choices=["video"]),
            PipeConfigSpec("decode", bool, True,
                           "Decode to video; set false to emit the raw latent "
                           "for a downstream latent_upscaler/refine stage instead", required=False),
            PipeConfigSpec("refine_sigmas", str, "",
                           "Explicit sigma schedule for a refine pass, comma-separated, "
                           "descending, used VERBATIM (unlike manual_sigmas, the head is not forced to "
                           "1.0) -- required when 'initial_latent' is connected", required=False),
            PipeConfigSpec("stage2_loras", list, [],
                           "LoRAs applied ONLY around this call's sampling, ADDED on top of the "
                           "generation LoRA stack already baked/resident on the shared DiT (e.g. the "
                           "distilled LoRA for a two-stage refine). Only meaningful when 'initial_latent' "
                           "is connected (a stage-2 refine call) -- ignored with a warning otherwise",
                           required=False),
            PipeConfigSpec("scoped_loras", list, [],
                           "LoRAs applied ONLY around this call's sampling, on every call (not just a "
                           "refine), and removed again afterwards so the next stage sees the DiT without "
                           "them. The Director's IC-LoRAs ride here so a stage-2 refine runs without "
                           "them unless asked to keep them", required=False),
            PipeConfigSpec("refine_reference", bool, False,
                           "On a stage-2 refine call, keep IC-LoRA reference placements and re-encode "
                           "them at this call's own resolution as reference tokens. Off (default) drops "
                           "them: the refine is a plain pass over the upscaled latent", required=False),
            PipeConfigSpec("steps", int, 24, "Denoising steps", required=False, min_value=1, max_value=100),
            PipeConfigSpec("cfg", float, 4.0, "True CFG scale", required=False, min_value=1.0, max_value=20.0),
            PipeConfigSpec("sampler", str, "euler", "Sampler (conditioned runs support only single-step, "
                           "no-history samplers -- see module docstring). Euler CFG++ (deterministic) pairs with "
                           "the Manual Sigmas field on the Advanced tab to reproduce Lightricks' own first-party "
                           "distilled-refine recipe (distilled LoRA ~0.5 strength, CFG 1.0, no ancestral noise). "
                           "Euler Ancestral CFG++ is a community (ComfyUI-workflow) variant of the same recipe "
                           "that adds ancestral noise injection on top. 'euler_ancestral' is LTX-2.5's own "
                           "stage-1 sampler (stochastic, eta=1.0 by default) -- pair it with "
                           "schedule='ltx_dynamic' for the matching resolution-aware sigma shift.", required=False,
                           choices=["euler", "euler_ancestral", "euler_ancestral_cfg_pp", "euler_cfg_pp"]),
            PipeConfigSpec("resolution", str, "768x512", "Resolution (WxH)", required=False),
            PipeConfigSpec("frames", int, 49, "Number of video frames (must be 1 + 8*k; up to ~40s at 25fps)",
                           required=False, min_value=1, max_value=MAX_FRAMES),
            PipeConfigSpec("fps", float, 25.0, "Output frame rate", required=False, min_value=1.0, max_value=60.0),
            PipeConfigSpec("match_video", bool, False,
                           "Take the resolution and frame rate from the first video clip in 'media_placements' "
                           "(a keyframe clip, else an IC-LoRA reference). With no 'extend_seconds', the frame "
                           "count is the clip's own, up to the frame limit and what one pass fits in free VRAM "
                           "('frames' is ignored). On a refine call ('initial_latent' connected) the size comes "
                           "from the latent and only the frame rate from the clip",
                           required=False),
            PipeConfigSpec("match_scale", float, 1.0,
                           "With match_video, multiply the clip's size by this before the pixel budget "
                           "(2.0 for a 2x upscaler)", required=False, min_value=0.25, max_value=4.0),
            PipeConfigSpec("max_pixels", int, 0,
                           "With match_video, scale the clip's size down to at most this many pixels; 0 = no cap",
                           required=False, min_value=0),
            PipeConfigSpec("windows", bool, False,
                           "With match_video and only IC-LoRA reference clips, a clip that one pass cannot fit "
                           "in free VRAM is processed in the fewest equal overlapping windows that do and "
                           "crossfaded, up to the frame limit, instead of being cut", required=False),
            PipeConfigSpec("refine_scale", float, 1.0,
                           "With match_video on a decode=false pass, the spatial scale of the refine pass its latent "
                           "feeds (2.0 behind a 2x latent upscaler): the clip length is chosen so that pass fits in "
                           "free VRAM too", required=False, min_value=1.0, max_value=4.0),
            PipeConfigSpec("window_overlap", int, 16,
                           "With windows, frames shared by neighbouring windows and crossfaded",
                           required=False, min_value=0, max_value=200),
            PipeConfigSpec("extend_seconds", float, 0.0,
                           "When above 0, frames = the first video keyframe clip's frames plus this many seconds",
                           required=False, min_value=0.0),
            PipeConfigSpec("quantity", int, 1, "Number of videos", required=False, min_value=1, max_value=4),
            seed_config_spec(),
            device_config_spec(),
            preview_config_spec("Emit live latent previews during sampling"),
            preview_decoder_config_spec(),
            PipeConfigSpec("media_placements", list, [],
                           "Media conditioning placements: [{source: image|video, index, frame: int|first|last|end, "
                           "strength, role: keyframe|reference}]. Empty with image inputs present = image[0] as "
                           "first frame (i2v) and image[1] (if any) as last frame (FLF).", required=False),
            PipeConfigSpec("audio", bool, False, "Produce an audio track", required=False),
            PipeConfigSpec("audio_source", str, "generate",
                           "Audio track source when audio=true: generate (AV DiT samples + decodes it here), "
                           "file (mux the user-supplied audio input), or passthrough (mux an already-finished "
                           "track from this pipe's own 'audio' output on a prior stage -- e.g. a stage-2 refine "
                           "muxing stage-1's decoded audio; requires 'initial_latent' to be connected, no "
                           "re-decode/re-generation)",
                           required=False, choices=["generate", "file", "passthrough"]),
            PipeConfigSpec("cfg_zero_star", bool, True, "CFG-Zero*: rescale the uncond branch onto cond before extrapolation (free quality correction)", required=False),
            PipeConfigSpec("zero_init_steps", int, 0, "CFG-Zero*: return a zero velocity prediction for the first N steps", required=False,
                           min_value=0, max_value=100),
            *nag_config_specs(),
            *apg_settings_config_specs(),
            *sampler_step_cache_config_specs(),
            *schedule_settings_config_specs(),
            *multimodal_guider_config_specs(),
        ]

    @classmethod
    def validate_config(cls, config: Dict[str, Any]) -> None:
        validate_ltx_schedule_config(config, pipe_id="generator/video_ltx")

    @classmethod
    def inputs(cls) -> List[PipeInputSpec]:
        return [
            PipeInputSpec("model", IOType.MODEL, True, "LTX model bundle", is_array=False),
            PipeInputSpec("conditioning", IOType.CONDITIONING, True, "Encoded prompt conditioning", is_array=True),
            PipeInputSpec("image", IOType.IMAGE, False, "Conditioning images (keyframes / first / last)", is_array=True),
            PipeInputSpec("video", IOType.VIDEO, False, "Conditioning clips (keyframe clips / IC-LoRA references)", is_array=True),
            PipeInputSpec("audio", IOType.AUDIO, False, "User audio track (muxed when audio_source=file)", is_array=True),
            PipeInputSpec("seed", IOType.SEED, False, "Random seeds", is_array=True),
            PipeInputSpec("initial_latent", IOType.LATENT, False,
                          "Seed latent(s) for a stage-2 refine pass, one per seed. May be combined "
                          "with image-sourced media_placements (reapplied at this call's own "
                          "resolution); video-sourced and role='reference' conditioning are rejected "
                          "alongside it. Absent -> ordinary video/director generation", is_array=True),
            PipeInputSpec("MODELS", IOType.SERVICE, False,
                          "Model lifecycle service, to release the idle TE's host RAM", is_array=False),
        ]

    @classmethod
    def outputs(cls) -> List[PipeOutputSpec]:
        return [
            PipeOutputSpec("video", IOType.VIDEO, "Generated videos (empty when decode=false)", is_array=True),
            PipeOutputSpec("latent", IOType.LATENT, "Raw per-seed latents (only populated when decode=false)",
                           is_array=True),
            PipeOutputSpec("audio", IOType.AUDIO,
                           "Decoded/passthrough audio track, one per seed (populated whenever audio=true, "
                           "independent of decode) -- wire into a stage-2 refine's own 'audio' input with "
                           "audio_source='passthrough' to mux without regenerating", is_array=True),
        ]

    # -- context -----------------------------------------------------------

    def _build_conditions(self, placements: List[dict], images: List[Any], videos: List[Any],
                          frames: int, reference_scales: Tuple[int, int] = (1, 1),
                          video_start: int = 0, pad_video: Optional[int] = None) -> List[LTXMediaCondition]:
        if not placements:
            # Convenience defaults: image[0] -> first frame (i2v), image[1] -> last (FLF).
            placements = []
            if images:
                placements.append({"source": "image", "index": 0, "frame": "first", "strength": 1.0})
            if len(images) > 1:
                placements.append({"source": "image", "index": 1, "frame": "last", "strength": 1.0})

        conditions: List[LTXMediaCondition] = []
        for p in placements:
            source = p.get("source", "image")
            idx = int(p.get("index", 0))
            role = p.get("role", "keyframe")
            strength = float(p.get("strength", 1.0))
            if source == "image":
                if idx >= len(images):
                    raise ValueError(f"media placement references image[{idx}] but only {len(images)} provided")
                media_frames = to_frames_tensor(images[idx])
            elif source == "video":
                if idx >= len(videos):
                    raise ValueError(f"media placement references video[{idx}] but only {len(videos)} provided")
                media_frames = load_video_frames(videos[idx], frames, start=video_start)
                missing = frames - int(media_frames.shape[0])
                if idx == pad_video and missing > 0:
                    media_frames = torch.cat([media_frames, media_frames[-1:].expand(missing, *media_frames.shape[1:])])
            else:
                raise ValueError(f"unknown media placement source {source!r}")
            if role == "reference":
                conditions.append(LTXMediaCondition(
                    frames=media_frames, latent_index=0, strength=strength, role=role,
                    downscale_factor=reference_scales[0], temporal_scale_factor=reference_scales[1]))
                continue
            frame = p.get("frame", "first")
            if frame == "end":
                latent_index = _end_anchored_latent_index(int(media_frames.shape[0]), frames)
            else:
                latent_index = _resolve_latent_index(frame, frames)
            conditions.append(LTXMediaCondition(
                frames=media_frames, latent_index=latent_index, strength=strength, role=role))
        return conditions

    def _geometry_from_clip(self, placements: List[dict], videos: List[Any], width: int, height: int,
                            fps: float, frames: int, grid: int = LTX_SPATIAL_DOWNSCALE,
                            latent: Optional[Any] = None) -> Tuple[int, int, float, int, int]:
        match = as_flag(self.config.get("match_video", False))
        extend = float(self.config.get("extend_seconds") or 0.0)
        if not match and extend <= 0:
            return width, height, fps, frames, 0
        index = _matched_clip_index(placements)
        if (index is None or index >= len(videos)) and latent is not None and extend <= 0:
            return (int(latent.shape[-1]) * LTX_SPATIAL_DOWNSCALE, int(latent.shape[-2]) * LTX_SPATIAL_DOWNSCALE,
                    fps, frames, 0)
        if index is None or index >= len(videos):
            raise ValueError("generator/video_ltx: 'match_video'/'extend_seconds' need a video clip in 'media_placements'")
        clip_width, clip_height, clip_fps, clip_frames = probe_video_stream(videos[index])
        if match:
            fps = min(max(clip_fps, 1.0), 60.0) if clip_fps > 0 else fps
            if latent is not None:
                return (int(latent.shape[-1]) * LTX_SPATIAL_DOWNSCALE, int(latent.shape[-2]) * LTX_SPATIAL_DOWNSCALE,
                        fps, frames, clip_frames)
            scale = float(self.config.get("match_scale", 1.0))
            width, height = _fit_pixel_budget(
                round(clip_width * scale), round(clip_height * scale), int(self.config.get("max_pixels") or 0), grid)
        if extend > 0:
            frames = clip_frames + round(extend * fps)
            if frames > MAX_FRAMES:
                logger.warning(
                    "[GENERATOR VIDEO-LTX] extension needs %d frames; clamped to the %d-frame limit", frames, MAX_FRAMES)
                frames = MAX_FRAMES
        return width, height, fps, frames, clip_frames

    def _sequence_tokens(self, placements: List[dict], frames: int, width: int, height: int,
                         reference_scales: Tuple[int, int]) -> int:
        t_lat = (frames - 1) // LTX_TEMPORAL_DOWNSCALE + 1
        frame_tokens = (height // LTX_SPATIAL_DOWNSCALE) * (width // LTX_SPATIAL_DOWNSCALE)
        tokens = t_lat * frame_tokens
        for p in placements:
            video = p.get("source") == "video"
            if p.get("role", "keyframe") == "reference":
                tokens += reference_token_count(frames if video else 1, height, width, *reference_scales)
            elif p.get("frame", "first") not in ("first", 0, "0", None):
                tokens += (t_lat if video else 1) * frame_tokens
        return tokens

    def _plan_clip(self, dit: Any, device: str, clip_frames: int, width: int, height: int, fps: float,
                   placements: List[dict], reference_scales: Tuple[int, int], audio_mode: str,
                   overlap: Optional[int], refine_scale: float = 1.0) -> Tuple[int, List[int]]:
        total = max(1, min(clip_frames, MAX_FRAMES))
        one = snap_frames_up(total)
        fit = sequence_fit(dit, device)
        if fit is None:
            return one, [0]
        sizes = [(width, height)]
        if refine_scale != 1.0:
            grid = LTX_SPATIAL_DOWNSCALE
            sizes.append((max(grid, round(width * refine_scale / grid) * grid),
                          max(grid, round(height * refine_scale / grid) * grid)))

        def tokens(frames: int, size: Tuple[int, int]) -> Tuple[int, int]:
            audio = audio_token_count(frames, fps) if audio_mode == "generate" else 0
            return self._sequence_tokens(placements, frames, size[0], size[1], reference_scales), audio

        floor = min_window_frames(overlap) if overlap is not None else 1
        checks = (fit.resident, fit.activations_fit) if overlap is not None else (fit.activations_fit,)
        frames = None
        for check in checks:
            frames = largest_fitting_frames(
                one, lambda f, check=check: all(check(*tokens(f, size)) for size in sizes), floor)
            if frames is not None:
                break
        if frames is None:
            frames = one if overlap is None else min(one, floor)
        length, starts = (frames, [0]) if overlap is None else plan_windows(total, frames, overlap)
        logger.info("[GENERATOR VIDEO-LTX] clip of %d frame(s): %d pass(es) of %d frame(s), %d tokens each",
                    clip_frames, len(starts), length, sum(tokens(length, sizes[0])))
        return length, starts

    def build_context(self, pipe_input: PipeInput) -> GeneratorContext:
        bundle = pipe_input.input["model"]
        conditioning = pipe_input.input["conditioning"] or []
        seeds = pipe_input.input.get("seed", [])
        images = pipe_input.input.get("image") or []
        videos = pipe_input.input.get("video") or []
        audio_files = pipe_input.input.get("audio") or []
        # `or []` is unusable here: a bare Tensor raises on truthiness. The
        # upstream latent_upscaler may deliver a single Tensor or a list.
        raw_initial = pipe_input.input.get("initial_latent")
        if raw_initial is None:
            initial_latents = []
        elif isinstance(raw_initial, (list, tuple)):
            initial_latents = list(raw_initial)
        else:
            initial_latents = [raw_initial]

        placements = list(self.config.get("media_placements") or [])
        if initial_latents:
            keep_reference = bool(self.config.get("refine_reference", False))
            dropped = [] if keep_reference else [p for p in placements if p.get("role") == "reference"]
            if dropped:
                logger.info(
                    "[GENERATOR VIDEO-LTX] stage-2 refine: dropping %d IC-LoRA reference placement(s) -- "
                    "the reference only steers the first pass, the refine runs on the upscaled latent",
                    len(dropped))
                placements = [p for p in placements if p.get("role") != "reference"]
            if dropped and not placements:
                images = []

        if bundle.spec.family != "ltx":
            raise ValueError(
                f"generator/video_ltx: loaded model '{bundle.spec.family}/{bundle.spec.variant}' "
                f"is not an LTX-2/2.3 checkpoint. Pick an LTX DiT for this preset."
            )

        release_idle_te(bundle, pipe_input.input.get("MODELS"), "GENERATOR VIDEO-LTX")

        steps = int(self.config.get("steps", 24))
        cfg = float(self.config.get("cfg", 4.0))
        sampler = self.config.get("sampler", "euler")
        quantity = int(self.config.get("quantity", 1))
        frames = int(self.config.get("frames", 49))
        fps = float(self.config.get("fps", 25.0))
        device = self.config.get("device", "cuda")
        dtype = bundle.dit.compute_dtype

        resolution = str(self.config.get("resolution", "768x512")).split("x")
        width, height = int(resolution[0]), int(resolution[1])
        stage2_loras_cfg = _active_loras(self.config.get("stage2_loras"))
        scoped_loras_cfg = _active_loras(self.config.get("scoped_loras"))
        reference_scales = (1, 1)
        if any(p.get("role") == "reference" for p in placements):
            reference_scales = resolve_reference_scales(
                [lora["file_path"] for lora in (*scoped_loras_cfg, *stage2_loras_cfg)])
        width, height, fps, frames, clip_frames = self._geometry_from_clip(
            placements, videos, width, height, fps, frames,
            grid=LTX_SPATIAL_DOWNSCALE * reference_scales[0],
            latent=initial_latents[0] if initial_latents else None)
        width, height, frames = snap_ltx_geometry(width, height, frames)

        # A stage-2 refine's temporal geometry comes from the latent it was
        # actually handed, not from re-snapping `duration*fps` again --
        # `initial_latents[i]` is `[1, C, F, H, W]`. Re-deriving `frames` here
        # (BEFORE `_build_conditions`/`prepare_ltx_conditions` below) means
        # every downstream consumer of `frames` -- keyframe latent_index
        # clamping (`_resolve_latent_index`), `prepare_ltx_conditions`'s own
        # `t_lat` recompute (conditioning.py), and this method's own `t_lat`
        # below -- agrees with the latent by construction, instead of a
        # config-vs-latent mismatch surfacing as a token-count crash after the
        # (expensive) stage-1 pass already ran.
        t_lat_from_latent: Optional[int] = None
        if initial_latents:
            t_lat_from_latent = int(initial_latents[0].shape[-3])
            for i, lat in enumerate(initial_latents[1:], start=1):
                lat_t_lat = int(lat.shape[-3])
                if lat_t_lat != t_lat_from_latent:
                    raise ValueError(
                        f"generator/video_ltx: initial_latent[{i}] has {lat_t_lat} latent "
                        f"frame(s) but initial_latent[0] has {t_lat_from_latent} -- every "
                        f"seed in one stage-2 refine call must share the same latent "
                        f"temporal geometry (they came from the same upstream stage)"
                    )
            frames = (t_lat_from_latent - 1) * LTX_TEMPORAL_DOWNSCALE + 1

        audio_mode = "none"
        audio_file: Optional[Any] = None
        if bool(self.config.get("audio", False)):
            audio_mode = str(self.config.get("audio_source", "generate"))
            if audio_mode == "file":
                if not audio_files:
                    raise ValueError(
                        "generator/video_ltx: audio_source='file' but no audio input was provided")
                audio_file = audio_files[0]
            elif audio_mode == "passthrough":
                if not initial_latents:
                    raise ValueError(
                        "generator/video_ltx: audio_source='passthrough' only makes sense on a stage-2 "
                        "refine call ('initial_latent' connected) -- it muxes a prior stage's already-"
                        "decoded audio output verbatim, without regenerating; use audio_source='generate' "
                        "or 'file' for a plain (single-stage) generation instead"
                    )
                if not audio_files:
                    raise ValueError(
                        "generator/video_ltx: audio_source='passthrough' but no audio input was provided "
                        "(expected the upstream stage's own 'audio' output wired into this pipe's "
                        "'audio' input)")
                audio_file = audio_files[0]
            elif getattr(bundle, "audio_vae", None) is None or getattr(bundle, "vocoder", None) is None:
                raise ValueError(
                    "generator/video_ltx: audio generation requested but the model bundle has no "
                    "audio VAE / vocoder — enable audio on the model loader (model_loader/ltx "
                    "config 'audio: true') so it loads the checkpoint's audio components."
                )

        window_starts = [0]
        total_frames = frames
        clip_length = (as_flag(self.config.get("match_video", False))
                       and float(self.config.get("extend_seconds") or 0.0) <= 0)
        from_clip = clip_length and not initial_latents
        if clip_length and initial_latents:
            total_frames = min(frames, clip_frames)
        if from_clip:
            if clip_frames > MAX_FRAMES:
                logger.warning("[GENERATOR VIDEO-LTX] input clip has %d frames; only the first %d are used",
                               clip_frames, MAX_FRAMES)
            windowed = (as_flag(self.config.get("windows", False)) and bool(self.config.get("decode", True))
                        and placements and audio_mode != "generate"
                        and all(p.get("source") == "video" and p.get("role") == "reference" for p in placements))
            frames, window_starts = self._plan_clip(
                bundle.dit, device, clip_frames, width, height, fps, placements, reference_scales, audio_mode,
                int(self.config.get("window_overlap", 16)) if windowed else None,
                float(self.config.get("refine_scale") or 1.0))
            total_frames = min(clip_frames, MAX_FRAMES, window_starts[-1] + frames)
        pad_video = _matched_clip_index(placements) if clip_length else None
        conditions = self._build_conditions(placements, images, videos, frames, reference_scales,
                                            pad_video=pad_video)
        windows: List[Tuple[int, PreparedConditioning]] = []
        self._trim_notice = None
        if clip_length and clip_frames > total_frames:
            self._trim_notice = f"Only the first {total_frames / fps:.1f} seconds of the input clip were used."

        # `frames` was already reconciled to the latent above (when present),
        # so this recompute would land on the same value by construction --
        # pin directly from the latent's own shape anyway, rather than lean
        # on that arithmetic identity, so this stays correct even if the
        # reconciliation above is ever changed independently.
        t_lat = t_lat_from_latent if t_lat_from_latent is not None else (frames - 1) // LTX_TEMPORAL_DOWNSCALE + 1
        h_lat = height // LTX_SPATIAL_DOWNSCALE
        w_lat = width // LTX_SPATIAL_DOWNSCALE

        if conditions:
            bundle.vae.move_to(device)

            def vae_encode(pixels: Tensor) -> Tensor:
                return encode_with_oom_retry(
                    bundle.vae, pixels, device,
                    profiler_mark="video_ltx.condition_encode", log_prefix="generator/video_ltx",
                )

            causal_fix = bool(getattr(bundle.spec, "causal_temporal_positioning", True)) \
                if hasattr(bundle.spec, "causal_temporal_positioning") \
                else bool(getattr(bundle.dit.module.config, "causal_temporal_positioning", True))
            prepared = prepare_ltx_conditions(
                conditions, vae_encode, frames=frames, height=height, width=width,
                device=device, dtype=dtype, latent_channels=LTX_LATENT_CHANNELS, causal_fix=causal_fix)
            for start in window_starts[1:]:
                window_conditions = self._build_conditions(
                    placements, images, videos, frames, reference_scales, video_start=start, pad_video=pad_video)
                windows.append((start, prepare_ltx_conditions(
                    window_conditions, vae_encode, frames=frames, height=height, width=width,
                    device=device, dtype=dtype, latent_channels=LTX_LATENT_CHANNELS, causal_fix=causal_fix)))
            bundle.vae.offload()
        else:
            s_base = t_lat * h_lat * w_lat
            prepared = PreparedConditioning(
                tokens=torch.zeros((1, s_base, LTX_LATENT_CHANNELS), device=device, dtype=dtype),
                mask=torch.zeros((1, s_base), device=device, dtype=dtype),
                clean=torch.zeros((1, s_base, LTX_LATENT_CHANNELS), device=device, dtype=dtype),
                extra_coords=None, n_extra=0, base_tokens=s_base,
            )

        audio_tokens = audio_token_count(frames, fps) if audio_mode == "generate" else 0

        stage2_lora_stack: List[Tuple[Dict[str, Any], float]] = []
        if stage2_loras_cfg:
            if not initial_latents:
                logger.warning(
                    "[GENERATOR VIDEO-LTX] 'stage2_loras' is configured but 'initial_latent' is not "
                    "connected (this call is not a stage-2 refine) -- ignoring stage2_loras"
                )
            else:
                stage2_lora_stack = _load_lora_stack(stage2_loras_cfg)

        scoped_lora_stack = _load_lora_stack(scoped_loras_cfg) if scoped_loras_cfg else []

        spec = bundle.spec
        # APG: read straight out of sampling_settings by _make_guidance. No
        # SLG -- LTXAVModel.forward has no skip_layers kwarg (Wan-only).
        sampling_settings = {
            **spec.sampling_settings,
            **apg_settings_overrides(self.config),
            **schedule_settings_overrides(self.config),
        }

        logger.info(
            "[GENERATOR VIDEO-LTX] %s: %d frame(s) @ %dx%d, %d steps, cfg %.1f, "
            "%d condition(s) (%d appended tokens), reference scale spatial 1/%d temporal 1/%d, audio=%s",
            spec.variant, frames, width, height, steps, cfg,
            len(conditions), prepared.n_extra, reference_scales[0], reference_scales[1], audio_mode,
        )

        # Stashed for emit_results() below: process() doesn't thread ctx through
        # to emit_results, and the (post-snap) width/height are only known here.
        self._video_resolution = (width, height)
        # Per-seed audio results, appended by generate_one() in the same
        # index order as `results` -- build_output() reads this back for the
        # "audio" output (see that method).
        self._audio_results: list = []

        return GeneratorContext(
            quantity=quantity,
            input_seeds=seeds,
            extra=_VideoLtxCtx(
                bundle=bundle, sampling_settings=sampling_settings, conditioning=conditioning,
                prepared=prepared, steps=steps, cfg=cfg, sampler=sampler,
                width=width, height=height, frames=frames, fps=fps,
                device=device, dtype=dtype, spec=spec,
                audio_mode=audio_mode, audio_file=audio_file, audio_tokens=audio_tokens,
                t_lat=t_lat, h_lat=h_lat, w_lat=w_lat,
                initial_latents=initial_latents, has_conditions=bool(conditions),
                stage2_lora_stack=stage2_lora_stack, scoped_lora_stack=scoped_lora_stack,
                windows=[(0, prepared), *windows] if windows else [], total_frames=total_frames,
            ),
        )

    # -- per-seed generation ----------------------------------------------

    def generate_one(self, ctx: GeneratorContext, index: int, seed: int, progress) -> Any:
        c: _VideoLtxCtx = ctx.extra
        if not c.windows:
            return self._generate(ctx, c, index, seed, progress)
        chunks = [
            (start, self._generate(ctx, replace(c, prepared=prepared), index, seed, progress, frames_only=True,
                                   window=(i, len(c.windows))))
            for i, (start, prepared) in enumerate(c.windows)
        ]
        frames_np = crossfade_windows(chunks, c.total_frames)
        audio_track = c.audio_file if c.audio_mode in ("file", "passthrough") else None
        self._audio_results.append(audio_track)
        out_path = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False).name
        encode_frames_to_mp4(frames_np, out_path, fps=c.fps, audio=audio_track)
        if index == ctx.quantity - 1:
            restore_dit_best_effort(c.bundle.dit, c.device)
        return out_path

    def _decode_fits_beside_dit(self, c: _VideoLtxCtx, video_latent: Tensor) -> bool:
        if c.audio_mode == "generate" or not dit_is_fully_resident(c.bundle.dit, c.device):
            return False
        return decode_fits_beside(c.bundle.vae, video_latent, effective_free_vram_gb(c.device))

    def _generate(self, ctx: GeneratorContext, c: _VideoLtxCtx, index: int, seed: int, progress,
                  frames_only: bool = False, window: Tuple[int, int] = (0, 1)) -> Any:
        cond_model = c.conditioning[index] if index < len(c.conditioning) else c.conditioning[-1]
        cond = to_device(cond_model.embeds, c.device, c.dtype)
        uncond = to_device(cond_model.n_embeds, c.device, c.dtype) if cond_model.n_embeds else None
        cond = attach_nag(cond, uncond, self.config)

        # A stage-2 refine pass seeds the base-token slice of `prepared` from
        # an upstream (upsampled) latent -- see module docstring for the two
        # cases (wholesale replace vs. mask-weighted merge).
        if c.initial_latents:
            init_latent_5d = c.initial_latents[index] if index < len(c.initial_latents) else c.initial_latents[-1]
            packed = pack_tokens(init_latent_5d.to(device=c.device, dtype=c.dtype))
            if packed.shape[1] != c.prepared.base_tokens:
                # Temporal geometry (t_lat) is derived from this very latent
                # (build_context), so it can no longer be the source of a
                # mismatch here -- what's left is a genuine SPATIAL disagreement
                # (or, with media conditioning attached, an n_extra one): the
                # latent's H/W doesn't match this stage's configured
                # 'resolution', or the appended-conditioning token count drifted.
                raise ValueError(
                    f"generator/video_ltx: initial_latent token count {packed.shape[1]} does not match "
                    f"the configured resolution's base token count {c.prepared.base_tokens} "
                    f"(n_extra={c.prepared.n_extra}) -- frame count is derived from the latent itself, "
                    f"so this means the latent's spatial resolution (H/W) doesn't match this stage's "
                    f"configured 'resolution' (WxH)"
                )
            if c.has_conditions:
                # Keyframe/reference conditioning was re-encoded at THIS call's
                # resolution (build_context), so `prepared` already holds the
                # right keyframe tokens/mask/extras for the target grid --
                # merge, don't replace: masked (keyframe-anchored) positions
                # keep the re-encoded keyframe tokens, unmasked positions take
                # the upsampled prior latent. `clean` stays `prepared.clean`
                # unmodified: both conditioning blend sites in
                # `ConditionedAVForward` (model-input clamp, x0-space blend)
                # weight `clean` by `mask`, so its value at mask=0 (unmasked,
                # upsampled-prior) positions is never read -- only the mask=1
                # keyframe anchors matter, and those are exactly what
                # `prepared.clean` already holds.
                tokens = merge_initial_latent_tokens(c.prepared, packed)
                prepared = replace(c.prepared, tokens=tokens)
            else:
                prepared = replace(c.prepared, tokens=packed, clean=packed)

            # `manual_sigmas`/build_sigmas's "manual" mode always forces
            # sigmas[0]=1.0 -- that would silently turn this refine back
            # into a full regeneration (mix_initial_noise's blend ignores
            # `clean` entirely at sigma0=1.0). `refine_sigmas` is required
            # instead: parsed VERBATIM, bypassing that forcing.
            raw_refine_sigmas = str(self.config.get("refine_sigmas") or "").strip()
            if not raw_refine_sigmas:
                raise ValueError(
                    "generator/video_ltx: 'initial_latent' is connected but 'refine_sigmas' is empty -- "
                    "set an explicit descending sigma schedule (sigma[0] < 1.0) for the refine pass"
                )
            sigmas = parse_explicit_sigmas(raw_refine_sigmas)
        else:
            # image_seq_len: the base (resolution-only, pre-conditioning)
            # video token count -- only consumed by sampling_settings['schedule']
            # == 'ltx_dynamic' (see flow_schedule.py's _ltx_dynamic_shift_sigmas);
            # every other schedule mode ignores it. Deliberately base_tokens, NOT
            # base_tokens + n_extra: the diffusers reference derives its own
            # "video_seq_len" from height/width/num_frames alone, before any
            # keyframe/reference conditioning tokens are appended.
            prepared = c.prepared
            sigmas = conditioned_sigmas(c.steps, c.sampling_settings, image_seq_len=prepared.base_tokens)
        sigma0 = float(sigmas[0])

        s_video = prepared.base_tokens + prepared.n_extra
        gen = torch.Generator(device=c.device).manual_seed(int(seed))
        # euler_ancestral (LTX-2.5 stage-1): a DEDICATED generator, offset from
        # the request seed -- see txt2vid_ltx.generate_one's identical comment.
        sampler_gen = gen
        if c.sampler == "euler_ancestral":
            sampler_gen = torch.Generator(device=c.device).manual_seed(int(seed) + ANCESTRAL_NOISE_SEED_OFFSET)
        noise_v = torch.randn((1, s_video, LTX_LATENT_CHANNELS), generator=gen, device=c.device, dtype=c.dtype)
        x = mix_initial_noise(prepared, noise_v, sigma0)
        if c.audio_tokens:
            noise_a = torch.randn((1, c.audio_tokens, 128), generator=gen, device=c.device, dtype=c.dtype)
            x = torch.cat([x, noise_a], dim=1)

        # Sequence-length-aware placement -- see
        # dit_placement.py's module docstring. video_tokens covers base +
        # appended conditioning tokens (media keyframes / IC-LoRA references);
        # audio_tokens covers the appended audio stream when audio generation
        # is on -- both ride the same packed sampler state ``x``.
        placement = place_dit_for_sequence(
            c.bundle.dit, c.device, video_tokens=s_video, audio_tokens=c.audio_tokens,
            own_models=tuple(m for m in (c.bundle.dit, c.bundle.vae, c.bundle.audio_vae, c.bundle.vocoder) if m is not None),
        )
        # `replace(c, prepared=prepared)`, NOT `c` itself: `c` (ctx.extra) is
        # SHARED across every seed of this process() call (BaseGeneratorPipe's
        # seed loop calls generate_one once per seed against the same ctx) --
        # ConditionedAVForward must see THIS seed's own `prepared` (built
        # above from the pristine `c.prepared`, never written back into it),
        # not a mutated shared copy a previous seed left behind.
        forward = ConditionedAVForward(c.bundle.dit.module, replace(c, prepared=prepared))

        # MultiModalGuider (quality recipe): build from pipe config; when
        # active, supply video_tokens boundary in cond for per-slice combine.
        mm_params = build_multimodal_guider_params(self.config)
        guidance_override = None
        if mm_params is not None:
            from src.platform.runtime.native.sampling.multimodal_guider import MultiModalGuidance
            video_params, audio_params = mm_params
            guidance_override = MultiModalGuidance(video_params, audio_params if c.audio_tokens else None)
            # Inject slice metadata into cond so the guider knows where video ends / audio begins.
            cond["mm_video_tokens"] = s_video
            if uncond is not None:
                uncond["mm_video_tokens"] = s_video
            logger.debug("[GENERATOR VIDEO-LTX] quality_mode ON (MultiModalGuider): "
                        "video cfg=%.1f stg=%.1f rescale=%.2f modality=%.1f, "
                        "audio cfg=%.1f, stg_blocks=%s",
                        video_params.cfg_scale, video_params.stg_scale,
                        video_params.rescale_scale, video_params.modality_scale,
                        audio_params.cfg_scale if c.audio_tokens else 0.0,
                        video_params.stg_blocks)

        def on_progress(_frac, step_index, total):
            done, count = window
            progress.step(done * total + step_index + 1, count * total, state="VIDEO",
                          icon=Icon(name="film", effect="pulse"))

        logger.debug("[GENERATOR VIDEO-LTX] video %d/%d, seed %d, state tokens %s (base %d + extra %d + audio %d)",
                    index + 1, ctx.quantity, seed, tuple(x.shape),
                    prepared.base_tokens, prepared.n_extra, c.audio_tokens)
        hooks = [ProgressHook(on_progress), StepPlanHook(lambda i, n: {"attn": "dense"})]
        if self.config.get("preview", True):
            preview_hook = make_preview_hook(c.spec, progress.preview, emit_clip=progress.preview_clip, latent_transform=forward.unpack_base, decoder_path=self.config.get("preview_decoder") or None)
            if preview_hook is not None:
                hooks.append(preview_hook)

        # `stage2_lora_stack` (built once in build_context) is applied ONLY
        # around this call -- the entire denoise loop, every per-step DiT
        # forward, but NOT the VAE decode below -- and restored exactly on
        # exit (including on an exception), additive with whatever LoRA
        # stack the model loader already baked/resident onto this shared DiT
        # (project rule: refinement shares the generation LoRA chain, this
        # only ADDS to it). Empty stack (no stage2_loras, or not a refine
        # call) -> no-op, byte-identical to before this feature existed.
        sample_forward = guard_sampling_oom(
            forward, dit=c.bundle.dit, device=c.device, decision=placement,
        )
        with temporarily_applied_loras(
                c.bundle.dit.module, [*c.scoped_lora_stack, *c.stage2_lora_stack], runtime_only=True):
            x = denoise_prenoised(
                sample_forward, x, cond, uncond,
                steps=c.steps, sampler_name=c.sampler,
                sampling_settings=c.sampling_settings, guidance_scale=c.cfg,
                sigmas=sigmas, hooks=hooks, is_cancelled=ctx.is_cancelled,
                cfg_zero_star=bool(self.config.get("cfg_zero_star", True)),
                zero_init_steps=int(self.config.get("zero_init_steps", 0)),
                guidance_override=guidance_override,
                # Seed determinism (stochastic samplers): see `sampler_gen`'s
                # construction above -- previously omitted here entirely, so
                # euler_ancestral_cfg_pp on this pipe fell back to the UNSEEDED
                # global RNG (ensure_sampler_generator's docstring) rather than
                # a reproducible per-seed stream.
                **sampler_step_cache_kwargs(self.config, sampler=c.sampler, generator=sampler_gen),
            )
        video_latent = forward.unpack_base(x)
        decode = bool(self.config.get("decode", True))
        keep_dit = (frames_only or decode) and self._decode_fits_beside_dit(c, video_latent)
        if not keep_dit:
            c.bundle.dit.offload()
        clear_gpu_memory()
        keep_resident = (c.bundle.dit,) if keep_dit else ()

        # Audio is decoded independently of the video `decode` flag below --
        # the audio VAE + vocoder round trip has no dependency on the video
        # VAE, so a decode=false (two-stage upscale) stage-1 call still
        # produces a finished `audio` output for a downstream stage-2 refine
        # to mux (see module docstring's `decode`/`audio` bullets). "file" and
        # "passthrough" both just carry `audio_file` through verbatim (a str/
        # Path for a user file, or an already-decoded AudioTrack handed down
        # from a prior stage) -- only "generate" does real work here.
        audio_slice = x[:, s_video:] if c.audio_tokens else None
        audio_track = None
        if c.audio_mode == "generate" and audio_slice is not None:
            audio_track = decode_generated_audio(c.bundle, audio_slice, c.device)
        elif c.audio_mode in ("file", "passthrough") and c.audio_file:
            audio_track = c.audio_file
        if frames_only:
            return decode_ltx_video(c.bundle.vae, c.device, video_latent, seed, keep_resident=keep_resident)
        self._audio_results.append(audio_track)

        if not decode:
            # Hand the raw latent to a downstream latent_upscaler/refine stage --
            # no VAE decode/mp4-encode round trip (audio, above, already ran).
            # See txt2vid_ltx's identical branch for the restore_dit_best_effort
            # reasoning.
            if index == ctx.quantity - 1:
                restore_dit_best_effort(c.bundle.dit, c.device)
            return video_latent

        frames_np = decode_ltx_video(c.bundle.vae, c.device, video_latent, seed, keep_resident=keep_resident)
        if 0 < c.total_frames < frames_np.shape[0]:
            frames_np = frames_np[:c.total_frames]
        out_path = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False).name
        encode_frames_to_mp4(frames_np, out_path, fps=c.fps, audio=audio_track)

        # Best-effort warm-start for the NEXT generation -- see
        # txt2vid_ltx.generate_one's identical comment for the full reasoning
        # (only on the last seed of this invocation; after decode + VAE
        # offload so this video's own wall-clock is unaffected).
        if index == ctx.quantity - 1:
            restore_dit_best_effort(c.bundle.dit, c.device)

        return out_path

    def emit_results(self, generation_outputs: callable, results: List[Any], used_seeds: List[int]) -> None:
        if not bool(self.config.get("decode", True)):
            return  # an internal latent hand-off, no gallery/seed emission here
        emit_video_results(generation_outputs, results, used_seeds, resolution=getattr(self, "_video_resolution", None))
        notice = getattr(self, "_trim_notice", None)
        if notice:
            generation_outputs(ProgressGenerationOutput(state=notice, icon=Icon("warning"), bar=False))

    def build_output(self, results: List[Any]) -> Dict[str, Any]:
        audio = list(getattr(self, "_audio_results", []))
        if not bool(self.config.get("decode", True)):
            return {"latent": results, "video": [], "audio": audio}
        return {**build_video_output(results), "latent": [], "audio": audio}
