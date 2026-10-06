from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

import yaml

from src.plugin_api.cloud import CloudModelSpec, MediaInputSpec, ParamSpec, PriceLine

CATALOG_FILE = Path(__file__).resolve().parents[1] / "cloud_models.yml"

FIXED_ASPECTS = ("auto", "1:1", "3:2", "2:3")
FLEXIBLE_ASPECTS = ("auto", "1:1", "3:2", "2:3", "4:3", "3:4", "16:9", "9:16", "21:9")
RESOLUTIONS = ("1K", "2K", "4K")
OUTPUT_FORMATS = ("png", "jpeg", "webp")
IMAGE_FORMATS = ("png", "jpeg", "webp")
MAX_IMAGES = 16
MAX_IMAGE_BYTES = 50_000_000
MAX_OUTPUTS = 10
RATE_NAMES = ("text_input", "image_input", "image_output")
RATE_LABELS = {"text_input": "text input", "image_input": "image input", "image_output": "image output"}
MILLION = Decimal(1_000_000)
EDIT = frozenset({"img_edit"})


def load_catalog(path: Path = CATALOG_FILE) -> Dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


def _rates(entry: Mapping[str, Any]) -> Dict[str, str]:
    table = entry.get("usd_per_million_tokens")
    rates: Dict[str, str] = {}
    for name in RATE_NAMES:
        try:
            value = Decimal(str((table or {}).get(name)))
        except (InvalidOperation, ValueError, AttributeError):
            continue
        if value.is_finite() and value >= 0:
            rates[name] = str(value)
    return rates


def _price_lines(rates: Mapping[str, str]) -> Tuple[PriceLine, ...]:
    return tuple(
        PriceLine(unit="token", usd=Decimal(rates[name]) / MILLION, applies_to=RATE_LABELS[name])
        for name in RATE_NAMES if name in rates
    )


def _params(entry: Mapping[str, Any], flexible: bool) -> List[ParamSpec]:
    quality = tuple(str(value) for value in entry.get("quality") or () if isinstance(value, str)) or ("auto",)
    params = [
        ParamSpec(
            name="aspect_ratio", kind="enum", values=FLEXIBLE_ASPECTS if flexible else FIXED_ASPECTS, default="auto",
            label="Aspect ratio",
        ),
        ParamSpec(name="quality", kind="enum", values=quality, default="auto" if "auto" in quality else quality[0], label="Quality"),
        ParamSpec(name="output_format", kind="enum", values=OUTPUT_FORMATS, default="png", label="File format"),
        ParamSpec(
            name="x.output_compression", kind="range", minimum=0, maximum=100, step=1, integer=True,
            label="Compression",
            description="For JPEG and WebP files: 100 keeps the most detail, lower values make smaller files.",
        ),
    ]
    if flexible:
        params.append(ParamSpec(name="resolution", kind="enum", values=RESOLUTIONS, default="1K", label="Resolution"))
    if entry.get("transparent") is True:
        params.append(ParamSpec(name="background", kind="boolean", default=False, label="Transparent background"))
    if entry.get("input_fidelity") is True:
        params.append(ParamSpec(
            name="x.input_fidelity", kind="enum", values=("low", "high"), default="low", tasks=EDIT,
            label="Match the input pictures",
            description="high keeps faces, logos and fine detail of the pictures closer, and costs more input tokens.",
        ))
    return sorted(params, key=lambda param: param.name)


def model_spec(model_id: str, entry: Any) -> Optional[CloudModelSpec]:
    if not isinstance(model_id, str) or not model_id or not isinstance(entry, dict):
        return None
    flexible = entry.get("sizes") == "flexible"
    rates = _rates(entry)
    label = entry.get("label") if isinstance(entry.get("label"), str) and entry.get("label") else model_id
    deprecated = entry.get("deprecated_at")
    return CloudModelSpec(
        provider_model_id=model_id,
        label=label,
        vendor="openai",
        description=entry.get("description") if isinstance(entry.get("description"), str) else None,
        tasks=frozenset({"txt2img", "img_edit"}),
        outputs=frozenset({"image"}),
        params=tuple(_params(entry, flexible)),
        inputs=(
            MediaInputSpec(
                role="reference", modality="image", min_items=0, max_items=MAX_IMAGES,
                max_bytes=MAX_IMAGE_BYTES, formats=IMAGE_FORMATS, tasks=EDIT,
            ),
            MediaInputSpec(role="mask", modality="image", min_items=0, max_items=1, formats=("png",), tasks=EDIT),
        ),
        max_outputs_per_job=MAX_OUTPUTS,
        pricing=_price_lines(rates),
        typical_seconds=60,
        deprecated_at=str(deprecated) if deprecated else None,
        raw={"sizes": "flexible" if flexible else "fixed", "transparent": entry.get("transparent") is True, "rates": rates},
    )


def catalog_specs(data: Mapping[str, Any]) -> List[CloudModelSpec]:
    models = data.get("models") if isinstance(data.get("models"), dict) else {}
    return [spec for spec in (model_spec(model_id, entry) for model_id, entry in models.items()) if spec is not None]
