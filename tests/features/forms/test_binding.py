"""Tests for bind_form (src/features/forms/binding.py) - the server-side form
binding boundary."""
import base64
import pytest

from src.features.forms.binding import bind_form, BoundForm, FormBindingError
from src.features.models.form_refs import make_model_ref
from src.features.forms.exceptions import FormNotFoundException
from src.features.presets.templates import FieldTemplate, FormTemplate, ModeTemplate, PresetTemplate


def _field(name, type_="string", default=None, required=False, configuration=None, children=None):
    return FieldTemplate(
        type=type_,
        name=name,
        default=default,
        required=required,
        configuration=configuration,
        children=children,
    )


def _preset(fields, form_name="custom", mode="txt2img", second_form=None):
    forms = [FormTemplate(name=form_name, fields=fields, default=True, order=0)]
    if second_form is not None:
        forms.append(second_form)
    return PresetTemplate(
        id="preset_1",
        name="Preset One",
        version="1.0.0",
        path="/presets/preset_1",
        modes={mode: ModeTemplate(forms=forms, pipes=[])},
    )


class TestLoopFieldExpansion:
    """`@loop` form-field declarations (e.g. numbered ControlNet slots -
    content/presets/marketplace/SDXL/modes/txt2img/tabs/controlnet.yml)
    must be expanded to their concrete per-iteration field names before the
    field index is built, or bind_form strips the submitted values as
    unknown keys and the pipeline's `form['controlnet_' ~ i ~ '_model']`
    access breaks even though the client sent real data."""

    def _loop_field(self, count=3):
        return FieldTemplate(
            type="@loop",
            configuration={
                "count": count,
                "template": {
                    "type": "text",
                    "name": "slot_{{ loop.index }}_model",
                    "default": "",
                },
            },
        )

    def test_loop_generated_fields_are_not_stripped(self):
        preset = _preset([self._loop_field(3)])
        raw = {"slot_1_model": "a.safetensors", "slot_2_model": "b.safetensors", "slot_3_model": "c.safetensors"}
        bound = bind_form(preset, "txt2img", None, raw, "user_1")
        assert bound.values["slot_1_model"] == "a.safetensors"
        assert bound.values["slot_2_model"] == "b.safetensors"
        assert bound.values["slot_3_model"] == "c.safetensors"
        assert bound.stripped == []

    def test_loop_generated_fields_apply_their_own_default_when_absent(self):
        preset = _preset([self._loop_field(2)])
        bound = bind_form(preset, "txt2img", None, {}, "user_1")
        assert bound.values["slot_1_model"] == ""
        assert bound.values["slot_2_model"] == ""

    def test_loop_count_matches_expanded_field_count(self):
        preset = _preset([self._loop_field(5)])
        bound = bind_form(preset, "txt2img", None, {}, "user_1")
        assert {f"slot_{i}_model" for i in range(1, 6)} <= set(bound.values.keys())


class TestFormNameResolution:
    def test_default_form_used_when_form_name_omitted(self):
        preset = _preset([_field("steps", "slider", default=20)])
        bound = bind_form(preset, "txt2img", None, {}, "user_1")
        assert bound.form_name == "custom"

    def test_unknown_form_name_raises(self):
        preset = _preset([_field("steps")])
        with pytest.raises(FormNotFoundException):
            bind_form(preset, "txt2img", "does_not_exist", {}, "user_1")

    def test_unknown_mode_raises(self):
        preset = _preset([_field("steps")])
        with pytest.raises(FormNotFoundException):
            bind_form(preset, "img2img", None, {}, "user_1")

    def test_named_form_variant_is_selected(self):
        second = FormTemplate(name="advanced_variant", fields=[_field("cfg", "slider", default=7)])
        preset = _preset([_field("steps", "slider", default=20)], second_form=second)
        bound = bind_form(preset, "txt2img", "advanced_variant", {}, "user_1")
        assert bound.form_name == "advanced_variant"
        assert bound.values["cfg"] == 7


