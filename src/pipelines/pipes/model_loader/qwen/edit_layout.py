from __future__ import annotations

import hashlib
from typing import Any, Dict, Optional

import torch

EDIT_PLUS_LAYOUT: Dict[str, Any] = {"picture_labels": True, "grounding_area": 384 * 384}
ORIGINAL_EDIT_PROJ_OUT_BIAS = frozenset({
    "6fc6af1f8996aa2a34cdfe6e10a7e2f676f4fdcb31ba18cdf4fa27f0528f108b",
})


def proj_out_bias_digest(module: Any) -> Optional[str]:
    bias = getattr(getattr(module, "proj_out", None), "bias", None)
    if not isinstance(bias, torch.Tensor) or bias.is_meta:
        return None
    canonical = bias.detach().float().to(torch.float8_e4m3fn).cpu().contiguous()
    return hashlib.sha256(canonical.view(torch.uint8).numpy().tobytes()).hexdigest()


def qwen_edit_vision_layout(module: Any) -> Dict[str, Any]:
    if getattr(getattr(module, "config", None), "default_ref_method", None) == "index_timestep_zero":
        return dict(EDIT_PLUS_LAYOUT)
    if proj_out_bias_digest(module) in ORIGINAL_EDIT_PROJ_OUT_BIAS:
        return {}
    return dict(EDIT_PLUS_LAYOUT)
