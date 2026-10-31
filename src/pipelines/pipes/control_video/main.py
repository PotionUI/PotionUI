import tempfile
from pathlib import Path
from typing import Any, Callable, Iterator, List, Optional, Tuple

import cv2
import numpy as np
from PIL import Image

from src.pipelines.contracts import (
    BasePipe,
    IOType,
    PipeConfigSpec,
    PipeInput,
    PipeInputSpec,
    PipeOutput,
    PipeOutputSpec,
    logger,
)
from src.pipelines.outputs import (
    GalleryGenerationOutput,
    GenerationExecutionError,
    Icon,
    ImageGenerationOutput,
    Progress,
    ProgressGenerationOutput,
    VideoGenerationOutput,
)
from src.pipelines.pipes._shared.media.inputs import as_flag, media_paths
from src.pipelines.pipes._shared.media.video_encode import probe_effective_fps
from src.pipelines.pipes._shared.pose.estimator import acquire_pose_estimator
from src.platform.runtime.native.errors import SamplingCancelled
from src.platform.runtime.pose.dwpose import DWPoseEstimator
from src.platform.runtime.pose.geometry import FIT_MODES, fit_transform, map_points, resolve_output_size
from src.platform.runtime.pose.render import RenderOptions, render_pose
from src.platform.runtime.pose.sequence import (
    NoPersonError,
    clean_sequence,
    estimate_sequence,
)
from src.platform.util.video_stream import StreamingMp4Writer

GUIDE_TYPES = ["pose"]
GUIDE_CRF = 12
DEFAULT_FPS = 24.0
PROGRESS_STEPS = 20


def _number(value: Any, cast: Callable, default: Any) -> Any:
    if value is None or value == "":
        return default
    return cast(float(value)) if cast is int else cast(value)