class TestDefaults:
    def test_default_applied_when_key_absent(self):
        preset = _preset([_field("steps", "slider", default=20)])
        bound = bind_form(preset, "txt2img", None, {}, "user_1")
        assert bound.values["steps"] == 20

    def test_submitted_value_overrides_default(self):
        preset = _preset([_field("steps", "slider", default=20)])
        bound = bind_form(preset, "txt2img", None, {"steps": 8}, "user_1")
        assert bound.values["steps"] == 8

    @pytest.mark.parametrize("value", [False, 0, "", []])
    def test_falsy_submitted_values_survive(self, value):
        field_type = "checkbox" if isinstance(value, bool) else (
            "slider" if isinstance(value, int) else "string" if isinstance(value, str) else "select"
        )
        preset = _preset([_field("x", field_type, default=None)])
        bound = bind_form(preset, "txt2img", None, {"x": value}, "user_1")
        assert bound.values["x"] == value

    def test_falsy_default_survives_when_key_absent(self):
        preset = _preset([_field("use_upscale", "checkbox", default=False)])
        bound = bind_form(preset, "txt2img", None, {}, "user_1")
        assert bound.values["use_upscale"] is False

    def test_nested_children_fields_are_flattened(self):
        child = _field("cfg", "slider", default=7)
        preset = _preset([_field(None, "tab", children=[child])])
        bound = bind_form(preset, "txt2img", None, {}, "user_1")
        assert bound.values["cfg"] == 7


class TestUnknownKeys:
    def test_unknown_keys_are_stripped(self):
        preset = _preset([_field("steps", "slider", default=20)])
        bound = bind_form(preset, "txt2img", None, {"steps": 10, "bogus_key": "x"}, "user_1")
        assert "bogus_key" not in bound.values
        assert "bogus_key" in bound.stripped

    def test_model_ref_passthrough_even_when_unknown(self):
        preset = _preset([])
        ref = make_model_ref("model_abc")
        bound = bind_form(preset, "txt2img", None, {"checkpoint": ref}, "user_1")
        assert bound.values["checkpoint"] == ref
        assert "checkpoint" not in bound.stripped

    def test_video_director_key_passes_through_unknown(self):
        preset = _preset([])
        doc = {"schema_version": 1, "mode": "t2v"}
        bound = bind_form(preset, "txt2img", None, {"video_director": doc}, "user_1")
        assert bound.values["video_director"] == doc

    def test_music_director_key_passes_through_unknown(self):
        """Regression: this key was once missing from _PASSTHROUGH_KEYS, so
        bind_form silently stripped the whole Music Director document and the
        generator saw an empty caption ('caption' cannot be empty, 2026-08-18)."""
        preset = _preset([])
        doc = {"schema_version": 1, "mode": "song", "description": "warm lo-fi"}
        bound = bind_form(preset, "txt2img", None, {"music_director": doc}, "user_1")
        assert bound.values["music_director"] == doc
        assert "music_director" not in bound.stripped

    def test_origin_key_passes_through_when_its_base_field_is_declared(self):
        """`<field>__origin` rides alongside a declared media field to
        mark it as seeded from a prior generation's output."""
        preset = _preset([_field("source_image", "image")])
        origin = {"generation_id": "gen_1", "file_index": 2}
        bound = bind_form(
            preset, "txt2img", None,
            {"source_image": "uploads/a.png", "source_image__origin": origin},
            "user_1",
        )
        assert bound.values["source_image__origin"] == origin
        assert "source_image__origin" not in bound.stripped

    def test_origin_key_is_stripped_when_its_base_field_is_not_declared(self):
        """An origin key naming a field the preset doesn't even have is
        meaningless - stripped like any other unknown key."""
        preset = _preset([])
        origin = {"generation_id": "gen_1", "file_index": 0}
        bound = bind_form(
            preset, "txt2img", None,
            {"source_image": "uploads/a.png", "source_image__origin": origin},
            "user_1",
        )
        assert "source_image__origin" not in bound.values
        assert "source_image__origin" in bound.stripped


