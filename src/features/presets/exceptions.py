"""
Domain exceptions for preset management.

These exceptions represent business logic errors that can occur during
preset operations. They are caught by the controller and mapped to
appropriate HTTP responses.
"""

from typing import Dict, List

from src.platform.http.errors import ConflictError, DomainError, InvalidInputError, NotFoundError


class PresetException(Exception):
    """Base exception for all preset-related errors."""
    pass


class PresetNotFoundException(PresetException):
    """Raised when a preset cannot be found by its ID."""

    def __init__(self, preset_id: str):
        self.preset_id = preset_id
        super().__init__(f"Preset '{preset_id}' not found")


class ModeNotFoundException(PresetException):
    """Raised when a mode is not found in a preset."""

    def __init__(self, preset_id: str, mode: str, available_modes: list = None):
        self.preset_id = preset_id
        self.mode = mode
        self.available_modes = available_modes or []
        modes_str = f" Available modes: {available_modes}" if available_modes else ""
        super().__init__(f"Mode '{mode}' not found in preset '{preset_id}'.{modes_str}")


class NoModesAvailableException(PresetException):
    """Raised when a preset has no modes defined."""

    def __init__(self, preset_id: str):
        self.preset_id = preset_id
        super().__init__(f"No modes defined for preset '{preset_id}'")


class PresetNotInstalledException(PresetException):
    """Raised when an operation requires an installed preset but it's not installed."""

    def __init__(self, preset_id: str):
        self.preset_id = preset_id
        super().__init__(f"Preset '{preset_id}' is not installed")


class PresetAlreadyInstalledException(PresetException):
    """Raised when trying to install an already installed preset."""

    def __init__(self, preset_id: str):
        self.preset_id = preset_id
        super().__init__(f"Preset '{preset_id}' is already installed")


class PresetAssignmentException(PresetException):
    """Raised when there's an error with preset assignment operations."""
    pass


class PresetNotAssignedException(PresetAssignmentException):
    """Raised when trying to unassign a preset that isn't assigned."""

    def __init__(self, preset_id: str, user_id: str):
        self.preset_id = preset_id
        self.user_id = user_id
        super().__init__(f"Preset '{preset_id}' is not assigned to user '{user_id}'")


class UserNotFoundException(PresetAssignmentException):
    """Raised when a user cannot be found."""

    def __init__(self, user_id: str):
        self.user_id = user_id
        super().__init__(f"User '{user_id}' not found")


class InvalidUsersException(PresetAssignmentException):
    """Raised when one or more users in a list are invalid."""

    def __init__(self, invalid_user_ids: list):
        self.invalid_user_ids = invalid_user_ids
        super().__init__(f"Users not found: {', '.join(invalid_user_ids)}")


class PermissionDeniedException(PresetException):
    """Raised when a user doesn't have permission for an operation."""

    def __init__(self, operation: str):
        self.operation = operation
        super().__init__(f"Permission denied for operation: {operation}")


class InvalidModeDataException(PresetException):
    """Raised when mode data is in an invalid format."""

    def __init__(self, preset_id: str, mode: str):
        self.preset_id = preset_id
        self.mode = mode
        super().__init__(f"Invalid mode data format for mode '{mode}' in preset '{preset_id}'")


class InvalidConfigurationException(PresetException):
    """Raised when a PUT to a preset's configuration has unknown keys or invalid values."""

    def __init__(self, preset_id: str, errors: list):
        self.preset_id = preset_id
        self.errors = errors
        super().__init__(
            f"Invalid configuration for preset '{preset_id}': {'; '.join(errors)}"
        )


class InvalidFormOverridesException(PresetException):
    """Raised when a PUT to a preset's form overrides has unknown fields or invalid values."""

    def __init__(self, preset_id: str, mode: str, errors: list):
        self.preset_id = preset_id
        self.mode = mode
        self.errors = errors
        super().__init__(
            f"Invalid form overrides for preset '{preset_id}' mode '{mode}': {'; '.join(errors)}"
        )


class PresetRowNotFoundError(NotFoundError):
    def __init__(self, preset_id: str):
        super().__init__("preset_not_found", f"Preset '{preset_id}' not found")


class PresetRowNotInstalledError(ConflictError):
    def __init__(self, preset_id: str):
        super().__init__("preset_not_installed", f"Preset '{preset_id}' is not installed")


class MirrorSourceNotInstalledError(ConflictError):
    def __init__(self, preset_id: str):
        super().__init__("source_not_installed", f"Install '{preset_id}' before creating a mirror of it")


class MirrorSourceInvalidError(InvalidInputError):
    def __init__(self, preset_id: str):
        super().__init__("mirror_source_invalid", f"'{preset_id}' is a mirror; create the mirror from its source preset")


class NotAMirrorError(InvalidInputError):
    def __init__(self, preset_id: str):
        super().__init__("not_a_mirror", f"'{preset_id}' is a preset file, so its modes cannot be chosen here")


class InvalidDisplayError(InvalidInputError):
    def __init__(self, errors: List[str]):
        super().__init__("invalid_display", "; ".join(errors), errors=errors)


class InvalidCoverError(InvalidInputError):
    def __init__(self, message: str):
        super().__init__("invalid_cover", message)


class InvalidModesError(InvalidInputError):
    def __init__(self, errors: List[str]):
        super().__init__("invalid_modes", "; ".join(errors), errors=errors)


class MirrorIdUnavailableError(DomainError):
    def __init__(self):
        super().__init__("mirror_id_unavailable", "Could not pick a free id for the mirror", 500)


class PresetHasMirrorsError(ConflictError):
    def __init__(self, preset_id: str, mirrors: List[Dict[str, str]]):
        names = ", ".join(mirror["name"] for mirror in mirrors)
        super().__init__(
            "preset_has_mirrors",
            f"Remove the mirrors of '{preset_id}' first: {names}",
            mirrors=mirrors,
        )
