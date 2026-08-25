"""MiniMax-H3 latent upsampler (spatial-only, in latent space).

Pure latent-space upsample: normalize (the arch's own fixed per-channel
``MEAN``/``STD`` -- see ``vae/minimax_h3_latent_upsampler.py``'s module
docstring for why these are interoperability constants rather than a VAE
statistics object like LTX's) -> the loaded ``MiniMaxH3LatentUpsampler`` arch
forward, run in temporal chunks -> re-normalize. No sampling loop, no LoRA,
no DiT -- cheap relative to a generation pass, same idiom as
``latent_upscaler/ltx``.

Two entry shapes, same as ``latent_upscaler/ltx``:

* ``latent`` input -- an already-generated latent, chained directly from a
  generator pipe's raw output (in-flow two-stage: stage 1 generates with
  ``decode: false``, this pipe upsamples, stage 2 refines).
* ``video`` input -- an existing video FILE (the standalone "upscale" mode):
  read, resized onto the encode canvas MiniMax-H3 generation itself resolves
  to (``geometry.resolve_canvas_size`` -- short edge 768, area capped, both
  axes on the 32px grid), frame count padded UP to the video VAE's ``17*n+5``
  lattice, then VAE-encoded before upsampling.

Requires ``model_loader/minimax_h3``'s optional ``upscale_model`` slot to
have been configured -- raises a clear error pointing at that slot otherwise,
rather than silently no-op'ing.

Target geometry (``geometry.resolve_target_geometry``): ``target_mode``
picks a decimal-megapixel area target or a straight axis multiplier; either
way the result is rounded onto the model's own 32px canvas grid and floor-
divided by 16 for the latent target. This model has no downscale mode, so an
``effective_scale < 1.0`` request is a hard error, not a silent shrink.

Temporal chunking (``geometry.upsample_chunked``): the upsampler's forward
resizes the WHOLE clip it is handed in one pass (unlike LTX's fixed-ratio
resampler, this one targets an arbitrary caller-given size), so a long clip
run whole would need activation memory proportional to its length. Windowed
in chunks of 16 latent frames with 2 frames of trimmed overlap instead --
frame count is unaffected either way (this pipe never changes T, only H/W).

VRAM: mirrors ``latent_upscaler/ltx``'s ``_free_room_for_upscale`` --
offloads a resident DiT and evicts every other foreign GPU-resident
component before this pipe's own VAE-encode + upsampler-forward work
(``dit_placement.py``'s eviction idiom, reused rather than reinvented). The
TE host-RAM release goes through the same shared ``release_idle_te``
(``generator/txt2vid_ltx/main.py``) every native family's generator already
calls -- by the time this pipe runs, ``prompt_encoder``/the generator have
usually already evicted it, so this call is typically a no-op fallback, same
as LTX's own belt-and-suspenders call.

Encode OOM: the H3 video VAE's ``encode()`` (``vae/minimax_h3_video.py``)
already does its own internal 17-frame chunking, but exposes no separate
tiled/OOM-retry ladder the way LTX's VAE does (no ``ltx_tiled_encode``-style
fallback exists for this family yet) -- a whole-clip OOM here raises a plain,
clearly-worded error rather than silently degrading.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

import torch
import torch.nn.functional as F

from src.pipelines.contracts import BasePipe
from src.pipelines.contracts import (
    IOType,
    PipeConfigSpec,
    PipeInput,
    PipeInputSpec,
    PipeOutput,
    PipeOutputSpec,
)
from src.pipelines.outputs import Icon, ProgressGenerationOutput
from src.platform.observability.profiling import get_profiler
from src.platform.runtime.device import clear_gpu_memory
from src.platform.runtime.model_lifecycle.manager import empty_pinned_host_cache
from src.platform.runtime.native.memory.residency import get_residency_manager
from src.platform.runtime.native.vae.minimax_h3_latent_upsampler import denormalize_h3_latent, normalize_h3_latent
from src.pipelines.pipes.generator.txt2vid_ltx.main import release_idle_te
from src.pipelines.pipes.generator.video_ltx.main import _load_video_frames
from src.pipelines.pipes.generator.video_minimax_h3.geometry import resolve_canvas_size
from src.pipelines.pipes.latent_upscaler.minimax_h3.geometry import (
    pad_frames_to_h3_grid,
    resolve_target_geometry,
    upsample_chunked,
)

logger = logging.getLogger(__name__)

# ImageNet normalization the H3 video VAE's `encode()` expects its pixels
# under -- the exact constants `generator/video_minimax_h3/conditioning.py`'s
# `encode_keyframe_condition` applies before its own `encode()` call.
# Duplicated here (rather than imported) because that module lives in a
# directory under concurrent edit by another agent this session; six float
# literals are not worth coupling this pipe's import graph to that file.
_PIXEL_MEAN = (0.485, 0.456, 0.406)
_PIXEL_STD = (0.229, 0.224, 0.225)


def _encode_dtype(vae_module: Any) -> torch.dtype:
    """The float dtype to hand `encode` its pixels in -- a quantised repack
    may store some parameters as integer codes, which are not a valid
    activation dtype, so the choice must come from a parameter actually
    stored in floating point. Same idiom as
    `generator/video_minimax_h3/conditioning.py`'s own `_encode_dtype`."""
    for parameter in vae_module.parameters():
        if parameter.is_floating_point():
            return parameter.dtype
    return torch.float32


