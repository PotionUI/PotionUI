"""
Image generation output handler for the application layer.

This module provides the handler for processing ImageGenerationOutput instances,
including saving images to the filesystem, generating thumbnails in multiple sizes,
and creating database records with proper metadata.

The handler integrates with:
- FileStore for consistent file storage and naming
- Settings management for configurable storage directories
- Generation repository for database persistence
- PIL for image processing and thumbnail generation

Thumbnail Generation:
    - Small: 480x480px
    - Medium: 768x768px
    - Large: 1024x1024px

All thumbnails are saved as WebP format with optimized compression for better
performance and reduced storage requirements.
"""

import io
import logging
import os
from typing import Dict, Any, Optional
from PIL import Image

from src.pipelines.outputs import ImageGenerationOutput
from src.features.generation.handlers.base_handler import BaseGenerationOutputHandler
from src.features.generation.output_types import OutputTypeSpec, SerializeContext, output_type_registry
from src.features.generation.media_utils import create_base64_image
from src.features.generation.records import File
from src.features.generation.repository import generation_repo
from src.platform.filesystem.storage_driver import FileStorageDriver
from src.platform.util.ids import generate_ulid
from src.platform.settings.settings import SettingsManager

logger = logging.getLogger(__name__)


def generate_thumbnails(image: Image.Image, storage_driver: FileStorageDriver, base_key: str, counter: int) -> Dict[str, str]:
    """
    Generate thumbnails of different sizes for an image, written through
    `storage_driver` under `{base_key}/thumbnails/...` - `base_key` is the
    key-space directory the main output was just saved into (the parent of
    its `files.file_path`).

    Args:
        image: PIL Image to create thumbnails from
        storage_driver: Where the thumbnail bytes actually live
        base_key: The saved output's parent key, e.g. `generations/<date>/<id>`
        counter: Image counter for filename generation

    Returns:
        Dictionary with thumbnail paths (relative to `base_key`, matching
        `files.thumbnail_small/medium/large`): {'small': path, ...}
    """
    thumbnail_sizes = {
        'small': (480, 480),
        'medium': (768, 768),
        'large': (1024, 1024)
    }

    thumbnail_paths = {}

    for size_name, (width, height) in thumbnail_sizes.items():
        try:
            # Create a copy of the image to avoid modifying the original
            thumb_image = image.copy()

            # Use thumbnail() method to maintain aspect ratio
            thumb_image.thumbnail((width, height), Image.Resampling.LANCZOS)

            # Convert to RGB if needed for WebP compatibility
            if thumb_image.mode == 'RGBA':
                # Create white background for images with transparency
                background = Image.new('RGB', thumb_image.size, (255, 255, 255))
                background.paste(thumb_image, mask=thumb_image.split()[-1])  # Use alpha channel as mask
                thumb_image = background

            # Save as WebP for better compression, entirely in memory
            filename = f"{counter}_{size_name}.webp"
            relative_path = f"thumbnails/{filename}"
            buf = io.BytesIO()
            thumb_image.save(buf, format='WebP', quality=85, method=6)
            storage_driver.put_bytes(f"{base_key}/{relative_path}", buf.getvalue())

            thumbnail_paths[size_name] = relative_path

            logger.debug(f"Created {size_name} thumbnail: {base_key}/{relative_path}")

        except Exception as e:
            logger.error(f"Failed to create {size_name} thumbnail: {str(e)}")
            # Continue with other sizes even if one fails
            continue

    return thumbnail_paths


