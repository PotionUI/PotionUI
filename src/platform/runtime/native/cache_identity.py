"""Cheap, exact identity for tensors used as run-cache keys.

A per-run cache (``RunCache``, engine.py) keys on the tensors a forward was
handed. The key must be computable on every step without touching device memory
— no hashing, no ``.item()``, no sync — yet must not answer a lookup with a value
computed from different numbers.

``(data_ptr, shape, dtype, device)`` is not enough on its own. It misses two
things:

  * an IN-PLACE write to the same tensor (``weights.mul_(2)``) leaves every one
    of those components unchanged;
  * two different-stride views of one storage (``base[:, :5, :]`` and
    ``base[:, :, :5].transpose(1, 2)``) share a ``data_ptr`` and can share a
    shape while addressing entirely different elements.

So the identity also carries ``stride``, ``storage_offset`` and the tensor's
version counter. The version counter is shared by a tensor and its views, so a
write through any alias bumps it.

Inference tensors
-----------------
Tensors produced under ``torch.inference_mode`` (every native text encoder
encodes that way, so conditioning routinely arrives as inference tensors, and a
no-op ``.to()`` in the engine's cond move returns them unchanged) do not track a
version counter — reading ``_version`` raises. They are not excluded from
caching, because PyTorch itself supplies the guarantee the counter would have
provided: an in-place write to an inference tensor outside ``InferenceMode``
raises ``RuntimeError``. Sampling runs under ``no_grad``, never
``inference_mode``, so such a tensor cannot change while a run holds it. They
therefore take a constant marker in the version slot.

The one gap this leaves is a write performed inside a NEW ``inference_mode``
block entered mid-run. Nothing in the sampling path does that — conditioning is
built before ``sample()`` and is read-only for the DiT — and no caller should
start.

Anything else whose version cannot be read is reported as
:data:`UNIDENTIFIABLE`, and a caller that sees it must not cache.
"""

from __future__ import annotations

from typing import Any, Optional

import torch

Tensor = torch.Tensor

# Version-slot marker for a tensor PyTorch guarantees cannot be written in place
# for the duration of a run. See the module docstring.
_INFERENCE_IMMUTABLE = "inference"


class _Unidentifiable:
    """Sentinel: this tensor's identity could not be established.

    Distinct from ``None``, which is a real identity meaning "the caller passed
    no tensor here". A key containing this must never be stored or looked up —
    use :func:`identity_usable`.
    """

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "<UNIDENTIFIABLE>"


UNIDENTIFIABLE = _Unidentifiable()


def tensor_identity(t: Optional[Tensor]) -> Any:
    """Identity of ``t`` for a run-cache key: a tuple, ``None``, or the sentinel.

    ``None`` in, ``None`` out (an absent optional tensor is itself a key
    component). Two calls return equal values exactly when the tensor addresses
    the same elements with the same layout and has not been written in place
    between them.
    """
    if t is None:
        return None
    if t.is_inference():
        version: Any = _INFERENCE_IMMUTABLE
    else:
        try:
            version = t._version
        except RuntimeError:
            return UNIDENTIFIABLE
    return (
        t.data_ptr(), tuple(t.shape), tuple(t.stride()), t.storage_offset(),
        t.dtype, t.device, version,
    )


def identity_usable(*parts: Any) -> bool:
    """True when every part is a usable key component (none is the sentinel)."""
    return not any(p is UNIDENTIFIABLE for p in parts)
