"""The model objects that travel between pipes.

A loader pipe produces a `Model` and hands it downstream as an `IOType.MODEL`
value; a generator pipe consumes it and calls whichever capability mixin the
model implements. `BaseModel` names the families those models belong to, which
is how a pipe states what it can drive.

This is the wiring between pipes, not the on-disk model/artifact cache - that is
`ModelLifecycle`, the service the generation feature injects into pipes as `MODELS`.
"""

from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, Dict

from src.platform.observability.logger import logger
from src.pipelines.contracts import GenerationInput
from src.pipelines.outputs import GenerationOutput


class BaseModel(Enum):
    """The model families a pipe can declare support for."""
    SDXL = "SDXL"
    MAYA = "MAYA"  # Text-to-speech generation


class Model(ABC):
    """Base model class that all models should inherit from"""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.pipe = None
        self.loaded_pipe_type = None

    @abstractmethod
    def validate_configuration(self):
        """Validate model configuration"""
        pass

    @abstractmethod
    def load(self, mode: str):
        """Load the model with specified mode"""
        pass

    def clear_cuda_cache(self, aggressive: bool = False):
        """
        Clear CUDA cache and run garbage collection.

        Args:
            aggressive: If True, performs more thorough cleanup including:
                       - Multiple GC passes
                       - CUDA synchronization
                       - Memory statistics logging
        """
        import torch
        import gc

        if torch.cuda.is_available():
            # Log memory usage before cleanup (if aggressive mode)
            if aggressive:
                mem_allocated = torch.cuda.memory_allocated() / (1024**3)
                mem_reserved = torch.cuda.memory_reserved() / (1024**3)
                logger.debug(f"[MODEL] Before cleanup - Allocated: {mem_allocated:.2f}GB, Reserved: {mem_reserved:.2f}GB")

            # Synchronize CUDA operations before cleanup
            if aggressive:
                torch.cuda.synchronize()

            # Clear CUDA cache
            torch.cuda.empty_cache()

            # Log memory usage after CUDA cleanup (if aggressive mode)
            if aggressive:
                mem_allocated = torch.cuda.memory_allocated() / (1024**3)
                mem_reserved = torch.cuda.memory_reserved() / (1024**3)
                logger.debug(f"[MODEL] After CUDA cleanup - Allocated: {mem_allocated:.2f}GB, Reserved: {mem_reserved:.2f}GB")

        # Run garbage collection (multiple passes in aggressive mode)
        if aggressive:
            # Multiple GC passes help ensure all circular references are broken
            for _ in range(3):
                gc.collect()
            logger.debug("[MODEL] Completed aggressive garbage collection (3 passes)")
        else:
            gc.collect()


class Text2ImageMixin(ABC):
    """Mixin for text-to-image capability"""

    @abstractmethod
    def txt2img(self, generation_input: GenerationInput, generation_outputs: callable) -> GenerationOutput:
        """Generate images from text prompts"""
        pass


class Image2ImageMixin(ABC):
    """Mixin for image-to-image capability"""

    @abstractmethod
    def img2img(self, generation_input: GenerationInput, generation_outputs: callable) -> GenerationOutput:
        """Transform existing images based on prompts"""
        pass


