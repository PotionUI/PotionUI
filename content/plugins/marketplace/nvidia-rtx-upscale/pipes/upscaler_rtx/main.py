"""NVIDIA RTX Video Super Resolution (`nvidia-vfx` / `import nvvfx`) as a
native-engine pipe.

`nvvfx` is a Tensor Core effect, not a diffusion model: no checkpoint, no
prompt, no seed. One `VideoSuperRes` instance drives four intents --
`upscale`, `upscale_highbitrate` (tuned for a compressed/lossy source),
`denoise` and `deblur` (the last two are same-resolution enhancement, not
upscaling) -- at four quality tiers each, selected via `nvvfx`'s
`QualityLevel` enum. `import nvvfx` happens lazily inside `process()`, never
at module scope, so this pipe stays visible in the catalog (marked
NOT_INSTALLED) on a host without the package -- see `_import_nvvfx()`.
"""

from __future__ import annotations

import contextlib
import importlib.metadata
import logging
import math
import re
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import numpy as np
from PIL import Image

from src.plugin_api.media import StreamingMp4Writer, mux_audio_from_source
from src.plugin_api.pipes import (
    BasePipe,
    GalleryGenerationOutput,
    GenerationExecutionError,
    ImageGenerationOutput,
    IOType,
    Icon,
    PipeConfigSpec,
    PipeInput,
    PipeInputSpec,
    PipeOutput,
    PipeOutputSpec,
    Progress,
    ProgressGenerationOutput,
    VideoGenerationOutput,
)

logger = logging.getLogger(__name__)

#: (intent, quality) -> the `nvvfx.VideoSuperRes.QualityLevel` member name.
#: BICUBIC (index 0) is deliberately unreachable through this table -- it is
#: not one of the four quality tiers this pipe exposes.
_QUALITY_LEVEL_NAMES = {
    "upscale": {
        "low": "LOW", "medium": "MEDIUM", "high": "HIGH", "ultra": "ULTRA",
    },
    "upscale_highbitrate": {
        "low": "HIGHBITRATE_LOW", "medium": "HIGHBITRATE_MEDIUM",
        "high": "HIGHBITRATE_HIGH", "ultra": "HIGHBITRATE_ULTRA",
    },
    "denoise": {
        "low": "DENOISE_LOW", "medium": "DENOISE_MEDIUM",
        "high": "DENOISE_HIGH", "ultra": "DENOISE_ULTRA",
    },
    "deblur": {
        "low": "DEBLUR_LOW", "medium": "DEBLUR_MEDIUM",
        "high": "DEBLUR_HIGH", "ultra": "DEBLUR_ULTRA",
    },
}

#: Denoise/deblur are same-resolution enhancement -- `scale` is ignored for them.
_SAME_RESOLUTION_INTENTS = {"denoise", "deblur"}

MIN_SCALE = 1.0
MAX_SCALE = 4.0
DEFAULT_STRENGTH = 1.0

INSTALL_HINT = (
    'pip install "nvidia-vfx>=0.2" --extra-index-url https://pypi.nvidia.com -- '
    "requires an NVIDIA RTX GPU (Turing or newer) and an NVIDIA driver >= 570.190 "
    "on Linux (>= 570.65 on Windows). pypi.org only carries a 3.7 kB source stub: "
    "the ~440 MB wheel comes from pypi.nvidia.com, hence the extra index. In Docker, "
    "the container needs the ngx driver capability "
    "(NVIDIA_DRIVER_CAPABILITIES=compute,utility,ngx), without which the effect "
    "fails to load with code -12. On WSL2 outside Docker, users report that the "
    "library only loads with LD_LIBRARY_PATH=/usr/lib/wsl/lib set before Python "
    "starts. The PyPI package name (nvidia-vfx) differs from its import name "
    "(nvvfx), so the plugin catalog's generic installer cannot run this for you "
    "-- install it yourself with the command above."
)

SDK_SIZE_ERROR_CODES = (-7, -16)
SDK_INIT_ERROR_CODES = (-12,)
SDK_DEVICE_ERROR_CODES = (-17, -18, -19)


