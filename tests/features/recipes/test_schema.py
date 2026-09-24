"""Schema validation/parsing for recipes.

Pure dict-in/list-of-strings-out tests - no filesystem, no DB. Mirrors how
`RecipeCatalog` and `scripts/recipe_lint.py` both consume `validate_recipe_dict`
and `parse_recipe`.
"""

import pytest

from src.features.recipes.schema import parse_recipe, validate_recipe_dict


def _valid_recipe(**overrides):
    data = {
        "schema_version": 1,
        "id": "sdxl-starter",
        "version": 1,
        "name": "SDXL Starter",
        "engine": "native",
        "category": "image",
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
        ({"category": ""}, "category"),
        ({"category": "not-a-real-category"}, "category"),
    ],
)
def test_top_level_field_violations(overrides, expected_substring):
    issues = validate_recipe_dict(_valid_recipe(**overrides))
    assert issues
    assert any(expected_substring in issue for issue in issues)


def test_category_missing_entirely_is_an_issue():
    data = _valid_recipe()
    del data["category"]
    issues = validate_recipe_dict(data)
    assert any("category" in issue for issue in issues)


@pytest.mark.parametrize("category", ["image", "video", "audio", "3d", "utility"])
def test_every_closed_category_value_is_accepted(category):
    assert validate_recipe_dict(_valid_recipe(category=category)) == []


def test_valid_category_round_trips_through_parse_recipe():
    recipe = parse_recipe(_valid_recipe(category="video"))
    assert recipe.category == "video"


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


def test_artifact_gated_must_be_boolean():
    data = _valid_recipe(
        artifacts=[
            {
                "id": "a",
                "kind": "checkpoint",
                "model_type": "checkpoint",
                "filename": "x.safetensors",
                "gated": "yes",
            }
        ]
    )
    issues = validate_recipe_dict(data)
    assert any("gated" in issue for issue in issues)


def test_artifact_license_url_must_be_non_empty_string():
    data = _valid_recipe(
        artifacts=[
            {
                "id": "a",
                "kind": "checkpoint",
                "model_type": "checkpoint",
                "filename": "x.safetensors",
                "license_url": "",
            }
        ]
    )
    issues = validate_recipe_dict(data)
    assert any("license_url" in issue for issue in issues)


def test_gated_artifact_with_license_url_round_trips():
    data = _valid_recipe(
        artifacts=[
            {
                "id": "a",
                "kind": "checkpoint",
                "model_type": "checkpoint",
                "filename": "x.safetensors",
                "gated": True,
                "license_url": "https://huggingface.co/some/model",
            }
        ]
    )
    assert validate_recipe_dict(data) == []
    recipe = parse_recipe(data)
    artifact = recipe.get_artifact("a")
    assert artifact.gated is True
    assert artifact.license_url == "https://huggingface.co/some/model"


def test_artifact_gated_and_license_url_default_when_omitted():
    recipe = parse_recipe(_valid_recipe())
    artifact = recipe.get_artifact("sdxl-checkpoint")
    assert artifact.gated is False
    assert artifact.license_url is None


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


# --- onboarding-only steps -------------------------------------------------


def test_onboarding_only_defaults_to_false_and_round_trips():
    data = _valid_recipe(
        steps=[
            {"key": "a", "kind": "backend.ensure", "title": "t", "params": {"engine": "native"}},
            {
                "key": "b",
                "kind": "workspace.activate",
                "title": "Finish",
                "onboarding_only": True,
                "params": {},
            },
        ]
    )
    assert validate_recipe_dict(data) == []

    recipe = parse_recipe(data)

    assert [s.onboarding_only for s in recipe.steps] == [False, True]


def test_non_boolean_onboarding_only_is_an_issue():
    data = _valid_recipe(
        steps=[
            {
                "key": "a",
                "kind": "backend.ensure",
                "title": "t",
                "onboarding_only": "yes",
                "params": {"engine": "native"},
            }
        ]
    )
    assert any("onboarding_only" in issue for issue in validate_recipe_dict(data))


def test_admin_plan_drops_onboarding_only_steps_and_reroutes_next():
    recipe = parse_recipe(
        _valid_recipe(
            steps=[
                {"key": "a", "kind": "backend.ensure", "title": "t", "params": {"engine": "native"}},
                {
                    "key": "b",
                    "kind": "workspace.activate",
                    "title": "Finish",
                    "onboarding_only": True,
                    "params": {},
                },
                {"key": "c", "kind": "models.index", "title": "t", "params": {"engine": "native"}},
            ]
        )
    )

    assert [s.key for s in recipe.steps_for_mode("onboarding")] == ["a", "b", "c"]
    assert [s.key for s in recipe.steps_for_mode("admin")] == ["a", "c"]
    assert recipe.next_step_after("a", "onboarding").key == "b"
    assert recipe.next_step_after("a", "admin").key == "c"


# --- plugin-registered step kinds ------------------------------------------


def test_unregistered_step_kind_is_an_error():
    data = _valid_recipe(
        steps=[{"key": "a", "kind": "collections.ensure", "title": "t", "params": {}}]
    )
    assert any("unknown step kind" in issue for issue in validate_recipe_dict(data))


