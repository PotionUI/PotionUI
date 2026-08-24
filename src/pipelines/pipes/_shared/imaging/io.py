"""Input-normalization for the image-utility pipes' `image` input.

Every producer this family of pipes chains after (`media_loader`, and each
other) declares its `image` output as `is_array=True` and hands over a LIST
- even for a single file. Accepting only a bare `PIL.Image` there raises
`'Image' object is not iterable` the moment a real preset feeds a list in (a
gallery/media_loader pairing this whole family sits downstream of). This
helper is the single place that normalizes: a bare `Image` is accepted
defensively (wrapped in a one-element list, e.g. for direct unit-testing of
a pipe in isolation) but every pipe always PROCESSES and EMITS the full list
- never just its first element - so a multi-image upstream is never
silently truncated to one.
"""

from typing import List

from PIL import Image


def as_image_list(value, pipe_name: str) -> List[Image.Image]:
    if value is None:
        raise ValueError(f"{pipe_name} requires an 'image' input")
    images = value if isinstance(value, list) else [value]
    if not images:
        raise ValueError(f"{pipe_name} requires a non-empty 'image' input")
    return images