def _installed_version() -> Optional[str]:
    try:
        return importlib.metadata.version("nvidia-vfx")
    except importlib.metadata.PackageNotFoundError:
        return None


def _import_nvvfx():
    try:
        import nvvfx
    except ImportError as exc:
        version = _installed_version()
        if version is None:
            raise GenerationExecutionError(
                f"NVIDIA RTX Video Super Resolution is not installed. {INSTALL_HINT}"
            ) from exc
        reason = f"{exc} {exc.__cause__ or ''}".strip()
        raise GenerationExecutionError(
            f"NVIDIA RTX Video Super Resolution is installed (nvidia-vfx {version}) but its library does not load: "
            f"{reason}. It needs an NVIDIA GPU and driver visible to this process; in Docker, start the "
            f"container with --gpus all. {INSTALL_HINT}"
        ) from exc
    return nvvfx


def _quality_level(nvvfx_module, intent: str, quality: str):
    """Resolve a `QualityLevel` member, trying both spellings NVIDIA's own
    docs and its ComfyUI reference node use (`VideoSuperRes.QualityLevel` vs
    `effects.QualityLevel`)."""
    attr_name = _QUALITY_LEVEL_NAMES.get(intent, {}).get(quality)
    if attr_name is None:
        raise GenerationExecutionError(
            f"upscaler/rtx_vsr: no QualityLevel for intent={intent!r} quality={quality!r}"
        )

    enum_cls = getattr(getattr(nvvfx_module, "VideoSuperRes", None), "QualityLevel", None)
    if enum_cls is None:
        enum_cls = getattr(getattr(nvvfx_module, "effects", None), "QualityLevel", None)
    if enum_cls is None:
        raise GenerationExecutionError(
            "upscaler/rtx_vsr: nvvfx exposes no QualityLevel enum under "
            "VideoSuperRes or effects"
        )

    level = getattr(enum_cls, attr_name, None)
    if level is None:
        raise GenerationExecutionError(
            f"upscaler/rtx_vsr: nvvfx.QualityLevel has no member {attr_name}"
        )
    return level


def round_to_8(value: float) -> int:
    """nvvfx requires output dimensions on the 8px grid -- the reference
    ComfyUI node's own `max(8, round(x/8)*8)`."""
    return max(8, round(value / 8) * 8)


def target_size(intent: str, scale: float, width: int, height: int, target_short_side: int = 0) -> Tuple[int, int]:
    """The (width, height) to load `VideoSuperRes` for, given one frame's
    source size. Denoise/deblur force output size = input size; the two
    upscale intents multiply by `scale`. Either way the result lands on the
    8px grid `nvvfx` requires."""
    if intent in _SAME_RESOLUTION_INTENTS:
        return round_to_8(width), round_to_8(height)
    factor = effective_scale(scale, target_short_side, width, height)
    return (
        max(round_to_8(width * factor), ceil_to_8(width)),
        max(round_to_8(height * factor), ceil_to_8(height)),
    )


def ceil_to_8(value: float) -> int:
    return max(8, math.ceil(value / 8) * 8)


def effective_scale(scale: float, target_short_side: int, width: int, height: int) -> float:
    if target_short_side and target_short_side > 0 and min(width, height) > 0:
        scale = target_short_side / min(width, height)
    return min(MAX_SCALE, max(MIN_SCALE, float(scale)))


def _sdk_code(exc: Exception, codes: Tuple[int, ...]) -> bool:
    return any(re.search(rf"code {code}(?!\d)", str(exc)) for code in codes)


