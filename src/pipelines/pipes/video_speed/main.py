import math
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, Any, List

from src.pipelines.outputs import ProgressGenerationOutput, VideoGenerationOutput
from src.pipelines.contracts import BasePipe, logger
from src.pipelines.contracts import (
    PipeInput,
    PipeOutput,
    IOType,
    PipeInputSpec,
    PipeOutputSpec,
    PipeConfigSpec,
)
from src.pipelines.outputs import Icon, Progress
from src.pipelines.pipes._shared.media.video_encode import (
    has_audio_stream,
    probe_source_duration,
)


class VideoSpeedPipe(BasePipe):
    name = "video_speed"
    description = "Change video playback speed using FFmpeg"

    @classmethod
    def get_default_config(cls) -> Dict[str, Any]:
        return {
            "speed_percent": 50,
            "keep_audio": True,
            "output_format": "mp4",
        }

    @classmethod
    def configuration(cls) -> List[PipeConfigSpec]:
        return [
            PipeConfigSpec(
                name="speed_percent",
                param_type=int,
                default=50,
                description="Playback speed as percentage of original (10=very slow, 100=original speed)",
                required=False,
                min_value=10,
                max_value=100,
            ),
            PipeConfigSpec(
                name="keep_audio",
                param_type=bool,
                default=True,
                description="Keep and adjust audio speed to match video",
                required=False,
            ),
            PipeConfigSpec(
                name="output_format",
                param_type=str,
                default="mp4",
                description="Output video format",
                required=False,
                choices=["mp4", "mov", "mkv", "webm"],
            ),
        ]

    @classmethod
    def inputs(cls) -> List[PipeInputSpec]:
        return [
            PipeInputSpec("video", IOType.VIDEO, True, "Input video file paths", is_array=True),
        ]

    @classmethod
    def outputs(cls) -> List[PipeOutputSpec]:
        return [
            PipeOutputSpec("video", IOType.VIDEO, "Speed-adjusted video file paths", is_array=True),
        ]

    def _parse_bool(self, value: Any) -> bool:
        """Parse boolean from various types including Jinja2 string output."""
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.lower() in ("true", "1", "yes")
        return bool(value)

    @staticmethod
    def _build_atempo_chain(speed_factor: float) -> List[str]:
        """Build a chain of atempo filters for audio speed adjustment.

        FFmpeg's atempo filter only accepts values between 0.5 and 100.0.
        For values below 0.5 we chain multiple atempo filters.
        """
        if speed_factor >= 0.5:
            return [f"atempo={speed_factor}"]

        filters = []
        remaining = speed_factor
        while remaining < 0.5:
            filters.append("atempo=0.5")
            remaining /= 0.5
        filters.append(f"atempo={remaining}")
        return filters

    def _change_speed(self, video_path: str, generation_outputs: callable) -> str:
        """Apply speed change to a video file using FFmpeg."""
        speed_percent = int(self.config.get("speed_percent", 50))
        keep_audio = self._parse_bool(self.config.get("keep_audio", True))
        output_format = self.config.get("output_format", "mp4")

        # speed_percent=50 means half speed, so setpts multiplier = 100/50 = 2.0
        setpts_factor = 100.0 / speed_percent
        # Audio speed factor is the inverse: 50% speed = 0.5x audio rate
        audio_speed_factor = speed_percent / 100.0

        output_path = tempfile.NamedTemporaryFile(
            suffix=f".{output_format}", delete=False
        ).name

        # Build FFmpeg command
        cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-filter:v", f"setpts={setpts_factor}*PTS",
        ]

        has_audio = has_audio_stream(video_path)

        if keep_audio and has_audio:
            atempo_chain = self._build_atempo_chain(audio_speed_factor)
            cmd.extend(["-filter:a", ",".join(atempo_chain)])
        else:
            cmd.append("-an")

        cmd.append(output_path)

        logger.info(
            f"[VIDEO_SPEED] Running FFmpeg: speed_percent={speed_percent}, "
            f"setpts={setpts_factor}, audio_speed={audio_speed_factor}, "
            f"keep_audio={keep_audio}, has_audio={has_audio}"
        )

        generation_outputs(ProgressGenerationOutput(
            state=f"Changing video speed to <<NUMBER:{speed_percent}%:gauge>>",
            icon=Icon("gauge"),
            progress=Progress(50, 100),
        ))

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=600
            )
            if result.returncode != 0:
                logger.error(f"[VIDEO_SPEED] FFmpeg failed: {result.stderr}")
                # Clean up empty output
                if os.path.exists(output_path):
                    os.unlink(output_path)
                generation_outputs(ProgressGenerationOutput(
                    state=f"FFmpeg error: {result.stderr[:200]}",
                    icon=Icon("x-circle"),
                ))
                return None
        except subprocess.TimeoutExpired:
            logger.error("[VIDEO_SPEED] FFmpeg timed out after 600s")
            if os.path.exists(output_path):
                os.unlink(output_path)
            generation_outputs(ProgressGenerationOutput(
                state="FFmpeg processing timed out",
                icon=Icon("x-circle"),
            ))
            return None

        # Verify output
        if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
            logger.error("[VIDEO_SPEED] Output file is empty or missing")
            if os.path.exists(output_path):
                os.unlink(output_path)
            return None

        duration = probe_source_duration(output_path) or 0.0
        file_size = os.path.getsize(output_path)

        generation_outputs(ProgressGenerationOutput(
            state=f"Video slowed to {speed_percent}%: <<NUMBER:{duration:.1f}s:clock>> duration, <<NUMBER:{file_size / 1024 / 1024:.1f}MB:hard-drive>>",
            icon=Icon("check-circle"),
            progress=Progress(100, 100),
        ))

        return output_path

    def process(self, pipe_input: PipeInput, generation_outputs: callable) -> PipeOutput:
        videos = pipe_input.input.get("video", [])

        if not videos:
            logger.error("[VIDEO_SPEED] No input videos provided")
            generation_outputs(ProgressGenerationOutput(
                state="No input videos provided",
                icon=Icon("x-circle"),
            ))
            return PipeOutput(output={"video": []})

        # Handle single string input (not wrapped in list)
        if isinstance(videos, str):
            videos = [videos]

        output_videos = []

        for i, video_path in enumerate(videos):
            generation_outputs(ProgressGenerationOutput(
                state=f"Processing video <<NUMBER:{i + 1}/{len(videos)}:film>>",
                icon=Icon("play"),
                progress=Progress(0, 100),
            ))

            result_path = self._change_speed(video_path, generation_outputs)

            if result_path:
                generation_outputs(VideoGenerationOutput(
                    video_path=result_path,
                    temporary=False,
                ))
                output_videos.append(result_path)

        return PipeOutput(output={"video": output_videos})
