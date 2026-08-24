"""Schema validation/parsing for Phase-3 setup recipes.

Pure dict-in/list-of-strings-out tests - no filesystem, no DB. Mirrors how
`RecipeCatalog` and `scripts/recipe_lint.py` both consume `validate_recipe_dict`
and `parse_recipe`.
"""

import pytest

from src.features.setup.recipe_schema import parse_recipe, validate_recipe_dict


def _valid_recipe(**overrides):
    data = {
        "schema_version": 1,
        "id": "sdxl-starter",
        "version": 1,
        "name": "SDXL Starter",
        "engine": "native",
        "plugins": [{"id": "downloader", "reason": "fetch the checkpoint"}],
        "backend": {"engine": "native"},
        "artifacts": [
            {
                "id": "sdxl-checkpoint",
                "kind": "checkpoint",
                "model_type": "checkpoint",
                "filename": "model.safetensors",
                "capability": "model-lookup",
            }
        ],
        "presets": [{"preset_id": "PRESET1", "assign_to_owner": True}],
        "smoke": {"preset_id": "PRESET1", "mode": "txt2img"},
        "steps": [
            {
                "key": "plugins.ensure",
                "kind": "plugins.ensure",
                "title": "Enable plugins",
                "params": {"plugin_ids": ["downloader"]},
            },
            {
                "key": "backend.ensure",
                "kind": "backend.ensure",
                "title": "Ensure backend",
                "params": {"engine": "native"},
            },
            {
                "key": "preset.ensure",
                "kind": "preset.ensure",
                "title": "Install preset",
                "params": {"preset_id": "PRESET1"},
            },
            {
                "key": "pipeline.render",
                "kind": "pipeline.render",
                "title": "Validate pipeline",
                "params": {"preset_id": "PRESET1", "mode": "txt2img"},
            },
        ],
    }
    data.update(overrides)
    return data


def test_valid_recipe_has_no_issues():
    assert validate_recipe_dict(_valid_recipe()) == []


def test_parse_recipe_roundtrips_valid_data():
    data = _valid_recipe()
    recipe = parse_recipe(data)

    assert recipe.id == "sdxl-starter"
    assert recipe.engine == "native"
    assert [s.key for s in recipe.steps] == [
        "plugins.ensure",
        "backend.ensure",
        "preset.ensure",
        "pipeline.render",
    ]
    assert recipe.get_step("backend.ensure").kind == "backend.ensure"
    assert recipe.next_step_after("plugins.ensure").key == "backend.ensure"
    assert recipe.next_step_after("pipeline.render") is None
    assert recipe.next_step_after("does-not-exist") is None
    assert recipe.get_artifact("sdxl-checkpoint").filename == "model.safetensors"


def test_not_a_mapping_is_rejected():
    assert validate_recipe_dict(["not", "a", "dict"]) != []
    assert validate_recipe_dict(None) != []


@pytest.mark.parametrize(
    "overrides,expected_substring",
    [
        ({"schema_version": 99}, "schema_version"),
        ({"schema_version": "1"}, "schema_version"),
        ({"id": "Not_A_Slug"}, "id"),
        ({"id": ""}, "id"),
        ({"version": 0}, "version"),
        ({"version": "1"}, "version"),
        ({"name": ""}, "name"),
        ({"engine": ""}, "engine"),
    ],
)
def test_top_level_field_violations(overrides, expected_substring):
    issues = validate_recipe_dict(_valid_recipe(**overrides))
    assert issues
    assert any(expected_substring in issue for issue in issues)


def test_duplicate_plugin_ids_rejected():
    data = _valid_recipe(plugins=[{"id": "downloader"}, {"id": "downloader"}])
    issues = validate_recipe_dict(data)
    assert any("duplicate plugin id" in issue for issue in issues)


def test_backend_engine_must_match_top_level_engine():
    data = _valid_recipe(backend={"engine": "comfyui"})
    issues = validate_recipe_dict(data)
    assert any("backend.engine" in issue for issue in issues)