def sdk_error(exc: Exception, source: Optional[Tuple[int, int]], output: Optional[Tuple[int, int]]) -> GenerationExecutionError:
    sizes = f" ({source[0]}x{source[1]} to {output[0]}x{output[1]})" if source and output else ""
    if _sdk_code(exc, SDK_SIZE_ERROR_CODES) or "invalid parameter" in str(exc).lower():
        return GenerationExecutionError(
            f"NVIDIA RTX Video Super Resolution refused the frame size{sizes}: the SDK rejects an "
            f"invalid size, a target smaller than the source among them. Pick another target, or "
            f"Denoise/Deblur to keep the size. SDK error: {exc}"
        )
    if _sdk_code(exc, SDK_INIT_ERROR_CODES):
        return GenerationExecutionError(
            f"NVIDIA RTX Video Super Resolution could not initialize{sizes}: the NVIDIA driver's NGX "
            f"library (libnvidia-ngx.so.1) is not reachable. In Docker, recreate the container with "
            f"NVIDIA_DRIVER_CAPABILITIES=compute,utility,ngx. SDK error: {exc}"
        )
    if _sdk_code(exc, SDK_DEVICE_ERROR_CODES):
        return GenerationExecutionError(
            f"NVIDIA RTX Video Super Resolution does not run on this GPU or driver{sizes}: it needs an "
            f"NVIDIA RTX GPU (Turing or newer) and a driver >= 570.190 on Linux (>= 570.65 on Windows). "
            f"SDK error: {exc}"
        )
    return GenerationExecutionError(f"NVIDIA RTX Video Super Resolution failed{sizes}: {exc}")


def default_stream(torch_module):
    if torch_module.cuda.is_available():
        return torch_module.cuda.stream(torch_module.cuda.default_stream())
    return contextlib.nullcontext()


def _clone_dlpack_result(result) -> "torch.Tensor":
    """`result.image`'s buffer is reused by nvvfx's NEXT `run()` call (or
    freed by `close()`) -- NVIDIA's own docs for `VideoSuperRes.run()`: "You
    must copy/clone it immediately before the next call or close()." Isolated
    into its own function so the clone is a single, directly testable step
    rather than buried inside the rest of `process_frame`'s dtype/layout
    conversions (which happen to copy anyway, and would mask a missing clone
    here in an end-to-end test)."""
    import torch

    return torch.from_dlpack(result.image).clone()


def _make_effect(effect_cls, quality_level, strength: Optional[float]):
    if strength is None or abs(float(strength) - DEFAULT_STRENGTH) < 1e-9:
        return effect_cls(quality=quality_level)
    try:
        return effect_cls(quality=quality_level, strength=float(strength))
    except TypeError:
        logger.warning("[UPSCALER_RTX] this nvvfx's VideoSuperRes takes no strength: running at full strength")
        return effect_cls(quality=quality_level)


class _RtxSession:
    """One `nvvfx.VideoSuperRes` instance for an entire pipe run.

    Constructing the effect is the expensive part, so it is built once and
    reused across every frame/image in the batch. `output_width`/`output_height`
    are fixed by `load()`, though, so a batch whose frames land on different
    target sizes (different source resolutions, or denoise/deblur mixing
    input sizes) needs a reload -- not a new effect -- whenever the size
    changes; `ensure_loaded` no-ops when it hasn't.
    """

    def __init__(self, nvvfx_module, quality_level, strength: Optional[float] = None):
        try:
            self._effect = _make_effect(nvvfx_module.VideoSuperRes, quality_level, strength)
        except Exception as exc:
            raise sdk_error(exc, None, None) from exc
        self._loaded_size: Optional[Tuple[int, int]] = None

    def ensure_loaded(self, width: int, height: int, source: Optional[Tuple[int, int]] = None) -> None:
        if self._loaded_size == (width, height):
            return
        self._effect.output_width = width
        self._effect.output_height = height
        try:
            self._effect.load()
        except Exception as exc:
            raise sdk_error(exc, source, (width, height)) from exc
        self._loaded_size = (width, height)

    def process_frame(self, image: Image.Image) -> Image.Image:
        import torch

        rgb = image.convert("RGB")
        arr = np.asarray(rgb, dtype=np.float32) / 255.0
        with default_stream(torch):
            frame = torch.from_numpy(arr).permute(2, 0, 1).contiguous().cuda().float()
            try:
                result = self._effect.run(frame)
            except Exception as exc:
                raise sdk_error(exc, image.size, self._loaded_size) from exc
            out = _clone_dlpack_result(result)
            del frame

            out = out.clamp(0.0, 1.0).mul(255.0).round().to(torch.uint8)
            out_np = out.permute(1, 2, 0).contiguous().cpu().numpy()
            del out
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        return Image.fromarray(out_np, mode="RGB")

    def close(self) -> None:
        exit_ = getattr(self._effect, "__exit__", None)
        if callable(exit_):
            exit_(None, None, None)
        else:
            close_ = getattr(self._effect, "close", None)
            if callable(close_):
                close_()
        self._effect = None
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass


