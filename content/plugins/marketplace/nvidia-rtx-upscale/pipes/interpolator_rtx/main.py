from __future__ import annotations

import contextlib
import importlib.metadata
import logging
import os
import re
import tempfile
from fractions import Fraction
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from src.plugin_api.media import (
    StreamingMp4Writer,
    mux_audio_from_source,
    output_count,
    output_fps,
    slot,
    source_per_output,
)
from src.plugin_api.pipes import (
    BasePipe,
    GalleryGenerationOutput,
    GenerationExecutionError,
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
from src.plugin_api.sampling import SamplingCancelled

logger = logging.getLogger(__name__)

FACTORS = (2, 4)
MODES = ("low", "medium", "high")
MODE_INDEX = {"low": 0, "medium": 1, "high": 2}
MAX_TARGET_FPS = 240.0
DEFAULT_SOURCE_FPS = 24.0
PROGRESS_EVERY = 8

INSTALL_HINT = (
    'pip install "nvidia-vfx>=0.2" --extra-index-url https://pypi.nvidia.com -- '
    "Video Frame Generation needs nvidia-vfx 0.2 or later, an NVIDIA Ada or Blackwell "
    "GPU (Hopper too on Linux), and an NVIDIA driver >= 570.190 on Linux (>= 570.65 on "
    "Windows). pypi.org only carries a 3.7 kB source stub: the ~440 MB wheel comes from "
    "pypi.nvidia.com, hence the extra index. In Docker, Frame Generation runs with the "
    "default compute,utility driver capabilities, while the plugin's Video Super Resolution "
    "also needs ngx (NVIDIA_DRIVER_CAPABILITIES=compute,utility,ngx). On WSL2 outside "
    "Docker, users report that the library only loads with LD_LIBRARY_PATH=/usr/lib/wsl/lib "
    "set before Python starts. The PyPI package name (nvidia-vfx) differs from its import "
    "name (nvvfx), so the plugin catalog's generic installer cannot run this for you -- "
    "install it yourself with the command above."
)

SDK_SIZE_ERROR_CODES = (-7, -16)
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
                f"NVIDIA RTX Video Frame Generation is not installed. {INSTALL_HINT}"
            ) from exc
        reason = f"{exc} {exc.__cause__ or ''}".strip()
        raise GenerationExecutionError(
            f"NVIDIA RTX Video Frame Generation is installed (nvidia-vfx {version}) but its library does not load: "
            f"{reason}. It needs an NVIDIA GPU and driver visible to this process; in Docker, start the "
            f"container with --gpus all. {INSTALL_HINT}"
        ) from exc
    return nvvfx


def _sdk_code(exc: Exception, codes: Tuple[int, ...]) -> bool:
    return any(re.search(rf"code {code}(?!\d)", str(exc)) for code in codes)


def sdk_error(exc: Exception, where: str) -> GenerationExecutionError:
    if _sdk_code(exc, SDK_SIZE_ERROR_CODES) or "invalid parameter" in str(exc).lower():
        return GenerationExecutionError(
            f"NVIDIA RTX Video Frame Generation refused the frame size {where}: the SDK rejects an "
            f"invalid size. Resize the video and try again. "
            f"SDK error: {exc}"
        )
    if _sdk_code(exc, SDK_DEVICE_ERROR_CODES):
        return GenerationExecutionError(
            f"NVIDIA RTX Video Frame Generation does not run on this GPU or driver {where}: it needs an "
            f"NVIDIA Ada or Blackwell GPU (Hopper too on Linux) and a driver >= 570.190 on Linux "
            f"(>= 570.65 on Windows). SDK error: {exc}"
        )
    return GenerationExecutionError(f"NVIDIA RTX Video Frame Generation failed {where}: {exc}")


def plan_frame(index: int, source_count: int, ratio: Fraction) -> Tuple[int, Optional[float]]:
    at = slot(index, ratio)
    if at.left >= source_count - 1:
        return source_count - 1, None
    if at.t == 0.0:
        return at.left, None
    return at.left, at.t