class ImageGenerationOutputHandler(BaseGenerationOutputHandler):
    """Handler for ImageGenerationOutput - handles image saving and processing."""

    def can_handle(self, output) -> bool:
        """Check if this handler can process ImageGenerationOutput."""
        return isinstance(output, ImageGenerationOutput)

    def handle(self, output: ImageGenerationOutput) -> Dict[str, Any]:
        """
        Process ImageGenerationOutput - save image if not temporary.

        Args:
            output: ImageGenerationOutput to process

        Returns:
            Dictionary with processing metadata including saved_path if applicable
        """
        metadata = {
            'handler': 'ImageGenerationOutputHandler',
            'processed': True,
            'temporary': output.temporary
        }

        try:
            # Only save if not temporary
            if not output.temporary and output.image:
                if not self._counter_seeded:
                    self.seed_counter_from_persisted_files()
                save_result = self._save_image(output.image)
                if save_result:
                    saved_path, thumbnail_paths = save_result
                    metadata['saved_path'] = saved_path
                    metadata['thumbnail_paths'] = thumbnail_paths
                    # Set the _saved_path attribute on the output for backward compatibility
                    output._saved_path = saved_path
                    logger.debug(f"Saved image to: {saved_path}")

                    # Save file record to database
                    try:
                        file_record = self._save_file_record(saved_path, output, thumbnail_paths)
                        if file_record:
                            metadata['file_id'] = file_record.id
                            logger.debug(f"Created file record with ID: {file_record.id}")
                        else:
                            logger.warning("Failed to create file record")
                            metadata['db_warning'] = "Failed to create file record"
                    except Exception as db_error:
                        logger.error(f"Error saving file record to database: {str(db_error)}")
                        metadata['db_error'] = str(db_error)
                else:
                    logger.warning("Failed to save image")
                    metadata['save_error'] = "Failed to save image"

            return metadata

        except Exception as e:
            logger.error(f"Error handling ImageGenerationOutput: {str(e)}")
            metadata['error'] = str(e)
            metadata['processed'] = False
            return metadata

    def _save_image(self, image: Image.Image) -> Optional[tuple]:
        """
        Save image (and its thumbnails) through `self.storage_driver`.

        Args:
            image: PIL Image to save

        Returns:
            Tuple of (relative_file_path, thumbnail_paths) if successful, None otherwise
        """
        try:
            storage_driver = self._resolve_storage_driver()

            # Import FileStore locally to avoid circular import
            from src.platform.filesystem.file_store import FileStore

            file_service = FileStore(self._resolve_storage_dir(), storage_driver=storage_driver)

            # Convert PIL image to bytes
            image_bytes = io.BytesIO()
            image.save(image_bytes, format='PNG')
            image_data = image_bytes.getvalue()

            full_path, file_metadata = file_service.save_file(
                generation_id=self.generation_id,
                file_data=image_data,
                extension='png',
                prefix=str(self.image_counter),
                storage_type='generations',
                is_temporary=False
            )

            if not full_path or not file_metadata:
                logger.error("Failed to save image using file service")
                return None

            # The saved output's parent key, e.g. "generations/<date>/<id>" -
            # thumbnails live alongside it under a "thumbnails/" child key.
            base_key = file_metadata['file_path'].rsplit('/', 1)[0]

            thumbnail_paths = generate_thumbnails(image, storage_driver, base_key, self.image_counter)
            logger.debug(f"Generated thumbnails: {thumbnail_paths}")

            # Increment counter for next image
            self.image_counter += 1

            # Return the relative path and thumbnail paths
            return file_metadata['file_path'], thumbnail_paths

        except Exception as e:
            logger.error(f"Error saving image: {str(e)}")
            return None

    def _save_file_record(self, file_path: str, output: ImageGenerationOutput, thumbnail_paths: Dict[str, str] = None) -> Optional[File]:
        """
        Save file record to database and associate with generation.

        Args:
            file_path: Relative path where the file was saved (from storage directory)
            output: ImageGenerationOutput containing metadata
            thumbnail_paths: Dictionary with thumbnail paths

        Returns:
            File record if successful, None otherwise
        """
        try:
            file_size = self._resolve_storage_driver().size(file_path)

            # Get image dimensions
            width, height = None, None
            if output.image:
                width, height = output.image.size

            # Create file record with new structure
            file_record = File(
                id=generate_ulid(),
                file_path=file_path,  # Store relative path
                file_type='IMAGE',  # Use uppercase 'IMAGE' for new convention
                mime_type='image/png',  # PNG is the default for saved images
                user_id=self.user_id,
                file_size=file_size,
                pipe_name=getattr(output, 'pipe_name', None),
                is_final=not output.temporary,
                is_derived=bool(getattr(output, 'derived', False)),
                thumbnail_small=thumbnail_paths.get('small') if thumbnail_paths else None,
                thumbnail_medium=thumbnail_paths.get('medium') if thumbnail_paths else None,
                thumbnail_large=thumbnail_paths.get('large') if thumbnail_paths else None,
                width=width,
                height=height
            )

            # Save to database and associate with generation
            created_file = generation_repo.add_file(self.generation_id, file_record)

            return created_file

        except Exception as e:
            logger.error(f"Error creating file record: {str(e)}")
            return None


