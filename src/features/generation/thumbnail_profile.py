"""Thumbnail profiles: which sizes get rendered, and how expensive each one is.

Animated WebP has no inter-frame compression, so a video's preview set is the
single largest thing PotionUI writes per generation. The five settings below
put that under admin control instead of hard-coding it in the two generators
(`generate_thumbnails`, `generate_video_thumbnails`), and `profile_hash` lets
the regeneration job tell which stored rows were produced under the settings
in force now.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

SIZE_WIDTHS: Dict[str, int] = {
    "small": 480,
    "medium": 768,
    "large": 1024,
}

SIZE_ORDER: Tuple[str, ...] = ("small", "medium", "large")

SETTING_SIZES = "thumbnail_sizes"
SETTING_VIDEO_FPS = "thumbnail_video_fps"
SETTING_VIDEO_SECONDS = "thumbnail_video_seconds"
SETTING_VIDEO_QUALITY = "thumbnail_video_quality"
SETTING_IMAGE_QUALITY = "thumbnail_image_quality"

SETTING_KEYS: Tuple[str, ...] = (
    SETTING_SIZES,
    SETTING_VIDEO_FPS,
    SETTING_VIDEO_SECONDS,
    SETTING_VIDEO_QUALITY,
    SETTING_IMAGE_QUALITY,
)

VIDEO_FPS_RANGE = (1, 60)
VIDEO_SECONDS_RANGE = (1, 10)
QUALITY_RANGE = (1, 100)


@dataclass(frozen=True)
class ThumbnailProfile:
    """The complete thumbnail rendering configuration."""

    sizes: Tuple[str, ...]
    video_fps: int
    video_seconds: int
    video_quality: int
    image_quality: int

    def widths(self) -> Tuple[Tuple[str, int], ...]:
        """`(size_name, width)` for every size in this profile, small first."""
        return tuple((name, SIZE_WIDTHS[name]) for name in SIZE_ORDER if name in self.sizes)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sizes": list(self.sizes),
            "video_fps": self.video_fps,
            "video_seconds": self.video_seconds,
            "video_quality": self.video_quality,
            "image_quality": self.image_quality,
        }


PROFILES: Dict[str, ThumbnailProfile] = {
    "compact": ThumbnailProfile(("small",), 8, 2, 40, 75),
    "balanced": ThumbnailProfile(("medium",), 12, 3, 50, 85),
    "full": ThumbnailProfile(("small", "medium", "large"), 24, 3, 50, 85),
}

DEFAULT_PROFILE = PROFILES["balanced"]


def _sizes(value: Any) -> Tuple[str, ...]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return DEFAULT_PROFILE.sizes
    if not isinstance(value, (list, tuple)):
        return DEFAULT_PROFILE.sizes
    picked = tuple(name for name in SIZE_ORDER if name in value)
    return picked or DEFAULT_PROFILE.sizes


def _bounded_int(value: Any, low: int, high: int, fallback: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return fallback
    return number if low <= number <= high else fallback


def load_thumbnail_profile(settings) -> ThumbnailProfile:
    """The profile the five SYSTEM settings describe.

    Every field falls back to the balanced default on its own, so a single
    legacy or out-of-range value never costs the other four.
    """
    try:
        raw_sizes = settings.get_setting(SETTING_SIZES, list(DEFAULT_PROFILE.sizes))
        raw_fps = settings.get_setting(SETTING_VIDEO_FPS, DEFAULT_PROFILE.video_fps)
        raw_seconds = settings.get_setting(SETTING_VIDEO_SECONDS, DEFAULT_PROFILE.video_seconds)
        raw_video_quality = settings.get_setting(SETTING_VIDEO_QUALITY, DEFAULT_PROFILE.video_quality)
        raw_image_quality = settings.get_setting(SETTING_IMAGE_QUALITY, DEFAULT_PROFILE.image_quality)
    except Exception:
        logger.warning("Could not read thumbnail settings; using the balanced profile", exc_info=True)
        return DEFAULT_PROFILE

    return ThumbnailProfile(
        sizes=_sizes(raw_sizes),
        video_fps=_bounded_int(raw_fps, *VIDEO_FPS_RANGE, DEFAULT_PROFILE.video_fps),
        video_seconds=_bounded_int(raw_seconds, *VIDEO_SECONDS_RANGE, DEFAULT_PROFILE.video_seconds),
        video_quality=_bounded_int(raw_video_quality, *QUALITY_RANGE, DEFAULT_PROFILE.video_quality),
        image_quality=_bounded_int(raw_image_quality, *QUALITY_RANGE, DEFAULT_PROFILE.image_quality),
    )


def profile_hash(profile: ThumbnailProfile) -> str:
    """A stable fingerprint of the five values, stored on every row whose
    thumbnails were rendered under this profile."""
    canonical = json.dumps(
        {
            "sizes": sorted(profile.sizes),
            "video_fps": profile.video_fps,
            "video_seconds": profile.video_seconds,
            "video_quality": profile.video_quality,
            "image_quality": profile.image_quality,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha1(canonical.encode("utf-8")).hexdigest()


def match_profile(profile: ThumbnailProfile) -> str:
    """The named profile `profile` equals, or `"custom"`."""
    for name, candidate in PROFILES.items():
        if profile == candidate:
            return name
    return "custom"


# Bytes per pixel measured on a mixed sample of real output; an estimate for
# sizing a disk budget, not a prediction of any single file.
_IMAGE_WEBP_BYTES_PER_PIXEL = 0.10
_VIDEO_STATIC_JPG_BYTES_PER_PIXEL = 0.07
_ANIMATED_WEBP_BYTES_PER_PIXEL_FRAME = 0.06


def estimate_bytes(profile: ThumbnailProfile, images: int, videos: int) -> int:
    """Roughly how much disk `images` image and `videos` video thumbnail sets
    take under `profile`, assuming a square frame at each size's width."""
    image_scale = profile.image_quality / 85.0
    video_scale = profile.video_quality / 50.0
    frames = profile.video_fps * profile.video_seconds

    per_image = 0.0
    per_video = 0.0
    for _, width in profile.widths():
        area = float(width * width)
        per_image += area * _IMAGE_WEBP_BYTES_PER_PIXEL * image_scale
        per_video += area * _VIDEO_STATIC_JPG_BYTES_PER_PIXEL
        per_video += area * frames * _ANIMATED_WEBP_BYTES_PER_PIXEL_FRAME * video_scale

    return int(per_image * max(images, 0) + per_video * max(videos, 0))


