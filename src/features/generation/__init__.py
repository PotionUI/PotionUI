"""
Generation module.

This module provides generation management functionality including:
- GenerationEngine: Core generation orchestration
- GenerationHistoryFacade: History/CRUD operations for generations
- GenerationOrchestrator: Lifecycle management for generations
- PipelineBuilder: preset + form data -> the processed pipe list, shared by
  generation execution and the preset pipeline preview
- OutputProcessor: Handler-based output processing
- Domain exceptions for generation operations

The output types themselves are the pipe contract and live in
src.pipelines.outputs; what this module owns is the machinery that consumes
them - the handlers, the serializers and the output-type registry.
"""

# Import exceptions (no external dependencies, safe to import)
from src.features.generation.exceptions import (
    GenerationException,
    GenerationNotFoundException,
    GenerationDeleteFailedException,
    UploadFailedException,
    InvalidTagException,
    InvalidDateFilterException,
    InvalidGenerationSourceException,
    GenerationBundleImportError,
)

# Import history manager (has dependencies but no circular import risk)
from src.features.generation.history_facade import GenerationHistoryFacade

# Access-control policy (no external dependencies beyond the user model)
from src.features.generation.policy import GenerationPolicy, GenerationAccessDenied

# Note: PipelineBuilder, OutputProcessor, GenerationEngine and GenerationOrchestrator
# are not imported here to avoid circular imports.
# Import them directly:
# - from src.features.generation.pipeline_builder import PipelineBuilder
# - from src.features.generation.output_processor import OutputProcessor
# - from src.features.generation.engine import GenerationEngine
# - from src.features.generation.orchestrator import GenerationOrchestrator

__all__ = [
    "GenerationHistoryFacade",
    "GenerationPolicy",
    "GenerationAccessDenied",
    "GenerationException",
    "GenerationNotFoundException",
    "GenerationDeleteFailedException",
    "UploadFailedException",
    "InvalidTagException",
    "InvalidDateFilterException",
    "InvalidGenerationSourceException",
    "GenerationBundleImportError",
]
