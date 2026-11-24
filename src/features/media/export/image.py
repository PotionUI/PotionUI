from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageOps

from src.features.media.export.probe import probe_image_support
from src.features.media.export.specs import (
    FormatUnavailable,
    ImageSpec,
    parse_fill_colour,
)

PNG_COMPRESS_LEVEL = 6
WEBP_LOSSLESS_QUALITY = 100
ALPHA_FORMATS = ("png", "webp", "avif")


def _has_alpha(image: Image.Image) -> bool:
    return image.mode in ("RGBA", "LA", "PA") or (image.mode == "P" and "transparency" in image.info)


def _flatten(image: Image.Image, fill: tuple[int, int, int]) -> Image.Image:
    rgba = image.convert("RGBA")
    background = Image.new("RGBA", rgba.size, fill + (255,))
    background.alpha_composite(rgba)
    return background.convert("RGB")


def convert_image(source: Path, destination: Path, spec: ImageSpec) -> Path:
    spec.validate()
    support = probe_image_support()[spec.fmt]
    if not support.available:
        raise FormatUnavailable(support.reason or f"{spec.fmt} unavailable")
    with Image.open(source) as opened:
        image = ImageOps.exif_transpose(opened)
        image.load()
    alpha = _has_alpha(image)
    wants_alpha = alpha and spec.keep_alpha and spec.fmt in ALPHA_FORMATS
    if wants_alpha:
        image = image.convert("RGBA")
    elif alpha:
        image = _flatten(image, parse_fill_colour(spec.fill_colour))
    elif image.mode not in ("RGB", "L"):
        image = image.convert("RGB")
    if spec.scale != 1.0:
        width = max(1, round(image.width * spec.scale))
        height = max(1, round(image.height * spec.scale))
        image = image.resize((width, height), Image.Resampling.LANCZOS)
    save_kwargs: dict = {}
    if spec.fmt == "png":
        save_kwargs = {"compress_level": PNG_COMPRESS_LEVEL}
        pillow_format = "PNG"
    elif spec.fmt == "jpeg":
        if image.mode == "L":
            image = image.convert("RGB")
        save_kwargs = {"quality": int(spec.quality), "optimize": True, "subsampling": 0 if spec.quality >= 90 else 2}
        pillow_format = "JPEG"
    elif spec.fmt == "webp":
        save_kwargs = {"quality": int(spec.quality), "method": 4}
        if int(spec.quality) >= WEBP_LOSSLESS_QUALITY:
            save_kwargs["lossless"] = True
        pillow_format = "WEBP"
    else:
        save_kwargs = {"quality": int(spec.quality)}
        pillow_format = "AVIF"
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with open(destination, "wb") as handle:
        image.save(handle, format=pillow_format, **save_kwargs)
    return destination
