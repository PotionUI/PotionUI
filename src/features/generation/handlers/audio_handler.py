"""
Audio generation output handler for the application layer.

Processes AudioGenerationOutput: copies the emitted file into storage through
FileStore and records a `files` row typed 'AUDIO' for final tracks.

Modelled on the video/mesh handlers (`video_handler.py`, `mesh_handler.py`)
rather than the narrower version of this module they superseded. It fixes
three defects the original version had relative to that standard:

- Every audio output is saved, not just final ones - temporary (in-progress)
  audio goes to `storage/tmp/` the same way a temporary video does. The
  original only saved when `not output.temporary`, but the serializer's
  temporary branch always emits a `/api/media/tmp/{filename}` preview URL
  regardless - so every in-progress audio preview was a dead link. The save
  is skipped only when there is no `audio_path` at all.
- The save streams disk-to-disk via `FileStore.save_file_from_path` instead
  of reading the whole file into memory first. Stable Audio 3 can emit up to
  380s of 44.1kHz stereo (~67MB); reading that into a `bytes` object just to
  hand it back to `save_file` doubles the buffered payload for no reason.
- The pipe's own temporary source file is registered with
  `temp_source_tracker` so it is unlinked once the generation reaches a
  terminal state, instead of nothing ever owning its cleanup.

`duration_seconds` is probed (`media_probe.get_audio_duration_seconds`) and
persisted on the file record when the pipe didn't already set
`output.duration` - the field that matters most given how long a track can
run.
"""

import logging
import os
from pathlib import Path
from typing import Dict, Any, Optional

from src.pipelines.outputs import AudioGenerationOutput
from src.features.generation.handlers.base_handler import BaseGenerationOutputHandler
from src.features.generation.output_types import OutputTypeSpec, SerializeContext, output_type_registry
from src.features.generation.records import File
from src.features.generation.repository import generation_repo
from src.features.generation import media_probe
from src.features.generation.temp_source_tracker import temp_source_tracker
from src.platform.filesystem.storage_driver import local_copy

logger = logging.getLogger(__name__)

AUDIO_FILE_TYPE = 'AUDIO'


class AudioGenerationOutputHandler(BaseGenerationOutputHandler):
    """Handler for AudioGenerationOutput - saves audio (preview or final) and
    records final tracks in the database."""

    def can_handle(self, output) -> bool:
        """Check if this handler can process AudioGenerationOutput."""
        return isinstance(output, AudioGenerationOutput)

    def handle(self, output: AudioGenerationOutput) -> Dict[str, Any]:
        """
        Process AudioGenerationOutput - save to disk (tmp if temporary),
        then record final tracks in the database.

        Args:
            output: AudioGenerationOutput to process

        Returns:
            Dictionary with processing metadata including file path
        """
        metadata = {
            'handler': 'AudioGenerationOutputHandler',
            'processed': True,
            'temporary': output.temporary,
            'track_type': output.track_type,
            'saved_path': None,
            'file_record': None,
        }

        try:
            if not output.audio_path:
                return metadata

            saved_path = self._save_audio_file(output)
            if saved_path:
                metadata['saved_path'] = saved_path
                output._saved_path = saved_path  # Store path in output for serializer access

                if not output.temporary:
                    try:
                        file_record = self._create_file_record(output, saved_path)
                        if file_record:
                            metadata['file_record'] = {
                                'id': file_record.id,
                                'filename': os.path.basename(file_record.file_path),
                                'file_path': file_record.file_path,
                                'file_size': file_record.file_size,
                            }
                            metadata['file_id'] = file_record.id
                        else:
                            logger.warning("Failed to create audio file record")
                            metadata['db_warning'] = "Failed to create file record"
                    except Exception as db_error:
                        logger.error(f"Error saving audio file record to database: {str(db_error)}")
                        metadata['db_error'] = str(db_error)
            elif not output.temporary:
                # A failed final save must be visible to the caller as a
                # failure, not silently reported as processed - the
                # orchestrator relies on 'processed'/'save_error' to decide
                # whether the generation actually completed.
                logger.warning("Failed to save audio")
                metadata['processed'] = False
                metadata['save_error'] = "Failed to save audio"

            return metadata

        except Exception as e:
            logger.error(f"Error handling AudioGenerationOutput: {str(e)}")
            metadata['error'] = str(e)
            metadata['processed'] = False
            return metadata

    def _save_audio_file(self, output: AudioGenerationOutput) -> Optional[str]:
        """
        Stream the audio file into storage (tmp if temporary, generations
        otherwise) using FileStore - same contract as the video/mesh
        handlers' `_save_video_file`/`_save_mesh_file`.

        Args:
            output: AudioGenerationOutput containing audio file path and metadata

        Returns:
            Relative file path if successful, None otherwise
        """
        try:
            from src.platform.filesystem.file_store import FileStore

            file_service = FileStore(self._resolve_storage_dir(), storage_driver=self._resolve_storage_driver())

            extension = Path(str(output.audio_path)).suffix.lstrip('.') or 'wav'

            if output.temporary:
                storage_type = 'tmp'
                is_temporary = True
                prefix = f"tmp_audio_{self.generation_id}" if self.generation_id else "tmp_audio"
            else:
                storage_type = 'generations'
                is_temporary = False
                self.image_counter += 1
                prefix = f"{self.image_counter}_{output.track_type}"

            full_path, file_metadata = file_service.save_file_from_path(
                generation_id=self.generation_id if not output.temporary else None,
                source_path=str(output.audio_path),
                extension=extension,
                prefix=prefix,
                storage_type=storage_type,
                is_temporary=is_temporary,
            )

            if full_path and file_metadata:
                # Same preview -> final re-read pattern as video/mesh: the
                # pipe's own temporary source may be read again later, so
                # hand it to the tracker rather than unlinking it here.
                # Registered only after a successful copy.
                temp_source_tracker.register(self.generation_id, str(output.audio_path))
                logger.debug(
                    f"Audio saved successfully to {'tmp' if output.temporary else 'generations'}: "
                    f"{file_metadata['file_path']}"
                )
                return file_metadata['file_path']  # Return relative path

            logger.error("Failed to save audio using file service")
            return None

        except Exception as e:
            logger.error(f"Error saving audio: {str(e)}")
            return None

    def _create_file_record(self, output: AudioGenerationOutput, saved_path: str) -> Optional[File]:
        """
        Create database record for the saved audio file.

        Args:
            saved_path: Relative path where the file was saved (from storage directory)
            output: AudioGenerationOutput containing metadata

        Returns:
            File record if successful, None otherwise
        """
        try:
            storage_driver = self._resolve_storage_driver()
            file_size = storage_driver.size(saved_path)

            # Prefer whatever the pipe already knows (some report it directly);
            # otherwise probe the saved file - materialized from the driver's
            # own copy (not `output.audio_path`, the pipe's temp source), so
            # this works under a non-local driver too. Never fails the record
            # over it - a bad probe just leaves duration_seconds None.
            duration_seconds = output.duration
            if duration_seconds is None:
                try:
                    with local_copy(storage_driver, saved_path, suffix=Path(saved_path).suffix) as local_path:
                        duration_seconds = media_probe.get_audio_duration_seconds(str(local_path))
                except Exception:
                    logger.warning("Failed to probe audio duration for %s", saved_path, exc_info=True)

            # No mime_type here, matching mesh/video: FileRepository.create's
            # INSERT does not persist that column for any file type (it has
            # an index but no writer), and the serve path derives MIME from
            # the suffix instead (MediaTypeResolver.get_media_type).
            file_record = File(
                file_path=saved_path,
                file_type=AUDIO_FILE_TYPE,
                user_id=self.user_id,
                file_size=file_size,
                pipe_name=getattr(output, 'pipe_name', None),
                is_final=not output.temporary,
                is_derived=bool(getattr(output, 'derived', False)),
                duration_seconds=duration_seconds,
            )

            return generation_repo.add_file(self.generation_id, file_record)

        except Exception as e:
            logger.exception(f"Error creating audio file record: {str(e)}")
            return None


