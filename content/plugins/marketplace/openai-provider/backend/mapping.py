import base64
import io
import math
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

import aiohttp
from PIL import Image

from src.plugin_api.cloud import CloudArtifact, CloudCost, CloudError, CloudResult

from .catalog import MAX_IMAGES, MILLION

EDGE = 16
MAX_EDGE = 3840
MAX_PIXELS = 8_294_400
SHORT_EDGE = {"1K": 1024, "2K": 2048}
FIXED_SIZES = {"1:1": "1024x1024", "3:2": "1536x1024", "2:3": "1024x1536"}
MEDIA_TYPES = {"png": "image/png", "jpeg": "image/jpeg", "webp": "image/webp"}
TRANSPARENT_FORMATS = ("png", "webp")
COMPRESSED_FORMATS = ("jpeg", "webp")
MAX_MASK_BYTES = 4_000_000


def _not_sent(message: str, detail: str) -> CloudError:
    return CloudError("invalid_request", message, detail=detail, request_sent=False)


def _ratio(aspect: str) -> Optional[float]:
    left, _, right = aspect.partition(":")
    try:
        width, height = float(left), float(right)
    except ValueError:
        return None
    if width <= 0 or height <= 0:
        return None
    return width / height


def _floor(value: float) -> int:
    return max(EDGE, int(value // EDGE) * EDGE)


def _round(value: float) -> int:
    return max(EDGE, int(round(value / EDGE)) * EDGE)


def flexible_size(ratio: float, resolution: str) -> Tuple[int, int]:
    if resolution == "4K":
        width, height = _floor(math.sqrt(MAX_PIXELS * ratio)), _floor(math.sqrt(MAX_PIXELS / ratio))
    else:
        short = SHORT_EDGE.get(resolution, SHORT_EDGE["1K"])
        width, height = (_round(short * ratio), short) if ratio >= 1 else (short, _round(short / ratio))
    longest = max(width, height)
    if longest > MAX_EDGE:
        scale = MAX_EDGE / longest
        width, height = (MAX_EDGE, _floor(height * scale)) if width >= height else (_floor(width * scale), MAX_EDGE)
    if width * height > MAX_PIXELS:
        scale = math.sqrt(MAX_PIXELS / (width * height))
        width, height = _floor(width * scale), _floor(height * scale)
    return width, height


def wire_size(params: Mapping[str, Any], flexible: bool) -> Optional[str]:
    aspect = str(params.get("aspect_ratio") or "auto")
    if aspect == "auto":
        return None
    if not flexible:
        size = FIXED_SIZES.get(aspect)
        if size is None:
            raise _not_sent("This model does not make that aspect ratio.", f"aspect ratio {aspect!r} on a fixed size model")
        return size
    ratio = _ratio(aspect)
    if ratio is None or not 1 / 3 <= ratio <= 3:
        raise _not_sent("This model does not make that aspect ratio.", f"aspect ratio {aspect!r}")
    width, height = flexible_size(ratio, str(params.get("resolution") or "1K"))
    return f"{width}x{height}"


def request_fields(request: Any, moderation: str, user: Optional[str]) -> Dict[str, Any]:
    raw = request.model.raw or {}
    offered = {param.name for param in request.model.params}
    params = {name: value for name, value in request.params.items() if name in offered}
    fields: Dict[str, Any] = {"model": request.model.provider_model_id, "prompt": request.prompt}
    if request.count > 1:
        fields["n"] = request.count
    size = wire_size(params, raw.get("sizes") == "flexible")
    if size:
        fields["size"] = size
    quality = params.get("quality")
    if quality:
        fields["quality"] = quality
    output_format = str(params.get("output_format") or "png")
    if params.get("output_format"):
        fields["output_format"] = output_format
    if params.get("background") is True:
        if not raw.get("transparent"):
            raise _not_sent("This model cannot make a transparent background.", "background transparent on an opaque model")
        if output_format not in TRANSPARENT_FORMATS:
            raise _not_sent(
                "A transparent background needs the PNG or WebP file format.",
                f"background transparent with output_format {output_format}",
            )
        fields["background"] = "transparent"
    compression = params.get("x.output_compression")
    if compression is not None and output_format in COMPRESSED_FORMATS:
        fields["output_compression"] = int(compression)
    fidelity = params.get("x.input_fidelity")
    if fidelity and request.task == "img_edit":
        fields["input_fidelity"] = fidelity
    if moderation and moderation != "auto":
        fields["moderation"] = moderation
    if user:
        fields["user"] = user
    return fields


def _mask_png(path: Path, size: Tuple[int, int]) -> bytes:
    with Image.open(path) as source:
        painted = source.convert("L")
    if painted.size != size:
        painted = painted.resize(size, Image.NEAREST)
    alpha = painted.point(lambda value: 0 if value >= 128 else 255)
    mask = Image.new("RGBA", size, (0, 0, 0, 255))
    mask.putalpha(alpha)
    buffer = io.BytesIO()
    mask.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def _image_size(path: Path) -> Tuple[int, int]:
    with Image.open(path) as image:
        return image.size


def edit_form(request: Any, moderation: str, user: Optional[str]) -> aiohttp.FormData:
    images = list(request.inputs.get("reference") or [])
    if not images:
        raise _not_sent("Add a picture to edit.", "no reference images for an edit")
    if len(images) > MAX_IMAGES:
        raise _not_sent(f"OpenAI takes at most {MAX_IMAGES} pictures in one edit.", f"{len(images)} images")
    masks = list(request.inputs.get("mask") or [])
    form = aiohttp.FormData()
    for name, value in request_fields(request, moderation, user).items():
        form.add_field(name, str(value))
    for index, media in enumerate(images):
        extension = media.media_type.rsplit("/", 1)[-1].replace("jpeg", "jpg")
        form.add_field("image[]", media.path.read_bytes(), filename=f"image_{index}.{extension}", content_type=media.media_type)
    if masks:
        mask = _mask_png(masks[0].path, _image_size(images[0].path))
        if len(mask) > MAX_MASK_BYTES:
            raise _not_sent("The painted mask is too large for OpenAI.", f"mask is {len(mask)} bytes")
        form.add_field("mask", mask, filename="mask.png", content_type="image/png")
    return form


def usage_cost(usage: Any, rates: Mapping[str, str]) -> Optional[CloudCost]:
    if not isinstance(usage, dict) or not rates:
        return None

    def count(value: Any) -> int:
        return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0

    details = usage.get("input_tokens_details") if isinstance(usage.get("input_tokens_details"), dict) else {}
    input_total = count(usage.get("input_tokens"))
    image_in = count(details.get("image_tokens"))
    text_in = count(details.get("text_tokens")) if "text_tokens" in details else max(0, input_total - image_in)
    image_out = count(usage.get("output_tokens"))
    if not (text_in or image_in or image_out):
        return None
    try:
        amount = sum(
            (Decimal(tokens) * Decimal(rates[name]) / MILLION for name, tokens in (
                ("text_input", text_in), ("image_input", image_in), ("image_output", image_out)
            ) if tokens),
            Decimal(0),
        )
    except (KeyError, InvalidOperation):
        return None
    return CloudCost(
        amount_usd=amount,
        source="estimate",
        detail={
            "basis": "tokens reported by OpenAI times the listed price",
            "tokens": {"text_input": text_in, "image_input": image_in, "image_output": image_out},
            "usd_per_million_tokens": dict(rates),
        },
    )


def parse_result(payload: Any, request: Any, request_id: Optional[str]) -> CloudResult:
    if not isinstance(payload, dict):
        raise CloudError("failed", "OpenAI sent an answer that could not be read.", detail="response is not an object")
    output_format = str(payload.get("output_format") or request.params.get("output_format") or "png").lower()
    media_type = MEDIA_TYPES.get(output_format, "image/png")
    artifacts: List[CloudArtifact] = []
    for index, entry in enumerate(payload.get("data") or []):
        if not isinstance(entry, dict) or not isinstance(entry.get("b64_json"), str):
            continue
        try:
            data = base64.b64decode(entry["b64_json"], validate=False)
        except ValueError:
            continue
        if data:
            artifacts.append(CloudArtifact(modality="image", index=index, data=data, media_type=media_type))
    if not artifacts:
        raise CloudError("failed", "OpenAI returned no images.", detail="no b64_json entries in data")
    return CloudResult(
        artifacts=tuple(artifacts),
        cost=usage_cost(payload.get("usage"), (request.model.raw or {}).get("rates") or {}),
        seed_used=None,
        provider_job_id=request_id,
    )
