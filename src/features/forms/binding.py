"""
The `bind_form` server boundary.

Today the production generation path accepts `GenerationRequest.form_data` as an
almost-unvalidated `Dict[str, Any]` and hands it straight to the template
context - `required`, numeric ranges, accepted values and falsy defaults are
presentation metadata, not request invariants (the "generation bypasses the
form schema" gap).

`bind_form` is the one place that changes: given a preset's already-loaded
`PresetTemplate`, a mode, an optional form variant name, and the raw wire
`form_data`, it

  1. resolves/validates `form_name` (unknown -> `FormNotFoundException`);
  2. strips keys that don't correspond to a declared field (logged);
  3. applies the field's schema `default` for every declared key absent from
     the submission;
  4. resolves each absent field's declared `reactions:` against the bound
     values - the same `when`/`then` engine
     frontend/src/lib/form/reactions.ts applies before a browser session
     ever POSTs, so a client that omits a field gets the value a full
     browser submission would have sent, not just the field's raw static
     default. A client-sent value always wins over a reaction. See
     `_apply_reactions`;
  5. validates the result (required / numeric range / static select options /
     checkbox bool), raising `FormBindingError` with every problem found;
  6. leniently coerces numeric/boolean strings from older clients, logging
     each coercion;
  6b. runs a registered field type's own `input()` validator when one
     exists (`_INPUT_VALIDATORS`: resolution format, LoRA strength/count,
     and image/video/audio/media shape - string vs. passthrough media-ref
     dict vs. legacy base64 dict, `multi`/`max_items`/per-item `label`, plus
     each item's `accepted_types`/`max_resolution`/duration limits; see
     `src/features/fields/media_input.py`);
  7. checks that every image/video/audio/media/file field's value(s)
     (string OR `{path, relative_path, ...}` dict, single or per-multi-item)
     resolve inside the user's storage root (the audit's P0 media-ownership
     finding, generalized from `src/features/video_director/normalize.py`'s
     per-field special case) - containment only, shape validation is step 6b's;
  8. returns an immutable `BoundForm`.

Model references (`model:<id>`) are left untouched here - they still resolve
in `src/features/models/form_refs.py`, AFTER backend selection (a model ref
can only become an engine-native string once the executing backend is known;
see `docs/backends.md`). Moving that resolution earlier would need backend
selection to move earlier too, which is out of scope for this increment -
`bind_form` only validates that a `model:<id>` string keeps its prefix shape
untouched (never treated as a media path, never stripped, never coerced).
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field as dc_field
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.features.forms.exceptions import FormNotFoundException
from src.features.fields.lora_picker import LoraPicker
from src.features.fields.resolution import Resolution
from src.features.fields.image import Image
from src.features.fields.video import Video
from src.features.fields.audio import Audio
from src.features.fields.media import Media
from src.features.models.form_refs import is_model_ref
from src.platform.templating import TemplateProcessor
from src.platform.util.path_resolution import resolve_within
from src.features.presets.templates import (
    FieldTemplate,
    FormTemplate,
    ModeTemplate,
    PresetTemplate,
    default_form_name,
    sorted_forms,
)

logger = logging.getLogger(__name__)

_warned_no_storage_dir_for_field: set = set()


def _expand_form_fields(fields: List[FieldTemplate], preset_template: PresetTemplate) -> List[FieldTemplate]:
    """Expand `@loop`/external-`children` form field declarations into their
    concrete per-iteration fields, exactly like the form-schema endpoint and
    `scripts/preset_render.py`'s golden harness do (see `PresetFormSerializer`
    docstring notes in that script for why it - not the unrelated,
    known-dead `PresetProcessor._process_form_fields` - is the correct
    reusable expander). A throwaway `TemplateProcessor`/`PresetFormSerializer`
    pair is fine here: neither settings-manager-backed rendering nor the
    preset loader is exercised by field-tree expansion, only `loop.index`
    substitution and external-file loading.
    """
    from src.features.presets.form_serializer import PresetFormSerializer

    template_processor = TemplateProcessor(settings_manager=None)
    form_serializer = PresetFormSerializer(preset_loader=None, template_processor=template_processor)
    context = {"paths": {"preset": preset_template.path}}
    return form_serializer._resolve_external_children(fields, context)

# Registered field-type names whose value is a plain string path (see
# src/features/fields/builtin.py). `video_director`/`prompt_timeline`/`llm` carry
# their own nested media and are normalized by their own boundary
# (normalize_video_director for the former); this set is deliberately only
# the ordinary single-path fields the audit flagged.
MEDIA_FIELD_TYPES = frozenset({"image", "video", "audio", "media", "file"})
_BOOL_FIELD_TYPES = frozenset({"checkbox", "boolean"})
_NUMERIC_FIELD_TYPES = frozenset({"slider", "number", "seed", "integer", "stepper"})
_SELECT_FIELD_TYPES = frozenset({"select"})

# Field types whose own registered `input()` implements validation that none
# of the generic buckets above cover (resolution format, LoRA strength
# clamping/cardinality, media shape/multi/max_items/label) - without this,
# a malformed "WIDTHxHEIGHT" string or an unbounded/out-of-range LoRA chain
# reaches the pipeline unchecked (late failure or avoidable OOM instead of a
# submission-time error). Instantiated once, statelessly: `.input()` on all
# of these needs no preset/template context - only their `.output()`
# schema-rendering path does.
#
# `image`/`video`/`audio`/`media` (each its own class - `media` is a
# distinct generic type, not a reuse of `Image` - matching
# src/features/fields/builtin.py's type->class table) run BEFORE
# `_check_media_containment` below - shape/multi/max_items/label/
# accepted_types/max_resolution/duration validation is this validator's job,
# path containment is `_check_media_containment`'s; see
# `src/features/fields/media_input.py` for the shared shape+constraint
# contract.
_INPUT_VALIDATORS = {
    "resolution": Resolution(None),
    "lora_picker": LoraPicker(None),
    "image": Image(None),
    "video": Video(None),
    "audio": Audio(None),
    "media": Media(None),
}


class FormBindingError(ValueError):
    """Raised with every problem found, not just the first.

    Carries both the flat, human-readable `errors` list (`"{name}: {message}"`,
    kept for backward compatibility with existing callers/`str(e)` summaries)
    and the structured `field_errors` dict (field name -> list of messages,
    WITHOUT the redundant `"{name}: "` prefix since the key already carries
    the name) so API callers can surface per-field validation results (e.g.
    HTTP 422 with inline form errors) instead of one flat string.
    """

    def __init__(
        self,
        errors: List[str],
        field_errors: Optional[Dict[str, List[str]]] = None,
        coercions: Optional[List[str]] = None,
        stripped: Optional[List[str]] = None,
    ):
        self.errors = list(errors)
        self.field_errors: Dict[str, List[str]] = (
            {k: list(v) for k, v in field_errors.items()} if field_errors else {}
        )
        self.coercions: List[str] = list(coercions) if coercions else []
        self.stripped: List[str] = list(stripped) if stripped else []
        super().__init__("; ".join(self.errors))


@dataclass(frozen=True)
class BoundForm:
    """The result of binding raw wire `form_data` against a preset's form schema."""

    values: Dict[str, Any]
    form_name: str
    coercions: List[str] = dc_field(default_factory=list)
    stripped: List[str] = dc_field(default_factory=list)
    # Names of fields whose value was server-injected because an admin
    # `field_overrides` entry locked (`editable: false`) or hid
    # (`visible: false`) them - the client-supplied value, if any, was
    # discarded. Callers that separately enforce model access must skip
    # verification for `model:<id>` refs living under these field
    # names: an admin-pinned hidden/locked model default bypasses the user's
    # own model-access scope by design.
    admin_pinned: List[str] = dc_field(default_factory=list)


