import base64
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from src.plugin_api.cloud import (
    EXTRA_PARAM_PREFIX,
    CloudArtifact,
    CloudCost,
    CloudError,
    CloudModelSpec,
    CloudResult,
    MediaInputSpec,
    ParamSpec,
    PriceLine,
    is_canonical_param,
)

CANONICAL_WIRE = {
    "aspect_ratio": "aspect_ratio",
    "resolution": "resolution",
    "size": "size",
    "quality": "quality",
    "output_format": "output_format",
    "background": "background",
}
IGNORED_WIRE = frozenset({"n", "prompt", "model", "stream", "user", "provider", "input_references", "seed"})
FALLBACK_ENUMS = {
    "resolution": ("512", "1K", "2K", "4K"),
    "output_format": ("png", "jpeg", "webp"),
}
FALLBACK_KINDS = {
    "background": "boolean",
    "size": "text",
    "output_compression": "range",
    "resolution": "enum",
    "output_format": "enum",
}
FALLBACK_RANGES = {"output_compression": (0.0, 100.0)}
KIND_ALIASES = {
    "enum": "enum", "select": "enum", "choice": "enum",
    "range": "range", "number": "range", "integer": "range", "int": "range", "float": "range",
    "boolean": "boolean", "bool": "boolean",
    "string": "text", "text": "text",
}
PRICE_UNITS = {
    "image": "image", "images": "image", "per_image": "image",
    "megapixel": "megapixel", "megapixels": "megapixel", "mp": "megapixel",
    "second": "second", "seconds": "second",
    "token": "token", "tokens": "token",
    "request": "request", "requests": "request",
}
DEFAULT_REFERENCE_LIMIT = 4
REFERENCE_FORMATS = ("png", "jpeg", "webp")
DEFAULT_MEDIA_TYPES = {"png": "image/png", "jpeg": "image/jpeg", "jpg": "image/jpeg", "webp": "image/webp", "svg": "image/svg+xml"}


def _items(payload: Any) -> List[Any]:
    if isinstance(payload, dict):
        payload = payload.get("data", payload.get("models", []))
    return payload if isinstance(payload, list) else []


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _descriptor(entry: Any) -> Optional[Dict[str, Any]]:
    if isinstance(entry, str) and entry:
        return {"name": entry}
    if not isinstance(entry, dict):
        return None
    name = entry.get("name") or entry.get("parameter") or entry.get("id")
    if not isinstance(name, str) or not name:
        return None
    values = entry.get("values") or entry.get("options") or entry.get("enum")
    kind = KIND_ALIASES.get(str(entry.get("type") or entry.get("kind") or "").lower())
    return {
        "name": name,
        "kind": kind,
        "values": [item for item in values if isinstance(item, (str, int, float)) and not isinstance(item, bool)] if isinstance(values, list) else [],
        "minimum": _number(entry.get("min", entry.get("minimum"))),
        "maximum": _number(entry.get("max", entry.get("maximum"))),
        "step": _number(entry.get("step")),
        "default": entry.get("default"),
        "label": entry.get("label") or entry.get("title"),
        "description": entry.get("description"),
        "integer": entry.get("integer"),
    }