def test_duplicate_artifact_ids_rejected():
    data = _valid_recipe(
        artifacts=[
            {"id": "a", "kind": "checkpoint", "model_type": "checkpoint", "filename": "x.safetensors"},
            {"id": "a", "kind": "checkpoint", "model_type": "checkpoint", "filename": "y.safetensors"},
        ]
    )
    issues = validate_recipe_dict(data)
    assert any("duplicate artifact id" in issue for issue in issues)


def test_artifact_size_bytes_must_be_non_negative_int():
    data = _valid_recipe(
        artifacts=[
            {
                "id": "a",
                "kind": "checkpoint",
                "model_type": "checkpoint",
                "filename": "x.safetensors",
                "size_bytes": -1,
            }
        ]
    )
    issues = validate_recipe_dict(data)
    assert any("size_bytes" in issue for issue in issues)


def test_artifact_checksum_requires_algorithm():
    data = _valid_recipe(
        artifacts=[
            {
                "id": "a",
                "kind": "checkpoint",
                "model_type": "checkpoint",
                "filename": "x.safetensors",
                "checksum": {"value": "deadbeef"},
            }
        ]
    )
    issues = validate_recipe_dict(data)
    assert any("algorithm" in issue for issue in issues)


def test_checksum_value_may_be_null():
    data = _valid_recipe(
        artifacts=[
            {
                "id": "a",
                "kind": "checkpoint",
                "model_type": "checkpoint",
                "filename": "x.safetensors",
                "checksum": {"algorithm": "sha256", "value": None},
            }
        ]
    )
    assert validate_recipe_dict(data) == []


def test_presets_must_be_non_empty():
    data = _valid_recipe(presets=[])
    issues = validate_recipe_dict(data)
    assert any("presets" in issue and "at least one" in issue for issue in issues)


def test_steps_must_be_non_empty():
    data = _valid_recipe(steps=[])
    issues = validate_recipe_dict(data)
    assert any("steps" in issue and "at least one" in issue for issue in issues)


def test_duplicate_step_keys_rejected():
    data = _valid_recipe()
    data["steps"].append(dict(data["steps"][0]))
    issues = validate_recipe_dict(data)
    assert any("duplicate step key" in issue for issue in issues)


def test_unknown_step_kind_rejected():
    data = _valid_recipe()
    data["steps"][0]["kind"] = "totally.unknown"
    issues = validate_recipe_dict(data)
    assert any("unknown step kind" in issue for issue in issues)


def test_deferred_step_kinds_are_recognized():
    data = _valid_recipe(
        artifacts=[
            {
                "id": "a",
                "kind": "checkpoint",
                "model_type": "checkpoint",
                "filename": "x.safetensors",
            }
        ]
    )
    data["steps"].append(
        {
            "key": "artifacts.fetch",
            "kind": "artifacts.fetch",
            "title": "Download",
            "params": {"artifact_ids": ["a"]},
        }
    )
    data["steps"].append(
        {
            "key": "generation.smoke",
            "kind": "generation.smoke",
            "title": "Smoke test",
            "params": {"preset_id": "PRESET1", "mode": "txt2img"},
        }
    )
    assert validate_recipe_dict(data) == []


@pytest.mark.parametrize(
    "step,expected_substring",
    [
        ({"key": "a", "kind": "plugins.ensure", "title": "t", "params": {}}, "plugin_ids"),
        (
            {"key": "a", "kind": "plugins.ensure", "title": "t", "params": {"plugin_ids": ["not-declared"]}},
            "undeclared plugin",
        ),
        ({"key": "a", "kind": "backend.ensure", "title": "t", "params": {}}, "params.engine"),
        (
            {"key": "a", "kind": "preset.ensure", "title": "t", "params": {"preset_id": "NOT-DECLARED"}},
            "undeclared preset",
        ),
        ({"key": "a", "kind": "preset.ensure", "title": "t", "params": {}}, "params.preset_id"),
        (
            {"key": "a", "kind": "pipeline.render", "title": "t", "params": {"preset_id": "PRESET1"}},
            "params.mode",
        ),
    ],
)
def test_step_param_referential_integrity(step, expected_substring):
    data = _valid_recipe(steps=[step])
    issues = validate_recipe_dict(data)
    assert any(expected_substring in issue for issue in issues), issues
