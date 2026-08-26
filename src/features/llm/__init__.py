"""
LLM module for PotionUI.

This module provides LLM functionality including:
- LLMResponseProcessor: Processes LLM responses (thinking tag removal, image preparation)
- Domain exceptions: LLMException and specific subtypes

Configuration/generation/assignment operations live in
`src.features.llm.operations` (see that package's docstring); `LLMController`
(`src.features.llm.routes`) is the sole caller.
"""

from src.features.llm.response_processor import LLMResponseProcessor
from src.features.llm.exceptions import (
    LLMException,
    ConfigurationNotFoundException,
    ConfigurationExistsException,
    ConfigurationCreationFailedException,
    ConfigurationUpdateFailedException,
    ConfigurationDeletionFailedException,
    CannotDeleteDefaultConfigException,
    VisionNotSupportedException,
    ImageLoadFailedException,
    GenerationFailedException,
    AssignmentNotFoundException,
    AssignmentFailedException,
)

__all__ = [
    # Main classes
    "LLMResponseProcessor",

    # Exceptions
    "LLMException",
    "ConfigurationNotFoundException",
    "ConfigurationExistsException",
    "ConfigurationCreationFailedException",
    "ConfigurationUpdateFailedException",
    "ConfigurationDeletionFailedException",
    "CannotDeleteDefaultConfigException",
    "VisionNotSupportedException",
    "ImageLoadFailedException",
    "GenerationFailedException",
    "AssignmentNotFoundException",
    "AssignmentFailedException",
]
