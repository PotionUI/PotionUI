"""Tests for src.features.presets.form_overrides - the admin per-field form
overrides validation/inventory/schema-merge helpers. Pure unit tests,
no database."""

from src.features.presets.form_overrides import (
    apply_overrides_to_fields,
    build_inventory_entries,
    mode_field_inventory,
    validate_form_overrides,
)
from src.features.presets.templates import FieldTemplate, FormTemplate, ModeTemplate, PresetTemplate


def _field(name, type_="string", default=None, required=False, configuration=None, label=None):
    return FieldTemplate(
        type=type_,
        name=name,
        label=label,
        default=default,
        required=required,
        configuration=configuration,
    )


def _preset(modes):
    """`modes`: {mode_name: [FormTemplate, ...]}"""
    return PresetTemplate(
        id="preset_1",
        name="Preset One",
        version="1.0.0",
        path="/presets/preset_1",
        modes={name: ModeTemplate(forms=forms, pipes=[]) for name, forms in modes.items()},
    )


class TestContributedModeFormOverrides:
    """Form overrides key by (preset, mode name) - `mode_field_inventory`/
    `validate_form_overrides` read `preset_template.modes[mode]` directly and
    never check where that ModeTemplate came from, so a plugin-contributed
    mode (`ModeTemplate.source_plugin` set) needs no extra plumbing
    here. This proves it, rather than just asserting it in a report."""

    def test_inventory_and_validation_work_identically_for_a_contributed_mode(self):
        contributed_mode = ModeTemplate(
            forms=[FormTemplate(
                name="custom",
                fields=[_field("steps", type_="slider", default=20, configuration={"min": 1, "max": 100})],
                default=True,
            )],
            pipes=[],
            source_plugin="some-plugin",
        )
        preset = PresetTemplate(
            id="preset_1", name="Preset One", version="1.0.0", path="/presets/preset_1",
            modes={"img2img": contributed_mode},
        )

        inventory = mode_field_inventory(preset, "img2img")
        assert set(inventory.keys()) == {"steps"}

        assert validate_form_overrides(preset, "img2img", {"steps": {"default": 30}}) == []
        assert validate_form_overrides(preset, "img2img", {"steps": {"default": "not-a-number"}}) != []


class TestModeFieldInventory:
    def test_single_variant(self):
        preset = _preset({
            "txt2img": [FormTemplate(name="custom", fields=[_field("steps"), _field("checkpoint")], default=True)],
        })
        inventory = mode_field_inventory(preset, "txt2img")
        assert set(inventory.keys()) == {"steps", "checkpoint"}

    def test_union_across_variants(self):
        preset = _preset({
            "txt2img": [
                FormTemplate(name="simple", fields=[_field("steps")], default=True),
                FormTemplate(name="advanced", fields=[_field("steps"), _field("sampler")]),
            ],
        })
        inventory = mode_field_inventory(preset, "txt2img")
        assert set(inventory.keys()) == {"steps", "sampler"}

    def test_unknown_mode_returns_empty(self):
        preset = _preset({"txt2img": [FormTemplate(name="custom", fields=[_field("steps")], default=True)]})
        assert mode_field_inventory(preset, "img2img") == {}


