"""
Video generation output handler for the application layer.

This module provides the handler for processing VideoGenerationOutput instances,
including saving videos to the filesystem, generating thumbnails asynchronously,
and creating database records with proper metadata.

The handler integrates with:
- FileStore for consistent file storage and naming
- Settings management for configurable storage directories
- Generation repository for database persistence
- ffmpeg for video thumbnail generation (both static and animated)
- Background threading for non-blocking thumbnail generation

Video Thumbnail Generation:
    - Static thumbnails (JPEG): First frame only, for immediate preview
    - Animated thumbnails (WebP): 3-second clips with reduced quality
    - Three sizes: small (480px), medium (768px), large (1024px)

Thumbnails are generated asynchronously to avoid blocking the main generation
pipeline, with database updates occurring when thumbnails are ready.
"""

import logging
import os
from typing import Dict, Any, Optional
from PIL import Image

from src.pipelines.outputs import VideoGenerationOutput
from src.features.generation.handlers.base_handler import BaseGenerationOutputHandler
from src.features.generation.output_types import OutputTypeSpec, SerializeContext, output_type_registry
from src.features.generation.records import File
from src.features.generation.repository import generation_repo
from src.features.generation import media_probe
from src.features.generation.temp_source_tracker import temp_source_tracker
from src.platform.filesystem.storage_driver import FileStorageDriver, local_copy, local_target
from src.platform.settings.settings import SettingsManager

logger = logging.getLogger(__name__)


def generate_video_thumbnails(
    video_path: str, storage_driver: FileStorageDriver, base_key: str, counter: int
) -> Dict[str, str]:
    """
    Generate WebP animated thumbnails of different sizes for a video, written
    through `storage_driver` under `{base_key}/thumbnails/...`.

    Args:
        video_path: Path to the LOCAL source video file (ffmpeg needs a real path)
        storage_driver: Where the thumbnail bytes actually live
        base_key: The saved output's parent key, e.g. `generations/<date>/<id>`
        counter: Video counter for filename generation

    Returns:
        Dictionary with thumbnail paths (relative to `base_key`): {'small': path, ...}
    """
    thumbnail_sizes = {
        'small': 480,
        'medium': 768,
        'large': 1024
    }

    thumbnail_paths = {}

    try:
        # Optimize: Generate thumbnails with reduced quality and parallel processing
        import subprocess
        import concurrent.futures

        def generate_static_thumbnail(size_name, width):
            try:
                static_filename = f"{counter}_{size_name}.jpg"
                relative_path = f"thumbnails/{static_filename}"
                key = f"{base_key}/{relative_path}"

                with local_target(storage_driver, key, suffix=".jpg") as target_path:
                    cmd = [
                        'ffmpeg', '-y',
                        '-i', video_path,
                        '-vf', f'scale={width}:-1',
                        '-vframes', '1',  # Only first frame
                        '-q:v', '8',  # Lower quality for speed (was 2)
                        '-an',
                        str(target_path)
                    ]
                    result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)

                if result.returncode == 0:
                    return size_name, relative_path
                else:
                    logger.error(f"Static thumbnail failed for {size_name}: {result.stderr}")
                    return size_name, None
            except Exception as e:
                logger.error(f"Error generating static thumbnail {size_name}: {str(e)}")
                return size_name, None

        def generate_animated_thumbnail(size_name, width):
            try:
                animated_filename = f"{counter}_{size_name}_animated.webp"
                key = f"{base_key}/thumbnails/{animated_filename}"

                with local_target(storage_driver, key, suffix=".webp") as target_path:
                    cmd = [
                        'ffmpeg', '-y',
                        '-i', video_path,
                        '-t', '3',  # Reduced from 5 to 3 seconds
                        '-vf', f'scale={width}:-1',
                        '-c:v', 'libwebp',
                        '-quality', '50',  # Reduced quality for speed
                        '-loop', '0',
                        '-an',
                        str(target_path)
                    ]
                    result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)

                if result.returncode == 0:
                    logger.debug(f"Created animated thumbnail: {animated_filename}")
                    return True
                else:
                    logger.error(f"Animated thumbnail failed for {size_name}: {result.stderr}")
                    return False
            except Exception as e:
                logger.error(f"Error generating animated thumbnail {size_name}: {str(e)}")
                return False

        try:
            # Run all static thumbnails in parallel (these are fast)
            with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
                static_futures = {
                    executor.submit(generate_static_thumbnail, size_name, width): size_name
                    for size_name, width in thumbnail_sizes.items()
                }

                for future in concurrent.futures.as_completed(static_futures):
                    size_name, path = future.result()
                    if path:
                        thumbnail_paths[size_name] = path

            # If static thumbnails succeeded, generate animated ones in parallel and
            # wait for them, bounded by a timeout so a slow encode cannot stall the worker.
            if thumbnail_paths:
                with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
                    animated_futures = {
                        executor.submit(generate_animated_thumbnail, size_name, width): size_name
                        for size_name, width in thumbnail_sizes.items()
                    }

                    # Wait for animated thumbnails but with shorter timeout
                    for future in concurrent.futures.as_completed(animated_futures, timeout=20):
                        future.result()  # Just to catch any exceptions

        except concurrent.futures.TimeoutError:
            logger.warning("Animated thumbnail generation timed out, but static thumbnails are available")
        except Exception as e:
            logger.error(f"Error in parallel thumbnail generation: {str(e)}")
            return {}

    except Exception as e:
        logger.error(f"Failed to create video thumbnails directory: {str(e)}")

    return thumbnail_paths


