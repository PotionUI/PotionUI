"""Tests for bind_form's server-side reaction resolution.

Field `reactions:` (`when: {field, equals/...} -> then: {set_value, ...}`)
were previously applied ONLY by the frontend
(frontend/src/lib/form/reactions.ts) before POSTing `form_data`. A client
that submits a partial payload - an API caller, an automation node, or any
non-browser caller - never ran that engine, so an omitted field fell back to
its raw static YAML `default`, which for several speed-profile-driven
presets (Z-Image, Krea-2, ...) happens to equal the FIRST profile's values,
silently ignoring whatever `speed_profile` (or similar trigger field) the
client actually chose. `bind_form` now evaluates the same reactions
server-side for any field the wire payload omits, so a thin client gets the
same resolved values a full browser session would have submitted.

See `src/features/forms/binding.py`: `_apply_reactions`,
`_evaluate_reaction_condition`, `_reaction_matches`.
"""

import math
import re
from pathlib import Path

import pytest

from src.features.forms.binding import _REACTION_OPERATORS, bind_form
from src.features.models.form_refs import make_model_ref
from src.features.presets.loader import PresetTemplateLoader
from src.features.presets.schema import OPERATORS
from src.features.presets.templates import FieldTemplate, FormTemplate, ModeTemplate, PresetTemplate

PRESETS_ROOT = Path(__file__).resolve().parents[3] / "content" / "presets"
REACTIONS_TS_PATH = (
    Path(__file__).resolve().parents[3] / "frontend" / "src" / "lib" / "form" / "reactions.ts"
)


def _flatten(fields, out=None):
    out = {} if out is None else out
    for f in fields or []:
        if f.name:
            out[f.name] = f
        if isinstance(f.children, list):
            _flatten(f.children, out)
    return out


def _required_model_refs(fields):
    """Every required `model`-typed field, filled with a dummy `model:<id>`
    ref - real presets require these, and bind_form's own model-access
    verification happens in the orchestrator, not here, so any well-formed
    ref clears `_validate_field`'s required check untouched."""
    flat = _flatten(fields)
    return {
        name: make_model_ref(f"dummy_{name}")
        for name, f in flat.items()
        if f.required and f.type == "model"
    }


def _field(name, type_="string", default=None, required=False, configuration=None, reactions=None):
    return FieldTemplate(
        type=type_,
        name=name,
        default=default,
        required=required,
        configuration=configuration,
        reactions=reactions,
    )


def _preset(fields, form_name="custom", mode="txt2img"):
    forms = [FormTemplate(name=form_name, fields=fields, default=True, order=0)]
    return PresetTemplate(
        id="preset_1",
        name="Preset One",
        version="1.0.0",
        path="/presets/preset_1",
        modes={mode: ModeTemplate(forms=forms, pipes=[])},
    )


def _load_zimage_txt2img():
    loader = PresetTemplateLoader([str(PRESETS_ROOT)])
    loader.load_presets()
    assert not loader.load_errors, loader.load_errors
    preset = next(p for p in loader.presets if p.path.endswith("/ZImage"))
    return preset


def _zimage_wire(preset, **overrides):
    forms = preset.modes["txt2img"].forms
    form = next((f for f in forms if f.default), forms[0])
    wire = _required_model_refs(form.fields)
    wire.update(overrides)
    return wire


class TestBE143SpeedProfileScenario:
    """The exact repro: a client posts only the trigger field."""

    def test_wire_speed_profile_base_resolves_base_recipe(self):
        preset = _load_zimage_txt2img()
        bound = bind_form(preset, "txt2img", None, _zimage_wire(preset, speed_profile="base"), "user_1")

        assert bound.values["speed_profile"] == "base"
        # Base recipe (tabs/advanced.yml), NOT turbo's values even though
        # turbo's steps/cfg happen to equal the fields' static `default`.
        assert bound.values["steps"] == 30
        assert bound.values["cfg"] == 4.0
        assert bound.values["sampler"] == "euler"

    def test_wire_speed_profile_turbo_resolves_turbo_recipe(self):
        preset = _load_zimage_txt2img()
        bound = bind_form(preset, "txt2img", None, _zimage_wire(preset, speed_profile="turbo"), "user_1")

        assert bound.values["steps"] == 8
        assert bound.values["cfg"] == 1.0

    def test_client_sent_value_beats_reaction(self):
        preset = _load_zimage_txt2img()
        bound = bind_form(
            preset, "txt2img", None,
            _zimage_wire(preset, speed_profile="base", steps=50), "user_1",
        )

        # Explicit client value wins over the base-profile reaction.
        assert bound.values["steps"] == 50
        # cfg was omitted, so it still resolves from the reaction.
        assert bound.values["cfg"] == 4.0

    def test_custom_profile_leaves_static_defaults(self):
        preset = _load_zimage_txt2img()
        bound = bind_form(preset, "txt2img", None, _zimage_wire(preset, speed_profile="custom"), "user_1")

        # "custom" only sets `set_disabled: false` - no set_value action - so
        # the field's own static default stands.
        assert bound.values["steps"] == 8
        assert bound.values["cfg"] == 1.0
        assert bound.values["sampler"] == "euler"