def bind_form(
    preset_template: PresetTemplate,
    mode: str,
    form_name: Optional[str],
    raw_form_data: Optional[Dict[str, Any]],
    user_id: Optional[str] = None,
    *,
    storage_dir: Optional[str] = None,
    field_overrides: Optional[Dict[str, Dict[str, Any]]] = None,
) -> BoundForm:
    """Bind `raw_form_data` against `preset_template`'s form for `mode`.

    Args:
        preset_template: The preset's already-loaded template.
        mode: Generation mode (e.g. "txt2img").
        form_name: Requested form variant name, or `None` for the default.
        raw_form_data: The wire `form_data` dict (may be `None`/empty).
        user_id: The authenticated user, for logging only - media containment
            is enforced via `storage_dir`, which the caller resolves (e.g.
            `SettingsManager.get_file_storage_directory(user_id)`, exactly as
            `normalize_video_director`'s caller does in the orchestrator).
        storage_dir: The user's file storage root. When `None`, media
            containment cannot be checked and is skipped (logged) - callers
            that have a user should always pass this.
        field_overrides: Admin per-field overrides for this mode,
            `{field_name: {"default"?, "editable"?, "visible"?}}` - the stored
            value of `presets.form_overrides[mode]`. A field with
            `editable: false` or `visible: false` never takes the client's
            wire value: the override's `default` (falling back to the
            field's own declared `default`) is used instead, and a
            differing client-supplied value is logged at WARNING, never
            rejected with a 422. A plain `default` override (no
            `editable`/`visible: false`) only substitutes when the key is
            absent from the wire submission, exactly like the field's own
            declared default.

    Returns:
        BoundForm

    Raises:
        FormNotFoundException: unknown `form_name`, or the mode has no forms.
        FormBindingError: any validation failure (required/range/options).
    """
    preset_id = preset_template.id
    mode_data: Optional[ModeTemplate] = (preset_template.modes or {}).get(mode)
    if mode_data is None:
        raise FormNotFoundException(preset_id, mode, form_name)

    form = _resolve_form(mode_data, preset_id, mode, form_name)

    # `form.fields` may still contain unexpanded `@loop`/external-`children`
    # field declarations (e.g. a numbered `controlnet_{{ loop.index }}_model`
    # generator) - the same expansion the form-schema endpoint and the
    # `scripts/preset_render.py` golden harness already run through
    # `PresetFormSerializer`. Binding has to see the CONCRETE per-iteration
    # field names (`controlnet_1_model`, ...), not the template declaration,
    # or every numbered field would look "unknown" and get stripped/defaulted
    # to nothing while pipeline.yml still references it directly.
    resolved_fields = _expand_form_fields(form.fields, preset_template)

    field_index: Dict[str, FieldTemplate] = {}
    _flatten_fields(resolved_fields, field_index)

    raw = dict(raw_form_data or {})
    errors: List[str] = []
    field_errors: Dict[str, List[str]] = {}
    coercions: List[str] = []
    stripped: List[str] = []

    values: Dict[str, Any] = {}
    admin_pinned: List[str] = []
    # Fields eligible to have their default overridden by a matching
    # `reactions:` set_value: absent from the wire payload AND not
    # admin-pinned. A client-sent value always wins, and an admin lock/hide
    # always wins - reactions only ever fill in what the field's own static
    # `default` would otherwise have supplied.
    reaction_candidates: set = set()

    # Known keys: strip-unknown, apply-default. Reactions resolve BEFORE
    # coercion/validation/media-containment run (below), on the trigger
    # fields' bound values (wire or default), so a resolved `set_value`
    # still goes through the same numeric/option checks a client-sent value
    # would.
    for name, spec in field_index.items():
        override = (field_overrides or {}).get(name)
        locked = bool(override) and override.get("editable") is False
        hidden = bool(override) and override.get("visible") is False
        override_default = override["default"] if override and "default" in override else spec.default

        if locked or hidden:
            if name in raw and raw[name] != override_default:
                logger.warning(
                    f"bind_form: ignoring client-supplied value for "
                    f"{'locked' if locked else 'hidden'} field '{name}' "
                    f"(preset '{preset_id}' mode '{mode}', admin override in effect)"
                )
            value = override_default
            admin_pinned.append(name)
        elif name in raw:
            value = raw[name]
        else:
            value = override_default
            reaction_candidates.add(name)

        values[name] = value

    _apply_reactions(field_index, values, reaction_candidates, preset_id=preset_id, mode=mode)

    for name, spec in field_index.items():
        value = values[name]
        value = _coerce_leniently(value, spec, name, coercions)
        _validate_field(name, value, spec, errors, field_errors)
        value = _run_input_validator(name, value, spec, errors, field_errors)
        if _is_media_path_field(spec) and not is_model_ref(value):
            value = _check_media_containment(
                value, storage_dir, name, errors, field_errors, configuration=spec.configuration
            )
        values[name] = value

    # Unknown keys are stripped, not silently forwarded - but never for
    # `model:<id>` refs (which key a form field but may be nested inside a
    # nested nested shape the flattened field index doesn't itself know, e.g.
    # `video_director`/composite fields carrying their own model refs), and
    # never for a handful of request-level keys that ride alongside
    # `form_data` in current submissions but are not declared preset fields.
    # `timeline`: presets/comfyui/LTX-2-3/official/modes/prompt_relay/pipeline.yml
    # reads `form.timeline` directly - an LTX Director timeline document
    # submitted the same out-of-band way `video_director` is, but with no
    # normalizer of its own yet (unlike video_director's
    # normalize_video_director()). Passing it through here at least stops
    # bind_form from silently stripping it; the missing normalizer is a
    # separate, pre-existing gap (the mode already had no backend validation
    # for this key before the templating rework either).
    # `<field>__origin`: a provenance sibling key next to a media
    # field, `{"generation_id": ..., "file_index": ...}` - the orchestrator's
    # submission path parses/validates it (`_parse_generation_origins` /
    # `_validate_generation_origins`) and persists it once the generation
    # exists (`generation_sources` table). Passed through only when its base
    # field IS a declared field - an origin key riding alongside a field the
    # preset doesn't even have would be meaningless.
    # `music_director`: the Music Director document, normalized downstream by
    # normalize_music_director() exactly like video_director's — missing from
    # this set once, which silently stripped the document and let doc-less
    # renders reach the generator ("'caption' cannot be empty", 2026-08-18).
    _PASSTHROUGH_KEYS = {"video_director", "music_director", "llm", "prompt_timeline", "timeline"}
    _ORIGIN_SUFFIX = "__origin"
    for key, value in raw.items():
        if key in field_index:
            continue
        if key in _PASSTHROUGH_KEYS or is_model_ref(value):
            values[key] = value
            continue
        if key.endswith(_ORIGIN_SUFFIX) and key[: -len(_ORIGIN_SUFFIX)] in field_index:
            values[key] = value
            continue
        stripped.append(key)

    if stripped:
        logger.debug(
            f"bind_form: stripped {len(stripped)} unknown key(s) from form_data "
            f"for preset '{preset_id}' mode '{mode}' form '{form.name}': {stripped}"
        )

    if errors:
        raise FormBindingError(errors, field_errors, coercions, stripped)

    return BoundForm(
        values=values, form_name=form.name, coercions=coercions, stripped=stripped,
        admin_pinned=admin_pinned,
    )


