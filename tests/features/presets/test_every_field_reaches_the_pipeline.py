import copy
import functools
import itertools
import re
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

import pytest

from scripts.preset_render import (
    ROOT,
    _MEDIA_PLACEHOLDER_PREFIX,
    build_fixture_form_data,
    build_processor,
    load_all_presets,
    render_preset_mode,
)
from src.features.presets.fixture_form import bind_fixture_form, default_form_fields
from src.features.presets.linter import _HOST_CONSUMED_FIELD_NAMES, _NON_VALUE_FIELD_TYPES, PresetLinter

NUMERIC_TYPES = frozenset({"slider", "stepper", "number", "integer"})
TOGGLE_TYPES = frozenset({"checkbox", "boolean", "gate"})
DOCUMENT_MODES = ("director",)
MEDIA_TYPES = frozenset({"image", "media"})
TEXT_TYPES = frozenset({"string", "textbox"})
RESOLUTION_CANDIDATES = ("1024x1024", "832x480", "1280x720", "512x512")
SAMPLER_CANDIDATES = ("euler", "dpmpp_2m", "heun", "euler_ancestral", "dpmpp_sde")
SCHEDULE_CANDIDATES = ("simple", "karras", "normal", "sgm_uniform", "beta")

UNMUTATED_TYPES = {
    "model": "weights are resolved by the loader, never present in a pure render",
    "models": "weights are resolved by the loader, never present in a pure render",
    "lora_picker": "adapters are resolved by the loader, never present in a pure render",
    "image": "media input needs real files",
    "video": "media input needs real files",
    "audio": "media input needs real files",
    "media": "media input needs real files",
    "file": "media input needs real files",
    "llm": "host-side prompt expansion, not a pipe input",
    "prompt_timeline": "structured document injected by the host",
    "camera_shot": "feeds the prompt text, which the harness pins to a fixed prompt",
    "tags": "feeds the prompt text, which the harness pins to a fixed prompt",
}

SKIPPED_PRESETS = {
    "FieldCatalog": "developer catalog of every field type, not a generation preset",
}

MAX_CONDITION_COMBOS = 81

VIDEO_DIRECTOR_DURATION = "the host folds this field into the Video Director document (video_director/simple_modes.py) before the pipeline renders; the harness injects a fixed document"
KNOWN_INERT: Dict[str, str] = {
    "MiniMax-H3/txt2video/duration": VIDEO_DIRECTOR_DURATION,
    "MiniMax-H3/img2video/duration": VIDEO_DIRECTOR_DURATION,
    "MiniMax-H3/first_last/duration": VIDEO_DIRECTOR_DURATION,
    "MiniMax-H3/refs/duration": VIDEO_DIRECTOR_DURATION,
}


def _walk(fields) -> Iterator[Any]:
    for field in fields:
        yield field
        if isinstance(field.children, list):
            yield from _walk(field.children)


@functools.lru_cache(maxsize=1)
def _presets():
    presets, _ = load_all_presets(
        presets_root=ROOT / "content" / "presets" / "marketplace", include_plugins=False
    )
    return {preset.id: preset for preset in presets}


@functools.lru_cache(maxsize=1)
def _processor():
    return build_processor()


@functools.lru_cache(maxsize=1)
def _registry():
    return PresetLinter._field_type_registry()


def _is_generation_field(field) -> bool:
    if not field.name or "{{" in field.name:
        return False
    if field.type in _NON_VALUE_FIELD_TYPES or field.type == "@loop":
        return False
    if _registry().get(field.type).container or field.display_only or field.capability:
        return False
    if field.name in _HOST_CONSUMED_FIELD_NAMES or field.name.endswith(("__origin", "_inpaint_mask")):
        return False
    return field.type not in UNMUTATED_TYPES


def _cases():
    out = []
    for preset in _presets().values():
        if Path(preset.path).parent.name in SKIPPED_PRESETS:
            continue
        for mode in sorted(preset.modes):
            seen = set()
            for field in _walk(default_form_fields(preset, mode)):
                if field.name in seen or not _is_generation_field(field):
                    continue
                seen.add(field.name)
                out.append(pytest.param(preset.id, mode, field.name, id=f"{preset.name}/{mode}/{field.name}"))
    return out


def _field(preset_id: str, mode: str, name: str):
    preset = _presets()[preset_id]
    return next(f for f in _walk(default_form_fields(preset, mode)) if f.name == name)


def _numeric_candidates(field, current) -> List[Any]:
    config = field.configuration or {}
    low, high = config.get("min"), config.get("max")
    step = config.get("step") or 1
    integral = field.type in ("stepper", "integer") or all(isinstance(v, int) for v in (step, current) if v is not None)
    base = current if isinstance(current, (int, float)) and not isinstance(current, bool) else (low if low is not None else 1)
    candidates = [base + step, base - step, base + 2 * step, base * 2, base / 2 if base else 1]
    if low is not None:
        candidates = [c for c in candidates if c >= low]
    if high is not None:
        candidates = [c for c in candidates if c <= high]
    if integral:
        candidates = [int(round(c)) for c in candidates]
    return [c for c in candidates if c != current]


