import base64
import math
import re
from decimal import Decimal, InvalidOperation
from pathlib import PurePosixPath
from typing import Any, Dict, List, Mapping, Optional, Tuple
from urllib.parse import urlsplit

from src.plugin_api.cloud import EXTRA_PARAM_PREFIX, CloudCost, CloudError

PIXELS = {"1K": 1024 * 1024, "2K": 2048 * 2048}
CREDIT_USD = Decimal("0.01")
MEDIA_TYPES = {"jpeg": "image/jpeg", "jpg": "image/jpeg", "png": "image/png", "webp": "image/webp"}
DEFAULT_MEDIA_TYPE = "image/jpeg"
USER_LIMIT = 256
REFERENCE_FIELDS = ("input_image",) + tuple(f"input_image_{index}" for index in range(2, 11))
API_HOST = re.compile(r"^api(\.[a-z0-9-]+)?\.bfl\.(ai|ml)$")
QUEUED = frozenset({"pending", "queued"})
READY = "ready"
ERROR = "error"
NOT_FOUND = "task not found"
REQUEST_MODERATED = "request moderated"
CONTENT_MODERATED = "content moderated"
REQUEST_MODERATED_MESSAGE = "BFL's content filter refused this prompt or picture. Try a different prompt or picture."
CONTENT_MODERATED_MESSAGE = "BFL's content filter blocked the finished picture. Try a different prompt."


def _ratio(value: Any) -> Optional[float]:
    match = re.match(r"^\s*(\d+(?:\.\d+)?)\s*:\s*(\d+(?:\.\d+)?)\s*$", str(value or ""))
    if not match or float(match.group(2)) == 0:
        return None
    ratio = float(match.group(1)) / float(match.group(2))
    return ratio if math.isfinite(ratio) and ratio > 0 else None