def serialize_audio_output(output: AudioGenerationOutput, ctx: SerializeContext) -> Dict[str, Any]:
    """Serialize AudioGenerationOutput for workbench_update messages."""
    result = {
        'temporary': getattr(output, 'temporary', True),
        'track_type': getattr(output, 'track_type', 'mixed'),
        'duration': getattr(output, 'duration', None),
        'sample_rate': getattr(output, 'sample_rate', None),
        'channels': getattr(output, 'channels', None),
        'seed': getattr(output, 'seed', None),
        'temperature': getattr(output, 'temperature', None),
        'top_p': getattr(output, 'top_p', None),
        'guidance_scale': getattr(output, 'guidance_scale', None),
        'segment': getattr(output, 'segment', None),
        'file_type': 'audio'  # Distinguish from image/video outputs
    }

    if output.audio_path:
        try:
            # Convert audio path to API endpoint path
            audio_path = str(output.audio_path)

            # For saved audio (non-temporary), create API path from audio_path
            if not getattr(output, 'temporary', True):
                # Check if we have a _saved_path attribute (set by handler)
                if hasattr(output, '_saved_path') and output._saved_path:
                    file_path = output._saved_path
                else:
                    # Use audio_path as fallback
                    file_path = audio_path

                # Extract filename and create API path using generation_id from context
                filename = file_path.split('/')[-1] if '/' in file_path else file_path
                result['path'] = f"/api/media/generations/{ctx.generation_id}/{filename}"

            # For temporary audio (intermediate), create temp file endpoint
            else:
                # For temporary intermediate audio, use the saved path if available
                # (always set now that the handler saves temporary audio to
                # tmp/ instead of skipping it - see module docstring).
                if hasattr(output, '_saved_path') and output._saved_path:
                    saved_path = output._saved_path
                    filename = Path(saved_path).name
                else:
                    # Fallback to original audio path name
                    filename = Path(audio_path).name

                result['path'] = f"/api/media/tmp/{filename}"
                result['temp_path'] = audio_path  # Keep original path for debugging
                result['audio_name'] = filename

        except Exception as e:
            logger.error(f"Failed to serialize audio path: {str(e)}")
            result['audio_name'] = None

    return result


output_type_registry.register(OutputTypeSpec(
    output_cls=AudioGenerationOutput,
    key='audio',
    message_type='workbench_update',
    serializer=serialize_audio_output,
    handler_cls=AudioGenerationOutputHandler,
))
