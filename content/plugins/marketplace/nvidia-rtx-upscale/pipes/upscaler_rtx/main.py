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

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import numpy as np
from PIL import Image

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

# -- ffmpeg subprocess helpers, video mode only --------------------------
#
# Inlined rather than imported: the core encode-with-audio-passthrough helper
# (`src/pipelines/pipes/_shared/media/video_encode.py`, used by the native
# SeedVR2/RIFE pipes) lives under `src.pipelines`, not `src.plugin_api` -- a
# plugin may only import from the latter. A sibling module in this same
# directory isn't an option either: the catalog loads a pipe's `main.py` via
# `importlib.util.spec_from_file_location` with no package context, so a
# relative `from .video_io import ...` would fail with "no known parent
# package" the moment this pipe is used.


class FFmpegNotFoundError(RuntimeError):
    """Raised when the `ffmpeg`/`ffprobe` binaries are not on PATH."""


class _StreamingMp4Writer:
    """Pipes RGB uint8 frames to ffmpeg's stdin one at a time, into a
    video-only MP4. All frames must share the writer's (width, height), and
    both must be even (yuv420p requires it) -- callers get this for free here
    since RTX output dimensions are always rounded to a multiple of 8."""

    def __init__(self, out_path: Union[str, Path], width: int, height: int,
                 fps: float, codec: str = "libx264", crf: int = 18):
        if shutil.which("ffmpeg") is None:
            raise FFmpegNotFoundError("ffmpeg binary not found on PATH")
        self.width = int(width)
        self.height = int(height)
        if self.width % 2 or self.height % 2:
            raise ValueError(
                f"_StreamingMp4Writer needs even dimensions for yuv420p, got {self.width}x{self.height}"
            )
        cmd = [
            "ffmpeg", "-y",
            "-f", "rawvideo", "-pix_fmt", "rgb24",
            "-s", f"{self.width}x{self.height}", "-r", str(fps),
            "-i", "-", "-an",
            "-c:v", codec, "-pix_fmt", "yuv420p", "-crf", str(crf),
            "-movflags", "+faststart",
            str(out_path),
        ]
        self._proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )

    def write(self, frame_rgb: "np.ndarray") -> None:
        arr = np.ascontiguousarray(frame_rgb, dtype=np.uint8)
        try:
            self._proc.stdin.write(arr.tobytes())
        except (BrokenPipeError, OSError):
            self._raise_with_stderr("ffmpeg died mid-encode")

    def close(self) -> None:
        # communicate() flushes and closes stdin itself (ffmpeg's EOF); closing
        # stdin beforehand makes that flush raise "flush of closed file".
        try:
            _, stderr = self._proc.communicate(timeout=600)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            raise RuntimeError("ffmpeg encode timed out after 600s")
        if self._proc.returncode != 0:
            msg = stderr.decode("utf-8", errors="replace")[-2000:] if stderr else ""
            raise RuntimeError(f"ffmpeg encode failed (exit {self._proc.returncode}): {msg}")

    def _raise_with_stderr(self, prefix: str) -> None:
        try:
            _, stderr = self._proc.communicate(timeout=10)
        except Exception:
            self._proc.kill()
            stderr = b""
        msg = stderr.decode("utf-8", errors="replace")[-2000:] if stderr else ""
        raise RuntimeError(f"{prefix} (exit {self._proc.returncode}): {msg}")


