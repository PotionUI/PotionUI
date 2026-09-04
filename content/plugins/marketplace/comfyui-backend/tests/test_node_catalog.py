"""`backend/preset_import/node_catalog.py`: the shipped catalog loads and is
internally valid, and `validate_catalog` actually catches what it claims to."""

from pathlib import Path

import pytest
import yaml

from backend.preset_import.node_catalog import (
    CATEGORIES,
    FIELD_TYPES,
    HISTORY_FORMATS,
    LINK_KINDS,
    ROLES,
    TRANSFORMS,
    NodeCatalog,
    get_catalog,
    load_catalog,
    validate_catalog,
)


class TestShippedCatalog:
    def test_loads_without_error(self):
        catalog = load_catalog()
        assert catalog.classes
        assert len(catalog.classes) == len(set(catalog.classes))

    def test_validates_clean(self):
        assert validate_catalog(load_catalog()) == []

    def test_get_catalog_is_cached_and_returns_the_same_instance(self):
        assert get_catalog() is get_catalog()

    def test_every_role_referenced_is_in_the_closed_set(self):
        for class_type, entry in load_catalog().nodes.items():
            for input_name, spec in entry.inputs.items():
                assert spec.role in ROLES, f"{class_type}.{input_name}"

    def test_every_field_referenced_is_a_real_core_field_type(self):
        for class_type, entry in load_catalog().nodes.items():
            for input_name, spec in entry.inputs.items():
                assert spec.field in FIELD_TYPES, f"{class_type}.{input_name}"

    def test_every_transform_referenced_is_valid(self):
        for class_type, entry in load_catalog().nodes.items():
            for input_name, spec in entry.inputs.items():
                assert spec.transform in TRANSFORMS, f"{class_type}.{input_name}"

    def test_every_category_referenced_is_valid(self):
        for class_type, entry in load_catalog().nodes.items():
            assert entry.category in CATEGORIES, class_type

    def test_every_link_kind_referenced_is_valid(self):
        for class_type, entry in load_catalog().nodes.items():
            for input_name, kind in entry.links.items():
                assert kind in LINK_KINDS, f"{class_type}.links.{input_name}"

    def test_by_category_returns_only_classes_in_that_category(self):
        catalog = load_catalog()
        samplers = catalog.by_category("sampler")
        assert "KSampler" in samplers
        assert "SamplerCustomAdvanced" in samplers
        assert "CheckpointLoaderSimple" not in samplers

    def test_get_returns_none_for_an_unknown_class(self):
        assert load_catalog().get("SomeCustomNode") is None

    def test_model_file_roles_declare_a_folder(self):
        for class_type, entry in load_catalog().nodes.items():
            for input_name, spec in entry.inputs.items():
                if spec.role in ("checkpoint", "diffusion_model", "clip", "vae", "lora_slot"):
                    assert spec.folder, f"{class_type}.{input_name} has role {spec.role!r} with no folder"

    def test_triple_clip_loader_has_three_clip_inputs_from_text_encoders(self):
        entry = load_catalog().get("TripleCLIPLoader")
        assert entry is not None
        assert entry.category == "loader"
        clip_inputs = {"clip_name1", "clip_name2", "clip_name3"}
        assert set(entry.inputs) == clip_inputs
        for input_name in clip_inputs:
            spec = entry.inputs[input_name]
            assert spec.role == "clip"
            assert spec.field == "model"
            assert spec.folder == "text_encoders"

    def test_empty_hunyuan_latent_video_yields_video_sizing_roles(self):
        entry = load_catalog().get("EmptyHunyuanLatentVideo")
        assert entry is not None
        assert entry.category == "latent"
        roles_by_input = {name: spec.role for name, spec in entry.inputs.items()}
        assert roles_by_input == {
            "width": "resolution_width",
            "height": "resolution_height",
            "length": "frames",
            "batch_size": "batch_size",
        }


