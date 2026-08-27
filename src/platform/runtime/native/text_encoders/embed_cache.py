"""Process-global LRU cache of encoded prompt embeddings.

Every native generation re-runs the text encoder — and, worse, pages a
multi-billion-parameter encoder onto the GPU to do it (see
``memory/residency.run_text_encode``). In an iterate-on-seed loop the prompt
never changes, so the *output* embeddings can be memoised and the whole GPU
dance skipped: on a hit the encoder never touches the device.

The cache stores detached **CPU clones** keyed by a stable fingerprint of (text
encoder identity, prompt text, encode kwargs). Values are the arbitrary nested
structure the encode function returns — a role dict, or a ``(cond, uncond)``
pair of them — with every tensor moved to CPU and cloned so that

  * no GPU memory is ever pinned by the cache, and
  * a later in-place mutation of either the caller's originals or the returned
    tensors cannot corrupt a cached entry.

A caller opts a component out of caching (image-conditioned encodes, or a text
encoder with no stable checkpoint fingerprint) by passing ``None`` where a key
is expected — :func:`prompt_embed_key` returns ``None`` when the model
fingerprint is missing.
"""

from __future__ import annotations

import hashlib
import logging
import threading
from collections import OrderedDict
from typing import Any, Optional

import torch

logger = logging.getLogger(__name__)

# Prompt-weighting grammar version. Bump if the A1111 ``(word:1.3)`` parsing in
# ``prompt_weights.py`` changes in a way that alters embeddings for the same
# prompt text, so stale entries from an older grammar can't be reused.
PROMPT_WEIGHTS_VERSION = 1


