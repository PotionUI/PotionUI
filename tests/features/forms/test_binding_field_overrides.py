"""Tests for bind_form's `field_overrides` enforcement:
admin-locked/hidden fields never take the client's wire value, and every
pinned field is reported via `BoundForm.admin_pinned`.
"""
import logging

import pytest

from src.features.forms.binding import bind_form, BoundForm
from src.features.models.form_refs import make_model_ref
from src.features.presets.templates import FieldTemplate, FormTemplate, ModeTemplate, PresetTemplate


def _field(name, type_="string", default=None, required=False, configuration=None):
    return FieldTemplate(
        type=type_,
        name=name,
        default=default,
        required=required,
        configuration=configuration,
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


class TestLockedField:
    def test_client_value_ignored_for_locked_field(self):
        preset = _preset([_field("checkpoint", default="sd_base.safetensors")])
        overrides = {"checkpoint": {"editable": False}}
        bound = bind_form(
            preset, "txt2img", None,
            {"checkpoint": "client_chosen.safetensors"}, "user_1",
            field_overrides=overrides,
        )
        assert bound.values["checkpoint"] == "sd_base.safetensors"
        assert bound.admin_pinned == ["checkpoint"]

    def test_locked_field_uses_override_default_not_preset_default(self):
        preset = _preset([_field("checkpoint", default="sd_base.safetensors")])
        overrides = {"checkpoint": {"default": "admin_pinned.safetensors", "editable": False}}
        bound = bind_form(
            preset, "txt2img", None,
            {"checkpoint": "client_chosen.safetensors"}, "user_1",
            field_overrides=overrides,
        )
        assert bound.values["checkpoint"] == "admin_pinned.safetensors"
        assert bound.admin_pinned == ["checkpoint"]

    def test_locked_field_with_no_client_value_still_pinned(self):
        preset = _preset([_field("checkpoint", default="sd_base.safetensors")])
        overrides = {"checkpoint": {"default": "admin_pinned.safetensors", "editable": False}}
        bound = bind_form(preset, "txt2img", None, {}, "user_1", field_overrides=overrides)
        assert bound.values["checkpoint"] == "admin_pinned.safetensors"
        assert bound.admin_pinned == ["checkpoint"]

    def test_matching_client_value_is_not_logged_as_a_warning(self, caplog):
        preset = _preset([_field("checkpoint", default="sd_base.safetensors")])
        overrides = {"checkpoint": {"editable": False}}
        with caplog.at_level(logging.WARNING, logger="src.features.forms.binding"):
            bind_form(
                preset, "txt2img", None,
                {"checkpoint": "sd_base.safetensors"}, "user_1",
                field_overrides=overrides,
            )
        assert not any("ignoring client-supplied value" in r.message for r in caplog.records)

    def test_differing_client_value_logs_a_warning(self, caplog):
        preset = _preset([_field("checkpoint", default="sd_base.safetensors")])
        overrides = {"checkpoint": {"editable": False}}
        with caplog.at_level(logging.WARNING, logger="src.features.forms.binding"):
            bind_form(
                preset, "txt2img", None,
                {"checkpoint": "client_chosen.safetensors"}, "user_1",
                field_overrides=overrides,
            )
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any("checkpoint" in r.message and "locked" in r.message for r in warnings)

    def test_never_raises_422_for_a_locked_field_override(self):
        """Contract: client-supplied values for locked/hidden fields are
        silently overridden + logged, never rejected with a validation error."""
        preset = _preset([_field("checkpoint", default="sd_base.safetensors")])
        overrides = {"checkpoint": {"editable": False}}
        bound = bind_form(
            preset, "txt2img", None,
            {"checkpoint": "anything-a-client-could-send"}, "user_1",
            field_overrides=overrides,
        )
        assert isinstance(bound, BoundForm)


class TestHiddenField:
    def test_client_value_ignored_for_hidden_field(self):
        preset = _preset([_field("debug_mode", type_="checkbox", default=False)])
        overrides = {"debug_mode": {"visible": False}}
        bound = bind_form(
            preset, "txt2img", None,
            {"debug_mode": True}, "user_1",
            field_overrides=overrides,
        )
        assert bound.values["debug_mode"] is False
        assert bound.admin_pinned == ["debug_mode"]

    def test_hidden_field_uses_override_default(self):
        preset = _preset([_field("debug_mode", type_="checkbox", default=False)])
        overrides = {"debug_mode": {"default": True, "visible": False}}
        bound = bind_form(
            preset, "txt2img", None,
            {"debug_mode": False}, "user_1",
            field_overrides=overrides,
        )
        assert bound.values["debug_mode"] is True


class TestPlainDefaultOverride:
    """A `default` override with no `editable`/`visible: false` behaves like
    the field's own declared default: it only substitutes when the key is
    absent from the wire submission."""

    def test_substitutes_when_key_absent(self):
        preset = _preset([_field("steps", type_="slider", default=20)])
        overrides = {"steps": {"default": 30}}
        bound = bind_form(preset, "txt2img", None, {}, "user_1", field_overrides=overrides)
        assert bound.values["steps"] == 30
        assert bound.admin_pinned == []

    def test_client_value_wins_when_key_present(self):
        preset = _preset([_field("steps", type_="slider", default=20)])
        overrides = {"steps": {"default": 30}}
        bound = bind_form(preset, "txt2img", None, {"steps": 42}, "user_1", field_overrides=overrides)
        assert bound.values["steps"] == 42
        assert bound.admin_pinned == []


class TestUnaffectedFields:
    def test_fields_without_an_override_entry_behave_normally(self):
        preset = _preset([
            _field("checkpoint", default="sd_base.safetensors"),
            _field("steps", type_="slider", default=20),
        ])
        overrides = {"checkpoint": {"editable": False}}
        bound = bind_form(
            preset, "txt2img", None,
            {"checkpoint": "ignored.safetensors", "steps": 42}, "user_1",
            field_overrides=overrides,
        )
        assert bound.values["steps"] == 42
        assert bound.admin_pinned == ["checkpoint"]

    def test_no_field_overrides_argument_behaves_exactly_as_before(self):
        preset = _preset([_field("checkpoint", default="sd_base.safetensors")])
        bound = bind_form(preset, "txt2img", None, {"checkpoint": "client.safetensors"}, "user_1")
        assert bound.values["checkpoint"] == "client.safetensors"
        assert bound.admin_pinned == []


class TestModelRefPinning:
    def test_locked_model_ref_field_pins_admin_default(self):
        admin_model_ref = make_model_ref("admin_model_id")
        preset = _preset([_field("checkpoint", default="sd_base.safetensors")])
        overrides = {"checkpoint": {"default": admin_model_ref, "editable": False}}
        bound = bind_form(
            preset, "txt2img", None,
            {"checkpoint": make_model_ref("user_chosen_model_id")}, "user_1",
            field_overrides=overrides,
        )
        assert bound.values["checkpoint"] == admin_model_ref
        assert bound.admin_pinned == ["checkpoint"]