class TestValidation:
    def test_required_missing_raises(self):
        preset = _preset([_field("prompt", "string", required=True)])
        with pytest.raises(FormBindingError):
            bind_form(preset, "txt2img", None, {}, "user_1")

    def test_required_present_passes(self):
        preset = _preset([_field("prompt", "string", required=True)])
        bound = bind_form(preset, "txt2img", None, {"prompt": "a cat"}, "user_1")
        assert bound.values["prompt"] == "a cat"

    def test_numeric_below_minimum_raises(self):
        preset = _preset([_field("steps", "slider", default=20, configuration={"min": 1, "max": 100})])
        with pytest.raises(FormBindingError):
            bind_form(preset, "txt2img", None, {"steps": 0}, "user_1")

    def test_numeric_above_maximum_raises(self):
        preset = _preset([_field("steps", "slider", default=20, configuration={"min": 1, "max": 100})])
        with pytest.raises(FormBindingError):
            bind_form(preset, "txt2img", None, {"steps": 500}, "user_1")

    def test_numeric_within_range_passes(self):
        preset = _preset([_field("steps", "slider", default=20, configuration={"min": 1, "max": 100})])
        bound = bind_form(preset, "txt2img", None, {"steps": 50}, "user_1")
        assert bound.values["steps"] == 50

    def test_select_rejects_value_outside_static_options(self):
        preset = _preset([
            _field("sampler", "select", default="euler", configuration={
                "options": [{"label": "Euler", "value": "euler"}, {"label": "DPM++", "value": "dpmpp"}]
            })
        ])
        with pytest.raises(FormBindingError):
            bind_form(preset, "txt2img", None, {"sampler": "not_a_real_sampler"}, "user_1")

    def test_select_accepts_declared_option(self):
        preset = _preset([
            _field("sampler", "select", default="euler", configuration={
                "options": [{"label": "Euler", "value": "euler"}, {"label": "DPM++", "value": "dpmpp"}]
            })
        ])
        bound = bind_form(preset, "txt2img", None, {"sampler": "dpmpp"}, "user_1")
        assert bound.values["sampler"] == "dpmpp"

    def test_dynamic_select_without_static_options_is_not_checked(self):
        preset = _preset([_field("model", "select", configuration={"file": {"path": "x.yml"}})])
        bound = bind_form(preset, "txt2img", None, {"model": "anything.safetensors"}, "user_1")
        assert bound.values["model"] == "anything.safetensors"


class TestFieldErrors:
    """`FormBindingError.field_errors` is a `{field_name: [messages]}` dict
    (no repeated `"name: "` prefix inside the message) that must collect
    EVERY failing field in one raise, not fail-fast on the first."""

    def test_multiple_fields_all_appear_in_field_errors(self):
        preset = _preset([
            _field("prompt", "string", required=True),
            _field("steps", "slider", default=20, configuration={"min": 1, "max": 100}),
        ])
        with pytest.raises(FormBindingError) as excinfo:
            bind_form(preset, "txt2img", None, {"steps": 500}, "user_1")
        field_errors = excinfo.value.field_errors
        assert set(field_errors.keys()) == {"prompt", "steps"}
        assert "required field is missing" in field_errors["prompt"]
        assert any("exceeds the maximum" in m for m in field_errors["steps"])

    def test_field_error_messages_have_no_redundant_name_prefix(self):
        preset = _preset([_field("prompt", "string", required=True)])
        with pytest.raises(FormBindingError) as excinfo:
            bind_form(preset, "txt2img", None, {}, "user_1")
        for message in excinfo.value.field_errors["prompt"]:
            assert not message.startswith("prompt:")

    def test_flat_errors_summary_still_readable(self):
        preset = _preset([_field("prompt", "string", required=True)])
        with pytest.raises(FormBindingError) as excinfo:
            bind_form(preset, "txt2img", None, {}, "user_1")
        assert excinfo.value.errors == ["prompt: required field is missing"]
        assert str(excinfo.value) == "prompt: required field is missing"

    def test_media_containment_failure_keys_on_media_field_name(self, tmp_path):
        storage = tmp_path / "storage"
        storage.mkdir(parents=True)
        outside = tmp_path / "outside.png"
        outside.write_bytes(b"x")

        preset = _preset([_field("source_image", "image")])
        with pytest.raises(FormBindingError) as excinfo:
            bind_form(
                preset, "txt2img", None, {"source_image": "../outside.png"}, "user_1",
                storage_dir=str(storage),
            )
        assert "source_image" in excinfo.value.field_errors
        assert any("escapes the user's storage directory" in m for m in excinfo.value.field_errors["source_image"])

    def test_coercions_and_stripped_are_attached_to_the_exception(self):
        preset = _preset([_field("prompt", "string", required=True)])
        with pytest.raises(FormBindingError) as excinfo:
            bind_form(preset, "txt2img", None, {"unknown_key": "x"}, "user_1")
        assert excinfo.value.stripped == ["unknown_key"]
        assert excinfo.value.coercions == []