def _schedule_async_thumbnail_generation(
    saved_path: str, storage_driver: FileStorageDriver, generation_id: str, counter: int
):
    """Schedule async thumbnail generation in background thread.

    `saved_path` is the already-published `generations/...` key (the write
    that put it there completed synchronously, before this is scheduled), so
    unlike the pre-driver version this never polls a file mid-write - the
    driver copy is durable the moment `put_file`/`put_bytes` returns. The
    wait/ffprobe loop stays anyway as a defensive check that the bytes are
    genuinely readable back (belt-and-suspenders against a backend with
    weaker consistency than local disk).
    """
    import threading

    def async_thumbnail_worker():
        try:
            with local_copy(storage_driver, saved_path, suffix=os.path.splitext(saved_path)[1]) as local_path:
                video_path = str(local_path)

                # Wait for video file to be fully written and accessible
                import time
                max_wait_time = 30  # Maximum wait time in seconds
                wait_interval = 0.5  # Check interval in seconds
                waited_time = 0

                while waited_time < max_wait_time:
                    if os.path.exists(video_path):
                        try:
                            # Try to open and verify the video file is complete
                            import subprocess
                            result = subprocess.run([
                                'ffprobe', '-v', 'quiet',
                                '-print_format', 'json',
                                '-show_format',
                                video_path
                            ], capture_output=True, text=True, timeout=5)

                            if result.returncode == 0:
                                # File is accessible and complete
                                break
                        except (subprocess.TimeoutExpired, subprocess.CalledProcessError, FileNotFoundError):
                            pass  # File not ready yet

                    time.sleep(wait_interval)
                    waited_time += wait_interval

                if waited_time >= max_wait_time:
                    logger.error(f"Timeout waiting for video file to be ready: {video_path}")
                    return

                # Generate thumbnails
                base_key = saved_path.rsplit('/', 1)[0]
                thumbnail_paths = generate_video_thumbnails(video_path, storage_driver, base_key, counter)

            if thumbnail_paths:
                # Update database with thumbnail paths
                _update_video_thumbnails_in_db(saved_path, generation_id, thumbnail_paths)

                logger.debug(f"Video thumbnails generated successfully: {saved_path}")
            else:
                logger.warning(f"Failed to generate video thumbnails: {saved_path}")

        except Exception as e:
            logger.error(f"Error in async thumbnail generation: {str(e)}")

    # Start background thread
    thread = threading.Thread(target=async_thumbnail_worker, daemon=True)
    thread.start()


def _update_video_thumbnails_in_db(saved_path: str, generation_id: str, thumbnail_paths: dict):
    """Update database file record with thumbnail paths"""
    try:
        from src.features.generation.repository import generation_repo
        from src.features.generation.file_repository import file_repo

        # Find the video file record
        files = generation_repo.get_files(generation_id, is_final=True)
        video_filename = os.path.basename(saved_path)

        # Update ALL matching video files (handle potential duplicates)
        matching_files = [
            file_record for file_record in files
            if os.path.basename(file_record.file_path) == video_filename and file_record.file_type == 'VIDEO'
        ]

        if matching_files:
            file_repo.set_thumbnail_paths(
                [file_record.id for file_record in matching_files],
                thumbnail_paths.get('small'),
                thumbnail_paths.get('medium'),
                thumbnail_paths.get('large'),
            )

            logger.debug(f"Updated thumbnails for {len(matching_files)} video file record(s): {video_filename}")
        else:
            logger.warning(f"No video file record found for thumbnail update: {video_filename}")

    except Exception as e:
        logger.error(f"Error updating video thumbnails in database: {str(e)}")


