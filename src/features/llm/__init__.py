"""
LLM module for PotionUI.

This module provides LLM functionality including:
- LLMManager: Orchestrates LLM configuration and generation operations
- LLMResponseProcessor: Processes LLM responses (thinking tag removal, image preparation)
- Domain exceptions: LLMException and specific subtypes
"""

from src.features.llm.manager import LLMManager
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
    "LLMManager",
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