def _merge(descriptors: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    merged: Dict[str, Dict[str, Any]] = {}
    for item in descriptors:
        current = merged.get(item["name"])
        if current is None:
            merged[item["name"]] = dict(item, values=list(item.get("values", [])))
            continue
        for value in item.get("values", []):
            if value not in current["values"]:
                current["values"].append(value)
        for key, pick in (("minimum", min), ("maximum", max)):
            if item.get(key) is not None:
                current[key] = item[key] if current.get(key) is None else pick(current[key], item[key])
        for key in ("kind", "step", "default", "label", "description", "integer"):
            if current.get(key) is None and item.get(key) is not None:
                current[key] = item[key]
    return merged


def _param_spec(descriptor: Dict[str, Any]) -> Optional[ParamSpec]:
    wire = descriptor["name"]
    if wire in IGNORED_WIRE:
        return None
    name = CANONICAL_WIRE.get(wire)
    if name is None:
        name = f"{EXTRA_PARAM_PREFIX}{wire}"
    kind = "boolean" if wire == "background" else descriptor.get("kind") or FALLBACK_KINDS.get(wire)
    values = descriptor.get("values") or []
    if kind is None:
        kind = "enum" if values else "range" if descriptor.get("minimum") is not None else None
    if kind == "enum":
        values = values or list(FALLBACK_ENUMS.get(wire, ()))
        if not values:
            return None
        default = descriptor.get("default")
        return ParamSpec(
            name=name, kind="enum", values=tuple(values),
            default=default if default in values else None,
            label=descriptor.get("label"), description=descriptor.get("description"),
        )
    if kind == "range":
        minimum, maximum = descriptor.get("minimum"), descriptor.get("maximum")
        if minimum is None or maximum is None:
            minimum, maximum = FALLBACK_RANGES.get(wire, (None, None))
        if minimum is None or maximum is None or minimum > maximum:
            return None
        step = descriptor.get("step")
        integer = descriptor.get("integer")
        if integer is None:
            integer = all(float(value).is_integer() for value in (minimum, maximum, step or 1))
        default = _number(descriptor.get("default"))
        return ParamSpec(
            name=name, kind="range", minimum=minimum, maximum=maximum, step=step, integer=bool(integer),
            default=default if default is not None and minimum <= default <= maximum else None,
            label=descriptor.get("label"), description=descriptor.get("description"),
        )
    if kind == "boolean":
        default = descriptor.get("default")
        return ParamSpec(
            name=name, kind="boolean", default=default if isinstance(default, bool) else None,
            label=descriptor.get("label"), description=descriptor.get("description"),
        )
    if kind == "text":
        return ParamSpec(name=name, kind="text", label=descriptor.get("label"), description=descriptor.get("description"))
    return None


def _price_lines(endpoints: List[Any]) -> Tuple[PriceLine, ...]:
    cheapest: Dict[Tuple[str, Optional[str]], Decimal] = {}
    for endpoint in endpoints:
        pricing = endpoint.get("pricing") if isinstance(endpoint, dict) else None
        lines = pricing if isinstance(pricing, list) else [pricing] if isinstance(pricing, dict) else []
        for line in lines:
            if not isinstance(line, dict):
                continue
            try:
                cost = Decimal(str(line.get("cost_usd")))
            except (InvalidOperation, ValueError):
                continue
            if not cost.is_finite() or cost < 0:
                continue
            unit = str(line.get("unit") or "").lower()
            mapped = PRICE_UNITS.get(unit)
            billable = line.get("billable")
            applies_to = None if mapped else (str(billable) if billable else unit or None)
            key = (mapped or "sku", applies_to)
            if key not in cheapest or cost < cheapest[key]:
                cheapest[key] = cost
    return tuple(PriceLine(unit=unit, usd=cost, applies_to=applies_to) for (unit, applies_to), cost in sorted(cheapest.items(), key=lambda pair: (pair[0][0], pair[0][1] or "")))


def endpoint_list(payload: Any) -> List[Any]:
    data = payload.get("data", payload) if isinstance(payload, dict) else payload
    if isinstance(data, dict):
        data = data.get("endpoints", [])
    return data if isinstance(data, list) else []


def model_spec(item: Any, endpoints: List[Any]) -> Optional[CloudModelSpec]:
    if not isinstance(item, dict):
        return None
    model_id = item.get("id")
    if not isinstance(model_id, str) or not model_id:
        return None
    architecture = item.get("architecture") if isinstance(item.get("architecture"), dict) else {}
    outputs = [str(mode).lower() for mode in architecture.get("output_modalities") or []]
    if "image" not in outputs:
        return None
    inputs = [str(mode).lower() for mode in architecture.get("input_modalities") or []]

    descriptors = [d for d in (_descriptor(e) for e in item.get("supported_parameters") or []) if d]
    for endpoint in endpoints:
        if isinstance(endpoint, dict):
            descriptors.extend(d for d in (_descriptor(e) for e in endpoint.get("supported_parameters") or []) if d)
            for passthrough in endpoint.get("allowed_passthrough_parameters") or []:
                if isinstance(passthrough, str) and passthrough:
                    descriptors.append({"name": passthrough, "kind": "text", "values": []})
    merged = _merge(descriptors)

    params: List[ParamSpec] = []
    seen = set()
    for descriptor in merged.values():
        spec = _param_spec(descriptor)
        if spec is not None and spec.name not in seen and is_canonical_param(spec.name):
            seen.add(spec.name)
            params.append(spec)

    count = merged.get("n") or {}
    max_outputs = int(count["maximum"]) if count.get("maximum") and count["maximum"] >= 1 else 1
    accepts_images = "image" in inputs
    reference = merged.get("input_references") or {}
    media = ()
    if accepts_images:
        limit = int(reference["maximum"]) if reference.get("maximum") and reference["maximum"] >= 1 else DEFAULT_REFERENCE_LIMIT
        media = (MediaInputSpec(
            role="reference", modality="image", min_items=0, max_items=limit,
            formats=REFERENCE_FORMATS, tasks=frozenset({"img_edit"}),
        ),)
    tasks = {"txt2img", "img_edit"} if accepts_images else {"txt2img"}
    label = item.get("name") if isinstance(item.get("name"), str) and item.get("name") else model_id
    description = item.get("description") if isinstance(item.get("description"), str) else None
    expiry = item.get("expiration_date") if isinstance(item.get("expiration_date"), str) else None
    return CloudModelSpec(
        provider_model_id=model_id,
        label=label,
        vendor=model_id.split("/", 1)[0] if "/" in model_id else None,
        description=description,
        tasks=frozenset(tasks),
        outputs=frozenset({"image"}),
        params=tuple(sorted(params, key=lambda spec: spec.name)),
        inputs=media,
        max_outputs_per_job=max_outputs,
        pricing=_price_lines(endpoints),
        deprecated_at=expiry,
        raw={"supported": sorted(merged)},
    )


def data_url(path: Path, media_type: str) -> str:
    return f"data:{media_type};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def build_body(request: Any, provider_only: List[str], user_hash: Optional[str]) -> Dict[str, Any]:
    supported = set((request.model.raw or {}).get("supported") or [])
    body: Dict[str, Any] = {"model": request.model.provider_model_id, "prompt": request.prompt}
    if request.count > 1:
        body["n"] = request.count
    if request.seed is not None and "seed" in supported:
        body["seed"] = request.seed
    for name, value in request.params.items():
        if value is None or value == "":
            continue
        if name.startswith(EXTRA_PARAM_PREFIX):
            body[name[len(EXTRA_PARAM_PREFIX):]] = value
        elif name == "background":
            if value is True or value == "transparent":
                body["background"] = "transparent"
            elif isinstance(value, str) and value not in ("true", "false"):
                body["background"] = value
        elif name in CANONICAL_WIRE:
            body[CANONICAL_WIRE[name]] = value
    references = request.inputs.get("reference") or []
    if references:
        body["input_references"] = [{"type": "image_url", "image_url": {"url": data_url(media.path, media.media_type)}} for media in references]
    if provider_only:
        body["provider"] = {"only": provider_only}
    if user_hash:
        body["user"] = user_hash
    return body


def parse_result(payload: Any, request: Any) -> CloudResult:
    if not isinstance(payload, dict):
        raise CloudError("failed", "OpenRouter sent an answer that could not be read.", detail="response is not an object")
    if isinstance(payload.get("error"), dict):
        message = payload["error"].get("message") or "no message"
        raise CloudError("failed", "OpenRouter could not complete the request.", detail=str(message)[:500])
    default_type = payload.get("media_type")
    artifacts: List[CloudArtifact] = []
    for index, entry in enumerate(payload.get("data") or []):
        if not isinstance(entry, dict):
            continue
        media_type = entry.get("media_type") or default_type or DEFAULT_MEDIA_TYPES.get(str(entry.get("output_format") or "png"), "image/png")
        if entry.get("b64_json"):
            try:
                data = base64.b64decode(entry["b64_json"], validate=False)
            except ValueError:
                continue
            if data:
                artifacts.append(CloudArtifact(modality="image", index=index, data=data, media_type=media_type))
        elif isinstance(entry.get("url"), str) and entry["url"]:
            artifacts.append(CloudArtifact(modality="image", index=index, url=entry["url"], media_type=media_type))
    if not artifacts:
        raise CloudError("failed", "OpenRouter returned no images.", detail="empty data list")
    cost = None
    usage = payload.get("usage")
    raw_cost = usage.get("cost") if isinstance(usage, dict) else None
    if raw_cost is not None:
        try:
            amount = Decimal(str(raw_cost))
            if amount.is_finite() and amount >= 0:
                cost = CloudCost(amount_usd=amount, source="provider")
        except (InvalidOperation, ValueError):
            cost = None
    return CloudResult(
        artifacts=tuple(artifacts),
        cost=cost,
        seed_used=request.seed,
        provider_job_id=str(payload["id"]) if payload.get("id") else None,
    )