class TestLeniencyCoercion:
    def test_numeric_string_is_coerced_and_logged(self):
        preset = _preset([_field("steps", "slider", default=20)])
        bound = bind_form(preset, "txt2img", None, {"steps": "8"}, "user_1")
        assert bound.values["steps"] == 8
        assert isinstance(bound.values["steps"], int)
        assert any("steps" in c for c in bound.coercions)

    def test_float_string_is_coerced(self):
        preset = _preset([_field("cfg", "number", default=7.0)])
        bound = bind_form(preset, "txt2img", None, {"cfg": "3.5"}, "user_1")
        assert bound.values["cfg"] == 3.5

    def test_boolean_string_is_coerced_and_logged(self):
        preset = _preset([_field("use_upscale", "checkbox", default=False)])
        bound = bind_form(preset, "txt2img", None, {"use_upscale": "true"}, "user_1")
        assert bound.values["use_upscale"] is True
        assert any("use_upscale" in c for c in bound.coercions)

    def test_non_numeric_string_is_left_as_a_validation_error(self):
        preset = _preset([_field("steps", "slider", default=20)])
        with pytest.raises(FormBindingError):
            bind_form(preset, "txt2img", None, {"steps": "not_a_number"}, "user_1")


class TestMediaContainment:
    def test_media_ref_dict_traversal_is_rejected(self, tmp_path):
        preset = _preset([_field("source_image", "image")])
        value = {"path": "../../etc/passwd", "relative_path": "uploads/x.png", "type": "image"}
        with pytest.raises(FormBindingError) as excinfo:
            bind_form(
                preset, "txt2img", None, {"source_image": value}, "user_1",
                storage_dir=str(tmp_path),
            )
        assert any("escapes the user's storage directory" in m for m in excinfo.value.field_errors["source_image"])

    def test_media_ref_dict_inside_storage_passes_unmodified(self, tmp_path):
        preset = _preset([_field("source_image", "image")])
        value = {"path": "uploads/x.png", "relative_path": "uploads/x.png", "type": "image"}
        bound = bind_form(
            preset, "txt2img", None, {"source_image": value}, "user_1",
            storage_dir=str(tmp_path),
        )
        assert bound.values["source_image"] == value

    def test_media_ref_dict_without_path_keys_is_untouched(self, tmp_path):
        preset = _preset([_field("source_image", "image")])
        value = {"url": "/api/media/x.png", "type": "image"}
        bound = bind_form(
            preset, "txt2img", None, {"source_image": value}, "user_1",
            storage_dir=str(tmp_path),
        )
        assert bound.values["source_image"] == value

    def test_media_path_inside_storage_root_passes_unmodified(self, tmp_path):
        """Validate-only, like the dict shape: rewriting to the resolved path
        persisted an absolute path into `generations.form_data` for history
        reuse to replay. See test_binding_media_value_shapes.py."""
        storage = tmp_path / "storage"
        (storage / "uploads").mkdir(parents=True)
        (storage / "uploads" / "photo.png").write_bytes(b"x")

        preset = _preset([_field("source_image", "image")])
        bound = bind_form(
            preset, "txt2img", None, {"source_image": "uploads/photo.png"}, "user_1",
            storage_dir=str(storage),
        )
        assert bound.values["source_image"] == "uploads/photo.png"

    def test_media_path_traversal_is_rejected(self, tmp_path):
        storage = tmp_path / "storage"
        storage.mkdir(parents=True)
        outside = tmp_path / "outside.png"
        outside.write_bytes(b"x")

        preset = _preset([_field("source_image", "image")])
        with pytest.raises(FormBindingError):
            bind_form(
                preset, "txt2img", None, {"source_image": "../outside.png"}, "user_1",
                storage_dir=str(storage),
            )

    def test_symlink_escape_is_rejected(self, tmp_path):
        storage = tmp_path / "storage"
        storage.mkdir(parents=True)
        outside = tmp_path / "outside.png"
        outside.write_bytes(b"x")
        link = storage / "escape.png"
        link.symlink_to(outside)

        preset = _preset([_field("source_image", "image")])
        with pytest.raises(FormBindingError):
            bind_form(
                preset, "txt2img", None, {"source_image": "escape.png"}, "user_1",
                storage_dir=str(storage),
            )

    def test_absolute_path_outside_storage_root_is_rejected(self, tmp_path):
        storage = tmp_path / "storage"
        storage.mkdir(parents=True)
        outside = tmp_path / "elsewhere.png"
        outside.write_bytes(b"x")

        preset = _preset([_field("source_image", "image")])
        with pytest.raises(FormBindingError):
            bind_form(
                preset, "txt2img", None, {"source_image": str(outside)}, "user_1",
                storage_dir=str(storage),
            )

    def test_missing_storage_dir_skips_containment_check(self):
        preset = _preset([_field("source_image", "image")])
        bound = bind_form(
            preset, "txt2img", None, {"source_image": "whatever/path.png"}, "user_1",
            storage_dir=None,
        )
        assert bound.values["source_image"] == "whatever/path.png"

    def test_empty_media_value_normalizes_to_none(self, tmp_path):
        """`Image.input()` is now registered in `_INPUT_VALIDATORS` (runs
        before containment) and always normalized a falsy value to `None`
        - previously dead code, since nothing wired it in. `""` and `None`
        are already equivalent "missing" everywhere else in bind_form (see
        `_validate_field`'s `required` check), so this is a value-identity
        change only, not a new gap."""
        preset = _preset([_field("source_image", "image")])
        bound = bind_form(
            preset, "txt2img", None, {"source_image": ""}, "user_1",
            storage_dir=str(tmp_path),
        )
        assert bound.values["source_image"] is None

    def test_model_ref_on_a_media_field_is_left_alone(self, tmp_path):
        preset = _preset([_field("source_image", "image")])
        ref = make_model_ref("some_id")
        bound = bind_form(
            preset, "txt2img", None, {"source_image": ref}, "user_1",
            storage_dir=str(tmp_path),
        )
        assert bound.values["source_image"] == ref


