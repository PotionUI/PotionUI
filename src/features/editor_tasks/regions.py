import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
from PIL import Image

from src.platform.imaging.regions import (
    dilate_round,
    extend_down,
    fit_axis,
    mask_bbox,
    mask_patch,
    matte_patch,
    region_patch,
    resample_image,
    scaled_crop_box,
    working_size,
)

MIN_SIDE = 256
LANDED = 128
WHOLE_SHARE = 0.7
FEATHER_FLOOR = 8
FEATHER_SHARE = 0.06
SOFT_FACTOR = 2.0
MAX_SIDE = 8192
STRATEGIES = ("crop", "mask", "whole")
GROW_FLOOR = 6
GROW_CAP_SHARE = 0.15
SHADOW_GROWTH = 2.5
SHADOW_DROP = 0.25


@dataclass(frozen=True)
class Geometry:
    strategy: str
    box: Tuple[int, int, int, int]
    working: Tuple[int, int]
    feather_px: int
    whole: bool
    has_region: bool
    soft: bool
    picture: Tuple[int, int]
    matte: bool = False

    @property
    def crop_size(self) -> Tuple[int, int]:
        top, bottom, left, right = self.box
        return right - left, bottom - top

    def rect(self) -> Dict[str, float]:
        top, bottom, left, right = self.box
        width, height = self.picture
        return {
            "x": left / width,
            "y": top / height,
            "width": (right - left) / width,
            "height": (bottom - top) / height,
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "strategy": self.strategy,
            "box": list(self.box),
            "working": list(self.working),
            "feather_px": self.feather_px,
            "whole": self.whole,
            "has_region": self.has_region,
            "soft": self.soft,
            "picture": list(self.picture),
            "matte": self.matte,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Geometry":
        return cls(
            strategy=data["strategy"],
            box=tuple(int(value) for value in data["box"]),
            working=tuple(int(value) for value in data["working"]),
            feather_px=int(data["feather_px"]),
            whole=bool(data["whole"]),
            has_region=bool(data["has_region"]),
            soft=bool(data.get("soft", False)),
            picture=tuple(int(value) for value in data["picture"]),
            matte=bool(data.get("matte", False)),
        )


@dataclass
class Prepared:
    image: Image.Image
    mask: Optional[Image.Image]
    base: np.ndarray
    region: Optional[np.ndarray]


def cap_picture(image: Image.Image, limit: int = MAX_SIDE) -> Image.Image:
    longest = max(image.size)
    if longest <= limit:
        return image
    scale = limit / longest
    size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
    return image.resize(size, Image.Resampling.LANCZOS)


def land_mask(mask: Optional[Image.Image], size: Tuple[int, int]) -> Optional[np.ndarray]:
    if mask is None:
        return None
    gray = mask.convert("L")
    if gray.size != size:
        gray = gray.resize(size, Image.Resampling.BILINEAR)
    landed = np.where(np.asarray(gray) >= LANDED, 255, 0).astype(np.uint8)
    return landed if landed.any() else None


def land_matte(mask: Optional[Image.Image], size: Tuple[int, int]) -> Optional[np.ndarray]:
    if mask is None:
        return None
    gray = mask.convert("L")
    if gray.size != size:
        gray = gray.resize(size, Image.Resampling.BILINEAR)
    landed = np.asarray(gray).copy()
    return landed if (landed >= LANDED).any() else None


def grow_region(region: Optional[np.ndarray], grow: float, shadow: bool = False) -> Optional[np.ndarray]:
    if region is None or grow <= 0:
        return region
    marked = region >= LANDED
    box = mask_bbox(marked)
    if box is None:
        return region
    top, bottom, left, right = box
    factor = grow * (SHADOW_GROWTH if shadow else 1.0)
    cap = max(GROW_FLOOR, int(GROW_CAP_SHARE * max(region.shape[:2])))
    radius = min(cap, max(GROW_FLOOR, int(round(factor * max(bottom - top, right - left)))))
    grown = dilate_round(marked, radius)
    if shadow:
        grown = extend_down(grown, int(round(SHADOW_DROP * (bottom - top))))
    return np.where(grown, 255, 0).astype(np.uint8)


def feather_for(box: Tuple[int, int, int, int]) -> int:
    top, bottom, left, right = box
    return max(FEATHER_FLOOR, int(round(FEATHER_SHARE * max(bottom - top, right - left))))


def _bucket_for(aspect: float, buckets: Sequence[Sequence[int]]) -> Tuple[int, int]:
    best = min(buckets, key=lambda pair: abs(math.log(pair[0] / pair[1]) - math.log(aspect)))
    return int(best[0]), int(best[1])


def grow_to_bucket(
    box: Tuple[int, int, int, int], shape: Tuple[int, int], buckets: Sequence[Sequence[int]]
) -> Tuple[int, int, int, int]:
    height, width = shape[:2]
    top, bottom, left, right = box
    crop_h, crop_w = bottom - top, right - left
    target_w, target_h = _bucket_for(crop_w / crop_h, buckets)
    wanted = target_w / target_h
    if crop_w / crop_h < wanted:
        left, right = fit_axis(left, right, int(math.ceil(crop_h * wanted)), width)
    elif crop_w / crop_h > wanted:
        top, bottom = fit_axis(top, bottom, int(math.ceil(crop_w / wanted)), height)
    return top, bottom, left, right


def plan(
    shape: Tuple[int, ...],
    region: Optional[np.ndarray],
    strategy: str,
    context: float,
    area: int,
    multiple: int,
    buckets: Optional[Sequence[Sequence[int]]] = None,
    min_side: int = MIN_SIDE,
    feather: float = 1.0,
    matte: bool = False,
) -> Geometry:
    height, width = shape[0], shape[1]
    has_region = region is not None and bool((region >= LANDED).any())
    full = (0, height, 0, width)
    whole = strategy == "whole" or not has_region
    box = full
    if not whole:
        box = scaled_crop_box((height, width), mask_bbox(np.where(region >= LANDED, 255, 0)), context, min_side)
        if (box[1] - box[0]) * (box[3] - box[2]) > WHOLE_SHARE * height * width:
            box, whole = full, True
    if buckets and strategy != "mask":
        box = full if whole else grow_to_bucket(box, (height, width), buckets)
    crop_h, crop_w = box[1] - box[0], box[3] - box[2]
    if strategy == "mask":
        work_h, work_w = working_size(height, width, area, multiple)
    elif buckets:
        work_w, work_h = _bucket_for(crop_w / crop_h, buckets)
    else:
        work_h, work_w = working_size(crop_h, crop_w, area, multiple)
    return Geometry(
        strategy=strategy,
        box=box,
        working=(work_w, work_h),
        feather_px=max(1, int(round(feather_for(box) * feather))),
        whole=whole,
        has_region=has_region,
        soft=crop_h * crop_w > SOFT_FACTOR * work_w * work_h,
        picture=(width, height),
        matte=matte and has_region,
    )


def prepare(
    picture: np.ndarray, region: Optional[np.ndarray], geometry: Geometry, with_mask: bool = False
) -> Prepared:
    top, bottom, left, right = geometry.box
    base = picture[top:bottom, left:right]
    region_crop = None if region is None or not geometry.has_region else region[top:bottom, left:right]
    if geometry.strategy == "mask":
        full = (
            np.where(region >= LANDED, 255, 0).astype(np.uint8)
            if geometry.has_region
            else np.full(picture.shape[:2], 255, dtype=np.uint8)
        )
        return Prepared(Image.fromarray(picture), Image.fromarray(full), base, region_crop)
    work_w, work_h = geometry.working
    cut_mask = None
    if with_mask:
        source = region_crop if region_crop is not None else np.zeros(base.shape[:2], dtype=np.uint8)
        resized = resample_image(source, work_w, work_h)
        cut_mask = Image.fromarray(np.where(resized >= LANDED, 255, 0).astype(np.uint8))
    return Prepared(Image.fromarray(resample_image(base, work_w, work_h)), cut_mask, base, region_crop)


def stitch(base: np.ndarray, region_crop: Optional[np.ndarray], result: Image.Image, geometry: Geometry) -> np.ndarray:
    top, bottom, left, right = geometry.box
    content_rgb = np.asarray(result.convert("RGB"))
    mask = region_crop if region_crop is not None else np.zeros(base.shape[:2], dtype=np.uint8)
    if geometry.strategy == "mask":
        width, height = geometry.picture
        content = resample_image(content_rgb, width, height)[top:bottom, left:right]
        if geometry.matte:
            return matte_patch(content, mask)
        return mask_patch(content, mask, geometry.feather_px)
    content = resample_image(content_rgb, right - left, bottom - top)
    if geometry.matte:
        return matte_patch(content, mask)
    return region_patch(base, content, mask, geometry.feather_px, True)


def cutout(result: Image.Image, picture: Tuple[int, int]) -> np.ndarray:
    rgba = result.convert("RGBA")
    if rgba.size != tuple(picture):
        rgba = rgba.resize(tuple(picture), Image.Resampling.LANCZOS)
    return np.asarray(rgba).copy()


def context_from(form_data: Dict[str, Any], default: float) -> float:
    raw = form_data.get("region_context")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    return value if 0.0 <= value <= 4.0 else default
