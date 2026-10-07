from __future__ import annotations

import math
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Optional

from ..trellis2.config import (
    SHAPE_SLAT_FLOW_512,
    SHAPE_SLAT_FLOW_1024,
    SS_FLOW_PRODUCTION,
    TEX_SLAT_FLOW_1024,
)

__all__ = [
    "BUNDLE_MODES",
    "CAMERA_ESTIMATE_SIZE",
    "CROP_PAD",
    "DEFAULT_FOV_DEG",
    "DEFAULT_TIER",
    "EXPORT_FRAMES",
    "FOV_AUTO",
    "FOV_ESTIMATED",
    "FOV_FALLBACK",
    "FOV_MANUAL",
    "FOV_MODES",
    "MULTIVIEW",
    "MULTIVIEW_FOV_DEG",
    "PIXAL3D_SHAPE_SLAT_FLOW_512",
    "PIXAL3D_SHAPE_SLAT_FLOW_1024",
    "PIXAL3D_SS_FLOW",
    "PIXAL3D_TEX_SLAT_FLOW_1024",
    "PIXAL3D_TIERS",
    "SINGLE_VIEW",
    "STAGE_CONDITIONING",
    "StageConditioning",
    "VIEW_AZIMUTHS",
    "VIEW_PAD",
    "bundle_mode_of",
]

PIXAL3D_SS_FLOW = replace(SS_FLOW_PRODUCTION, image_attn_mode="proj", proj_in_channels=1024)
PIXAL3D_SHAPE_SLAT_FLOW_512 = replace(SHAPE_SLAT_FLOW_512, image_attn_mode="proj", proj_in_channels=2048)
PIXAL3D_SHAPE_SLAT_FLOW_1024 = replace(SHAPE_SLAT_FLOW_1024, image_attn_mode="proj", proj_in_channels=2048)
PIXAL3D_TEX_SLAT_FLOW_1024 = replace(TEX_SLAT_FLOW_1024, image_attn_mode="proj", proj_in_channels=2048)

PIXAL3D_TIERS = ("1024", "1536")
DEFAULT_TIER = "1536"

DEFAULT_FOV_DEG = 49.13
UPSTREAM_DEFAULT_FOV = 0.8575560450553894
MULTIVIEW_FOV_DEG = 20.0

FOV_MANUAL = "manual"
FOV_AUTO = "auto"
FOV_MODES = (FOV_MANUAL, FOV_AUTO)
FOV_ESTIMATED = "estimated"
FOV_FALLBACK = "fallback"
CAMERA_ESTIMATE_SIZE = 1024

CROP_PAD = 1.1
VIEW_PAD = 1.1
VIEW_AZIMUTHS = {"front": 0.0, "left": 90.0, "back": 180.0, "right": 270.0}

SINGLE_VIEW = "single"
MULTIVIEW = "multiview"
BUNDLE_MODES = (SINGLE_VIEW, MULTIVIEW)

EXPORT_FRAMES = ("upstream", "camera")

SS_PROJECTION_GRID = 16
LR_PROJECTION_GRID = 32


@dataclass(frozen=True)
class StageConditioning:
    image_size: int
    naf_size: Optional[int]


STAGE_CONDITIONING = {
    "sparse_structure": StageConditioning(512, None),
    "shape_lr": StageConditioning(512, 512),
    "shape_hr": StageConditioning(1024, 512),
    "texture": StageConditioning(1024, 1024),
}

_MULTIVIEW_MARKER = re.compile(r"(multi[_\-. ]?view|(^|[_\-. ])mv([_\-. ]|$))")


def bundle_mode_of(path: str | Path) -> str:
    return MULTIVIEW if _MULTIVIEW_MARKER.search(Path(str(path)).stem.lower()) else SINGLE_VIEW


def fov_radians(fov_deg: float) -> float:
    fov_deg = float(fov_deg)
    if not 0.0 < fov_deg < 180.0:
        raise ValueError(f"camera field of view must be between 0 and 180 degrees, got {fov_deg}")
    return math.radians(fov_deg)
