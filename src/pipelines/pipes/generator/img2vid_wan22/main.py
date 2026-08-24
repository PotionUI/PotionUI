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

import numpy as np
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
from src.pipelines.pipes._shared.media.video_encode import encode_frames_to_mp4
from src.pipelines.pipes._shared.vae.wan_tiled_encode import make_wan_vae_encode
from src.pipelines.pipes.generator.img2vid_wan22.concat import build_i2v_concat
from src.pipelines.pipes.generator.txt2vid_wan22.main import (
    _attach_nag,
    _build_video_output,
    _decode_video,
    _emit_video_results,
    _ExpertRouter,
    _TEMPORAL_DOWNSCALE,
    _snap_geometry,
    _to_device,
    resolve_expert_boundary,
)


class _I2VForward:
    """Wraps the expert router to prepend the constant i2v concat to the latent."""

    def __init__(self, router: _ExpertRouter, concat: torch.Tensor) -> None:
        self.router = router
        self.concat = concat

    def __call__(self, x: torch.Tensor, sigma: torch.Tensor, conditioning: dict) -> torch.Tensor:
        return self.router(torch.cat([x, self.concat], dim=1), sigma, conditioning)


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


def _prep_start_frame(image: Any, height: int, width: int, device: str, dtype: torch.dtype) -> torch.Tensor:
    """Normalize a PIL image / HWC tensor to ``(1, H, W, 3)`` float [0,1], resized
    to cover the target and centre-cropped (ComfyUI ``common_upscale`` 'center')."""
    if hasattr(image, "convert"):  # PIL
        arr = torch.from_numpy(np.asarray(image.convert("RGB"))).to(dtype) / 255.0
    else:
        arr = torch.as_tensor(image, dtype=dtype)
        if arr.max() > 1.5:
            arr = arr / 255.0
    if arr.ndim == 4:
        arr = arr[0]
    chw = arr.permute(2, 0, 1).unsqueeze(0).to(device)  # (1,3,H0,W0)
    _, _, h0, w0 = chw.shape
    scale = max(width / w0, height / h0)
    chw = torch.nn.functional.interpolate(chw, size=(round(h0 * scale), round(w0 * scale)),
                                          mode="bilinear", align_corners=False)
    _, _, h1, w1 = chw.shape
    top, left = (h1 - height) // 2, (w1 - width) // 2
    chw = chw[:, :, top:top + height, left:left + width]
    return chw.squeeze(0).permute(1, 2, 0).unsqueeze(0)  # (1, H, W, 3)