def plan(source_count: int, ratio: Fraction) -> List[Tuple[int, Optional[float]]]:
    return [plan_frame(k, source_count, ratio) for k in range(output_count(source_count, ratio))]


def to_even_rgb(rgb: np.ndarray) -> np.ndarray:
    pad_h, pad_w = rgb.shape[0] % 2, rgb.shape[1] % 2
    if pad_h or pad_w:
        rgb = np.pad(rgb, ((0, pad_h), (0, pad_w), (0, 0)), mode="edge")
    return np.ascontiguousarray(rgb, dtype=np.uint8)


def _size_label(width: int, height: int, source: Optional[Tuple[int, int]]) -> str:
    if source is None or source == (width, height):
        return f"{width}x{height}"
    return f"{width}x{height} (a {source[0]}x{source[1]} video padded to even sides)"


def default_stream(torch_module):
    if torch_module.cuda.is_available():
        return torch_module.cuda.stream(torch_module.cuda.default_stream())
    return contextlib.nullcontext()


def _mode(effect_cls, name: str):
    enum_cls = getattr(effect_cls, "Mode", None)
    member = getattr(enum_cls, name.upper(), None) if enum_cls is not None else None
    return member if member is not None else MODE_INDEX[name]


def _to_tensor(rgb: np.ndarray):
    import torch

    arr = rgb.astype(np.float32) / 255.0
    return torch.from_numpy(arr).permute(2, 0, 1).contiguous().cuda().float()


def _to_rgb(tensor) -> np.ndarray:
    import torch

    out = tensor.clamp(0.0, 1.0).mul(255.0).round().to(torch.uint8)
    return out.permute(1, 2, 0).contiguous().cpu().numpy()


class _VfgSession:
    def __init__(self, nvvfx_module, mode: str, scene_detection: bool, multiplier: Optional[int]):
        effect_cls = getattr(nvvfx_module, "VideoFrameGeneration", None)
        if effect_cls is None:
            raise GenerationExecutionError(
                f"This nvvfx has no Video Frame Generation (it came in nvidia-vfx 0.2). {INSTALL_HINT}"
            )
        try:
            self._effect = effect_cls(
                mode=_mode(effect_cls, mode),
                automatic_shot_change_detection_enabled=bool(scene_detection),
            )
        except Exception as exc:
            raise sdk_error(exc, "when the effect is created") from exc
        self._multiplier = multiplier
        self._loaded_size: Optional[Tuple[int, int]] = None
        self._tensors: Dict[int, Any] = {}

    def ensure_loaded(self, width: int, height: int, source: Optional[Tuple[int, int]] = None) -> None:
        if self._loaded_size == (width, height):
            return
        self._effect.input_width = width
        self._effect.input_height = height
        if self._multiplier:
            self._effect.frame_multiplier = self._multiplier
        try:
            self._effect.load()
        except Exception as exc:
            raise sdk_error(exc, f"for {_size_label(width, height, source)} frames") from exc
        self._loaded_size = (width, height)

    def _tensor(self, index: int, frames: Dict[int, np.ndarray]):
        if index not in self._tensors:
            self._tensors[index] = _to_tensor(frames[index])
        return self._tensors[index]

    def forget_before(self, index: int) -> None:
        for key in [key for key in self._tensors if key < index]:
            del self._tensors[key]

    def frame_between(self, frames: Dict[int, np.ndarray], index: int, step: float) -> np.ndarray:
        import torch

        with default_stream(torch):
            previous = self._tensor(index, frames)
            current = self._tensor(index + 1, frames)
            try:
                if self._multiplier:
                    result = self._effect.run(previous, current, frame_index=int(round(step * self._multiplier)))
                else:
                    result = self._effect.run_at_timestep(previous, current, float(step))
            except Exception as exc:
                raise sdk_error(exc, f"between frames {index} and {index + 1}") from exc
            return _to_rgb(torch.from_dlpack(result.image).clone())

    def close(self) -> None:
        self._tensors.clear()
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


