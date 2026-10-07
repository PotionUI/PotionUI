from __future__ import annotations

from pathlib import Path

import torch

from ..trellis2 import load as trellis2_load
from ..trellis2.slat_flow import SLatFlowModel
from ..trellis2.ss_flow import SSFlowDiT
from .config import (
    PIXAL3D_SHAPE_SLAT_FLOW_512,
    PIXAL3D_SHAPE_SLAT_FLOW_1024,
    PIXAL3D_SS_FLOW,
    PIXAL3D_TEX_SLAT_FLOW_1024,
)
from .naf import NAF, load_naf

__all__ = [
    "load_pixal3d_naf",
    "load_pixal3d_shape_flow",
    "load_pixal3d_ss_flow",
    "load_pixal3d_tex_flow",
]

_SHAPE_FLOWS = {"512": PIXAL3D_SHAPE_SLAT_FLOW_512, "1024": PIXAL3D_SHAPE_SLAT_FLOW_1024}


def load_pixal3d_ss_flow(path: str | Path, *, dtype: torch.dtype | None = None) -> SSFlowDiT:
    return trellis2_load.load_ss_flow(path, PIXAL3D_SS_FLOW, dtype=dtype)


def load_pixal3d_shape_flow(path: str | Path, tier: str, *, dtype: torch.dtype | None = None) -> SLatFlowModel:
    if tier not in _SHAPE_FLOWS:
        raise ValueError(f"unknown Pixal3D shape flow tier {tier!r}; expected one of {sorted(_SHAPE_FLOWS)}")
    return trellis2_load.load_shape_slat_flow(path, tier, _SHAPE_FLOWS[tier], dtype=dtype)


def load_pixal3d_tex_flow(path: str | Path, *, dtype: torch.dtype | None = None) -> SLatFlowModel:
    return trellis2_load.load_tex_slat_flow(path, "1024", PIXAL3D_TEX_SLAT_FLOW_1024, dtype=dtype)


def load_pixal3d_naf(path: str | Path) -> NAF:
    return load_naf(path)
