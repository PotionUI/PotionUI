
from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import torch

from src.platform.runtime.native.sampling import ANCESTRAL_NOISE_SEED_OFFSET, ProgressHook, denoise, make_preview_hook
from src.pipelines.contracts import logger
from src.pipelines.contracts import IOType, PipeInput, PipeInputSpec, PipeOutputSpec, PipeConfigSpec
from src.pipelines.outputs import Icon
from src.platform.runtime.device import clear_gpu_memory
from src.pipelines.pipes._shared.generation.generator_base import BaseGeneratorPipe, GeneratorContext
from src.pipelines.pipes._shared.generation.dit_placement import guard_sampling_oom, place_dit_for_sequence
from src.pipelines.pipes._shared.generation.dit_restore import restore_dit_best_effort
from src.pipelines.pipes._shared.generation.freeinit import freeinit_blend, freeinit_config_specs, resolve_freeinit
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
from src.pipelines.pipes._shared.generation.progress import ProgressEmitter
from src.pipelines.pipes._shared.media.video_encode import encode_frames_to_mp4, probe_effective_fps
from src.pipelines.pipes._shared.generation.nag_inputs import attach_nag
from src.pipelines.pipes._shared.video.idle_encoder import release_idle_te
from src.pipelines.pipes._shared.video.ltx_common import (
    decode_ltx_video,
    ltx_reserve_terms,
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
from src.pipelines.pipes._shared.generation.config_specs import (
    device_config_spec,
    nag_config_specs,
    preview_config_spec,
    seed_config_spec,
)


# Fallback fps used only when `fps` is left unset (None -- see
# ``get_default_config``) AND there is no ``audio_source`` to sync to. When
# ``audio_source`` IS set, an unset `fps` instead resolves from the source
# (see ``build_context``) rather than this constant -- see module docstring
# on ``_LTXCtx.audio_source`` for why a stale default here would desync
# muxed audio.
_DEFAULT_FPS = 25.0


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
                           "Sampler: any key registered on the sampler registry (see GET /api/sampling/catalog). "
                           "'euler_ancestral' is LTX-2.5's stage-1 sampler (stochastic, eta=1.0 "
                           "by default via sampler_options) -- pair it with schedule='ltx_dynamic' for the "
                           "matching resolution-aware sigma shift.", required=False),
            PipeConfigSpec("resolution", str, "768x512", "Resolution (WxH)", required=False),
            PipeConfigSpec("frames", int, 49, "Number of video frames (must be 1 + 8*k; up to ~40s at 25fps)",
                           required=False, min_value=1, max_value=1001),
            PipeConfigSpec("fps", float, None,
                           f"Output frame rate (unset -> {_DEFAULT_FPS}, or the audio_source video's own "
                           "detected rate when one is connected, so muxed audio stays in sync)",
                           required=False, min_value=1.0, max_value=60.0),
            PipeConfigSpec("quantity", int, 1, "Number of videos", required=False, min_value=1, max_value=4),
            seed_config_spec(),
            device_config_spec(),
            preview_config_spec("Emit live latent previews (frame 0) to the workbench during sampling"),
            PipeConfigSpec("cfg_zero_star", bool, True, "CFG-Zero*: rescale the uncond branch onto cond before extrapolation (free quality correction)", required=False),
            PipeConfigSpec("zero_init_steps", int, 0, "CFG-Zero*: return a zero velocity prediction for the first N steps", required=False,
                           min_value=0, max_value=100),
            *nag_config_specs(),
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
            width = int(w_lat0) * LTX_SPATIAL_DOWNSCALE
            height = int(h_lat0) * LTX_SPATIAL_DOWNSCALE
            frames = (int(t_lat0) - 1) * LTX_TEMPORAL_DOWNSCALE + 1
        else:
            resolution = str(self.config.get("resolution", "768x512")).split("x")
            width, height = int(resolution[0]), int(resolution[1])
            width, height, frames = snap_ltx_geometry(width, height, frames)

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
        cond = to_device(cond_model.embeds, c.device, c.dtype)
        uncond = to_device(cond_model.n_embeds, c.device, c.dtype) if cond_model.n_embeds else None
        cond = attach_nag(cond, uncond, self.config)

        t_lat = (c.frames - 1) // LTX_TEMPORAL_DOWNSCALE + 1
        h_lat = c.height // LTX_SPATIAL_DOWNSCALE
        w_lat = c.width // LTX_SPATIAL_DOWNSCALE
        shape = (1, LTX_LATENT_CHANNELS, t_lat, h_lat, w_lat)

        gen = torch.Generator(device=c.device).manual_seed(int(seed))
        noise = torch.randn(shape, generator=gen, device=c.device, dtype=c.dtype)
        # euler_ancestral (LTX-2.5 stage-1): a DEDICATED generator, offset from
        # the request seed, so its per-step stochastic draws never overlap the
        # init-noise/FreeInit stream `gen` already drives -- unlike every other
        # sampler the registry marks `stochastic`, which deliberately reuses
        # `gen` itself (see ensure_sampler_generator's docstring vs.
        # euler_ancestral.py's own).
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
        # clips still pin the whole DiT.
        placement = place_dit_for_sequence(
            c.bundle.dit, c.device, video_tokens=t_lat * h_lat * w_lat,
            own_models=(c.bundle.dit, c.vae),
            **ltx_reserve_terms(c.bundle.dit, per_token_timestep=False),
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
        # pipe config. FBCache/NAG (already wired above/at model_forward) compose
        # with it safely -- see multimodal_guider.py's module docstring.
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

        # Only the FIRST forward of the first pass is guarded; see
        # `guard_sampling_oom`. The estimate never refuses, so the real
        # allocator is what decides whether this clip runs.
        sample_forward = guard_sampling_oom(
            model_forward, dit=c.bundle.dit, device=c.device, decision=placement,
        )
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
                sample_forward, latents, cond, uncond,
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

        frames = decode_ltx_video(c.vae, c.device, latent, seed)
        if c.trim_to_frame_count is not None and 0 < c.trim_to_frame_count < frames.shape[0]:
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

        if index == ctx.quantity - 1:
            restore_dit_best_effort(c.bundle.dit, c.device)

        return out_path

    def emit_results(self, generation_outputs: callable, results: List[Any], used_seeds: List[int]) -> None:
        if not bool(self.config.get("decode", True)):
            return  # stage 1: an internal latent hand-off, no gallery/seed emission here
        emit_video_results(generation_outputs, results, used_seeds, resolution=getattr(self, "_video_resolution", None))

    def build_output(self, results: List[Any]) -> Dict[str, Any]:
        if not bool(self.config.get("decode", True)):
            return {"latent": results, "video": []}
        return {**build_video_output(results), "latent": []}