class VideoGenerationOutputHandler(BaseGenerationOutputHandler):
    """Handler for VideoGenerationOutput - handles video saving and processing."""

    def can_handle(self, output) -> bool:
        """Check if this handler can process VideoGenerationOutput."""
        return isinstance(output, VideoGenerationOutput)

    def handle(self, output: VideoGenerationOutput) -> Dict[str, Any]:
        """
        Process VideoGenerationOutput - save video to disk (to tmp if temporary).

        Args:
            output: VideoGenerationOutput to process

        Returns:
            Dictionary with processing metadata including file path
        """
        metadata = {
            'handler': 'VideoGenerationOutputHandler',
            'processed': True,
            'temporary': output.temporary,
            'saved_path': None,
            'file_record': None
        }

        try:
            # Save all videos - temporary ones go to tmp directory
            if output.video_path:
                saved_path = self._save_video_file(output)
                if saved_path:
                    metadata['saved_path'] = saved_path
                    output._saved_path = saved_path  # Store path in output for serializer access

                    # Only create database record for non-temporary videos
                    if not output.temporary:
                        file_record = self._create_file_record(output, saved_path)
                        if file_record:
                            metadata['file_record'] = {
                                'id': file_record.id,
                                'filename': os.path.basename(file_record.file_path),
                                'file_path': file_record.file_path,
                                'file_size': file_record.file_size
                            }
                elif not output.temporary:
                    # A failed final save must be visible to the caller as a
                    # failure, not silently reported as processed - the
                    # orchestrator relies on 'processed'/'save_error' to
                    # decide whether the generation actually completed.
                    logger.warning("Failed to save video")
                    metadata['processed'] = False
                    metadata['save_error'] = "Failed to save video"

        except Exception as e:
            logger.error(f"Error handling VideoGenerationOutput: {str(e)}")
            metadata['processed'] = False
            metadata['error'] = str(e)

        return metadata

    def _save_video_file(self, output: VideoGenerationOutput) -> Optional[str]:
        """Save video file using FileStore with consistent naming and structure."""
        try:
            # Import FileStore locally to avoid circular import
            from src.platform.filesystem.file_store import FileStore

            # Initialize file service with the correct storage directory
            file_service = FileStore(self._resolve_storage_dir(), storage_driver=self._resolve_storage_driver())

            # Get file extension
            extension = os.path.splitext(output.video_path)[1].lstrip('.') or 'mp4'

            # Determine storage type and whether it's temporary
            if output.temporary:
                # Save temporary videos to tmp directory
                storage_type = 'tmp'
                is_temporary = True
                # For temporary files, use generation_id as prefix if available
                prefix = f"tmp_video_{self.generation_id}" if self.generation_id else "tmp_video"
            else:
                # Save non-temporary videos to generations directory
                storage_type = 'generations'
                is_temporary = False
                # Increment counter only for non-temporary videos
                self.image_counter += 1
                prefix = str(self.image_counter)

            full_path, file_metadata = file_service.save_file_from_path(
                generation_id=self.generation_id if not output.temporary else None,
                source_path=output.video_path,
                extension=extension,
                prefix=prefix,
                storage_type=storage_type,
                is_temporary=is_temporary
            )

            if full_path and file_metadata:
                # This path is the pipe's own NamedTemporaryFile source -- the
                # same path is read here again later (preview -> final save),
                # so track it for cleanup rather than deleting it now. See
                # temp_source_tracker. Registered only after a successful
                # copy: a source that was never actually read is not ours to
                # clean up.
                temp_source_tracker.register(self.generation_id, output.video_path)
                logger.debug(f"Video saved successfully to {'tmp' if output.temporary else 'generations'}: {file_metadata['file_path']}")
                return file_metadata['file_path']  # Return relative path
            else:
                logger.error("Failed to save video file")
                return None

        except Exception as e:
            logger.error(f"Error saving video file: {str(e)}")
            return None

    def _get_video_dimensions(self, video_path: str) -> tuple[Optional[int], Optional[int]]:
        """Get video dimensions using ffprobe or opencv as fallback.

        Delegates to the shared probe in ``media_probe`` (also used by the
        media upload path).
        """
        return media_probe.get_video_dimensions(video_path)

    def _schedule_async_thumbnail_generation(self, saved_path: str, generation_id: str, counter: int):
        """Schedule async thumbnail generation for this video"""
        _schedule_async_thumbnail_generation(saved_path, self._resolve_storage_driver(), generation_id, counter)

    def _create_file_record(self, output: VideoGenerationOutput, saved_path: str) -> Optional[File]:
        """Create database record for the saved video file."""
        try:
            storage_driver = self._resolve_storage_driver()
            file_size = storage_driver.size(saved_path) or 0

            # Get video dimensions and duration/fps. Best-effort - None for
            # either half of the pair means "not determined", not "zero".
            # Materialized from the driver's own copy (not `output.video_path`,
            # the pipe's temp source) so this works under a non-local driver
            # too and is unaffected by when `temp_source_tracker` cleans that
            # temp source up.
            with local_copy(storage_driver, saved_path, suffix=os.path.splitext(saved_path)[1]) as local_path:
                width, height = self._get_video_dimensions(str(local_path))
                duration_seconds, fps = media_probe.get_video_duration_fps(str(local_path))

            # Schedule async thumbnail generation for final videos
            thumbnail_paths = {}
            if not output.temporary:
                # Start async thumbnail generation (don't wait for it)
                self._schedule_async_thumbnail_generation(saved_path, self.generation_id, self.image_counter)
                logger.debug(f"Scheduled async thumbnail generation for video: {saved_path}")
            else:
                logger.debug("Skipping thumbnail generation for temporary video")

            file_record = File(
                file_path=saved_path,
                file_type='VIDEO',
                user_id=self.user_id,
                file_size=file_size,
                pipe_name=getattr(output, 'pipe_name', None),
                is_final=not output.temporary,
                is_derived=bool(getattr(output, 'derived', False)),
                width=width,
                height=height,
                duration_seconds=duration_seconds,
                fps=fps,
                thumbnail_small=thumbnail_paths.get('small'),
                thumbnail_medium=thumbnail_paths.get('medium'),
                thumbnail_large=thumbnail_paths.get('large')
            )

            # Save to database and associate with generation
            created_file = generation_repo.add_file(self.generation_id, file_record)

            return created_file

        except Exception as e:
            logger.error(f"Error creating video file record: {str(e)}")
            return None