def _resize_cover_center_crop_01(frames: torch.Tensor, height: int, width: int) -> torch.Tensor:
    """``(n, H0, W0, 3)`` [0,1] -> ``(1, 3, n, H, W)`` [0,1] (cover + center
    crop). Same recipe as ``generator/video_ltx/conditioning.py``'s
    ``_resize_cover_center_crop``, minus its ``[-1, 1]`` remap -- the H3 VAE
    wants ImageNet-normalized pixels (:data:`_PIXEL_MEAN`/:data:`_PIXEL_STD`),
    not LTX's own ``[-1, 1]`` convention."""
    chw = frames.permute(0, 3, 1, 2)  # (n, 3, H0, W0)
    _, _, h0, w0 = chw.shape
    scale = max(width / w0, height / h0)
    chw = F.interpolate(chw, size=(round(h0 * scale), round(w0 * scale)), mode="bilinear", align_corners=False)
    _, _, h1, w1 = chw.shape
    top, left = (h1 - height) // 2, (w1 - width) // 2
    chw = chw[:, :, top:top + height, left:left + width]
    return chw.permute(1, 0, 2, 3).unsqueeze(0)  # (1, 3, n, H, W)


def _unload_idle_te(bundle: Any, models: Any) -> float:
    """Thin wrapper over the shared ``release_idle_te`` -- see the module
    docstring's VRAM section. Kept as a call-site-stable function, same as
    ``latent_upscaler/ltx``'s own ``_unload_idle_te``."""
    return release_idle_te(bundle, models, "H3_UPSCALE")


def _free_room_for_upscale(bundle: Any, device: str, models: Any = None) -> None:
    """Evict a resident DiT and every other foreign GPU-resident component
    before this pipe's own VAE-encode/upsampler-forward GPU work, plus fully
    unload the idle TE's host RAM -- see the module docstring. Mirrors
    ``latent_upscaler/ltx``'s own ``_free_room_for_upscale`` exactly, adapted
    to the H3 bundle's field names (``video_vae``/``upsampler`` instead of
    ``vae``/``upsampler``+``temporal_upsampler``).
    """
    te_unloaded_gb = _unload_idle_te(bundle, models)
    get_profiler().census_now("h3_upscale.te_evicted")
    empty_pinned_host_cache()
    pinned_emptied = True

    if not str(device).startswith("cuda"):
        get_profiler().mark(
            "h3_upscale.free_room", device=str(device), dit_was_resident=False,
            te_unloaded_gb=round(te_unloaded_gb, 2), pinned_emptied=pinned_emptied,
        )
        return
    alloc0 = torch.cuda.memory_allocated(device) / (1 << 30) if torch.cuda.is_available() else 0.0
    dit = getattr(bundle, "dit", None)
    dit_dev = str(getattr(dit, "device", "<no dit>"))
    dit_was_resident = dit is not None and dit_dev.startswith("cuda")
    if dit_was_resident:
        dit.offload()
    own_models = tuple(
        m for m in (
            getattr(bundle, "video_vae", None),
            getattr(bundle, "audio_vae", None),
            getattr(bundle, "upsampler", None),
        ) if m is not None
    )
    get_residency_manager().offload_all(device, exclude=own_models)
    clear_gpu_memory()
    alloc1 = torch.cuda.memory_allocated(device) / (1 << 30) if torch.cuda.is_available() else 0.0
    logger.debug(
        "[H3_UPSCALE] eviction pass: dit.device=%s, allocated %.2fGB -> %.2fGB, te_unloaded_gb=%.2f",
        dit_dev, alloc0, alloc1, te_unloaded_gb,
    )
    get_profiler().mark(
        "h3_upscale.free_room", device=str(device), dit_was_resident=dit_was_resident,
        alloc_before_gb=round(alloc0, 2), alloc_after_gb=round(alloc1, 2),
        te_unloaded_gb=round(te_unloaded_gb, 2), pinned_emptied=pinned_emptied,
    )