def _resolve_form(
    mode_data: ModeTemplate, preset_id: str, mode: str, form_name: Optional[str]
) -> FormTemplate:
    if not mode_data.forms:
        raise FormNotFoundException(preset_id, mode, form_name)

    if form_name:
        for form in mode_data.forms:
            if form.name == form_name:
                return form
        raise FormNotFoundException(preset_id, mode, form_name)

    target_name = default_form_name(mode_data)
    for form in sorted_forms(mode_data):
        if form.name == target_name:
            return form
    return sorted_forms(mode_data)[0]


def _flatten_fields(fields: Optional[List[FieldTemplate]], out: Dict[str, FieldTemplate]) -> None:
    """Depth-first walk collecting every NAMED field (leaves and named
    containers alike) into a flat name -> FieldTemplate index.

    All 52 current modes submit one flat `form_data` dict keyed by leaf field
    name regardless of tab/row/group nesting (see the audit: "2,157 [get_form]
    paths contain only one field key") - the tree only matters for rendering,
    not for the wire shape.
    """
    for f in fields or []:
        if f.name:
            out[f.name] = f
        if isinstance(f.children, list):
            _flatten_fields(f.children, out)


# The ONE reaction evaluator on the server side, mirroring
# frontend/src/lib/form/reactions.ts's `operators` table - the full closed
# 12-operator set from `OPERATORS` in src/features/presets/schema.py, kept in
# lockstep with it (see tests/features/forms/test_reaction_resolution.py's
# operator-set parity test). Every `when` dict reaching this evaluator has
# already been normalized by `ConditionSpec._resolve_sugar_operator` at
# preset-load time (src/features/presets/schema.py) - both the sugar form
# (`{field, equals: x}`) and the explicit form (`{field, operator: "equals",
# value: x}`) dump to the same `{"field", "operator", "value", ...}` shape,
# so this reads `operator`/`value` directly rather than re-detecting sugar
# keys the way the frontend (which never round-trips through model_dump)
# has to.
# JS equality helpers. Python's `==` is not a stand-in for either JS
# comparison operator the frontend's `operators` table uses: `bool` is an
# `int` subclass in Python, so bare `==` lets `False == 0` and `True == 1`
# slip through, where JS's `===`/SameValueZero treat booleans and numbers as
# distinct types with no cross-type equality (`false === 0` is `false`,
# `[0].includes(false)` is `false`). Everything else `==` already gets right
# for the value shapes forms produce: string-vs-number is already `False` in
# Python the same as JS (`"1" == 1` is `False` in both), int/float compare by
# value in both (JS has one number type, so `1 === 1.0`), and `None` only
# equals `None` the same way `null` only equals `null`/`undefined` in JS.
# Deliberately NOT attempting to mirror JS's reference equality for
# arrays/objects (`[1] === [1]` is always `false` in JS, but no preset today
# compares a list/dict-valued field with `equals`/`in` - see the audit note
# in this module's tests) - Python structural equality is used for those, a
# narrower and pragmatic divergence, not the bool/number hazard this exists
# to close.
def _js_strict_eq(a: Any, b: Any) -> bool:
    """Mirrors JS `===` for `equals`/`not_equals`. NaN falls out for free:
    Python's `float('nan') == float('nan')` is already `False`, same as JS's
    `NaN === NaN`."""
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    return a == b


