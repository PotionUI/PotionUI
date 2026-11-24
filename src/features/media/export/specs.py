from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

IMAGE_FORMATS = ("png", "jpeg", "webp", "avif")
VIDEO_FORMATS = ("mp4_h264", "mp4_h265", "mp4_av1", "mov_prores", "gif", "png_sequence_zip")
QUALITY_LEVELS = ("draft", "standard", "high", "lossless")

IMAGE_EXTENSIONS = {"png": "png", "jpeg": "jpg", "webp": "webp", "avif": "avif"}
VIDEO_EXTENSIONS = {
    "mp4_h264": "mp4",
    "mp4_h265": "mp4",
    "mp4_av1": "mp4",
    "mov_prores": "mov",
    "gif": "gif",
    "png_sequence_zip": "zip",
}

CRF_BY_CODEC = {
    "h264": {"draft": 28, "standard": 23, "high": 18, "lossless": 0},
    "h265": {"draft": 31, "standard": 26, "high": 20, "lossless": 0},
    "av1": {"draft": 45, "standard": 34, "high": 26, "lossless": 0},
}

PRORES_PROFILE_BY_LEVEL = {"draft": 0, "standard": 2, "high": 3, "lossless": 4}

GIF_COLOURS_BY_LEVEL = {"draft": 64, "standard": 128, "high": 192, "lossless": 256}

GIF_DEFAULT_FPS = 15.0

IMAGE_QUALITY_RANGE = (1, 100)
IMAGE_SCALE_RANGE = (0.05, 4.0)
MAX_VIDEO_DIMENSION = 16384

_HEX_COLOUR = re.compile(r"^#?([0-9a-fA-F]{6})$")


class ConvertError(ValueError):
    pass


class FormatUnavailable(ConvertError):
    pass


class ConversionCancelled(Exception):
    pass


def parse_fill_colour(value: str) -> tuple[int, int, int]:
    match = _HEX_COLOUR.match((value or "").strip())
    if not match:
        raise ConvertError(f"Invalid fill colour: {value!r}")
    raw = match.group(1)
    return int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16)


def crf_for(codec: str, level: str) -> int:
    try:
        return CRF_BY_CODEC[codec][level]
    except KeyError as exc:
        raise ConvertError(f"No quality mapping for {codec}/{level}") from exc


@dataclass(frozen=True)
class ImageSpec:
    fmt: str
    quality: int = 90
    keep_alpha: bool = True
    fill_colour: str = "#ffffff"
    scale: float = 1.0

    def validate(self) -> None:
        if self.fmt not in IMAGE_FORMATS:
            raise ConvertError(f"Unsupported image format: {self.fmt}")
        low, high = IMAGE_QUALITY_RANGE
        if not low <= int(self.quality) <= high:
            raise ConvertError(f"Quality must be {low}-{high}")
        low_s, high_s = IMAGE_SCALE_RANGE
        if not low_s <= float(self.scale) <= high_s:
            raise ConvertError(f"Scale must be {low_s}-{high_s}")
        parse_fill_colour(self.fill_colour)

    def options(self) -> dict:
        return {
            "kind": "image",
            "fmt": self.fmt,
            "quality": int(self.quality),
            "keep_alpha": bool(self.keep_alpha),
            "fill": self.fill_colour.lstrip("#").lower(),
            "scale": round(float(self.scale), 4),
        }

    @property
    def extension(self) -> str:
        return IMAGE_EXTENSIONS[self.fmt]


@dataclass(frozen=True)
class VideoSpec:
    fmt: str
    quality_level: str = "standard"
    fps: Optional[float] = None
    size: Optional[tuple[int, int]] = None
    include_audio: bool = True
    keep_alpha: bool = False

    def validate(self) -> None:
        if self.fmt not in VIDEO_FORMATS:
            raise ConvertError(f"Unsupported video format: {self.fmt}")
        if self.quality_level not in QUALITY_LEVELS:
            raise ConvertError(f"Unknown quality level: {self.quality_level}")
        if self.fps is not None and not 0 < float(self.fps) <= 240:
            raise ConvertError("fps must be between 0 and 240")
        if self.size is not None:
            width, height = self.size
            if not (0 < int(width) <= MAX_VIDEO_DIMENSION and 0 < int(height) <= MAX_VIDEO_DIMENSION):
                raise ConvertError("Invalid output size")

    def options(self) -> dict:
        return {
            "kind": "video",
            "fmt": self.fmt,
            "level": self.quality_level,
            "fps": None if self.fps is None else round(float(self.fps), 3),
            "size": None if self.size is None else [int(self.size[0]), int(self.size[1])],
            "audio": bool(self.include_audio),
            "alpha": bool(self.keep_alpha),
        }

    @property
    def extension(self) -> str:
        return VIDEO_EXTENSIONS[self.fmt]
