import math
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple

from src.plugin_api.cloud import (
    EXTRA_PARAM_PREFIX,
    CloudArtifact,
    CloudCost,
    CloudError,
    CloudModelSpec,
    CloudResult,
    CloudStatus,
    MediaInputSpec,
    ParamSpec,
    PriceLine,
    is_canonical_param,
)

from .mapping import data_url

STATUS_STATES = {
    "pending": "queued",
    "queued": "queued",
    "in_progress": "running",
    "processing": "running",
    "running": "running",
    "completed": "succeeded",
    "succeeded": "succeeded",
    "failed": "failed",
    "error": "failed",
    "cancelled": "cancelled",
    "canceled": "cancelled",
    "expired": "expired",
}
MODERATION_WORDS = ("moderation", "safety", "policy", "blocked", "flagged", "not allowed")
FIRST_POLL_SECONDS = 5.0
POLL_SECONDS = 30.0
VIDEO_MEDIA_TYPE = "video/mp4"


def _strings(value: Any) -> List[str]:
    return [str(item) for item in value if isinstance(item, (str, int, float)) and not isinstance(item, bool)] if isinstance(value, list) else []


def _numbers(value: Any) -> List[float]:
    out: List[float] = []
    for item in value if isinstance(value, list) else []:
        if isinstance(item, bool):
            continue
        try:
            out.append(float(item))
        except (TypeError, ValueError):
            continue
    return out


def _duration_param(durations: List[float]) -> Optional[ParamSpec]:
    values = sorted(set(durations))
    if not values or values[0] <= 0:
        return None
    steps = {round(b - a, 6) for a, b in zip(values, values[1:])}
    whole = all(float(v).is_integer() for v in values)
    if len(values) >= 3 and len(steps) == 1 and whole:
        return ParamSpec(name="duration_s", kind="range", minimum=values[0], maximum=values[-1], step=steps.pop(), integer=True)
    return ParamSpec(name="duration_s", kind="enum", values=tuple(int(v) if v.is_integer() else v for v in values))


def _sku_lines(skus: Any) -> Tuple[PriceLine, ...]:
    entries: List[Tuple[str, Any]] = []
    if isinstance(skus, dict):
        entries = [(str(name), cost) for name, cost in skus.items()]
    elif isinstance(skus, list):
        for item in skus:
            if isinstance(item, dict):
                name = item.get("sku") or item.get("name") or item.get("billable") or "sku"
                entries.append((str(name), item.get("cost_usd", item.get("price", item.get("usd")))))
    lines: List[PriceLine] = []
    for name, cost in entries:
        if isinstance(cost, dict):
            cost = cost.get("cost_usd", cost.get("usd"))
        try:
            amount = Decimal(str(cost))
        except (InvalidOperation, ValueError):
            continue
        if not amount.is_finite() or amount < 0:
            continue
        unit = "second" if "second" in name.lower() else "sku"
        lines.append(PriceLine(unit=unit, usd=amount, applies_to=None if unit == "second" else name))
    return tuple(sorted(lines, key=lambda line: (line.unit, line.applies_to or "")))


def _single_number(value: Any) -> Optional[float]:
    numbers = _numbers(value) if isinstance(value, list) else _numbers([value])
    numbers = [number for number in numbers if math.isfinite(number) and number > 0]
    return numbers[0] if len(set(numbers)) == 1 else None


def _director(item: Dict[str, Any]) -> Dict[str, Any]:
    fps = _single_number(item.get("supported_fps")) or _single_number(item.get("fps"))
    if fps is None:
        return {}
    return {"limits": {"default_fps": int(fps) if fps.is_integer() else fps}}


def merge_director(base: Dict[str, Any], extra: Any) -> Dict[str, Any]:
    merged = dict(base or {})
    if not isinstance(extra, dict):
        return merged
    for key, value in extra.items():
        if key in ("limits", "modes") and isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = {**merged[key], **value}
        else:
            merged[key] = value
    return merged


def video_spec(item: Any) -> Optional[CloudModelSpec]:
    if not isinstance(item, dict):
        return None
    model_id = item.get("id")
    if not isinstance(model_id, str) or not model_id:
        return None
    architecture = item.get("architecture") if isinstance(item.get("architecture"), dict) else {}
    outputs = [str(mode).lower() for mode in architecture.get("output_modalities") or ["video"]]
    if "video" not in outputs:
        return None

    params: List[ParamSpec] = []
    duration = _duration_param(_numbers(item.get("supported_durations")))
    if duration is not None:
        params.append(duration)
    for name, key in (("resolution", "supported_resolutions"), ("aspect_ratio", "supported_aspect_ratios"), ("size", "supported_sizes")):
        values = _strings(item.get(key))
        if values:
            params.append(ParamSpec(name=name, kind="enum", values=tuple(dict.fromkeys(values))))
    if item.get("generate_audio") or item.get("supports_audio") or item.get("supports_generate_audio"):
        params.append(ParamSpec(name="generate_audio", kind="boolean"))
    for passthrough in item.get("allowed_passthrough_parameters") or []:
        if isinstance(passthrough, str) and passthrough:
            spec_name = f"{EXTRA_PARAM_PREFIX}{passthrough}"
            if is_canonical_param(spec_name) and all(p.name != spec_name for p in params):
                params.append(ParamSpec(name=spec_name, kind="text"))

    frames = set(_strings(item.get("supported_frame_images")))
    input_modalities = [str(mode).lower() for mode in architecture.get("input_modalities") or []]
    if not frames and "image" in input_modalities:
        frames = {"first_frame"}
    inputs: List[MediaInputSpec] = []
    for role in ("first_frame", "last_frame"):
        if role in frames:
            inputs.append(MediaInputSpec(role=role, modality="image", max_items=1, formats=("png", "jpeg", "webp"), tasks=frozenset({"img2video"})))
    reference_limit = item.get("max_input_references")
    if isinstance(reference_limit, int) and not isinstance(reference_limit, bool) and reference_limit >= 1:
        inputs.append(MediaInputSpec(
            role="reference", modality="image", max_items=reference_limit,
            formats=("png", "jpeg", "webp"), tasks=frozenset({"img2video"}),
        ))
    tasks = {"txt2video"} | ({"img2video"} if inputs else set())
    supported = _strings(item.get("supported_parameters")) or ["seed"]
    label = item.get("name") if isinstance(item.get("name"), str) and item.get("name") else model_id
    return CloudModelSpec(
        provider_model_id=model_id,
        label=label,
        vendor=model_id.split("/", 1)[0] if "/" in model_id else None,
        description=item.get("description") if isinstance(item.get("description"), str) else None,
        tasks=frozenset(tasks),
        outputs=frozenset({"video"}),
        params=tuple(sorted(params, key=lambda spec: spec.name)),
        inputs=tuple(inputs),
        pricing=_sku_lines(item.get("pricing_skus")),
        typical_seconds=120,
        deprecated_at=item.get("expiration_date") if isinstance(item.get("expiration_date"), str) else None,
        raw={"supported": supported},
        director=_director(item),
    )


