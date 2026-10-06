from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from src.plugin_api.cloud import CloudModelSpec, MediaInputSpec, ParamSpec, PriceLine

FAMILY_GEMINI = "gemini"
FAMILY_VEO = "veo"

METHODS = {
    FAMILY_GEMINI: "generateContent",
    FAMILY_VEO: "predictLongRunning",
}

ASPECT_RATIOS = ("1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9")
WIDE_ASPECT_RATIOS = ASPECT_RATIOS + ("1:4", "4:1", "1:8", "8:1")
VIDEO_ASPECT_RATIOS = ("16:9", "9:16")
VIDEO_DURATIONS = (4, 6, 8)
IMAGE_FORMATS = ("png", "jpeg", "webp")
UNKNOWN_REFERENCE_LIMIT = 3
RETIRED_STAGES = frozenset({"RETIRED"})
DEPRECATED_STAGES = frozenset({"DEPRECATED"})
MILLION = Decimal(1_000_000)


@dataclass(frozen=True)
class TokenPrices:
    input_per_million: Decimal
    output_text_per_million: Decimal
    output_image_per_million: Decimal


@dataclass(frozen=True)
class CatalogModel:
    model_id: str
    label: str
    family: str
    description: str
    aspect_ratios: Tuple[str, ...] = ()
    image_sizes: Tuple[str, ...] = ()
    max_references: int = 0
    image_prices: Mapping[str, Decimal] = field(default_factory=dict)
    tokens: Optional[TokenPrices] = None
    resolutions: Tuple[str, ...] = ()
    frames: Tuple[str, ...] = ()
    second_prices: Mapping[str, Decimal] = field(default_factory=dict)
    deprecated_at: Optional[str] = None


STATIC_MODELS: Tuple[CatalogModel, ...] = (
    CatalogModel(
        model_id="gemini-3.1-flash-image",
        label="Nano Banana 2 (Gemini 3.1 Flash Image)",
        family=FAMILY_GEMINI,
        description="Google's general purpose image model: text to image and editing with up to 14 reference pictures, at 1K, 2K or 4K.",
        aspect_ratios=WIDE_ASPECT_RATIOS,
        image_sizes=("1K", "2K", "4K"),
        max_references=14,
        image_prices={"1K": Decimal("0.067"), "2K": Decimal("0.101"), "4K": Decimal("0.151")},
        tokens=TokenPrices(Decimal("0.50"), Decimal("3.00"), Decimal("60.00")),
    ),
    CatalogModel(
        model_id="gemini-3-pro-image",
        label="Nano Banana Pro (Gemini 3 Pro Image)",
        family=FAMILY_GEMINI,
        description="Google's studio grade image model for complex scenes and legible text: text to image and editing with up to 14 reference pictures, at 1K, 2K or 4K.",
        aspect_ratios=ASPECT_RATIOS,
        image_sizes=("1K", "2K", "4K"),
        max_references=14,
        image_prices={"1K": Decimal("0.134"), "2K": Decimal("0.134"), "4K": Decimal("0.24")},
        tokens=TokenPrices(Decimal("2.00"), Decimal("12.00"), Decimal("120.00")),
    ),
    CatalogModel(
        model_id="gemini-3.1-flash-lite-image",
        label="Nano Banana 2 Lite (Gemini 3.1 Flash Lite Image)",
        family=FAMILY_GEMINI,
        description="The quickest and cheapest Gemini image model: text to image and editing with up to 14 reference pictures, at 1K.",
        aspect_ratios=ASPECT_RATIOS,
        max_references=14,
        image_prices={"": Decimal("0.0336")},
        tokens=TokenPrices(Decimal("0.25"), Decimal("1.50"), Decimal("30.00")),
    ),
    CatalogModel(
        model_id="veo-3.1-lite-generate-preview",
        label="Veo 3.1 Lite",
        family=FAMILY_VEO,
        description="Google's lowest cost video model, with sound: 4, 6 or 8 second clips at 720p or 1080p, from text or from a start picture, optionally with an end picture.",
        aspect_ratios=VIDEO_ASPECT_RATIOS,
        resolutions=("720p", "1080p"),
        frames=("first_frame", "last_frame"),
        second_prices={"720p": Decimal("0.05"), "1080p": Decimal("0.08")},
    ),
    CatalogModel(
        model_id="veo-3.1-generate-preview",
        label="Veo 3.1",
        family=FAMILY_VEO,
        description="Google's highest quality video model, with sound: 4, 6 or 8 second clips at 720p, 1080p or 4K, from text or from a start picture, optionally with an end picture.",
        aspect_ratios=VIDEO_ASPECT_RATIOS,
        resolutions=("720p", "1080p", "4k"),
        frames=("first_frame", "last_frame"),
        second_prices={"720p": Decimal("0.40"), "1080p": Decimal("0.40"), "4k": Decimal("0.60")},
        deprecated_at="2026-10-22",
    ),
    CatalogModel(
        model_id="veo-3.1-fast-generate-preview",
        label="Veo 3.1 Fast",
        family=FAMILY_VEO,
        description="A quicker, cheaper Veo 3.1, with sound: 4, 6 or 8 second clips at 720p, 1080p or 4K.",
        aspect_ratios=VIDEO_ASPECT_RATIOS,
        resolutions=("720p", "1080p", "4k"),
        frames=("first_frame", "last_frame"),
        second_prices={"720p": Decimal("0.10"), "1080p": Decimal("0.12"), "4k": Decimal("0.30")},
        deprecated_at="2026-10-22",
    ),
)

