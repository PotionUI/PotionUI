"""LLM module exceptions."""


class LLMException(Exception):
    """Base exception for LLM operations."""
    pass


class ConfigurationNotFoundException(LLMException):
    """Raised when an LLM configuration is not found."""
    pass


class ConfigurationExistsException(LLMException):
    """Raised when trying to create a configuration that already exists."""
    pass


class ConfigurationCreationFailedException(LLMException):
    """Raised when configuration creation fails."""
    pass


class ConfigurationUpdateFailedException(LLMException):
    """Raised when configuration update fails."""
    pass


class ConfigurationDeletionFailedException(LLMException):
    """Raised when configuration deletion fails."""
    pass


class CannotDeleteDefaultConfigException(LLMException):
    """Raised when trying to delete the default configuration."""
    pass


class VisionNotSupportedException(LLMException):
    """Raised when vision is requested but not supported by the config."""
    pass


class ImageLoadFailedException(LLMException):
    """Raised when image loading/processing fails."""
    pass


class GenerationFailedException(LLMException):
    """Raised when LLM generation fails."""
    pass


class AssignmentNotFoundException(LLMException):
    """Raised when a user-LLM assignment is not found."""
    pass


class AssignmentFailedException(LLMException):
    """Raised when assigning LLM to user fails."""
    pass
