import copy
import hashlib
import json
import random as _random
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Set

from src.features.forms.binding import condition_matches
from src.features.prompt.conditions import parse_template
from src.features.sessions.request_segments import (
    active_prompt_index,
    build_segments_payload,
    first_empty_prompt,
    flatten_rich_segments,
    prompt_list,
    seed_prompt_tabs,
    seed_segments_from_text,
    shuffle_segment_chips,
)
from src.features.sessions.request_variables import (
    Random,
    apply_independent_usages,
    build_variables_for_submit,
)

LAYOUT_KEYS = frozenset(
    {
        "workbenchMaxHeight",
        "leftPanelWidth",
        "leftPanelCollapsed",
        "workbenchCollapsed",
        "layoutMode",
        "promptPanelWidth",
        "positiveSegmentsCollapsed",
        "negativeSegmentsCollapsed",
        "sectionCollapsed",
    }
)

OVERRIDE_KEYS = frozenset(
    {
        "formData",
        "prompt",
        "negativePrompt",
        "promptSegments",
        "negativePromptSegments",
        "promptTabs",
        "activePromptTab",
        "promptRelay",
        "variables",
        "seed",
        "videoDirector",
        "musicDirector",
    }
)

ENTRY_KEYS_HANDLED = frozenset(
    {
        "selectedVariant",
        "prompt",
        "negativePrompt",
        "promptSegments",
        "negativePromptSegments",
        "promptTabs",
        "activePromptTab",
        "promptRelay",
        "variables",
        "formData",
        "autoTagIds",
        "autoCollectionIds",
        "sourcePromptId",
    }
)

ENTRY_KEYS_REFUSED = frozenset({"videoDirector", "musicDirector"})

ENTRY_KEYS_IGNORED = frozenset(
    {"selectedPreset", "selectedMode", "presetVersion", "segments", "negativeSegments", "seed", "compare"}
)

VIDEO_DIRECTOR_SHAPES = ("t2v", "i2v", "flf", "director")
MUSIC_DIRECTOR_SHAPES = ("t2m", "song", "style", "director", "extend", "repaint")

DIRECTOR_SUPPORTED = (
    "plain modes of every preset (prompt, segments, prompt tabs, prompt relay and variables, "
    "plus every form field)"
)
DIRECTOR_REFUSAL = (
    "This session uses the Video Director or Music Director, which a script cannot start yet. "
    f"Supported: {DIRECTOR_SUPPORTED}. Start it from the Generate page, or send a full request "
    "to POST /api/generations/start with form_data.video_director or form_data.music_director."
)


class SessionModeMissing(LookupError):
    pass