STATIC_BY_ID: Dict[str, CatalogModel] = {model.model_id: model for model in STATIC_MODELS}


def family_of(model_id: str, methods: Iterable[str] = ()) -> Optional[str]:
    methods = set(methods)
    name = model_id.lower()
    if name.startswith("veo-") and (not methods or METHODS[FAMILY_VEO] in methods):
        return FAMILY_VEO
    if name.startswith("gemini-") and "-image" in name and (not methods or METHODS[FAMILY_GEMINI] in methods):
        return FAMILY_GEMINI
    return None


def default_entry(model_id: str, family: str, label: str, description: str, deprecated_at: Optional[str]) -> CatalogModel:
    if family == FAMILY_VEO:
        return CatalogModel(
            model_id=model_id, label=label, family=family, description=description,
            aspect_ratios=VIDEO_ASPECT_RATIOS, resolutions=("720p",), frames=("first_frame",), deprecated_at=deprecated_at,
        )
    return CatalogModel(
        model_id=model_id, label=label, family=family, description=description,
        aspect_ratios=ASPECT_RATIOS, max_references=UNKNOWN_REFERENCE_LIMIT, deprecated_at=deprecated_at,
    )


def _surcharge_lines(unit: str, prices: Mapping[str, Decimal]) -> Tuple[PriceLine, ...]:
    if not prices:
        return ()
    base = min(prices.values())
    lines = [PriceLine(unit=unit, usd=base)]
    for tier, price in sorted(prices.items()):
        if tier and price > base:
            lines.append(PriceLine(unit=unit, usd=price - base, applies_to=tier))
    return tuple(lines)


def _image_spec(entry: CatalogModel, label: str, description: str) -> CloudModelSpec:
    params: List[ParamSpec] = []
    if entry.aspect_ratios:
        params.append(ParamSpec(name="aspect_ratio", kind="enum", values=entry.aspect_ratios, default="1:1"))
    if entry.image_sizes:
        params.append(ParamSpec(name="resolution", kind="enum", values=entry.image_sizes, default=entry.image_sizes[0]))
    tasks = {"txt2img"}
    inputs: Tuple[MediaInputSpec, ...] = ()
    if entry.max_references > 0:
        tasks.add("img_edit")
        inputs = (MediaInputSpec(
            role="reference", modality="image", min_items=0, max_items=entry.max_references,
            formats=IMAGE_FORMATS, tasks=frozenset({"img_edit"}),
        ),)
    return CloudModelSpec(
        provider_model_id=entry.model_id,
        label=label,
        vendor="google",
        description=description,
        tasks=frozenset(tasks),
        outputs=frozenset({"image"}),
        params=tuple(sorted(params, key=lambda spec: spec.name)),
        inputs=inputs,
        max_outputs_per_job=1,
        pricing=_surcharge_lines("image", entry.image_prices),
        typical_seconds=30,
        deprecated_at=entry.deprecated_at,
        raw={"family": entry.family},
    )


