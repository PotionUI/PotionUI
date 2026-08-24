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
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import torch

from src.platform.runtime.native.lora import temporarily_applied_loras
from src.platform.runtime.native.sampling import (
    ANCESTRAL_NOISE_SEED_OFFSET,
    ProgressHook,
    conditioned_sigmas,
    denoise_prenoised,
    make_preview_hook,
)
from src.pipelines.contracts import logger
from src.pipelines.contracts import IOType, PipeInput, PipeInputSpec, PipeOutputSpec, PipeConfigSpec
from src.pipelines.outputs import Icon
from src.platform.runtime.device import clear_gpu_memory
from src.pipelines.pipes._shared.generation.generator_base import BaseGeneratorPipe, GeneratorContext
from src.pipelines.pipes._shared.generation.dit_placement import place_dit_for_sequence
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
from src.pipelines.pipes.generator.txt2vid_ltx.main import (
    _LATENT_CHANNELS,
    _SPATIAL_DOWNSCALE,
    _TEMPORAL_DOWNSCALE,
    _decode_video,
    _snap_geometry,
    parse_explicit_sigmas,
    release_idle_te,
    validate_ltx_schedule_config,
)
from src.pipelines.pipes.generator.txt2vid_wan22.main import (
    _attach_nag,
    _build_video_output,
    _emit_video_results,
    _to_device,
)
from src.pipelines.pipes.generator.video_ltx.audio import audio_token_count, decode_generated_audio
from src.pipelines.pipes.generator.video_ltx.conditioning import (
    LTXMediaCondition,
    PreparedConditioning,
    _pack,
    merge_initial_latent_tokens,
    mix_initial_noise,
    prepare_ltx_conditions,
)

Tensor = torch.Tensor


def _to_frames_tensor(image: Any) -> Tensor:
    """PIL image / HWC array -> ``(1, H0, W0, 3)`` float32 in [0, 1]."""
    if hasattr(image, "convert"):  # PIL
        arr = torch.from_numpy(np.array(image.convert("RGB"))).float() / 255.0
    else:
        arr = torch.as_tensor(image, dtype=torch.float32)
        if arr.max() > 1.5:
            arr = arr / 255.0
    if arr.ndim == 3:
        arr = arr.unsqueeze(0)
    return arr


