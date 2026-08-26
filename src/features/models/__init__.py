"""
Model index module for PotionUI.

This module provides model indexing functionality including:
- ModelIndexCollaborators: bundles the focused role classes that do the work
- Domain exceptions: ModelIndexException and specific subtypes
"""

from src.features.models.collaborators import ModelIndexCollaborators, build_model_index_collaborators
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
    # Collaborators bundle
    "ModelIndexCollaborators",
    "build_model_index_collaborators",

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
