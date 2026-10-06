from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from src.plugin_api.cloud import CloudModelSpec, MediaInputSpec, ParamSpec, PriceLine

CATALOG_FILE = Path(__file__).resolve().parents[1] / "cloud_models.yml"
VENDOR = "Black Forest Labs"
ASPECT_RATIOS = ("21:9", "16:9", "3:2", "4:3", "5:4", "1:1", "4:5", "3:4", "2:3", "9:16", "9:21")
FLUX3_ASPECT_RATIOS = ("auto", "21:9", "2:1", "16:9", "3:2", "7:5", "4:3", "5:4", "1:1", "4:5", "3:4", "5:7", "2:3", "9:16", "1:2", "9:21")
FLUX3_RESOLUTIONS = ("768sq", "1k", "1.5k", "2k", "4k")
OUTPUT_FORMATS = ("jpeg", "png", "webp")
REFERENCE_FORMATS = ("png", "jpeg", "webp")
SHAPES = ("dimensions", "aspect_ratio", "flux3")
RAW_KEYS = ("shape", "step", "min_side", "max_side", "references", "upsampling")


def load_catalog(path: Path = CATALOG_FILE) -> Dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


def _range(name: str, entry: Any, label: str, description: str, integer: bool) -> Optional[ParamSpec]:
    if not isinstance(entry, dict):
        return None
    return ParamSpec(
        name=name, kind="range", minimum=float(entry["minimum"]), maximum=float(entry["maximum"]),
        step=float(entry["step"]) if entry.get("step") is not None else None, integer=integer,
        default=float(entry["default"]) if entry.get("default") is not None else None,
        label=label, description=description,
    )


def _aspect_param(item: Dict[str, Any], tasks: frozenset) -> ParamSpec:
    if item["shape"] == "flux3":
        values: Tuple[str, ...] = FLUX3_ASPECT_RATIOS
    elif "img_edit" in tasks:
        values = ("auto", *ASPECT_RATIOS)
    else:
        values = ASPECT_RATIOS
    automatic = "auto" in values
    return ParamSpec(
        name="aspect_ratio", kind="enum", values=values, default="auto" if automatic else "1:1", label="Aspect ratio",
        description="auto lets the model choose; when editing it keeps the shape of the first picture." if automatic else None,
    )


def _params(item: Dict[str, Any], tasks: frozenset) -> List[ParamSpec]:
    params = [_aspect_param(item, tasks)]
    if item["shape"] == "flux3":
        params.append(ParamSpec(name="resolution", kind="enum", values=FLUX3_RESOLUTIONS, default="1k", label="Resolution"))
    resolutions = tuple(item.get("resolutions") or ())
    if item["shape"] == "dimensions" and len(resolutions) > 1:
        params.append(ParamSpec(
            name="resolution", kind="enum", values=resolutions, default=resolutions[0], label="Resolution",
            description="1K is about 1 megapixel, 2K about 4 megapixels.",
        ))
    if item.get("output_format", True):
        default = item.get("output_format_default", "jpeg")
        params.append(ParamSpec(name="output_format", kind="enum", values=OUTPUT_FORMATS, default=default, label="File format"))
    if item.get("upsampling"):
        params.append(ParamSpec(
            name="enhance_prompt", kind="boolean", default=bool(item.get("upsampling_default")), label="Improve the prompt",
            description="BFL rewrites the prompt with more detail before drawing (prompt upsampling).",
        ))
    for spec in (
        _range("guidance", item.get("guidance"), "Guidance", "How closely the picture follows the prompt.", False),
        _range("steps", item.get("steps"), "Steps", "More steps take longer and can add detail.", True),
    ):
        if spec is not None:
            params.append(spec)
    safety_max = int(item.get("safety_max", 2))
    params.append(ParamSpec(
        name="x.safety_tolerance", kind="range", minimum=0, maximum=safety_max, step=1, integer=True, default=2,
        label="Safety tolerance", description=f"How strict BFL's moderation is: 0 is the strictest, {safety_max} the most lenient.",
    ))
    if item.get("raw"):
        params.append(ParamSpec(name="x.raw", kind="boolean", default=False, label="Raw mode", description="A less processed, more natural look."))
    if item.get("grounding"):
        params.append(ParamSpec(
            name="x.grounding", kind="boolean", default=True, label="Web grounding",
            description="Let the model look up facts on the web while it plans the picture.",
        ))
    return sorted(params, key=lambda spec: spec.name)


def _pricing(item: Dict[str, Any]) -> Tuple[PriceLine, ...]:
    lines: List[PriceLine] = []
    for line in item.get("pricing") or []:
        try:
            amount = Decimal(str(line["usd"]))
        except (InvalidOperation, KeyError, TypeError):
            continue
        lines.append(PriceLine(unit=line.get("unit", "image"), usd=amount, applies_to=line.get("applies_to")))
    return tuple(lines)


def model_spec(item: Any) -> Optional[CloudModelSpec]:
    if not isinstance(item, dict) or not isinstance(item.get("id"), str) or item.get("shape") not in SHAPES:
        return None
    tasks = frozenset(task for task in item.get("tasks") or [] if isinstance(task, str))
    references = int(item.get("references") or 0)
    inputs: Tuple[MediaInputSpec, ...] = ()
    if "img_edit" in tasks and references:
        inputs = (MediaInputSpec(
            role="reference", modality="image", min_items=1, max_items=references,
            formats=REFERENCE_FORMATS, tasks=frozenset({"img_edit"}),
        ),)
    raw = {key: item[key] for key in RAW_KEYS if key in item}
    raw.update({
        "seed": bool(item.get("seed", True)),
        "output_format": bool(item.get("output_format", True)),
        "user": bool(item.get("user", True)),
    })
    return CloudModelSpec(
        provider_model_id=item["id"],
        label=str(item.get("label") or item["id"]),
        vendor=VENDOR,
        description=item.get("description"),
        tasks=tasks,
        outputs=frozenset({"image"}),
        params=tuple(_params(item, tasks)),
        inputs=inputs,
        max_outputs_per_job=1,
        pricing=_pricing(item),
        typical_seconds=item.get("typical_seconds"),
        raw=raw,
    )


def catalog_specs(data: Dict[str, Any]) -> List[CloudModelSpec]:
    return [spec for spec in (model_spec(item) for item in data.get("models") or []) if spec is not None]


def suggested_ids(data: Dict[str, Any]) -> Tuple[str, ...]:
    return tuple(item for item in data.get("suggested") or [] if isinstance(item, str))
