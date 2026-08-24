"""Domain-specific exceptions for pipe installation."""


class PipeException(Exception):
    """Base exception for pipe operations."""
    pass


class PipeNotFoundException(PipeException):
    """Raised when no pipe is registered under the requested name."""
    pass


class PipeInstallInProgressException(PipeException):
    """Raised when an install is already running for the requested pipe."""
    pass
