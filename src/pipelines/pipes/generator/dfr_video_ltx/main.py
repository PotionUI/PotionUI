"""DFR temporal densification rounds for the native LTX-2.5 family.

Phase C of Diffusion Fidelity Rendering: takes the finished video latent from
the base pass (or from the upscale/refine stage) and runs 0-2 **temporal
rounds** over it, each doubling the frame count and the frame rate, then
decodes once and muxes the base pass's audio track verbatim.

One round is: temporal-upsample the latent (``T -> 2T - 1`` latent frames), cut
the new timeline into tiles that meet at shared keyframe seams, re-denoise each
tile independently from a partially-noised start with the carried keyframes
pinned as anchors, and concatenate the tiles back along the temporal axis. Each
round's keyframes become the next round's anchors, at doubled positions.

Not a ``BaseGeneratorPipe`` seed-loop pipe: tile *i* of round *r* shares carried
state with tile *i-1* and with round *r-1*, so this pipe owns its own sequential
``process()`` -- the same reason ``generator/chain_video_wan22`` does (read that
pipe for the shape of the loop; none of its implementation transfers, since it
stitches encoded mp4 segments in the pixel domain and this stitches latents).

The layout arithmetic -- canvas segment grid, tile windows, dropped prefixes,
stitch plan -- lives in ``_shared/generation/dfr_layout.py`` and is unit-tested
against the specification's worked tables. Nothing here recomputes it.

**Increment 1 scope.** Rounds carry *anchors only*: there are no generated
keyframe slots yet, so the bag is synthesized from the incoming latent
(``anchors.py``) and thereafter only doubles. Round 2 therefore runs on the
round-1 seams doubled -- coarser seams than the full scheme, which
``reanchor_each_round`` can restore at the cost of one extra decode per round.
The keyframe absolute-position embedding is not involved at all: anchors are
ordinary given-content conditioning, so this runs on any LTX-2.5 checkpoint.

Conventions this pipe holds constant:

* **Mask polarity is STRENGTH** -- 1 = clean/fully pinned, 0 = free generation
  target (per-token timestep ``sigma * (1 - mask)``). Carried anchors go in at
  strength 0.95, not 1.0: a seam frozen absolutely gives the tile that must
  reconcile its content across the seam no freedom to settle it. Every
  conditioning interface below states its polarity where it is used.
* **Three frame rates, and they are not interchangeable.** The base fps is what
  the form asked for. The *conditioning* fps -- ``min(60, base * 2**round)`` --
  is the RoPE time base handed to the DiT, and the cap is load-bearing: RoPE
  time is ``pixel_frame / fps``, so an uncapped 96 fps base at round 2 puts
  every token's temporal span at 5/8 of the trained distribution, whose recorded
  failure signature is a motion spike at each latent boundary followed by a
  stall. The *playback* fps -- ``base * 2**rounds``, uncapped -- governs decode
  and the muxed container only. All three are logged every round and the
  conditioning fps is asserted.
* **Sigmas are parsed VERBATIM** (``parse_explicit_sigmas``, like
  ``generator/video_ltx``'s ``refine_sigmas``), never through
  ``manual_sigmas``/``flow_schedule``'s manual mode, which force-overwrites
  ``sigmas[0] = 1.0`` and would silently turn every tile's partial-noise start
  back into a fresh generation.
* **Seed streams** extend the existing convention: initial noise comes from one
  stateful ``Generator(seed)`` per call, the per-tile ancestral noise from
  ``seed + ANCESTRAL_NOISE_SEED_OFFSET + 1000*round + tile`` (tiles are
  positionally identical, so a shared ancestral stream would inject
  byte-identical noise into every tile and correlate them visibly), and the
  decode from ``seed + DECODE_NOISE_SEED_OFFSET``.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import numpy as np
import torch

from src.pipelines.contracts import BasePipe, logger
from src.pipelines.contracts import (
    IOType,
    PipeConfigSpec,
    PipeInput,
    PipeInputSpec,
    PipeOutput,
    PipeOutputSpec,
)
from src.pipelines.outputs import Icon
from src.platform.runtime.device import clear_gpu_memory
from src.platform.runtime.native.sampling import (
    ANCESTRAL_NOISE_SEED_OFFSET,
    ProgressHook,
    denoise_prenoised,
)
from src.pipelines.pipes._shared.generation.dfr_layout import (
    double_positions,
    merge_keyframe_bag,
    plan_canvas,
    plan_round,
    reanchor_positions,
    resolve_tile_count,
    round_output_frames,
    tile_local_placements,
)
from src.pipelines.pipes._shared.generation.dit_placement import place_dit_for_sequence
from src.pipelines.pipes._shared.generation.dit_restore import restore_dit_best_effort
from src.pipelines.pipes._shared.generation.ltx_conditioned_forward import ConditionedAVForward
from src.pipelines.pipes._shared.generation.progress import ProgressEmitter
from src.pipelines.pipes._shared.media.video_encode import encode_frames_to_mp4
from src.pipelines.pipes._shared.vae.ltx_latent_upsample import upsample_ltx_latent
from src.pipelines.pipes.generator.dfr_video_ltx.anchors import bag_items, synthesize_anchor_bag
from src.pipelines.pipes.generator.txt2vid_ltx.main import (
    _LATENT_CHANNELS,
    _SPATIAL_DOWNSCALE,
    _TEMPORAL_DOWNSCALE,
    _decode_video,
    _snap_geometry,
    parse_explicit_sigmas,
    release_idle_te,
)
from src.pipelines.pipes.generator.txt2vid_wan22.main import (
    _attach_nag,
    _emit_video_results,
    _to_device,
)
from src.pipelines.pipes.generator.video_ltx.conditioning import (
    LTXMediaCondition,
    PreparedConditioning,
    _pack,
    merge_initial_latent_tokens,
    mix_initial_noise,
    prepare_ltx_conditions,
)
from src.pipelines.pipes.generator.video_ltx.main import _to_frames_tensor

Tensor = torch.Tensor

_LOG_TAG = "GENERATOR DFR-LTX"
# The distilled schedule from its index-4 knot onward -- the temporal rounds
# start at a HIGHER noise level (0.975) than the detailing pass does (0.909375).
_DEFAULT_ROUND_SIGMAS = "0.975,0.909375,0.725,0.421875,0.0"
# RoPE time base ceiling. Playback fps is deliberately NOT capped.
_CONDITIONING_FPS_CAP = 60.0
_MAX_ROUNDS = 2


@dataclass
class _TileCtx:
    """The duck-typed view :class:`ConditionedAVForward` reads, per tile.

    ``fps`` here is the round's CONDITIONING fps (the capped RoPE time base),
    never the playback fps.
    """

    prepared: PreparedConditioning
    fps: float
    t_lat: int
    h_lat: int
    w_lat: int
    device: str
    dtype: torch.dtype
    audio_tokens: int = 0


@dataclass
class _DecodeShim:
    """Adapter for ``txt2vid_ltx._decode_video``, which expects a ctx exposing
    ``vae`` and ``device``."""

    vae: Any
    device: str


def _resolve_pixel_frame(frame: Any, frames: int) -> int:
    """Placement ``frame`` (pixel index, ``"first"``, ``"last"``) -> an absolute
    pixel index on the BASE timeline, clamped into range.

    Clamping rather than raising mirrors ``generator/video_ltx``'s
    ``_resolve_latent_index``: placements are computed upstream from an
    UNSNAPPED ``duration * fps``, so an end-of-clip keyframe legitimately lands
    one or two frames past the snapped total.
    """
    if frame in ("first", 0, "0", None):
        return 0
    if frame == "last":
        return frames - 1
    index = int(frame)
    if index < 0:
        index = index % frames
    return max(0, min(index, frames - 1))


def _pad_latent_to_canvas(latent: Tensor, target_latent_frames: int) -> Tensor:
    """Repeat the last latent frame up to ``target_latent_frames``.

    The canvas may be longer than the request when the content length is not a
    whole number of segments; the excess is trimmed back off after the last
    round. (Increment 2 moves this padding into the base pass so the model
    *generates* the tail instead of freezing it.)
    """
    have = int(latent.shape[2])
    if have >= target_latent_frames:
        return latent
    tail = latent[:, :, -1:].expand(-1, -1, target_latent_frames - have, -1, -1)
    return torch.cat([latent, tail], dim=2)


class GeneratorDfrLtxPipe(BasePipe):
    name = "generator"
    description = ("Native LTX-2.5 DFR temporal rounds: densify a finished video latent to 2x/4x "
                   "the frame rate with tiled, keyframe-anchored re-denoising")

    @classmethod
    def get_default_config(cls) -> Dict[str, Any]:
        return {
            "rounds": 1,
            "fps": 25.0,
            "frames": 121,
            "resolution": "768x512",
            "round_sigmas": _DEFAULT_ROUND_SIGMAS,
            "anchor_strength": 0.95,
            "ancestral_eta": 0.5,
            "cfg": 1.0,
            "num_tiles_override": 0,
            "max_tile_tokens": 0,
            "reanchor_each_round": False,
            "media_placements": [],
            "decode": True,
            "device": "cuda",
            "nag_scale": 1.0,
            "nag_tau": 3.5,
            "nag_alpha": 0.5,
        }

    @classmethod
    def configuration(cls) -> List[PipeConfigSpec]:
        return [
            PipeConfigSpec("rounds", int, 1,
                           "Temporal densification rounds. Each doubles the frame count and the "
                           "playback frame rate (0 = decode the incoming latent unchanged, useful "
                           "for an A/B against the same seed)",
                           required=False, min_value=0, max_value=_MAX_ROUNDS),
            PipeConfigSpec("fps", float, 25.0,
                           "BASE frame rate of the incoming latent. The output plays at "
                           "fps * 2**rounds; the RoPE conditioning frame rate is capped at 60",
                           required=False, min_value=1.0, max_value=60.0),
            PipeConfigSpec("frames", int, 121,
                           "Requested pixel frames (cross-check only -- the real count is derived "
                           "from the incoming latent, as in generator/video_ltx)",
                           required=False, min_value=9, max_value=1001),
            PipeConfigSpec("resolution", str, "768x512",
                           "Expected resolution (WxH) of the incoming latent, cross-checked before "
                           "any GPU work", required=False),
            PipeConfigSpec("round_sigmas", str, _DEFAULT_ROUND_SIGMAS,
                           "Sigma schedule for every tile of every round, comma-separated, "
                           "descending, used VERBATIM (like generator/video_ltx's refine_sigmas: "
                           "the head is NOT forced to 1.0, which is what keeps the partial-noise "
                           "start intact)", required=False),
            PipeConfigSpec("anchor_strength", float, 0.95,
                           "Conditioning strength of a carried seam keyframe (1 = fully pinned, "
                           "0 = free). Deliberately just short of 1.0 so the tiles either side of "
                           "a seam can settle it between them", required=False,
                           min_value=0.0, max_value=1.0),
            PipeConfigSpec("ancestral_eta", float, 0.5,
                           "Ancestral (SDE) Euler eta for the per-tile denoise", required=False,
                           min_value=0.0, max_value=1.0),
            PipeConfigSpec("cfg", float, 1.0,
                           "True CFG scale for the per-tile denoise. 1.0 (single forward) matches "
                           "the refine character of a round; the negative prompt then reaches the "
                           "pass only through NAG", required=False, min_value=1.0, max_value=20.0),
            PipeConfigSpec("num_tiles_override", int, 0,
                           "Force a tile count per round instead of 2**round (0 = automatic). "
                           "A larger count is the VRAM relief valve: the seam, lead-in and drop "
                           "rules are unchanged, tiles just own fewer segments each",
                           required=False, min_value=0, max_value=64),
            PipeConfigSpec("max_tile_tokens", int, 0,
                           "Raise a round's tile count until no tile exceeds this projected video "
                           "token count (0 = off). The finer split never changes the seam geometry",
                           required=False, min_value=0),
            PipeConfigSpec("reanchor_each_round", bool, False,
                           "Re-derive the keyframe bag from each round's own stitched output at "
                           "full canvas density instead of only doubling the carried positions. "
                           "Costs one extra decode+encode per round; without generated keyframe "
                           "slots it is the only way to keep the seams from halving in density "
                           "every round", required=False),
            PipeConfigSpec("media_placements", list, [],
                           "Media conditioning placements, same shape as the generator pipes': "
                           "[{source: image, index, frame: int|first|last, strength}]. Re-applied "
                           "per tile at tile-LOCAL frame indices; only image sources are supported",
                           required=False),
            PipeConfigSpec("decode", bool, True,
                           "Decode to video; false emits the densified latent instead",
                           required=False),
            PipeConfigSpec("device", str, "cuda", "Compute device", required=False,
                           choices=["cuda", "cpu"]),
            PipeConfigSpec("nag_scale", float, 1.0,
                           "Normalized Attention Guidance scale (1.0 = off). The only channel by "
                           "which the negative prompt reaches a cfg-1.0 round", required=False,
                           min_value=1.0, max_value=20.0),
            PipeConfigSpec("nag_tau", float, 3.5, "NAG norm-clamp threshold", required=False,
                           min_value=0.1, max_value=20.0),
            PipeConfigSpec("nag_alpha", float, 0.5, "NAG blend-back weight", required=False,
                           min_value=0.0, max_value=1.0),
        ]

    @classmethod
    def inputs(cls) -> List[PipeInputSpec]:
        return [
            PipeInputSpec("model", IOType.MODEL, True,
                          "LTX model bundle -- the SAME bundle the base/refine stages used, so the "
                          "rounds share their LoRA chain. Needs 'temporal_upscale_model' loaded "
                          "whenever rounds > 0", is_array=False),
            PipeInputSpec("conditioning", IOType.CONDITIONING, True,
                          "Encoded prompt conditioning (the same the earlier stages consumed)",
                          is_array=True),
            PipeInputSpec("latent", IOType.LATENT, True,
                          "Finished video latent to densify -- the upscale/refine stage's raw "
                          "latent output when upscaling, otherwise the base pass's", is_array=True),
            PipeInputSpec("seed", IOType.SEED, False, "Random seeds", is_array=True),
            PipeInputSpec("image", IOType.IMAGE, False,
                          "Conditioning images, re-applied per tile at tile-local indices",
                          is_array=True),
            PipeInputSpec("audio", IOType.AUDIO, False,
                          "Finished audio track from the base pass, muxed verbatim into the final "
                          "encode (rounds generate no audio of their own)", is_array=True),
            PipeInputSpec("MODELS", IOType.SERVICE, False,
                          "Model lifecycle service, to release the idle TE's host RAM",
                          is_array=False),
        ]

    @classmethod
    def outputs(cls) -> List[PipeOutputSpec]:
        return [
            PipeOutputSpec("video", IOType.VIDEO, "Densified videos (empty when decode=false)",
                           is_array=True),
            PipeOutputSpec("latent", IOType.LATENT,
                           "Densified per-seed latents (only populated when decode=false)",
                           is_array=True),
            PipeOutputSpec("audio", IOType.AUDIO, "The passed-through audio track(s)", is_array=True),
        ]

    # -- process ------------------------------------------------------------

    def process(
            self,
            pipe_input: PipeInput,
            generation_outputs: callable,
            is_cancelled: Optional[callable] = None,
    ) -> PipeOutput:
        bundle = pipe_input.input["model"]
        conditioning = pipe_input.input.get("conditioning") or []
        images = pipe_input.input.get("image") or []
        audio_tracks = pipe_input.input.get("audio") or []
        seeds = list(pipe_input.input.get("seed") or [])

        raw_latent = pipe_input.input.get("latent")
        if raw_latent is None:
            raise ValueError("generator/dfr_video_ltx requires a 'latent' input (the finished "
                             "video latent from the base or refine stage)")
        latents = list(raw_latent) if isinstance(raw_latent, (list, tuple)) else [raw_latent]

        if bundle.spec.family != "ltx":
            raise ValueError(
                f"generator/dfr_video_ltx: loaded model '{bundle.spec.family}/"
                f"{bundle.spec.variant}' is not an LTX checkpoint")

        rounds = int(self.config.get("rounds", 1))
        if not 0 <= rounds <= _MAX_ROUNDS:
            raise ValueError(f"generator/dfr_video_ltx: rounds must be 0..{_MAX_ROUNDS}, got {rounds}")
        if rounds > 0 and getattr(bundle, "temporal_upsampler", None) is None:
            raise ValueError(
                "generator/dfr_video_ltx: rounds > 0 requires the LTX-2.5 temporal x2 latent "
                "upscaler -- set model_loader/ltx's 'temporal_upscale_model' config to it "
                "(the spatial upscaler is a different checkpoint and cannot stand in)")

        release_idle_te(bundle, pipe_input.input.get("MODELS"), _LOG_TAG)

        device = self.config.get("device", "cuda")
        dtype = bundle.dit.compute_dtype
        base_fps = float(self.config.get("fps", 25.0))
        progress = ProgressEmitter(generation_outputs, title="Motion smoothing")

        videos: List[str] = []
        out_latents: List[Tensor] = []
        used_seeds: List[int] = []
        for index, latent in enumerate(latents):
            if is_cancelled and is_cancelled():
                logger.info("[%s] cancelled before latent %d", _LOG_TAG, index + 1)
                break
            seed = int(seeds[index]) if index < len(seeds) else (int(seeds[-1]) if seeds else 0)
            cond_model = conditioning[index] if index < len(conditioning) else conditioning[-1]
            result = self._densify_one(
                bundle=bundle, latent=latent, cond_model=cond_model, seed=seed,
                images=images, device=device, dtype=dtype, base_fps=base_fps,
                rounds=rounds, progress=progress, is_cancelled=is_cancelled,
            )
            if result is None:  # cancelled mid-round
                break
            used_seeds.append(seed)
            audio_track = audio_tracks[index] if index < len(audio_tracks) else (
                audio_tracks[0] if audio_tracks else None)

            if not bool(self.config.get("decode", True)):
                out_latents.append(result)
                continue
            videos.append(self._decode_and_mux(
                bundle, result, seed, device, base_fps * (2 ** rounds), audio_track))

        restore_dit_best_effort(bundle.dit, device)

        if videos:
            _emit_video_results(generation_outputs, videos, used_seeds,
                                resolution=getattr(self, "_resolution", None))
        return PipeOutput(output={
            "video": videos,
            "latent": out_latents,
            "audio": list(audio_tracks),
        })

    # -- one latent through every round -------------------------------------

    def _densify_one(
            self, *, bundle, latent: Tensor, cond_model, seed: int, images: List[Any],
            device: str, dtype, base_fps: float, rounds: int,
            progress: ProgressEmitter, is_cancelled,
    ) -> Optional[Tensor]:
        latent = latent.to(device=device, dtype=dtype)
        _, _, t_lat_in, h_lat, w_lat = latent.shape
        width, height = w_lat * _SPATIAL_DOWNSCALE, h_lat * _SPATIAL_DOWNSCALE
        self._resolution = (width, height)
        frames_in = (t_lat_in - 1) * _TEMPORAL_DOWNSCALE + 1
        self._preflight(frames_in, width, height)

        if rounds == 0:
            # Pass-through: no upsample, no tiles, no anchors -- just the same
            # decode/mux the rounds path ends with, so an A/B against rounds > 0
            # differs in exactly one variable.
            logger.info("[%s] rounds=0: passing the %d-frame latent through unchanged",
                        _LOG_TAG, frames_in)
            return latent

        canvas = plan_canvas(frames_in)
        canvas_latents = (canvas.canvas_frames - 1) // _TEMPORAL_DOWNSCALE + 1
        latent = _pad_latent_to_canvas(latent, canvas_latents)
        logger.info(
            "[%s] canvas: %d requested frames -> segment %d, %d canvas frames (+%d pad), "
            "slots %s", _LOG_TAG, frames_in, canvas.segment_length, canvas.canvas_frames,
            canvas.padding_frames, list(canvas.slot_positions),
        )

        progress.state("Synthesizing seam keyframes", icon=Icon(name="bolt", effect="pulse"))
        bag = self._synthesize_bag(bundle, latent, canvas.slot_positions, seed, device, dtype)

        placements = self._resolve_placements(frames_in, images)
        sigmas = parse_explicit_sigmas(str(self.config.get("round_sigmas") or _DEFAULT_ROUND_SIGMAS))
        init_gen = torch.Generator(device=device).manual_seed(int(seed))

        current_frames = canvas.canvas_frames
        for round_index in range(1, rounds + 1):
            # Positions double onto the round's timeline; the latents are
            # single-frame, so only their positions scale.
            carried = sorted(bag.items())
            bag = dict(zip(double_positions(p for p, _ in carried),
                           (v for _, v in carried)))

            progress.state(f"Temporal upsample (round {round_index}/{rounds})",
                           icon=Icon(name="bolt", effect="pulse"))
            latent = upsample_ltx_latent(bundle, bundle.temporal_upsampler, latent, device)
            latent = latent.to(dtype=dtype)
            current_frames = 2 * current_frames - 1

            conditioning_fps = min(_CONDITIONING_FPS_CAP, base_fps * (2 ** round_index))
            playback_fps = base_fps * (2 ** rounds)
            assert conditioning_fps <= _CONDITIONING_FPS_CAP
            logger.info(
                "[%s] round %d/%d: %d frames, fps base=%.3f conditioning=%.3f (cap %.0f) "
                "playback=%.3f", _LOG_TAG, round_index, rounds, current_frames,
                base_fps, conditioning_fps, _CONDITIONING_FPS_CAP, playback_fps,
            )

            latent = self._run_round(
                bundle=bundle, latent=latent, bag=bag, frames=current_frames,
                round_index=round_index, rounds=rounds, seed=seed, sigmas=sigmas,
                cond_model=cond_model, images=images, placements=placements,
                conditioning_fps=conditioning_fps, device=device, dtype=dtype,
                h_lat=h_lat, w_lat=w_lat, width=width, height=height,
                init_gen=init_gen, progress=progress, is_cancelled=is_cancelled,
            )
            if latent is None:
                return None

            if round_index == rounds:
                break  # nothing consumes the bag after the last round
            if bool(self.config.get("reanchor_each_round", False)):
                positions = reanchor_positions(current_frames, canvas.segment_length)
                logger.info("[%s] round %d: re-anchoring at %d canvas positions",
                            _LOG_TAG, round_index, len(positions))
                bag = self._synthesize_bag(bundle, latent, positions, seed, device, dtype)
            else:
                # Increment 1 generates no mid-segment slots, so the merge is
                # the doubled anchors alone -- run it through the real merge so
                # the bag contract (non-empty, sorted, slot-wins) is exercised
                # by the same code increment 2 will feed slots into.
                bag = dict(merge_keyframe_bag(sorted(bag.items()), []))

        return self._trim(latent, frames_in, rounds)

    def _preflight(self, frames_in: int, width: int, height: int) -> None:
        if (frames_in - 1) % _TEMPORAL_DOWNSCALE != 0:
            raise ValueError(
                f"generator/dfr_video_ltx: the incoming latent decodes to {frames_in} frames, "
                f"which is not on the causal VAE's 1 + 8k grid")
        configured = str(self.config.get("resolution", "")).lower().split("x")
        if len(configured) == 2 and configured[0].strip().isdigit():
            # Compare through the SAME snap the generator stages applied (see
            # the frames comparison below): a preset carries the raw form
            # value (e.g. 720x480) while the stage that produced this latent
            # already snapped it onto the LTX grid (704x480).
            want_w, want_h, _ = _snap_geometry(int(configured[0]), int(configured[1]), frames_in)
            if (want_w, want_h) != (width, height):
                raise ValueError(
                    f"generator/dfr_video_ltx: the incoming latent is {width}x{height} but this "
                    f"node is configured for {want_w}x{want_h} (after grid snap) -- the rounds "
                    f"run at the latent's own resolution, so the two must agree")
        configured_frames = int(self.config.get("frames", 0) or 0)
        if configured_frames:
            # Compare through the SAME snap the generator stages applied, or
            # this fires on every ordinary render: a preset computes `frames`
            # as an unsnapped `duration * fps` (5.0 s at 24 fps -> 120) and the
            # stage that produced this latent already snapped it onto 1 + 8k.
            _, _, snapped = _snap_geometry(width, height, configured_frames)
            if snapped != frames_in:
                logger.warning(
                    "[%s] configured frames=%d (snaps to %d) but the incoming latent carries "
                    "%d -- using the latent's own count",
                    _LOG_TAG, configured_frames, snapped, frames_in)

    # -- anchors ------------------------------------------------------------

    def _synthesize_bag(self, bundle, latent: Tensor, positions, seed: int,
                        device: str, dtype) -> Dict[int, Tensor]:
        """Decode once, then VAE-encode each seam frame as a standalone
        one-frame clip. Slicing a mid-stream latent frame is NOT equivalent --
        see ``anchors.py``."""
        shim = _DecodeShim(vae=bundle.vae, device=device)

        def decode(z: Tensor) -> np.ndarray:
            return _decode_video(shim, z, seed)

        def encode_frame(pixels: Tensor) -> Tensor:
            bundle.vae.move_to(device)
            try:
                with torch.no_grad():
                    return bundle.vae.module.encode(
                        pixels.to(device=device, dtype=bundle.vae.compute_dtype)).to(dtype=dtype)
            finally:
                bundle.vae.offload()

        bag = synthesize_anchor_bag(latent, list(positions), decode=decode,
                                    encode_frame=encode_frame)
        clear_gpu_memory()
        return bag

    # -- one round ----------------------------------------------------------

    def _run_round(
            self, *, bundle, latent: Tensor, bag: Dict[int, Tensor], frames: int,
            round_index: int, rounds: int, seed: int, sigmas, cond_model, images,
            placements, conditioning_fps: float, device: str, dtype,
            h_lat: int, w_lat: int, width: int, height: int, init_gen,
            progress: ProgressEmitter, is_cancelled,
    ) -> Optional[Tensor]:
        seams = sorted(bag)
        tokens_per_latent_frame = h_lat * w_lat
        num_tiles = resolve_tile_count(
            seams, round_index=round_index,
            override=int(self.config.get("num_tiles_override", 0) or 0) or None,
            tokens_per_latent_frame=tokens_per_latent_frame,
            max_tile_tokens=int(self.config.get("max_tile_tokens", 0) or 0) or None,
        )
        layout = plan_round(seams, frames=frames, num_tiles=num_tiles)
        logger.info("[%s] round %d: %d seam(s), %d tile(s), owned segments %s",
                    _LOG_TAG, round_index, len(seams), len(layout.tiles),
                    [t.owned_segments for t in layout.tiles])

        pieces: List[Tensor] = []
        for tile in layout.tiles:
            if is_cancelled and is_cancelled():
                logger.info("[%s] cancelled during round %d, tile %d",
                            _LOG_TAG, round_index, tile.index + 1)
                return None
            progress.state(
                f"Round {round_index}/{rounds}, tile {tile.index + 1}/{len(layout.tiles)}",
                icon=Icon(name="film", effect="pulse"))
            denoised = self._denoise_tile(
                bundle=bundle, latent=latent, tile=tile, bag=bag, round_index=round_index,
                seed=seed, sigmas=sigmas, cond_model=cond_model, images=images,
                placements=placements,
                conditioning_fps=conditioning_fps, device=device, dtype=dtype,
                h_lat=h_lat, w_lat=w_lat, width=width, height=height,
                init_gen=init_gen, progress=progress, is_cancelled=is_cancelled,
            )
            # Hard concatenation of disjoint latent ranges: no overlap blending,
            # no crossfade, no weighted seam. The dropped prefix covers the
            # tile's lead-in (whose local latents 0 and 1 are image-anchored and
            # must never enter the mid-canvas stream) plus the seam latent the
            # previous tile already owns.
            pieces.append(denoised[:, :, tile.drop_latent_prefix:])

        stitched = torch.cat(pieces, dim=2)
        if stitched.shape[2] != layout.expected_latents:
            raise RuntimeError(
                f"generator/dfr_video_ltx: round {round_index} stitched to "
                f"{stitched.shape[2]} latent frames but the layout predicts "
                f"{layout.expected_latents} -- the seam handover is off")
        return stitched

    def _denoise_tile(
            self, *, bundle, latent: Tensor, tile, bag: Dict[int, Tensor], round_index: int,
            seed: int, sigmas, cond_model, images, placements,
            conditioning_fps: float, device: str, dtype, h_lat: int, w_lat: int,
            width: int, height: int, init_gen, progress: ProgressEmitter, is_cancelled,
    ) -> Tensor:
        tile_latent = latent[:, :, tile.latent_start:tile.latent_end]
        t_lat = int(tile_latent.shape[2])
        local_frames = tile.local_frames

        conditions = self._tile_conditions(
            tile=tile, bag=bag, images=images, placements=placements,
            round_index=round_index)

        bundle.vae.move_to(device)
        vae_module = bundle.vae.module

        def vae_encode(pixels: Tensor) -> Tensor:
            with torch.no_grad():
                return vae_module.encode(pixels.to(dtype=bundle.vae.compute_dtype))

        prepared = prepare_ltx_conditions(
            conditions, vae_encode, frames=local_frames, height=height, width=width,
            device=device, dtype=dtype, latent_channels=_LATENT_CHANNELS)
        bundle.vae.offload()

        # Seed the base token slice from the tile's own (upsampled) latent, the
        # partial-noise start this whole scheme rests on. Masked base positions
        # -- the window-opening anchor and any tile-local image -- keep their
        # freshly encoded conditioning values instead; `clean` is left untouched
        # because both blend sites weight it by the mask, so its value at mask=0
        # positions is never read.
        packed = _pack(tile_latent)
        if packed.shape[1] != prepared.base_tokens:
            raise RuntimeError(
                f"generator/dfr_video_ltx: tile {tile.index} packs {packed.shape[1]} base tokens "
                f"but its conditioning was built for {prepared.base_tokens}")
        tokens = merge_initial_latent_tokens(prepared, packed)
        prepared = PreparedConditioning(
            tokens=tokens, mask=prepared.mask, clean=prepared.clean,
            extra_coords=prepared.extra_coords, n_extra=prepared.n_extra,
            base_tokens=prepared.base_tokens,
        )

        ctx = _TileCtx(prepared=prepared, fps=conditioning_fps, t_lat=t_lat,
                       h_lat=h_lat, w_lat=w_lat, device=device, dtype=dtype)
        s_video = prepared.base_tokens + prepared.n_extra
        sigma0 = float(sigmas[0])
        noise = torch.randn((1, s_video, _LATENT_CHANNELS), generator=init_gen,
                            device=device, dtype=dtype)
        x = mix_initial_noise(prepared, noise, sigma0)

        place_dit_for_sequence(
            bundle.dit, device, video_tokens=s_video, audio_tokens=0,
            own_models=tuple(m for m in (bundle.dit, bundle.vae, bundle.temporal_upsampler)
                             if m is not None),
        )
        forward = ConditionedAVForward(bundle.dit.module, ctx)

        cond = _to_device(cond_model.embeds, device, dtype)
        uncond = _to_device(cond_model.n_embeds, device, dtype) if cond_model.n_embeds else None
        cond = _attach_nag(cond, uncond, self.config)

        # Tiles are positionally identical -- same resolution, same local frame
        # layout, same conditioning structure -- so a shared ancestral stream
        # would inject byte-identical noise into every one of them and correlate
        # them visibly. This extends the documented stream-offset convention
        # (init at `seed`, ancestral at +10000, decode at +20000) with a
        # per-round/per-tile offset inside the ancestral band.
        tile_gen = torch.Generator(device=device).manual_seed(
            tile_noise_seed(seed, round_index, tile.index))

        def on_progress(_frac, step_index, total):
            progress.step(step_index + 1, total, state=f"Tile {tile.index + 1}",
                          icon=Icon(name="film", effect="pulse"))

        x = denoise_prenoised(
            forward, x, cond, uncond,
            steps=len(sigmas) - 1, sampler_name="euler_ancestral",
            sampling_settings=dict(bundle.spec.sampling_settings),
            guidance_scale=float(self.config.get("cfg", 1.0)),
            sigmas=sigmas, hooks=[ProgressHook(on_progress)],
            is_cancelled=is_cancelled,
            sampler_options={
                "eta": float(self.config.get("ancestral_eta", 0.5)),
                "generator": tile_gen,
            },
        )
        bundle.dit.offload()
        clear_gpu_memory()
        return forward.unpack_base(x)

    def _tile_conditions(self, *, tile, bag, images, placements,
                         round_index: int) -> List[LTXMediaCondition]:
        """Tile-local conditioning, in the order the reference applies it:
        images first, then the carried anchors.

        Both are addressed in TILE-LOCAL pixel frames -- frame index 0 means
        this tile's first frame, so re-applying the opening image on a non-first
        tile would pin the wrong frame onto the seam. Anchor strength is
        ``anchor_strength`` (1 = clean); image strengths are the caller's own.
        """
        conditions: List[LTXMediaCondition] = []

        scale = 2 ** round_index
        scaled = [{**p, "frame": int(p["frame"]) * scale} for p in placements]
        for placement in tile_local_placements(tile, scaled):
            local = int(placement["frame"])
            frames_tensor = _to_frames_tensor(images[int(placement["index"])])
            conditions.append(LTXMediaCondition(
                frames=frames_tensor,
                # local == 0 addresses the tile's own first latent frame (the
                # first-frame overwrite); anything else is an appended token at
                # that exact tile-local pixel frame.
                latent_index=0,
                pixel_frame_index=None if local == 0 else local,
                strength=float(placement.get("strength", 1.0)),
            ))

        anchor_strength = float(self.config.get("anchor_strength", 0.95))
        for position, anchor in bag_items(bag, tile.anchors):
            local = tile.to_local(position)
            conditions.append(LTXMediaCondition(
                latent=anchor,
                # local == 0 is the tile's window-opening anchor: it lands on
                # the tile's own first latent frame, which under causal encoding
                # already covers exactly one pixel frame, so it goes in as a
                # first-frame overwrite rather than an appended token.
                latent_index=0,
                pixel_frame_index=None if local == 0 else local,
                strength=anchor_strength,
            ))
        return conditions

    def _resolve_placements(self, frames_in: int, images: List[Any]) -> List[Dict[str, Any]]:
        resolved: List[Dict[str, Any]] = []
        for placement in list(self.config.get("media_placements") or []):
            source = placement.get("source", "image")
            if source != "image":
                raise ValueError(
                    f"generator/dfr_video_ltx: media placement source {source!r} is not supported "
                    f"-- temporal rounds re-apply conditioning per tile at tile-local indices, "
                    f"which is only defined for still images")
            if placement.get("role", "keyframe") != "keyframe":
                raise ValueError(
                    "generator/dfr_video_ltx: role='reference' (IC-LoRA) conditioning is not "
                    "supported in temporal rounds -- its coordinates are tied to the whole-clip "
                    "coordinate frame, not a tile window")
            index = int(placement.get("index", 0))
            if index >= len(images):
                raise ValueError(
                    f"generator/dfr_video_ltx: media placement references image[{index}] but only "
                    f"{len(images)} provided")
            resolved.append({
                "index": index,
                "frame": _resolve_pixel_frame(placement.get("frame", "first"), frames_in),
                "strength": float(placement.get("strength", 1.0)),
            })
        return resolved

    # -- output -------------------------------------------------------------

    def _trim(self, latent: Tensor, frames_in: int, rounds: int) -> Tensor:
        """Trim the canvas padding back off.

        Safe on a latent boundary because ``frames_in - 1`` is a multiple of 8,
        so ``(target - 1)//8 + 1`` always lands exactly. Overshooting the
        generated canvas is an error, never a pad.
        """
        target = round_output_frames(frames_in, rounds)
        keep = (target - 1) // _TEMPORAL_DOWNSCALE + 1
        have = int(latent.shape[2])
        if keep > have:
            raise RuntimeError(
                f"generator/dfr_video_ltx: need {keep} latent frames for a {target}-frame output "
                f"but the rounds produced {have}")
        if keep < have:
            logger.info("[%s] trimming %d canvas latent frame(s) back off (target %d frames)",
                        _LOG_TAG, have - keep, target)
        return latent[:, :, :keep]

    def _decode_and_mux(self, bundle, latent: Tensor, seed: int, device: str,
                        playback_fps: float, audio_track) -> str:
        shim = _DecodeShim(vae=bundle.vae, device=device)
        frames_np = _decode_video(shim, latent, seed)
        out_path = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False).name
        # The N -> 2(N-1)+1 frame map leaves the picture a few tens of
        # milliseconds shorter than the base pass's audio; the muxer's
        # shortest-stream behavior absorbs it, so no audio re-trim here.
        encode_frames_to_mp4(frames_np, out_path, fps=playback_fps, audio=audio_track)
        return out_path


def tile_noise_seed(seed: int, round_index: int, tile_index: int) -> int:
    """Ancestral-noise seed for one tile of one round.

    ``seed + ANCESTRAL_NOISE_SEED_OFFSET + 1000*round + tile`` -- with rounds in
    {1, 2} and a handful of tiles the offsets stay well inside the ancestral
    band and clear of the decode stream at +20000.
    """
    return int(seed) + ANCESTRAL_NOISE_SEED_OFFSET + 1000 * int(round_index) + int(tile_index)