class RtxUpscalePipe(BasePipe):
    name = "upscaler/rtx_vsr"
    description = "NVIDIA RTX Video Super Resolution: upscale, denoise or deblur images/video on RTX hardware"

    @classmethod
    def get_default_config(cls) -> Dict[str, Any]:
        return {
            "intent": "upscale",
            "quality": "ultra",
            "scale": 2.0,
            "target_short_side": 0,
            "strength": DEFAULT_STRENGTH,
            "keep_audio": True,
        }

    @classmethod
    def configuration(cls) -> List[PipeConfigSpec]:
        return [
            PipeConfigSpec(
                name="intent", param_type=str, default="upscale",
                description="What the RTX effect does to each frame",
                required=False,
                choices=["upscale", "upscale_highbitrate", "denoise", "deblur"],
            ),
            PipeConfigSpec(
                name="quality", param_type=str, default="ultra",
                description="RTX effect quality tier",
                required=False, choices=["low", "medium", "high", "ultra"],
            ),
            PipeConfigSpec(
                name="scale", param_type=float, default=2.0,
                description="Output size multiplier per side; ignored for denoise/deblur (same-resolution enhancement) and when target_short_side is set",
                required=False, min_value=MIN_SCALE, max_value=MAX_SCALE,
            ),
            PipeConfigSpec(
                name="target_short_side", param_type=int, default=0,
                description="Short side of the output in pixels (0 = use scale); the scale it implies is held to 1x-4x",
                required=False, min_value=0, max_value=8192,
            ),
            PipeConfigSpec(
                name="strength", param_type=float, default=DEFAULT_STRENGTH,
                description="Effect strength from 0 to 1; 1.0 is the SDK default and is not sent",
                required=False, min_value=0.0, max_value=1.0,
            ),
            PipeConfigSpec(
                name="keep_audio", param_type=bool, default=True,
                description="Mux the source video's audio track into the output (video input only)",
                required=False,
            ),
        ]

    @classmethod
    def inputs(cls) -> List[PipeInputSpec]:
        return [
            PipeInputSpec("image", IOType.IMAGE, False, "Images to process", is_array=True),
            PipeInputSpec("video", IOType.VIDEO, False, "Video(s) to process", is_array=True),
        ]

    @classmethod
    def outputs(cls) -> List[PipeOutputSpec]:
        return [
            PipeOutputSpec("image", IOType.IMAGE, "Processed images", is_array=True),
            PipeOutputSpec("video", IOType.VIDEO, "Processed video(s)", is_array=True),
        ]

    @classmethod
    def get_requirements(cls) -> Dict[str, Any]:
        # `requirements_satisfied` (src/pipelines/installer.py) reads this list
        # as import names, not pip package names -- "nvvfx" is correct here
        # even though the wheel is published as "nvidia-vfx".
        return {"pip": ["nvvfx"], "git": [], "models": []}

    @classmethod
    def manual_install_instructions(cls) -> Optional[str]:
        # Non-None makes PipeInstaller refuse rather than run
        # `pip install nvvfx` against the import-name entry above, which would
        # install the wrong thing (or nothing).
        return INSTALL_HINT

    def process(
        self,
        pipe_input: PipeInput,
        generation_outputs: callable,
        is_cancelled: Optional[callable] = None,
    ) -> PipeOutput:
        images = pipe_input.input.get("image") or []
        videos = pipe_input.input.get("video") or []
        if not isinstance(images, list):
            images = [images]
        if not isinstance(videos, list):
            videos = [videos]
        if not images and not videos:
            raise GenerationExecutionError("upscaler/rtx_vsr requires an 'image' or 'video' input")

        nvvfx_module = _import_nvvfx()
        intent = str(self.config.get("intent", "upscale"))
        quality = str(self.config.get("quality", "ultra"))
        scale = float(self.config.get("scale", 2.0))
        target_short_side = int(float(self.config.get("target_short_side") or 0))
        strength = self.config.get("strength")
        strength = DEFAULT_STRENGTH if strength in (None, "") else float(strength)
        keep_audio = bool(self.config.get("keep_audio", True))
        level = _quality_level(nvvfx_module, intent, quality)

        session = _RtxSession(nvvfx_module, level, strength)
        out_images: List[ImageGenerationOutput] = []
        out_videos: List[VideoGenerationOutput] = []
        try:
            for idx, image in enumerate(images):
                if is_cancelled and is_cancelled():
                    break
                generation_outputs(ProgressGenerationOutput(
                    state=f"RTX {intent} <<NUMBER:{idx + 1}/{len(images)}:bolt>>",
                    icon=Icon(name="bolt"),
                    progress=Progress(idx, len(images)),
                ))
                width, height = image.size
                out_w, out_h = target_size(intent, scale, width, height, target_short_side)
                session.ensure_loaded(out_w, out_h, (width, height))
                processed = session.process_frame(image)
                out_images.append(ImageGenerationOutput(image=processed))

            for video_path in videos:
                if is_cancelled and is_cancelled():
                    break
                out_videos.append(self._process_video(
                    session, str(video_path), intent, scale, keep_audio,
                    generation_outputs, is_cancelled, target_short_side,
                ))
        finally:
            session.close()

        if out_images:
            generation_outputs(GalleryGenerationOutput(images=out_images))
        if out_videos:
            generation_outputs(GalleryGenerationOutput(images=[], videos=out_videos))

        return PipeOutput(output={
            "image": [o.image for o in out_images],
            "video": [o.video_path for o in out_videos],
        })

    def _process_video(
        self,
        session: _RtxSession,
        video_path: str,
        intent: str,
        scale: float,
        keep_audio: bool,
        generation_outputs: callable,
        is_cancelled: Optional[callable],
        target_short_side: int = 0,
    ) -> VideoGenerationOutput:
        import cv2

        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            raise GenerationExecutionError(f"upscaler/rtx_vsr: could not open video: {video_path}")

        writer: Optional[StreamingMp4Writer] = None
        written = 0
        out_tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False).name
        try:
            fps = cap.get(cv2.CAP_PROP_FPS) or 24.0
            total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)

            while True:
                if is_cancelled and is_cancelled():
                    break
                ret, frame_bgr = cap.read()
                if not ret or frame_bgr is None:
                    break

                rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
                pil_frame = Image.fromarray(rgb)
                out_w, out_h = target_size(intent, scale, pil_frame.width, pil_frame.height, target_short_side)
                session.ensure_loaded(out_w, out_h, pil_frame.size)
                processed = session.process_frame(pil_frame)

                if writer is None:
                    writer = StreamingMp4Writer(out_tmp, processed.width, processed.height, fps)
                writer.write(np.asarray(processed))
                written += 1

                if written % 8 == 0:
                    generation_outputs(ProgressGenerationOutput(
                        state=f"RTX {intent}: <<NUMBER:{written}>> / <<NUMBER:{total}>> frames",
                        icon=Icon(name="film"),
                        progress=Progress(written, total or written),
                    ))
        finally:
            cap.release()

        if writer is None:
            raise GenerationExecutionError(f"upscaler/rtx_vsr: no frames decoded from {video_path}")
        writer.close()

        final_path = out_tmp
        if keep_audio:
            muxed = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False).name
            if mux_audio_from_source(out_tmp, video_path, muxed):
                final_path = muxed
            else:
                logger.warning(
                    "[UPSCALER_RTX] audio mux skipped for %s -- keeping video-only output", video_path
                )

        return VideoGenerationOutput(video_path=final_path, temporary=True, fps=float(fps))