class RtxFrameGenerationPipe(BasePipe):
    name = "interpolator/rtx_vfg"
    description = "NVIDIA RTX Video Frame Generation: raise a video's frame rate on Ada or Blackwell hardware"

    @classmethod
    def get_default_config(cls) -> Dict[str, Any]:
        return {
            "factor": 2,
            "target_fps": 0.0,
            "mode": "medium",
            "scene_detection": True,
            "keep_audio": True,
        }

    @classmethod
    def configuration(cls) -> List[PipeConfigSpec]:
        return [
            PipeConfigSpec(
                name="factor", param_type=int, default=2,
                description="Frame rate multiplier when no target frame rate is set",
                required=False, choices=list(FACTORS),
            ),
            PipeConfigSpec(
                name="target_fps", param_type=float, default=0.0,
                description="Output frame rate (0 = source frame rate times factor); the duration is kept",
                required=False, min_value=0.0, max_value=MAX_TARGET_FPS,
            ),
            PipeConfigSpec(
                name="mode", param_type=str, default="medium",
                description="Video Frame Generation model: low, medium or high complexity",
                required=False, choices=list(MODES),
            ),
            PipeConfigSpec(
                name="scene_detection", param_type=bool, default=True,
                description="Let the SDK detect shot changes and copy a frame there instead of blending across the cut",
                required=False,
            ),
            PipeConfigSpec(
                name="keep_audio", param_type=bool, default=True,
                description="Mux the source video's audio track into the output",
                required=False,
            ),
        ]

    @classmethod
    def inputs(cls) -> List[PipeInputSpec]:
        return [
            PipeInputSpec("video", IOType.VIDEO, True, "Video(s) to interpolate", is_array=True),
        ]

    @classmethod
    def outputs(cls) -> List[PipeOutputSpec]:
        return [
            PipeOutputSpec("video", IOType.VIDEO, "Frame-interpolated video(s)", is_array=True),
        ]

    @classmethod
    def get_requirements(cls) -> Dict[str, Any]:
        return {"pip": ["nvvfx"], "git": [], "models": []}

    @classmethod
    def manual_install_instructions(cls) -> Optional[str]:
        return INSTALL_HINT

    def _settings(self) -> Tuple[int, float, str, bool, bool]:
        factor = int(float(self.config.get("factor", 2) or 2))
        target_fps = float(self.config.get("target_fps") or 0.0)
        mode = str(self.config.get("mode") or "medium").lower()
        if target_fps < 0 or target_fps > MAX_TARGET_FPS:
            raise GenerationExecutionError(f"interpolator/rtx_vfg: target_fps must be between 0 and {MAX_TARGET_FPS:g}, got {target_fps:g}")
        if target_fps == 0 and factor not in FACTORS:
            raise GenerationExecutionError(f"interpolator/rtx_vfg: factor must be 2 or 4, got {factor}")
        if mode not in MODES:
            raise GenerationExecutionError(f"interpolator/rtx_vfg: mode must be one of {', '.join(MODES)}, got {mode}")
        return factor, target_fps, mode, bool(self.config.get("scene_detection", True)), bool(self.config.get("keep_audio", True))

    def process(
        self,
        pipe_input: PipeInput,
        generation_outputs: callable,
        is_cancelled: Optional[callable] = None,
    ) -> PipeOutput:
        videos = pipe_input.input.get("video") or []
        if not isinstance(videos, list):
            videos = [videos]
        if not videos:
            raise GenerationExecutionError("interpolator/rtx_vfg requires a 'video' input")

        factor, target_fps, mode, scene_detection, keep_audio = self._settings()
        cancelled = is_cancelled or (lambda: False)
        nvvfx_module = _import_nvvfx()
        out_videos: List[VideoGenerationOutput] = []
        try:
            for video_path in videos:
                if cancelled():
                    raise SamplingCancelled()
                session = _VfgSession(nvvfx_module, mode, scene_detection, None if target_fps > 0 else factor)
                try:
                    out_videos.append(self._process_video(
                        session, str(video_path), factor, target_fps, keep_audio, generation_outputs, cancelled,
                    ))
                finally:
                    session.close()
        except Exception:
            _discard(*(str(o.video_path) for o in out_videos))
            raise

        generation_outputs(GalleryGenerationOutput(images=[], videos=out_videos))
        return PipeOutput(output={"video": [o.video_path for o in out_videos]})

    def _process_video(
        self,
        session: _VfgSession,
        video_path: str,
        factor: int,
        target_fps: float,
        keep_audio: bool,
        generation_outputs: callable,
        is_cancelled: callable,
    ) -> VideoGenerationOutput:
        import cv2

        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            raise GenerationExecutionError(f"interpolator/rtx_vfg: could not open video: {video_path}")

        writer: Optional[StreamingMp4Writer] = None
        out_tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False).name
        owned = [out_tmp]
        frames: Dict[int, np.ndarray] = {}
        try:
            try:
                source_fps = cap.get(cv2.CAP_PROP_FPS)
                source_fps = float(source_fps) if source_fps and source_fps > 0 else DEFAULT_SOURCE_FPS
                ratio = source_per_output(source_fps, factor, target_fps)
                out_fps = output_fps(source_fps, factor, target_fps)
                expected = output_count(int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0), ratio)
                source_size: Optional[Tuple[int, int]] = None
                read = 0
                ended = False
                index = 0
                while True:
                    if is_cancelled():
                        raise SamplingCancelled()
                    whole = slot(index, ratio).left
                    while not ended and read <= whole + 1:
                        ok, frame_bgr = cap.read()
                        if not ok or frame_bgr is None:
                            ended = True
                            break
                        if source_size is None:
                            source_size = (frame_bgr.shape[1], frame_bgr.shape[0])
                        frames[read] = to_even_rgb(cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB))
                        read += 1
                    if read == 0:
                        break
                    if ended and index >= output_count(read, ratio):
                        break
                    source, step = plan_frame(index, read, ratio)
                    if writer is None:
                        height, width = frames[source].shape[:2]
                        writer = StreamingMp4Writer(out_tmp, width, height, out_fps)
                    if step is None:
                        made = frames[source]
                    else:
                        session.ensure_loaded(frames[source].shape[1], frames[source].shape[0], source_size)
                        made = session.frame_between(frames, source, step)
                    writer.write(made)
                    for key in [key for key in frames if key < source]:
                        del frames[key]
                    session.forget_before(source)
                    index += 1
                    if index % PROGRESS_EVERY == 0:
                        generation_outputs(ProgressGenerationOutput(
                            state=f"RTX frame generation: <<NUMBER:{index}>> / <<NUMBER:{expected}>> frames",
                            icon=Icon(name="film"),
                            progress=Progress(index, max(expected, index)),
                        ))
            finally:
                cap.release()

            if writer is None:
                raise GenerationExecutionError(f"interpolator/rtx_vfg: no frames decoded from {video_path}")
            if is_cancelled():
                raise SamplingCancelled()
            writer.close()

            final_path = out_tmp
            if keep_audio:
                muxed = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False).name
                owned.append(muxed)
                if mux_audio_from_source(out_tmp, video_path, muxed):
                    final_path = muxed
                    _discard(out_tmp)
                else:
                    _discard(muxed)
                    logger.warning("[INTERPOLATOR_RTX] audio mux skipped for %s -- keeping video-only output", video_path)
            if is_cancelled():
                raise SamplingCancelled()
        except Exception:
            _cleanup_on_failure(writer, owned)
            raise

        return VideoGenerationOutput(video_path=final_path, temporary=True, fps=float(out_fps))


def _discard(*paths: str) -> None:
    for path in paths:
        try:
            os.remove(path)
        except OSError:
            pass


def _cleanup_on_failure(writer: Optional[StreamingMp4Writer], owned: List[str]) -> None:
    if writer is not None:
        try:
            writer.abort()
        except Exception:
            logger.warning("[INTERPOLATOR_RTX] writer.abort() failed during cleanup", exc_info=True)
    _discard(*owned)