class TestValidateFormOverrides:
    def _preset(self):
        return _preset({
            "txt2img": [FormTemplate(
                name="custom",
                fields=[
                    _field("steps", type_="slider", default=20, configuration={"min": 1, "max": 100}),
                    _field("checkpoint"),
                ],
                default=True,
            )],
        })

    def test_valid_overrides_pass(self):
        errors = validate_form_overrides(self._preset(), "txt2img", {
            "steps": {"default": 30, "editable": False},
            "checkpoint": {"visible": False},
        })
        assert errors == []

    def test_unknown_field_name_rejected(self):
        errors = validate_form_overrides(self._preset(), "txt2img", {"not_a_real_field": {"editable": False}})
        assert any("unknown field" in e for e in errors)

    def test_unknown_override_key_rejected(self):
        errors = validate_form_overrides(self._preset(), "txt2img", {"steps": {"bogus_key": 1}})
        assert any("unknown override key" in e for e in errors)

    def test_editable_must_be_bool(self):
        errors = validate_form_overrides(self._preset(), "txt2img", {"steps": {"editable": "false"}})
        assert any("boolean" in e for e in errors)

    def test_visible_must_be_bool(self):
        errors = validate_form_overrides(self._preset(), "txt2img", {"steps": {"visible": "no"}})
        assert any("boolean" in e for e in errors)

    def test_default_out_of_range_rejected(self):
        errors = validate_form_overrides(self._preset(), "txt2img", {"steps": {"default": 999}})
        assert any("steps" in e and "maximum" in e for e in errors)

    def test_default_wrong_type_rejected(self):
        errors = validate_form_overrides(self._preset(), "txt2img", {"steps": {"default": "not-a-number"}})
        assert any("steps" in e for e in errors)

    def test_clear_signal_empty_object_never_errors_even_for_unknown_field(self):
        errors = validate_form_overrides(self._preset(), "txt2img", {"not_a_real_field": {}})
        assert errors == []

    def test_clear_signal_none_never_errors(self):
        errors = validate_form_overrides(self._preset(), "txt2img", {"steps": None})
        assert errors == []

    def test_override_must_be_an_object(self):
        errors = validate_form_overrides(self._preset(), "txt2img", {"steps": "not-a-dict"})
        assert any("must be an object" in e for e in errors)


class TestBuildInventoryEntries:
    def test_entries_carry_preset_default_and_override(self):
        preset = _preset({
            "txt2img": [FormTemplate(
                name="custom",
                fields=[_field("steps", type_="slider", default=20, label="Steps")],
                default=True,
            )],
        })
        entries = build_inventory_entries(preset, "txt2img", {"steps": {"default": 30, "editable": False}})
        assert entries == [{
            "name": "steps",
            "label": "Steps",
            "type": "slider",
            "preset_default": 20,
            "override": {"default": 30, "editable": False},
        }]

    def test_override_is_none_when_unset(self):
        preset = _preset({
            "txt2img": [FormTemplate(name="custom", fields=[_field("steps", type_="slider", default=20)], default=True)],
        })
        entries = build_inventory_entries(preset, "txt2img", {})
        assert entries[0]["override"] is None

    def test_label_falls_back_to_titleized_name(self):
        preset = _preset({
            "txt2img": [FormTemplate(name="custom", fields=[_field("model_checkpoint")], default=True)],
        })
        entries = build_inventory_entries(preset, "txt2img", {})
        assert entries[0]["label"] == "Model Checkpoint"


