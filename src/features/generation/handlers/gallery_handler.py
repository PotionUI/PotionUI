"""
Gallery generation output handler for processing collections of images, videos, and audio.

This module provides a handler for GalleryGenerationOutput, which processes
multiple images, videos, and audio files in a single generation output. It delegates to
ImageGenerationOutputHandler, VideoGenerationOutputHandler, and AudioGenerationOutputHandler
for individual items while maintaining consistent counter state across all items.
"""

import logging
from typing import Dict, Any, Optional

from src.pipelines.outputs import GenerationOutput, GalleryGenerationOutput
from src.features.generation.handlers.base_handler import BaseGenerationOutputHandler
from src.features.generation.handlers.image_handler import ImageGenerationOutputHandler
from src.features.generation.handlers.video_handler import VideoGenerationOutputHandler
from src.features.generation.handlers.audio_handler import AudioGenerationOutputHandler
from src.features.generation.handlers.mesh_handler import (
    MeshGenerationOutputHandler,
    mesh_api_path,
    mesh_format_of,
)
from src.features.generation.output_types import OutputTypeSpec, SerializeContext, output_type_registry
from src.features.generation.media_utils import create_base64_image
from src.platform.settings.settings import Settings

logger = logging.getLogger(__name__)


class GalleryGenerationOutputHandler(BaseGenerationOutputHandler):
    """Handler for GalleryGenerationOutput - processes multiple images, videos, audio files and meshes."""

    def can_handle(self, output: GenerationOutput) -> bool:
        """Check if this handler can process GalleryGenerationOutput."""
        return isinstance(output, GalleryGenerationOutput)

    def handle(self, output: GalleryGenerationOutput) -> Dict[str, Any]:
        """
        Process GalleryGenerationOutput - handle each media item in the gallery.

        Args:
            output: GalleryGenerationOutput to process

        Returns:
            Dictionary with processing metadata for all media items
        """
        self.seed_counter_from_persisted_files()
        metadata = {
            'handler': 'GalleryGenerationOutputHandler',
            'processed': True,
            'image_count': len(output.images) if output.images else 0,
            'video_count': len(output.videos) if output.videos else 0,
            'audio_count': len(output.audios) if output.audios else 0,
            'mesh_count': len(output.meshes) if output.meshes else 0,
            'processed_images': [],
            'processed_videos': [],
            'processed_audios': [],
            'processed_meshes': []
        }

        try:
            # Handle images
            if output.images:
                # Create an image handler for processing individual images
                image_handler = ImageGenerationOutputHandler(self.generation_id, self.user_id, self.settings, self.storage_driver)

                for idx, image_output in enumerate(output.images):
                    # Use the same counter across all images in the gallery
                    image_handler.image_counter = self.image_counter
                    image_handler._counter_seeded = True

                    # Process the individual image
                    image_metadata = image_handler.handle(image_output)
                    image_metadata['gallery_index'] = idx
                    metadata['processed_images'].append(image_metadata)

                    # Update our counter
                    self.image_counter = image_handler.image_counter

            # Handle videos
            if output.videos:
                # Create a video handler for processing individual videos
                video_handler = VideoGenerationOutputHandler(self.generation_id, self.user_id, self.settings, self.storage_driver)

                for idx, video_output in enumerate(output.videos):
                    # Use the same counter for videos
                    video_handler.image_counter = self.image_counter

                    # Process the individual video
                    video_metadata = video_handler.handle(video_output)
                    video_metadata['gallery_index'] = idx
                    metadata['processed_videos'].append(video_metadata)

                    # Update our counter
                    self.image_counter = video_handler.image_counter

            # Handle audio files
            if output.audios:
                # Create an audio handler for processing individual audio files
                audio_handler = AudioGenerationOutputHandler(self.generation_id, self.user_id, self.settings, self.storage_driver)

                for idx, audio_output in enumerate(output.audios):
                    # Use the same counter for audio files
                    audio_handler.image_counter = self.image_counter

                    # Process the individual audio file
                    audio_metadata = audio_handler.handle(audio_output)
                    audio_metadata['gallery_index'] = idx
                    metadata['processed_audios'].append(audio_metadata)

                    # Update our counter
                    self.image_counter = audio_handler.image_counter

            # Handle meshes
            if output.meshes:
                mesh_handler = MeshGenerationOutputHandler(self.generation_id, self.user_id, self.settings, self.storage_driver)

                for idx, mesh_output in enumerate(output.meshes):
                    # Use the same counter for meshes
                    mesh_handler.image_counter = self.image_counter

                    mesh_metadata = mesh_handler.handle(mesh_output)
                    mesh_metadata['gallery_index'] = idx
                    metadata['processed_meshes'].append(mesh_metadata)

                    # Update our counter
                    self.image_counter = mesh_handler.image_counter

            return metadata

        except Exception as e:
            logger.error(f"Error handling GalleryGenerationOutput: {str(e)}")
            metadata['error'] = str(e)
            metadata['processed'] = False
            return metadata


