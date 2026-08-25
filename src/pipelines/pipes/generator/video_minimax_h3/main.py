# Derived from: diffusers `modular_pipelines/minimax_h3/denoise.py`
# (`MiniMaxH3LoopDenoiser`, `MiniMaxH3LoopSchedulerStep`) and `decoders.py`
# (`MiniMaxH3VideoDecodeStep`, `MiniMaxH3AudioDecodeStep`), Apache-2.0,
# "Copyright 2026 The MiniMax and HuggingFace Teams" -- the per-step forward
# call shape, the row-slice scheduler update (condition rows excluded), and
# the video/audio decode+denormalize recipes are ported near-verbatim into a
# bespoke loop (see below for why).
"""Native MiniMax-H3 video+audio generator: `t2va` (text only), `fl2va`
(optional first/last keyframe images) and `ref2va` (image, video and audio
references), same checkpoint, same pipe.

**The `ref2va` ordering contract.** The three reference inputs
(`reference_images`/`reference_videos`/`reference_audios`) are collapsed into
ONE packed order by `_shared.generation.reference_order.pack_references` --
images, then videos, then audio -- and everything downstream is derived from
that single traversal: the `ReferenceMedia` list this pipe normalizes and
encodes, the `ReferenceBlock` list it hands the layout, and the two condition-
latent iterators the layout consumes alongside it. `prompt_encoder` derives
the text encoder's presentation order from the SAME function. A divergence
between any two of those is silent -- the request runs, the shapes agree, and
every reference conditions the generation from another reference's position.

**Sampling-loop choice: bespoke, not `denoise`/`denoise_prenoised`.** Every
other native family's sampler assumes ONE sigma schedule, ONE state tensor,
and a `cond`/`uncond` guidance strategy. MiniMax-H3 has none of that shape:

- TWO independent schedules (video shift 12.0, audio shift 3.0) advance
  together, same step count, inside ONE transformer call per step -- the row-
  timestep VECTOR built by `layout.build_row_timesteps` is what makes one
  forward serve both. The shared sampler machinery has no seam for a second,
  differently-shifted schedule.
- The velocity sign is reversed and `t = 1 - sigma` (see `schedule.py`'s
  module docstring) -- a fully different scheduler algebra, not a
  parameterization of the shared one.
- Guidance is "none" (distilled): no `cond`/`uncond`, no CFG, no guider
  strategy object to plug in.
- Conditioning (keyframe) rows are excluded from the scheduler update by a
  ROW SLICE (`rows[num_condition_rows:]`), not a mask blend the way LTX's
  `ConditionedAVForward` does it -- the reference's own contract (dossier
  §B: "Conditioning rows are returned unmasked -- masking them out ... is the
  caller's job").

A bespoke loop reproduces the reference's `MiniMaxH3LoopDenoiser` +
`MiniMaxH3LoopSchedulerStep` pair directly and stays simple; `ProgressHook`/
preview hooks are still dispatched through the shared `sampling.hooks.
run_hooks` isolation helper so a preview failure can never break generation,
same guarantee every other family's loop gets from `denoise()`.

**Two-stage `decode`/`audio` contract** (mirrors LTX's `generator/video_ltx`):
`decode=false` skips VAE decode/mp4 encode and returns the raw video latent
via the `latent` output instead; the `audio` output is still populated
independent of `decode` (a later stage can mux it without re-sampling).

**Refine entry path** (`initial_latent`, native equivalent of a ComfyUI
"detailer" second pass): connecting a raw, un-normalized video latent -- a
`latent_upscaler`'s output, same idiom as `generator/txt2vid_ltx`'s own
`initial_latent` -- switches the request onto a refine, mutually exclusive
with `document`/`image`/every reference input (dossier: a refine has no
keyframe/reference overlay to combine with). `resolution`/`frames` are then
DERIVED from the latent's own shape rather than read from config, exactly as
`txt2vid_ltx` derives them. `denoise` (default `1.0`, a no-op) truncates the
schedule the ComfyUI `BasicScheduler` way (see `schedule.build_t_grid`);
`video_sigma_shift` (default `schedule.VIDEO_SHIFT`) lets the refine run its
video stream at a different shift than a fresh generation without touching
the audio stream's own (always `AUDIO_SHIFT`). The initial latent is noised
up to the truncated schedule's first kept sigma with `schedule.scale_noise`
-- the same math `prepare_keyframe_condition_rows` already uses for a
keyframe anchor, reused rather than reinvented.

**Audio on a refine.** This pipe does not lock the target audio rows clean
during a refine (that needs a THIRD row-timestep category the packed-
sequence/scheduler machinery has no seam for today, on top of the existing
condition/target split -- out of scope here, see the port notes). The
pragmatic route instead: audio samples normally over the truncated schedule,
and a caller that wants to preserve the source clip's lipsync sets
`audio_source="passthrough"` with the existing `audio` input wired to the
source track, which mutes this pipe's own audio and mux'es the source's
verbatim (`_resolve_audio`'s existing "file"/"passthrough" branch -- no new
machinery needed for this).
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

import numpy as np
import torch

from src.pipelines.contracts import logger
from src.pipelines.contracts import IOType, PipeInput, PipeInputSpec, PipeOutputSpec, PipeConfigSpec
from src.pipelines.outputs import Icon
from src.platform.runtime.device import clear_gpu_memory
from src.platform.runtime.native.errors import SamplingCancelled
from src.platform.runtime.native.sampling.hooks import ProgressHook, run_hooks
from src.platform.runtime.native.sampling.preview import make_preview_hook
from src.platform.runtime.native.sampling.step_cache import FirstBlockCache, normalize_options
from src.platform.runtime.native.sla_attn import SlaAttnContext, build_sla_attn_context
from src.platform.runtime.native.sla_attn import estimate_transient_gb as sla_estimate_transient_gb
from src.platform.runtime.native.sol_attn import SolAttnContext, build_sol_attn_context
from src.platform.runtime.native.sol_attn import estimate_transient_gb as sol_estimate_transient_gb
from src.pipelines.pipes._shared.generation.generator_base import BaseGeneratorPipe, GeneratorContext, emit_gallery
from src.pipelines.pipes._shared.generation.dit_placement import place_dit_for_sequence
from src.pipelines.pipes._shared.generation.dit_restore import restore_dit_best_effort
from src.pipelines.pipes._shared.generation.reference_order import pack_references
from src.pipelines.pipes._shared.media.pixel_convert import pixels_3thw_to_uint8_frames
from src.pipelines.pipes._shared.media.video_encode import AudioInput, AudioTrack, encode_frames_to_mp4
from src.pipelines.pipes._shared.media.video_read import read_video_frames
from src.pipelines.pipes.generator.txt2vid_ltx.main import release_idle_te
from src.pipelines.pipes.generator.video_minimax_h3.audio import (
    AUDIO_CHANNELS,
    AUDIO_SAMPLE_RATE,
    decode_generated_audio,
    pack_audio_rows,
    unpack_audio_rows,
)
from src.pipelines.pipes.generator.video_minimax_h3.conditioning import (
    MAX_AUDIO_REFERENCES,
    MAX_IMAGE_REFERENCES,
    MAX_REFERENCES,
    MAX_VIDEO_REFERENCES,
    ReferenceMedia,
    normalize_references,
    prepare_keyframe_condition_rows,
    prepare_reference_conditioning,
)
from src.pipelines.pipes.generator.video_minimax_h3.geometry import (
    FPS,
    audio_latent_num_frames,
    pixel_frames_for_latent_frames,
    resolve_request_geometry,
    video_latent_num_frames,
)
from src.pipelines.pipes.generator.video_minimax_h3.layout import (
    PackedLayout,
    build_packed_sequence,
    build_ref2va_packed_sequence,
    build_row_timesteps,
    patchify_video_latents,
    unpatchify_video_rows,
)
from src.pipelines.pipes.generator.video_minimax_h3.samplers import SAMPLERS, make_stepper
from src.pipelines.pipes.generator.video_minimax_h3.schedule import (
    KEYFRAME_NOISE_AUG,
    SCHEDULERS,
    SIMPLE_SCHEDULER,
    VIDEO_SHIFT,
    data_estimate,
    parse_manual_sigmas,
    resolve_schedules,
    scale_noise,
)
from src.pipelines.pipes.generator.video_minimax_h3.windows import (
    DirectorPlan,
    DirectorPlanError,
    WindowPlan,
    build_director_plan,
)

Tensor = torch.Tensor

VIDEO_LATENT_CHANNELS = 24
AUDIO_LATENT_CHANNELS = 32
PATCH_SIZE = (1, 2, 2)

# Attention inner dim (56 heads x 128) vs. residual-stream width (5376) --
# `place_dit_for_sequence`'s activation-reserve formula is sized off the
# WIDER of the two (dossier §A.1: "attn inner dim ... is wider than the
# residual stream" -- attention Q/K/V buffers and the packed hidden-state
# tensor both scale with whichever is larger).
H3_NUM_HEADS = 56
H3_HEAD_DIM = 128
H3_ATTN_INNER_DIM = H3_NUM_HEADS * H3_HEAD_DIM
H3_HIDDEN_SIZE = 5376
H3_INNER_DIM = max(H3_ATTN_INNER_DIM, H3_HIDDEN_SIZE)
# SwiGLU FFN inner width (dossier §A.5/§A.1) -- `place_dit_for_sequence`'s
# `ffn_dim` folds in the fc1 fused value|gate + SiLU + product transient,
# which the attention-shaped terms above don't cover at all. Root cause of a
# real turbo-LoRA OOM: with this term absent, the reserve undercounted by
# enough that a configuration that should have streamed the DiT (partial
# residency) was instead placed fully resident, and died mid-sampling on
# exactly this allocation (`_ffn_transient_bytes_per_token`'s docstring).
H3_FFN_DIM = 14336

_VALID_ANCHORS = ("first", "last")
_VALID_AUDIO_SOURCES = ("generate", "file", "passthrough")
_VALID_MODES = ("video", "references")
# `MiniMaxH3Ref2VASetupStep`'s own per-modality defaults (before_encoder.py),
# re-exported from `conditioning` so the early request-shape check and the
# late `validate_references` can never bound different numbers. Validation
# bounds, not hard architectural limits.
_MAX_REFERENCE_IMAGES = MAX_IMAGE_REFERENCES
_MAX_REFERENCE_VIDEOS = MAX_VIDEO_REFERENCES
_MAX_REFERENCE_AUDIOS = MAX_AUDIO_REFERENCES


def _load_reference_video(video_path: Any) -> ReferenceMedia:
    """One `ref2va` video reference's file path -> its raw frames and native
    frame rate.

    `media_loader` hands videos over as PATHS (it decodes only images), so the
    decode lands here. The frames are RAW: `normalize_references` is what puts
    them on H3's 24 fps and canvas, and it needs the source rate to do it.
    """
    frames, fps = read_video_frames(video_path)
    return ReferenceMedia(kind="video", frames=frames, fps=float(fps))


def _load_reference_audio(audio_path: Any) -> ReferenceMedia:
    """One `ref2va` audio reference's file path -> a `(channels, samples)`
    float32 waveform at its native sample rate.

    More than two channels are downmixed to mono here rather than at the
    resampler: `normalize_condition_waveform` accepts mono or stereo only, and
    upmixing a mono average is a defined operation where dropping four of six
    channels is a silent choice about which ones matter.
    """
    import soundfile as sf

    samples, sample_rate = sf.read(str(audio_path), dtype="float32", always_2d=True)
    waveform = torch.from_numpy(np.ascontiguousarray(samples.T))
    if waveform.shape[0] > AUDIO_CHANNELS:
        waveform = waveform.mean(dim=0, keepdim=True)
    return ReferenceMedia(kind="audio", audio=waveform, sample_rate=int(sample_rate))


def validate_minimax_h3_config(config: Dict[str, Any], *, pipe_id: str) -> None:
    """Static (pipe_input-independent) cross-field checks, run before the
    pipe -- and the model it needs loaded -- ever runs."""
    anchors = list(config.get("keyframe_anchors") or [])
    if len(anchors) > 2:
        raise ValueError(f"{pipe_id}: 'keyframe_anchors' takes at most 2 entries (first, last), got {len(anchors)}")
    for anchor in anchors:
        if anchor not in _VALID_ANCHORS:
            raise ValueError(f"{pipe_id}: 'keyframe_anchors' entries must be 'first'/'last', got {anchor!r}")
    if len(anchors) == 2 and anchors[0] == anchors[1]:
        raise ValueError(f"{pipe_id}: 'keyframe_anchors' cannot repeat the same anchor twice, got {anchors}")

    audio_source = config.get("audio_source", "generate")
    if audio_source not in _VALID_AUDIO_SOURCES:
        raise ValueError(
            f"{pipe_id}: 'audio_source' must be one of {_VALID_AUDIO_SOURCES}, got {audio_source!r}"
        )

    sampler = config.get("sampler", "euler")
    if sampler not in SAMPLERS:
        raise ValueError(f"{pipe_id}: 'sampler' must be one of {list(SAMPLERS)}, got {sampler!r}")

    scheduler = config.get("scheduler", SIMPLE_SCHEDULER)
    if scheduler not in SCHEDULERS:
        raise ValueError(f"{pipe_id}: 'scheduler' must be one of {list(SCHEDULERS)}, got {scheduler!r}")

    manual_video = str(config.get("manual_sigmas", "") or "")
    manual_audio = str(config.get("manual_audio_sigmas", "") or "")
    if scheduler != SIMPLE_SCHEDULER and (manual_video.strip() or manual_audio.strip()):
        raise ValueError(
            f"{pipe_id}: 'scheduler' is {scheduler!r} and a manual sigma grid is set -- the two are both "
            f"answers to where the knots go, so neither is allowed to silently win. Clear the manual list "
            f"or set 'scheduler' back to {SIMPLE_SCHEDULER!r}"
        )
    video_sigmas = parse_manual_sigmas(manual_video, label=f"{pipe_id}: 'manual_sigmas'") if manual_video.strip() else None
    audio_sigmas = parse_manual_sigmas(manual_audio, label=f"{pipe_id}: 'manual_audio_sigmas'") if manual_audio.strip() else None
    if video_sigmas is not None and audio_sigmas is not None:
        if video_sigmas.sigmas.numel() != audio_sigmas.sigmas.numel():
            raise ValueError(
                f"{pipe_id}: 'manual_sigmas' and 'manual_audio_sigmas' must list the same number of values "
                f"(got {int(video_sigmas.sigmas.numel())} and {int(audio_sigmas.sigmas.numel())}) -- both "
                f"streams advance inside one transformer call per step"
            )


def build_step_cache(config: Dict[str, Any]) -> Optional[FirstBlockCache]:
    """Resolve the three flat preset knobs into one :class:`FirstBlockCache`,
    or ``None`` when caching is off (the default).

    One cache per generation, not a `StepCacheSet`: MiniMax-H3 is guidance-
    distilled, so a step is exactly one transformer call on one trajectory --
    there is no cond/uncond pair to keep separate anchors for. A non-numeric
    knob falls back to its default instead of failing the generation.
    """
    def coerce(key: str, default, cast):
        value = config.get(key, default)
        try:
            return cast(value)
        except (TypeError, ValueError):
            logger.warning("[GENERATOR MINIMAX-H3] ignoring non-numeric %s=%r", key, value)
            return default

    options = normalize_options({
        "rel_threshold": coerce("step_cache_threshold", 0.0, float),
        "warmup_steps": coerce("step_cache_warmup_steps", 4, int),
        "max_consecutive_skips": coerce("step_cache_max_skips", 3, int),
    })
    if options["rel_threshold"] <= 0.0:
        return None
    return FirstBlockCache(**options)


def video_target_start(layout: PackedLayout) -> int:
    """Row index the packed sequence's target-video tail begins at.

    Everything before it — text, condition audio, keyframe condition rows and
    the target audio rows — is the conditioning Sol-Attn must keep exact, so
    this index IS the sink length. `layout.py` lays the sequence out as
    `[text | condition audio | keyframe conditions | target audio | target
    video]`, which makes the video target a contiguous tail; that is asserted
    here (once per window, not per step) rather than assumed, because a sink
    computed against a different row order would silently make the wrong rows
    approximate instead of failing.
    """
    tail = layout.video_indices[layout.num_condition_video_rows:]
    sequence_length = int(layout.position_ids.shape[0])
    if tail.numel() == 0:
        raise ValueError("the packed layout has no target video rows")
    start = int(tail[0])
    expected = torch.arange(start, sequence_length, device=tail.device, dtype=tail.dtype)
    if tail.numel() != expected.numel() or not torch.equal(tail, expected):
        raise ValueError(
            "generator/video_minimax_h3: the target video rows are not the packed sequence's "
            "contiguous tail, so the Sol-Attn sink prefix cannot be derived from them"
        )
    return start


def build_sparse_attn_ctx(config: Dict[str, Any], layout: PackedLayout) -> Optional[SolAttnContext | SlaAttnContext]:
    """Resolve the preset's sparse-attention method against one window's
    layout, or ``None`` when the feature is off (the default).

    ``sparse_attn`` picks ONE method: ``"sol"`` (threshold routing) or
    ``"sla"`` (top-k routing, pairs with the SLA turbo LoRA). An unrecognised
    value warns once and is treated as off rather than failing the
    generation -- a preset typo must not sink a render.

    One context per WINDOW: the prefix/sink is a row count, and a
    continuation window differs from its predecessor in frame count,
    condition rows and prompt length. The context's ``dense`` flag is then
    flipped per step by the sampling loop.

    The layout is not inspected at all when the feature is off, so a knob
    left at its default cannot fail a generation on `video_target_start`'s
    tail assertion.
    """
    method = str(config.get("sparse_attn", "off")).strip().lower()
    if method == "off":
        return None
    if method == "sol":
        return build_sol_attn_context(
            enabled=True,
            tau=config.get("sol_attn_tau", 1.0),
            sink_tokens=video_target_start(layout),
            log_prefix="GENERATOR MINIMAX-H3",
        )
    if method == "sla":
        return build_sla_attn_context(
            enabled=True,
            sparsity=config.get("sla_sparsity", 0.90),
            block_size=config.get("sla_block_size", 64),
            prefix_tokens=video_target_start(layout),
            log_prefix="GENERATOR MINIMAX-H3",
        )
    logger.warning("[GENERATOR MINIMAX-H3] ignoring unknown sparse_attn=%r, treating as off", method)
    return None


def sparse_attn_reserve_gb(ctx: Optional[SolAttnContext | SlaAttnContext], layout: PackedLayout) -> float:
    """VRAM to hold back from the DiT placement for the sparse-attention
    method's own transients.

    0.0 when the feature is off, so the placement call is what it always was.
    """
    if ctx is None:
        return 0.0
    seq_len = int(layout.position_ids.shape[0])
    if isinstance(ctx, SolAttnContext):
        return sol_estimate_transient_gb(seq_len, H3_NUM_HEADS, H3_HEAD_DIM)
    return sla_estimate_transient_gb(seq_len, H3_NUM_HEADS, H3_HEAD_DIM)


def sparse_attn_dense_last_steps(config: Dict[str, Any]) -> int:
    """How many trailing steps run dense. A non-numeric knob falls back to the
    default rather than failing the generation."""
    value = config.get("sparse_attn_dense_last_steps", 2)
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        logger.warning("[GENERATOR MINIMAX-H3] ignoring non-numeric sparse_attn_dense_last_steps=%r", value)
        return 2


def is_dense_step(step_index: int, num_steps: int, dense_last_steps: int) -> bool:
    """Whether this step runs on the normal dense attention path.

    The trailing `dense_last_steps` steps do. A window with no more steps than
    that runs entirely dense, which is the feature turning itself off rather
    than a special case to guard.
    """
    return step_index >= num_steps - dense_last_steps


def _require_h3_video_vae(module):
    """The video VAE picker shows every VAE file the depot knows, and a wrong
    pick loads cleanly under its OWN architecture (e.g. an LTX-2.5
    CausalDiffusionVAE) only to die deep in conditioning/decode with a bare
    AttributeError. Fail at first use naming the actual fix instead."""
    if not hasattr(module, "latents_mean") or not hasattr(module, "latents_std"):
        raise ValueError(
            f"The selected video VAE loaded as {type(module).__name__}, which is not a "
            "MiniMax-H3 video VAE. Pick the H3 repack (minimax_h3_video_vae_fp16.safetensors) "
            "in the Models tab - another family's VAE file loads under its own architecture "
            "and cannot run here."
        )
    return module


def _trim_audio_head(track: AudioInput, overlap_frames: int) -> AudioInput:
    """Drop the samples covering `overlap_frames` pixel frames off the front.

    The video side of a continuation window is trimmed by whole frames, so the
    audio has to lose the same DURATION, not the same number of latents -- the
    audio latent grid (40/s) and the video frame grid (24 fps) do not divide
    each other. A track that is a file path (a user-supplied mux) is returned
    untouched: it was never generated per window and has no head to trim.
    """
    if overlap_frames <= 0 or not isinstance(track, AudioTrack):
        return track
    samples = int(round(overlap_frames / FPS * track.sample_rate))
    return AudioTrack(waveform=track.waveform[:, samples:], sample_rate=track.sample_rate)


def _concat_audio_tracks(tracks: List[AudioInput]) -> AudioInput:
    """Join per-window tracks end to end, matching the stitched frames."""
    waveforms = [t.waveform for t in tracks if isinstance(t, AudioTrack)]
    if not waveforms:
        return next((t for t in tracks if t is not None), None)
    return AudioTrack(
        waveform=np.concatenate(waveforms, axis=1),
        sample_rate=next(t.sample_rate for t in tracks if isinstance(t, AudioTrack)),
    )


@dataclass
class _MiniMaxH3Forward:
    """One packed-sequence transformer call: video/audio rows -> predicted
    velocities. Owns nothing beyond that one call -- the scheduler update
    (row-sliced, per modality) lives in the loop below."""

    dit_module: Any
    layout: PackedLayout
    prompt_embeds: Tensor

    def __call__(
        self, video_rows: Tensor, audio_rows: Tensor, unique_timesteps: Tensor, timestep_indices: Tensor,
        step_cache: Optional[FirstBlockCache] = None,
        sparse_attn_ctx: Optional[SolAttnContext | SlaAttnContext] = None,
    ) -> tuple[Tensor, Tensor]:
        video_pred, audio_pred = self.dit_module(
            hidden_states=video_rows[None],
            audio_hidden_states=audio_rows[None],
            encoder_hidden_states=self.prompt_embeds,
            timestep=unique_timesteps,
            timestep_indices=timestep_indices,
            token_tags=self.layout.token_tags,
            position_ids=self.layout.position_ids,
            video_indices=self.layout.video_indices,
            audio_indices=self.layout.audio_indices,
            text_indices=self.layout.text_indices,
            step_cache=step_cache,
            sparse_attn_ctx=sparse_attn_ctx,
        )
        return video_pred[0], audio_pred[0]


@dataclass
class _MiniMaxH3Ctx:
    bundle: Any
    conditioning: list
    steps: int
    height: int
    width: int
    frames: int
    num_latent_frames: int
    latent_height: int
    latent_width: int
    num_audio_latents: int
    device: str
    dtype: torch.dtype
    spec: Any
    keyframe_images: list = field(default_factory=list)
    keyframe_anchors: tuple = ()
    # ALREADY-NORMALIZED `ref2va` references, in packed order. Normalized once
    # in `build_context` rather than per seed: the fit/resample is seed-
    # independent, and decoding a reference video once per `quantity` would
    # re-read the file for every output.
    references: tuple = ()
    audio_source: str = "generate"
    audio_file: Optional[Any] = None
    decode: bool = True
    manual_sigmas: str = ""
    manual_audio_sigmas: str = ""
    sampler: str = "euler"
    scheduler: str = SIMPLE_SCHEDULER
    # Set only for a routed multi-segment Video Director run; `None` is what
    # keeps every other request on the single-window path below.
    plan: Optional[DirectorPlan] = None
    director_images: list = field(default_factory=list)
    # Refine entry path (module docstring, "Refine entry path"). One raw
    # (un-normalized) video latent per seed, in the video VAE's own native
    # space -- `[]` is the ordinary from-noise path, unchanged.
    initial_latents: list = field(default_factory=list)
    source_frame_count: Optional[int] = None
    denoise: float = 1.0
    video_sigma_shift: float = VIDEO_SHIFT


class GeneratorMinimaxH3Pipe(BaseGeneratorPipe):
    name = "generator"
    description = "Native MiniMax-H3 t2va/fl2va video+audio generator"

    @classmethod
    def get_default_config(cls) -> Dict[str, Any]:
        return {
            "mode": "video",
            "steps": 24,
            "resolution": "1344x768",
            "frames": 124,
            "quantity": 1,
            "seed": -1,
            "device": "cuda",
            "preview": True,
            "keyframe_anchors": [],
            "audio_source": "generate",
            "decode": True,
            "step_cache_threshold": 0.0,
            "step_cache_warmup_steps": 4,
            "step_cache_max_skips": 3,
            "sparse_attn": "off",
            "sol_attn_tau": 1.0,
            "sla_sparsity": 0.90,
            "sla_block_size": 64,
            "sparse_attn_dense_last_steps": 2,
            "sampler": "euler",
            "scheduler": SIMPLE_SCHEDULER,
            "manual_sigmas": "",
            "manual_audio_sigmas": "",
            "denoise": 1.0,
            "video_sigma_shift": VIDEO_SHIFT,
            "document": None,
            "references": [],
            "reference_videos": [],
            "reference_audios": [],
        }

    @classmethod
    def configuration(cls) -> List[PipeConfigSpec]:
        return [
            PipeConfigSpec("mode", str, "video", "Generation mode. 'video' is t2va/fl2va: the request "
                           "is the prompt plus any keyframes, and a request with neither is a valid "
                           "text-only one. 'references' is ref2va: the request MUST carry at least one "
                           "reference, and a run that resolved to none is refused rather than quietly "
                           "falling back to t2va -- on a ref2va checkpoint that fallback returns "
                           "plausible video that ignores the whole point of the request",
                           required=True, choices=list(_VALID_MODES)),
            PipeConfigSpec("steps", int, 24, "Denoising steps (video and audio share the count, own schedules)",
                           required=False, min_value=2, max_value=100),
            PipeConfigSpec("resolution", str, "1344x768", "Resolution (WxH); snapped to the 32px canvas "
                           "grid and the [1:4, 4:1] aspect range", required=False),
            PipeConfigSpec("frames", int, 124, "Number of frames at the fixed 24 fps (5-15s; snapped up to "
                           "the next 17*n+5 the video VAE can decode)", required=False, min_value=120, max_value=360),
            PipeConfigSpec("quantity", int, 1, "Number of videos", required=False, min_value=1, max_value=4),
            PipeConfigSpec("seed", int, -1, "Random seed", required=False, min_value=-1),
            PipeConfigSpec("device", str, "cuda", "Compute device", required=False, choices=["cuda", "cpu"]),
            PipeConfigSpec("preview", bool, True, "Emit live latent previews during sampling", required=False),
            PipeConfigSpec("keyframe_anchors", list, [],
                           "Which end of the video each 'image' input anchors, positional with it: "
                           "['first'], ['last'] or ['first','last'] (fl2va). Empty with images present "
                           "= image[0] as first frame, image[1] (if any) as last frame; empty with no "
                           "images = t2va", required=False),
            PipeConfigSpec("audio_source", str, "generate",
                           "Audio track source: generate (decode the jointly-sampled audio latents -- "
                           "MiniMax-H3 always samples an audio stream, this only picks what the FINAL "
                           "muxed track is), file (mux the user-supplied audio input instead), or "
                           "passthrough (mux an already-decoded track from this pipe's own prior 'audio' "
                           "output, e.g. a future refine stage)", required=False, choices=list(_VALID_AUDIO_SOURCES)),
            PipeConfigSpec("decode", bool, True, "Decode to video; set false to emit the raw latent instead "
                           "(audio is still decoded/populated either way)", required=False),
            PipeConfigSpec(
                "step_cache_threshold", float, 0.0,
                "FBCache step-skipping: relative-change threshold below which the whole "
                "transformer forward is skipped and the previous step's output reused. "
                "0.0 (default) is off; ~0.08 is a conservative starting point, ~0.15 more "
                "aggressive. Only pays off on runs of 15+ steps.",
                required=False, min_value=0.0, max_value=1.0,
            ),
            PipeConfigSpec(
                "step_cache_warmup_steps", int, 4,
                "FBCache step-skipping: number of leading steps forced to fully compute "
                "(never skipped) before the cache is allowed to kick in.",
                required=False, min_value=0,
            ),
            PipeConfigSpec(
                "step_cache_max_skips", int, 3,
                "FBCache step-skipping: maximum number of consecutive steps the cache may "
                "skip before a forced recompute.",
                required=False, min_value=0,
            ),
            PipeConfigSpec(
                "sparse_attn", str, "off",
                "Sparse attention method: 'off' (default), 'sol' (Sol-Attn, threshold routing) "
                "or 'sla' (SLA, top-k routing, pairs with the SLA turbo LoRA). A speed-for-"
                "fidelity trade on long sequences, not a free win. Needs a bfloat16/float16 "
                "CUDA device with compute capability 8.0+; anywhere else it logs one warning "
                "and the generation continues on the normal attention path.",
                required=False, choices=["off", "sol", "sla"],
            ),
            PipeConfigSpec(
                "sol_attn_tau", float, 1.0,
                "Sol-Attn sparsity temperature: higher skips more KV blocks (faster, less "
                "faithful). Only has an effect when 'sparse_attn' is 'sol'.",
                required=False, min_value=0.0, max_value=3.0,
            ),
            PipeConfigSpec(
                "sla_sparsity", float, 0.90,
                "SLA: fraction of key blocks skipped by top-k routing (faster, less faithful "
                "the higher it goes). Only has an effect when 'sparse_attn' is 'sla'.",
                required=False, min_value=0.0, max_value=0.95,
            ),
            PipeConfigSpec(
                "sla_block_size", int, 64,
                "SLA: KV block width, 64 or 128. Only has an effect when 'sparse_attn' is "
                "'sla'.",
                required=False, choices=[64, 128],
            ),
            PipeConfigSpec(
                "sparse_attn_dense_last_steps", int, 2,
                "Sparse attention: number of trailing steps of each window run on the normal "
                "dense attention path. The end of a trajectory carries the least noise, so a "
                "sparse approximation there is the most visible. At or above the step count the "
                "feature is effectively off.",
                required=False, min_value=0, max_value=10,
            ),
            PipeConfigSpec(
                "sampler", str, "euler",
                "Which solver advances each stream between two grid sigmas. 'euler' is the "
                "reference first-order step. 'res_multistep' and 'dpmpp_2m' are second-order "
                "multistep solvers -- they reuse the previous step's x0 estimate, so they cost "
                "no extra model evaluations and mostly pay off at low step counts. 'sa_solver' is "
                "a deterministic order-2 predictor-corrector, another no-extra-cost option in the "
                "same family. 'er_sde' is stochastic (adds noise every step, seeded like the rest "
                "of the request) -- more diverse, less reproducible frame-to-frame than the "
                "deterministic solvers. Both streams run the chosen solver on their OWN sigma "
                "grid, and the history is per stream and per Director window.",
                required=False, choices=list(SAMPLERS),
            ),
            PipeConfigSpec(
                "scheduler", str, SIMPLE_SCHEDULER,
                "Where the schedule places its knots. 'simple' is the reference uniform grid; "
                "'beta' pushes it through a Beta(0.6, 0.6) quantile function, which clusters "
                "knots at both ends of the trajectory. The video (shift 12) and audio (shift 3) "
                "schedules are both derived from the SAME knots, so the two streams stay paired "
                "whichever scheduler runs. Cannot be combined with a manual sigma grid.",
                required=False, choices=list(SCHEDULERS),
            ),
            PipeConfigSpec(
                "manual_sigmas", str, "",
                "Explicit video sigma grid, comma-separated and strictly descending, ending at "
                "exactly 0.0 (e.g. '1.0, 0.86, 0.5, 0.0'). Empty (default) computes the grid from "
                "'steps' at shift 12.0. The list's length is the grid, so it drives one fewer "
                "model evaluation than it has values, and it OVERRIDES 'steps'.",
                required=False,
            ),
            PipeConfigSpec(
                "manual_audio_sigmas", str, "",
                "Explicit audio sigma grid, same format as 'manual_sigmas'. Empty (default) "
                "computes the grid at shift 3.0, sized to match whatever the video stream ended up "
                "with. Set both and the two lists must have the same length.",
                required=False,
            ),
            PipeConfigSpec(
                "denoise", float, 1.0,
                "Refine strength for a run seeded from the 'initial_latent' input: the fraction "
                "of the full schedule actually walked. 1.0 (default) is a full generation from "
                "pure noise, byte-identical to what 'steps' alone would produce. Below 1.0, "
                "ComfyUI's BasicScheduler convention: 'steps' knots are kept off the TAIL of a "
                "longer ceil(steps/denoise)-step schedule, so the run starts short of full noise "
                "instead of at it -- a detailer-style second pass (e.g. 4 steps, denoise 0.45). "
                "Only meaningful with 'initial_latent' connected; set otherwise is refused.",
                required=False, min_value=0.01, max_value=1.0,
            ),
            PipeConfigSpec(
                "video_sigma_shift", float, VIDEO_SHIFT,
                f"Video stream's exponential-shift constant (default {VIDEO_SHIFT:g}, the "
                "released checkpoint's own -- see 'manual_sigmas'). A refine pass over an "
                "already-upsampled latent typically runs at a lower shift (e.g. 9) than a fresh "
                "generation; the audio stream's own shift (3.0) is a separate, unaffected knob.",
                required=False, min_value=0.1, max_value=50.0,
            ),
            PipeConfigSpec(
                "document", dict, None,
                "A normalized Video Director document. With a routed multi-segment `director` "
                "document this pipe runs a SLIDING WINDOW: one full MiniMax-H3 generation per "
                "segment, each continuation window pinned to its predecessor's final latent "
                "frames, trimmed back to the overlap and stitched into one clip. Anything else "
                "(absent, or a single-shot t2v/flf document) leaves the pipe on its ordinary "
                "one-generation path, where `frames`/`resolution`/`steps` above are the request.",
                required=False,
            ),
            PipeConfigSpec(
                "references", list, [],
                "The ref2va references form field's own value, passed through untouched (a list "
                "of `{path, relative_path?, label?, ...}` dicts, one per entry, in the SAME order "
                "as the 'reference_images' input's loaded array -- same idiom as 'document' above. "
                "Used ONLY to cross-validate that the loaded 'reference_images' array actually has "
                "as many entries as the preset's references field declared, the same guard "
                "'document'/'director_image' get for Video Director; nothing here reaches the "
                "layout or the presentation, both of which read the LOADED images' own order. A "
                "per-item 'label' is a display/LLM handle (reaches the model via the chat context, "
                "not this pipe) -- never the `<Picture N>` text, which is always pipe-computed "
                "from position (see 'reference_images' below).",
                required=False,
            ),
            PipeConfigSpec(
                "reference_videos", list, [],
                "The ref2va reference-VIDEOS form field's own value, passed through untouched -- the "
                "video counterpart of 'references' above, and used for the same count cross-check "
                "against the 'reference_videos' input.",
                required=False,
            ),
            PipeConfigSpec(
                "reference_audios", list, [],
                "The ref2va reference-AUDIO form field's own value, passed through untouched -- the "
                "audio counterpart of 'references' above, and used for the same count cross-check "
                "against the 'reference_audios' input.",
                required=False,
            ),
        ]

    @classmethod
    def validate_config(cls, config: Dict[str, Any]) -> None:
        validate_minimax_h3_config(config, pipe_id="generator/video_minimax_h3")

    @classmethod
    def inputs(cls) -> List[PipeInputSpec]:
        return [
            PipeInputSpec("model", IOType.MODEL, True, "MiniMax-H3 model bundle", is_array=False),
            PipeInputSpec("conditioning", IOType.CONDITIONING, True, "Encoded prompt conditioning "
                          "(context + token_tags -- see model_loader/minimax_h3/clip.py)", is_array=True),
            PipeInputSpec("image", IOType.IMAGE, False, "fl2va keyframe image(s), positional with "
                          "'keyframe_anchors'", is_array=True),
            PipeInputSpec("reference_images", IOType.IMAGE, False, "ref2va reference image(s), first in "
                          "packed order -- mutually exclusive with 'image'/'keyframe_anchors' (fl2va)",
                          is_array=True),
            PipeInputSpec("reference_videos", IOType.VIDEO, False, "ref2va reference video(s), packed "
                          "after every image reference. Each contributes one visual condition latent "
                          "(a frame stack, so 5*n+2 latent frames rather than 1) at the canvas its own "
                          "aspect ratio resolves to", is_array=True),
            PipeInputSpec("reference_audios", IOType.AUDIO, False, "ref2va reference audio track(s), "
                          "packed after every video reference. Each contributes CLEAN audio condition "
                          "rows and no visual latent at all, and cannot be the only reference kind on a "
                          "request", is_array=True),
            PipeInputSpec("director_image", IOType.IMAGE, False, "Video Director images, indexed by the "
                          "document's own `media_placements`. Kept apart from 'image' on purpose: these "
                          "condition latent frames only and never reach the text encoder's vision tower",
                          is_array=True),
            PipeInputSpec("initial_latent", IOType.LATENT, False,
                          "Seed latent for a refine pass, one per seed: a raw MiniMax-H3 VIDEO latent "
                          "(B, 24, T_lat, H_lat, W_lat) in the video VAE's own native (un-normalized) "
                          "space -- this pipe normalizes it itself, the exact inverse of the "
                          "'* latents_std + latents_mean' step 'decode' applies. Connected -> "
                          "'resolution'/'frames' config are ignored and DERIVED from the latent's own "
                          "shape instead, and 'denoise' controls how much of the schedule is walked. "
                          "Absent -> ordinary generation from pure noise, unchanged. Mutually exclusive "
                          "with 'document', 'image'/'keyframe_anchors' and every reference input",
                          is_array=True),
            PipeInputSpec("source_frame_count", IOType.INT, False,
                          "Original (pre-temporal-padding) frame count of a refine's source clip, from "
                          "the upstream latent_upscaler's own 'source_frame_count' output -- used to "
                          "trim the padded duplicate tail frames from this pipe's decoded output before "
                          "mux. Absent -> no trim", is_array=False),
            PipeInputSpec("audio", IOType.AUDIO, False, "User audio track (audio_source=file/passthrough)",
                          is_array=True),
            PipeInputSpec("seed", IOType.SEED, False, "Random seeds", is_array=True),
            PipeInputSpec("MODELS", IOType.SERVICE, False, "Model lifecycle service, to release the idle "
                          "TE's host RAM", is_array=False),
        ]

    @classmethod
    def outputs(cls) -> List[PipeOutputSpec]:
        return [
            PipeOutputSpec("video", IOType.VIDEO, "Generated videos (empty when decode=false)", is_array=True),
            PipeOutputSpec("latent", IOType.LATENT, "Raw per-seed video latents (only populated when "
                           "decode=false)", is_array=True),
            PipeOutputSpec("audio", IOType.AUDIO, "Decoded/passthrough audio track, one per seed", is_array=True),
        ]

    # -- context -------------------------------------------------------

    def build_context(self, pipe_input: PipeInput) -> GeneratorContext:
        bundle = pipe_input.input["model"]
        conditioning = pipe_input.input["conditioning"] or []
        seeds = pipe_input.input.get("seed", [])
        images = pipe_input.input.get("image") or []
        reference_images = list(pipe_input.input.get("reference_images") or [])
        reference_videos = list(pipe_input.input.get("reference_videos") or [])
        reference_audios = list(pipe_input.input.get("reference_audios") or [])
        audio_files = pipe_input.input.get("audio") or []
        # `or []` is unusable here: a bare Tensor raises on truthiness (same
        # trap `txt2vid_ltx.build_context` documents for its own
        # `initial_latent`) -- the upstream latent_upscaler may deliver a
        # single Tensor or a list.
        raw_initial_latent = pipe_input.input.get("initial_latent")
        if raw_initial_latent is None:
            initial_latents: list = []
        elif isinstance(raw_initial_latent, (list, tuple)):
            initial_latents = list(raw_initial_latent)
        else:
            initial_latents = [raw_initial_latent]
        source_frame_count = pipe_input.input.get("source_frame_count")

        if bundle.spec.family != "minimax_h3":
            raise ValueError(
                f"generator/video_minimax_h3: loaded model '{bundle.spec.family}/{bundle.spec.variant}' "
                f"is not a MiniMax-H3 checkpoint. Pick a MiniMax-H3 DiT for this preset."
            )

        # This pipe never touches the TE itself (conditioning was already
        # produced by prompt_encoder) -- same eviction idiom LTX established
        # (release_idle_te's docstring), reused verbatim: it is duck-typed on
        # bundle.te_cache_key/bundle.te, both of which MiniMaxH3ModelBundle
        # carries.
        release_idle_te(bundle, pipe_input.input.get("MODELS"), "GENERATOR MINIMAX-H3")

        mode = str(self.config.get("mode", "video"))
        configured_anchors = list(self.config.get("keyframe_anchors") or [])
        # Every reference kind falls under the SAME mutual exclusion as
        # images did: ref2va's reference prefix and fl2va's keyframe overlay
        # are two different packed layouts, not two features of one request.
        packed = pack_references(reference_images, reference_videos, reference_audios)
        if packed and (images or configured_anchors):
            raise ValueError(
                "generator/video_minimax_h3: 'reference_images'/'reference_videos'/'reference_audios' "
                "(ref2va) are mutually exclusive with 'image'/'keyframe_anchors' (fl2va) -- a request "
                "is one or the other"
            )
        # A refine pass (module docstring, "Refine entry path") has no
        # keyframe/reference overlay layout to combine with -- it seeds the
        # target rows directly from `initial_latent`, which is the ONLY
        # condition a refine carries.
        if initial_latents and (packed or images or configured_anchors or self.config.get("document")):
            raise ValueError(
                "generator/video_minimax_h3: 'initial_latent' (a refine pass) is mutually exclusive with "
                "'document' (Video Director), 'image'/'keyframe_anchors' (fl2va) and every reference "
                "input (ref2va) -- a refine pass has no keyframe/reference overlay to combine it with"
            )
        denoise = float(self.config.get("denoise", 1.0))
        if not initial_latents and denoise < 1.0:
            raise ValueError(
                "generator/video_minimax_h3: 'denoise' < 1.0 has no effect without 'initial_latent' "
                "connected -- connect a refine seed latent, or leave 'denoise' at its default 1.0"
            )
        for name, loaded, limit in (
            ("reference_images", reference_images, _MAX_REFERENCE_IMAGES),
            ("reference_videos", reference_videos, _MAX_REFERENCE_VIDEOS),
            ("reference_audios", reference_audios, _MAX_REFERENCE_AUDIOS),
        ):
            if len(loaded) > limit:
                raise ValueError(
                    f"generator/video_minimax_h3: at most {limit} {name!r} are supported, got {len(loaded)}"
                )
        if len(packed) > MAX_REFERENCES:
            raise ValueError(
                f"generator/video_minimax_h3: at most {MAX_REFERENCES} references in total are supported, "
                f"got {len(packed)}"
            )
        self._validate_references_count("references", self.config.get("references"), reference_images)
        self._validate_references_count("reference_videos", self.config.get("reference_videos"), reference_videos)
        self._validate_references_count("reference_audios", self.config.get("reference_audios"), reference_audios)
        if mode == "references" and not packed:
            raise ValueError(
                "generator/video_minimax_h3: mode='references' (ref2va) but the request carries no "
                "reference image, video or audio -- pick at least one. Running this checkpoint with an "
                "empty reference set is a text-only request against the reference partition, which "
                "returns plausible video rather than an error"
            )

        anchors = tuple(configured_anchors)
        if not anchors and images:
            anchors = ("first",) if len(images) == 1 else ("first", "last")
        if len(anchors) > len(images):
            raise ValueError(
                f"generator/video_minimax_h3: {len(anchors)} keyframe_anchors configured but only "
                f"{len(images)} 'image' input(s) provided"
            )
        keyframe_images = list(images[: len(anchors)])

        audio_source = str(self.config.get("audio_source", "generate"))
        audio_file: Optional[Any] = None
        if audio_source in ("file", "passthrough"):
            if not audio_files:
                raise ValueError(
                    f"generator/video_minimax_h3: audio_source={audio_source!r} but no 'audio' input was "
                    f"provided"
                )
            audio_file = audio_files[0]

        # This pipe's `resolution` config always applies (no "auto" sentinel):
        # unlike the reference, which derives the canvas from a keyframe's own
        # aspect ratio when `height`/`width` are unset, a keyframe here is
        # always fit onto the CONFIGURED canvas (`conditioning.
        # fit_keyframe_to_canvas`). Documented gap, not a silent behavior
        # difference.
        if initial_latents:
            # A refine pass derives width/height/frames from the SEED
            # LATENT's own shape rather than 'resolution'/'frames' config --
            # an upstream latent's source is only known at runtime, never at
            # preset-render time (same rationale `txt2vid_ltx.build_context`
            # documents for its own 'initial_latent').
            b0, c0, t_lat0, h_lat0, w_lat0 = initial_latents[0].shape
            if int(c0) != VIDEO_LATENT_CHANNELS:
                raise ValueError(
                    f"generator/video_minimax_h3: 'initial_latent' must carry {VIDEO_LATENT_CHANNELS} "
                    f"channels, got {int(c0)}"
                )
            latent_height, latent_width = int(h_lat0), int(w_lat0)
            height, width = latent_height * 16, latent_width * 16
            num_latent_frames = int(t_lat0)
            frames = pixel_frames_for_latent_frames(num_latent_frames)
            num_audio_latents = audio_latent_num_frames(frames)
        else:
            # This pipe's `resolution` config always applies (no "auto"
            # sentinel): unlike the reference, which derives the canvas from
            # a keyframe's own aspect ratio when `height`/`width` are unset, a
            # keyframe here is always fit onto the CONFIGURED canvas
            # (`conditioning.fit_keyframe_to_canvas`). Documented gap, not a
            # silent behavior difference.
            resolution = str(self.config.get("resolution", "1344x768")).split("x")
            width, height = int(resolution[0]), int(resolution[1])
            frames = int(self.config.get("frames", 124))
            height, width, frames, num_latent_frames, latent_height, latent_width, num_audio_latents = (
                resolve_request_geometry(height, width, frames)
            )

        spec = bundle.spec
        steps = int(self.config.get("steps", 24))
        quantity = int(self.config.get("quantity", 1))
        device = self.config.get("device", "cuda")
        decode = bool(self.config.get("decode", True))
        video_sigma_shift = float(self.config.get("video_sigma_shift", VIDEO_SHIFT))

        plan = build_director_plan(self.config.get("document"), default_seed=int(self.config.get("seed", -1)))
        director_images = list(pipe_input.input.get("director_image") or [])
        if plan is not None:
            if not decode:
                raise DirectorPlanError(
                    "generator/video_minimax_h3: a Video Director run has to decode -- the windows are "
                    "trimmed and stitched in pixel space, so there is no single latent to hand on"
                )
            if packed:
                # Every window becomes its own ref2va generation -- see
                # `_validate_refs_director_plan`'s docstring and windows.py's
                # module docstring ("ref2va Director runs are hard-cut-only")
                # for why continuation and per-window keyframes are refused
                # here rather than combined with the reference prefix.
                self._validate_refs_director_plan(plan, num_references=len(packed))
            else:
                self._validate_director_images(plan, director_images)
            # One composed clip per request: the window loop already consumes
            # the document's per-segment seeds, so a second seed axis on top of
            # it would multiply the run rather than vary it.
            quantity = 1

        manual_sigmas = str(self.config.get("manual_sigmas", "") or "")
        manual_audio_sigmas = str(self.config.get("manual_audio_sigmas", "") or "")
        sampler = str(self.config.get("sampler", "euler"))
        scheduler = str(self.config.get("scheduler", SIMPLE_SCHEDULER))
        video_schedule, _ = resolve_schedules(
            steps, manual_video=manual_sigmas, manual_audio=manual_audio_sigmas, scheduler=scheduler,
            video_shift=video_sigma_shift, denoise=denoise,
        )
        effective_steps = int(video_schedule.timesteps.numel())

        if plan is not None:
            logger.info(
                "[GENERATOR MINIMAX-H3] %s: director run of %d window(s) @ %dx%d, %d frame(s) stitched=%s "
                "-- %s",
                spec.variant, len(plan.windows), width, height, plan.total_frames, plan.stitch,
                ", ".join(
                    f"[{w.index}] {w.sub_type} {w.frames}f"
                    + (f" (asked {w.requested_frames})" if w.frames != w.requested_frames else "")
                    + (f" -{w.overlap_frames}f overlap" if w.overlap_latents else "")
                    + (f" {len(w.keyframes)} keyframe(s)" if w.keyframes else "")
                    for w in plan.windows
                ),
            )
        elif packed:
            logger.info(
                "[GENERATOR MINIMAX-H3] %s: %d frame(s) @ %dx%d, %d steps%s (%s/%s), ref2va packed as "
                "[%s], audio_source=%s",
                spec.variant, frames, width, height, effective_steps,
                " (manual sigmas)" if (manual_sigmas.strip() or manual_audio_sigmas.strip()) else "",
                sampler, scheduler,
                ", ".join(kind for kind, _media in packed), audio_source,
            )
        elif initial_latents:
            logger.info(
                "[GENERATOR MINIMAX-H3] %s: refine of %d frame(s) @ %dx%d (derived from initial_latent), "
                "%d steps%s (%s/%s), denoise=%.2f, video_sigma_shift=%.1f, audio_source=%s",
                spec.variant, frames, width, height, effective_steps,
                " (manual sigmas)" if (manual_sigmas.strip() or manual_audio_sigmas.strip()) else "",
                sampler, scheduler, denoise, video_sigma_shift, audio_source,
            )
        else:
            logger.info(
                "[GENERATOR MINIMAX-H3] %s: %d frame(s) @ %dx%d, %d steps%s (%s/%s), %d keyframe(s) (%s), "
                "audio_source=%s",
                spec.variant, frames, width, height, effective_steps,
                " (manual sigmas)" if (manual_sigmas.strip() or manual_audio_sigmas.strip()) else "",
                sampler, scheduler,
                len(keyframe_images), anchors or "t2va", audio_source,
            )

        self._video_resolution = (width, height)
        self._audio_results: list = []

        # A director run's own top-level `frames` is vestigial (each window
        # has its own count, per `document`'s PipeConfigSpec); normalizing
        # against it would truncate a reference video/audio to whichever
        # window happens to be shortest. The longest window's frame count is
        # the only bound that cannot under-serve any window a whole-film or
        # per-shot reference actually conditions.
        reference_num_frames = frames
        if plan is not None and plan.windows:
            reference_num_frames = max(window.frames for window in plan.windows)
        references = self._normalize_reference_media(packed, num_frames=reference_num_frames) if packed else ()

        return GeneratorContext(
            quantity=quantity,
            input_seeds=seeds,
            extra=_MiniMaxH3Ctx(
                bundle=bundle, conditioning=conditioning, steps=steps,
                height=height, width=width, frames=frames,
                num_latent_frames=num_latent_frames, latent_height=latent_height, latent_width=latent_width,
                num_audio_latents=num_audio_latents, device=device, dtype=bundle.dit.compute_dtype, spec=spec,
                keyframe_images=keyframe_images, keyframe_anchors=anchors,
                references=references,
                audio_source=audio_source, audio_file=audio_file, decode=decode,
                manual_sigmas=manual_sigmas, manual_audio_sigmas=manual_audio_sigmas,
                sampler=sampler, scheduler=scheduler,
                plan=plan, director_images=director_images,
                initial_latents=initial_latents,
                source_frame_count=int(source_frame_count) if source_frame_count else None,
                denoise=denoise, video_sigma_shift=video_sigma_shift,
            ),
        )

    @staticmethod
    def _validate_director_images(plan: DirectorPlan, images: List[Any]) -> None:
        """Every placement must address an image the loader actually produced.

        The document's `media_images` and the `director_image` input are two
        views of one list built by the preset from the same field, so a gap
        here means the pipeline is miswired -- worth failing on rather than
        generating a window silently missing its keyframe.
        """
        needed = [kf.image_index for window in plan.windows for kf in window.keyframes]
        if needed and max(needed) >= len(images):
            raise DirectorPlanError(
                f"generator/video_minimax_h3: the document places an image at index {max(needed)} but only "
                f"{len(images)} director image(s) were loaded"
            )

    @staticmethod
    def _validate_refs_director_plan(plan: DirectorPlan, *, num_references: int) -> None:
        """A refs-conditioned Director run is hard-cut-only (windows.py's
        module docstring, "ref2va Director runs are hard-cut-only"):
        continuation and per-window Director keyframes both build their
        condition rows through the OVERLAY `keyframe_anchors` mechanism
        (`layout.build_packed_sequence`), which no diffusers block combines
        with the reference-block prefix (`layout.build_ref2va_packed_
        sequence`) every window needs instead. Named per offending segment
        rather than a blanket refusal, so the fix a document author needs is
        obvious from the error alone.
        """
        for window in plan.windows:
            if window.continues_previous:
                raise DirectorPlanError(
                    f"generator/video_minimax_h3: segment {window.segment_id!r} continues the previous "
                    f"window (sub_type 'chain'), but ref2va references are active on this run -- "
                    f"MiniMax-H3 has no layout that combines a reference-conditioned prefix with "
                    f"continuation's own condition-row overlay. Make every segment a cut (drop 'chain') "
                    f"or drop the references"
                )
            if window.keyframes:
                raise DirectorPlanError(
                    f"generator/video_minimax_h3: segment {window.segment_id!r} has its own Director "
                    f"keyframe image, but ref2va references are active on this run -- a keyframe overlay "
                    f"and a reference prefix are two different packed layouts and cannot share one "
                    f"window. Remove the segment's keyframe or drop the references"
                )
            if window.reference_indices is not None:
                if not window.reference_indices:
                    raise DirectorPlanError(
                        f"generator/video_minimax_h3: segment {window.segment_id!r}'s 'reference_indices' "
                        f"is empty -- omit the field to use every reference, or list at least one index"
                    )
                out_of_range = [i for i in window.reference_indices if not 0 <= i < num_references]
                if out_of_range:
                    raise DirectorPlanError(
                        f"generator/video_minimax_h3: segment {window.segment_id!r}'s 'reference_indices' "
                        f"{out_of_range} is out of range for {num_references} packed reference(s)"
                    )

    @staticmethod
    def _validate_references_count(field: str, references_config: Any, loaded: List[Any]) -> None:
        """A reference field's raw config value and its loaded input are two
        views of the same preset field (one raw, one loaded) -- same
        precedent as `_validate_director_images` above. A count mismatch
        means the media_loader feeding the input and the raw field value have
        drifted apart, which is worth failing on loudly rather than silently
        mis-numbering `<Picture N>`/`<Video k>`/`<Audio j>` labels against a
        shorter or longer loaded array.
        """
        declared = list(references_config or [])
        if not declared and not loaded:
            return
        if len(declared) != len(loaded):
            raise ValueError(
                f"generator/video_minimax_h3: the {field!r} field declares {len(declared)} "
                f"reference(s) but {len(loaded)} were loaded -- the media_loader over that field and "
                f"the field's own value have drifted apart"
            )

    @staticmethod
    def _normalize_reference_media(packed: List[tuple], *, num_frames: int) -> tuple:
        """The packed `[(kind, media), ...]` list -> ALREADY-NORMALIZED
        :class:`ReferenceMedia`, in the same order.

        `media_loader` decodes images and hands videos and audio over as
        PATHS, so the two file kinds are decoded here; `normalize_references`
        then puts every one of them on H3's own resolutions and rates in ONE
        pass, which is also where the released checkpoint's per-modality and
        total reference rules are enforced (`validate_references`).
        """
        loaded: List[ReferenceMedia] = []
        for kind, media in packed:
            if kind == "image":
                loaded.append(ReferenceMedia(kind="image", image=media))
            elif kind == "video":
                loaded.append(_load_reference_video(media))
            else:
                loaded.append(_load_reference_audio(media))
        return tuple(normalize_references(loaded, num_frames=num_frames))

    @staticmethod
    def _build_ref2va_layout(
        c: "_MiniMaxH3Ctx", references: tuple, text_token_tags: Tensor, *,
        num_latent_frames: int, num_audio_latents: int, generator: torch.Generator,
    ) -> tuple[PackedLayout, Tensor, Optional[Tensor]]:
        """VAE-encode `references` (the whole packed set, or one window's own
        subset) into a `ref2va` layout, its noised condition-row prefix and
        its clean condition-audio prefix, for ONE generation.

        `references` is a parameter rather than always `c.references`
        because a Director window's per-shot subset (`WindowPlan.
        reference_indices`) still comes from the SAME already-normalized
        media -- only the SELECTION and the resulting layout differ per
        window, never the fit/resample.

        One draw off `generator` per VISUAL reference (`prepare_reference_
        conditioning`'s own "one generator, three draws, in order" contract)
        -- the caller must not have drawn its own video/audio noise yet.
        Moves and offloads the video/audio VAE itself; the audio VAE only
        when this reference set actually carries a soundtrack, same as the
        single-window path this replaces did inline.
        """
        video_vae_module = _require_h3_video_vae(c.bundle.video_vae.module)
        needs_audio_vae = any(reference.has_audio for reference in references)
        c.bundle.video_vae.move_to(c.device)
        if needs_audio_vae:
            c.bundle.audio_vae.move_to(c.device)
        try:
            reference_conditioning = prepare_reference_conditioning(
                references, vae_module=video_vae_module,
                audio_vae_module=c.bundle.audio_vae.module if needs_audio_vae else None,
                patch_size=PATCH_SIZE, device=c.device, dtype=c.dtype,
                latents_mean=video_vae_module.latents_mean, latents_std=video_vae_module.latents_std,
                generator=generator,
            )
        finally:
            c.bundle.video_vae.offload()
            if needs_audio_vae:
                c.bundle.audio_vae.offload()

        # All four arguments come off the SAME `ReferenceConditioning`, which
        # `prepare_reference_conditioning` built in one pass over
        # `references` -- so the blocks, the two latent iterators and the row
        # prefixes cannot describe different traversals.
        layout = build_ref2va_packed_sequence(
            text_token_tags, reference_conditioning.blocks,
            reference_conditioning.condition_latents, reference_conditioning.audio_condition_latents,
            num_latent_frames=num_latent_frames, latent_height=c.latent_height, latent_width=c.latent_width,
            num_audio_latents=num_audio_latents, patch_size=PATCH_SIZE, device=c.device,
        )
        return layout, reference_conditioning.condition_rows, reference_conditioning.condition_audio_rows

    @staticmethod
    def _window_references(c: "_MiniMaxH3Ctx", window: WindowPlan) -> tuple:
        """This window's own reference subset, in the subset's OWN relative
        order -- `window.reference_indices` is `None` (every reference, the
        whole-film case) or a tuple of indices into `c.references`' packed
        order (windows.py's module docstring, "Per-segment reference
        selection")."""
        if window.reference_indices is None:
            return c.references
        return tuple(c.references[i] for i in window.reference_indices)

    @staticmethod
    def _normalized_initial_latent(c: "_MiniMaxH3Ctx", index: int, video_vae_module: Any) -> Optional[Tensor]:
        """This seed's `initial_latent`, normalized into the DiT's own
        working space -- `None` when the request carries none (the ordinary
        from-noise path).

        `c.initial_latents[index]`, falling back to the last entry for a
        seed beyond the list (same "one latent may serve every seed"
        fallback `txt2vid_ltx.generate_one` uses for its own
        `initial_latents`). The input arrives in the video VAE's native
        (un-normalized) space -- see the `initial_latent` `PipeInputSpec` --
        so this applies the exact inverse of `_decode_video`'s
        `* latents_std + latents_mean` denormalize.
        """
        if not c.initial_latents:
            return None
        raw = c.initial_latents[index] if index < len(c.initial_latents) else c.initial_latents[-1]
        expected_shape = (1, VIDEO_LATENT_CHANNELS, c.num_latent_frames, c.latent_height, c.latent_width)
        if tuple(raw.shape) != expected_shape:
            raise ValueError(
                f"generator/video_minimax_h3: initial_latent[{index}] shape {tuple(raw.shape)} does not "
                f"match initial_latent[0]'s derived geometry {expected_shape} -- every seed's refine "
                f"latent in one call must share the same dimensions"
            )
        latent = raw.to(device=c.device, dtype=torch.float32)
        lmean = torch.as_tensor(video_vae_module.latents_mean, device=c.device, dtype=torch.float32)
        lstd = torch.as_tensor(video_vae_module.latents_std, device=c.device, dtype=torch.float32)
        return (latent - lmean.view(1, -1, 1, 1, 1)) / lstd.view(1, -1, 1, 1, 1)

    # -- per-seed generation ---------------------------------------------

    def generate_one(self, ctx: GeneratorContext, index: int, seed: int, progress) -> Any:
        c: _MiniMaxH3Ctx = ctx.extra
        if c.plan is not None:
            return self._generate_director(c, c.plan, progress, ctx.is_cancelled)
        cond_model = c.conditioning[index] if index < len(c.conditioning) else c.conditioning[-1]
        prompt_embeds = cond_model.embeds["context"].to(device=c.device, dtype=c.dtype)
        text_token_tags = cond_model.embeds["token_tags"].to(device=c.device)

        # ONE generator per seed; three draws off it, IN ORDER: conditioning
        # noise, then video noise, then audio noise (dossier "Generation
        # constraints" -- reproducing seed parity requires this exact order).
        gen = torch.Generator(device=c.device).manual_seed(int(seed))

        ref2va_layout = None
        condition_audio_rows = None
        initial_latent: Optional[Tensor] = None
        if c.references:
            ref2va_layout, condition_rows, condition_audio_rows = self._build_ref2va_layout(
                c, c.references, text_token_tags,
                num_latent_frames=c.num_latent_frames, num_audio_latents=c.num_audio_latents,
                generator=gen,
            )
        else:
            video_vae_module = _require_h3_video_vae(c.bundle.video_vae.module)
            c.bundle.video_vae.move_to(c.device)
            try:
                condition_rows = prepare_keyframe_condition_rows(
                    c.keyframe_images, c.keyframe_anchors, vae_module=video_vae_module,
                    height=c.height, width=c.width, patch_size=PATCH_SIZE, device=c.device, dtype=c.dtype,
                    latents_mean=video_vae_module.latents_mean, latents_std=video_vae_module.latents_std,
                    generator=gen,
                )
                # Refine entry path (module docstring, "Refine entry path"):
                # normalized while the video VAE is still resident, the exact
                # inverse of `_decode_video`'s `* latents_std + latents_mean`.
                # `c.keyframe_images`/`c.references` are both empty here (the
                # mutual-exclusion guard in `build_context` guarantees it), so
                # `condition_rows` above is always the empty tensor already.
                initial_latent = self._normalized_initial_latent(c, index, video_vae_module)
            finally:
                c.bundle.video_vae.offload()

        video_rows, audio_rows, layout = self._sample_window(
            c, prompt_embeds=prompt_embeds, text_token_tags=text_token_tags,
            condition_rows=condition_rows, keyframe_anchors=c.keyframe_anchors,
            num_latent_frames=c.num_latent_frames, num_audio_latents=c.num_audio_latents,
            condition_audio_rows=condition_audio_rows, steps=c.steps, generator=gen, progress=progress,
            layout=ref2va_layout, is_cancelled=ctx.is_cancelled, initial_latent=initial_latent,
        )
        if ctx.is_cancelled():
            # Sampling completed (or was mid-step cancellation lost the race
            # against the last step) with nothing left to abort on -- the
            # video/audio VAE decode and mp4 encode below are ~27s of GPU
            # work a cancelled generation must not pay for. Mirrors the
            # SamplingCancelled convention every per-step check above uses.
            raise SamplingCancelled()
        n_cv = layout.num_condition_video_rows
        n_ca = layout.num_condition_audio_rows

        video_latent = unpatchify_video_rows(
            video_rows[n_cv:], num_latent_frames=c.num_latent_frames, latent_height=c.latent_height,
            latent_width=c.latent_width, channels=VIDEO_LATENT_CHANNELS, patch_size=PATCH_SIZE,
        )

        audio_track = self._resolve_audio(c, audio_rows, num_condition_audio_rows=n_ca)
        self._audio_results.append(audio_track)

        if not c.decode:
            if index == ctx.quantity - 1:
                restore_dit_best_effort(c.bundle.dit, c.device)
            return video_latent

        frames_np = self._decode_video(c, video_latent)
        if c.source_frame_count and 0 < c.source_frame_count < frames_np.shape[0]:
            # A refine's source clip was padded (repeated tail frames) to hit
            # the video VAE's 17*n+5 alignment before it was ever encoded --
            # trim that padding back off before mux, same idiom
            # `txt2vid_ltx`'s own `trim_to_frame_count` uses.
            frames_np = frames_np[: c.source_frame_count]
        out_path = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False).name
        encode_frames_to_mp4(frames_np, out_path, fps=FPS, audio=audio_track)

        if index == ctx.quantity - 1:
            restore_dit_best_effort(c.bundle.dit, c.device)
        return out_path

    # -- the one sampling loop both paths run ------------------------------

    def _sample_window(
        self, c: _MiniMaxH3Ctx, *, prompt_embeds: Tensor, text_token_tags: Tensor,
        condition_rows: Tensor, keyframe_anchors: tuple, num_latent_frames: int, num_audio_latents: int,
        condition_audio_rows: Optional[Tensor], steps: int, generator: torch.Generator, progress,
        progress_offset: int = 0, progress_total: Optional[int] = None, progress_state: str = "VIDEO",
        layout: Optional[PackedLayout] = None, is_cancelled: Optional[Callable[[], bool]] = None,
        initial_latent: Optional[Tensor] = None,
    ) -> tuple[Tensor, Tensor, PackedLayout]:
        """Sample ONE packed sequence to completion; returns the final
        `(video_rows, audio_rows, layout)` with the condition prefixes still
        attached (the caller slices them off, and a windowed run needs the
        untouched rows to chain into the next window).

        The caller has already drawn the CONDITIONING noise off `generator`;
        this method draws the video noise and then the audio noise, in that
        order, which is the whole of the "one generator, three draws, in
        order" contract for one window. A windowed run repeats the triple per
        window with that window's own generator, never one generator spanning
        windows -- a window's output must not depend on how many windows ran
        before it.

        `layout`: pre-built (default `None` -> build the `t2va`/`fl2va`
        layout below from `keyframe_anchors`). A `ref2va` caller builds its
        own `build_ref2va_packed_sequence` layout up front -- a reference
        block's geometry comes from what was actually VAE-encoded, which this
        method has no way to reconstruct from `keyframe_anchors` alone -- and
        passes it here instead, along with the `condition_audio_rows` any
        audio-bearing reference produced. `keyframe_anchors` is then unused
        (a `ref2va` request carries none).

        `is_cancelled`, when given, is polled once per step (same contract as
        every per-step sampler in `sampling/algorithms/`) and raises
        `SamplingCancelled` rather than returning a partial trajectory -- this
        loop has no shared `denoise()`/`denoise_prenoised()` to inherit that
        check from (see the module docstring's "bespoke, not denoise" note).

        `initial_latent`: already NORMALIZED (module docstring, "Refine entry
        path"), `(1, VIDEO_LATENT_CHANNELS, num_latent_frames, latent_height,
        latent_width)`. `None` (default) is the ordinary path: the video
        target rows start from pure noise. Given, they instead start from
        `initial_latent` noised up to `c.denoise`'s truncated schedule's
        FIRST kept sigma (`schedule.scale_noise`, the same math a keyframe
        anchor's own noise augmentation uses) -- the video noise draw below
        still happens either way, same shape, so the generator's draw count
        is unchanged by which branch runs.
        """
        if layout is None:
            # `device=c.device` explicit: build_packed_sequence builds every
            # tensor it owns directly on this device and coerces
            # text_token_tags onto it too, rather than this call site
            # patching a mismatch afterward (see layout.py's module docstring
            # "Device" section -- this used to be a post-hoc `.to(c.device)`
            # re-wrap here, which never ran because the mismatch crashed
            # INSIDE build_packed_sequence first).
            num_condition_audio_latents = (
                0 if condition_audio_rows is None else condition_audio_rows.shape[0] // AUDIO_CHANNELS
            )
            layout = build_packed_sequence(
                text_token_tags, num_latent_frames=num_latent_frames, latent_height=c.latent_height,
                latent_width=c.latent_width, num_audio_latents=num_audio_latents, patch_size=PATCH_SIZE,
                keyframe_anchors=keyframe_anchors, num_condition_audio_latents=num_condition_audio_latents,
                device=c.device,
            )

        # Resolved BEFORE the video noise draw below (moved up from this
        # method's original order) -- a refine (`initial_latent` given) needs
        # the truncated schedule's first sigma to noise up to, so the
        # schedule has to exist before that draw is even shaped.
        video_schedule, audio_schedule = resolve_schedules(
            steps, manual_video=c.manual_sigmas, manual_audio=c.manual_audio_sigmas, scheduler=c.scheduler,
            video_shift=c.video_sigma_shift, denoise=c.denoise,
        )
        num_steps = int(video_schedule.timesteps.numel())

        video_noise = torch.randn(
            (1, VIDEO_LATENT_CHANNELS, num_latent_frames, c.latent_height, c.latent_width),
            generator=generator, device=c.device, dtype=torch.float32,
        )
        if initial_latent is not None:
            # `t = 1 - sigma`, data-ward (schedule.py module docstring) --
            # the SAME noise-augmentation math `prepare_keyframe_condition_
            # rows` uses for a keyframe anchor, at the truncated schedule's
            # own first kept timestep instead of the fixed `KEYFRAME_NOISE_
            # AUG`. `denoise=1.0` (the default) makes `timesteps[0] == 0.0`
            # (t=0, pure noise) -- `scale_noise` there just returns the raw
            # draw, so this is byte-identical to the plain-noise branch below.
            first_video_t = float(video_schedule.timesteps[0])
            noised = scale_noise(initial_latent.to(torch.float32), first_video_t, video_noise)
            video_target_rows = patchify_video_latents(noised.to(c.dtype), PATCH_SIZE)
        else:
            video_target_rows = patchify_video_latents(video_noise, PATCH_SIZE).to(c.dtype)
        video_rows = (
            torch.cat([condition_rows.to(c.dtype), video_target_rows], dim=0)
            if condition_rows.shape[0] else video_target_rows
        )

        audio_target_rows = torch.randn(
            (num_audio_latents * 2, AUDIO_LATENT_CHANNELS), generator=generator, device=c.device,
            dtype=torch.float32,
        ).to(c.dtype)
        audio_rows = (
            torch.cat([condition_audio_rows.to(c.dtype), audio_target_rows], dim=0)
            if condition_audio_rows is not None else audio_target_rows
        )

        # One stepper per STREAM (they walk different sigma grids) and one set
        # per WINDOW (a window is its own trajectory, so a multistep history
        # must not cross into it from the window before).
        video_stepper = make_stepper(c.sampler, generator=generator)
        audio_stepper = make_stepper(c.sampler, generator=generator)

        # Built BEFORE the placement below, not with the sampling state further
        # down, because the placement has to know the sparse-attention
        # method's transients exist. One context per WINDOW: the prefix/sink
        # is a row count into THIS window's packed sequence.
        sparse_attn_ctx = build_sparse_attn_ctx(self.config, layout)
        dense_last_steps = sparse_attn_dense_last_steps(self.config)
        sparse_attn_reserve = sparse_attn_reserve_gb(sparse_attn_ctx, layout)
        if isinstance(sparse_attn_ctx, SolAttnContext):
            logger.info(
                "[GENERATOR MINIMAX-H3] Sol-Attn requested: tau=%.2f, %d exact prefix row(s) of %d, "
                "last %d of %d step(s) dense, reserving %.2f GB for its transients",
                sparse_attn_ctx.tau, sparse_attn_ctx.sink_tokens, int(layout.position_ids.shape[0]),
                min(dense_last_steps, num_steps), num_steps, sparse_attn_reserve,
            )
        elif isinstance(sparse_attn_ctx, SlaAttnContext):
            logger.info(
                "[GENERATOR MINIMAX-H3] SLA requested: sparsity=%.2f, block=%d, %d pinned prefix "
                "row(s) of %d, last %d of %d step(s) dense, reserving %.2f GB for its transients",
                sparse_attn_ctx.sparsity, sparse_attn_ctx.block_size, sparse_attn_ctx.prefix_tokens,
                int(layout.position_ids.shape[0]), min(dense_last_steps, num_steps), num_steps,
                sparse_attn_reserve,
            )

        place_dit_for_sequence(
            # Text rows ride the SAME packed attention document as video/audio
            # (dossier §A.2: no cross-attention, one sequence) -- folded into
            # `video_tokens` since `place_dit_for_sequence`'s reserve is sized
            # off the TOTAL sequence length, not "video" in the LTX sense
            # (LTX's own text conditioning is cross-attention, off-sequence;
            # H3 has none, so every row here is part of the one S this
            # function budgets for).
            c.bundle.dit, c.device,
            video_tokens=video_rows.shape[0] + layout.text_indices.numel(), audio_tokens=audio_rows.shape[0],
            own_models=(c.bundle.dit, c.bundle.video_vae, c.bundle.audio_vae),
            inner_dim=H3_INNER_DIM, ffn_dim=H3_FFN_DIM,
            # 0.0 unless a sparse-attention method is on: its routing and QKV
            # copies are the one piece of this generation's GPU work the
            # token-derived reserve cannot see.
            reserve_gb=sparse_attn_reserve,
        )
        forward = _MiniMaxH3Forward(c.bundle.dit.module, layout, prompt_embeds)

        reported_total = progress_total if progress_total is not None else num_steps

        def on_progress(_frac, step_index, total):
            progress.step(progress_offset + step_index + 1, reported_total, state=progress_state,
                          icon=Icon(name="film", effect="pulse"))

        hooks = [ProgressHook(on_progress)]
        if self.config.get("preview", True):
            preview_hook = make_preview_hook(c.spec, progress.preview)
            if preview_hook is not None:
                hooks.append(preview_hook)

        n_cv = layout.num_condition_video_rows
        n_ca = layout.num_condition_audio_rows
        num_text_tokens = int(layout.text_indices.numel())

        # One FirstBlockCache per WINDOW: its residual anchor is shaped by the
        # sequence it was primed on, and a window may differ from its
        # predecessor in frame count, condition rows or prompt length.
        step_cache = build_step_cache(self.config)

        run_hooks(hooks, "on_start", num_steps)
        for step_index in range(num_steps):
            if is_cancelled is not None and is_cancelled():
                raise SamplingCancelled(step_index=step_index)

            video_t = float(video_schedule.timesteps[step_index])
            audio_t = float(audio_schedule.timesteps[step_index])
            unique_timesteps, timestep_indices = build_row_timesteps(
                layout.video_indices, layout.audio_indices,
                num_condition_video_rows=n_cv, num_condition_audio_rows=n_ca, num_text_tokens=num_text_tokens,
                video_timestep=video_t, audio_timestep=audio_t,
                condition_video_timestep=max(video_t, KEYFRAME_NOISE_AUG), condition_audio_timestep=1.0,
            )
            # The last step is never cached: it is the one that lands on the
            # final latent, so a replayed velocity there shows up directly in
            # the output (the shared denoise loop makes the same exclusion).
            is_final_step = step_index == num_steps - 1
            if sparse_attn_ctx is not None:
                sparse_attn_ctx.dense = is_dense_step(step_index, num_steps, dense_last_steps)
            video_pred, audio_pred = forward(
                video_rows, audio_rows, unique_timesteps.to(c.device), timestep_indices.to(c.device),
                step_cache=None if is_final_step else step_cache,
                sparse_attn_ctx=sparse_attn_ctx,
            )

            # A step the cache skipped hands back the previous step's velocity;
            # it enters the multistep history unchanged, because it is the
            # model's most recent real output and the sample it moved is real
            # too (samplers.py, `_MultistepStepper`).
            video_rows[n_cv:] = video_stepper.step(
                video_pred[n_cv:].float(), video_t, video_rows[n_cv:].float(),
                video_schedule.sigmas[step_index], video_schedule.sigmas[step_index + 1],
            ).to(c.dtype)
            audio_rows[n_ca:] = audio_stepper.step(
                audio_pred[n_ca:].float(), audio_t, audio_rows[n_ca:].float(),
                audio_schedule.sigmas[step_index], audio_schedule.sigmas[step_index + 1],
            ).to(c.dtype)

            if len(hooks) > 1:  # a preview hook is registered
                video_x0 = data_estimate(video_pred[n_cv:].float(), video_t, video_rows[n_cv:].float())
                video_x0_5d = unpatchify_video_rows(
                    video_x0, num_latent_frames=num_latent_frames, latent_height=c.latent_height,
                    latent_width=c.latent_width, channels=VIDEO_LATENT_CHANNELS, patch_size=PATCH_SIZE,
                )
            else:
                video_x0_5d = None
            run_hooks(hooks, "on_step", step_index, num_steps, video_rows, video_schedule.sigmas[step_index], video_x0_5d)
        run_hooks(hooks, "on_end")

        if step_cache is not None:
            stats = step_cache.stats()
            logger.debug(
                "[GENERATOR MINIMAX-H3] FBCache: skipped %d of %d model forwards (rel_threshold=%.3f)",
                stats["skipped"], stats["skipped"] + stats["computed"], step_cache.rel_threshold,
            )

        c.bundle.dit.offload()
        clear_gpu_memory()
        return video_rows, audio_rows, layout

    def _resolve_audio(
        self, c: _MiniMaxH3Ctx, generated_audio_rows: Tensor, *,
        num_audio_latents: Optional[int] = None, num_condition_audio_rows: int = 0,
    ) -> AudioInput:
        """MiniMax-H3 always jointly samples an audio stream (it is one block
        of the SAME packed sequence, not an opt-in the way LTX's is) --
        `audio_source` only picks what the FINAL muxed track is.

        `num_condition_audio_rows` is forwarded rather than pre-sliced by the
        caller: a windowed run carries a clean condition-audio PREFIX into
        every continuation window, and decoding that prefix would prepend the
        previous window's soundtrack to this window's track.
        """
        if c.audio_source == "generate":
            c.bundle.audio_vae.move_to(c.device)
            try:
                return decode_generated_audio(
                    c.bundle.audio_vae.module, generated_audio_rows.float(),
                    num_audio_latents=num_audio_latents if num_audio_latents is not None else c.num_audio_latents,
                    num_condition_audio_rows=num_condition_audio_rows,
                )
            finally:
                c.bundle.audio_vae.offload()
        return c.audio_file  # "file" / "passthrough": carried through verbatim

    # -- windowed (Video Director) generation -------------------------------

    def _generate_director(
        self, c: _MiniMaxH3Ctx, plan: DirectorPlan, progress,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> List[str]:
        """Run the document's segments as a chain of windows and return the
        finished clip path(s).

        Each window is a complete, independent H3 generation; continuity is
        carried purely as tensors between them -- the previous window's video
        latent tail becomes this window's leading condition rows, and a slice
        of its audio latents becomes the clean condition-audio prefix. Nothing
        is decoded and re-encoded to make that happen.

        A reference-conditioned run (`c.references` non-empty) takes a
        different branch per window: `_validate_refs_director_plan` has
        already refused every window that would need continuation's or a
        Director keyframe's OVERLAY condition rows, so every window here
        builds its own `ref2va` reference-block layout instead
        (`_build_ref2va_layout`, `_window_references` for the per-shot
        subset) and `previous_latent`/`previous_audio_rows` are tracked but
        never read back into one -- there is no continuation to feed them
        into.
        """
        windows = plan.windows
        total_steps = sum(self._window_step_count(c, window) for window in windows)
        clips: List[str] = []
        tracks: List[Any] = []
        window_frames: List[np.ndarray] = []
        previous_latent: Optional[Tensor] = None
        previous_audio_rows: Optional[Tensor] = None
        previous_audio_latents = 0
        steps_done = 0

        for window in windows:
            if is_cancelled is not None and is_cancelled():
                raise SamplingCancelled()

            cond_model = (
                c.conditioning[window.index] if window.index < len(c.conditioning) else c.conditioning[-1]
            )
            prompt_embeds = cond_model.embeds["context"].to(device=c.device, dtype=c.dtype)
            text_token_tags = cond_model.embeds["token_tags"].to(device=c.device)

            # A window's own seed, never a generator carried across windows:
            # re-running one segment of a document has to reproduce the same
            # shot regardless of what ran before it.
            generator = torch.Generator(device=c.device).manual_seed(int(window.seed))

            if c.references:
                window_references = self._window_references(c, window)
                ref2va_layout, condition_rows, condition_audio_rows = self._build_ref2va_layout(
                    c, window_references, text_token_tags,
                    num_latent_frames=window.num_latent_frames, num_audio_latents=window.num_audio_latents,
                    generator=generator,
                )
                anchors: tuple = ()
            else:
                ref2va_layout = None
                anchors, condition_rows = self._window_condition_rows(c, window, previous_latent, generator)
                condition_audio_rows = self._window_condition_audio(
                    window, previous_audio_rows, previous_audio_latents, dtype=c.dtype,
                )

            video_rows, audio_rows, layout = self._sample_window(
                c, prompt_embeds=prompt_embeds, text_token_tags=text_token_tags,
                condition_rows=condition_rows, keyframe_anchors=anchors,
                num_latent_frames=window.num_latent_frames, num_audio_latents=window.num_audio_latents,
                condition_audio_rows=condition_audio_rows,
                steps=window.steps if window.steps is not None else c.steps,
                generator=generator, progress=progress,
                progress_offset=steps_done, progress_total=total_steps,
                progress_state=f"SHOT {window.index + 1}/{len(windows)}",
                layout=ref2va_layout, is_cancelled=is_cancelled,
            )
            if is_cancelled is not None and is_cancelled():
                # Same rationale as the non-director path: don't pay for this
                # window's decode/mp4-encode once cancellation is observed.
                raise SamplingCancelled()
            steps_done += self._window_step_count(c, window)
            n_cv = layout.num_condition_video_rows
            n_ca = layout.num_condition_audio_rows

            latent = unpatchify_video_rows(
                video_rows[n_cv:], num_latent_frames=window.num_latent_frames,
                latent_height=c.latent_height, latent_width=c.latent_width,
                channels=VIDEO_LATENT_CHANNELS, patch_size=PATCH_SIZE,
            )
            previous_latent = latent
            previous_audio_rows = audio_rows[n_ca:]
            previous_audio_latents = window.num_audio_latents

            # The overlap is context the model was TOLD to reproduce, so it is
            # the previous window's footage a second time. Emitting it is the
            # "the shot resets to its first frame" class of bug; trim it off
            # both streams before this window joins the timeline.
            frames = self._decode_video(c, latent)[window.overlap_frames:]
            track = _trim_audio_head(
                self._resolve_audio(
                    c, audio_rows, num_audio_latents=window.num_audio_latents,
                    num_condition_audio_rows=n_ca,
                ),
                window.overlap_frames,
            )
            window_frames.append(frames)
            tracks.append(track)

            clip = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False).name
            encode_frames_to_mp4(frames, clip, fps=FPS, audio=track)
            clips.append(clip)
            emit_gallery(
                progress.emit, images=[], seeds=None, videos=[clip],
                video_resolution=getattr(self, "_video_resolution", None),
            )

        restore_dit_best_effort(c.bundle.dit, c.device)

        if not plan.stitch:
            self._audio_results.extend(tracks)
            return clips

        stitched_track = plan.mux_audio_path or _concat_audio_tracks(tracks)
        stitched = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False).name
        encode_frames_to_mp4(np.concatenate(window_frames, axis=0), stitched, fps=FPS, audio=stitched_track)
        self._audio_results.append(stitched_track)
        return [stitched]

    def _window_step_count(self, c: _MiniMaxH3Ctx, window: WindowPlan) -> int:
        """Model evaluations one window runs -- the schedule's length, which a
        manual sigma grid owns outright, not the requested step count."""
        video_schedule, _ = resolve_schedules(
            window.steps if window.steps is not None else c.steps,
            manual_video=c.manual_sigmas, manual_audio=c.manual_audio_sigmas, scheduler=c.scheduler,
            video_shift=c.video_sigma_shift, denoise=c.denoise,
        )
        return int(video_schedule.timesteps.numel())

    def _window_condition_rows(
        self, c: _MiniMaxH3Ctx, window: WindowPlan, previous_latent: Optional[Tensor],
        generator: torch.Generator,
    ) -> tuple[tuple, Tensor]:
        """This window's `(keyframe_anchors, condition_rows)`.

        Continuation latents come FIRST (anchored at latent frames
        `0 .. overlap_latents-1`), then the document's own images at their
        placed anchors -- and the rows are concatenated in exactly that order,
        because `build_packed_sequence` lays condition blocks out positionally
        against `keyframe_anchors`.

        The tail latents skip `conditioning.encode_keyframe_condition`
        entirely: they are already normalized latents straight off the
        previous window's sampler, so encoding them would mean decoding to
        pixels and re-encoding, losing a VAE round trip's worth of detail for
        nothing. Everything downstream of the encode is shared -- the same
        `KEYFRAME_NOISE_AUG` noise augmentation, one noise draw per condition
        frame, the same patchify.
        """
        anchors: list = []
        rows: List[Tensor] = []

        if window.overlap_latents and previous_latent is not None:
            tail = previous_latent[:, :, previous_latent.shape[2] - window.overlap_latents:]
            for offset in range(window.overlap_latents):
                frame = tail[:, :, offset: offset + 1].to(device=c.device, dtype=torch.float32)
                noise = torch.randn(frame.shape, generator=generator, device=c.device, dtype=torch.float32)
                rows.append(patchify_video_latents(
                    scale_noise(frame, KEYFRAME_NOISE_AUG, noise).to(c.dtype), PATCH_SIZE,
                ))
                anchors.append(offset)

        images = [c.director_images[kf.image_index] for kf in window.keyframes]
        image_anchors = tuple(kf.latent_index for kf in window.keyframes)
        if images:
            video_vae_module = _require_h3_video_vae(c.bundle.video_vae.module)
            c.bundle.video_vae.move_to(c.device)
            try:
                rows.append(prepare_keyframe_condition_rows(
                    images, image_anchors, vae_module=video_vae_module,
                    height=c.height, width=c.width, patch_size=PATCH_SIZE, device=c.device, dtype=c.dtype,
                    latents_mean=video_vae_module.latents_mean, latents_std=video_vae_module.latents_std,
                    generator=generator,
                ))
            finally:
                c.bundle.video_vae.offload()
            anchors.extend(image_anchors)

        if not rows:
            return (), torch.zeros(
                (0, VIDEO_LATENT_CHANNELS * PATCH_SIZE[0] * PATCH_SIZE[1] * PATCH_SIZE[2]),
                device=c.device, dtype=c.dtype,
            )
        return tuple(anchors), torch.cat(rows, dim=0)

    @staticmethod
    def _window_condition_audio(
        window: WindowPlan, previous_audio_rows: Optional[Tensor], previous_audio_latents: int, *,
        dtype: torch.dtype,
    ) -> Optional[Tensor]:
        """The clean condition-audio prefix for a continuation window.

        The prefix is a slice of the previous window's own audio latents,
        re-packed straight into row order -- no VAE, no waveform, and no noise
        draw, so the pipe's three-draws-in-order contract is untouched.

        WHICH slice matters. The layout places the prefix so that its LAST
        latent abuts the target's frame 0, and a continuation window's frame 0
        sits `overlap_frames` BEFORE the previous window ended (that is what
        the video overlap is). So the audio that belongs there is the slice
        ending one overlap short of the previous window's end -- not its final
        latents, which are the audio the overlap itself replays. Taking the
        plain tail instead would hand the model a prefix from the future and
        put the two streams out of step by the overlap, compounding per
        window.
        """
        overlap_latents = audio_latent_num_frames(window.overlap_frames)
        if not window.overlap_latents or previous_audio_rows is None or overlap_latents <= 0:
            return None
        end = previous_audio_latents - overlap_latents
        start = max(0, end - overlap_latents)
        if end <= start:
            return None
        latents = unpack_audio_rows(previous_audio_rows, num_audio_latents=previous_audio_latents)
        return pack_audio_rows(latents[..., start:end]).to(dtype)

    def _decode_video(self, c: _MiniMaxH3Ctx, latent: Tensor) -> np.ndarray:
        vae = c.bundle.video_vae
        vae_module = _require_h3_video_vae(vae.module)
        device = c.device
        vae.move_to(device)
        try:
            latents_mean = vae_module.latents_mean.to(device=device, dtype=torch.float32).view(1, -1, 1, 1, 1)
            latents_std = vae_module.latents_std.to(device=device, dtype=torch.float32).view(1, -1, 1, 1, 1)
            z = latent.to(device=device, dtype=torch.float32) * latents_std + latents_mean
            with torch.no_grad(), torch.autocast(
                device_type="cuda" if str(device).startswith("cuda") else "cpu",
                dtype=torch.float16, enabled=str(device).startswith("cuda"),
            ):
                video = vae_module.decode(z.to(dtype=vae.compute_dtype))
            pixel_mean = torch.tensor((0.485, 0.456, 0.406), device=device, dtype=torch.float32).view(1, -1, 1, 1, 1)
            pixel_std = torch.tensor((0.229, 0.224, 0.225), device=device, dtype=torch.float32).view(1, -1, 1, 1, 1)
            video = (video.float() * pixel_std + pixel_mean).clamp(0.0, 1.0)
        finally:
            vae.offload()
        return pixels_3thw_to_uint8_frames(video[0], value_range="unit")

    @staticmethod
    def _flatten(results: List[Any]) -> List[Any]:
        """A Director run produces several clips from ONE seed (the unstitched
        per-shot files), so `generate_one` returns a list there while every
        other path returns a single item."""
        flat: List[Any] = []
        for result in results:
            flat.extend(result) if isinstance(result, list) else flat.append(result)
        return flat

    def emit_results(self, generation_outputs: callable, results: List[Any], used_seeds: List[int]) -> None:
        if not bool(self.config.get("decode", True)):
            return  # a latent hand-off, no gallery/seed emission here
        emit_gallery(
            generation_outputs, images=[], seeds=used_seeds, videos=self._flatten(results),
            video_resolution=getattr(self, "_video_resolution", None),
        )

    def build_output(self, results: List[Any]) -> Dict[str, Any]:
        audio = list(getattr(self, "_audio_results", []))
        if not bool(self.config.get("decode", True)):
            return {"latent": results, "video": [], "audio": audio}
        return {"video": self._flatten(results), "latent": [], "audio": audio}
