import tempfile
from pathlib import Path
from typing import Callable, Iterator, List, Optional, Tuple

import cv2
import numpy as np

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
from src.pipelines.outputs import GenerationExecutionError, Icon, Progress, ProgressGenerationOutput
from src.pipelines.pipes._shared.media.inputs import as_flag, media_paths
from src.pipelines.pipes._shared.media.video_encode import probe_effective_fps
from src.platform.runtime.native.errors import SamplingCancelled
from src.platform.util.video_stream import FFmpegNotFoundError, StreamingAlphaWebmWriter, StreamingMp4Writer

DEFAULT_FPS = 24.0
PROGRESS_STEPS = 20
CHECKER_CELL = 16
CHECKER_LIGHT = 204
CHECKER_DARK = 153
PREVIEW_CRF = 20


def checkerboard(width: int, height: int) -> np.ndarray:
    rows = (np.arange(height) // CHECKER_CELL)[:, None]
    columns = (np.arange(width) // CHECKER_CELL)[None, :]
    shade = np.where((rows + columns) % 2 == 0, CHECKER_LIGHT, CHECKER_DARK).astype(np.uint8)
    return np.repeat(shade[:, :, None], 3, axis=2)


def matte_to_alpha(matte_bgr: np.ndarray, width: int, height: int, black: float, white: float) -> np.ndarray:
    gray = cv2.cvtColor(matte_bgr, cv2.COLOR_BGR2GRAY) if matte_bgr.ndim == 3 else matte_bgr
    if gray.shape[1] != width or gray.shape[0] != height:
        interpolation = cv2.INTER_AREA if gray.shape[1] > width else cv2.INTER_LINEAR
        gray = cv2.resize(gray, (width, height), interpolation=interpolation)
    span = max(white - black, 1e-3)
    alpha = np.clip((gray.astype(np.float32) / 255.0 - black) / span, 0.0, 1.0)
    return np.rint(alpha * 255.0).astype(np.uint8)


def compose_rgba(rgb: np.ndarray, alpha: np.ndarray) -> np.ndarray:
    rgba = np.empty((rgb.shape[0], rgb.shape[1], 4), dtype=np.uint8)
    rgba[:, :, :3] = np.where(alpha[:, :, None] > 0, rgb, 0)
    rgba[:, :, 3] = alpha
    return rgba


def over_checkerboard(rgb: np.ndarray, alpha: np.ndarray, board: np.ndarray) -> np.ndarray:
    weight = alpha[:, :, None].astype(np.float32) / 255.0
    return np.rint(rgb.astype(np.float32) * weight + board.astype(np.float32) * (1.0 - weight)).astype(np.uint8)


class AlphaVideoPipe(BasePipe):
    name = "alpha_video"
    description = "Combine a clip and its alpha matte into a transparent WebM, with an optional checkerboard preview"
    display_title = "Cutting out the subject"

    @classmethod
    def configuration(cls) -> List[PipeConfigSpec]:
        return [
            PipeConfigSpec("black_point", float, 0.0, "Matte values at or below this become fully transparent", required=False, min_value=0.0, max_value=0.9),
            PipeConfigSpec("white_point", float, 1.0, "Matte values at or above this become fully opaque", required=False, min_value=0.1, max_value=1.0),
            PipeConfigSpec("crf", int, 24, "WebM quality; lower is sharper and larger", required=False, min_value=0, max_value=63),
            PipeConfigSpec("preview", bool, True, "Also write an MP4 of the cut-out over a checkerboard, for players that cannot show transparency", required=False),
            PipeConfigSpec("save_matte", bool, False, "Also pass the black and white matte on as a video", required=False),
            PipeConfigSpec("max_frames", int, 0, "Only process the first N frames; 0 = all", required=False, min_value=0),
        ]

    @classmethod
    def inputs(cls) -> List[PipeInputSpec]:
        return [
            PipeInputSpec("video", IOType.VIDEO, True, "The original clips", is_array=True),
            PipeInputSpec("matte", IOType.VIDEO, True, "Black and white alpha matte clips, one per original clip", is_array=True),
        ]

    @classmethod
    def outputs(cls) -> List[PipeOutputSpec]:
        return [PipeOutputSpec("video", IOType.VIDEO, "Per clip: the transparent WebM, then its checkerboard MP4 and the matte when enabled", is_array=True)]

    def process(
        self, pipe_input: PipeInput, generation_outputs: Callable, is_cancelled: Optional[Callable] = None,
    ) -> PipeOutput:
        sources = media_paths(pipe_input.input.get("video"))
        mattes = media_paths(pipe_input.input.get("matte"))
        if not sources or not mattes:
            raise GenerationExecutionError("alpha_video needs a clip and its matte.")
        if len(sources) > 1 and len(mattes) != len(sources):
            raise GenerationExecutionError("alpha_video needs one matte per clip.")
        cancelled = is_cancelled or (lambda: False)
        written: List[str] = []
        try:
            for position, matte in enumerate(mattes):
                source = sources[min(position, len(sources) - 1)]
                written.extend(self._cut_out(source, matte, generation_outputs, cancelled))
        except BaseException:
            self._discard(written)
            raise
        return PipeOutput(output={"video": written})

    @staticmethod
    def _open(path: str, captures: List[cv2.VideoCapture]) -> Tuple[cv2.VideoCapture, int, int, int]:
        capture = cv2.VideoCapture(str(path))
        captures.append(capture)
        if not capture.isOpened():
            raise GenerationExecutionError(f"Could not open the video {Path(path).name}.")
        return (
            capture,
            int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0),
            int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0),
            int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0),
        )

    @staticmethod
    def _pairs(source: cv2.VideoCapture, matte: cv2.VideoCapture, limit: int) -> Iterator[Tuple[np.ndarray, np.ndarray]]:
        count = 0
        while not limit or count < limit:
            ok_source, frame = source.read()
            ok_matte, shade = matte.read()
            if not ok_source or not ok_matte or frame is None or shade is None:
                return
            count += 1
            yield frame, shade

    def _progress(self, generation_outputs: Callable, current: int, total: int) -> None:
        generation_outputs(ProgressGenerationOutput(
            state="Cutting out the subject",
            icon=Icon("film"),
            progress=Progress(current, max(total, 1)),
            bar=True,
        ))

    @staticmethod
    def _temp(suffix: str, registry: List[str]) -> str:
        handle = tempfile.NamedTemporaryFile(prefix="potionui-alpha-", suffix=suffix, delete=False)
        handle.close()
        registry.append(handle.name)
        return handle.name

    def _cut_out(self, source_path: str, matte_path: str, generation_outputs: Callable, cancelled: Callable) -> List[str]:
        black = float(self.config.get("black_point") or 0.0)
        white = float(self.config.get("white_point") if self.config.get("white_point") is not None else 1.0)
        if white <= black:
            raise GenerationExecutionError("The matte white point must be above the black point.")
        limit = int(self.config.get("max_frames") or 0)
        want_preview = as_flag(self.config.get("preview", True))
        captures: List[cv2.VideoCapture] = []
        temp_files: List[str] = []
        writers: List[StreamingMp4Writer] = []
        try:
            source, width, height, total = self._open(source_path, captures)
            matte, _, _, matte_total = self._open(matte_path, captures)
            fps = probe_effective_fps(source_path) or float(source.get(cv2.CAP_PROP_FPS) or 0.0) or DEFAULT_FPS
            width -= width % 2
            height -= height % 2
            if width <= 0 or height <= 0:
                raise GenerationExecutionError("The clip has no readable picture.")
            expected = min(total or matte_total, matte_total or total) if (total or matte_total) else 0
            if limit:
                expected = min(expected, limit) if expected else limit
            step = max(1, expected // PROGRESS_STEPS)
            board = checkerboard(width, height) if want_preview else None
            webm_path = self._temp(".webm", temp_files)
            webm = StreamingAlphaWebmWriter(webm_path, width, height, fps, crf=int(self.config.get("crf") or 24))
            writers.append(webm)
            preview = None
            preview_path = None
            if want_preview:
                preview_path = self._temp(".mp4", temp_files)
                preview = StreamingMp4Writer(preview_path, width, height, fps, crf=PREVIEW_CRF)
                writers.append(preview)
            count = 0
            for index, (frame, shade) in enumerate(self._pairs(source, matte, limit)):
                if cancelled():
                    raise SamplingCancelled()
                rgb = cv2.cvtColor(frame[:height, :width], cv2.COLOR_BGR2RGB)
                alpha = matte_to_alpha(shade, width, height, black, white)
                webm.write(compose_rgba(rgb, alpha))
                if preview is not None:
                    preview.write(over_checkerboard(rgb, alpha, board))
                count += 1
                if index % step == 0:
                    self._progress(generation_outputs, index, expected)
            if count == 0:
                raise GenerationExecutionError("The clip and its matte share no frames.")
            for writer in writers:
                writer.close()
        except BaseException as error:
            for writer in writers:
                writer.abort()
            for path in temp_files:
                Path(path).unlink(missing_ok=True)
            if isinstance(error, FFmpegNotFoundError):
                raise GenerationExecutionError(str(error)) from error
            raise
        finally:
            for capture in captures:
                capture.release()
        logger.info(f"[ALPHA_VIDEO] wrote {count} frames at {width}x{height}, {fps:.3f} fps")
        outputs = [webm_path]
        if preview_path:
            outputs.append(preview_path)
        if as_flag(self.config.get("save_matte", False)):
            outputs.append(matte_path)
        return outputs

    @staticmethod
    def _discard(paths: List[str]) -> None:
        for path in paths:
            Path(path).unlink(missing_ok=True)
