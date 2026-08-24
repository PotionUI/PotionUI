"""Anchor synthesis for DFR's temporal rounds.

A carried anchor is a single-PIXEL-frame latent pinned at a seam so the tiles
either side of it converge on the same content. The anchor mechanism itself is
content-agnostic (it takes any one-pixel-frame latent), so increment 1 -- which
has no generated keyframe slots yet -- synthesizes the bag from the incoming
latent instead: decode it once, take the pixel frame at each canvas slot
position, and VAE-encode each of those frames as a **standalone one-frame
clip**.

The obvious shortcut -- slicing latent frame ``position // 8`` straight out of
the incoming latent -- is not a substitute and produces silently wrong anchors.
Under the causal VAE a mid-stream latent frame encodes eight pixel frames
*relative to its predecessors*; only the first latent frame of an encode covers
exactly one pixel frame standing alone. That is the same reason a generated
keyframe has to be decoded one frame at a time rather than as a K-frame clip.

Cost: one extra full decode of the round-input clip. A later optimization can
decode only the seam neighbourhoods; increment 1 pays for the whole thing.
"""

from __future__ import annotations

from typing import Callable, Dict, Iterable, Sequence

import numpy as np
import torch

Tensor = torch.Tensor


def frame_to_pixels(frame: np.ndarray) -> Tensor:
    """One decoded ``(H, W, 3)`` uint8 frame -> ``(1, 3, 1, H, W)`` in [-1, 1],
    the shape ``LTXCausalVideoVAE.encode`` expects for a one-frame clip."""
    chw = torch.from_numpy(np.ascontiguousarray(frame)).float().div_(255.0).permute(2, 0, 1)
    return (chw * 2.0 - 1.0).unsqueeze(0).unsqueeze(2)


def synthesize_anchor_bag(
    latent: Tensor,
    positions: Sequence[int],
    *,
    decode: Callable[[Tensor], np.ndarray],
    encode_frame: Callable[[Tensor], Tensor],
) -> Dict[int, Tensor]:
    """Build ``{pixel position: (1, C, 1, h, w) latent}`` for ``positions``.

    ``decode`` takes the whole latent and returns ``(T, H, W, 3)`` uint8 pixel
    frames; ``encode_frame`` takes one ``(1, 3, 1, H, W)`` clip in [-1, 1] and
    returns its normalized latent. Both are injected so this stays a pure
    orchestration step -- the pipe supplies the real decode ladder and VAE.
    """
    if not positions:
        raise ValueError("DFR anchor synthesis needs at least one seam position")
    frames = decode(latent)
    available = int(frames.shape[0])
    bag: Dict[int, Tensor] = {}
    for position in positions:
        index = int(position)
        if not 0 <= index < available:
            raise ValueError(
                f"DFR anchor at pixel frame {index} is outside the {available} decoded frames -- "
                f"the canvas padding did not reach the terminal slot")
        bag[index] = encode_frame(frame_to_pixels(frames[index]))
    return bag


def bag_items(bag: Dict[int, Tensor], positions: Iterable[int]):
    """``(position, latent)`` pairs for ``positions``, in the order given --
    the shape ``dfr_layout.merge_keyframe_bag`` consumes."""
    return [(int(p), bag[int(p)]) for p in positions]
