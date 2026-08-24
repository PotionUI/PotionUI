"""
Dimension Utilities - Centralized divisible-by-N alignment logic.

Provides functions for rounding, flooring, aligning, and validating
image dimensions to multiples required by diffusion models (typically 8).
"""

from typing import Literal, Tuple


def round_to_multiple(value: int, multiple: int = 8) -> int:
    """
    Round value UP to the nearest multiple (ceiling).

    Args:
        value: Value to round
        multiple: Target multiple (default 8)

    Returns:
        Smallest integer >= value that is a multiple of ``multiple``
    """
    return ((value + multiple - 1) // multiple) * multiple


def floor_to_multiple(value: int, multiple: int = 8) -> int:
    """
    Round value DOWN to the nearest multiple (floor).

    Args:
        value: Value to round
        multiple: Target multiple (default 8)

    Returns:
        Largest integer <= value that is a multiple of ``multiple``
    """
    return (value // multiple) * multiple


def align_dimensions(
    width: int,
    height: int,
    multiple: int = 8,
    mode: Literal["floor", "ceil"] = "floor",
) -> Tuple[int, int]:
    """
    Align both width and height to the given multiple.

    Args:
        width: Image width in pixels
        height: Image height in pixels
        multiple: Target multiple (default 8)
        mode: "floor" rounds down, "ceil" rounds up

    Returns:
        Tuple of (aligned_width, aligned_height)
    """
    fn = floor_to_multiple if mode == "floor" else round_to_multiple
    return fn(width, multiple), fn(height, multiple)


def validate_resolution(width: int, height: int, multiple: int = 8) -> None:
    """
    Validate that width and height are aligned to the given multiple.

    Args:
        width: Image width in pixels
        height: Image height in pixels
        multiple: Required multiple (default 8)

    Raises:
        ValueError: If width or height is not a multiple of ``multiple``
    """
    if width % multiple != 0 or height % multiple != 0:
        raise ValueError(
            f"Resolution must be divisible by {multiple}, got {width}x{height}. "
            f"Use align_dimensions() to fix."
        )
