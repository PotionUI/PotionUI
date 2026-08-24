import os
from typing import Dict, Any, List, Union
from pathlib import Path
from PIL import Image, ImageFile

from src.pipelines.outputs import (
    ProgressGenerationOutput, ImageGenerationOutput, VideoGenerationOutput, AudioGenerationOutput,
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


class MediaLoaderPipe(BasePipe):
    name = "media_loader"
    description = "Load media files (images/videos/audio) from form file paths and convert them to proper IOType objects"

    @classmethod
    def get_default_config(cls) -> Dict[str, Any]:
        return {
            "validate_files": True,  # Validate that files exist and are readable
            "max_file_size": 104857600,  # 100MB max file size
            "supported_image_formats": [".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".webp"],
            "supported_video_formats": [".mp4", ".avi", ".mov", ".mkv", ".webm", ".flv", ".wmv"],
            "supported_audio_formats": [".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac"],
            "auto_detect_type": True,  # Auto-detect media type from file extension
            "media": [],  # List of media files to load: [{"type": "video", "path": "..."}]
        }

    @classmethod
    def configuration(cls) -> List[PipeConfigSpec]:
        return [
            PipeConfigSpec(
                name="validate_files",
                param_type=bool,
                default=True,
                description="Validate that files exist and are readable",
                required=False
            ),
            PipeConfigSpec(
                name="max_file_size",
                param_type=int,
                default=104857600,
                description="Maximum file size in bytes (default 100MB)",
                required=False,
                min_value=1024,  # 1KB minimum
                max_value=1073741824  # 1GB maximum
            ),
            PipeConfigSpec(
                name="auto_detect_type",
                param_type=bool,
                default=True,
                description="Auto-detect media type from file extension",
                required=False
            ),
            PipeConfigSpec(
                name="supported_image_formats",
                param_type=list,
                default=[".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".webp"],
                description="Supported image file extensions",
                required=False
            ),
            PipeConfigSpec(
                name="supported_video_formats",
                param_type=list,
                default=[".mp4", ".avi", ".mov", ".mkv", ".webm", ".flv", ".wmv"],
                description="Supported video file extensions",
                required=False
            ),
            PipeConfigSpec(
                name="supported_audio_formats",
                param_type=list,
                default=[".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac"],
                description="Supported audio file extensions",
                required=False
            ),
            PipeConfigSpec(
                name="media",
                param_type=list,
                default=[],
                description="List of media files to load with type and path",
                required=False
            ),
        ]

    @classmethod
    def inputs(cls) -> List[PipeInputSpec]:
        # Media files come from configuration; the only input is the settings
        # service used to resolve storage-root-relative paths.
        return [
            PipeInputSpec("SETTINGS", IOType.SERVICE, False,
                          "Settings manager, to resolve storage-root-relative media paths", is_array=False),
        ]

    @classmethod
    def outputs(cls) -> List[PipeOutputSpec]:
        return [
            PipeOutputSpec("image", IOType.IMAGE, "Loaded image files", is_array=True),
            PipeOutputSpec("video", IOType.VIDEO, "Loaded video files", is_array=True),
            PipeOutputSpec("audio", IOType.AUDIO, "Loaded audio file paths", is_array=True),
            PipeOutputSpec("media_metadata", IOType.DICT, "Metadata about loaded media files", is_array=True),
        ]

    def _resolve_media_path(self, file_path: str, settings) -> str:
        """Two relative-path conventions reach this pipe: CWD-relative values
        including the storage prefix (the upload flow stores
        'storage/uploads/...') and storage-root-relative values (the DB stores
        generation outputs as 'generations/<date>/<id>/<n>.ext', which the
        history picker copies verbatim). Try the value as-given first, then
        joined onto the storage root; first existing wins. Absolute paths and
        values that already resolve are returned untouched - never re-rooted
        (a contained path must not be silently double-prefixed)."""
        path = Path(file_path)
        if path.is_absolute() or path.exists():
            return file_path
        if settings is not None:
            candidate = Path(settings.get_file_storage_directory()) / file_path
            if candidate.exists():
                return str(candidate)
        return file_path

    def _validate_file(self, file_path: str) -> bool:
        """Validate that a file exists and is readable"""
        try:
            path = Path(file_path)

            # Check if file exists
            if not path.exists():
                logger.error(f"[MEDIA_LOADER] File does not exist: {file_path}")
                return False

            # Check if it's a file (not directory)
            if not path.is_file():
                logger.error(f"[MEDIA_LOADER] Path is not a file: {file_path}")
                return False

            # Check file size
            max_size = self.config.get("max_file_size", 104857600)
            file_size = path.stat().st_size
            if file_size > max_size:
                logger.error(f"[MEDIA_LOADER] File too large ({file_size} bytes > {max_size}): {file_path}")
                return False

            # Check if file is readable
            if not os.access(file_path, os.R_OK):
                logger.error(f"[MEDIA_LOADER] File is not readable: {file_path}")
                return False

            return True

        except Exception as e:
            logger.error(f"[MEDIA_LOADER] Error validating file {file_path}: {e}")
            return False

    def _detect_media_type(self, file_path: str) -> str:
        """Auto-detect media type from file extension"""
        path = Path(file_path)
        extension = path.suffix.lower()

        image_formats = self.config.get("supported_image_formats", [])
        video_formats = self.config.get("supported_video_formats", [])
        audio_formats = self.config.get("supported_audio_formats", [])

        if extension in image_formats:
            return "image"
        elif extension in video_formats:
            return "video"
        elif extension in audio_formats:
            return "audio"
        else:
            logger.warning(f"[MEDIA_LOADER] Unknown file format: {extension} for file: {file_path}")
            return "unknown"

    def _decode_image(self, file_path: str) -> Image.Image:
        """Open the image and force a full pixel decode (PIL is lazy on `Image.open`:
        it only reads the header, and the actual pixel decode happens on first use,
        against a still-open file object).

        This eager `image.load()` is a CORRECTNESS requirement, not just better error
        attribution - do not remove it as an "optimization". This pipe runs inside the
        generation pipeline on a worker thread (`InProcessBackend` drives pipes via
        `asyncio.to_thread`), but the image this method returns is also handed to
        `generation_outputs(ImageGenerationOutput(...))`, which the event loop consumes
        via `OutputBridge` and encodes as a base64 preview
        (`src/features/generation/handlers/image_handler.py::create_base64_image`) on the
        EVENT-LOOP thread, concurrently with this pipe (and later pipes, e.g.
        `_prep_start_frame`'s `.convert("RGB")`) continuing to run on the worker thread.
        A lazily-opened `Image` still holds an open file handle (`image.fp`) and no
        decoded pixel buffer (`image.im`); if two threads both trigger PIL's decoder on
        that same file object, the decode is corrupted mid-stream and raises errors like
        "unrecognized data stream contents when reading image file" - on a perfectly
        valid file. Forcing the decode here, before the image is ever handed off,
        guarantees `image.im` is populated and `image.fp` is cleared (see
        `PIL.Image.Image.load`), so every later consumer - on any thread - only ever
        reads an already-decoded, file-independent image.

        If the strict decode raises OSError (PIL's IMAGING_CODEC_UNKNOWN / truncated data
        - i.e. the file genuinely is truncated/corrupt, unlike the race above), retry once
        with `ImageFile.LOAD_TRUNCATED_IMAGES = True` and log a loud warning on success.
        That flag is global PIL state, so it is always restored afterwards.
        """
        image = Image.open(file_path)
        try:
            image.load()
            return image
        except OSError:
            previous = ImageFile.LOAD_TRUNCATED_IMAGES
            try:
                ImageFile.LOAD_TRUNCATED_IMAGES = True
                image = Image.open(file_path)
                image.load()
                logger.warning(
                    f"[MEDIA_LOADER] Truncated/corrupt image file was only PARTIALLY decoded "
                    f"(missing pixel data was filled in, e.g. as a grey/black band) via the "
                    f"LOAD_TRUNCATED_IMAGES fallback - downstream output from this image may be "
                    f"visibly incomplete: {file_path}"
                )
                return image
            finally:
                ImageFile.LOAD_TRUNCATED_IMAGES = previous

    def _load_image(self, file_path: str, generation_outputs: callable) -> Image.Image:
        """Load an image file"""
        try:
            generation_outputs(ProgressGenerationOutput(
                state=f"Loading image: <<EFFECT:{Path(file_path).name}:image>>",
                icon=Icon("image")
            ))

            image = self._decode_image(file_path)

            # Convert to RGB if necessary
            if image.mode in ('RGBA', 'LA', 'P'):
                # Convert to RGB, handling transparency
                if image.mode == 'P' and 'transparency' in image.info:
                    image = image.convert('RGBA')

                if image.mode in ('RGBA', 'LA'):
                    # Create white background
                    background = Image.new('RGB', image.size, (255, 255, 255))
                    if image.mode == 'RGBA':
                        background.paste(image, mask=image.split()[-1])  # Use alpha channel as mask
                    else:
                        background.paste(image, mask=image.split()[-1])  # Use alpha channel as mask
                    image = background
                else:
                    image = image.convert('RGB')
            elif image.mode not in ('RGB', 'L'):
                image = image.convert('RGB')

            logger.info(f"[MEDIA_LOADER] Loaded image: {file_path} ({image.size[0]}x{image.size[1]})")
            return image

        except Exception as e:
            logger.error(f"[MEDIA_LOADER] Error loading image {file_path}: {e}")
            generation_outputs(ProgressGenerationOutput(
                state=f"Error loading image: {str(e)}",
                icon=Icon("x-circle")
            ))

            abs_path = str(Path(file_path).resolve())
            try:
                file_size = Path(file_path).stat().st_size
            except OSError:
                file_size = -1
            try:
                with open(file_path, "rb") as f:
                    header_hex = f.read(12).hex()
            except OSError:
                header_hex = "<unreadable>"
            detected_format = None
            try:
                with Image.open(file_path) as probe:
                    detected_format = probe.format
            except Exception:
                pass

            raise OSError(
                f"[MEDIA_LOADER] Failed to decode image '{abs_path}' "
                f"(size={file_size} bytes, format={detected_format!r}, "
                f"first_bytes={header_hex}): {e}"
            ) from e

    def _load_video(self, file_path: str, generation_outputs: callable) -> str:
        """Load a video file (returns path for now, video pipes handle the actual loading)"""
        try:
            generation_outputs(ProgressGenerationOutput(
                state=f"Loading video: <<EFFECT:{Path(file_path).name}:video>>",
                icon=Icon("video")
            ))

            # For videos, we typically just return the path
            # The actual video processing is handled by other pipes like video_frame_extractor
            logger.info(f"[MEDIA_LOADER] Loaded video path: {file_path}")
            return file_path

        except Exception as e:
            logger.error(f"[MEDIA_LOADER] Error loading video {file_path}: {e}")
            generation_outputs(ProgressGenerationOutput(
                state=f"Error loading video: {str(e)}",
                icon=Icon("x-circle")
            ))
            return None

    def _load_audio(self, file_path: str, generation_outputs: callable) -> str:
        """Load an audio file (returns path -- passthrough, same convention as
        _load_video: downstream pipes that mux/decode audio take the path)."""
        try:
            generation_outputs(ProgressGenerationOutput(
                state=f"Loading audio: <<EFFECT:{Path(file_path).name}:audio>>",
                icon=Icon("audio")
            ))

            logger.info(f"[MEDIA_LOADER] Loaded audio path: {file_path}")
            return file_path

        except Exception as e:
            logger.error(f"[MEDIA_LOADER] Error loading audio {file_path}: {e}")
            generation_outputs(ProgressGenerationOutput(
                state=f"Error loading audio: {str(e)}",
                icon=Icon("x-circle")
            ))
            return None

    def process(self, pipe_input: PipeInput, generation_outputs: callable) -> PipeOutput:
        settings = pipe_input.input.get("SETTINGS") if pipe_input is not None and pipe_input.input else None
        # Get media list from configuration instead of input
        media_list = self.config.get("media", [])

        if not media_list:
            logger.error("[MEDIA_LOADER] No media files configured")
            generation_outputs(ProgressGenerationOutput(
                state="No media files configured",
                icon=Icon("x-circle")
            ))
            return PipeOutput(output={"image": [], "video": [], "audio": [], "media_metadata": []})

        if not isinstance(media_list, list):
            media_list = [media_list]

        loaded_images = []
        loaded_videos = []
        loaded_audio = []
        metadata_list = []

        validate_files = self.config.get("validate_files", True)
        auto_detect_type = self.config.get("auto_detect_type", True)

        generation_outputs(ProgressGenerationOutput(
            state=f"Processing <<NUMBER:{len(media_list)} media files:folder>>",
            icon=Icon("folder"),
            progress=Progress(0, 100)
        ))

        for i, media_item in enumerate(media_list):
            try:
                # Handle different input formats
                if isinstance(media_item, dict):
                    media_type = media_item.get("type", "unknown")
                    file_path = media_item.get("path", "")
                elif isinstance(media_item, str):
                    # If just a string path, auto-detect type
                    file_path = media_item
                    media_type = "unknown"
                else:
                    logger.error(f"[MEDIA_LOADER] Invalid media item format: {type(media_item)}")
                    continue

                if not file_path:
                    logger.error(f"[MEDIA_LOADER] Empty file path in media item {i}")
                    continue

                file_path = self._resolve_media_path(file_path, settings)


                # Validate file if requested. A configured media item that fails
                # validation (missing/unreadable/oversized) is a bug, not something
                # to silently skip - raise so the failure is visible.
                if validate_files and not self._validate_file(file_path):
                    tried = str(Path(file_path).resolve())
                    if not Path(file_path).is_absolute():
                        storage_root = settings.get_file_storage_directory() if settings is not None else "storage"
                        tried += f" (also tried under the storage root: {str((Path(storage_root) / file_path).resolve())})"
                    raise OSError(f"[MEDIA_LOADER] Configured media file is missing or unreadable: {tried}")

                # A pipeline stage that declares an explicit expected kind (e.g. a
                # video-upscale mode configuring `type: "video"`) is stating a hard
                # requirement, not a hint - the form field, upload endpoint, and
                # frontend picker can all be bypassed (stale form data, a picker bug,
                # a direct API call), so this is the one seam every path through here
                # converges on. Cross-check the expected kind against the file's own
                # extension and fail fast, naming both kinds, rather than silently
                # loading the wrong kind of file into the wrong output bucket (or
                # routing it into a bucket the pipeline never wired up at all).
                expected_type = media_type if media_type not in ("unknown", "") else None

                # Auto-detect type if not specified or unknown
                if auto_detect_type and media_type in ("unknown", ""):
                    media_type = self._detect_media_type(file_path)
                elif expected_type in ("image", "video", "audio"):
                    actual_type = self._detect_media_type(file_path)
                    if actual_type not in ("unknown", "") and actual_type != expected_type:
                        raise ValueError(
                            f"[MEDIA_LOADER] This pipeline stage expects a {expected_type} file, "
                            f"but '{Path(file_path).name}' is a {actual_type} file: "
                            f"{str(Path(file_path).resolve())}"
                        )

                generation_outputs(ProgressGenerationOutput(
                    state=f"Loading <<EFFECT:{Path(file_path).name}:{media_type}>> (<<NUMBER:{i+1}/{len(media_list)}>>)",
                    icon=Icon("folder-open"),
                    progress=Progress((i * 80) // len(media_list), 100)
                ))

                # Load based on type
                if media_type == "image":
                    image = self._load_image(file_path, generation_outputs)
                    if image:
                        loaded_images.append(image)

                        # Output image immediately for preview
                        generation_outputs(ImageGenerationOutput(
                            image=image,
                            temporary=True
                        ))

                        metadata_list.append({
                            "type": "image",
                            "path": file_path,
                            "filename": Path(file_path).name,
                            "size": image.size,
                            "mode": image.mode,
                            "file_size": Path(file_path).stat().st_size
                        })

                elif media_type == "video":
                    video_path = self._load_video(file_path, generation_outputs)
                    if video_path:
                        loaded_videos.append(video_path)

                        # Output video path for pipeline
                        generation_outputs(VideoGenerationOutput(
                            video_path=video_path,
                            temporary=True
                        ))

                        metadata_list.append({
                            "type": "video",
                            "path": file_path,
                            "filename": Path(file_path).name,
                            "file_size": Path(file_path).stat().st_size
                        })

                elif media_type == "audio":
                    audio_path = self._load_audio(file_path, generation_outputs)
                    if audio_path:
                        loaded_audio.append(audio_path)

                        # Output audio path for pipeline
                        generation_outputs(AudioGenerationOutput(
                            audio_path=audio_path,
                            temporary=True
                        ))

                        metadata_list.append({
                            "type": "audio",
                            "path": file_path,
                            "filename": Path(file_path).name,
                            "file_size": Path(file_path).stat().st_size
                        })

                else:
                    logger.warning(f"[MEDIA_LOADER] Unsupported media type '{media_type}' for file: {file_path}")
                    continue

            except (OSError, ValueError):
                # A configured media item that cannot be validated/decoded, or that
                # fails the expected-kind check above, is a bug - not something to
                # continue past - let it propagate. (_load_image and the
                # validate_files check raise OSError; the expected-kind check raises
                # ValueError.)
                raise
            except Exception as e:
                logger.error(f"[MEDIA_LOADER] Error processing media item {i}: {e}")
                continue

        total_loaded = len(loaded_images) + len(loaded_videos) + len(loaded_audio)
        generation_outputs(ProgressGenerationOutput(
            state=f"Loaded <<NUMBER:{total_loaded} files:check-circle>> (<<NUMBER:{len(loaded_images)} images:image>>, "
                  f"<<NUMBER:{len(loaded_videos)} videos:video>>, <<NUMBER:{len(loaded_audio)} audio:audio>>)",
            icon=Icon("check-circle"),
            progress=Progress(100, 100)
        ))

        logger.info(f"[MEDIA_LOADER] Successfully loaded {len(loaded_images)} images, {len(loaded_videos)} videos, "
                    f"{len(loaded_audio)} audio files")

        return PipeOutput(output={
            "image": loaded_images,
            "video": loaded_videos,
            "audio": loaded_audio,
            "media_metadata": metadata_list
        })