class TestValidateCatalogCatchesProblems:
    def _catalog(self, nodes: dict) -> NodeCatalog:
        return NodeCatalog.model_validate({"version": 1, "nodes": nodes})

    def test_unknown_category_is_flagged(self):
        catalog = self._catalog({"Foo": {"category": "not_a_real_category"}})
        problems = validate_catalog(catalog)
        assert any("unknown category" in p for p in problems)

    def test_unknown_role_is_flagged(self):
        catalog = self._catalog(
            {
                "Foo": {
                    "category": "loader",
                    "inputs": {
                        "bar": {"role": "not_a_real_role", "field": "model", "name": "bar", "label": "Bar"}
                    },
                }
            }
        )
        problems = validate_catalog(catalog)
        assert any("unknown role" in p for p in problems)

    def test_unknown_field_type_is_flagged(self):
        catalog = self._catalog(
            {
                "Foo": {
                    "category": "loader",
                    "inputs": {
                        "bar": {"role": "option", "field": "not_a_real_field", "name": "bar", "label": "Bar"}
                    },
                }
            }
        )
        problems = validate_catalog(catalog)
        assert any("unknown field type" in p for p in problems)

    def test_unknown_transform_is_flagged(self):
        catalog = self._catalog(
            {
                "Foo": {
                    "category": "loader",
                    "inputs": {
                        "bar": {
                            "role": "option", "field": "textbox", "name": "bar", "label": "Bar",
                            "transform": "not_a_real_transform",
                        }
                    },
                }
            }
        )
        problems = validate_catalog(catalog)
        assert any("unknown transform" in p for p in problems)

    def test_unknown_history_format_is_flagged(self):
        catalog = self._catalog(
            {
                "Foo": {
                    "category": "loader",
                    "inputs": {
                        "bar": {
                            "role": "option", "field": "textbox", "name": "bar", "label": "Bar",
                            "history": "not_a_real_format",
                        }
                    },
                }
            }
        )
        problems = validate_catalog(catalog)
        assert any("unknown history format" in p for p in problems)

    def test_unknown_link_kind_is_flagged(self):
        catalog = self._catalog({"Foo": {"category": "loader", "links": {"model": "not_a_real_kind"}}})
        problems = validate_catalog(catalog)
        assert any("unknown link kind" in p for p in problems)

    def test_branch_side_not_declared_as_passthrough_is_flagged(self):
        catalog = self._catalog(
            {
                "Foo": {
                    "category": "modifier",
                    "links": {"on_true": "passthrough", "on_false": "model_chain"},
                    "branch": {"input": "switch", "on_true": "on_true", "on_false": "on_false"},
                }
            }
        )
        problems = validate_catalog(catalog)
        assert any("on_false" in p and "passthrough" in p for p in problems)

    def test_branch_input_declared_as_a_link_is_flagged(self):
        catalog = self._catalog(
            {
                "Foo": {
                    "category": "modifier",
                    "links": {"on_true": "passthrough", "on_false": "passthrough", "switch": "model_chain"},
                    "branch": {"input": "switch", "on_true": "on_true", "on_false": "on_false"},
                }
            }
        )
        problems = validate_catalog(catalog)
        assert any("must not itself be a link" in p for p in problems)

    def test_bite_check_a_valid_branch_is_not_flagged(self):
        catalog = self._catalog(
            {
                "Foo": {
                    "category": "modifier",
                    "links": {"on_true": "passthrough", "on_false": "passthrough"},
                    "branch": {"input": "switch", "on_true": "on_true", "on_false": "on_false"},
                    "inputs": {
                        "switch": {"role": "option", "field": "checkbox", "name": "switch", "label": "Switch"}
                    },
                }
            }
        )
        assert validate_catalog(catalog) == []

    def test_model_role_without_folder_is_flagged(self):
        catalog = self._catalog(
            {
                "Foo": {
                    "category": "loader",
                    "inputs": {
                        "bar": {"role": "checkpoint", "field": "model", "name": "bar", "label": "Bar"}
                    },
                }
            }
        )
        problems = validate_catalog(catalog)
        assert any("needs 'folder'" in p for p in problems)

    def test_reusing_a_form_field_name_under_a_mismatched_field_type_is_flagged(self):
        """Two different node classes sharing a form field name is normal
        (every sampler family's own seed input is named "seed") - it's only
        a problem when they render as different core field types, which is
        what this asserts. See `validate_catalog`'s docstring."""
        catalog = self._catalog(
            {
                "Foo": {
                    "category": "sampling",
                    "inputs": {
                        "seed": {"role": "seed", "field": "seed", "name": "seed", "label": "Seed"}
                    },
                },
                "Bar": {
                    "category": "sampling",
                    "inputs": {
                        "seed": {"role": "option", "field": "number", "name": "seed", "label": "Seed"}
                    },
                },
            }
        )
        problems = validate_catalog(catalog)
        assert any("also used as field type" in p for p in problems)

    def test_bite_check_reusing_a_name_under_the_same_field_type_is_not_flagged(self):
        """Confirms the assertion above can actually fail: sharing a name
        under the SAME field type (the intended, common case) must not be
        flagged at all."""
        catalog = self._catalog(
            {
                "Foo": {
                    "category": "sampling",
                    "inputs": {
                        "seed": {"role": "seed", "field": "seed", "name": "seed", "label": "Seed"}
                    },
                },
                "Bar": {
                    "category": "sampling",
                    "inputs": {
                        "seed": {"role": "seed", "field": "seed", "name": "seed", "label": "Seed"}
                    },
                },
            }
        )
        assert validate_catalog(catalog) == []

    def test_history_formats_set_is_exhaustive_for_the_shared_schema_contract(self):
        assert HISTORY_FORMATS == {"model_name", "number", "wxh", "list", "as_is"}
