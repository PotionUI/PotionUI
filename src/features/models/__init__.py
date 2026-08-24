"""
Model index module for PotionUI.

This module provides model indexing functionality including:
- ModelIndexManager: Orchestrates model index operations with business logic
- Domain exceptions: ModelIndexException and specific subtypes
"""

from src.features.models.manager import ModelIndexManager
from src.features.models.exceptions import (
    ModelIndexException,
    ModelNotFoundException,
    ModelAccessDeniedException,
    ModelIndexingException,
    ProviderFetchException,
    InvalidModelTypeException,
    InvalidTagException,
    InvalidModelMetadataException,
    ModelDownloadException,
    ModelAssignmentException,
)

__all__ = [
    # Main classes
    "ModelIndexManager",

    # Exceptions
    "ModelIndexException",
    "ModelNotFoundException",
    "ModelAccessDeniedException",
    "ModelIndexingException",
    "ProviderFetchException",
    "InvalidModelTypeException",
    "InvalidTagException",
    "InvalidModelMetadataException",
    "ModelDownloadException",
    "ModelAssignmentException",
]
