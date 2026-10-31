"""Image-to-video generator for the native Wan family (concat-i2v).

Same flow as ``generator/txt2vid_wan22`` but with a start-image conditioning: the
image is resized to the target, VAE-encoded, and folded (with a temporal mask)
into the 20-channel ``c_concat`` (see ``concat.build_i2v_concat``). The i2v
``model_forward`` prepends that 20ch concat to the 16ch noisy latent each step so
the DiT sees its 36 input channels; the sampled latent stays 16ch. Targets the
``wan22_i2v_14b`` spec (in_dim 36, no CLIP-vision; concat conditioning only).

Registered name is ``generator/img2vid_wan22`` (2-level discovery, per the t2v
naming convention).
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from typing import Any, Dict, List

import torch

from src.platform.runtime.native.sampling import ProgressHook, denoise, make_preview_hook
from src.platform.runtime.native.vae.causal_3d import LATENTS_MEAN, LATENTS_STD
from src.pipelines.contracts import logger
from src.pipelines.contracts import IOType, PipeInput, PipeInputSpec, PipeOutputSpec, PipeConfigSpec
from src.pipelines.outputs import Icon
from src.pipelines.pipes._shared.generation.generator_base import BaseGeneratorPipe, GeneratorContext
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
from src.pipelines.pipes._shared.generation.wan_i2v_contract import require_concat_i2v_contract_for_experts
from src.pipelines.pipes._shared.media.video_encode import encode_frames_to_mp4
from src.pipelines.pipes._shared.vae.wan_tiled_encode import make_wan_vae_encode
from src.pipelines.pipes.generator.img2vid_wan22.concat import build_i2v_concat
from src.pipelines.pipes._shared.generation.nag_inputs import attach_nag
from src.pipelines.pipes._shared.video.frames import prep_start_frame
from src.pipelines.pipes._shared.video.video_common import build_video_output, emit_video_results, to_device
from src.pipelines.pipes._shared.video.wan_experts import (
    decode_wan_video,
    ExpertRouter,
    resolve_expert_boundary,
    snap_wan_geometry,
    WAN_TEMPORAL_DOWNSCALE,
)
from src.pipelines.pipes._shared.generation.config_specs import (
    device_config_spec,
    nag_config_specs,
    preview_config_spec,
    preview_decoder_config_spec,
    seed_config_spec,
)


class _I2VForward:
    """Wraps the expert router to prepend the constant i2v concat to the latent."""

    def __init__(self, router: ExpertRouter, concat: torch.Tensor) -> None:
        self.router = router
        self.concat = concat

    def __call__(self, x: torch.Tensor, sigma: torch.Tensor, conditioning: dict) -> torch.Tensor:
        return self.router(torch.cat([x, self.concat], dim=1), sigma, conditioning)

    def cache_identity(self, sigma_val: float) -> tuple:
        """Forward the router's per-expert step-cache identity through the wrapper
        (``denoise()`` sees this object, not the router)."""
        return self.router.cache_identity(sigma_val)


@dataclass
class _Ctx:
    forward: _I2VForward
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
        router = self.forward.router
        for dit in (router.high, router.low):
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


class GeneratorWanImg2VidPipe(BaseGeneratorPipe):
    name = "generator"
    description = "Native Wan image-to-video generator (concat conditioning, dual-expert)"

    @classmethod
    def configuration(cls) -> List[PipeConfigSpec]:
        return [
            PipeConfigSpec("mode", str, "img2vid", "Generation mode", required=True, choices=["img2vid"]),
            PipeConfigSpec("steps", int, 30, "Denoising steps", required=False, min_value=1, max_value=100),
            PipeConfigSpec("cfg", float, 5.0, "True CFG scale", required=False, min_value=1.0, max_value=20.0),
            PipeConfigSpec("sampler", str, "unipc", "Sampler: any key registered on the sampler registry "
                           "(see GET /api/sampling/catalog)", required=False),
            PipeConfigSpec("resolution", str, "832x480", "Resolution (WxH)", required=False),
            PipeConfigSpec("frames", int, 81, "Number of video frames", required=False, min_value=1, max_value=257),
            PipeConfigSpec("fps", float, 16.0, "Output frame rate", required=False, min_value=1.0, max_value=60.0),
            PipeConfigSpec("quantity", int, 1, "Number of videos", required=False, min_value=1, max_value=4),
            seed_config_spec(),
            device_config_spec(),
            PipeConfigSpec("expert_boundary", float, None, "Dual-expert switch boundary override", required=False,
                           min_value=0.0, max_value=1.0),
            PipeConfigSpec("expert_switch_step", int, None, "Switch high->low expert at this step (wins over expert_boundary; converted to that step's sigma)", required=False,
                           min_value=0),
            preview_config_spec("Emit live latent previews (frame 0) to the workbench during sampling"),
            preview_decoder_config_spec(),
            PipeConfigSpec("cfg_zero_star", bool, True, "CFG-Zero*: rescale the uncond branch onto cond before extrapolation (free quality correction)", required=False),
            PipeConfigSpec("zero_init_steps", int, 0, "CFG-Zero*: return a zero velocity prediction for the first N steps", required=False,
                           min_value=0, max_value=100),
            *nag_config_specs(),
            *riflex_config_specs(),
            *apg_settings_config_specs(),
            *slg_settings_config_specs(),
            *sampler_step_cache_config_specs(),
            *schedule_settings_config_specs(),
        ]

    @classmethod
    def inputs(cls) -> List[PipeInputSpec]:
        return [
            PipeInputSpec("model", IOType.MODEL, True, "Wan model bundle", is_array=False),
            PipeInputSpec("conditioning", IOType.CONDITIONING, True, "Encoded prompt conditioning", is_array=True),
            PipeInputSpec("image", IOType.IMAGE, True, "Start frame for image-to-video", is_array=True),
            PipeInputSpec("end_image", IOType.IMAGE, False, "Optional end frame for first+last-frame (FLF) conditioning", is_array=True),
            PipeInputSpec("seed", IOType.SEED, False, "Random seeds", is_array=True),
        ]

    @classmethod
    def outputs(cls) -> List[PipeOutputSpec]:
        return [PipeOutputSpec("video", IOType.VIDEO, "Generated videos", is_array=True)]

    # -- context -----------------------------------------------------------

    def build_context(self, pipe_input: PipeInput) -> GeneratorContext:
        bundle = pipe_input.input["model"]
        conditioning = pipe_input.input["conditioning"] or []
        seeds = pipe_input.input.get("seed", [])
        images = pipe_input.input.get("image") or []
        end_images = pipe_input.input.get("end_image") or []
        if not images:
            raise ValueError("generator/img2vid_wan22 requires a start image input")

        mode_label = "flf" if end_images else "i2v"

        dit_in_dim = bundle.high_dit.module.in_dim
        if dit_in_dim == 16:
            raise ValueError(
                f"generator/img2vid_wan22: loaded model '{bundle.spec.variant}' is a t2v Wan "
                f"checkpoint (in_dim=16) with no image conditioning support, but the pipeline "
                f"mode is img2vid. Pick an i2v Wan model, or use the Director's t2v sub-type."
            )
        if dit_in_dim != 36:
            raise ValueError(
                f"generator/img2vid_wan22: loaded model '{bundle.spec.variant}' has in_dim="
                f"{dit_in_dim}, not the 36-channel concat-i2v contract this generator implements "
                f"(e.g. the Wan 2.2 TI2V-5B checkpoint has its own built-in text+image "
                f"conditioning that this concat path does not replicate). {mode_label} mode needs "
                f"a Wan 2.2 A14B i2v checkpoint (in_dim=36); the 5B checkpoint only works in "
                f"text-to-video."
            )

        # Classic Wan i2v checkpoints (with or without the FLF `emb_pos` table)
        # condition through CLIP-vision `img_emb` cross-attention, not through
        # this generator's concat-only construction -- using one here would
        # silently drop the image conditioning the checkpoint actually expects.
        # Also has in_dim=36 (same as the concat-i2v contract above), so only
        # `img_emb`'s presence tells the two apart. Check EVERY expert that can
        # execute (high AND low, when a low-noise expert is loaded) -- a
        # compatible high-noise expert paired with an incompatible classic
        # low-noise one must still reject, not just a mismatched high expert.
        require_concat_i2v_contract_for_experts(
            (bundle.high_dit, bundle.low_dit), generator="generator/img2vid_wan22",
            variant=bundle.spec.variant, mode_label=mode_label,
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
        # Assembled before the router so resolve_expert_boundary can reuse the
        # exact schedule inputs when converting an 'expert_switch_step' to a sigma.
        sampling_settings = {
            **spec.sampling_settings,
            **apg_settings_overrides(self.config),
            **slg_settings_overrides(self.config),
            **schedule_settings_overrides(self.config),
        }
        boundary = resolve_expert_boundary(spec, self.config, sampling_settings, steps,
                                           default=0.900, log_tag="GENERATOR WAN-I2V")
        riflex = build_riflex(self.config)
        router = ExpertRouter(bundle.high_dit, bundle.low_dit, boundary, device, riflex=riflex)
        lf = spec.latent_format
        dtype = bundle.high_dit.compute_dtype

        # Snap to the model's granularity BEFORE the start frame is resized/encoded
        # into the concat (spatial to spatial_downscale*patch, frames to 1+4k).
        width, height, frames = snap_wan_geometry(bundle, lf, width, height, frames)

        # Build the i2v concat ONCE (constant across seeds): VAE-encode the start frame.
        # The causal-3D VAE encode is the memory-heavy step (~2x decode); at 480p+ the
        # untiled encode OOMs, so fall back to a spatially-tiled encode on OOM (mirrors
        # the engine's decode OOM safety net -- temporal chunking is unchanged, tiling
        # is spatial only).
        start = prep_start_frame(images[0], height, width, device, dtype)
        end = prep_start_frame(end_images[0], height, width, device, dtype) if end_images else None
        bundle.vae.move_to(device)
        # try/finally: build_context runs OUTSIDE process()'s own try (it's
        # called before that try opens -- see BaseGeneratorPipe.process), so
        # without this a raise here (e.g. an OOM the tiled fallback ladder
        # itself can't recover from) leaves the VAE resident with no cleanup
        # path at all, not even BaseGeneratorPipe's generic release_gpu().
        try:
            with torch.no_grad():
                concat = build_i2v_concat(
                    start, make_wan_vae_encode(bundle.vae.module, width, height, log_prefix="GENERATOR WAN-I2V"),
                    length=frames, height=height, width=width,
                    latents_mean=LATENTS_MEAN, latents_std=LATENTS_STD, end_frames=end,
                    device=device, dtype=dtype,
                ).to(dtype)
        finally:
            bundle.vae.offload()

        logger.info("[GENERATOR WAN-I2V] %s: %d frame(s) @ %dx%d, %d steps, cfg %.1f, concat %s, flf=%s, dual_expert=%s",
                    spec.variant, frames, width, height, steps, cfg, tuple(concat.shape), end is not None, bundle.is_dual_expert)

        # Stashed for emit_results() below: process() doesn't thread ctx through
        # to emit_results, and the (post-snap) width/height are only known here.
        self._video_resolution = (width, height)

        return GeneratorContext(
            quantity=quantity, input_seeds=seeds,
            extra=_Ctx(
                forward=_I2VForward(router, concat), vae=bundle.vae,
                sampling_settings=sampling_settings, conditioning=conditioning,
                steps=steps, cfg=cfg, sampler=sampler, width=width, height=height,
                frames=frames, fps=fps, latent_channels=lf["latent_channels"],
                spatial_downscale=lf.get("spatial_downscale", 8), device=device, dtype=dtype, spec=spec,
            ),
        )

    # -- per-seed generation ----------------------------------------------

    def generate_one(self, ctx: GeneratorContext, index: int, seed: int, progress: ProgressEmitter) -> str:
        c: _Ctx = ctx.extra
        cond_model = c.conditioning[index] if index < len(c.conditioning) else c.conditioning[-1]
        cond = to_device(cond_model.embeds, c.device, c.dtype)
        uncond = to_device(cond_model.n_embeds, c.device, c.dtype) if cond_model.n_embeds else None
        cond = attach_nag(cond, uncond, self.config)

        t_lat = (c.frames - 1) // WAN_TEMPORAL_DOWNSCALE + 1
        shape = (1, c.latent_channels, t_lat, c.height // c.spatial_downscale, c.width // c.spatial_downscale)
        # Activation-aware partial residency for each expert — the i2v
        # concat rides the same forward, so the DiT still full-pinned OOMs at 720p/5s.
        c.forward.router.latents_shape = shape
        gen = torch.Generator(device=c.device).manual_seed(int(seed))
        noise = torch.randn(shape, generator=gen, device=c.device, dtype=c.dtype)
        latents = torch.zeros(shape, device=c.device, dtype=c.dtype)

        def on_progress(_frac, i, total):
            progress.step(i + 1, total, state="IMG2VID", icon=Icon(name="film", effect="pulse"))

        logger.debug("[GENERATOR WAN-I2V] video %d/%d, seed %d", index + 1, ctx.quantity, seed)
        hooks = [ProgressHook(on_progress)]
        if self.config.get("preview", True):
            preview_hook = make_preview_hook(c.spec, progress.preview, emit_clip=progress.preview_clip, decoder_path=self.config.get("preview_decoder") or None)
            if preview_hook is not None:
                hooks.append(preview_hook)
        # try/finally: a partial-residency (streamed) expert pins host
        # RAM; an OOM/error mid-sampling must still tear the streamer
        # down (unpin) or the pinned pool leaks into the RAM-cached expert. See
        # the txt2vid pipe's matching guard for the full rationale.
        try:
            latent = denoise(
                c.forward, latents, cond, uncond,
                steps=c.steps, sampler_name=c.sampler,
                sampling_settings=c.sampling_settings, guidance_scale=c.cfg,
                seed_noise=noise, hooks=hooks, is_cancelled=ctx.is_cancelled,
                cfg_zero_star=bool(self.config.get("cfg_zero_star", True)),
                zero_init_steps=int(self.config.get("zero_init_steps", 0)),
                expert_boundary=c.forward.router.boundary if c.forward.router.low is not None else None,
                # Seed determinism (stochastic samplers): reuse the same `gen`
                # that drew the init noise above -- see
                # ensure_sampler_generator's docstring.
                **sampler_step_cache_kwargs(self.config, sampler=c.sampler, generator=gen),
            )
        finally:
            if c.forward.router.active is not None:
                c.forward.router.active.offload()

        frames = decode_wan_video(c, latent)
        out_path = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False).name
        encode_frames_to_mp4(frames, out_path, fps=c.fps)
        return out_path

    def emit_results(self, generation_outputs: callable, results: List[str], used_seeds: List[int]) -> None:
        emit_video_results(generation_outputs, results, used_seeds, resolution=getattr(self, "_video_resolution", None))

    def build_output(self, results: List[str]) -> Dict[str, Any]:
        return build_video_output(results)
