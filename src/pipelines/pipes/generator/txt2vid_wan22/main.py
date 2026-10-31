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

import torch

from src.platform.runtime.native.sampling import ProgressHook, denoise, make_preview_hook
from src.pipelines.contracts import logger
from src.pipelines.contracts import IOType, PipeInput, PipeInputSpec, PipeOutputSpec, PipeConfigSpec
from src.pipelines.outputs import Icon
from src.pipelines.pipes._shared.generation.generator_base import BaseGeneratorPipe, GeneratorContext
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
from src.pipelines.pipes._shared.media.video_encode import encode_frames_to_mp4
from src.pipelines.pipes._shared.generation.config_specs import (
    device_config_spec,
    nag_config_specs,
    preview_config_spec,
    preview_decoder_config_spec,
    seed_config_spec,
)
from src.pipelines.pipes._shared.generation.nag_inputs import attach_nag
from src.pipelines.pipes._shared.video.video_common import build_video_output, emit_video_results, to_device
from src.pipelines.pipes._shared.video.wan_experts import (
    decode_wan_video,
    ExpertRouter,
    resolve_expert_boundary,
    snap_wan_geometry,
    WAN_TEMPORAL_DOWNSCALE,
)


@dataclass
class _WanCtx:
    router: ExpertRouter
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
    def configuration(cls) -> List[PipeConfigSpec]:
        return [
            PipeConfigSpec("mode", str, "txt2vid", "Generation mode", required=True, choices=["txt2vid"]),
            PipeConfigSpec("steps", int, 30, "Denoising steps", required=False, min_value=1, max_value=100),
            PipeConfigSpec("cfg", float, 5.0, "True CFG scale", required=False, min_value=1.0, max_value=20.0),
            PipeConfigSpec("sampler", str, "unipc", "Sampler: any key registered on the sampler registry "
                           "(see GET /api/sampling/catalog)", required=False),
            PipeConfigSpec("expert_boundary", float, None, "Dual-expert switch boundary override (sigma fraction)", required=False,
                           min_value=0.0, max_value=1.0),
            PipeConfigSpec("expert_switch_step", int, None, "Switch high->low expert at this step (wins over expert_boundary; converted to that step's sigma)", required=False,
                           min_value=0),
            PipeConfigSpec("cfg_zero_star", bool, True, "CFG-Zero*: rescale the uncond branch onto cond before extrapolation (free quality correction)", required=False),
            PipeConfigSpec("zero_init_steps", int, 0, "CFG-Zero*: return a zero velocity prediction for the first N steps", required=False,
                           min_value=0, max_value=100),
            *nag_config_specs(),
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
            seed_config_spec(),
            device_config_spec(),
            preview_config_spec("Emit live latent previews (frame 0) to the workbench during sampling"),
            preview_decoder_config_spec(),
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
        router = ExpertRouter(bundle.high_dit, bundle.low_dit, boundary, device, riflex=riflex)
        lf = spec.latent_format

        # Snap to the model's granularity: spatial to spatial_downscale*patch (16px
        # for Wan21, 32px for Wan22-5B), frames to the causal VAE's 1+4k lattice.
        width, height, frames = snap_wan_geometry(bundle, lf, width, height, frames)

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
        cond = to_device(cond_model.embeds, c.device, c.dtype)
        uncond = to_device(cond_model.n_embeds, c.device, c.dtype) if cond_model.n_embeds else None
        cond = attach_nag(cond, uncond, self.config)

        t_lat = (c.frames - 1) // WAN_TEMPORAL_DOWNSCALE + 1
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
                    preview_hook = make_preview_hook(c.spec, progress.preview, decoder_path=self.config.get("preview_decoder") or None)
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

        frames = decode_wan_video(c, latent)
        out_path = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False).name
        encode_frames_to_mp4(frames, out_path, fps=c.fps)
        return out_path

    def emit_results(self, generation_outputs: callable, results: List[str], used_seeds: List[int]) -> None:
        emit_video_results(generation_outputs, results, used_seeds, resolution=getattr(self, "_video_resolution", None))

    def build_output(self, results: List[str]) -> Dict[str, Any]:
        return build_video_output(results)
