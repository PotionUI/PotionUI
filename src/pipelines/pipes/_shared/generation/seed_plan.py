"""Shared seed-planning helper for generator pipes.

Unifies the "seeds supplied by an upstream pipe vs. a config seed vs. a fresh
random seed" resolution that generator/qwen, generator/chroma and
generator/sdxl each hand-rolled slightly differently (and, in chroma's case,
incorrectly).
"""
from typing import List, Optional, Union

from src.platform.util.latents import generate_seed


def plan_seeds(
        input_seeds: Optional[Union[List[int], int]],
        config_seed: int,
        quantity: int,
) -> List[int]:
    """Resolve the seed to use for each of `quantity` items to generate.

    For index i: use `input_seeds[i]` if an upstream pipe (e.g. a
    seed_generator) provided enough seeds, otherwise fall back to a fresh
    seed derived from `config_seed` (-1 means fully random, any other value
    is reused as-is via `generate_seed`).

    This mirrors the behavior already used by generator/qwen and
    generator/sdxl (`seeds[i] if i < len(seeds) else generate_seed(config_seed)`).
    It also fixes the equivalent generator/chroma bug that treated the seeds
    *list* as a dict (`seeds[_] if _ in seeds else ...`), which meant any
    seed list with a value at index 0 (the common case) was accidentally
    ignored for every subsequent index because `_ in seeds` checks values,
    not positions.
    """
    if input_seeds is None:
        seeds: List[int] = []
    elif isinstance(input_seeds, list):
        seeds = input_seeds
    else:
        seeds = [input_seeds]

    return [
        seeds[i] if i < len(seeds) else generate_seed(config_seed)
        for i in range(quantity)
    ]
