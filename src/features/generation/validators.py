"""
Validation policy for generation request DTOs.
"""


def validate_rating_policy(value: int) -> int:
    if value < 0 or value > 5:
        raise ValueError('rating must be between 0 and 5')
    return value