class TestUnsupportedShapes:
    def test_unsupported_operator_fails_open_to_static_default(self, caplog):
        # "starts_with" is deliberately not one of the 12 closed-set
        # operators (see OPERATORS in src/features/presets/schema.py and
        # _REACTION_OPERATORS in src/features/forms/binding.py) - it stands
        # in for a hypothetical future operator neither list recognizes yet.
        # This used to use "greater_than" as its example, but that operator
        # is now implemented (see TestNewOperatorsThroughBindForm), so it no
        # longer demonstrates an unsupported operator.
        preset = _preset([
            _field("profile", default="a"),
            _field(
                "steps", type_="slider", default=8,
                reactions=[
                    {"when": {"field": "profile", "operator": "starts_with", "value": "b"}, "then": {"set_value": 99}},
                ],
            ),
        ])
        with caplog.at_level("DEBUG"):
            bound = bind_form(preset, "txt2img", None, {"profile": "b"}, "user_1")

        assert bound.values["steps"] == 8
        assert any("unsupported" in r.message for r in caplog.records)

    def test_logical_and_group_fails_open(self):
        preset = _preset([
            _field("a", default=1),
            _field("b", default=2),
            _field(
                "target", type_="slider", default=0,
                reactions=[
                    {
                        "when": {"logic": "AND", "conditions": [{"field": "a", "operator": "equals", "value": 1}]},
                        "then": {"set_value": 42},
                    },
                ],
            ),
        ])
        bound = bind_form(preset, "txt2img", None, {}, "user_1")
        assert bound.values["target"] == 0

    def test_list_of_conditions_fails_open(self):
        preset = _preset([
            _field("a", default=1),
            _field(
                "target", type_="slider", default=0,
                reactions=[
                    {"when": [{"field": "a", "operator": "equals", "value": 1}], "then": {"set_value": 42}},
                ],
            ),
        ])
        bound = bind_form(preset, "txt2img", None, {}, "user_1")
        assert bound.values["target"] == 0


class TestSetVisibilityBinding:
    """`set_visibility` never gates value resolution - the frontend submits
    every field's current value regardless of visibility, so a hidden
    field's `set_value` reactions still apply, and the absence of a
    `set_value` action leaves the static default in place either way."""

    def test_visibility_reaction_does_not_blank_the_field(self):
        preset = _preset([
            _field("detailer", type_="select", default=[], configuration={"options": []}),
            _field(
                "face_strength", type_="slider", default=0.5,
                reactions=[
                    {"when": {"field": "detailer", "operator": "contains", "value": "fix_faces"}, "then": {"set_visibility": True}},
                    {"when": {"field": "detailer", "operator": "not_contains", "value": "fix_faces"}, "then": {"set_visibility": False}},
                ],
            ),
        ])

        hidden = bind_form(preset, "txt2img", None, {"detailer": []}, "user_1")
        assert hidden.values["face_strength"] == 0.5

        shown = bind_form(preset, "txt2img", None, {"detailer": ["fix_faces"]}, "user_1")
        assert shown.values["face_strength"] == 0.5

    def test_visibility_and_value_reactions_combine(self):
        preset = _preset([
            _field("profile", default="turbo"),
            _field(
                "steps", type_="slider", default=8,
                reactions=[
                    {"when": {"field": "profile", "operator": "equals", "value": "base"}, "then": {"set_value": 30, "set_visibility": True}},
                    {"when": {"field": "profile", "operator": "equals", "value": "turbo"}, "then": {"set_value": 8, "set_visibility": False}},
                ],
            ),
        ])
        bound = bind_form(preset, "txt2img", None, {"profile": "base"}, "user_1")
        assert bound.values["steps"] == 30


class TestContainsOperatorListSemantics:
    """`contains`/`not_contains` on a list-valued field replicate the
    frontend's `String(x).includes(...)` substring match (comma-joined
    stringified list), not Python `in` membership - see face-hand-detailer.yml."""

    def test_contains_matches_stringified_list(self):
        preset = _preset([
            _field("detailer", type_="select", default=[]),
            _field(
                "eye_strength", type_="slider", default=0.0,
                reactions=[
                    {"when": {"field": "detailer", "operator": "contains", "value": "fix_eyes"}, "then": {"set_value": 0.4}},
                ],
            ),
        ])
        bound = bind_form(preset, "txt2img", None, {"detailer": ["fix_faces", "fix_eyes"]}, "user_1")
        assert bound.values["eye_strength"] == 0.4

    def test_not_contains_when_absent_from_list(self):
        preset = _preset([
            _field("detailer", type_="select", default=[]),
            _field(
                "eye_strength", type_="slider", default=0.0,
                reactions=[
                    {"when": {"field": "detailer", "operator": "not_contains", "value": "fix_eyes"}, "then": {"set_value": -1.0}},
                ],
            ),
        ])
        bound = bind_form(preset, "txt2img", None, {"detailer": ["fix_faces"]}, "user_1")
        assert bound.values["eye_strength"] == -1.0


class TestChainedReactions:
    """A field's own resolved (reaction-set) value can itself be the trigger
    for another field's reaction, converging over the fixed-point loop -
    mirroring DynamicForm.svelte's repeated reactive reprocessing."""

    def test_two_level_chain_converges(self):
        preset = _preset([
            _field("profile", default="a"),
            _field(
                "mid", default=0,
                reactions=[{"when": {"field": "profile", "operator": "equals", "value": "b"}, "then": {"set_value": 10}}],
            ),
            _field(
                "leaf", default=0,
                reactions=[{"when": {"field": "mid", "operator": "equals", "value": 10}, "then": {"set_value": 20}}],
            ),
        ])
        bound = bind_form(preset, "txt2img", None, {"profile": "b"}, "user_1")
        assert bound.values["mid"] == 10
        assert bound.values["leaf"] == 20