class TestMultiItemMediaContainment:
    """`configuration.multi: true` media fields carry a LIST of items - each
    goes through the same per-item containment check a single-valued field
    gets, plus max_items and label sanitization. A non-multi field must be
    completely unaffected (see TestMediaContainment above, unchanged)."""

    def test_multi_item_path_traversal_is_rejected(self, tmp_path):
        storage = tmp_path / "storage"
        storage.mkdir(parents=True)
        preset = _preset([_field("refs", "image", configuration={"multi": True})])
        with pytest.raises(FormBindingError) as excinfo:
            bind_form(
                preset, "txt2img", None, {"refs": ["../outside.png"]}, "user_1",
                storage_dir=str(storage),
            )
        assert any("escapes the user's storage directory" in m for m in excinfo.value.field_errors["refs"])

    def test_multi_item_paths_inside_storage_root_pass_unmodified(self, tmp_path):
        storage = tmp_path / "storage"
        (storage / "uploads").mkdir(parents=True)
        preset = _preset([_field("refs", "image", configuration={"multi": True})])
        bound = bind_form(
            preset, "txt2img", None, {"refs": ["uploads/a.png", "uploads/b.png"]}, "user_1",
            storage_dir=str(storage),
        )
        assert bound.values["refs"] == ["uploads/a.png", "uploads/b.png"]

    def test_multi_item_object_values_are_containment_checked(self, tmp_path):
        """Object-shaped items (the real MediaLoaderField shape) get their
        path keys containment-checked; a clean item passes through
        unmodified (validate-only, consumers re-resolve)."""
        preset = _preset([_field("refs", "image", configuration={"multi": True})])
        item = {"path": "uploads/a.png", "relative_path": "uploads/a.png", "type": "image"}
        bound = bind_form(
            preset, "txt2img", None, {"refs": [item]}, "user_1",
            storage_dir=str(tmp_path),
        )
        assert bound.values["refs"] == [item]

    def test_multi_item_object_value_traversal_is_rejected(self, tmp_path):
        preset = _preset([_field("refs", "image", configuration={"multi": True})])
        item = {"path": "../../etc/passwd", "type": "image"}
        with pytest.raises(FormBindingError) as excinfo:
            bind_form(
                preset, "txt2img", None, {"refs": [item]}, "user_1",
                storage_dir=str(tmp_path),
            )
        assert any("escapes the user's storage directory" in m for m in excinfo.value.field_errors["refs"])

    def test_multi_item_over_max_items_is_rejected(self, tmp_path):
        preset = _preset([_field("refs", "image", configuration={"multi": True, "max_items": 2})])
        with pytest.raises(FormBindingError) as excinfo:
            bind_form(
                preset, "txt2img", None,
                {"refs": [{"path": "uploads/a.png"}, {"path": "uploads/b.png"}, {"path": "uploads/c.png"}]},
                "user_1", storage_dir=str(tmp_path),
            )
        assert any("maximum is 2" in m for m in excinfo.value.field_errors["refs"])

    def test_multi_item_non_list_value_is_rejected(self, tmp_path):
        preset = _preset([_field("refs", "image", configuration={"multi": True})])
        with pytest.raises(FormBindingError) as excinfo:
            bind_form(
                preset, "txt2img", None, {"refs": "uploads/a.png"}, "user_1",
                storage_dir=str(tmp_path),
            )
        assert any("list of media items" in m for m in excinfo.value.field_errors["refs"])

    def test_multi_item_label_is_stripped_and_capped(self, tmp_path):
        preset = _preset([_field("refs", "image", configuration={"multi": True})])
        item = {"path": "uploads/a.png", "label": "  " + ("x" * 100) + "  "}
        bound = bind_form(
            preset, "txt2img", None, {"refs": [item]}, "user_1",
            storage_dir=str(tmp_path),
        )
        assert bound.values["refs"][0]["label"] == "x" * 64

    def test_multi_item_empty_list_is_left_alone(self, tmp_path):
        preset = _preset([_field("refs", "image", configuration={"multi": True})])
        bound = bind_form(
            preset, "txt2img", None, {"refs": []}, "user_1",
            storage_dir=str(tmp_path),
        )
        assert bound.values["refs"] == []

    def test_non_multi_field_is_unaffected_by_multi_support(self, tmp_path):
        """Bite-check: a plain (non-multi) image field's containment
        behavior is byte-identical to before multi-item support existed."""
        storage = tmp_path / "storage"
        (storage / "uploads").mkdir(parents=True)
        preset = _preset([_field("source_image", "image")])
        bound = bind_form(
            preset, "txt2img", None, {"source_image": "uploads/photo.png"}, "user_1",
            storage_dir=str(storage),
        )
        assert bound.values["source_image"] == "uploads/photo.png"