def _to_cpu_tree(obj: Any) -> Any:
    """Deep-copy ``obj`` with every tensor detached and cloned onto the CPU.

    Recurses through dict / list / tuple; passes non-tensor leaves (None, ints,
    strings) through unchanged. The ``clone()`` gives the cache its own storage
    so mutating the caller's originals afterwards can't reach into the entry.
    """
    if isinstance(obj, torch.Tensor):
        return obj.detach().to("cpu").clone()
    if isinstance(obj, dict):
        return {k: _to_cpu_tree(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        mapped = [_to_cpu_tree(v) for v in obj]
        return _rebuild_sequence(obj, mapped)
    return obj


def _rebuild_sequence(original: Any, mapped: list) -> Any:
    """Reconstruct a list/tuple, using ``_make`` for namedtuples (E22).

    ``type(obj)(mapped)`` works for a plain ``list``/``tuple`` but raises for a
    namedtuple (its constructor takes positional fields, not a single iterable),
    so a text encoder returning ``Output(context, pooled)`` would break caching.
    """
    if isinstance(original, tuple):
        if hasattr(original, "_fields"):          # namedtuple
            return type(original)._make(mapped)
        return tuple(mapped)
    return list(mapped)


def _to_device_tree(obj: Any, device: "str | torch.device") -> Any:
    """Mirror of :func:`_to_cpu_tree` that materialises a fresh copy on ``device``.

    Always returns tensors the caller owns outright: a device move to a *different*
    device already copies, and when the target is the CPU (the tensor would be
    returned as-is) an explicit ``clone()`` keeps the cached entry isolated.
    """
    if isinstance(obj, torch.Tensor):
        moved = obj.to(device=device)
        return moved.clone() if moved is obj else moved
    if isinstance(obj, dict):
        return {k: _to_device_tree(v, device) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        mapped = [_to_device_tree(v, device) for v in obj]
        return _rebuild_sequence(obj, mapped)
    return obj


def _reject_tensors(obj: Any) -> None:
    """Raise if any tensor is nested in a cache-key part (E16).

    Keys hash ``repr(...)``, which ellipsizes large tensors — two distinct
    tensors with identical printed edges would collide. Real callers only pass
    strings/scalars, so a tensor here is a programming error; fail loudly rather
    than silently alias.
    """
    if isinstance(obj, torch.Tensor):
        raise TypeError(
            "PromptEmbedCache key parts must be repr-stable (str/int/bool/...); "
            "got a torch.Tensor, whose repr() ellipsizes and would alias distinct inputs"
        )
    if isinstance(obj, dict):
        for v in obj.values():
            _reject_tensors(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _reject_tensors(v)


class PromptEmbedCache:
    """Process-global LRU of encoded prompt embeddings, keyed by a stable hash.

    Thread-safe (a lock guards the ``OrderedDict``); values are CPU tensor trees.
    Sized by entry count, not bytes — an entry is a few MB, so 32 of them is a
    small, bounded cost against re-running a multi-billion-parameter encoder.
    """

    def __init__(self, max_entries: int = 32) -> None:
        self._store: "OrderedDict[str, Any]" = OrderedDict()
        self._max = max(1, int(max_entries))
        self._lock = threading.Lock()

    @staticmethod
    def key(te_fingerprint: str, prompt: Any, extra: Any = ()) -> str:
        """Stable content hash of (encoder identity, prompt(s), encode kwargs).

        Deterministic within and across processes (sha256 over a canonical repr),
        so it is safe to compare in tests and never salted like ``hash()``.
        """
        _reject_tensors((te_fingerprint, prompt, extra))
        canonical = repr((te_fingerprint, prompt, PROMPT_WEIGHTS_VERSION, extra))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _peek(self, key: str) -> Optional[Any]:
        """Internal: the raw stored tree (LRU-touched), or ``None``. Callers that
        return it to the outside MUST copy first (see :meth:`get`)."""
        with self._lock:
            if key not in self._store:
                return None
            self._store.move_to_end(key)
            return self._store[key]

    def get(self, key: str) -> Optional[Any]:
        """Return a fresh CPU-cloned copy of the cached tensor tree, or ``None``.

        Returns a COPY (E23): the stored tree is never exposed directly, so a
        caller mutating the result (``get(k)["context"].zero_()``) cannot corrupt
        the entry for later hits. ``get_on_device`` is the fast path that copies
        as part of the device move."""
        cached = self._peek(key)
        return None if cached is None else _to_cpu_tree(cached)

    def get_on_device(self, key: str, device: "str | torch.device") -> Optional[Any]:
        """Like :meth:`get` but materialised as a fresh copy on ``device``."""
        cached = self._peek(key)
        if cached is None:
            return None
        return _to_device_tree(cached, device)

    def put(self, key: str, value: Any) -> None:
        """Store ``value`` (any nested tensor tree) as detached CPU clones."""
        cpu_value = _to_cpu_tree(value)
        with self._lock:
            self._store[key] = cpu_value
            self._store.move_to_end(key)
            while len(self._store) > self._max:
                evicted, _ = self._store.popitem(last=False)
                logger.debug("PromptEmbedCache: evicted LRU entry %s", evicted[:12])

    def clear(self) -> None:
        with self._lock:
            self._store.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._store)


_PROMPT_EMBED_CACHE: PromptEmbedCache | None = None
_SINGLETON_LOCK = threading.Lock()


def get_prompt_embed_cache() -> PromptEmbedCache:
    """The process-global :class:`PromptEmbedCache` singleton.

    Mirrors ``memory.residency.get_residency_registry`` — one instance per process,
    lazily created.
    """
    global _PROMPT_EMBED_CACHE
    if _PROMPT_EMBED_CACHE is None:
        with _SINGLETON_LOCK:
            if _PROMPT_EMBED_CACHE is None:
                _PROMPT_EMBED_CACHE = PromptEmbedCache()
    return _PROMPT_EMBED_CACHE


def image_content_fingerprint(image: torch.Tensor) -> str:
    """Deterministic content hash of one image tensor, for a ``prompt_embed_key``
    ``*parts`` entry.

    Image-conditioned text-encoder calls (Qwen-Image-Edit's vision path, see
    ``qwen25_vl.py``'s ``Qwen25VLTextEncoder._encode_with_images``) must NOT be
    cached under a key built from the prompt text alone: two different images
    with the same prompt would silently alias to the wrong cached embedding — a
    correctness bug, not mere cache staleness. No caller does this today (there
    is no image-conditioned pipe yet — the encoder itself never touches the
    cache, matching every other native text encoder), so this function has no
    caller yet either; it exists so the FUTURE caller that wires one up has a
    correct, tested building block instead of reaching for ``repr(image)`` (which
    :func:`prompt_embed_key`'s ``_reject_tensors`` already refuses to hash for
    exactly this reason — ``repr()`` ellipsizes large tensors and would alias
    distinct images) or the image's Python ``id()`` (not stable/deterministic).

    Hashes the raw pixel bytes (moved to CPU, cast to fp32, made contiguous)
    plus shape, so two images differing only in dtype but identical once cast to
    fp32 collide (deliberate — same visual content), while any shape or pixel
    difference does not.
    """
    cpu = image.detach().to("cpu").to(torch.float32).contiguous()
    header = f"{tuple(cpu.shape)}".encode("utf-8")
    return hashlib.sha256(header + cpu.numpy().tobytes()).hexdigest()


def prompt_embed_key(model_fingerprint: Optional[str], role: Optional[str], *parts: Any) -> Optional[str]:
    """Build a cache key from a text encoder's identity and encode inputs.

    Returns ``None`` — signalling *do not cache* — when ``model_fingerprint`` is
    missing, because without the checkpoint (+ LoRA set) identity two different
    models could otherwise alias to the same prompt embeddings. ``role`` is the
    encoder's variant tag (e.g. ``"qwen3_8b"`` / ``"t5xxl"``); ``parts`` are the
    prompt string(s) and any encode kwargs that change the output.
    """
    if not model_fingerprint:
        return None
    te_fingerprint = f"{model_fingerprint}|{role or ''}"
    return PromptEmbedCache.key(te_fingerprint, parts)