def _js_same_value_zero_eq(a: Any, b: Any) -> bool:
    """Mirrors JS SameValueZero, which `Array.prototype.includes` (and so
    `in`/`not_in`) uses instead of `===`: identical to `_js_strict_eq` except
    NaN equals NaN (`[NaN].includes(NaN)` is `true` in JS, unlike `NaN ===
    NaN`)."""
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if isinstance(a, float) and isinstance(b, float) and a != a and b != b:
        return True
    return a == b


_REACTION_OPERATORS: Dict[str, Any] = {
    "equals": lambda fv, cv: _js_strict_eq(fv, cv),
    "not_equals": lambda fv, cv: not _js_strict_eq(fv, cv),
    "in": lambda fv, cv: isinstance(cv, list) and any(_js_same_value_zero_eq(fv, item) for item in cv),
    "not_in": lambda fv, cv: isinstance(cv, list) and not any(_js_same_value_zero_eq(fv, item) for item in cv),
    "greater_than": lambda fv, cv: _reaction_gt(fv, cv),
    "less_than": lambda fv, cv: _reaction_lt(fv, cv),
    "greater_than_or_equals": lambda fv, cv: _reaction_gte(fv, cv),
    "less_than_or_equals": lambda fv, cv: _reaction_lte(fv, cv),
    "contains": lambda fv, cv: _reaction_stringify(cv) in _reaction_stringify(fv),
    "not_contains": lambda fv, cv: _reaction_stringify(cv) not in _reaction_stringify(fv),
    "is_empty": lambda fv, cv=None: _reaction_is_empty(fv),
    "is_not_empty": lambda fv, cv=None: not _reaction_is_empty(fv),
}

