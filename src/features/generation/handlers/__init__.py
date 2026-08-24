"""
Output Handlers

Handlers process generation outputs and perform side effects like:
- Saving images/videos to disk
- Creating database records
- Generating thumbnails
- Processing artifacts

Each handler is responsible for one type of output and follows
the handler pattern with can_handle() and handle() methods.
"""

from src.features.generation.handlers.base_handler import BaseGenerationOutputHandler
from src.features.generation.handlers.image_handler import ImageGenerationOutputHandler, generate_thumbnails
from src.features.generation.handlers.video_handler import VideoGenerationOutputHandler, generate_video_thumbnails
from src.features.generation.handlers.audio_handler import AudioGenerationOutputHandler
from src.features.generation.handlers.mesh_handler import MeshGenerationOutputHandler
from src.features.generation.handlers.gallery_handler import GalleryGenerationOutputHandler
from src.features.generation.handlers.param_handler import ParamGenerationOutputHandler
from src.features.generation.handlers.artifact_handlers import (
    CompareImagesGenerationOutputHandler,
    ProgressGenerationOutputHandler,
    TimerGenerationOutputHandler,
    ModelsGenerationOutputHandler,
    SeedGenerationOutputHandler,
    RenderedPromptGenerationOutputHandler,
    WarmStartGenerationOutputHandler,
    ComfyUIWorkflowGenerationOutputHandler,
    DiffTextGenerationOutputHandler,
)
from src.features.generation.handlers.error_handler import serialize_error_output

__all__ = [
    'BaseGenerationOutputHandler',
    'ImageGenerationOutputHandler',
    'generate_thumbnails',
    'VideoGenerationOutputHandler',
    'generate_video_thumbnails',
    'AudioGenerationOutputHandler',
    'MeshGenerationOutputHandler',
    'GalleryGenerationOutputHandler',
    'ParamGenerationOutputHandler',
    'CompareImagesGenerationOutputHandler',
    'ProgressGenerationOutputHandler',
    'TimerGenerationOutputHandler',
    'ModelsGenerationOutputHandler',
    'SeedGenerationOutputHandler',
    'RenderedPromptGenerationOutputHandler',
    'WarmStartGenerationOutputHandler',
    'ComfyUIWorkflowGenerationOutputHandler',
    'DiffTextGenerationOutputHandler',
    'serialize_error_output',
]