class TestRegisteredFieldInputValidators:
    """`resolution` and `lora_picker` carry their own `input()` validator
    (format-checking, strength clamping/cardinality) that `_validate_field`'s
    four generic buckets (media/bool/numeric/select) don't cover. bind_form
    must run it - it's the only path the generation submission route ever
    calls."""

    def test_malformed_resolution_is_rejected(self):
        preset = _preset([_field("resolution", "resolution", default="1024x1024")])
        with pytest.raises(FormBindingError) as excinfo:
            bind_form(preset, "txt2img", None, {"resolution": "not-a-resolution"}, "user_1")
        assert "resolution" in excinfo.value.field_errors

    def test_non_numeric_resolution_parts_are_rejected(self):
        preset = _preset([_field("resolution", "resolution", default="1024x1024")])
        with pytest.raises(FormBindingError):
            bind_form(preset, "txt2img", None, {"resolution": "widextall"}, "user_1")

    def test_valid_resolution_passes_through(self):
        preset = _preset([_field("resolution", "resolution", default="1024x1024")])
        bound = bind_form(preset, "txt2img", None, {"resolution": "1920x1080"}, "user_1")
        assert bound.values["resolution"] == "1920x1080"

    def test_resolution_default_is_not_reverified_and_survives(self):
        """The field's own declared default is trusted YAML content, not
        untrusted wire input - the validator still runs against it (as it
        would for any other value) but a well-formed default must pass."""
        preset = _preset([_field("resolution", "resolution", default="1024x1024")])
        bound = bind_form(preset, "txt2img", None, {}, "user_1")
        assert bound.values["resolution"] == "1024x1024"

    def test_lora_strength_outside_range_is_clamped_not_passed_through(self):
        preset = _preset([
            _field("loras", "lora_picker", default=[], configuration={
                "strength_min": -2.0, "strength_max": 2.0,
            })
        ])
        bound = bind_form(
            preset, "txt2img", None,
            {"loras": [{"model": "chaos.safetensors", "strength": 999}]},
            "user_1",
        )
        assert bound.values["loras"] == [{"model": "chaos.safetensors", "strength": 2.0}]

    def test_lora_entry_count_over_max_items_raises(self):
        preset = _preset([
            _field("loras", "lora_picker", default=[], configuration={"max_items": 2})
        ])
        with pytest.raises(FormBindingError) as excinfo:
            bind_form(
                preset, "txt2img", None,
                {"loras": [
                    {"model": "a.safetensors"},
                    {"model": "b.safetensors"},
                    {"model": "c.safetensors"},
                ]},
                "user_1",
            )
        assert "loras" in excinfo.value.field_errors

    def test_lora_entries_within_bounds_pass_through(self):
        preset = _preset([
            _field("loras", "lora_picker", default=[], configuration={
                "strength_min": -2.0, "strength_max": 2.0, "max_items": 6,
            })
        ])
        bound = bind_form(
            preset, "txt2img", None,
            {"loras": [{"model": "a.safetensors", "strength": 0.8}]},
            "user_1",
        )
        assert bound.values["loras"] == [{"model": "a.safetensors", "strength": 0.8}]

    def test_lora_model_ref_on_an_entry_is_not_mistaken_for_a_top_level_ref(self):
        """`is_model_ref` only short-circuits when the FIELD's whole value is
        a `model:<id>` string; a lora_picker's value is a list, so its
        entries still go through LoraPicker.input() untouched."""
        ref = make_model_ref("some_lora_id")
        preset = _preset([_field("loras", "lora_picker", default=[])])
        bound = bind_form(
            preset, "txt2img", None,
            {"loras": [{"model": ref, "strength": 1.0}]},
            "user_1",
        )
        assert bound.values["loras"] == [{"model": ref, "strength": 1.0}]


