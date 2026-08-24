"""Memoizes the base64 JPEG encode of a PIL Image across repeat emissions.

A generated image is commonly emitted twice with an identical encode: once as
a temporary live preview (`workbench_update` / `gallery_update`), then again,
unchanged, as the final non-temporary emission once no further pipe transforms
it (e.g. `src/pipelines/pipes/gallery/main.py` re-wrapping the same PIL Image
the generator already previewed). `create_base64_image` was redoing the same
resize + JPEG encode both times.

`PIL.Image.Image` is not hashable (see `Image.__eq__`), so it cannot key a
`WeakKeyDictionary` directly. The cache instead keys on `id(image)` and pairs
each entry with a `weakref.ref` whose callback evicts that id the moment the
object is actually deallocated -- so a later, unrelated image reusing the same
freed address can never collide with a stale entry.
"""

import functools
import threading
import weakref
from typing import Callable, Dict, Optional

from PIL import Image

_lock = threading.Lock()
_by_dimension: Dict[int, Dict[int, Optional[str]]] = {}
_refs: Dict[int, weakref.ref] = {}


def _evict(image_id: int, _ref=None) -> None:
    with _lock:
        _by_dimension.pop(image_id, None)
        _refs.pop(image_id, None)


def get_or_encode(
    image: Image.Image,
    max_dimension: int,
    encode_fn: Callable[[Image.Image, int], Optional[str]],
) -> Optional[str]:
    """Return the memoized encode of `image` at `max_dimension`, computing it
    via `encode_fn(image, max_dimension)` on a miss."""
    image_id = id(image)

    with _lock:
        cached = _by_dimension.get(image_id)
        if cached is not None and max_dimension in cached:
            return cached[max_dimension]

    encoded = encode_fn(image, max_dimension)

    with _lock:
        if image_id not in _refs:
            try:
                _refs[image_id] = weakref.ref(image, functools.partial(_evict, image_id))
            except TypeError:
                # Some Image-like object that can't be weakly referenced --
                # return the freshly encoded value without caching it.
                return encoded
        _by_dimension.setdefault(image_id, {})[max_dimension] = encoded

    return encoded