def serialize_video_output(output: VideoGenerationOutput, ctx: SerializeContext) -> Dict[str, Any]:
    """Serialize VideoGenerationOutput for workbench_update messages."""
    result = {
        'temporary': getattr(output, 'temporary', True),
        'seed': getattr(output, 'seed', None),
        'resolution': getattr(output, 'resolution', None),
        'duration': getattr(output, 'duration', None),
        'fps': getattr(output, 'fps', None),
        'sampler': getattr(output, 'sampler', None),
        'clip_skip': getattr(output, 'clip_skip', None),
        'cfg': getattr(output, 'cfg', None),
        'denoise': getattr(output, 'denoise', None),
        'step': getattr(output, 'step', None),
        'motion_strength': getattr(output, 'motion_strength', None),
        'file_type': 'video'  # Distinguish from image outputs
    }

    if output.video_path:
        try:
            # Convert video path to API endpoint path
            video_path = str(output.video_path)

            # For saved videos (non-temporary), create API path from video_path
            if not getattr(output, 'temporary', True):
                # Check if we have a _saved_path attribute (set by handler)
                if hasattr(output, '_saved_path') and output._saved_path:
                    file_path = output._saved_path
                else:
                    # Use video_path as fallback
                    file_path = video_path

                # Extract filename and create API path using generation_id from context
                filename = file_path.split('/')[-1] if '/' in file_path else file_path
                result['path'] = f"/api/media/generations/{ctx.generation_id}/{filename}"

            # For temporary videos (intermediate), create temp file endpoint
            else:
                # For temporary intermediate videos, use the saved path if available
                from pathlib import Path

                # Check if we have a _saved_path from the handler (for videos saved to tmp)
                if hasattr(output, '_saved_path') and output._saved_path:
                    # Extract filename from the saved path (e.g., "tmp/tmp_video_xxx_ULID.mp4")
                    saved_path = output._saved_path
                    filename = Path(saved_path).name
                else:
                    # Fallback to original video path name
                    filename = Path(video_path).name

                result['path'] = f"/api/media/tmp/{filename}"
                result['temp_path'] = video_path  # Keep original path for debugging
                result['video_name'] = filename

        except Exception as e:
            logger.error(f"Failed to serialize video path: {str(e)}")
            result['video_name'] = None

    return result


output_type_registry.register(OutputTypeSpec(
    output_cls=VideoGenerationOutput,
    key='video',
    message_type='workbench_update',
    serializer=serialize_video_output,
    handler_cls=VideoGenerationOutputHandler,
))