def is_video_spec(spec: CloudModelSpec) -> bool:
    return "video" in spec.outputs


def build_video_body(request: Any, provider_only: List[str], user_ref: Optional[str]) -> Dict[str, Any]:
    supported = set((request.model.raw or {}).get("supported") or [])
    body: Dict[str, Any] = {"model": request.model.provider_model_id, "prompt": request.prompt}
    if request.seed is not None and "seed" in supported:
        body["seed"] = request.seed
    for name, value in request.params.items():
        if value is None or value == "":
            continue
        if name == "duration_s":
            body["duration"] = value
        elif name.startswith(EXTRA_PARAM_PREFIX):
            body[name[len(EXTRA_PARAM_PREFIX):]] = value
        elif name in ("resolution", "aspect_ratio", "size", "generate_audio"):
            body[name] = value
    frames = []
    for role in ("first_frame", "last_frame"):
        for media in request.inputs.get(role) or []:
            frames.append({"type": "image_url", "image_url": {"url": data_url(media.path, media.media_type)}, "frame_type": role})
    if frames:
        body["frame_images"] = frames
    references = request.inputs.get("reference") or []
    if references:
        body["input_references"] = [{"type": "image_url", "image_url": {"url": data_url(media.path, media.media_type)}} for media in references]
    if provider_only:
        body["provider"] = {"only": provider_only}
    if user_ref:
        body["user"] = user_ref
    return body


def polling_target(http: Any, payload: Dict[str, Any], job_id: str) -> str:
    url = payload.get("polling_url")
    if isinstance(url, str) and url and http.owns_url(http.resolve(url)):
        return url
    return f"/videos/{job_id}"


def _progress(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    if isinstance(value, int) or number > 1:
        number /= 100.0
    return min(max(number, 0.0), 1.0)


def _reason(payload: Dict[str, Any]) -> str:
    error = payload.get("error")
    if isinstance(error, dict):
        return str(error.get("message") or error.get("code") or "")[:500]
    return str(error or payload.get("message") or "")[:500]


def parse_status(http: Any, payload: Any, job_id: str) -> CloudStatus:
    if not isinstance(payload, dict):
        raise CloudError("failed", "OpenRouter sent an answer that could not be read.", detail="poll response is not an object")
    raw_state = str(payload.get("status") or "").lower()
    state = STATUS_STATES.get(raw_state, "running")
    position = payload.get("queue_position")
    position = position if isinstance(position, int) and not isinstance(position, bool) and position >= 0 else None
    if state == "succeeded":
        urls = [url for url in payload.get("unsigned_urls") or [] if isinstance(url, str) and url]
        if not urls:
            urls = [http.resolve(f"/videos/{job_id}/content?index=0")]
        artifacts = tuple(
            CloudArtifact(modality="video", index=index, url=http.resolve(url), media_type=VIDEO_MEDIA_TYPE)
            for index, url in enumerate(urls)
        )
        cost = None
        usage = payload.get("usage")
        if isinstance(usage, dict) and usage.get("cost") is not None:
            try:
                amount = Decimal(str(usage["cost"]))
                if amount.is_finite() and amount >= 0:
                    cost = CloudCost(amount_usd=amount, source="provider")
            except (InvalidOperation, ValueError):
                cost = None
        return CloudStatus(
            state="succeeded", progress=1.0,
            result=CloudResult(artifacts=artifacts, cost=cost, provider_job_id=job_id),
        )
    if state == "failed":
        reason = _reason(payload)
        if any(word in reason.lower() for word in MODERATION_WORDS):
            raise CloudError(
                "refused", "The model's content filter refused this request. Try a different prompt or picture.",
                detail=f"video job failed: {reason}",
            )
        return CloudStatus(state="failed", message=reason or "no reason given", poll_after_s=None)
    if state in ("cancelled", "expired"):
        return CloudStatus(state=state, message=_reason(payload) or None)
    return CloudStatus(
        state=state, progress=_progress(payload.get("progress")), queue_position=position,
        poll_after_s=POLL_SECONDS,
    )
