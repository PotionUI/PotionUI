"""
Conversion utilities for common type conversions.

Provides standalone functions for type conversions used across
pipeline pipes and configuration handling.
"""


def str_to_bool(value) -> bool:
    """Convert string or boolean value to proper boolean.

    Handles YAML string values like "true", "false", "True", "False",
    as well as numeric-style strings like "1" and "0".

    Args:
        value: The value to convert. Can be bool, str, or any type
            supporting ``bool()`` conversion.

    Returns:
        Boolean representation of the value.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in ("true", "1", "yes", "on")
    return bool(value)
