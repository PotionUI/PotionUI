"""
The vocabulary a backend uses to report which models it can load.

A backend answers with `BackendModel` entries. Two fields carry the weight:

* `filename` is the **identity** — a model is `(model_type, filename)`. Native's
  `models/loras/x.safetensors` and ComfyUI's `style/x.safetensors` are the same model.
* `ref` is the **engine-native string** that this backend needs handed back to it in
  order to load the file. It is opaque to core; only the backend that produced it
  knows how to interpret it.

Backends differ in what they can prove. Native reads bytes, so it can hash. A ComfyUI
server reports names, and sometimes sizes, and never hashes. `confidence` records that
difference instead of hiding it. See docs/models.md.
"""

from dataclasses import dataclass
from typing import List, Optional


CONFIDENCE_VERIFIED = "verified"    # bytes were read on this host; sha256 is known
CONFIDENCE_REPORTED = "reported"    # the backend reported a name and a size
CONFIDENCE_NAME_ONLY = "name_only"  # the backend reported a name and nothing else

# Not something a backend reports about itself - `BackendModelIndexer` assigns this to a
# `model_availability` row when the digest a backend just computed for its own copy of a
# file disagrees with the model's canonical `models.sha256`. See docs/models.md and
# migration 110_model_availability_digest.py.
CONFIDENCE_CONFLICT = "conflict"


class ModelListingNotSupported(RuntimeError):
    """Raised by backends that cannot enumerate their models."""


@dataclass(frozen=True)
class BackendModel:
    model_type: str
    filename: str
    ref: str
    size: Optional[int] = None
    sha256: Optional[str] = None

    @property
    def confidence(self) -> str:
        if self.sha256:
            return CONFIDENCE_VERIFIED
        if self.size is not None:
            return CONFIDENCE_REPORTED
        return CONFIDENCE_NAME_ONLY

    @property
    def identity(self) -> tuple:
        return (self.model_type, self.filename)


def deduplicate(entries: List[BackendModel]) -> List[BackendModel]:
    """Collapse entries that name the same file twice.

    A ComfyUI folder can have several search roots, so one file is reachable under two
    refs (`upscale.pth` and `extra/upscale.pth`, identical size).
    Keep the first — refs are interchangeable to the server that produced them.

    Deduplication is on `(model_type, filename, size)` rather than on identity alone:
    two files with the same name and *different* sizes are genuinely different models
    (a quantised copy, say), and collapsing them would silently pick one at random.
    """
    seen = set()
    result = []
    for entry in entries:
        key = (entry.model_type, entry.filename, entry.size)
        if key in seen:
            continue
        seen.add(key)
        result.append(entry)
    return result
