"""Domain-specific exceptions for download operations."""


class DownloadException(Exception):
    """Base exception for download operations."""
    pass


class DownloadNotFoundException(DownloadException):
    """Raised when a download is not found."""
    pass


class DownloadQueueException(DownloadException):
    """Raised when queueing a download fails."""
    pass


class DownloadOperationException(DownloadException):
    """Raised when a download operation (pause, resume, cancel) fails."""
    pass


class DownloadAuthenticationException(DownloadException):
    """Raised when download authentication fails."""
    pass


class InvalidStatusException(DownloadException):
    """Raised when an invalid status is provided."""
    pass


class InvalidTypeException(DownloadException):
    """Raised when an invalid download type is provided."""
    pass
