"""
ULID utility functions for consistent ID generation across the application.

This module provides ULID (Universally Unique Lexicographically Sortable Identifier)
generation functions to replace UUID v4 and integer autoincrement IDs.

ULIDs are:
- 26 characters long (vs UUID's 36)
- Lexicographically sortable by creation time
- URL-safe and case-insensitive
- Globally unique
"""

from ulid import ULID


def generate_ulid() -> str:
    """
    Generate a new ULID string.
    
    Returns:
        str: A 26-character ULID string (e.g., "01ARZ3NDEKTSV4RRFFQ69G5FAV")
    """
    return str(ULID())