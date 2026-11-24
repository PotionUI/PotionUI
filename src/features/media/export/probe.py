from __future__ import annotations

import functools
import re
import subprocess
from dataclasses import dataclass
from typing import Optional

from src.features.media.export.specs import IMAGE_FORMATS, VIDEO_FORMATS

PROBE_TIMEOUT_SECONDS = 20

_ENCODER_LINE = re.compile(r"^\s*[VAS][A-Za-z.]{5}\s+(\S+)\s")

FORMAT_ENCODERS = {
    "mp4_h264": ("libx264",),
    "mp4_h265": ("libx265",),
    "mp4_av1": ("libsvtav1", "libaom-av1"),
    "mov_prores": ("prores_ks",),
    "gif": ("gif",),
    "png_sequence_zip": ("png",),
}

UNAVAILABLE_REASONS = {
    "mp4_h264": "No H.264 encoder in this ffmpeg build",
    "mp4_h265": "No H.265 encoder in this ffmpeg build",
    "mp4_av1": "No AV1 encoder in this ffmpeg build",
    "mov_prores": "No ProRes encoder in this ffmpeg build",
    "gif": "No GIF encoder in this ffmpeg build",
    "png_sequence_zip": "No PNG encoder in this ffmpeg build",
}

NO_FFMPEG_REASON = "ffmpeg is not installed"


@dataclass(frozen=True)
class FormatSupport:
    fmt: str
    available: bool
    encoder: Optional[str] = None
    reason: Optional[str] = None

    def to_dict(self) -> dict:
        return {"format": self.fmt, "available": self.available, "encoder": self.encoder, "reason": self.reason}


def parse_encoders(text: str) -> frozenset[str]:
    names = set()
    for line in (text or "").splitlines():
        match = _ENCODER_LINE.match(line)
        if match:
            names.add(match.group(1))
    return frozenset(names)


def video_support(encoders: frozenset[str]) -> dict[str, FormatSupport]:
    result = {}
    for fmt in VIDEO_FORMATS:
        chosen = next((name for name in FORMAT_ENCODERS[fmt] if name in encoders), None)
        if chosen:
            result[fmt] = FormatSupport(fmt, True, encoder=chosen)
        else:
            result[fmt] = FormatSupport(fmt, False, reason=UNAVAILABLE_REASONS[fmt])
    return result


def no_ffmpeg_support() -> dict[str, FormatSupport]:
    return {fmt: FormatSupport(fmt, False, reason=NO_FFMPEG_REASON) for fmt in VIDEO_FORMATS}


@functools.lru_cache(maxsize=8)
def _encoders_for(ffmpeg: str) -> frozenset[str]:
    try:
        result = subprocess.run(
            [ffmpeg, "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=PROBE_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        return frozenset()
    return parse_encoders(result.stdout)


def probe_video_support(ffmpeg: Optional[str]) -> dict[str, FormatSupport]:
    if not ffmpeg:
        return no_ffmpeg_support()
    encoders = _encoders_for(ffmpeg)
    if not encoders:
        return {
            fmt: FormatSupport(fmt, False, reason="ffmpeg could not be queried for encoders")
            for fmt in VIDEO_FORMATS
        }
    return video_support(encoders)


def clear_probe_cache() -> None:
    _encoders_for.cache_clear()


def probe_image_support() -> dict[str, FormatSupport]:
    from PIL import features

    checks = {"png": True, "jpeg": features.check("jpg"), "webp": features.check("webp"), "avif": features.check("avif")}
    result = {}
    for fmt in IMAGE_FORMATS:
        if checks[fmt]:
            result[fmt] = FormatSupport(fmt, True, encoder="pillow")
        else:
            result[fmt] = FormatSupport(fmt, False, reason=f"This Pillow build has no {fmt.upper()} support")
    return result
