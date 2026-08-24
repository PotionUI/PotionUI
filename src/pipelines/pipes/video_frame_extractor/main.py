from typing import Dict, Any, List
from pathlib import Path
from PIL import Image

from src.pipelines.outputs import ProgressGenerationOutput, ImageGenerationOutput
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


class VideoFrameExtractorPipe(BasePipe):
    name = "video_frame_extractor"
    description = "Extract frames from video files at specified frame rate"

    @classmethod
    def get_default_config(cls) -> Dict[str, Any]:
        return {
            "frame_rate": 1,  # Extract 1 frame per second by default
            "start_time": 0,  # Start extraction from beginning
            "end_time": -1,   # Extract until end (-1 means full video)
            "max_frames": -1, # Maximum number of frames to extract (-1 means no limit)
            "frame_format": "png",  # Output frame format
            "quality": 95,    # JPEG quality if using jpg format
            "frame_index": None,  # When set, extract exactly this one frame (bypasses fps interval)
        }

    @classmethod
    def configuration(cls) -> List[PipeConfigSpec]:
        return [
            PipeConfigSpec(
                name="frame_rate",
                param_type=float,
                default=1.0,
                description="Frames per second to extract (e.g., 0.5 = 1 frame every 2 seconds)",
                required=False,
                min_value=0.1,
                max_value=60.0
            ),
            PipeConfigSpec(
                name="start_time",
                param_type=float,
                default=0.0,
                description="Start time in seconds",
                required=False,
                min_value=0.0
            ),
            PipeConfigSpec(
                name="end_time",
                param_type=float,
                default=-1.0,
                description="End time in seconds (-1 for full video)",
                required=False
            ),
            PipeConfigSpec(
                name="max_frames",
                param_type=int,
                default=-1,
                description="Maximum number of frames to extract (-1 for no limit)",
                required=False
            ),
            PipeConfigSpec(
                name="frame_format",
                param_type=str,
                default="png",
                description="Output frame format",
                required=False,
                choices=["png", "jpg", "jpeg"]
            ),
            PipeConfigSpec(
                name="quality",
                param_type=int,
                default=95,
                description="JPEG quality (only used for jpg/jpeg format)",
                required=False,
                min_value=1,
                max_value=100
            ),
            PipeConfigSpec(
                name="frame_index",
                param_type=int,
                default=None,
                description="When set, extract exactly this one frame (Python indexing, -1 = last) "
                            "instead of sampling at frame_rate over the time range",
                required=False
            ),
        ]

    @classmethod
    def inputs(cls) -> List[PipeInputSpec]:
        return [
            PipeInputSpec("video", IOType.VIDEO, True, "Input video files", is_array=True),
        ]

    @classmethod
    def outputs(cls) -> List[PipeOutputSpec]:
        return [
            PipeOutputSpec("image", IOType.IMAGE, "Extracted video frames", is_array=True),
            PipeOutputSpec("frame_metadata", IOType.DICT, "Frame extraction metadata", is_array=True),
        ]

    def extract_frames_from_video(self, video_path: str, generation_outputs: callable) -> List[Image.Image]:
        """Extract frames from a single video file"""
        frame_index = self.config.get("frame_index")
        if frame_index is not None:
            return self._extract_single_frame(video_path, int(frame_index), generation_outputs)

        try:
            # Import OpenCV only when needed
            import cv2

            # Open video file
            cap = cv2.VideoCapture(video_path)

            if not cap.isOpened():
                logger.error(f"[VIDEO_FRAME_EXTRACTOR] Could not open video: {video_path}")
                generation_outputs(ProgressGenerationOutput(
                    state=f"Failed to open video: {Path(video_path).name}",
                    icon=Icon("x-circle")
                ))
                return []

            # Get video properties
            total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            video_fps = cap.get(cv2.CAP_PROP_FPS)
            video_duration = total_frames / video_fps if video_fps > 0 else 0

            logger.info(f"[VIDEO_FRAME_EXTRACTOR] Video: {Path(video_path).name}, "
                       f"Duration: {video_duration:.2f}s, FPS: {video_fps:.2f}, Total frames: {total_frames}")

            # Configure extraction parameters
            extract_fps = float(self.config.get("frame_rate", 1.0))
            start_time = float(self.config.get("start_time", 0.0))
            end_time = float(self.config.get("end_time", -1.0))
            max_frames = int(self.config.get("max_frames", -1))

            # Calculate time range
            if end_time < 0:
                end_time = video_duration
            else:
                end_time = min(end_time, video_duration)

            start_time = max(0, min(start_time, video_duration))
            extraction_duration = end_time - start_time

            if extraction_duration <= 0:
                logger.warning(f"[VIDEO_FRAME_EXTRACTOR] Invalid time range: {start_time}-{end_time}")
                cap.release()
                return []

            # Calculate frame interval
            frame_interval = video_fps / extract_fps if extract_fps > 0 else 1

            # Set start position
            start_frame = int(start_time * video_fps)
            cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)

            extracted_frames = []
            frame_count = 0
            current_time = start_time

            generation_outputs(ProgressGenerationOutput(
                state=f"Extracting frames from <<EFFECT:{Path(video_path).name}:video>> at <<NUMBER:{extract_fps} FPS:clock>>",
                icon=Icon("film"),
                progress=Progress(0, 100)
            ))

            while current_time < end_time and (max_frames < 0 or len(extracted_frames) < max_frames):
                # Set position to next frame to extract
                target_frame = start_frame + int(frame_count * frame_interval)
                cap.set(cv2.CAP_PROP_POS_FRAMES, target_frame)

                ret, frame = cap.read()
                if ret and frame is not None:
                    frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    pil_image = Image.fromarray(frame_rgb)
                else:
                    # Seek-to-frame is codec-dependent and can fail on some
                    # containers; fall back to the shared sequential-read path
                    # instead of truncating the extraction here.
                    from src.pipelines.pipes._shared.media.frame_extract import extract_frame

                    try:
                        pil_image = extract_frame(video_path, target_frame)
                    except ValueError as e:
                        logger.warning(f"[VIDEO_FRAME_EXTRACTOR] {e}")
                        break

                extracted_frames.append(pil_image)

                # Output frame as temporary image
                generation_outputs(ImageGenerationOutput(
                    image=pil_image,
                    temporary=True
                ))

                frame_count += 1
                current_time = start_time + (frame_count * frame_interval) / video_fps

                # Update progress
                progress = int((current_time - start_time) / extraction_duration * 100)
                generation_outputs(ProgressGenerationOutput(
                    state=f"Extracted <<NUMBER:{len(extracted_frames)} frames:photo>> from <<EFFECT:{Path(video_path).name}:video>>",
                    icon=Icon("film"),
                    progress=Progress(progress, 100)
                ))

            cap.release()

            logger.info(f"[VIDEO_FRAME_EXTRACTOR] Extracted {len(extracted_frames)} frames from {Path(video_path).name}")

            generation_outputs(ProgressGenerationOutput(
                state=f"Successfully extracted <<NUMBER:{len(extracted_frames)} frames:check-circle>> from video",
                icon=Icon("check-circle"),
                progress=Progress(100, 100)
            ))

            return extracted_frames

        except Exception as e:
            logger.error(f"[VIDEO_FRAME_EXTRACTOR] Error extracting frames from {video_path}: {e}")
            generation_outputs(ProgressGenerationOutput(
                state=f"Error extracting frames: {str(e)}",
                icon=Icon("x-circle")
            ))
            return []

    def _extract_single_frame(
        self, video_path: str, frame_index: int, generation_outputs: callable
    ) -> List[Image.Image]:
        """``frame_index`` bypass path: extract exactly one frame via the shared
        frame-exact helper instead of the fps-interval loop."""
        from src.pipelines.pipes._shared.media.frame_extract import extract_frame

        generation_outputs(ProgressGenerationOutput(
            state=f"Extracting <<NUMBER:frame {frame_index}:film>> from <<EFFECT:{Path(video_path).name}:video>>",
            icon=Icon("film"),
            progress=Progress(0, 100)
        ))

        try:
            frame = extract_frame(video_path, frame_index)
        except ValueError as e:
            logger.error(f"[VIDEO_FRAME_EXTRACTOR] {e}")
            generation_outputs(ProgressGenerationOutput(
                state=f"Error extracting frame {frame_index}: {str(e)}",
                icon=Icon("x-circle")
            ))
            return []

        generation_outputs(ImageGenerationOutput(image=frame, temporary=True))
        generation_outputs(ProgressGenerationOutput(
            state=f"Extracted <<NUMBER:frame {frame_index}:check-circle>> from "
                  f"<<EFFECT:{Path(video_path).name}:video>>",
            icon=Icon("check-circle"),
            progress=Progress(100, 100)
        ))
        return [frame]

    def process(self, pipe_input: PipeInput, generation_outputs: callable) -> PipeOutput:
        videos = pipe_input.input.get("video", [])

        if not videos:
            logger.error("[VIDEO_FRAME_EXTRACTOR] No input videos provided")
            generation_outputs(ProgressGenerationOutput(
                state="No input videos provided",
                icon=Icon("x-circle")
            ))
            return PipeOutput(output={"image": [], "frame_metadata": []})

        if not isinstance(videos, list):
            videos = [videos]

        all_frames = []
        all_metadata = []

        for i, video in enumerate(videos):
            # Handle video input - could be file path or Path object
            if isinstance(video, Path):
                video_path = str(video)
            elif isinstance(video, str):
                video_path = video
            else:
                logger.error(f"[VIDEO_FRAME_EXTRACTOR] Invalid video input type: {type(video)}")
                continue

            generation_outputs(ProgressGenerationOutput(
                state=f"Processing video <<NUMBER:{i+1}/{len(videos)}:film>>: <<EFFECT:{Path(video_path).name}:video>>",
                icon=Icon("play"),
                progress=Progress((i * 100) // len(videos), 100)
            ))

            frames = self.extract_frames_from_video(video_path, generation_outputs)
            all_frames.extend(frames)

            # Create metadata for this video
            metadata = {
                "video_path": video_path,
                "video_name": Path(video_path).name,
                "extracted_frames": len(frames),
                "frame_rate": float(self.config.get("frame_rate", 1.0)),
                "start_time": float(self.config.get("start_time", 0.0)),
                "end_time": float(self.config.get("end_time", -1.0)),
            }
            all_metadata.append(metadata)

        generation_outputs(ProgressGenerationOutput(
            state=f"Frame extraction complete: <<NUMBER:{len(all_frames)} total frames:check-circle>>",
            icon=Icon("check-circle"),
            progress=Progress(100, 100)
        ))

        return PipeOutput(output={
            "image": all_frames,
            "frame_metadata": all_metadata
        })