def test_registered_step_kind_validates():
    data = _valid_recipe(
        steps=[{"key": "a", "kind": "collections.ensure", "title": "t", "params": {}}]
    )
    assert validate_recipe_dict(data, extra_kinds={"collections.ensure"}) == []


def _variant(vid, precision="fp8", **extra):
    entry = {
        "id": vid,
        "label": "Balanced",
        "precision": precision,
        "filename": f"{vid}.safetensors",
        "size_bytes": 10,
        "checksum": {"algorithm": "sha256", "value": "ab" * 32},
        "provider_hint": {"source": "huggingface", "model_id": "org/repo", "version_id": f"main@{vid}.safetensors"},
        "uploader": "org",
        "source_url": "https://huggingface.co/org/repo",
    }
    entry.update(extra)
    return entry


def _slot(variants, **extra):
    entry = {"id": "dit", "kind": "diffusion_model", "model_type": "diffusion_model", "display_name": "DiT", "variants": variants}
    entry.update(extra)
    return entry


def _with_slot(slot):
    return _valid_recipe(artifacts=[slot])


def test_variant_slot_validates_and_parses():
    data = _with_slot(
        _slot(
            [
                _variant("dit_bf16", "bf16", recommended_for=[{"min_vram_gb": 40}]),
                _variant("dit_fp8", default=True, recommended_for=[{"min_vram_gb": 20, "generations": ["ada"]}]),
                _variant("dit_nvfp4", "nvfp4", gated=True, license_url="https://example.com/licence"),
            ]
        )
    )
    assert validate_recipe_dict(data) == []
    artifact = parse_recipe(data).get_artifact("dit")
    assert [v.id for v in artifact.variants] == ["dit_bf16", "dit_fp8", "dit_nvfp4"]
    assert artifact.filename == "dit_fp8.safetensors"
    assert artifact.default_variant.id == "dit_fp8"
    assert artifact.checksum.value == "ab" * 32
    fp8 = artifact.get_variant("dit_fp8")
    assert fp8.recommended_for[0].min_vram_gb == 20
    assert fp8.recommended_for[0].generations == ("ada",)
    assert fp8.uploader == "org"
    nvfp4 = artifact.resolve("dit_nvfp4")
    assert nvfp4.filename == "dit_nvfp4.safetensors"
    assert nvfp4.gated is True and nvfp4.license_url == "https://example.com/licence"
    assert nvfp4.display_name == "DiT (Balanced, nvfp4)"
    assert nvfp4.provider_hint["version_id"] == "main@dit_nvfp4.safetensors"


@pytest.mark.parametrize(
    "slot,expected",
    [
        (_slot([]), "'variants' must be a non-empty list"),
        (_slot([_variant("a"), _variant("b")]), "exactly one variant must be marked 'default: true' (found 0)"),
        (_slot([_variant("a", default=True), _variant("b", default=True)]), "(found 2)"),
        (_slot([_variant("a", default=True)], filename="x.safetensors"), "'filename' belongs on each variant"),
        (_slot([_variant("a", default=True)], checksum={"algorithm": "sha256"}), "'checksum' belongs on each variant"),
        (_slot([_variant("a", default=True), _variant("a")]), "duplicate variant id 'a'"),
        (_slot([_variant("a", default=True), _variant("b", filename="a.safetensors")]), "duplicate variant filename"),
        (_slot([_variant("a", "mxfp8", default=True)]), "'precision' must be one of"),
        (_slot([_variant("A b", default=True)]), "'id' must be a lowercase slug"),
        (_slot([_variant("a", default=True, filename="dir/a.safetensors")]), "bare file name"),
        (_slot([_variant("a", default=True, size_bytes=-1)]), "'size_bytes' must be a non-negative integer"),
        (_slot([_variant("a", default=True, recommended_for=[{"generations": ["volta"]}])]), "unknown GPU generation"),
        (_slot([_variant("a", default=True, recommended_for=[{"min_vram_gb": -2}])]), "'min_vram_gb' must be a non-negative number"),
        (_slot([_variant("a", default=True, recommended_for=[{}])]), "must declare 'min_vram_gb' and/or 'generations'"),
        (_slot([_variant("a", default=True, recommended_for=[{"vram": 8}])]), "unknown keys ['vram']"),
        (_slot([_variant("a", default=True, recommended_for={"min_vram_gb": 8})]), "'recommended_for' must be a list"),
        (_slot([_variant("a", default=True, uploader="")]), "'uploader' must be a non-empty string"),
        (_slot([_variant("a", default="yes")]), "'default' must be a boolean"),
        (_slot([_variant("a", default=True, tier="x")]), "unknown keys ['tier']"),
        (_slot([{"id": "a", "default": True, "filename": "a.safetensors"}]), "'label' is required"),
    ],
)
def test_variant_slot_violations(slot, expected):
    issues = validate_recipe_dict(_with_slot(slot))
    assert any(expected in issue for issue in issues), issues


def test_single_file_artifact_has_no_variants():
    recipe = parse_recipe(_valid_recipe())
    artifact = recipe.get_artifact("sdxl-checkpoint")
    assert artifact.variants == ()
    assert artifact.resolve("anything") is artifact