def serialize_gallery_output(output: GalleryGenerationOutput, ctx: SerializeContext) -> Dict[str, Any]:
    """Serialize GalleryGenerationOutput for gallery_update messages."""
    result = {
        'images': [],
        'image_urls_list': [],
        'videos': [],
        'video_urls_list': [],
        'audios': [],
        'audio_urls_list': [],
        'meshes': [],
        'mesh_urls_list': []
    }

    if output.images:
        try:
            # Process each image individually
            images_list = []

            for img_output in output.images:
                # Get the base64 image data directly
                base64_image = create_base64_image(img_output.image, max_dimension=768)

                # Create a simplified result with just the base64 string
                # This makes it compatible with the frontend's expectations
                images_list.append(base64_image)

                # If the image has a path, add it to a separate list for the frontend
                if not getattr(img_output, 'temporary', True) and hasattr(img_output, '_saved_path') and img_output._saved_path:
                    # Convert file path to API endpoint path
                    file_path = img_output._saved_path
                    api_path = None

                    # Handle old path format
                    if file_path.startswith('outputs/images/'):
                        path_parts = file_path.replace('outputs/images/', '').split('/', 1)
                        if len(path_parts) == 2:
                            generation_id, filename = path_parts
                            api_path = f"/api/media/generations/{generation_id}/{filename}"

                    # Handle new path format
                    elif file_path.startswith('outputs/'):
                        import re
                        match = re.match(r'outputs/(\d{4}-\d{2}-\d{2})/([^/]+)/([^/]+)', file_path)
                        if match:
                            date, generation_id, filename = match.groups()
                            api_path = f"/api/media/generations/{generation_id}/{filename}"

                    # Add to the URLs list if we have a valid path
                    if api_path:
                        image_data = {
                            'original': api_path,
                            'derived': bool(getattr(img_output, 'derived', False)),
                            'seed': getattr(img_output, 'seed', None),
                            'resolution': getattr(img_output, 'resolution', None),
                            'sampler': getattr(img_output, 'sampler', None),
                            'clip_skip': getattr(img_output, 'clip_skip', None),
                            'cfg': getattr(img_output, 'cfg', None),
                            'denoise': getattr(img_output, 'denoise', None),
                            'step': getattr(img_output, 'step', None)
                        }
                        result['image_urls_list'].append(image_data)

            # Set the images list with just the base64 strings
            result['images'] = images_list

        except Exception as e:
            logger.error(f"Failed to serialize gallery images: {str(e)}")
            result['images'] = []

    # Process videos if present
    if hasattr(output, 'videos') and output.videos:
        try:
            videos_list = []

            for video_output in output.videos:
                # For videos, we can't create a base64 preview like images
                # Instead, we'll provide metadata and the API path when available
                video_data = {
                    'file_type': 'video',
                    'derived': bool(getattr(video_output, 'derived', False)),
                    'resolution': getattr(video_output, 'resolution', None),
                    'duration': getattr(video_output, 'duration', None),
                    'fps': getattr(video_output, 'fps', None),
                    'seed': getattr(video_output, 'seed', None),
                    'temporary': getattr(video_output, 'temporary', True)
                }

                # If video has been saved, add the API path
                if not getattr(video_output, 'temporary', True):
                    # Check for _saved_path or use video_path as fallback
                    if hasattr(video_output, '_saved_path') and video_output._saved_path:
                        file_path = video_output._saved_path
                    else:
                        file_path = str(video_output.video_path)

                    # Extract filename from path and use generation_id from context
                    # The serve endpoint will handle finding the actual file location
                    filename = file_path.split('/')[-1] if '/' in file_path else file_path
                    api_path = f"/api/media/generations/{ctx.generation_id}/{filename}"
                    logger.debug(f"Created video API path: {api_path} (file_path: {file_path})")

                    if api_path:
                        video_data['path'] = api_path
                        video_data.update({
                            'sampler': getattr(video_output, 'sampler', None),
                            'clip_skip': getattr(video_output, 'clip_skip', None),
                            'cfg': getattr(video_output, 'cfg', None),
                            'denoise': getattr(video_output, 'denoise', None),
                            'step': getattr(video_output, 'step', None),
                            'motion_strength': getattr(video_output, 'motion_strength', None)
                        })
                        result['video_urls_list'].append(video_data)

                videos_list.append(video_data)
                logger.debug(f"Added video to list: {video_data}")

            result['videos'] = videos_list
            logger.debug(f"Final gallery videos result: {len(videos_list)} videos")

        except Exception as e:
            logger.error(f"Failed to serialize gallery videos: {str(e)}")
            result['videos'] = []

    # Process audio files if present
    if hasattr(output, 'audios') and output.audios:
        try:
            audios_list = []

            for audio_output in output.audios:
                # For audio, we provide metadata and the API path when available
                audio_data = {
                    'file_type': 'audio',
                    'track_type': getattr(audio_output, 'track_type', 'mixed'),
                    'duration': getattr(audio_output, 'duration', None),
                    'sample_rate': getattr(audio_output, 'sample_rate', None),
                    'channels': getattr(audio_output, 'channels', None),
                    'seed': getattr(audio_output, 'seed', None),
                    'temporary': getattr(audio_output, 'temporary', True)
                }

                # If audio has been saved, add the API path
                if not getattr(audio_output, 'temporary', True):
                    # Check for _saved_path or use audio_path as fallback
                    if hasattr(audio_output, '_saved_path') and audio_output._saved_path:
                        file_path = audio_output._saved_path
                    else:
                        file_path = str(audio_output.audio_path)

                    # Extract filename from path and use generation_id from context
                    # The serve endpoint will handle finding the actual file location
                    filename = file_path.split('/')[-1] if '/' in file_path else file_path
                    api_path = f"/api/media/generations/{ctx.generation_id}/{filename}"
                    logger.debug(f"Created audio API path: {api_path} (file_path: {file_path})")

                    if api_path:
                        audio_data['path'] = api_path
                        audio_data.update({
                            'temperature': getattr(audio_output, 'temperature', None),
                            'top_p': getattr(audio_output, 'top_p', None),
                            'guidance_scale': getattr(audio_output, 'guidance_scale', None),
                            'segment': getattr(audio_output, 'segment', None)
                        })
                        result['audio_urls_list'].append(audio_data)

                audios_list.append(audio_data)
                logger.debug(f"Added audio to list: {audio_data}")

            result['audios'] = audios_list
            logger.debug(f"Final gallery audios result: {len(audios_list)} audios")

        except Exception as e:
            logger.error(f"Failed to serialize gallery audios: {str(e)}")
            result['audios'] = []

    # Process meshes if present
    if getattr(output, 'meshes', None):
        try:
            meshes_list = []

            for mesh_output in output.meshes:
                mesh_data = {
                    'file_type': 'mesh',
                    'mesh_format': mesh_format_of(mesh_output),
                    'derived': bool(getattr(mesh_output, 'derived', False)),
                    'seed': getattr(mesh_output, 'seed', None),
                    'vertex_count': getattr(mesh_output, 'vertex_count', None),
                    'face_count': getattr(mesh_output, 'face_count', None),
                    'temporary': getattr(mesh_output, 'temporary', True)
                }

                api_path = mesh_api_path(mesh_output, ctx.generation_id)
                if api_path and not getattr(mesh_output, 'temporary', True):
                    mesh_data['path'] = api_path
                    result['mesh_urls_list'].append(mesh_data)

                meshes_list.append(mesh_data)

            result['meshes'] = meshes_list
            logger.debug(f"Final gallery meshes result: {len(meshes_list)} meshes")

        except Exception as e:
            logger.error(f"Failed to serialize gallery meshes: {str(e)}")
            result['meshes'] = []

    return result


output_type_registry.register(OutputTypeSpec(
    output_cls=GalleryGenerationOutput,
    key='gallery',
    message_type='gallery_update',
    serializer=serialize_gallery_output,
    handler_cls=GalleryGenerationOutputHandler,
))