def _has_audio_stream(path: Union[str, Path]) -> bool:
    """Probe `path` for an audio stream via ffprobe, so a silent source video
    degrades to the `-an` path instead of being handed to ffmpeg as a second
    input with nothing for `-map 1:a:0` to resolve."""
    if shutil.which("ffprobe") is None:
        return False
    cmd = [
        "ffprobe", "-v", "error", "-select_streams", "a",
        "-show_entries", "stream=index", "-of", "csv=p=0", str(path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=30)
    except (subprocess.TimeoutExpired, OSError):
        return False
    return result.returncode == 0 and bool(result.stdout.strip())


def _mux_audio_from_source(video_only: Union[str, Path], source: Union[str, Path],
                            out_path: Union[str, Path]) -> bool:
    """Copy `video_only`'s video stream and `source`'s audio into `out_path`.
    Returns True on success; on any failure (no audio stream, ffmpeg error,
    timeout) leaves `out_path` untouched and returns False so the caller keeps
    the video-only file rather than failing the whole generation over audio."""
    if not _has_audio_stream(source):
        return False
    for audio_args in (["-c:a", "copy"], ["-c:a", "aac", "-b:a", "192k"]):
        cmd = [
            "ffmpeg", "-y",
            "-i", str(video_only), "-i", str(source),
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "copy", *audio_args,
            str(out_path),
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, timeout=600)
        except (subprocess.TimeoutExpired, OSError):
            return False
        if result.returncode == 0:
            return True
    return False


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

INSTALL_HINT = (
    "pip install nvidia-vfx -- requires an NVIDIA RTX GPU (Turing or newer), a "
    "Linux NVIDIA driver >= 570.190, and downloads a ~600MB wheel from "
    "pypi.nvidia.com. The PyPI package name (nvidia-vfx) differs from its "
    "import name (nvvfx), so the plugin catalog's generic installer cannot run "
    "this for you -- install it yourself with the command above."
)


def _import_nvvfx():
    try:
        import nvvfx
    except ImportError as exc:
        raise GenerationExecutionError(
            f"NVIDIA RTX Video Super Resolution is not installed. {INSTALL_HINT}"
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


def target_size(intent: str, scale: float, width: int, height: int) -> Tuple[int, int]:
    """The (width, height) to load `VideoSuperRes` for, given one frame's
    source size. Denoise/deblur force output size = input size; the two
    upscale intents multiply by `scale`. Either way the result lands on the
    8px grid `nvvfx` requires."""
    if intent in _SAME_RESOLUTION_INTENTS:
        return round_to_8(width), round_to_8(height)
    return round_to_8(width * scale), round_to_8(height * scale)


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


class _RtxSession:
    """One `nvvfx.VideoSuperRes` instance for an entire pipe run.

    Constructing the effect is the expensive part, so it is built once and
    reused across every frame/image in the batch. `output_width`/`output_height`
    are fixed by `load()`, though, so a batch whose frames land on different
    target sizes (different source resolutions, or denoise/deblur mixing
    input sizes) needs a reload -- not a new effect -- whenever the size
    changes; `ensure_loaded` no-ops when it hasn't.
    """

    def __init__(self, nvvfx_module, quality_level):
        self._effect = nvvfx_module.VideoSuperRes(quality=quality_level)
        self._loaded_size: Optional[Tuple[int, int]] = None

    def ensure_loaded(self, width: int, height: int) -> None:
        if self._loaded_size == (width, height):
            return
        self._effect.output_width = width
        self._effect.output_height = height
        self._effect.load()
        self._loaded_size = (width, height)

    def process_frame(self, image: Image.Image) -> Image.Image:
        import torch

        rgb = image.convert("RGB")
        arr = np.asarray(rgb, dtype=np.float32) / 255.0
        frame = torch.from_numpy(arr).permute(2, 0, 1).contiguous().cuda().float()
        result = self._effect.run(frame)
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
                description="Output area multiplier; ignored for denoise/deblur (same-resolution enhancement)",
                required=False, min_value=1.0, max_value=4.0,
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
        keep_audio = bool(self.config.get("keep_audio", True))
        level = _quality_level(nvvfx_module, intent, quality)

        session = _RtxSession(nvvfx_module, level)
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
                out_w, out_h = target_size(intent, scale, width, height)
                session.ensure_loaded(out_w, out_h)
                processed = session.process_frame(image)
                out_images.append(ImageGenerationOutput(image=processed))

            for video_path in videos:
                if is_cancelled and is_cancelled():
                    break
                out_videos.append(self._process_video(
                    session, str(video_path), intent, scale, keep_audio,
                    generation_outputs, is_cancelled,
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
    ) -> VideoGenerationOutput:
        import cv2

        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            raise GenerationExecutionError(f"upscaler/rtx_vsr: could not open video: {video_path}")

        writer: Optional[_StreamingMp4Writer] = None
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
                out_w, out_h = target_size(intent, scale, pil_frame.width, pil_frame.height)
                session.ensure_loaded(out_w, out_h)
                processed = session.process_frame(pil_frame)

                if writer is None:
                    writer = _StreamingMp4Writer(out_tmp, processed.width, processed.height, fps)
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
            if _mux_audio_from_source(out_tmp, video_path, muxed):
                final_path = muxed
            else:
                logger.warning(
                    "[UPSCALER_RTX] audio mux skipped for %s -- keeping video-only output", video_path
                )

        return VideoGenerationOutput(video_path=final_path, temporary=True, fps=float(fps))
