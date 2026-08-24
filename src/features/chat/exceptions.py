"""
Chat domain exceptions.

These exceptions represent business logic errors that can occur
during chat operations. They allow controllers to map domain errors
to appropriate HTTP responses.
"""


class ChatException(Exception):
    """Base exception for chat operations."""
    pass


class SessionNotFoundException(ChatException):
    """Session not found in the database."""
    pass


class AccessDeniedException(ChatException):
    """User doesn't have access to this resource."""
    pass


class SessionClosedException(ChatException):
    """Session is closed, cannot perform operation."""
    pass


class InvalidLLMConfigException(ChatException):
    """LLM configuration is missing or invalid."""
    pass


class MessageCreationFailedException(ChatException):
    """Failed to create message in the database."""
    pass


class SessionCreationFailedException(ChatException):
    """Failed to create session in the database."""
    pass


class PreChatActionError(ChatException):
    """A blocking pre-chat action failed."""
    pass


class UnknownChatModeException(ChatException):
    """The requested chat mode is not registered."""
    pass