def _video_spec(entry: CatalogModel, label: str, description: str) -> CloudModelSpec:
    params: List[ParamSpec] = [
        ParamSpec(name="duration_s", kind="enum", values=VIDEO_DURATIONS, default=8),
    ]
    if entry.aspect_ratios:
        params.append(ParamSpec(name="aspect_ratio", kind="enum", values=entry.aspect_ratios, default=entry.aspect_ratios[0]))
    if entry.resolutions:
        params.append(ParamSpec(
            name="resolution", kind="enum", values=entry.resolutions, default=entry.resolutions[0],
            description="1080p and 4K videos are always 8 seconds long.",
        ))
    inputs = tuple(
        MediaInputSpec(role=role, modality="image", max_items=1, formats=IMAGE_FORMATS, tasks=frozenset({"img2video"}))
        for role in ("first_frame", "last_frame") if role in entry.frames
    )
    tasks = {"txt2video"} | ({"img2video"} if inputs else set())
    return CloudModelSpec(
        provider_model_id=entry.model_id,
        label=label,
        vendor="google",
        description=description,
        tasks=frozenset(tasks),
        outputs=frozenset({"video"}),
        params=tuple(sorted(params, key=lambda spec: spec.name)),
        inputs=inputs,
        pricing=_surcharge_lines("second", entry.second_prices),
        typical_seconds=120,
        deprecated_at=entry.deprecated_at,
        raw={"family": entry.family},
        director={"limits": {"default_fps": 24}},
    )


def spec_for(entry: CatalogModel, label: Optional[str] = None, description: Optional[str] = None) -> CloudModelSpec:
    label = label or entry.label
    description = description or entry.description
    if entry.family == FAMILY_VEO:
        return _video_spec(entry, label, description)
    return _image_spec(entry, label, description)


def static_specs() -> List[CloudModelSpec]:
    return [spec_for(entry) for entry in STATIC_MODELS]


def bare_id(name: Any) -> Optional[str]:
    if not isinstance(name, str) or not name:
        return None
    return name[len("models/"):] if name.startswith("models/") else name


def _text(value: Any) -> Optional[str]:
    return value if isinstance(value, str) and value.strip() else None


def live_specs(items: Iterable[Any]) -> List[CloudModelSpec]:
    specs: List[CloudModelSpec] = []
    seen = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        model_id = bare_id(item.get("name"))
        if not model_id or model_id in seen:
            continue
        stage = str(item.get("modelStage") or "").upper()
        if stage in RETIRED_STAGES:
            continue
        methods = [method for method in item.get("supportedGenerationMethods") or [] if isinstance(method, str)]
        entry = STATIC_BY_ID.get(model_id)
        family = entry.family if entry else family_of(model_id, methods)
        if family is None or (methods and METHODS[family] not in methods):
            continue
        retirement = _text(item.get("retirementTime"))
        deprecated_at = retirement[:10] if retirement and stage in DEPRECATED_STAGES else None
        if entry is None:
            label = _text(item.get("displayName")) or model_id
            entry = default_entry(model_id, family, label, _text(item.get("description")) or "", deprecated_at)
        elif deprecated_at and not entry.deprecated_at:
            entry = replace(entry, deprecated_at=deprecated_at)
        specs.append(spec_for(entry))
        seen.add(model_id)
    return specs


def token_prices(model_id: str) -> Optional[TokenPrices]:
    entry = STATIC_BY_ID.get(model_id)
    return entry.tokens if entry else None


def image_price(model_id: str, size: Optional[str]) -> Optional[Decimal]:
    entry = STATIC_BY_ID.get(model_id)
    if entry is None or not entry.image_prices:
        return None
    if size and size in entry.image_prices:
        return entry.image_prices[size]
    return entry.image_prices.get("") or entry.image_prices.get("1K") or min(entry.image_prices.values())


def second_price(model_id: str, resolution: Optional[str]) -> Optional[Decimal]:
    entry = STATIC_BY_ID.get(model_id)
    if entry is None or not entry.second_prices:
        return None
    if resolution and resolution in entry.second_prices:
        return entry.second_prices[resolution]
    return min(entry.second_prices.values())
