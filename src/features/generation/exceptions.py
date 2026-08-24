"""
Generation domain exceptions.

These exceptions represent business logic errors that can occur
during generation history operations. They allow controllers to map
domain errors to appropriate HTTP responses.
"""


class GenerationException(Exception):
    """Base exception for generation operations."""
    pass


class GenerationNotFoundException(GenerationException):
    """Generation not found in the database."""
    pass


class GenerationDeleteFailedException(GenerationException):
    """Failed to delete generation."""
    pass


class UploadFailedException(GenerationException):
    """Failed to upload files."""
    pass


class InvalidTagException(GenerationException):
    """Invalid tag specified."""
    pass


class InvalidDateFilterException(GenerationException):
    """Invalid date filter format or range."""
    pass


class InvalidGenerationSourceException(GenerationException):
    """A `<field>__origin` submission references a source generation that
    does not exist or does not belong to the submitting user.

    Deliberately not `ValueError` - the controller maps this to a 404, the
    same existence-concealing status `ModelNotFoundException` /
    `ModelAccessDeniedException` get, never a 403 that would confirm the
    referenced id exists."""
    pass


class GenerationBundleImportError(GenerationException):
    """An uploaded generation export bundle is malformed, oversized, or not
    a PotionUI generation envelope. The controller maps this to a 400."""
    pass
