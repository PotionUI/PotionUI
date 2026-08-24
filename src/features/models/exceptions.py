"""
Model index module exceptions.

Domain-specific exceptions for model index operations.
"""


class ModelIndexException(Exception):
    """Base exception for model index operations."""
    pass


class ModelNotFoundException(ModelIndexException):
    """Raised when a model is not found."""
    pass


class ModelAccessDeniedException(ModelIndexException):
    """Raised when access to a model is denied."""
    pass


class ModelIndexingException(ModelIndexException):
    """Raised when model indexing fails."""
    pass


class ProviderFetchException(ModelIndexException):
    """Raised when fetching provider information fails."""
    pass


class InvalidModelTypeException(ModelIndexException):
    """Raised when an invalid model type is provided."""
    pass


class InvalidTagException(ModelIndexException):
    """Raised when an invalid tag is provided."""
    pass


class InvalidModelMetadataException(ModelIndexException):
    """Raised when a model_metadata update names an undeclared field, or a
    value of the wrong type / out of the declared range."""
    pass


class ModelDownloadException(ModelIndexException):
    """Raised when model download fails."""
    pass


class ModelAssignmentException(ModelIndexException):
    """Raised when model assignment operation fails."""
    pass


class ModelAlreadyAssignedException(ModelAssignmentException):
    """
    Raised when the model is already assigned to the user.

    A subclass, so existing `except ModelAssignmentException` callers (and the
    REST endpoint) keep treating it as a failure. Callers that want assignment to
    be idempotent - notably `action.assign_model`, since a file watcher legitimately
    fires more than once for the same file - catch this specifically and carry on.
    `assignment` is the pre-existing row.
    """

    def __init__(self, message: str, assignment=None):
        super().__init__(message)
        self.assignment = assignment
