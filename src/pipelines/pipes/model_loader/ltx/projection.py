"""Reads the ``text_embedding_projection.*`` tensors out of the all-in-one LTX
checkpoint (the DiT loader keeps only the ``model.diffusion_model.*`` slice, so
these top-level tensors are read separately here via ``safe_open``).

19b ships one
shared bias-less ``aggregate_embed.weight``; 2.3 ships dual biased
``{video,audio}_aggregate_embed.{weight,bias}``. The returned dict is fed
straight into ``LTXAVModel.apply_text_conditioning(**projections)``.

LTX-2.5 relocated the projection into the Gemma4 TE file instead (dual biased,
same 2.3 key set) — ``te_path`` is an optional fallback probed only when the
DiT checkpoint carries neither key set.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional

import torch
from safetensors import safe_open

_PROJECTION_KEY = "text_embedding_projection.aggregate_embed.weight"


def _read_projection(path: str, device: str, dtype: torch.dtype) -> Optional[Dict[str, torch.Tensor]]:
    """Return the projection tensors found in ``path``, or ``None`` if absent."""
    with safe_open(path, framework="pt", device="cpu") as f:
        keys = set(f.keys())

        def get(k: str) -> torch.Tensor:
            return f.get_tensor(k).to(device=device, dtype=dtype)

        if _PROJECTION_KEY in keys:  # 19b: shared bias-less projection
            return {"video_projection_weight": get(_PROJECTION_KEY)}

        prefix = "text_embedding_projection."
        video_w = prefix + "video_aggregate_embed.weight"
        if video_w not in keys:
            return None
        out = {
            "video_projection_weight": get(video_w),
            "audio_projection_weight": get(prefix + "audio_aggregate_embed.weight"),
        }
        for stream in ("video", "audio"):
            bias = f"{prefix}{stream}_aggregate_embed.bias"
            if bias in keys:
                out[f"{stream}_projection_bias"] = get(bias)
        return out


def load_projection(
    dit_path: str, device: str, dtype: torch.dtype, te_path: Optional[str] = None,
) -> Dict[str, torch.Tensor]:
    """Load the text-embedding projection weight(s)/bias(es).

    Tries ``dit_path`` (the all-in-one checkpoint) first — the 2.0/2.3 layout.
    When ``te_path`` is given and the DiT carries neither the 19b nor the 2.3
    key set, falls back to probing the TE checkpoint (the 2.5 layout, where
    the projection moved off the DiT and onto the Gemma4 TE file).

    Raises ``KeyError`` if the projection is found in neither location (or
    only ``dit_path`` was checked, matching the pre-``te_path`` contract).
    """
    out = _read_projection(dit_path, device, dtype)
    if out is not None:
        return out
    if te_path is not None:
        out = _read_projection(te_path, device, dtype)
        if out is not None:
            return out
        raise KeyError(
            f"no text_embedding_projection found in {Path(dit_path).name} or "
            f"{Path(te_path).name}; expected {_PROJECTION_KEY!r} (19b) or the "
            "2.3/2.5 video_aggregate_embed key set."
        )
    raise KeyError(
        f"no text_embedding_projection found in {Path(dit_path).name}; expected "
        f"{_PROJECTION_KEY!r} (19b) or "
        "'text_embedding_projection.video_aggregate_embed.weight' (2.3)."
    )