class GeneratorWanImg2VidPipe(BaseGeneratorPipe):
    name = "generator"
    description = "Native Wan image-to-video generator (concat conditioning, dual-expert)"

    @classmethod
    def get_default_config(cls) -> Dict[str, Any]:
        return {
            "mode": "img2vid", "steps": 30, "cfg": 5.0, "sampler": "unipc",
            "resolution": "832x480", "frames": 81, "fps": 16.0,
            "quantity": 1, "seed": -1, "device": "cuda", "expert_boundary": None, "expert_switch_step": None,
            "preview": True,
            "cfg_zero_star": True,
            "zero_init_steps": 0,
            "nag_scale": 1.0,
            "nag_tau": 3.5,
            "nag_alpha": 0.5,
            "riflex": False,
            "riflex_trained_frames": None,
            # None-sentinel: guidance_options.py's *_overrides() only emit a key
            # when non-None, so an unset knob here lets the model's own
            # ModelSpec.sampling_settings survive the merge (see that module's
            # docstring; P5 fix).
            "apg_eta": None,
            "apg_norm_threshold": None,
            "apg_momentum": None,
            "slg_scale": None,
            "slg_layers": None,
            "slg_sigma_start": None,
            "slg_sigma_end": None,
            "sampler_options": {},
            "step_cache": {},
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
            PipeConfigSpec("mode", str, "img2vid", "Generation mode", required=True, choices=["img2vid"]),
            PipeConfigSpec("steps", int, 30, "Denoising steps", required=False, min_value=1, max_value=100),
            PipeConfigSpec("cfg", float, 5.0, "True CFG scale", required=False, min_value=1.0, max_value=20.0),
            PipeConfigSpec("sampler", str, "unipc", "Sampler", required=False,
                           choices=["euler", "dpmpp_2m", "dpmpp_2m_sde", "dpmpp_3m", "res_multistep", "unipc", "lcm"]),
            PipeConfigSpec("resolution", str, "832x480", "Resolution (WxH)", required=False),
            PipeConfigSpec("frames", int, 81, "Number of video frames", required=False, min_value=1, max_value=257),
            PipeConfigSpec("fps", float, 16.0, "Output frame rate", required=False, min_value=1.0, max_value=60.0),
            PipeConfigSpec("quantity", int, 1, "Number of videos", required=False, min_value=1, max_value=4),
            PipeConfigSpec("seed", int, -1, "Random seed", required=False, min_value=-1),
            PipeConfigSpec("device", str, "cuda", "Compute device", required=False, choices=["cuda", "cpu"]),
            PipeConfigSpec("expert_boundary", float, None, "Dual-expert switch boundary override", required=False,
                           min_value=0.0, max_value=1.0),
            PipeConfigSpec("expert_switch_step", int, None, "Switch high->low expert at this step (wins over expert_boundary; converted to that step's sigma)", required=False,
                           min_value=0),
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

        dit_in_dim = bundle.high_dit.module.in_dim
        if dit_in_dim == 16:
            raise ValueError(
                f"generator/img2vid_wan22: loaded model '{bundle.spec.variant}' is a t2v Wan "
                f"checkpoint (in_dim=16) with no image conditioning support, but the pipeline "
                f"mode is img2vid. Pick an i2v Wan model, or use the Director's t2v sub-type."
            )

        # Wan 2.1 FLF2V-style checkpoints condition first/last frames through CLIP-vision
        # (img_emb with position embeddings for the two image tokens), not through this
        # generator's concat-only construction -- using one here would silently drop the
        # image conditioning the checkpoint actually expects.
        img_emb = getattr(bundle.high_dit.module, "img_emb", None)
        if img_emb is not None and getattr(img_emb, "emb_pos", None) is not None:
            raise ValueError(
                f"generator/img2vid_wan22: loaded model '{bundle.spec.variant}' is a Wan 2.1 "
                f"FLF2V-style checkpoint (CLIP-vision image conditioning with position "
                f"embeddings), which this generator does not support. Use a Wan 2.2 i2v "
                f"checkpoint instead."
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
        router = _ExpertRouter(bundle.high_dit, bundle.low_dit, boundary, device, riflex=riflex)
        lf = spec.latent_format
        dtype = bundle.high_dit.compute_dtype

        # Snap to the model's granularity BEFORE the start frame is resized/encoded
        # into the concat (spatial to spatial_downscale*patch, frames to 1+4k).
        width, height, frames = _snap_geometry(bundle, lf, width, height, frames)

        # Build the i2v concat ONCE (constant across seeds): VAE-encode the start frame.
        # The causal-3D VAE encode is the memory-heavy step (~2x decode); at 480p+ the
        # untiled encode OOMs, so fall back to a spatially-tiled encode on OOM (mirrors
        # the engine's decode OOM safety net -- temporal chunking is unchanged, tiling
        # is spatial only).
        start = _prep_start_frame(images[0], height, width, device, dtype)
        end = _prep_start_frame(end_images[0], height, width, device, dtype) if end_images else None
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
        cond = _to_device(cond_model.embeds, c.device, c.dtype)
        uncond = _to_device(cond_model.n_embeds, c.device, c.dtype) if cond_model.n_embeds else None
        cond = _attach_nag(cond, uncond, self.config)

        t_lat = (c.frames - 1) // _TEMPORAL_DOWNSCALE + 1
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
            preview_hook = make_preview_hook(c.spec, progress.preview)
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

        frames = _decode_video(c, latent)
        out_path = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False).name
        encode_frames_to_mp4(frames, out_path, fps=c.fps)
        return out_path

    def emit_results(self, generation_outputs: callable, results: List[str], used_seeds: List[int]) -> None:
        _emit_video_results(generation_outputs, results, used_seeds, resolution=getattr(self, "_video_resolution", None))

    def build_output(self, results: List[str]) -> Dict[str, Any]:
        return _build_video_output(results)