class RequestRefused(Exception):
    def __init__(self, code: str, message: str, detail: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail or {}


@dataclass
class Randomness:
    random: Random = field(default=_random.random)
    now: Callable[[], float] = field(default=lambda: time.time() * 1000)


@dataclass
class ResolvedRequest:
    request: Dict[str, Any]
    settings_hash: str
    notes: List[str] = field(default_factory=list)
    rolls: Dict[str, Dict[str, Any]] = field(default_factory=dict)


def mode_payload(session_data: Mapping[str, Any], mode: str) -> Dict[str, Any]:
    payload = (session_data or {}).get(mode)
    if not isinstance(payload, Mapping):
        raise SessionModeMissing(mode)
    return {key: copy.deepcopy(value) for key, value in payload.items() if key not in LAYOUT_KEYS}


def apply_overrides(snapshot: Optional[Mapping[str, Any]], overrides: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    effective = copy.deepcopy(dict(snapshot or {}))
    for key, value in (overrides or {}).items():
        if key == "formData":
            merged = dict(effective.get("formData") or {})
            merged.update(copy.deepcopy(value or {}))
            effective["formData"] = merged
        else:
            effective[key] = copy.deepcopy(value)
    return effective


def settings_hash(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _record(value: Any) -> bool:
    return isinstance(value, Mapping)


def _merge_capabilities(base: Mapping[str, Any], override: Mapping[str, Any]) -> Dict[str, Any]:
    merged: Dict[str, Any] = dict(base)
    for key, value in override.items():
        if key != "modes":
            merged[key] = value
    base_modes = base.get("modes") if _record(base.get("modes")) else {}
    override_modes = override.get("modes") if _record(override.get("modes")) else {}
    modes: Dict[str, Any] = dict(base_modes)
    for shape, shape_override in override_modes.items():
        existing = base_modes.get(shape)
        modes[shape] = {**existing, **shape_override} if _record(shape_override) and _record(existing) else shape_override
    merged["modes"] = modes
    return merged


def _director_active(raw: Any, shapes: Sequence[str], mode: str) -> bool:
    if not _record(raw):
        return False
    overrides = raw.get("preset_mode_overrides") if _record(raw.get("preset_mode_overrides")) else None
    override = overrides.get(mode) if overrides and _record(overrides.get(mode)) else None
    resolved = _merge_capabilities(raw, override) if override else raw
    modes = resolved.get("modes") if _record(resolved.get("modes")) else None
    if not modes or not any(_record(modes.get(shape)) for shape in shapes):
        return False
    preset_modes = resolved.get("preset_modes")
    if not isinstance(preset_modes, list):
        return True
    return mode in [item for item in preset_modes if isinstance(item, str)]


def director_active(preset_vars: Optional[Mapping[str, Any]], mode: str) -> bool:
    vars_ = preset_vars or {}
    return _director_active(vars_.get("video_director"), VIDEO_DIRECTOR_SHAPES, mode) or _director_active(
        vars_.get("music_director"), MUSIC_DIRECTOR_SHAPES, mode
    )


def _lists_mode(preset_vars: Optional[Mapping[str, Any]], key: str, mode: str, form_data: Optional[Mapping[str, Any]]) -> bool:
    if not mode or not preset_vars:
        return False
    modes = preset_vars.get(key)
    if not isinstance(modes, list):
        return False
    for entry in modes:
        if isinstance(entry, str):
            if entry == mode:
                return True
            continue
        if not _record(entry) or entry.get("mode") != mode:
            continue
        when = entry.get("when")
        if when is None:
            return True
        if form_data is None:
            continue
        try:
            if condition_matches(when, dict(form_data)):
                return True
        except Exception:
            continue
    return False


def is_promptless_mode(preset_vars: Optional[Mapping[str, Any]], mode: str, form_data: Optional[Mapping[str, Any]] = None) -> bool:
    return _lists_mode(preset_vars, "promptless_modes", mode, form_data)


def is_optional_prompt_mode(preset_vars: Optional[Mapping[str, Any]], mode: str, form_data: Optional[Mapping[str, Any]] = None) -> bool:
    return _lists_mode(preset_vars, "optional_prompt_modes", mode, form_data) and not is_promptless_mode(
        preset_vars, mode, form_data
    )


def _condition_readers(texts: Sequence[str]) -> Set[str]:
    names: Set[str] = set()
    for text in texts:
        if "<<" not in text:
            continue
        parsed = parse_template(text)
        if parsed is not None:
            names.update(parsed.reads())
    return names


def _prompt_state(tab: Mapping[str, Any], batch_prompts: bool) -> Dict[str, Any]:
    def pick(source: Mapping[str, Any], keys: Sequence[str]) -> Dict[str, Any]:
        return {key: source[key] for key in keys if key in source}

    shared = pick(tab, ("variables", "promptRelay", "videoDirector"))
    if not batch_prompts:
        return pick(
            tab,
            (
                "prompt",
                "negativePrompt",
                "promptSegments",
                "negativePromptSegments",
                "variables",
                "promptTabs",
                "activePromptTab",
                "promptRelay",
                "videoDirector",
            ),
        )
    entry_keys = ("prompt", "negativePrompt", "promptSegments", "negativePromptSegments")
    entries = prompt_list(tab)
    return {**pick(entries[0], entry_keys), **shared, "prompts": [pick(entry, entry_keys) for entry in entries]}


def _build_request(
    tab: Mapping[str, Any],
    preset_id: str,
    mode: str,
    tab_id: Optional[str],
    prompts: List[Dict[str, str]],
    form_data: Mapping[str, Any],
    variables: Optional[Dict[str, str]],
    batch_prompts: bool,
) -> Dict[str, Any]:
    request: Dict[str, Any] = {"preset_id": preset_id, "prompts": prompts}
    if batch_prompts:
        request["batch_prompts"] = True
    request["mode"] = mode
    if tab.get("selectedVariant") is not None:
        request["form_name"] = tab["selectedVariant"]
    request["form_data"] = dict(form_data)
    if tab.get("autoTagIds"):
        request["tag_ids"] = tab["autoTagIds"]
    if tab.get("autoCollectionIds"):
        request["collection_ids"] = tab["autoCollectionIds"]
    if variables is not None:
        request["variables"] = variables
    if tab_id is not None:
        request["tab_id"] = tab_id
    if tab.get("sourcePromptId") is not None:
        request["source_prompt_id"] = tab["sourcePromptId"]
    request["prompt_state"] = _prompt_state(tab, batch_prompts)
    request["segments"] = build_segments_payload(tab, batch_prompts)
    return request


def resolve(
    entry: Mapping[str, Any],
    preset_id: str,
    mode: str,
    preset_vars: Optional[Mapping[str, Any]] = None,
    overrides: Optional[Mapping[str, Any]] = None,
    randomness: Optional[Randomness] = None,
    can_batch: bool = False,
    tab_id: Optional[str] = None,
) -> ResolvedRequest:
    sources = randomness or Randomness()
    random, now = sources.random, sources.now
    vars_ = preset_vars or {}
    merged = apply_overrides(entry, overrides)

    if director_active(vars_, mode):
        raise RequestRefused("session_needs_editor", DIRECTOR_REFUSAL, {"supported": DIRECTOR_SUPPORTED})

    raw: Dict[str, Any] = {
        "promptSegments": [],
        "negativePromptSegments": [],
        **merged,
        "selectedPreset": preset_id,
        "selectedMode": mode,
        "selectedVariant": merged.get("selectedVariant"),
        "formData": merged.get("formData") or {},
        "prompt": merged.get("prompt") if merged.get("prompt") is not None else "",
        "negativePrompt": merged.get("negativePrompt") if merged.get("negativePrompt") is not None else "",
    }
    tab = seed_segments_from_text(raw)
    if "promptTabs" in raw:
        tab["promptTabs"] = seed_prompt_tabs(raw["promptTabs"])

    segment_join = "paragraph" if (vars_.get("prompt") or {}).get("segment_join") == "paragraph" else "space"
    relay_modes = vars_.get("prompt_relay_modes") or []
    relay_active = bool(mode) and mode in relay_modes
    promptless = is_promptless_mode(vars_, mode) or is_promptless_mode(vars_, mode, tab["formData"])
    optional = (
        _lists_mode(vars_, "optional_prompt_modes", mode, None) and not is_promptless_mode(vars_, mode)
    ) or is_optional_prompt_mode(vars_, mode, tab["formData"])

    entries = prompt_list(tab)
    active = active_prompt_index(tab)
    positive, positive_changed = shuffle_segment_chips(entries[active].get("promptSegments"), random)
    negative, negative_changed = shuffle_segment_chips(entries[active].get("negativePromptSegments"), random)

    form_data: Mapping[str, Any] = tab["formData"]
    batch_prompts = False
    if relay_active:
        relay = tab.get("promptRelay") if _record(tab.get("promptRelay")) else {}
        timeline = relay.get("timeline") if _record(relay.get("timeline")) else None
        ordered = sorted(
            [s for s in ((timeline or {}).get("segments") or []) if _record(s)],
            key=lambda s: s.get("start") or 0,
        )
        global_prompt = (relay.get("global_prompt") or "").strip()
        joined = " | ".join(text for text in ((s.get("text") or "").strip() for s in ordered) if text)
        form_data = {
            **tab["formData"],
            "global_prompt": global_prompt,
            "timeline": timeline if timeline is not None else {"duration": 5, "fps": 24, "segments": []},
        }
        prompts = [{"positive": " | ".join(part for part in (global_prompt, joined) if part), "negative": ""}]
    else:
        batch_prompts = can_batch and len(entries) > 1
        sent = entries if batch_prompts else entries[:1]
        if batch_prompts and not optional and first_empty_prompt(sent) >= 0:
            raise RequestRefused("no_prompt", "One of the prompts in this session is empty.")
        prompts = []
        for index, item in enumerate(sent):
            entry_positive = positive if index == active else shuffle_segment_chips(item.get("promptSegments"), random)[0]
            entry_negative = negative if index == active else shuffle_segment_chips(item.get("negativePromptSegments"), random)[0]
            prompts.append(
                {
                    "positive": flatten_rich_segments(entry_positive, segment_join).strip(),
                    "negative": flatten_rich_segments(entry_negative, segment_join).strip(),
                }
            )

    if not any(p["positive"].strip() for p in prompts) and not promptless and not optional:
        raise RequestRefused(
            "no_prompt", "The session has no prompt for this mode. Pass overrides.prompt or add one in the session."
        )

    readers = _condition_readers([text for p in prompts for text in (p["positive"], p["negative"])])
    independent, usage_counts = apply_independent_usages(prompts, tab.get("variables"), readers)
    wire, rolls = build_variables_for_submit(tab.get("variables"), random, now, usage_counts, readers)
    request = _build_request(
        tab, tab["selectedPreset"], mode, tab_id, independent, form_data, wire or None, batch_prompts
    )

    notes: List[str] = []
    if merged.get("compare"):
        notes.append("Ignored the saved compare settings: grids have their own route.")
    return ResolvedRequest(request=request, settings_hash=settings_hash(mode_payload({mode: entry}, mode)), notes=notes, rolls=rolls)