class TestAdminOverridePrecedence:
    """An admin-locked/hidden field's value is never touched by a reaction -
    the override precedes reaction resolution just as it precedes the wire
    value."""

    def test_locked_field_ignores_matching_reaction(self):
        preset = _preset([
            _field("profile", default="base"),
            _field(
                "steps", type_="slider", default=8,
                reactions=[{"when": {"field": "profile", "operator": "equals", "value": "base"}, "then": {"set_value": 30}}],
            ),
        ])
        bound = bind_form(
            preset, "txt2img", None, {"profile": "base"}, "user_1",
            field_overrides={"steps": {"editable": False, "default": 99}},
        )
        assert bound.values["steps"] == 99
        assert "steps" in bound.admin_pinned


class TestNoReactionsRegressionGuard:
    """Fields with no `reactions:` of their own bind byte-identically to
    before this change: `_apply_reactions` short-circuits whenever no field
    in the index declares any, so the pre-existing default-fill behavior is
    untouched. Computed here directly from the real preset fixtures rather
    than a stored snapshot, since the same fixtures are available at both
    "before" (no reactions declared -> no-op) and "after" (this change)."""

    @pytest.mark.parametrize("preset_dirname", ["SDXL", "Wan", "LTX-2"])
    def test_binding_a_real_preset_with_empty_payload_matches_static_defaults(self, preset_dirname):
        loader = PresetTemplateLoader([str(PRESETS_ROOT)])
        loader.load_presets()
        preset = next(p for p in loader.presets if p.path.endswith(f"/{preset_dirname}"))

        mode_name = next(iter(preset.modes))
        mode_data = preset.modes[mode_name]
        form = next((f for f in mode_data.forms if f.default), mode_data.forms[0])

        # Only fields that declare no reactions of their own are asserted -
        # this preset tree may mix reaction-bearing and reaction-free fields
        # in the same form (e.g. SDXL's generation.yml), and only the latter
        # are this guard's concern.
        static_fields = _flatten(form.fields)
        expected = {
            name: f.default for name, f in static_fields.items()
            if not f.reactions and not (f.required and f.type == "model")
        }

        wire = _required_model_refs(form.fields)
        bound = bind_form(preset, mode_name, None, wire, "user_1")
        for name, expected_default in expected.items():
            assert bound.values[name] == expected_default, name