_MAX_REACTION_ITERATIONS = 20

# `reactions.ts`'s `greater_than`/`less_than`/`*_or_equals` run
# `parseFloat(fieldValue) > parseFloat(conditionValue)` (etc.) inside a
# try/catch that `parseFloat` never actually throws into - its real failure
# mode is returning `NaN`, and every JS comparison against `NaN` is `false`,
# for `>`/`<`/`>=`/`<=` alike. This regex mirrors `parseFloat`'s "parse the
# longest valid leading numeric prefix, else NaN" behavior: leading
# whitespace, an optional sign, then either the literal token `Infinity`
# (parseFloat special-cases this exact spelling - case-sensitive, no
# `-Infinty`/`inf` alias) or digits with an optional fraction/exponent. A
# comparison against a non-numeric string, `None`, or a dict resolves to
# `None` here (this module's NaN) rather than raising - Python's bare `>`/`<`
# raise `TypeError` on `None`/str-vs-number, which would abort a generation
# instead of just leaving one reaction unmatched.
_FLOAT_PREFIX_RE = re.compile(
    r"^\s*[+-]?(?:Infinity|\d+\.?\d*(?:[eE][+-]?\d+)?|\.\d+(?:[eE][+-]?\d+)?)"
)


def _reaction_parse_float(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, list):
        # `parseFloat([12])` is `12` in JS: a non-string argument is
        # ToString-coerced before parsing (`String([12]) === "12"`,
        # `String([1, 2]) === "1,2"` -> parseFloat gives `1`, `String([])
        # === ""` -> NaN). Reuse `contains`'s `String(x)` coercion rather
        # than a second implementation.
        value = _reaction_stringify(value)
    if not isinstance(value, str):
        # A dict (or any other non-stringifiable-here shape) ToStrings to
        # something that never matches a numeric prefix in JS either
        # (`String({}) === "[object Object]"`), so `None`/NaN is already
        # the right answer without walking that string.
        return None
    m = _FLOAT_PREFIX_RE.match(value)
    if not m:
        return None
    try:
        return float(m.group(0))
    except ValueError:
        return None


def _reaction_gt(fv: Any, cv: Any) -> bool:
    a, b = _reaction_parse_float(fv), _reaction_parse_float(cv)
    return a is not None and b is not None and a > b


def _reaction_lt(fv: Any, cv: Any) -> bool:
    a, b = _reaction_parse_float(fv), _reaction_parse_float(cv)
    return a is not None and b is not None and a < b


def _reaction_gte(fv: Any, cv: Any) -> bool:
    a, b = _reaction_parse_float(fv), _reaction_parse_float(cv)
    return a is not None and b is not None and a >= b


def _reaction_lte(fv: Any, cv: Any) -> bool:
    a, b = _reaction_parse_float(fv), _reaction_parse_float(cv)
    return a is not None and b is not None and a <= b


def _reaction_is_empty(value: Any) -> bool:
    """Mirrors `operators.is_empty` in reactions.ts exactly: `None`/missing is
    empty; string/list/dict emptiness is length/key-count; every other type
    (int, float, bool included) falls through to `False`. A numeric slider at
    `0` or an unchecked `False` checkbox is therefore NOT empty - the frontend
    deliberately does not use a bare `!value` truthiness check here, so there
    is no 0-or-False trap to mirror or correct."""
    if value is None:
        return True
    if isinstance(value, (str, list, dict)):
        return len(value) == 0
    return False