def dimensions(aspect_ratio: Any, resolution: Any, raw: Mapping[str, Any]) -> Optional[Tuple[int, int]]:
    ratio = _ratio(aspect_ratio)
    if ratio is None:
        return None
    pixels = PIXELS.get(str(resolution or "1K").upper(), PIXELS["1K"])
    step = int(raw.get("step") or 16)
    low = int(math.ceil(int(raw.get("min_side") or 64) / step) * step)
    high = int(int(raw.get("max_side") or 2048) // step * step)

    def snap(value: float) -> int:
        return int(min(max(round(value / step) * step, low), high))

    width = snap(math.sqrt(pixels * ratio))
    height = snap(width / ratio)
    width = snap(height * ratio)
    while width * height > pixels and (width > low or height > low):
        if width >= height and width > low:
            width -= step
        else:
            height -= step
    return width, height


def _encoded(path: Any) -> str:
    return base64.b64encode(path.read_bytes()).decode("ascii")


def build_body(request: Any, user_ref: Optional[str]) -> Dict[str, Any]:
    raw = request.model.raw or {}
    shape = raw.get("shape")
    params = dict(request.params)
    body: Dict[str, Any] = {"prompt": request.prompt}
    aspect = params.pop("aspect_ratio", None)
    resolution = params.pop("resolution", None)
    if shape == "flux3":
        if aspect:
            body["aspect_ratio"] = aspect
        if resolution:
            body["resolution"] = resolution
    elif shape == "aspect_ratio":
        if aspect and aspect != "auto":
            body["aspect_ratio"] = aspect
    else:
        size = dimensions(aspect, resolution, raw)
        if size is not None:
            body["width"], body["height"] = size
    if request.seed is not None and raw.get("seed", True):
        body["seed"] = int(request.seed)
    output_format = params.pop("output_format", None)
    if output_format and raw.get("output_format", True):
        body["output_format"] = output_format
    enhance = params.pop("enhance_prompt", None)
    if isinstance(enhance, bool):
        if raw.get("upsampling") == "disable_pup":
            body["disable_pup"] = not enhance
        elif raw.get("upsampling") == "prompt_upsampling":
            body["prompt_upsampling"] = enhance
    for name in ("guidance", "steps"):
        value = params.pop(name, None)
        if value is not None:
            body[name] = int(value) if name == "steps" else float(value)
    for name, value in params.items():
        if name.startswith(EXTRA_PARAM_PREFIX) and value is not None and value != "":
            wire = name[len(EXTRA_PARAM_PREFIX):]
            body[wire] = int(value) if wire == "safety_tolerance" else value
    references = list(request.inputs.get("reference") or []) if request.task == "img_edit" else []
    limit = int(raw.get("references") or 0)
    if references:
        if limit and len(references) > limit:
            raise CloudError(
                "invalid_request", f"This model takes at most {limit} pictures.",
                detail=f"{len(references)} references for {request.model.provider_model_id}", request_sent=False,
            )
        encoded = [_encoded(media.path) for media in references]
        if shape == "flux3":
            body["images"] = encoded
        else:
            body.update(zip(REFERENCE_FIELDS, encoded))
    if user_ref and raw.get("user", True):
        body["user"] = user_ref[:USER_LIMIT]
    return body


def credits_cost(credits: Any, payload: Mapping[str, Any], source_field: str) -> Optional[CloudCost]:
    if credits is None or isinstance(credits, bool):
        return None
    try:
        amount = Decimal(str(credits))
    except (InvalidOperation, ValueError):
        return None
    if not amount.is_finite() or amount < 0:
        return None
    detail: Dict[str, Any] = {"credits": str(amount), "reported_by": source_field}
    for name in ("input_mp", "output_mp"):
        if payload.get(name) is not None:
            detail[name] = str(payload[name])
    return CloudCost(amount_usd=amount * CREDIT_USD, source="provider", detail=detail)


def media_type_for(output_format: Any, url: str) -> str:
    if isinstance(output_format, str) and output_format.lower() in MEDIA_TYPES:
        return MEDIA_TYPES[output_format.lower()]
    suffix = PurePosixPath(urlsplit(url).path).suffix.lstrip(".").lower()
    return MEDIA_TYPES.get(suffix, DEFAULT_MEDIA_TYPE)


def is_bfl_api_url(url: str) -> bool:
    parts = urlsplit(url)
    return parts.scheme == "https" and bool(API_HOST.match((parts.hostname or "").lower())) and parts.port in (None, 443)


def origin_of(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def polling_target(http: Any, payload: Mapping[str, Any], job_id: str) -> Dict[str, Any]:
    url = payload.get("polling_url")
    if isinstance(url, str) and url:
        resolved = http.resolve(url)
        if http.owns_url(resolved):
            return {"target": resolved, "origin": None}
        if is_bfl_api_url(resolved) and is_bfl_api_url(http.base_url):
            return {"target": resolved, "origin": origin_of(resolved)}
    return {"target": "/get_result", "params": {"id": job_id}, "origin": None}


def progress_of(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    if number > 1:
        number /= 100.0
    return min(max(number, 0.0), 1.0)


def moderation_reasons(payload: Mapping[str, Any]) -> List[str]:
    details = payload.get("details")
    if not isinstance(details, dict):
        return []
    reasons = details.get("Moderation Reasons") or details.get("moderation_reasons") or []
    if isinstance(reasons, str):
        reasons = [reasons]
    return [str(reason)[:80] for reason in reasons if isinstance(reason, (str, int))][:10] if isinstance(reasons, list) else []


def moderation_error(status: str, payload: Mapping[str, Any], job_id: str) -> CloudError:
    reasons = moderation_reasons(payload)
    listed = f"; reasons: {', '.join(reasons)}" if reasons else ""
    if status == CONTENT_MODERATED:
        return CloudError(
            "refused", CONTENT_MODERATED_MESSAGE,
            detail=f"Content Moderated: BFL made the picture and its output filter withheld it, so the job may still be billed (task {job_id}{listed})",
        )
    return CloudError(
        "refused", REQUEST_MODERATED_MESSAGE,
        detail=f"Request Moderated: BFL's input filter refused the prompt or a picture before drawing (task {job_id}{listed})",
    )


def error_reason(payload: Mapping[str, Any]) -> str:
    for key in ("error", "details", "message", "detail"):
        value = payload.get(key)
        if isinstance(value, dict):
            value = value.get("message") or value.get("error") or ", ".join(f"{k}: {v}" for k, v in value.items())
        if value:
            return str(value)[:500]
    return "BFL gave no reason"


def body_message(body: Any) -> str:
    if isinstance(body, dict):
        detail = body.get("detail", body.get("message", body.get("error")))
        if isinstance(detail, list):
            parts = []
            for item in detail[:5]:
                if isinstance(item, dict):
                    where = ".".join(str(part) for part in item.get("loc") or [] if part != "body")
                    parts.append(f"{where}: {item.get('msg')}" if where else str(item.get("msg")))
                else:
                    parts.append(str(item))
            return "; ".join(parts)
        if detail is not None:
            return str(detail)
    return str(body)[:300]