def _reactions_ts_operator_keys() -> set:
    """Parse the `export const operators = { ... }` object literal in
    frontend/src/lib/form/reactions.ts and return its top-level keys.

    Parsed from source rather than hand-copied, since a hand-maintained copy
    of this list in Python is exactly how schema.py's `OPERATORS` and
    binding.py's `_REACTION_OPERATORS` drifted from the frontend in the first
    place. This is a brace-matching scan, not a JS parser - it only assumes
    the operators object is a top-level `{ key: (...) => ..., ... }` literal,
    which is how reactions.ts has always declared it.
    """
    text = REACTIONS_TS_PATH.read_text()
    marker = "export const operators"
    start = text.index(marker)
    brace_start = text.index("{", start)

    depth = 0
    end = None
    for i in range(brace_start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                end = i
                break
    assert end is not None, "could not find the closing brace of reactions.ts's operators table"

    body = text[brace_start + 1 : end]
    keys = re.findall(r"(?m)^\s*(\w+):\s*\(", body)
    assert keys, "regex extraction found no operator keys - reactions.ts's formatting changed"
    return set(keys)


class TestOperatorSetParity:
    """Locks the three lists that must agree: reactions.ts's `operators`
    table (what the browser applies before ever POSTing), schema.py's
    `OPERATORS` frozenset (what preset-load validation accepts), and
    binding.py's `_REACTION_OPERATORS` (what bind_form actually evaluates
    for every non-browser caller). A preset author can write a `when:`
    clause using anything schema.py accepts; if binding.py's set is a
    strict subset, that clause is schema-valid, passes preset_lint, and
    silently fails open server-side - the exact bug this test exists to
    catch before it recurs.
    """

    def test_frontend_schema_and_binding_operator_sets_agree(self):
        frontend_ops = _reactions_ts_operator_keys()
        schema_ops = set(OPERATORS)
        binding_ops = set(_REACTION_OPERATORS)

        missing_from_schema = frontend_ops - schema_ops
        extra_in_schema = schema_ops - frontend_ops
        missing_from_binding = frontend_ops - binding_ops
        extra_in_binding = binding_ops - frontend_ops

        assert not missing_from_schema, (
            f"schema.py OPERATORS is missing {sorted(missing_from_schema)}, "
            f"declared in reactions.ts's operators table"
        )
        assert not extra_in_schema, (
            f"schema.py OPERATORS declares {sorted(extra_in_schema)} not present "
            f"in reactions.ts's operators table"
        )
        assert not missing_from_binding, (
            f"binding.py _REACTION_OPERATORS is missing {sorted(missing_from_binding)}, "
            f"declared in reactions.ts's operators table - bind_form will fail open "
            f"for a schema-valid preset using it"
        )
        assert not extra_in_binding, (
            f"binding.py _REACTION_OPERATORS declares {sorted(extra_in_binding)} not "
            f"present in reactions.ts's operators table"
        )


class TestNewOperatorsThroughBindForm:
    """The 7 operators that were schema-valid but unimplemented in bind_form:
    not_in, greater_than, less_than, greater_than_or_equals,
    less_than_or_equals, is_empty, is_not_empty. Each exercised through the
    real bind_form/field_index path (not a hand-built values dict), mirroring
    the corresponding body in frontend/src/lib/form/reactions.ts's
    `operators` table."""

    def test_not_in_matches_when_trigger_value_absent_from_list(self):
        preset = _preset([
            _field("mode", default="a"),
            _field(
                "flag", type_="slider", default=0,
                reactions=[{"when": {"field": "mode", "operator": "not_in", "value": ["b", "c"]}, "then": {"set_value": 1}}],
            ),
        ])
        bound = bind_form(preset, "txt2img", None, {"mode": "a"}, "user_1")
        assert bound.values["flag"] == 1

    def test_not_in_does_not_match_when_trigger_value_present(self):
        preset = _preset([
            _field("mode", default="a"),
            _field(
                "flag", type_="slider", default=0,
                reactions=[{"when": {"field": "mode", "operator": "not_in", "value": ["a", "c"]}, "then": {"set_value": 1}}],
            ),
        ])
        bound = bind_form(preset, "txt2img", None, {"mode": "a"}, "user_1")
        assert bound.values["flag"] == 0

    def test_not_in_does_not_match_when_condition_value_is_not_a_list(self):
        """reactions.ts: `Array.isArray(conditionValue) && !conditionValue.includes(...)`
        - a non-array `value` makes not_in false, not true."""
        preset = _preset([
            _field("mode", default="a"),
            _field(
                "flag", type_="slider", default=0,
                reactions=[{"when": {"field": "mode", "operator": "not_in", "value": "a"}, "then": {"set_value": 1}}],
            ),
        ])
        bound = bind_form(preset, "txt2img", None, {"mode": "a"}, "user_1")
        assert bound.values["flag"] == 0

    def test_greater_than_matches_above_threshold(self):
        preset = _preset([
            _field("steps", type_="slider", default=10),
            _field(
                "quality_pass", type_="slider", default=0,
                reactions=[{"when": {"field": "steps", "operator": "greater_than", "value": 20}, "then": {"set_value": 1}}],
            ),
        ])
        bound = bind_form(preset, "txt2img", None, {"steps": 30}, "user_1")
        assert bound.values["quality_pass"] == 1

    def test_greater_than_does_not_match_at_threshold(self):
        preset = _preset([
            _field("steps", type_="slider", default=10),
            _field(
                "quality_pass", type_="slider", default=0,
                reactions=[{"when": {"field": "steps", "operator": "greater_than", "value": 20}, "then": {"set_value": 1}}],
            ),
        ])
        bound = bind_form(preset, "txt2img", None, {"steps": 20}, "user_1")
        assert bound.values["quality_pass"] == 0

    def test_less_than_matches_below_threshold(self):
        preset = _preset([
            _field("cfg", type_="slider", default=5.0),
            _field(
                "low_cfg_warning", type_="slider", default=0,
                reactions=[{"when": {"field": "cfg", "operator": "less_than", "value": 2.0}, "then": {"set_value": 1}}],
            ),
        ])
        bound = bind_form(preset, "txt2img", None, {"cfg": 1.0}, "user_1")
        assert bound.values["low_cfg_warning"] == 1

    def test_greater_than_or_equals_matches_at_boundary(self):
        preset = _preset([
            _field("steps", type_="slider", default=10),
            _field(
                "quality_pass", type_="slider", default=0,
                reactions=[{"when": {"field": "steps", "operator": "greater_than_or_equals", "value": 20}, "then": {"set_value": 1}}],
            ),
        ])
        bound = bind_form(preset, "txt2img", None, {"steps": 20}, "user_1")
        assert bound.values["quality_pass"] == 1

    def test_less_than_or_equals_matches_at_boundary(self):
        preset = _preset([
            _field("cfg", type_="slider", default=5.0),
            _field(
                "low_cfg_warning", type_="slider", default=0,
                reactions=[{"when": {"field": "cfg", "operator": "less_than_or_equals", "value": 2.0}, "then": {"set_value": 1}}],
            ),
        ])
        bound = bind_form(preset, "txt2img", None, {"cfg": 2.0}, "user_1")
        assert bound.values["low_cfg_warning"] == 1

    def test_is_empty_matches_empty_string(self):
        preset = _preset([
            _field("note", type_="string", default=""),
            _field(
                "placeholder_shown", type_="slider", default=0,
                reactions=[{"when": {"field": "note", "operator": "is_empty"}, "then": {"set_value": 1}}],
            ),
        ])
        bound = bind_form(preset, "txt2img", None, {"note": ""}, "user_1")
        assert bound.values["placeholder_shown"] == 1

    def test_is_not_empty_matches_nonempty_string(self):
        preset = _preset([
            _field("note", type_="string", default=""),
            _field(
                "placeholder_shown", type_="slider", default=0,
                reactions=[{"when": {"field": "note", "operator": "is_not_empty"}, "then": {"set_value": 1}}],
            ),
        ])
        bound = bind_form(preset, "txt2img", None, {"note": "hello"}, "user_1")
        assert bound.values["placeholder_shown"] == 1


class TestNumericComparisonCoercion:
    """The deliberate coercion decision for greater_than/less_than/
    *_or_equals: mirror reactions.ts's `parseFloat(fieldValue) > parseFloat
    (conditionValue)` (etc.) - parse a leading numeric prefix, else treat as
    unparseable (JS's NaN). A NaN-equivalent comparison must resolve to "no
    match", never raise - Python's bare `>`/`<` would raise TypeError on
    None or str-vs-number, which would abort a generation instead of just
    leaving one reaction unmatched."""

    def test_numeric_string_trigger_value_compares_against_numeric_threshold(self):
        preset = _preset([
            _field("steps", type_="slider", default=10),
            _field(
                "quality_pass", type_="slider", default=0,
                reactions=[{"when": {"field": "steps", "operator": "greater_than", "value": 20}, "then": {"set_value": 1}}],
            ),
        ])
        bound = bind_form(preset, "txt2img", None, {"steps": "30"}, "user_1")
        assert bound.values["quality_pass"] == 1

    def test_none_trigger_value_does_not_match_and_does_not_raise(self):
        preset = _preset([
            _field("steps", type_="slider", default=None, required=False),
            _field(
                "quality_pass", type_="slider", default=0,
                reactions=[{"when": {"field": "steps", "operator": "greater_than", "value": 20}, "then": {"set_value": 1}}],
            ),
        ])
        bound = bind_form(preset, "txt2img", None, {}, "user_1")
        assert bound.values["quality_pass"] == 0

    def test_non_numeric_trigger_value_does_not_match_and_does_not_raise(self):
        preset = _preset([
            _field("steps", type_="string", default="fast"),
            _field(
                "quality_pass", type_="slider", default=0,
                reactions=[{"when": {"field": "steps", "operator": "greater_than", "value": 20}, "then": {"set_value": 1}}],
            ),
        ])
        bound = bind_form(preset, "txt2img", None, {"steps": "fast"}, "user_1")
        assert bound.values["quality_pass"] == 0

    def test_non_numeric_condition_value_does_not_match_and_does_not_raise(self):
        preset = _preset([
            _field("steps", type_="slider", default=10),
            _field(
                "quality_pass", type_="slider", default=0,
                reactions=[{"when": {"field": "steps", "operator": "greater_than", "value": "not_a_number"}, "then": {"set_value": 1}}],
            ),
        ])
        bound = bind_form(preset, "txt2img", None, {"steps": 30}, "user_1")
        assert bound.values["quality_pass"] == 0


class TestEmptyOperatorZeroAndFalseTrap:
    """0 and False must NOT be treated as empty. reactions.ts's `is_empty`
    does not use a bare falsy check (`!fieldValue`) - it explicitly checks
    null/undefined, then string/array length, then object key count, and
    falls through to `false` for every other type (numbers and booleans
    included). This locks that exact behavior on the Python side so it
    can't silently regress to `not value`, which WOULD treat a slider at 0
    or an unchecked checkbox as empty."""

    def test_is_empty_does_not_match_numeric_zero(self):
        preset = _preset([
            _field("count", type_="slider", default=1),
            _field(
                "warn_zero", type_="slider", default=0,
                reactions=[{"when": {"field": "count", "operator": "is_empty"}, "then": {"set_value": 1}}],
            ),
        ])
        bound = bind_form(preset, "txt2img", None, {"count": 0}, "user_1")
        assert bound.values["warn_zero"] == 0

    def test_is_not_empty_matches_numeric_zero(self):
        preset = _preset([
            _field("count", type_="slider", default=1),
            _field(
                "shows_count", type_="slider", default=0,
                reactions=[{"when": {"field": "count", "operator": "is_not_empty"}, "then": {"set_value": 1}}],
            ),
        ])
        bound = bind_form(preset, "txt2img", None, {"count": 0}, "user_1")
        assert bound.values["shows_count"] == 1

    def test_is_empty_does_not_match_false_checkbox(self):
        preset = _preset([
            _field("enabled", type_="checkbox", default=True),
            _field(
                "warn_disabled", type_="slider", default=0,
                reactions=[{"when": {"field": "enabled", "operator": "is_empty"}, "then": {"set_value": 1}}],
            ),
        ])
        bound = bind_form(preset, "txt2img", None, {"enabled": False}, "user_1")
        assert bound.values["warn_disabled"] == 0

    def test_is_not_empty_matches_false_checkbox(self):
        preset = _preset([
            _field("enabled", type_="checkbox", default=True),
            _field(
                "shows_state", type_="slider", default=0,
                reactions=[{"when": {"field": "enabled", "operator": "is_not_empty"}, "then": {"set_value": 1}}],
            ),
        ])
        bound = bind_form(preset, "txt2img", None, {"enabled": False}, "user_1")
        assert bound.values["shows_state"] == 1

    def test_is_empty_matches_empty_list(self):
        preset = _preset([
            _field("tags", type_="select", default=["a"]),
            _field(
                "show_placeholder", type_="slider", default=0,
                reactions=[{"when": {"field": "tags", "operator": "is_empty"}, "then": {"set_value": 1}}],
            ),
        ])
        bound = bind_form(preset, "txt2img", None, {"tags": []}, "user_1")
        assert bound.values["show_placeholder"] == 1


class TestRealPresetTreeReactionShapes:
    """Walks every preset under `presets/` and asserts the server-side
    evaluator recognizes every `when`/`then` shape actually declared. A
    future preset introducing a new operator or a value-affecting action
    this evaluator doesn't handle must fail HERE, loudly, rather than
    silently diverging from the frontend at generation time."""

    _SUPPORTED_OPERATORS = {"equals", "not_equals", "in", "contains", "not_contains"}
    # Actions that affect a BOUND VALUE. `set_visibility`/`set_disabled` are
    # presentation-only (see TestSetVisibilityBinding); `update_options`/
    # `update_validation`/`set_filter_tags` never set a value either.
    _VALUE_ACTIONS = {"set_value"}
    _KNOWN_INERT_ACTIONS = {"set_visibility", "set_disabled", "update_options", "update_validation", "set_filter_tags"}

    def _walk_all_fields(self):
        loader = PresetTemplateLoader([str(PRESETS_ROOT)])
        loader.load_presets()
        assert not loader.load_errors, loader.load_errors

        fields = []
        for preset in loader.presets:
            for mode_data in preset.modes.values():
                for form in mode_data.forms:
                    fields.extend(_flatten(form.fields).values())
        return fields

    def test_every_declared_operator_is_supported(self):
        fields = self._walk_all_fields()
        seen_operators = set()
        for f in fields:
            for reaction in f.reactions or []:
                when = reaction.get("when")
                if isinstance(when, dict) and "operator" in when:
                    seen_operators.add(when["operator"])

        assert seen_operators, "expected at least one reaction in the preset tree"
        unsupported = seen_operators - self._SUPPORTED_OPERATORS
        assert not unsupported, (
            f"preset tree now uses operator(s) {unsupported} that bind_form's "
            f"reaction evaluator doesn't support - add them to "
            f"_REACTION_OPERATORS in src/features/forms/binding.py"
        )

    def test_every_when_shape_is_a_single_condition_dict(self):
        """No preset today uses a logical AND/OR group or a bare
        list-of-conditions `when` - both fail open server-side (see
        TestUnsupportedShapes). This fails loudly the day one is added, so
        that gap gets closed deliberately instead of silently."""
        fields = self._walk_all_fields()
        for f in fields:
            for reaction in f.reactions or []:
                when = reaction.get("when")
                assert isinstance(when, dict) and "field" in when, (
                    f"field '{f.name}' uses an unsupported 'when' shape {when!r} - "
                    f"bind_form's reaction evaluator only supports single-condition "
                    f"dicts today"
                )

    def test_every_declared_action_is_recognized(self):
        fields = self._walk_all_fields()
        recognized = self._VALUE_ACTIONS | self._KNOWN_INERT_ACTIONS
        for f in fields:
            for reaction in f.reactions or []:
                then = reaction.get("then") or {}
                used = {k for k, v in then.items() if v is not None}
                unrecognized = used - recognized
                assert not unrecognized, (
                    f"field '{f.name}' reaction uses action(s) {unrecognized} "
                    f"unrecognized by bind_form's reaction evaluator"
                )


class TestParseFloatJSParity:
    """QA-bounced gap #1: `parseFloat("Infinity")` is `Infinity` in JS (and
    `parseFloat("-Infinity")` is `-Infinity`); the old regex-based
    `_reaction_parse_float` rejected both, silently treating them as NaN
    (never matching any threshold). Also gap #2: a non-string trigger value
    is JS-ToString-coerced before parsing, not rejected outright -
    `parseFloat([12])` is `12` (`String([12]) === "12"`)."""

    def test_infinity_string_trigger_beats_any_finite_threshold(self):
        preset = _preset([
            _field("budget", type_="string", default="0"),
            _field(
                "unlimited", type_="slider", default=0,
                reactions=[{"when": {"field": "budget", "operator": "greater_than", "value": 1000000}, "then": {"set_value": 1}}],
            ),
        ])
        bound = bind_form(preset, "txt2img", None, {"budget": "Infinity"}, "user_1")
        assert bound.values["unlimited"] == 1

    def test_negative_infinity_string_is_below_any_finite_threshold(self):
        preset = _preset([
            _field("budget", type_="string", default="0"),
            _field(
                "underflow", type_="slider", default=0,
                reactions=[{"when": {"field": "budget", "operator": "less_than", "value": -1000000}, "then": {"set_value": 1}}],
            ),
        ])
        bound = bind_form(preset, "txt2img", None, {"budget": "-Infinity"}, "user_1")
        assert bound.values["underflow"] == 1

    def test_single_element_list_trigger_is_stringified_before_parsing(self):
        # String([12]) === "12" in JS -> parseFloat gives 12, not NaN.
        preset = _preset([
            _field("dims", type_="select", default=[]),
            _field(
                "wide_enough", type_="slider", default=0,
                reactions=[{"when": {"field": "dims", "operator": "greater_than_or_equals", "value": 12}, "then": {"set_value": 1}}],
            ),
        ])
        bound = bind_form(preset, "txt2img", None, {"dims": [12]}, "user_1")
        assert bound.values["wide_enough"] == 1

    def test_multi_element_list_trigger_parses_only_the_first_number(self):
        # String([1, 2]) === "1,2" -> parseFloat("1,2") is 1, not 2 and not NaN.
        preset = _preset([
            _field("dims", type_="select", default=[]),
            _field(
                "at_least_one", type_="slider", default=0,
                reactions=[{"when": {"field": "dims", "operator": "greater_than_or_equals", "value": 1}, "then": {"set_value": 1}}],
            ),
            _field(
                "at_least_two", type_="slider", default=0,
                reactions=[{"when": {"field": "dims", "operator": "greater_than_or_equals", "value": 2}, "then": {"set_value": 1}}],
            ),
        ])
        bound = bind_form(preset, "txt2img", None, {"dims": [1, 2]}, "user_1")
        assert bound.values["at_least_one"] == 1
        assert bound.values["at_least_two"] == 0

    def test_empty_list_trigger_parses_to_nan_and_never_matches(self):
        # String([]) === "" -> parseFloat("") is NaN -> every comparison false.
        preset = _preset([
            _field("dims", type_="select", default=[]),
            _field(
                "flag", type_="slider", default=0,
                reactions=[{"when": {"field": "dims", "operator": "greater_than", "value": -1}, "then": {"set_value": 1}}],
            ),
        ])
        bound = bind_form(preset, "txt2img", None, {"dims": []}, "user_1")
        assert bound.values["flag"] == 0


class TestMembershipSameValueZeroBoolHazard:
    """QA-bounced gap #3: `False in [0]` is `True` in Python (bool is an int
    subclass) but `[0].includes(false)` is `false` in JS
    (`Array.prototype.includes` uses SameValueZero, which treats booleans and
    numbers as distinct types). A checkbox at `false` must not match a
    condition `in: [0]`/`not_in: [0]` server-side just because Python's bare
    `in`/`not in` would."""

    def test_false_checkbox_does_not_match_numeric_zero_option_list(self):
        preset = _preset([
            _field("enabled", type_="checkbox", default=True),
            _field(
                "flag", type_="slider", default=0,
                reactions=[{"when": {"field": "enabled", "operator": "in", "value": [0]}, "then": {"set_value": 1}}],
            ),
        ])
        bound = bind_form(preset, "txt2img", None, {"enabled": False}, "user_1")
        assert bound.values["flag"] == 0

    def test_false_checkbox_matches_a_boolean_option_list(self):
        preset = _preset([
            _field("enabled", type_="checkbox", default=True),
            _field(
                "flag", type_="slider", default=0,
                reactions=[{"when": {"field": "enabled", "operator": "in", "value": [False, "disabled"]}, "then": {"set_value": 1}}],
            ),
        ])
        bound = bind_form(preset, "txt2img", None, {"enabled": False}, "user_1")
        assert bound.values["flag"] == 1

    def test_true_checkbox_is_correctly_not_in_a_numeric_one_list(self):
        # not_in must ALSO not treat True as matching [1] - mirrors
        # `[1].includes(true) === false`, so not_in stays true (unmatched by
        # the numeric list).
        preset = _preset([
            _field("enabled", type_="checkbox", default=False),
            _field(
                "flag", type_="slider", default=0,
                reactions=[{"when": {"field": "enabled", "operator": "not_in", "value": [1]}, "then": {"set_value": 1}}],
            ),
        ])
        bound = bind_form(preset, "txt2img", None, {"enabled": True}, "user_1")
        assert bound.values["flag"] == 1


class TestEqualsBoolNumberHazardAudit:
    """Audit finding (not one of the three bounced gaps, but the same bug
    class in a more commonly used operator): `equals`/`not_equals` used bare
    Python `==`/`!=`, so `False == 0` matched too - `false === 0` is `false`
    in JS. Fixed alongside `in`/`not_in` since the SDXL preset tree itself
    uses `equals: false` against checkbox fields (`adm_guidance_enabled`,
    `sag_enabled` in content/presets/marketplace/SDXL/modes/txt2img/tabs/advanced.yml) -
    real usage the original bounce didn't happen to probe with a numeric
    literal."""

    def test_false_checkbox_does_not_equal_zero(self):
        preset = _preset([
            _field("enabled", type_="checkbox", default=True),
            _field(
                "flag", type_="slider", default=0,
                reactions=[{"when": {"field": "enabled", "operator": "equals", "value": 0}, "then": {"set_value": 1}}],
            ),
        ])
        bound = bind_form(preset, "txt2img", None, {"enabled": False}, "user_1")
        assert bound.values["flag"] == 0

    def test_false_checkbox_not_equals_zero_is_true(self):
        preset = _preset([
            _field("enabled", type_="checkbox", default=True),
            _field(
                "flag", type_="slider", default=0,
                reactions=[{"when": {"field": "enabled", "operator": "not_equals", "value": 0}, "then": {"set_value": 1}}],
            ),
        ])
        bound = bind_form(preset, "txt2img", None, {"enabled": False}, "user_1")
        assert bound.values["flag"] == 1

    def test_false_checkbox_still_equals_false(self):
        # Regression guard for the real SDXL usage pattern - the fix must
        # not break the actual, correct `equals: false` reactions in use.
        preset = _preset([
            _field("enabled", type_="checkbox", default=True),
            _field(
                "flag", type_="slider", default=0,
                reactions=[{"when": {"field": "enabled", "operator": "equals", "value": False}, "then": {"set_value": 1}}],
            ),
        ])
        bound = bind_form(preset, "txt2img", None, {"enabled": False}, "user_1")
        assert bound.values["flag"] == 1


class TestContainsNumberStringifyAudit:
    """Audit finding: `contains`/`not_contains` stringify via `String(x)`
    before substring-matching. JS has one numeric type, so an integral float
    stringifies without a decimal point (`String(12.0) === "12"`, which does
    NOT contain the literal substring `"12.0"`); Python's bare
    `str(12.0) == "12.0"` DOES contain `"12.0"` as a substring of itself,
    which would wrongly match. (A condition of plain `"12"` is not
    discriminating here - `"12" in "12.0"` is coincidentally `True` under
    both the buggy and the fixed stringification, so it can't catch a
    regression; `"12.0"` is the case that actually depends on the fix.)"""

    def test_integral_float_trigger_does_not_contain_its_own_decimal_point(self):
        preset = _preset([
            _field("scale", type_="slider", default=1.0),
            _field(
                "flag", type_="string", default="unmatched",
                reactions=[{"when": {"field": "scale", "operator": "not_contains", "value": "12.0"}, "then": {"set_value": "matched"}}],
            ),
        ])
        bound = bind_form(preset, "txt2img", None, {"scale": 12.0}, "user_1")
        assert bound.values["flag"] == "matched"


class TestOperatorBehaviorParityTable:
    """A (fieldValue, conditionValue) -> expected table for every operator,
    each expectation derived by hand-tracing
    frontend/src/lib/form/reactions.ts's `operators` table (not by running
    it - there is no JS runtime in this test process) - see the trailing
    comment on each row for the JS expression it was traced from.
    `TestOperatorSetParity` above only locks the operator *names* in step
    across reactions.ts/schema.py/binding.py; that is exactly why the three
    bounced gaps shipped invisibly - the names matched, the semantics
    didn't. This table locks a sample of *behavior* too, so a future edit
    that keeps every operator name but drifts what it computes fails a test
    here instead of shipping.

    Exercised directly against `_REACTION_OPERATORS` rather than through
    `bind_form`/a preset fixture: the point of this table is breadth across
    many small value pairs cheaply, and the `bind_form`-driven test classes
    above already prove each of the three bounced cases' effect is
    observable end-to-end through the real binding pipeline.
    """

    CASES = [
        # equals / not_equals - JS `===`: bool is its own type, not an int.
        ("equals", False, 0, False, "false === 0"),
        ("equals", True, 1, False, "true === 1"),
        ("equals", False, False, True, "false === false"),
        ("equals", 0, 0.0, True, "0 === 0.0 (JS has one number type)"),
        ("equals", "1", 1, False, '"1" === 1'),
        ("equals", None, None, True, "null === null"),
        ("equals", None, False, False, "null === false"),
        ("equals", math.nan, math.nan, False, "NaN === NaN is false"),
        ("not_equals", False, 0, True, "false !== 0"),
        ("not_equals", "a", "a", False, '"a" !== "a"'),
        # in / not_in - JS SameValueZero: same type-strictness as ===, but NaN self-equal.
        ("in", False, [0], False, "[0].includes(false)"),
        ("in", 0, [0], True, "[0].includes(0)"),
        ("in", False, [False], True, "[false].includes(false)"),
        ("in", 1, [1.0], True, "[1.0].includes(1)"),
        ("in", "1", [1], False, '[1].includes("1")'),
        ("in", math.nan, [math.nan], True, "SameValueZero: [NaN].includes(NaN)"),
        ("in", "x", "not_a_list", False, "Array.isArray('not_a_list') is false"),
        ("not_in", False, [0], True, "![0].includes(false)"),
        ("not_in", "x", "not_a_list", False, "Array.isArray fails -> not_in is false too, not true"),
        # greater_than / less_than / *_or_equals - parseFloat-based.
        ("greater_than", "Infinity", 1000000, True, 'parseFloat("Infinity") > 1000000'),
        ("less_than", "-Infinity", -1000000, True, 'parseFloat("-Infinity") < -1000000'),
        ("greater_than_or_equals", [12], 12, True, "parseFloat(String([12])) >= 12"),
        ("greater_than_or_equals", [1, 2], 2, False, 'parseFloat("1,2") is 1, not 2'),
        ("greater_than", [], 0, False, "parseFloat(String([])) is NaN"),
        ("greater_than", "12abc", 5, True, 'parseFloat("12abc") is 12'),
        ("less_than", None, 5, False, "parseFloat(null) is NaN"),
        ("less_than", True, 5, False, "parseFloat(true) is NaN (String(true) === \"true\")"),
        # contains / not_contains - String() coercion, substring match.
        ("contains", 12.0, "12", True, 'String(12.0) === "12"'),
        ("contains", ["fix_faces", "fix_hands"], "fix_hands", True, 'String([...]) === "fix_faces,fix_hands"'),
        ("contains", False, "false", True, 'String(false) === "false"'),
        ("not_contains", ["fix_faces"], "fix_eyes", True, "substring absent from the joined list"),
        # is_empty / is_not_empty - 0/false are never empty.
        ("is_empty", 0, None, False, "0 is not empty"),
        ("is_empty", False, None, False, "false is not empty"),
        ("is_empty", [], None, True, "[] is empty"),
        ("is_empty", None, None, True, "null is empty"),
        ("is_not_empty", 0, None, True, "negation of the row above"),
    ]

    @pytest.mark.parametrize("operator,field_value,condition_value,expected,comment", CASES)
    def test_operator_matches_js_semantics(self, operator, field_value, condition_value, expected, comment):
        fn = _REACTION_OPERATORS[operator]
        assert fn(field_value, condition_value) is expected, comment