class TestRegisteredMediaInputValidators:
    """`image`/`video`/`audio`/`media` (each its own class, per
    src/features/fields/builtin.py) are registered in `_INPUT_VALIDATORS` -
    shape/multi/max_items/label/accepted_types/max_resolution/duration
    validation runs live on every real submission, ahead of
    `_check_media_containment`'s path-only check (TestMediaContainment/
    TestMultiItemMediaContainment above already cover containment in
    isolation with storage_dir set; these prove the FULL pipeline - both
    stages together, no storage_dir passed since shape validation doesn't
    need one)."""

    @pytest.mark.parametrize("field_type", ["image", "video", "audio", "media"])
    def test_real_single_media_ref_dict_passes_through_unchanged(self, field_type):
        preset = _preset([_field("source", field_type)])
        item = {
            "path": "generations/2026-01-01/gen1/0.bin",
            "relative_path": "generations/2026-01-01/gen1/0.bin",
            "url": "/api/media/generations/gen1/0.bin",
            "name": "0.bin",
            "type": field_type,
        }
        bound = bind_form(preset, "txt2img", None, {"source": item}, "user_1")
        assert bound.values["source"] == item

    @pytest.mark.parametrize("field_type", ["image", "video", "audio"])
    def test_malformed_media_dict_is_rejected(self, field_type):
        """A dict with no path/relative_path/url and no base64 `data` is a
        broken submission - the live validator catches it; before this
        field type was registered, it would have reached the pipeline
        unvalidated."""
        preset = _preset([_field("source", field_type)])
        with pytest.raises(FormBindingError) as excinfo:
            bind_form(preset, "txt2img", None, {"source": {"name": "x"}}, "user_1")
        assert "source" in excinfo.value.field_errors

    def test_media_type_uses_its_own_validator(self):
        preset = _preset([_field("source", "media")])
        with pytest.raises(FormBindingError) as excinfo:
            bind_form(preset, "txt2img", None, {"source": {"name": "x"}}, "user_1")
        assert any("Missing media data" in m for m in excinfo.value.field_errors["source"])

    def test_media_field_accepted_types_rejects_a_disallowed_category(self):
        """`accepted_types` is enforced live through bind_form, not just
        Media.input() in isolation."""
        preset = _preset([_field(
            "refs", "media", configuration={"multi": True, "accepted_types": ["image", "video"]},
        )])
        items = [
            {"path": "uploads/a.png", "type": "image"},
            {"path": "uploads/a.mp3", "type": "audio"},
        ]
        with pytest.raises(FormBindingError) as excinfo:
            bind_form(preset, "txt2img", None, {"refs": items}, "user_1")
        assert any("not accepted" in m for m in excinfo.value.field_errors["refs"])

    def test_media_field_mixed_items_enforce_type_specific_totals_independently(self):
        """A field holding images, videos AND audio at once must partition
        duration totals by category - the total video cap must not be
        tripped by audio duration and vice versa, and an image (no
        duration at all) must not affect either total."""
        preset = _preset([_field(
            "refs", "media",
            configuration={
                "multi": True,
                "max_total_video_duration_seconds": 10,
                "max_total_audio_duration_seconds": 10,
            },
        )])
        items = [
            {"path": "uploads/a.png", "type": "image", "metadata": {"width": 512, "height": 512}},
            {"path": "uploads/a.mp4", "type": "video", "metadata": {"duration_seconds": 6}},
            {"path": "uploads/b.mp4", "type": "video", "metadata": {"duration_seconds": 6}},
            {"path": "uploads/a.mp3", "type": "audio", "metadata": {"duration_seconds": 4}},
        ]
        with pytest.raises(FormBindingError) as excinfo:
            bind_form(preset, "txt2img", None, {"refs": items}, "user_1")
        messages = excinfo.value.field_errors["refs"]
        assert any("video items total 12s" in m for m in messages)
        assert not any("audio items total" in m for m in messages)

    def test_media_field_unknown_duration_fails_open_on_the_total(self):
        """A best-effort probe that came back empty for one item must not
        make the total check reject a submission it can't actually verify -
        see media_input._check_media_constraints' fail-open policy."""
        preset = _preset([_field(
            "refs", "media", configuration={"multi": True, "max_total_video_duration_seconds": 1},
        )])
        items = [
            {"path": "uploads/a.mp4", "type": "video", "metadata": {"duration_seconds": 30}},
            {"path": "uploads/b.mp4", "type": "video", "metadata": {}},
        ]
        bound = bind_form(preset, "txt2img", None, {"refs": items}, "user_1")
        assert bound.values["refs"] == items

    def test_multi_item_label_and_containment_flow_through_full_pipeline(self, tmp_path):
        """Shape validation (Image.input(), stripping/capping the label)
        and path containment (_check_media_containment) both run, in that
        order, against the same submission."""
        preset = _preset([_field("refs", "image", configuration={"multi": True, "max_items": 2})])
        items = [
            {"path": "uploads/a.png", "label": "  " + ("x" * 100) + "  "},
            {"path": "uploads/b.png"},
        ]
        bound = bind_form(
            preset, "txt2img", None, {"refs": items}, "user_1",
            storage_dir=str(tmp_path),
        )
        assert bound.values["refs"][0]["label"] == "x" * 64
        assert "label" not in bound.values["refs"][1]

    def test_multi_item_over_max_items_rejected_by_the_field_validator(self):
        """max_items is enforced by Image.input() itself now (live), not
        just by containment's defense-in-depth guard - exercised here with
        no storage_dir, so containment would have nothing to check."""
        preset = _preset([_field("refs", "image", configuration={"multi": True, "max_items": 1})])
        with pytest.raises(FormBindingError) as excinfo:
            bind_form(
                preset, "txt2img", None,
                {"refs": [{"path": "uploads/a.png"}, {"path": "uploads/b.png"}]},
                "user_1",
            )
        assert any("maximum is 1" in m for m in excinfo.value.field_errors["refs"])

    def test_single_dict_media_traversal_is_rejected_end_to_end(self, tmp_path):
        """Closes the containment gap for the single (non-multi, non-array)
        case end to end through bind_form - bite-checked (see
        _check_media_ref_dict's neutered-branch check in binding.py history)."""
        preset = _preset([_field("source_image", "image")])
        value = {"path": "../../etc/passwd", "type": "image"}
        with pytest.raises(FormBindingError) as excinfo:
            bind_form(
                preset, "txt2img", None, {"source_image": value}, "user_1",
                storage_dir=str(tmp_path),
            )
        assert any("escapes the user's storage directory" in m for m in excinfo.value.field_errors["source_image"])

    def test_legacy_base64_upload_still_works_through_bind_form(self):
        """Backward compatibility: a direct API caller sending the old
        base64 upload shape (not MediaLoaderField's media-ref shape) is
        still accepted and decoded."""
        preset = _preset([_field("source_image", "image")])
        b64 = base64.b64encode(b"\x00" * 8).decode()
        bound = bind_form(
            preset, "txt2img", None,
            {"source_image": {"data": b64, "name": "x.png", "type": "image/png", "size": 8}},
            "user_1",
        )
        assert isinstance(bound.values["source_image"]["data"], bytes)


class TestReturnShape:
    def test_bound_form_is_frozen(self):
        preset = _preset([_field("steps", "slider", default=20)])
        bound = bind_form(preset, "txt2img", None, {}, "user_1")
        assert isinstance(bound, BoundForm)
        with pytest.raises(Exception):
            bound.form_name = "other"
