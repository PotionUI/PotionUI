"""Tests for model_values.py's model-existence validation.

`get_form_schema`'s `form_schema.properties` is never a flat `{name: spec}`
map - real presets lay fields out under a `tabs` root whose `children` nest
`tab`/`section`/`row` wrappers arbitrarily deep (see Krea-2's txt2img form).
Fixtures here mirror that real shape rather than a flattened stand-in.
"""

from unittest.mock import MagicMock

from src.features.llm.tools.model_values import (
    model_field_names,
    preset_form_model_errors,
    validate_model_value,
)

NESTED_SCHEMA_PROPERTIES = {
    "tabs": {
        "type": "tabs",
        "children": [
            {
                "type": "tab",
                "title": "Generation",
                "children": [
                    {"type": "select", "name": "speed_profile"},
                    {
                        "type": "section",
                        "children": [
                            {
                                "type": "model",
                                "name": "diffusion_model",
                                "configuration": {"model_type": "diffusion_model"},
                            },
                            {
                                "type": "model",
                                "name": "text_encoder",
                                "configuration": {"model_type": "clip"},
                            },
                            {
                                "type": "model",
                                "name": "vae",
                                "configuration": {"model_type": "vae"},
                            },
                        ],
                    },
                ],
            },
        ],
    },
}


def _model_index_manager(*, found: bool):
    manager = MagicMock()
    if found:
        manager.model_repo.get_by_file_path.return_value = MagicMock(
            to_dict=lambda **_: {"id": "m1", "filename": "found.safetensors"}
        )
    else:
        manager.model_repo.get_by_file_path.return_value = None
        manager.model_repo.get_all.return_value = []
    return manager


class TestModelFieldNames:
    def test_walks_nested_tabs_sections_and_rows(self):
        assert model_field_names(NESTED_SCHEMA_PROPERTIES) == {
            "diffusion_model", "text_encoder", "vae",
        }

    def test_ignores_non_model_fields(self):
        assert "speed_profile" not in model_field_names(NESTED_SCHEMA_PROPERTIES)

    def test_empty_for_non_dict_input(self):
        assert model_field_names(None) == set()
        assert model_field_names({}) == set()


class TestValidateModelValue:
    def test_empty_value_is_always_allowed(self):
        assert validate_model_value("diffusion_model", None, _model_index_manager(found=False)) == []
        assert validate_model_value("diffusion_model", "", _model_index_manager(found=False)) == []

    def test_no_model_index_manager_skips_rather_than_rejects(self):
        assert validate_model_value("diffusion_model", "whatever.safetensors", None) == []

    def test_unresolvable_filename_is_rejected(self):
        errors = validate_model_value("diffusion_model", "made_up.safetensors", _model_index_manager(found=False))
        assert len(errors) == 1
        assert "diffusion_model" in errors[0]
        assert "made_up.safetensors" in errors[0]

    def test_resolvable_filename_is_accepted(self):
        errors = validate_model_value("diffusion_model", "found.safetensors", _model_index_manager(found=True))
        assert errors == []


class TestPresetFormModelErrors:
    def _preset_manager(self):
        preset_manager = MagicMock()
        preset_manager.get_form_schema.return_value = {"form_schema": {"properties": NESTED_SCHEMA_PROPERTIES}}
        return preset_manager

    def test_rejects_every_unresolvable_model_field(self):
        errors = preset_form_model_errors(
            self._preset_manager(), _model_index_manager(found=False), "krea2", "txt2img",
            {"diffusion_model": "a.safetensors", "text_encoder": "b.safetensors", "speed_profile": "turbo"},
        )
        assert len(errors) == 2

    def test_accepts_resolvable_model_fields(self):
        errors = preset_form_model_errors(
            self._preset_manager(), _model_index_manager(found=True), "krea2", "txt2img",
            {"diffusion_model": "found.safetensors"},
        )
        assert errors == []

    def test_no_preset_manager_skips(self):
        assert preset_form_model_errors(None, _model_index_manager(found=False), "krea2", "txt2img", {"diffusion_model": "x"}) == []

    def test_no_proposed_values_skips(self):
        assert preset_form_model_errors(self._preset_manager(), _model_index_manager(found=False), "krea2", "txt2img", {}) == []