def _candidates(field, current) -> List[Any]:
    config = field.configuration or {}
    if field.type in NUMERIC_TYPES:
        return _numeric_candidates(field, current)
    if field.type in TOGGLE_TYPES:
        return [not bool(current)]
    if field.type in TEXT_TYPES:
        return [f"X:1\nK:C\n{current or ''}changed"]
    if field.type == "select":
        options = [o.get("value") if isinstance(o, dict) else o for o in config.get("options") or []]
        return [o for o in options if o != current and o not in (None, "")]
    if field.type == "carousel":
        return [i["value"] for i in config.get("items") or [] if i["value"] != current]
    if field.type == "checkbox_group":
        options = [o["value"] for o in config.get("options") or []]
        current = list(current or [])
        return [[o for o in current if o != options[0]] if options[0] in current else current + [options[0]]]
    if field.type == "resolution":
        return [c for c in RESOLUTION_CANDIDATES if c != current]
    if field.type == "sampler":
        return [c for c in SAMPLER_CANDIDATES if c != current]
    if field.type == "schedule":
        return [c for c in SCHEDULE_CANDIDATES if c != current]
    return []


def _render(preset, mode: str, form: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    try:
        bound = bind_fixture_form(preset, mode, copy.deepcopy(form), mask_prefix=_MEDIA_PLACEHOLDER_PREFIX)
    except Exception:
        return None
    record = render_preset_mode(_processor(), preset, mode, bound)
    assert "error" not in record, record.get("error")
    return record["pipes"]


@functools.lru_cache(maxsize=None)
def _base_form(preset_id: str, mode: str) -> Dict[str, Any]:
    return build_fixture_form_data(_presets()[preset_id], mode)


def _changes_pipeline(preset, mode: str, base: Dict[str, Any], name: str, candidates: List[Any]) -> bool:
    reference = _render(preset, mode, base)
    for candidate in candidates:
        rendered = _render(preset, mode, {**base, name: candidate})
        if rendered is not None and rendered != reference:
            return True
    return False


def _toggled_bases(preset, mode: str, base: Dict[str, Any], name: str) -> Iterator[Dict[str, Any]]:
    for field in _walk(default_form_fields(preset, mode)):
        if field.name and field.name != name and field.type in TOGGLE_TYPES:
            yield {**base, field.name: not bool(base.get(field.name))}


def _strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _strings(item)


def _condition_names(preset, mode: str, name: str) -> List[str]:
    token = re.compile(r"\bform(?:\.|\[['\"])" + re.escape(name) + r"\b")
    ref = re.compile(r"\bform(?:\.|\[['\"])([A-Za-z_][A-Za-z0-9_]*)")
    found: List[str] = []
    for pipe in preset.modes[mode].pipes:
        texts = [t for t in _strings(pipe.configuration) if token.search(t)]
        if not texts and not token.search(str(pipe.enabled)):
            continue
        for other in ref.findall(" ".join([str(pipe.enabled), *texts])):
            if other != name and other not in found:
                found.append(other)
    return found


def _alternatives(field, current) -> List[Any]:
    if field.type in MEDIA_TYPES:
        return [f"{_MEDIA_PLACEHOLDER_PREFIX}{field.name}.png", current]
    values = _candidates(field, current)
    if field.type in NUMERIC_TYPES:
        config = field.configuration or {}
        extremes = [config.get("max"), config.get("min")]
        values = values + [v for v in extremes if v is not None and v != current]
    return values[:2] + [current]


def _condition_bases(preset, mode: str, base: Dict[str, Any], name: str) -> Iterator[Dict[str, Any]]:
    by_name = {f.name: f for f in _walk(default_form_fields(preset, mode)) if f.name}
    axes = []
    for other in _condition_names(preset, mode, name)[:4]:
        field = by_name.get(other)
        if field is None:
            document = base.get(other)
            if isinstance(document, dict) and "mode" in document:
                axes.append((other, [{**document, "mode": mode_name} for mode_name in DOCUMENT_MODES] + [document]))
            continue
        values = _alternatives(field, base.get(other))
        if values:
            axes.append((other, values))
    for combo in itertools.islice(itertools.product(*[v for _, v in axes]), MAX_CONDITION_COMBOS):
        yield {**base, **{n: v for (n, _), v in zip(axes, combo)}}


@pytest.mark.parametrize("preset_id,mode,name", _cases())
def test_changing_field_changes_the_rendered_pipeline(preset_id, mode, name):
    key = f"{_presets()[preset_id].name}/{mode}/{name}"
    if key in KNOWN_INERT:
        pytest.skip(KNOWN_INERT[key])
    preset = _presets()[preset_id]
    field = _field(preset_id, mode, name)
    base = _base_form(preset_id, mode)
    candidates = _candidates(field, base.get(name))
    assert candidates, f"no alternative value for {key}"

    if _changes_pipeline(preset, mode, base, name, candidates):
        return
    for enabling in itertools.chain(_toggled_bases(preset, mode, base, name), _condition_bases(preset, mode, base, name)):
        if _changes_pipeline(preset, mode, enabling, name, candidates):
            return
    pytest.fail(f"changing {key} does not change the rendered pipeline")