def fallback_sizes(size: str) -> Tuple[str, ...]:
    """`size` first, then the sizes to try when it was never rendered."""
    chain = {
        "small": ("small", "medium", "large"),
        "medium": ("medium", "large", "small"),
        "large": ("large", "medium", "small"),
    }
    return chain.get(size, ())


def validate_setting(key: str, value: Any) -> Optional[str]:
    """The reason `value` is not acceptable for thumbnail setting `key`, or
    None. Returns None for any key this module does not own."""
    if key == SETTING_SIZES:
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError:
                return "must be a list of sizes"
        if not isinstance(value, (list, tuple)) or not value:
            return "must be a non-empty list of sizes"
        unknown = [name for name in value if name not in SIZE_WIDTHS]
        if unknown:
            return f"unknown size(s): {', '.join(str(name) for name in unknown)}"
        return None

    ranges = {
        SETTING_VIDEO_FPS: VIDEO_FPS_RANGE,
        SETTING_VIDEO_SECONDS: VIDEO_SECONDS_RANGE,
        SETTING_VIDEO_QUALITY: QUALITY_RANGE,
        SETTING_IMAGE_QUALITY: QUALITY_RANGE,
    }
    if key not in ranges:
        return None

    low, high = ranges[key]
    if isinstance(value, bool):
        return f"must be an integer between {low} and {high}"
    try:
        number = int(value)
    except (TypeError, ValueError):
        return f"must be an integer between {low} and {high}"
    if not low <= number <= high:
        return f"must be between {low} and {high}"
    return None