class TestApplyOverridesToFields:
    """`apply_overrides_to_fields` is the v2 merge seam: it runs on the
    FieldTemplate tree right after @loop/external-children resolution
    (PresetFormSerializer.process_form_fields), not on the serialized dict."""

    def test_no_overrides_is_a_noop(self):
        fields = [_field("steps", "slider", default=20)]
        result = apply_overrides_to_fields(fields, {})
        assert result is fields

    def test_visible_false_removes_top_level_field(self):
        fields = [_field("steps", "slider", default=20), _field("checkpoint")]
        result = apply_overrides_to_fields(fields, {"checkpoint": {"visible": False}})
        assert [f.name for f in result] == ["steps"]

    def test_editable_false_sets_readonly(self):
        fields = [_field("steps", "slider", default=20)]
        result = apply_overrides_to_fields(fields, {"steps": {"editable": False}})
        assert result[0].readonly is True

    def test_default_replaces_field_default(self):
        fields = [_field("steps", "slider", default=20)]
        result = apply_overrides_to_fields(fields, {"steps": {"default": 30}})
        assert result[0].default == 30

    def test_editable_false_and_default_together(self):
        fields = [_field("checkpoint", default="sd_base.safetensors")]
        result = apply_overrides_to_fields(
            fields, {"checkpoint": {"default": "admin_pinned.safetensors", "editable": False}}
        )
        assert result[0].readonly is True
        assert result[0].default == "admin_pinned.safetensors"

    def test_visible_false_removes_nested_child(self):
        fields = [
            FieldTemplate(
                type="tab",
                name="generation",
                children=[_field("steps", "slider", default=20), _field("checkpoint")],
            ),
        ]
        result = apply_overrides_to_fields(fields, {"checkpoint": {"visible": False}})
        names = [c.name for c in result[0].children]
        assert names == ["steps"]

    def test_editable_false_on_nested_child_sets_readonly(self):
        fields = [
            FieldTemplate(type="tab", name="generation", children=[_field("steps", "slider", default=20)]),
        ]
        result = apply_overrides_to_fields(fields, {"steps": {"editable": False, "default": 30}})
        child = result[0].children[0]
        assert child.readonly is True
        assert child.default == 30

    def test_unrelated_fields_are_untouched(self):
        fields = [_field("steps", "slider", default=20), _field("checkpoint", default="a.safetensors")]
        result = apply_overrides_to_fields(fields, {"steps": {"editable": False}})
        checkpoint = next(f for f in result if f.name == "checkpoint")
        assert checkpoint.default == "a.safetensors"
        assert checkpoint.readonly is False

    def test_original_fields_are_not_mutated(self):
        """process_form_fields calls this on objects that are already fresh
        copies (from _resolve_external_children), but the function itself
        must still never mutate its input in place - it always returns new
        objects for anything it changes."""
        original = _field("steps", "slider", default=20)
        fields = [original]
        apply_overrides_to_fields(fields, {"steps": {"editable": False, "default": 30}})
        assert original.readonly is False
        assert original.default == 20

    def test_hidden_field_removed_even_when_nested_two_levels_deep(self):
        fields = [
            FieldTemplate(
                type="tabs",
                name="tabs",
                children=[
                    FieldTemplate(
                        type="tab",
                        name="advanced",
                        children=[_field("steps", "slider", default=20), _field("checkpoint")],
                    ),
                ],
            ),
        ]
        result = apply_overrides_to_fields(fields, {"checkpoint": {"visible": False}})
        inner_names = [c.name for c in result[0].children[0].children]
        assert inner_names == ["steps"]

    def test_container_with_all_children_hidden_is_removed(self):
        fields = [
            FieldTemplate(type="section", name="advanced_section", children=[_field("checkpoint")]),
            _field("steps", "slider", default=20),
        ]
        result = apply_overrides_to_fields(fields, {"checkpoint": {"visible": False}})
        assert [f.name for f in result] == ["steps"]

    def test_container_with_at_least_one_visible_child_is_kept(self):
        fields = [
            FieldTemplate(
                type="group",
                name="settings",
                children=[_field("steps", "slider", default=20), _field("checkpoint")],
            ),
        ]
        result = apply_overrides_to_fields(fields, {"checkpoint": {"visible": False}})
        assert len(result) == 1
        assert [c.name for c in result[0].children] == ["steps"]

    def test_container_removal_folds_up_recursively(self):
        """A row whose only child is a group whose only child is hidden must
        itself be dropped, not left as an empty shell."""
        fields = [
            FieldTemplate(
                type="row",
                name="outer_row",
                children=[
                    FieldTemplate(
                        type="group",
                        name="inner_group",
                        children=[_field("checkpoint")],
                    ),
                ],
            ),
            _field("steps", "slider", default=20),
        ]
        result = apply_overrides_to_fields(fields, {"checkpoint": {"visible": False}})
        assert [f.name for f in result] == ["steps"]

    def test_gate_is_kept_even_when_all_children_hidden(self):
        """A gate owns a real boolean value (unlike a pure layout container),
        so it must not be folded away when its governed children are hidden."""
        fields = [
            FieldTemplate(type="gate", name="hires_fix", default=False, children=[_field("checkpoint")]),
        ]
        result = apply_overrides_to_fields(fields, {"checkpoint": {"visible": False}})
        assert [f.name for f in result] == ["hires_fix"]
        assert result[0].children == []

    def test_childless_container_is_untouched(self):
        """A section with no `children:` at all is a plain divider, not a
        container whose emptiness was caused by an override - it must not be
        folded away just because it never had children to begin with."""
        fields = [
            FieldTemplate(type="section", name=None, label="Sampling"),
            _field("checkpoint"),
        ]
        result = apply_overrides_to_fields(fields, {"checkpoint": {"visible": False}})
        assert [f.type for f in result] == ["section"]