def _load_video_frames(path: str, max_frames: int) -> Tensor:
    """Read up to ``max_frames`` frames from a video file -> ``(n, H, W, 3)``
    float32 in [0, 1]."""
    import cv2  # local import: cv2 is heavyweight and test environments stub it

    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise ValueError(f"generator/video_ltx: cannot open video file {path!r}")
    frames: List[np.ndarray] = []
    try:
        while len(frames) < max_frames:
            ok, frame = cap.read()
            if not ok:
                break
            frames.append(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
    finally:
        cap.release()
    if not frames:
        raise ValueError(f"generator/video_ltx: no frames decoded from {path!r}")
    # `.float().div_(255.0)` (in-place divide), NOT `.float() / 255.0`: the
    # non-in-place `/` allocates a SECOND full-size fp32 buffer before the
    # first one is freed (transient 2x peak) -- for a long/high-res source
    # (the standalone upscale mode reads up to 1001 frames) that doubling is
    # multiple GB of avoidable host-RAM pressure. `div_` mutates
    # the tensor `.float()` just allocated, so only ONE fp32 buffer ever exists.
    return torch.from_numpy(np.stack(frames)).float().div_(255.0)


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
    return (f - 1) // _TEMPORAL_DOWNSCALE + 1


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
    # True when `prepared` was built from real media conditions (not the
    # all-zero t2v fallback) -- decides whether an `initial_latent` refine
    # MERGES into the base slice (keyframes re-applied at this call's own
    # resolution) or wholesale REPLACES it (plain refine, prior
    # behavior). See generate_one and build_context.
    has_conditions: bool = False

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
    def get_default_config(cls) -> Dict[str, Any]:
        return {
            "mode": "video",
            "steps": 24,
            "cfg": 4.0,
            "sampler": "euler",
            "resolution": "768x512",
            "frames": 49,
            "fps": 25.0,
            "quantity": 1,
            "seed": -1,
            "device": "cuda",
            "preview": True,
            "media_placements": [],
            "audio": False,
            "audio_source": "generate",
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
            "stage2_loras": [],
        }

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
                           required=False, min_value=1, max_value=1001),
            PipeConfigSpec("fps", float, 25.0, "Output frame rate", required=False, min_value=1.0, max_value=60.0),
            PipeConfigSpec("quantity", int, 1, "Number of videos", required=False, min_value=1, max_value=4),
            PipeConfigSpec("seed", int, -1, "Random seed", required=False, min_value=-1),
            PipeConfigSpec("device", str, "cuda", "Compute device", required=False, choices=["cuda", "cpu"]),
            PipeConfigSpec("preview", bool, True, "Emit live latent previews during sampling", required=False),
            PipeConfigSpec("media_placements", list, [],
                           "Media conditioning placements: [{source: image|video, index, frame: int|first|last, "
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
                          frames: int) -> List[LTXMediaCondition]:
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
                media_frames = _to_frames_tensor(images[idx])
            elif source == "video":
                if idx >= len(videos):
                    raise ValueError(f"media placement references video[{idx}] but only {len(videos)} provided")
                media_frames = _load_video_frames(videos[idx], frames)
            else:
                raise ValueError(f"unknown media placement source {source!r}")
            latent_index = 0 if role == "reference" else _resolve_latent_index(p.get("frame", "first"), frames)
            conditions.append(LTXMediaCondition(
                frames=media_frames, latent_index=latent_index, strength=strength, role=role))
        return conditions

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
        if initial_latents and videos:
            raise ValueError(
                "generator/video_ltx: 'initial_latent' (stage-2 refine) cannot be combined with "
                "video-sourced media conditioning -- reapplying video keyframe/IC-LoRA-reference "
                "conditioning at a refined resolution is unvalidated here; use image-sourced "
                "keyframes for a stage-2 refine instead"
            )

        if bundle.spec.family != "ltx":
            raise ValueError(
                f"generator/video_ltx: loaded model '{bundle.spec.family}/{bundle.spec.variant}' "
                f"is not an LTX-2/2.3 checkpoint. Pick an LTX DiT for this preset."
            )

        # TE eviction: this pipe's own media-conditioning VAE-encode below
        # (`_build_conditions` -> `prepare_ltx_conditions`) uses `bundle.vae`,
        # never the TE -- conditioning text was already produced by
        # `prompt_encoder`, upstream of every LTX preset pipeline. Safe on
        # BOTH generator_stage1 and generator_stage2 (same pipe class, two
        # node instances reading the same `model` bundle in the in-flow
        # two-stage pipeline): stage 2 never re-encodes text either, so a
        # second call here is a harmless no-op (already evicted by stage 1).
        # See `release_idle_te`'s docstring.
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
        width, height, frames = _snap_geometry(width, height, frames)

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
            frames = (t_lat_from_latent - 1) * _TEMPORAL_DOWNSCALE + 1

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

        conditions = self._build_conditions(placements, images, videos, frames)

        if initial_latents and any(c.role == "reference" for c in conditions):
            raise ValueError(
                "generator/video_ltx: 'initial_latent' (stage-2 refine) cannot be combined with "
                "role='reference' (IC-LoRA) conditioning -- IC-LoRA reference semantics are tied "
                "to the distilled first-pass pipeline and are not validated for a stage-2 refine"
            )

        # `frames` was already reconciled to the latent above (when present),
        # so this recompute would land on the same value by construction --
        # pin directly from the latent's own shape anyway, rather than lean
        # on that arithmetic identity, so this stays correct even if the
        # reconciliation above is ever changed independently.
        t_lat = t_lat_from_latent if t_lat_from_latent is not None else (frames - 1) // _TEMPORAL_DOWNSCALE + 1
        h_lat = height // _SPATIAL_DOWNSCALE
        w_lat = width // _SPATIAL_DOWNSCALE

        if conditions:
            bundle.vae.move_to(device)
            vae_module = bundle.vae.module

            def vae_encode(pixels: Tensor) -> Tensor:
                with torch.no_grad():
                    return vae_module.encode(pixels.to(dtype=bundle.vae.compute_dtype))

            causal_fix = bool(getattr(bundle.spec, "causal_temporal_positioning", True)) \
                if hasattr(bundle.spec, "causal_temporal_positioning") \
                else bool(getattr(bundle.dit.module.config, "causal_temporal_positioning", True))
            prepared = prepare_ltx_conditions(
                conditions, vae_encode, frames=frames, height=height, width=width,
                device=device, dtype=dtype, latent_channels=_LATENT_CHANNELS, causal_fix=causal_fix)
            bundle.vae.offload()
        else:
            s_base = t_lat * h_lat * w_lat
            prepared = PreparedConditioning(
                tokens=torch.zeros((1, s_base, _LATENT_CHANNELS), device=device, dtype=dtype),
                mask=torch.zeros((1, s_base), device=device, dtype=dtype),
                clean=torch.zeros((1, s_base, _LATENT_CHANNELS), device=device, dtype=dtype),
                extra_coords=None, n_extra=0, base_tokens=s_base,
            )

        audio_tokens = audio_token_count(frames, fps) if audio_mode == "generate" else 0

        stage2_loras_cfg = _active_loras(self.config.get("stage2_loras"))
        stage2_lora_stack: List[Tuple[Dict[str, Any], float]] = []
        if stage2_loras_cfg:
            if not initial_latents:
                logger.warning(
                    "[GENERATOR VIDEO-LTX] 'stage2_loras' is configured but 'initial_latent' is not "
                    "connected (this call is not a stage-2 refine) -- ignoring stage2_loras"
                )
            else:
                stage2_lora_stack = _load_lora_stack(stage2_loras_cfg)

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
            "%d condition(s) (%d appended tokens), audio=%s",
            spec.variant, frames, width, height, steps, cfg,
            len(conditions), prepared.n_extra, audio_mode,
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
                stage2_lora_stack=stage2_lora_stack,
            ),
        )

    # -- per-seed generation ----------------------------------------------

    def generate_one(self, ctx: GeneratorContext, index: int, seed: int, progress) -> Any:
        c: _VideoLtxCtx = ctx.extra
        cond_model = c.conditioning[index] if index < len(c.conditioning) else c.conditioning[-1]
        cond = _to_device(cond_model.embeds, c.device, c.dtype)
        uncond = _to_device(cond_model.n_embeds, c.device, c.dtype) if cond_model.n_embeds else None
        # NAG: see txt2vid_ltx's generate_one -- uncond["context"] is already
        # the post-apply_text_conditioning negative context, so _attach_nag
        # (family-agnostic, keys off "context" alone) is reused unmodified.
        cond = _attach_nag(cond, uncond, self.config)

        # A stage-2 refine pass seeds the base-token slice of `prepared` from
        # an upstream (upsampled) latent -- see module docstring for the two
        # cases (wholesale replace vs. mask-weighted merge).
        if c.initial_latents:
            init_latent_5d = c.initial_latents[index] if index < len(c.initial_latents) else c.initial_latents[-1]
            packed = _pack(init_latent_5d.to(device=c.device, dtype=c.dtype))
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
        noise_v = torch.randn((1, s_video, _LATENT_CHANNELS), generator=gen, device=c.device, dtype=c.dtype)
        x = mix_initial_noise(prepared, noise_v, sigma0)
        if c.audio_tokens:
            noise_a = torch.randn((1, c.audio_tokens, 128), generator=gen, device=c.device, dtype=c.dtype)
            x = torch.cat([x, noise_a], dim=1)

        # Sequence-length-aware placement -- see
        # dit_placement.py's module docstring. video_tokens covers base +
        # appended conditioning tokens (media keyframes / IC-LoRA references);
        # audio_tokens covers the appended audio stream when audio generation
        # is on -- both ride the same packed sampler state ``x``.
        place_dit_for_sequence(
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
            progress.step(step_index + 1, total, state="VIDEO", icon=Icon(name="film", effect="pulse"))

        logger.debug("[GENERATOR VIDEO-LTX] video %d/%d, seed %d, state tokens %s (base %d + extra %d + audio %d)",
                    index + 1, ctx.quantity, seed, tuple(x.shape),
                    prepared.base_tokens, prepared.n_extra, c.audio_tokens)
        hooks = [ProgressHook(on_progress)]
        if self.config.get("preview", True):
            preview_hook = make_preview_hook(c.spec, progress.preview, latent_transform=forward.unpack_base)
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
        with temporarily_applied_loras(c.bundle.dit.module, c.stage2_lora_stack):
            x = denoise_prenoised(
                forward, x, cond, uncond,
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
        c.bundle.dit.offload()
        clear_gpu_memory()

        video_latent = forward.unpack_base(x)

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
        self._audio_results.append(audio_track)

        decode = bool(self.config.get("decode", True))
        if not decode:
            # Hand the raw latent to a downstream latent_upscaler/refine stage --
            # no VAE decode/mp4-encode round trip (audio, above, already ran).
            # See txt2vid_ltx's identical branch for the restore_dit_best_effort
            # reasoning.
            if index == ctx.quantity - 1:
                restore_dit_best_effort(c.bundle.dit, c.device)
            return video_latent

        frames_np = _decode_video(_DecodeShim(c), video_latent, seed)
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
        _emit_video_results(generation_outputs, results, used_seeds, resolution=getattr(self, "_video_resolution", None))

    def build_output(self, results: List[Any]) -> Dict[str, Any]:
        audio = list(getattr(self, "_audio_results", []))
        if not bool(self.config.get("decode", True)):
            return {"latent": results, "video": [], "audio": audio}
        return {**_build_video_output(results), "latent": [], "audio": audio}


@dataclass
class _DecodeShim:
    """Adapter for ``txt2vid_ltx._decode_video`` which expects a ctx exposing
    ``vae`` and ``device``."""

    _src: _VideoLtxCtx

    @property
    def vae(self):
        return self._src.bundle.vae

    @property
    def device(self):
        return self._src.device