class ControlVideoPipe(BasePipe):
    name = "control_video"
    description = "Render a driving video into a pose guide video"
    display_title = "Reading the motion"

    @classmethod
    def configuration(cls) -> List[PipeConfigSpec]:
        return [
            PipeConfigSpec("type", str, "pose", "Kind of guide to render", required=False, choices=GUIDE_TYPES),
            PipeConfigSpec("width", int, 0, "Exact guide width; 0 derives it", required=False, min_value=0),
            PipeConfigSpec("height", int, 0, "Exact guide height; 0 derives it", required=False, min_value=0),
            PipeConfigSpec("max_pixels", int, 0, "Pixel budget for the derived size; 0 = no cap", required=False, min_value=0),
            PipeConfigSpec("size_multiple", int, 32, "Guide width and height are multiples of this", required=False, min_value=2),
            PipeConfigSpec("fit", str, "contain", "How the source frame fits a different-shaped guide", required=False, choices=list(FIT_MODES)),
            PipeConfigSpec("body", bool, True, "Draw the body skeleton", required=False),
            PipeConfigSpec("hands", bool, True, "Draw the hands", required=False),
            PipeConfigSpec("face", bool, True, "Draw the face points", required=False),
            PipeConfigSpec("detect_resolution", int, 512, "Short side the pose is detected at; line widths scale from it", required=False, min_value=128),
            PipeConfigSpec("line_scale", float, 1.0, "Multiplier for line widths and dot sizes", required=False, min_value=0.25, max_value=4.0),
            PipeConfigSpec("min_confidence", float, 0.3, "Points below this score are dropped", required=False, min_value=0.0, max_value=1.0),
            PipeConfigSpec("smoothing", float, 0.5, "Temporal smoothing strength; 0 = off", required=False, min_value=0.0, max_value=1.0),
            PipeConfigSpec("keyframe_interval", int, 8, "Run the person detector every this many frames", required=False, min_value=1),
            PipeConfigSpec("max_gap", int, 12, "Longest run of missing frames that is interpolated", required=False, min_value=0),
            PipeConfigSpec("max_frames", int, 0, "Only process the first N frames; 0 = all", required=False, min_value=0),
            PipeConfigSpec("save_guide", bool, False, "Also save the guide video to the gallery", required=False),
            PipeConfigSpec("weights_dir", str, "", "Folder holding the two DWPose ONNX files; blank fetches them", required=False),
        ]

    @classmethod
    def inputs(cls) -> List[PipeInputSpec]:
        return [
            PipeInputSpec("video", IOType.VIDEO, True, "Driving videos", is_array=True),
            PipeInputSpec("MODELS", IOType.SERVICE, False, "Model lifecycle service for caching the pose models", is_array=False),
            PipeInputSpec("ASSETS", IOType.SERVICE, False, "Asset fetcher for the pose model files", is_array=False),
        ]

    @classmethod
    def outputs(cls) -> List[PipeOutputSpec]:
        return [PipeOutputSpec("video", IOType.VIDEO, "Guide videos, one per input video", is_array=True)]

    def _load_estimator(self, pipe_input: PipeInput) -> DWPoseEstimator:
        return acquire_pose_estimator(
            pipe_input.input.get("ASSETS"), pipe_input.input.get("MODELS"), str(self.config.get("weights_dir") or ""),
        )

    @staticmethod
    def _frames(path: str, limit: int) -> Tuple[Iterator[np.ndarray], Callable[[], None], Tuple[int, int, float, int]]:
        capture = cv2.VideoCapture(str(path))
        if not capture.isOpened():
            raise GenerationExecutionError(f"Could not open the video {Path(path).name}.")
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        fps = probe_effective_fps(path) or float(capture.get(cv2.CAP_PROP_FPS) or 0.0) or DEFAULT_FPS
        total = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        if limit:
            total = min(total, limit) if total else limit

        def generate() -> Iterator[np.ndarray]:
            count = 0
            while not limit or count < limit:
                ok, frame = capture.read()
                if not ok or frame is None:
                    return
                count += 1
                yield cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

        return generate(), capture.release, (width, height, fps, total)

    def _options(self) -> RenderOptions:
        return RenderOptions(
            body=as_flag(self.config.get("body", True)),
            hands=as_flag(self.config.get("hands", True)),
            face=as_flag(self.config.get("face", True)),
            min_confidence=_number(self.config.get("min_confidence"), float, 0.3),
            detect_resolution=_number(self.config.get("detect_resolution"), int, 512),
            line_scale=_number(self.config.get("line_scale"), float, 1.0),
        )

    def _progress(self, generation_outputs: Callable, label: str, current: int, total: int) -> None:
        generation_outputs(ProgressGenerationOutput(
            state=label,
            icon=Icon("film"),
            progress=Progress(current, max(total, 1)),
            bar=True,
        ))

    def process(
        self, pipe_input: PipeInput, generation_outputs: Callable, is_cancelled: Optional[Callable] = None,
    ) -> PipeOutput:
        guide_type = str(self.config.get("type") or "pose")
        if guide_type not in GUIDE_TYPES:
            raise GenerationExecutionError(f"Unknown guide type '{guide_type}'; supported: {', '.join(GUIDE_TYPES)}.")
        videos = media_paths(pipe_input.input.get("video"))
        if not videos:
            raise GenerationExecutionError("control_video needs at least one video.")
        cancelled = is_cancelled or (lambda: False)
        estimator = self._load_estimator(pipe_input)
        guides: List[str] = []
        try:
            for video in videos:
                guides.append(self._render_one(video, estimator, generation_outputs, cancelled))
        except NoPersonError as error:
            self._discard(guides)
            message = str(error)
            raise GenerationExecutionError(message[:1].upper() + message[1:] + ".") from error
        except BaseException:
            self._discard(guides)
            raise
        return PipeOutput(output={"video": guides})

    def _render_one(
        self,
        video: str,
        estimator: DWPoseEstimator,
        generation_outputs: Callable,
        cancelled: Callable,
    ) -> str:
        options = self._options()
        limit = _number(self.config.get("max_frames"), int, 0)
        frames, release, (video_width, video_height, fps, total) = self._frames(video, limit)
        try:
            step = max(1, total // PROGRESS_STEPS)

            def checked() -> Iterator[np.ndarray]:
                for index, frame in enumerate(frames):
                    if cancelled():
                        raise SamplingCancelled()
                    if index % step == 0:
                        self._progress(generation_outputs, "Reading the motion", index, total)
                    yield frame

            raw_poses = estimate_sequence(
                checked(),
                estimator,
                keyframe_interval=_number(self.config.get("keyframe_interval"), int, 8),
                min_confidence=options.min_confidence,
                detect_resolution=options.detect_resolution,
            )
        finally:
            release()
        count = raw_poses.shape[0]
        driver = clean_sequence(
            raw_poses, fps,
            min_confidence=options.min_confidence,
            max_gap=_number(self.config.get("max_gap"), int, 12),
            smoothing=_number(self.config.get("smoothing"), float, 0.5),
        )
        source_width, source_height = video_width, video_height
        out_width, out_height = resolve_output_size(
            source_width, source_height,
            width=_number(self.config.get("width"), int, 0),
            height=_number(self.config.get("height"), int, 0),
            max_pixels=_number(self.config.get("max_pixels"), int, 0),
            multiple=_number(self.config.get("size_multiple"), int, 32),
        )
        scale, offset_x, offset_y = fit_transform(
            source_width, source_height, out_width, out_height, str(self.config.get("fit") or "contain"),
        )
        placed = map_points(driver, scale, scale, offset_x, offset_y)
        return self._encode(placed, out_width, out_height, fps, options, generation_outputs, cancelled, count)

    def _encode(
        self,
        poses: np.ndarray,
        width: int,
        height: int,
        fps: float,
        options: RenderOptions,
        generation_outputs: Callable,
        cancelled: Callable,
        count: int,
    ) -> str:
        handle = tempfile.NamedTemporaryFile(prefix="potionui-guide-", suffix=".mp4", delete=False)
        handle.close()
        path = handle.name
        step = max(1, count // PROGRESS_STEPS)
        preview: Optional[np.ndarray] = None
        writer = StreamingMp4Writer(path, width, height, fps, crf=GUIDE_CRF)
        try:
            for index in range(count):
                if cancelled():
                    raise SamplingCancelled()
                frame = render_pose(poses[index], width, height, options)
                if index == count // 2:
                    preview = frame
                writer.write(frame)
                if index % step == 0:
                    self._progress(generation_outputs, "Drawing the pose guide", index, count)
            writer.close()
        except BaseException:
            try:
                writer.close()
            except Exception:
                writer.abort()
            Path(path).unlink(missing_ok=True)
            raise
        if preview is not None:
            generation_outputs(ImageGenerationOutput(
                image=Image.fromarray(preview), temporary=True, isArtifact=True, label="Pose guide",
            ))
        if as_flag(self.config.get("save_guide", False)):
            generation_outputs(GalleryGenerationOutput(images=[], videos=[
                VideoGenerationOutput(video_path=Path(path), temporary=False, resolution=(width, height), fps=fps),
            ]))
        logger.info(f"[CONTROL_VIDEO] wrote {count} frames at {width}x{height}, {fps:.3f} fps")
        return path

    @staticmethod
    def _discard(paths: List[str]) -> None:
        for path in paths:
            Path(path).unlink(missing_ok=True)