class LatentUpscalerMinimaxH3Pipe(BasePipe):
    name = "latent_upscaler"
    description = "Upsample a MiniMax-H3 video latent spatially, via the model's optional upscaler checkpoint"

    @classmethod
    def get_default_config(cls) -> Dict[str, Any]:
        return {
            "device": "cuda", "target_mode": "megapixels", "megapixels": 2.1, "scale": 2.0, "max_frames": 601,
        }

    @classmethod
    def configuration(cls) -> List[PipeConfigSpec]:
        return [
            PipeConfigSpec("device", str, "cuda", "Compute device", required=False, choices=["cuda", "cpu"]),
            PipeConfigSpec("target_mode", str, "megapixels", "How to size the upscale target: a decimal-megapixel "
                           "area, or a straight axis multiplier", required=False, choices=["megapixels", "scale"]),
            PipeConfigSpec("megapixels", float, 2.1, "Target area in megapixels (target_mode='megapixels')",
                           required=False, min_value=0.5, max_value=8.0),
            PipeConfigSpec("scale", float, 2.0, "Target axis multiplier (target_mode='scale')",
                           required=False, min_value=1.0, max_value=4.0),
            PipeConfigSpec("max_frames", int, 601,
                           "Max frames to read from a 'video' input (standalone upscale mode)",
                           required=False, min_value=1, max_value=601),
        ]

    @classmethod
    def inputs(cls) -> List[PipeInputSpec]:
        return [
            PipeInputSpec("model", IOType.MODEL, True,
                          "MiniMax-H3 model bundle (needs the video VAE plus the upscale_model slot)",
                          is_array=False),
            PipeInputSpec("latent", IOType.LATENT, False,
                          "Direct latent to upsample (in-flow two-stage)", is_array=False),
            PipeInputSpec("video", IOType.VIDEO, False,
                          "Existing video file to VAE-encode then upsample (standalone upscale mode)",
                          is_array=True),
            PipeInputSpec("MODELS", IOType.SERVICE, False,
                          "Model lifecycle service, to release the idle TE's host RAM",
                          is_array=False),
        ]

    @classmethod
    def outputs(cls) -> List[PipeOutputSpec]:
        return [
            PipeOutputSpec("latent", IOType.LATENT, "Upsampled latent", is_array=False),
            PipeOutputSpec("source_frame_count", IOType.INT,
                           "Original (pre-temporal-padding) frame count of the 'video' input, before "
                           "pad_frames_to_h3_grid repeated its last frame up to the video VAE's 17*n+5 lattice "
                           "-- lets a downstream refine trim that padding back out of its own decoded output "
                           "before mux. None on the 'latent'-input path (in-flow two-stage: no video was "
                           "decoded here, so no padding was ever added)", is_array=False),
        ]

    def process(self, pipe_input: PipeInput, generation_outputs: callable) -> PipeOutput:
        bundle = pipe_input.input["model"]
        upsampler = getattr(bundle, "upsampler", None)
        if upsampler is None:
            raise ValueError(
                "latent_upscaler/minimax_h3: the model bundle has no latent upsampler loaded -- set "
                "model_loader/minimax_h3's 'upscale_model' config to a MiniMax-H3 3D latent-upscaler checkpoint"
            )

        device = self.config.get("device", "cuda")
        models = pipe_input.input.get("MODELS")

        # Make room BEFORE any of this pipe's own GPU work -- see
        # `_free_room_for_upscale`. `models` additionally lets it release the
        # idle TE's host RAM.
        _free_room_for_upscale(bundle, device, models)

        latent = pipe_input.input.get("latent")
        source_frame_count: Optional[int] = None
        if latent is None:
            videos = pipe_input.input.get("video") or []
            if not videos:
                raise ValueError("latent_upscaler/minimax_h3 requires either a 'latent' or a 'video' input")
            generation_outputs(ProgressGenerationOutput(
                state="Encoding source video", icon=Icon(name="film", effect="pulse")))
            latent, source_frame_count, source_height, source_width = self._encode_video(bundle, videos[0], device)
        else:
            _, _, _, lat_h, lat_w = latent.shape
            source_height, source_width = lat_h * 16, lat_w * 16

        target_mode = str(self.config.get("target_mode", "megapixels"))
        geometry = resolve_target_geometry(
            source_height, source_width, mode=target_mode,
            megapixels=float(self.config.get("megapixels", 2.1)), scale=float(self.config.get("scale", 2.0)),
        )

        generation_outputs(ProgressGenerationOutput(
            state="Upsampling latent", icon=Icon(name="bolt", effect="pulse")))
        upsampled = self._upsample(upsampler, latent, geometry, device)
        return PipeOutput(output={"latent": upsampled, "source_frame_count": source_frame_count})

    # -- helpers -------------------------------------------------------------

    def _encode_video(self, bundle: Any, video_path: str, device: str) -> Tuple[torch.Tensor, int, int, int]:
        """Standalone-mode entry: read an existing video file, put it on the
        SAME encode canvas MiniMax-H3 generation itself resolves to for that
        aspect ratio (``resolve_canvas_size``), pad its frame count UP to the
        video VAE's ``17*n+5`` lattice, and VAE-encode it to a raw latent --
        ready for :meth:`_upsample`.

        Returns ``(latent, n0, height, width)`` -- ``n0`` is the frame count
        BEFORE temporal padding (surfaced via this pipe's own
        ``source_frame_count`` output); ``height``/``width`` are the encode
        canvas in pixels, the ``resolve_target_geometry`` source size.
        """
        max_frames = int(self.config.get("max_frames", 601))
        frames = _load_video_frames(video_path, max_frames)
        h0, w0 = int(frames.shape[1]), int(frames.shape[2])
        height, width = resolve_canvas_size(w0, h0)

        frames = frames.to(device=device)
        frames, n0 = pad_frames_to_h3_grid(frames)
        pixels01 = _resize_cover_center_crop_01(frames, height, width)
        del frames

        pixel_mean = torch.tensor(_PIXEL_MEAN, device=device, dtype=torch.float32).view(1, -1, 1, 1, 1)
        pixel_std = torch.tensor(_PIXEL_STD, device=device, dtype=torch.float32).view(1, -1, 1, 1, 1)
        pixels = (pixels01 - pixel_mean) / pixel_std
        del pixels01

        bundle.video_vae.move_to(device)
        try:
            pixels = pixels.to(dtype=_encode_dtype(bundle.video_vae.module))
            try:
                with torch.no_grad():
                    latent = bundle.video_vae.module.encode(pixels)
            except torch.cuda.OutOfMemoryError as exc:
                raise torch.cuda.OutOfMemoryError(
                    "latent_upscaler/minimax_h3: VAE encode ran out of VRAM -- the H3 video VAE has no tiled "
                    "encode fallback yet, so try a shorter source clip or free VRAM first"
                ) from exc
        finally:
            bundle.video_vae.offload()
            del pixels
        return latent, n0, height, width

    @staticmethod
    def _upsample(upsampler: Any, latent: torch.Tensor, geometry: Any, device: str) -> torch.Tensor:
        """Run ``upsampler`` over ``latent`` inside the arch's fixed
        normalization sandwich, chunked over the temporal axis (module
        docstring). Moved onto ``device`` for the call and offloaded again on
        the way out, including on an exception.
        """
        upsampler.move_to(device)
        try:
            with torch.no_grad():
                z = latent.to(device=device, dtype=upsampler.compute_dtype)
                z = normalize_h3_latent(z)
                z = upsample_chunked(
                    upsampler.module, z, geometry.effective_scale,
                    (geometry.latent_height, geometry.latent_width),
                )
                z = denormalize_h3_latent(z)
        finally:
            upsampler.offload()
        return z