def serialize_image_output(output: ImageGenerationOutput, ctx: SerializeContext) -> Dict[str, Any]:
    """Serialize ImageGenerationOutput for workbench_update or pipe_artifact messages."""
    # Check if this is an artifact
    is_artifact = getattr(output, 'isArtifact', False)

    if is_artifact:
        # Artifact format - goes to pipe_artifact message
        result = {
            'artifact_type': 'image',
            'artifact_data': {
                'label': getattr(output, 'label', 'Image'),
            }
        }

        if output.image:
            try:
                result['artifact_data']['image'] = create_base64_image(output.image, max_dimension=768)
            except Exception as e:
                logger.error(f"Failed to serialize artifact image: {str(e)}")
                result['artifact_data']['image'] = None

        return result

    # Regular workbench format
    result = {
        'temporary': getattr(output, 'temporary', True),
        'seed': getattr(output, 'seed', None),
        'resolution': getattr(output, 'resolution', None),
        'sampler': getattr(output, 'sampler', None),
        'clip_skip': getattr(output, 'clip_skip', None),
        'cfg': getattr(output, 'cfg', None),
        'denoise': getattr(output, 'denoise', None),
        'step': getattr(output, 'step', None)
    }

    if output.image:
        try:
            # Always provide base64 representation of the image (required for immediate display)
            result['image'] = create_base64_image(output.image, max_dimension=768)

            # If not temporary and _saved_path is provided, add the path endpoint for original image
            if not getattr(output, 'temporary', True) and hasattr(output, '_saved_path') and output._saved_path:
                # Convert file path to API endpoint path
                file_path = output._saved_path

                # Handle old path format: outputs/images/gen_123/preset_20250710_123456_000.png
                if file_path.startswith('outputs/images/'):
                    # Extract generation_id and filename from path
                    path_parts = file_path.replace('outputs/images/', '').split('/', 1)
                    if len(path_parts) == 2:
                        generation_id, filename = path_parts
                        result['path'] = f"/api/media/generations/{generation_id}/{filename}"

                # Handle new path format: outputs/yyyy-mm-dd/gen_123/0.png
                elif file_path.startswith('outputs/'):
                    import re
                    # Use regex to match the date-based path format
                    match = re.match(r'outputs/(\d{4}-\d{2}-\d{2})/([^/]+)/([^/]+)', file_path)
                    if match:
                        # Extract date, generation_id, and filename
                        date, generation_id, filename = match.groups()
                        # We only need generation_id and filename for the API path
                        result['path'] = f"/api/media/generations/{generation_id}/{filename}"

        except Exception as e:
            logger.error(f"Failed to serialize image: {str(e)}")
            result['image'] = None
    return result


output_type_registry.register(OutputTypeSpec(
    output_cls=ImageGenerationOutput,
    key='image',
    message_type=lambda output: "pipe_artifact" if getattr(output, "isArtifact", False) else "workbench_update",
    serializer=serialize_image_output,
    handler_cls=ImageGenerationOutputHandler,
))