def _js_number_to_string(value: Any) -> str:
    """Mirror JS's `Number.prototype.toString` for the one case Python's
    `str()` gets wrong here: JS has a single numeric type, so an integral
    float stringifies without a decimal point (`String(12.0) === "12"`),
    while Python's `str(12.0) == "12.0"`. Also spells the non-finite values
    the way JS does (`"Infinity"`/`"-Infinity"`/`"NaN"` vs Python's
    `"inf"`/`"-inf"`/`"nan"`). `int` values already print identically in
    both languages and pass through `str()` unchanged.
    """
    if isinstance(value, float):
        if value != value:
            return "NaN"
        if math.isinf(value):
            return "Infinity" if value > 0 else "-Infinity"
        if value.is_integer():
            return str(int(value))
    return str(value)


def _reaction_stringify(value: Any) -> str:
    """Replicate the frontend's `String(x)` coercion for `contains`/
    `not_contains` (frontend/src/lib/form/reactions.ts: `String(fieldValue)
    .includes(String(conditionValue))`) and for `parseFloat`'s non-string
    coercion. The only current preset usage is a multi-select field whose
    value is a list of option strings (e.g. `detailer: ["fix_faces",
    "fix_hands"]`) - JS's `Array.prototype.toString` comma-joins elements,
    which is a substring match, not array membership, so a list value is
    joined the same way here rather than checked with `in`.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return ",".join(_reaction_stringify(v) for v in value)
    if value is None:
        return ""
    if isinstance(value, (int, float)):
        return _js_number_to_string(value)
    if isinstance(value, dict):
        # JS: `String({})` is the generic `Object.prototype.toString`
        # fallback `"[object Object]"` for any plain object, regardless of
        # its keys. No preset today feeds a dict into `contains`/
        # `not_contains`/a numeric comparison, so this is audit completeness
        # rather than an observed bug.
        return "[object Object]"
    return str(value)


def _evaluate_reaction_condition(when: Any) -> Optional[Any]:
    """Return a `(field, evaluator)` pair for a single-condition `when` dict,
    or `None` if `when` isn't a shape this evaluator supports: a logical
    AND/OR group (`{"logic", "conditions"}`), a list-of-conditions, or an
    operator outside `_REACTION_OPERATORS`. Neither shape occurs in the
    preset tree today; fail-open (the caller logs and treats the reaction as
    not matching) rather than guessing. `_REACTION_OPERATORS` now covers the
    full 12-operator set `ConditionSpec` accepts (see the parity test in
    tests/features/forms/test_reaction_resolution.py), so an unrecognized
    operator reaching here is a real drift, not an expected gap - the caller
    logs it at WARNING, not DEBUG.
    """
    if not isinstance(when, dict) or "field" not in when:
        return None
    fn = _REACTION_OPERATORS.get(when.get("operator"))
    if fn is None:
        return None
    return when["field"], when.get("value"), fn


def _reaction_matches(
    when: Any, values: Dict[str, Any], *, preset_id: str, mode: str, field_name: str
) -> bool:
    resolved = _evaluate_reaction_condition(when)
    if resolved is None:
        logger.warning(
            f"bind_form: skipping reaction on field '{field_name}' with an "
            f"unsupported 'when' shape (preset '{preset_id}' mode '{mode}'): {when!r}"
        )
        return False
    trigger_field, condition_value, fn = resolved
    try:
        return bool(fn(values.get(trigger_field), condition_value))
    except Exception as e:
        logger.warning(
            f"bind_form: reaction condition on field '{field_name}' raised "
            f"(preset '{preset_id}' mode '{mode}'): {e!r}"
        )
        return False


def _apply_reactions(
    field_index: Dict[str, FieldTemplate],
    values: Dict[str, Any],
    candidates: set,
    *,
    preset_id: str,
    mode: str,
) -> None:
    """Fill in `values` for fields the wire payload omitted (`candidates`)
    whose declared `reactions:` match, mirroring DynamicForm.svelte's
    reactive reprocessing loop (frontend/src/lib/form/reactions.ts's
    `processSchemaWithReactions`, re-run to a fixed point every time
    `formData` changes - see the reactive block in DynamicForm.svelte).

    Only `set_value` affects binding. `set_visibility`/`set_disabled` are
    recognized action keys but are genuine no-ops here, not an unhandled
    gap: the frontend's own `flattenFormData`/`getFormData` submit every
    field's current value regardless of visibility or disabled state (see
    DynamicForm.svelte's comment on the reactive block - "submission ...
    is unaffected"), so a real browser session never omits a hidden or
    disabled field from the wire either. `update_options`,
    `update_validation`, and `set_filter_tags` only affect presentation
    (option lists / validation copy / model-picker filtering) and never a
    bound value, so they need no handling here.

    Evaluates each pass against a frozen snapshot of `values` (Jacobi-style,
    like the frontend's snapshot-then-batch-commit reactive tick) rather
    than mutating `values` field-by-field mid-pass, so evaluation order
    inside one pass can't change the result - only a genuinely chained
    reaction (field A's reaction depends on field B, which is itself only
    resolved by a reaction) needs a second pass, which the outer loop
    provides up to `_MAX_REACTION_ITERATIONS`. No preset today chains more
    than one level deep; the cap is a defensive guard against a future
    cyclic definition, not an expected case.
    """
    fields_with_reactions = [
        (name, spec) for name, spec in field_index.items()
        if spec.reactions and name in candidates
    ]
    if not fields_with_reactions:
        return

    for _ in range(_MAX_REACTION_ITERATIONS):
        snapshot = dict(values)
        updates: Dict[str, Any] = {}
        for name, spec in fields_with_reactions:
            resolved = snapshot[name]
            for reaction in spec.reactions:
                if not isinstance(reaction, dict):
                    continue
                when = reaction.get("when")
                then = reaction.get("then") or {}
                if not _reaction_matches(when, snapshot, preset_id=preset_id, mode=mode, field_name=name):
                    continue
                if then.get("set_value") is not None:
                    resolved = then["set_value"]
            if resolved != snapshot[name]:
                updates[name] = resolved
        if not updates:
            return
        values.update(updates)

    logger.warning(
        f"bind_form: reaction resolution for preset '{preset_id}' mode '{mode}' "
        f"did not converge after {_MAX_REACTION_ITERATIONS} iterations"
    )


def _coerce_leniently(
    value: Any, field: FieldTemplate, path: str, coercions: List[str]
) -> Any:
    """One deliberate leniency: numeric/boolean strings from older clients."""
    if not isinstance(value, str):
        return value

    if field.type in _NUMERIC_FIELD_TYPES:
        try:
            coerced: Any = int(value)
        except ValueError:
            try:
                coerced = float(value)
            except ValueError:
                return value
        coercions.append(f"{path}: {value!r} -> {coerced!r} (numeric string)")
        return coerced

    if field.type in _BOOL_FIELD_TYPES and value.lower() in ("true", "false"):
        coerced = value.lower() == "true"
        coercions.append(f"{path}: {value!r} -> {coerced!r} (boolean string)")
        return coerced

    return value


def _validate_field(
    name: str,
    value: Any,
    field: FieldTemplate,
    errors: List[str],
    field_errors: Dict[str, List[str]],
) -> None:
    def _fail(message: str) -> None:
        errors.append(f"{name}: {message}")
        field_errors.setdefault(name, []).append(message)

    if is_model_ref(value):
        return  # resolved later, per-backend; not this boundary's concern

    if field.required and (value is None or value == ""):
        _fail("required field is missing")
        return

    if value is None:
        return

    config = field.configuration or {}

    if field.type in _NUMERIC_FIELD_TYPES:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            _fail(f"expected a number, got {type(value).__name__} ({value!r})")
        else:
            minimum = config.get("min")
            maximum = config.get("max")
            if minimum is not None and value < minimum:
                _fail(f"{value} is below the minimum {minimum}")
            if maximum is not None and value > maximum:
                _fail(f"{value} exceeds the maximum {maximum}")

    if field.type in _BOOL_FIELD_TYPES and not isinstance(value, bool):
        _fail(f"expected a boolean, got {type(value).__name__}")

    if field.type in _SELECT_FIELD_TYPES:
        static_options = config.get("options")
        # Only enforced when options are statically declared in the preset
        # (a dynamic `file`/`files`-backed select can't be checked here).
        if isinstance(static_options, list) and static_options:
            allowed = {opt.get("value") for opt in static_options if isinstance(opt, dict)}
            if allowed and value not in allowed:
                _fail(f"{value!r} is not one of the declared options")


def _run_input_validator(
    name: str,
    value: Any,
    field: FieldTemplate,
    errors: List[str],
    field_errors: Dict[str, List[str]],
) -> Any:
    """Run the field type's own registered `input()` validator, if any.

    A `ValueError` becomes a normal field error (accumulated like every
    other check here) instead of propagating - one malformed field must not
    abort validation of the rest of the submission. On success, the
    validator's return value wins (e.g. LoRA entries with out-of-range
    strength get clamped, not just rejected).
    """
    if is_model_ref(value):
        return value  # resolved later, per-backend; not this boundary's concern
    validator = _INPUT_VALIDATORS.get(field.type)
    if validator is None:
        return value
    try:
        return validator.input(name, value, field.configuration or {})
    except ValueError as e:
        message = str(e)
        errors.append(f"{name}: {message}")
        field_errors.setdefault(name, []).append(message)
        return value


def _is_media_path_field(field: FieldTemplate) -> bool:
    return field.type in MEDIA_FIELD_TYPES


def _check_media_containment(
    value: Any,
    storage_dir: Optional[str],
    name: str,
    errors: List[str],
    field_errors: Dict[str, List[str]],
    configuration: Optional[Dict[str, Any]] = None,
) -> Any:
    """Reject an image/video/audio/media/file field whose value(s) don't
    resolve inside `storage_dir` after symlink resolution - containment
    ONLY. Shape validation (multi/max_items/label) is the registered field
    type's own `input()` job (`_run_input_validator`, which runs before
    this), for the types that have one (`image`/`video`/`audio`/`media` -
    see `_INPUT_VALIDATORS` above); this function doesn't duplicate it.

    Generalizes `video_director/normalize.py::_resolve_media_ref`'s
    containment check (the audit's recommended pattern) to every ordinary
    media field, not just Video Director's nested references. A missing
    `storage_dir` means the caller has no user context to check against;
    skip rather than guess (logged, not silently accepted as safe).

    A `configuration.multi: true` field carries a LIST of media items
    instead of one value - each item goes through the same per-item check
    below. A non-multi field is completely unaffected by this branch - same
    function, same return value, as before multi existed.
    """
    config = configuration or {}
    if not config.get("multi"):
        return _check_single_media_value(value, storage_dir, name, errors, field_errors)

    if not value:
        return value
    if not isinstance(value, list):
        # The registered field-type validator (Image/Video/Audio.input(),
        # via _INPUT_VALIDATORS) already rejects a non-list multi value -
        # this is defense in depth only, so containment doesn't try to
        # iterate a non-list value and crash.
        message = "expected a list of media items for a multi-item field"
        errors.append(f"{name}: {message}")
        field_errors.setdefault(name, []).append(message)
        return value

    return [_check_single_media_value(item, storage_dir, name, errors, field_errors) for item in value]


def _check_single_media_value(
    value: Any,
    storage_dir: Optional[str],
    name: str,
    errors: List[str],
    field_errors: Dict[str, List[str]],
) -> Any:
    if isinstance(value, dict):
        return _check_media_ref_dict(value, storage_dir, name, errors, field_errors)
    if not isinstance(value, str) or not value:
        return value
    if storage_dir is None:
        if name not in _warned_no_storage_dir_for_field:
            logger.warning(f"bind_form: no storage_dir available to check media containment for '{name}'")
            _warned_no_storage_dir_for_field.add(name)
        return value

    storage_path = Path(storage_dir).resolve()
    if resolve_within(storage_path, value) is None:
        message = f"media path {value!r} escapes the user's storage directory"
        errors.append(f"{name}: {message}")
        field_errors.setdefault(name, []).append(message)
        return value

    # Validate-only, exactly like `_check_media_ref_dict`. Returning the
    # resolved path instead would persist an absolute path into
    # `generations.form_data`, which history reuse replays - pinning the
    # storage root of the day - and would silently DOUBLE-PREFIX the upload
    # convention ('storage/uploads/...' is CWD-relative and already carries
    # the prefix; joined onto the root it lands inside the root twice over
    # and still passes the check above). `media_loader._resolve_media_path`
    # resolves both relative conventions and leaves absolutes alone, so rows
    # that already carry a rewritten path keep replaying.
    return value


def _check_media_ref_dict(
    value: Dict[str, Any],
    storage_dir: Optional[str],
    name: str,
    errors: List[str],
    field_errors: Dict[str, List[str]],
) -> Any:
    """Containment-check a `{path, relative_path, ...}` media reference.

    Validate-only: the dict is returned unmodified because downstream
    consumers (`video_director/normalize._resolve_media_ref`, the media
    pipes) re-resolve these keys themselves against the storage root -
    rewriting here would change the wire shape they key off. Every present
    path key must stay inside `storage_dir`; `_resolve_media_ref`'s lenient
    first-contained-key-wins order is a resolution strategy, not a
    validation one - a traversal in either key rejects the whole value.
    """
    raws = [r for r in (value.get("path"), value.get("relative_path")) if isinstance(r, str) and r]
    if not raws:
        return value
    if storage_dir is None:
        if name not in _warned_no_storage_dir_for_field:
            logger.warning(f"bind_form: no storage_dir available to check media containment for '{name}'")
            _warned_no_storage_dir_for_field.add(name)
        return value

    storage_path = Path(storage_dir).resolve()
    for raw in raws:
        if resolve_within(storage_path, raw) is None:
            message = f"media path {raw!r} escapes the user's storage directory"
            errors.append(f"{name}: {message}")
            field_errors.setdefault(name, []).append(message)
            return value
    return value
