import tempfile
import numpy as np
from typing import Dict, Any, List
from pathlib import Path
from PIL import Image

from src.pipelines.outputs import (
    GenerationExecutionError,
    ProgressGenerationOutput,
    VideoGenerationOutput,
)
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
from src.platform.util.dimensions import align_dimensions


class VideoFrameMergerPipe(BasePipe):
    name = "video_frame_merger"
    description = "Merge image frames into video files"

    @classmethod
    def get_default_config(cls) -> Dict[str, Any]:
        return {
            "fps": 30.0,           # Output video frame rate
            "codec": "mp4v",       # Video codec
            "output_format": "mp4", # Output format
            "loop_count": 1,       # Number of times to loop the frames
            "reverse": False,      # Add reverse frames for ping-pong effect
            "fade_in": 0,          # Fade in duration in frames
            "fade_out": 0,         # Fade out duration in frames
            "resize_mode": "keep", # keep, fit, fill, stretch
            "target_width": -1,    # Target width (-1 to use source)
            "target_height": -1,   # Target height (-1 to use source)
        }

    @classmethod
    def configuration(cls) -> List[PipeConfigSpec]:
        return [
            PipeConfigSpec(
                name="fps",
                param_type=float,
                default=30.0,
                description="Output video frame rate",
                required=False,
                min_value=1.0,
                max_value=240.0
            ),
            PipeConfigSpec(
                name="codec",
                param_type=str,
                default="mp4v",
                description="Video codec",
                required=False,
                choices=["mp4v", "xvid", "mjpg", "divx"]  # Removed h264/x264 as they require special handling
            ),
            PipeConfigSpec(
                name="output_format",
                param_type=str,
                default="mp4",
                description="Output video format",
                required=False,
                choices=["mp4", "avi", "mov", "webm"]
            ),
            PipeConfigSpec(
                name="loop_count",
                param_type=int,
                default=1,
                description="Number of times to loop the frames",
                required=False,
                min_value=1,
                max_value=100
            ),
            PipeConfigSpec(
                name="reverse",
                param_type=bool,
                default=False,
                description="Add reverse frames for ping-pong effect",
                required=False
            ),
            PipeConfigSpec(
                name="fade_in",
                param_type=int,
                default=0,
                description="Fade in duration in frames",
                required=False,
                min_value=0,
                max_value=60
            ),
            PipeConfigSpec(
                name="fade_out",
                param_type=int,
                default=0,
                description="Fade out duration in frames",
                required=False,
                min_value=0,
                max_value=60
            ),
            PipeConfigSpec(
                name="resize_mode",
                param_type=str,
                default="keep",
                description="How to handle frame resizing",
                required=False,
                choices=["keep", "fit", "fill", "stretch"]
            ),
            PipeConfigSpec(
                name="target_width",
                param_type=int,
                default=-1,
                description="Target video width (-1 to use source)",
                required=False
            ),
            PipeConfigSpec(
                name="target_height",
                param_type=int,
                default=-1,
                description="Target video height (-1 to use source)",
                required=False
            ),
        ]

    @classmethod
    def inputs(cls) -> List[PipeInputSpec]:
        return [
            PipeInputSpec("image", IOType.IMAGE, True, "Input image frames", is_array=True),
        ]

    @classmethod
    def outputs(cls) -> List[PipeOutputSpec]:
        return [
            PipeOutputSpec("video", IOType.VIDEO, "Generated video from frames", is_array=True),
        ]

    def apply_fade_effect(self, frame: np.ndarray, alpha: float) -> np.ndarray:
        """Apply fade effect to a frame"""
        if alpha >= 1.0:
            return frame

        # Create fade effect by blending with black
        faded_frame = frame.astype(np.float32) * alpha
        return np.clip(faded_frame, 0, 255).astype(np.uint8)

    def resize_frame(self, frame: Image.Image, target_size: tuple, mode: str) -> Image.Image:
        """Resize frame according to the specified mode"""
        if mode == "keep":
            return frame

        target_w, target_h = target_size
        current_w, current_h = frame.size

        if mode == "stretch":
            # Stretch to exact dimensions
            return frame.resize((target_w, target_h), Image.LANCZOS)

        elif mode == "fit":
            # Fit inside target dimensions, maintaining aspect ratio
            frame.thumbnail((target_w, target_h), Image.LANCZOS)

            # Create black background
            result = Image.new('RGB', (target_w, target_h), (0, 0, 0))

            # Center the resized frame
            x = (target_w - frame.width) // 2
            y = (target_h - frame.height) // 2
            result.paste(frame, (x, y))

            return result

        elif mode == "fill":
            # Fill target dimensions, cropping if necessary
            current_ratio = current_w / current_h
            target_ratio = target_w / target_h

            if current_ratio > target_ratio:
                # Image is wider, fit by height and crop width
                new_h = target_h
                new_w = int(target_h * current_ratio)
            else:
                # Image is taller, fit by width and crop height
                new_w = target_w
                new_h = int(target_w / current_ratio)

            # Resize and crop
            frame = frame.resize((new_w, new_h), Image.LANCZOS)

            # Calculate crop box
            left = (new_w - target_w) // 2
            top = (new_h - target_h) // 2
            right = left + target_w
            bottom = top + target_h

            return frame.crop((left, top, right, bottom))

        return frame

    def create_video_from_frames(self, frames: List[Image.Image], generation_outputs: callable) -> str:
        """Create video file from PIL Image frames.

        Raises GenerationExecutionError if the encode fails. A merger that
        cannot write its video has failed the generation: returning None here
        used to leave the pipeline reporting a completed generation whose only
        output - the video - does not exist, so the failure surfaced to the user
        as an empty gallery rather than an error.
        """
        if not frames:
            logger.error("[VIDEO_FRAME_MERGER] No frames provided")
            return None

        try:
            # Import OpenCV only when needed
            import cv2
            # Get configuration
            fps = float(self.config.get("fps", 30.0))
            codec = self.config.get("codec", "mp4v")
            output_format = self.config.get("output_format", "mp4")
            loop_count = int(self.config.get("loop_count", 1))
            reverse = bool(self.config.get("reverse", False))
            fade_in = int(self.config.get("fade_in", 0))
            fade_out = int(self.config.get("fade_out", 0))
            resize_mode = self.config.get("resize_mode", "keep")
            target_width = int(self.config.get("target_width", -1))
            target_height = int(self.config.get("target_height", -1))

            # Determine output video dimensions
            first_frame = frames[0]
            if target_width > 0 and target_height > 0:
                video_width, video_height = target_width, target_height
            else:
                video_width, video_height = first_frame.size

            # Make sure dimensions are even (required by many codecs)
            video_width, video_height = align_dimensions(video_width, video_height, 2, "floor")

            logger.info(f"[VIDEO_FRAME_MERGER] Creating video: {video_width}x{video_height}, "
                       f"{fps}fps, codec: {codec}, frames: {len(frames)}")

            generation_outputs(ProgressGenerationOutput(
                state=f"Creating video from <<NUMBER:{len(frames)} frames:photo>> at <<NUMBER:{fps} FPS:film>>",
                icon=Icon("video"),
                progress=Progress(0, 100)
            ))

            # Create temporary file for output video
            with tempfile.NamedTemporaryFile(suffix=f'.{output_format}', delete=False) as temp_file:
                output_path = temp_file.name

            # Initialize video writer with fallback codecs
            video_writer = None
            codecs_to_try = [codec, "mp4v", "xvid", "mjpg"]  # Fallback codecs

            for try_codec in codecs_to_try:
                try:
                    if len(try_codec) == 4:
                        fourcc = cv2.VideoWriter_fourcc(*try_codec)
                    else:
                        # Handle special codec names
                        if try_codec.lower() == "h264":
                            fourcc = cv2.VideoWriter_fourcc(*"avc1")  # Use avc1 for h264
                        elif try_codec.lower() == "x264":
                            fourcc = cv2.VideoWriter_fourcc(*"x264")
                        else:
                            fourcc = cv2.VideoWriter_fourcc(*try_codec[:4])

                    video_writer = cv2.VideoWriter(output_path, fourcc, fps, (video_width, video_height))

                    if video_writer.isOpened():
                        if try_codec != codec:
                            logger.warning(f"[VIDEO_FRAME_MERGER] Codec {codec} failed, using {try_codec} instead")
                        logger.debug(f"[VIDEO_FRAME_MERGER] Successfully initialized video writer with codec {try_codec}")
                        break
                except Exception as e:
                    logger.debug(f"[VIDEO_FRAME_MERGER] Codec {try_codec} failed: {e}")
                    continue

            if not video_writer or not video_writer.isOpened():
                logger.error(f"[VIDEO_FRAME_MERGER] Failed to initialize video writer with any codec")
                generation_outputs(ProgressGenerationOutput(
                    state=f"Failed to initialize video writer",
                    icon=Icon("x-circle")
                ))
                raise GenerationExecutionError(
                    "Could not encode the video: no available codec could open a writer",
                    detail=(
                        f"Tried codecs {', '.join(codecs_to_try)} for "
                        f"{video_width}x{video_height} .{output_format} at {fps} fps. "
                        f"The OpenCV build on this host may lack the encoder."
                    ),
                )

            # Process frames
            processed_frames = []

            # Resize frames if needed
            for i, frame in enumerate(frames):
                if resize_mode != "keep" or (target_width > 0 and target_height > 0):
                    frame = self.resize_frame(frame, (video_width, video_height), resize_mode)
                elif frame.size != (video_width, video_height):
                    # Ensure consistent size
                    frame = frame.resize((video_width, video_height), Image.LANCZOS)

                processed_frames.append(frame)

            # Create frame sequence with looping and reversing
            final_frames = []
            for loop in range(loop_count):
                final_frames.extend(processed_frames)
                if reverse and len(processed_frames) > 1:
                    # Add reverse frames (excluding first and last to avoid duplicates)
                    final_frames.extend(reversed(processed_frames[1:-1]))

            total_frames = len(final_frames)
            logger.debug(f"[VIDEO_FRAME_MERGER] Writing {total_frames} frames to video")

            # Write frames to video
            for i, frame in enumerate(final_frames):
                # Convert PIL to OpenCV format (RGB to BGR)
                frame_array = np.array(frame)
                if len(frame_array.shape) == 3:
                    frame_bgr = cv2.cvtColor(frame_array, cv2.COLOR_RGB2BGR)
                else:
                    frame_bgr = frame_array

                # Apply fade effects
                alpha = 1.0
                if fade_in > 0 and i < fade_in:
                    alpha = i / fade_in
                elif fade_out > 0 and i >= (total_frames - fade_out):
                    alpha = (total_frames - i) / fade_out

                if alpha < 1.0:
                    frame_bgr = self.apply_fade_effect(frame_bgr, alpha)

                video_writer.write(frame_bgr)

                # Update progress
                progress = int((i + 1) / total_frames * 100)
                if i % 10 == 0 or i == total_frames - 1:  # Update every 10 frames
                    generation_outputs(ProgressGenerationOutput(
                        state=f"Writing frames to video: <<NUMBER:{i+1}/{total_frames}:film>>",
                        icon=Icon("video"),
                        progress=Progress(progress, 100)
                    ))

            video_writer.release()

            # Verify output file
            if not Path(output_path).exists():
                logger.error("[VIDEO_FRAME_MERGER] Output video file was not created")
                raise GenerationExecutionError(
                    "Could not encode the video: the encoder wrote no output file",
                    detail=f"Expected {output_path} after writing {total_frames} frames.",
                )

            file_size = Path(output_path).stat().st_size
            # An opened writer that produced an empty file is the same failure as
            # one that produced no file - the codec accepted every frame and
            # encoded none of them.
            if file_size == 0:
                logger.error("[VIDEO_FRAME_MERGER] Output video file is empty")
                raise GenerationExecutionError(
                    "Could not encode the video: the encoder produced an empty file",
                    detail=(
                        f"{output_path} is 0 bytes after writing {total_frames} frames "
                        f"at {video_width}x{video_height}."
                    ),
                )
            duration = total_frames / fps

            logger.info(f"[VIDEO_FRAME_MERGER] Video created successfully: {output_path}, "
                       f"Size: {file_size/1024/1024:.2f}MB, Duration: {duration:.2f}s")

            generation_outputs(ProgressGenerationOutput(
                state=f"Video created: <<NUMBER:{duration:.1f}s:clock>> duration, <<NUMBER:{file_size/1024/1024:.1f}MB:hard-drive>>",
                icon=Icon("check-circle"),
                progress=Progress(100, 100)
            ))

            return output_path

        except GenerationExecutionError:
            raise
        except Exception as e:
            import traceback
            logger.error(f"[VIDEO_FRAME_MERGER] Error creating video: {e}")
            generation_outputs(ProgressGenerationOutput(
                state=f"Error creating video: {str(e)}",
                icon=Icon("x-circle")
            ))
            raise GenerationExecutionError(
                f"Could not encode the video: {e}",
                detail=traceback.format_exc(),
            ) from e

    def process(self, pipe_input: PipeInput, generation_outputs: callable) -> PipeOutput:
        frames = pipe_input.input.get("image", [])

        if not frames:
            logger.error("[VIDEO_FRAME_MERGER] No input frames provided")
            generation_outputs(ProgressGenerationOutput(
                state="No input frames provided",
                icon=Icon("x-circle")
            ))
            return PipeOutput(output={"video": []})

        if not isinstance(frames, list):
            frames = [frames]

        generation_outputs(ProgressGenerationOutput(
            state=f"Processing <<NUMBER:{len(frames)} frames:photo>> for video creation",
            icon=Icon("play"),
            progress=Progress(0, 100)
        ))

        # Create video from frames
        video_path = self.create_video_from_frames(frames, generation_outputs)

        if not video_path:
            # Unreachable while frames is non-empty (create_video_from_frames
            # raises on every failure path), and a hard error rather than an
            # empty output if that ever stops being true: a video pipe that
            # emits no video has not succeeded.
            raise GenerationExecutionError(
                "Could not encode the video: the merger produced no output file"
            )

        generation_outputs(VideoGenerationOutput(
            video_path=video_path,
            temporary=True,
            fps=float(self.config.get("fps", 30.0))
        ))

        return PipeOutput(output={"video": [video_path]})
