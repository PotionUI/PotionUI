"""State-dict introspection and reshaping helpers.

Pure functions over ``dict[str, torch.Tensor]`` — no model knowledge here.
Detection modules build on top of these.
"""

from __future__ import annotations

from collections import Counter

import torch


def strip_prefix(sd: dict[str, torch.Tensor], prefix: str) -> dict[str, torch.Tensor]:
    """Return a new dict with ``prefix`` removed from every key that has it.

    Keys that do not start with ``prefix`` are dropped — this mirrors
    ComfyUI's component-extraction behaviour (isolate one sub-model).
    """
    return {k[len(prefix):]: v for k, v in sd.items() if k.startswith(prefix)}


def detect_prefix(sd: dict[str, torch.Tensor], candidates: list[str]) -> str | None:
    """Pick the candidate prefix that the most keys share.

    Count-based like ComfyUI's ``unet_prefix_from_state_dict``: the winning
    prefix is the one owning the largest number of keys. Returns ``None`` when
    no candidate matches any key.
    """
    best: str | None = None
    best_count = 0
    for prefix in candidates:
        count = sum(1 for k in sd if k.startswith(prefix))
        if count > best_count:
            best = prefix
            best_count = count
    return best


def count_blocks(sd: dict[str, torch.Tensor], pattern: str) -> int:
    """Count contiguous indices ``i`` for which ``pattern.format(i)`` prefixes a key.

    ``pattern`` must contain a single ``{}`` placeholder, e.g.
    ``"double_blocks.{}."``. Counting stops at the first gap, so a checkpoint
    with blocks 0..7 returns 8 regardless of stray higher indices.
    """
    n = 0
    while any(k.startswith(pattern.format(n)) for k in sd):
        n += 1
    return n


def key_shapes(sd: dict[str, torch.Tensor]) -> dict[str, tuple[int, ...]]:
    """Map every key to its tensor shape (as a plain tuple)."""
    return {k: tuple(v.shape) for k, v in sd.items()}


def is_nvfp4_packed(sd: dict[str, torch.Tensor], weight_key: str) -> bool:
    """True when ``weight_key`` (a ``...weight`` tensor) is nvfp4-packed on disk:
    two 4-bit codes per stored byte along the in-features axis, so the stored
    shape is ``[out, in // 2]`` rather than the model's true ``[out, in]``.

    Detected via the sibling ``weight_scale_2`` key -- the nvfp4 marker consumed
    by ``vendor.gpl.comfyui.ops.Nvfp4Linear`` -- rather than dtype, since a
    packed nvfp4 tensor is plain ``torch.uint8``, indistinguishable from any
    other byte tensor by dtype alone. Never true for Conv weights or raw
    Parameters (nvfp4 packing in ComfyUI checkpoints applies to ``nn.Linear``
    weights only), so callers may check any ``.weight`` key unconditionally.
    """
    if not weight_key.endswith(".weight"):
        return False
    return weight_key[: -len(".weight")] + ".weight_scale_2" in sd


def linear_in_features(sd: dict[str, torch.Tensor], weight_key: str) -> int:
    """True in-features of a ``[out, in]`` Linear weight, undoing nvfp4 packing
    when ``weight_key`` is packed (see :func:`is_nvfp4_packed`).

    Config detectors must read a possibly-quantized Linear weight's
    in-features through this helper, never a bare ``.shape[1]``: an unpacked
    read silently halves the inferred dimension and builds the model
    at the wrong width, which then fails ``load_state_dict`` with a size
    mismatch on every OTHER unquantized tensor sized off that dimension --
    not on the packed weight itself, which is why the symptom looks unrelated
    to quantisation. ``out-features`` (``.shape[0]``) is never packed and
    needs no such guard.
    """
    width = int(sd[weight_key].shape[1])
    return width * 2 if is_nvfp4_packed(sd, weight_key) else width


def weight_dtype(sd: dict[str, torch.Tensor]) -> torch.dtype | None:
    """Majority dtype among floating-point tensors in the state dict.

    Non-floating tensors (int index buffers etc.) are ignored. Returns
    ``None`` for an empty / all-integer state dict.
    """
    counts: Counter[torch.dtype] = Counter()
    for v in sd.values():
        if v.is_floating_point():
            counts[v.dtype] += 1
    if not counts:
        return None
    return counts.most_common(1)[0][0]
