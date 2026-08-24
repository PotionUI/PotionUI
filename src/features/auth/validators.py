"""
Validation policy for auth request DTOs.
"""


def validate_password_policy(value: str) -> str:
    """Shared password policy: used by registration and change-password."""
    if len(value) < 8:
        raise ValueError("Password must be at least 8 characters")
    if len(value.encode("utf-8")) > 72:
        raise ValueError("Password must be at most 72 UTF-8 bytes")
    return value


def validate_username_policy(value: str) -> str:
    """Username policy: used by registration."""
    value = value.strip()
    if not 3 <= len(value) <= 64:
        raise ValueError("Username must be between 3 and 64 characters")
    if not all(char.isalnum() or char in "._-" for char in value):
        raise ValueError("Username may only contain letters, numbers, dots, underscores, and hyphens")
    return value
