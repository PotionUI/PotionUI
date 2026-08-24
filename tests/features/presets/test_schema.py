"""Tests for the canonical preset schema (src.features.presets.schema)."""

import pytest

from src.features.presets.schema import (
    PresetManifest,
    PipelineFile,
    FormFile,
    ReactionSpec,
    SPEED_PROFILE_KNOWN_KEYS,
    SpeedProfile,
    CONFIGURATION_TYPES,
    ConfigurationEntry,
    PresetLLMSpec,
    PresetLLMContextSpec,
    PresetLLMModeSpec,
    PresetRequirements,
    validate_manifest,
    validate_pipeline_file,
    validate_form_file,
)


def valid_manifest_data(**overrides):
    data = {
        "schema": 1,
        "id": "01K0W24A3RADXXABH16YQ7KE90",
        "name": "Test Preset",
        "version": "1.0.0",
        "category": "image",
        "engine": "native",
        "modes": ["txt2img"],
    }
    data.update(overrides)
    return data


class TestPresetManifest:
    def test_valid_manifest(self):
        manifest, errors = validate_manifest(valid_manifest_data())
        assert errors == []
        assert manifest is not None
        assert manifest.id == "01K0W24A3RADXXABH16YQ7KE90"
        assert manifest.schema_version == 1
        assert manifest.modes == ["txt2img"]

    def test_missing_required_field(self):
        data = valid_manifest_data()
        del data["category"]
        manifest, errors = validate_manifest(data)
        assert manifest is None
        assert any("category" in e for e in errors)

    def test_invalid_id_format(self):
        manifest, errors = validate_manifest(valid_manifest_data(id="!!bad!!"))
        assert manifest is None
        assert any("id" in e or "does not match" in e for e in errors)

    def test_invalid_version(self):
        manifest, errors = validate_manifest(valid_manifest_data(version="v1"))
        assert manifest is None
        assert any("semver" in e for e in errors)

    def test_invalid_category(self):
        manifest, errors = validate_manifest(valid_manifest_data(category="not_a_category"))
        assert manifest is None
        assert errors

    def test_empty_modes_rejected(self):
        manifest, errors = validate_manifest(valid_manifest_data(modes=[]))
        assert manifest is None
        assert any("modes" in e for e in errors)

    def test_empty_engine_rejected(self):
        manifest, errors = validate_manifest(valid_manifest_data(engine=""))
        assert manifest is None
        assert any("engine" in e for e in errors)

    def test_missing_engine_rejected(self):
        data = valid_manifest_data()
        del data["engine"]
        manifest, errors = validate_manifest(data)
        assert manifest is None
        assert any("engine" in e for e in errors)

    def test_unknown_key_rejected(self):
        manifest, errors = validate_manifest(valid_manifest_data(resolutions=["512x512"]))
        assert manifest is None
        assert errors

    def test_defaults(self):
        manifest, errors = validate_manifest(valid_manifest_data())
        assert errors == []
        assert manifest.tags == []
        assert manifest.vars == {}

    def test_errors_are_collected_not_fail_fast(self):
        """Multiple simultaneous business-rule failures should all be reported
        (not just the first one encountered)."""
        data = valid_manifest_data(id="!!bad!!", version="v1")
        manifest, errors = validate_manifest(data)
        assert manifest is None
        assert any("does not match" in e for e in errors)
        assert any("semver" in e for e in errors)


class TestConfigurationEntry:
    """`configuration:` (admin-set preset knobs, see docs/presets.md "Configuration
    (admin-set)") - the schema only validates the declared shape, not admin-set
    values (those live in src.features.presets.configuration / the presets table)."""

    def test_valid_configuration_entry(self):
        manifest, errors = validate_manifest(valid_manifest_data(configuration={
            "checkpoint_tags": {
                "type": "model_tags",
                "label": "Allowed checkpoint tags",
                "description": "Restricts the checkpoint picker",
            },
        }))
        assert errors == []
        assert manifest is not None
        assert manifest.configuration["checkpoint_tags"].type == "model_tags"
        assert manifest.configuration["checkpoint_tags"].label == "Allowed checkpoint tags"

    def test_configuration_entry_minimal(self):
        manifest, errors = validate_manifest(valid_manifest_data(configuration={
            "checkpoint_tags": {"type": "model_tags"},
        }))
        assert errors == []
        assert manifest.configuration["checkpoint_tags"].label is None

    def test_unknown_configuration_type_is_schema_error(self):
        manifest, errors = validate_manifest(valid_manifest_data(configuration={
            "checkpoint_tags": {"type": "not_a_real_type"},
        }))
        assert manifest is None
        assert any("Unsupported configuration type" in e for e in errors)

    def test_configuration_entry_forbids_extra_keys(self):
        manifest, errors = validate_manifest(valid_manifest_data(configuration={
            "checkpoint_tags": {"type": "model_tags", "bogus": "nope"},
        }))
        assert manifest is None
        assert errors  # extra="forbid"

    def test_no_configuration_block_is_fine(self):
        manifest, errors = validate_manifest(valid_manifest_data())
        assert errors == []
        assert manifest.configuration is None

    def test_configuration_types_contains_model_tags(self):
        assert "model_tags" in CONFIGURATION_TYPES


class TestPresetLLMSpec:
    """`llm:` (chat workspace injection, see docs/presets.md "LLM context") -
    an optional preset/family-level prompting guide plus knobs controlling how
    much of the form schema the chat LLM sees per turn."""

    def test_no_llm_block_is_none_by_default(self):
        manifest, errors = validate_manifest(valid_manifest_data())
        assert errors == []
        assert manifest.llm is None

    def test_minimal_llm_block_defaults_context_form_to_summary(self):
        manifest, errors = validate_manifest(valid_manifest_data(llm={}))
        assert errors == []
        assert manifest is not None
        assert manifest.llm.guide is None
        assert manifest.llm.context.form == "summary"
        assert manifest.llm.context.fields is None
        assert manifest.llm.context.guidance_chars is None

    def test_full_llm_block(self):
        manifest, errors = validate_manifest(valid_manifest_data(llm={
            "guide": "Prefer comma-separated tags over full sentences.",
            "context": {
                "form": "full",
                "fields": ["checkpoint", "loras"],
                "guidance_chars": 800,
            },
        }))
        assert errors == []
        assert manifest.llm.guide == "Prefer comma-separated tags over full sentences."
        assert manifest.llm.context.form == "full"
        assert manifest.llm.context.fields == ["checkpoint", "loras"]
        assert manifest.llm.context.guidance_chars == 800

    def test_context_form_off_is_valid(self):
        manifest, errors = validate_manifest(valid_manifest_data(llm={"context": {"form": "off"}}))
        assert errors == []
        assert manifest.llm.context.form == "off"

    def test_invalid_form_enum_is_collected_not_raised(self):
        manifest, errors = validate_manifest(valid_manifest_data(llm={"context": {"form": "verbose"}}))
        assert manifest is None
        assert any("form" in e for e in errors)

    def test_non_positive_guidance_chars_is_a_schema_error(self):
        manifest, errors = validate_manifest(valid_manifest_data(llm={"context": {"guidance_chars": 0}}))
        assert manifest is None
        assert any("guidance_chars" in e for e in errors)

    def test_llm_block_forbids_extra_keys(self):
        manifest, errors = validate_manifest(valid_manifest_data(llm={"guide": "x", "bogus": "nope"}))
        assert manifest is None
        assert errors

    def test_llm_context_forbids_extra_keys(self):
        manifest, errors = validate_manifest(valid_manifest_data(llm={"context": {"bogus": "nope"}}))
        assert manifest is None
        assert errors

    def test_llm_context_fields_must_be_a_list(self):
        manifest, errors = validate_manifest(valid_manifest_data(llm={"context": {"fields": "checkpoint"}}))
        assert manifest is None
        assert errors

    def test_no_modes_key_is_none_by_default(self):
        manifest, errors = validate_manifest(valid_manifest_data(llm={}))
        assert errors == []
        assert manifest.llm.modes is None

    def test_modes_dict_with_valid_overrides(self):
        manifest, errors = validate_manifest(valid_manifest_data(llm={
            "guide": "Base guide.",
            "modes": {
                "refs": {"guide": "Six-section reference brief format."},
                "video": {"guide": "Three-field video prompt format."},
            },
        }))
        assert errors == []
        assert manifest is not None
        assert manifest.llm.guide == "Base guide."
        assert set(manifest.llm.modes.keys()) == {"refs", "video"}
        assert manifest.llm.modes["refs"].guide == "Six-section reference brief format."
        assert manifest.llm.modes["video"].guide == "Three-field video prompt format."

    def test_mode_spec_forbids_extra_keys(self):
        manifest, errors = validate_manifest(valid_manifest_data(llm={
            "modes": {"refs": {"guide": "x", "bogus": "nope"}},
        }))
        assert manifest is None
        assert errors

    def test_mode_spec_requires_guide(self):
        manifest, errors = validate_manifest(valid_manifest_data(llm={
            "modes": {"refs": {}},
        }))
        assert manifest is None
        assert any("guide" in e for e in errors)

    def test_modes_must_be_a_dict(self):
        manifest, errors = validate_manifest(valid_manifest_data(llm={
            "modes": ["refs"],
        }))
        assert manifest is None
        assert errors

    def test_modes_empty_string_key_is_a_schema_error(self):
        manifest, errors = validate_manifest(valid_manifest_data(llm={
            "modes": {"": {"guide": "x"}},
        }))
        assert manifest is None
        assert any("modes" in e for e in errors)

    def test_modes_not_cross_validated_against_declared_modes(self):
        """A mode key with no matching entry in the preset's own `modes:` list
        (e.g. a plugin-contributed mode) is still valid - see docs/presets.md
        "LLM context"."""
        manifest, errors = validate_manifest(valid_manifest_data(
            modes=["txt2img"],
            llm={"modes": {"a_plugin_mode_not_in_modes_list": {"guide": "x"}}},
        ))
        assert errors == []
        assert manifest is not None
        assert "a_plugin_mode_not_in_modes_list" in manifest.llm.modes


class TestSpeedProfile:
    """`speed_profiles:` (roadmap 3.6) - typed known keys, structurally-permissive
    unknown keys (flagged by the linter, not the schema), type errors ARE
    schema errors."""

    def test_valid_profile_all_known_keys(self):
        manifest, errors = validate_manifest(valid_manifest_data(speed_profiles={
            "draft": {
                "steps": 6, "guidance": 1.0, "shift": 5.0, "sampler": "euler",
                "schedule": "normal", "loras": [{"file": "x.safetensors", "weight": 1.0}],
            },
        }))
        assert errors == []
        assert manifest is not None
        profile = manifest.speed_profiles["draft"]
        assert profile.steps == 6
        assert profile.guidance == 1.0
        assert profile.shift == 5.0
        assert profile.sampler == "euler"
        assert profile.schedule == "normal"
        assert profile.loras == [{"file": "x.safetensors", "weight": 1.0}]

    def test_minimal_profile_only_some_keys(self):
        manifest, errors = validate_manifest(valid_manifest_data(speed_profiles={
            "standard": {"steps": 28, "guidance": 5.0},
        }))
        assert errors == []
        assert manifest.speed_profiles["standard"].steps == 28
        assert manifest.speed_profiles["standard"].sampler is None

    def test_multiple_profiles(self):
        manifest, errors = validate_manifest(valid_manifest_data(speed_profiles={
            "draft": {"steps": 6},
            "standard": {"steps": 28},
            "max": {"steps": 40},
        }))
        assert errors == []
        assert set(manifest.speed_profiles.keys()) == {"draft", "standard", "max"}

    def test_no_speed_profiles_is_none_by_default(self):
        manifest, errors = validate_manifest(valid_manifest_data())
        assert errors == []
        assert manifest.speed_profiles is None

    def test_non_numeric_steps_is_a_schema_error(self):
        manifest, errors = validate_manifest(valid_manifest_data(speed_profiles={
            "draft": {"steps": "fast"},
        }))
        assert manifest is None
        assert any("steps" in e for e in errors)

    def test_non_numeric_guidance_is_a_schema_error(self):
        manifest, errors = validate_manifest(valid_manifest_data(speed_profiles={
            "draft": {"guidance": "high"},
        }))
        assert manifest is None
        assert any("guidance" in e for e in errors)

    def test_loras_wrong_shape_is_a_schema_error(self):
        # loras must be a list of mappings, not a list of bare strings.
        manifest, errors = validate_manifest(valid_manifest_data(speed_profiles={
            "draft": {"loras": ["not_a_mapping.safetensors"]},
        }))
        assert manifest is None
        assert any("loras" in e for e in errors)

    def test_unknown_key_is_allowed_structurally_not_a_schema_error(self):
        """Unlike the manifest's own extra="forbid", a speed_profiles entry with
        a typo'd/forward-looking key must NOT make the whole preset unloadable
        - that's a lint warning (see TestLintSpeedProfiles in test_linter.py)."""
        manifest, errors = validate_manifest(valid_manifest_data(speed_profiles={
            "draft": {"steps": 6, "totally_unknown_key": 123},
        }))
        assert errors == []
        assert manifest is not None
        assert manifest.speed_profiles["draft"].model_extra == {"totally_unknown_key": 123}

    def test_extra_bag_is_a_known_key_not_flagged(self):
        manifest, errors = validate_manifest(valid_manifest_data(speed_profiles={
            "draft": {"steps": 6, "extra": {"forward_compat_knob": True}},
        }))
        assert errors == []
        assert manifest.speed_profiles["draft"].extra == {"forward_compat_knob": True}
        assert manifest.speed_profiles["draft"].model_extra in (None, {})

    def test_known_keys_constant_matches_typed_fields(self):
        assert SPEED_PROFILE_KNOWN_KEYS == {
            "steps", "guidance", "shift", "loras", "sampler", "schedule", "extra",
        }

    def test_speed_profile_direct_construction_extra_allowed(self):
        profile = SpeedProfile(steps=6, some_future_key="value")
        assert profile.steps == 6
        assert profile.model_extra == {"some_future_key": "value"}


class TestPresetMedia:
    """`media:` validates path SHAPE only - existence and mode cross-refs are lint."""

    def test_manifest_without_media_is_valid(self):
        manifest, errors = validate_manifest(valid_manifest_data())
        assert errors == []
        assert manifest.media is None

    def test_valid_media_block(self):
        data = valid_manifest_data(media={
            "cover": "public/cover.png",
            "gallery": [{
                "src": "public/examples/a.webp",
                "caption": "c", "prompt": "p", "seed": 7, "mode": "txt2img",
            }],
        })
        manifest, errors = validate_manifest(data)
        assert errors == []
        assert manifest.media.cover == "public/cover.png"
        assert manifest.media.gallery[0].seed == 7

    def test_cover_only(self):
        manifest, errors = validate_manifest(
            valid_manifest_data(media={"cover": "public/cover.png"})
        )
        assert errors == []
        assert manifest.media.gallery == []

    def test_video_gallery_entry_allowed(self):
        """`category: video` presets have moving examples."""
        manifest, errors = validate_manifest(
            valid_manifest_data(media={"gallery": [{"src": "public/clip.mp4"}]})
        )
        assert errors == []
        assert manifest.media.gallery[0].src == "public/clip.mp4"

    @pytest.mark.parametrize("bad_src", [
        "/etc/passwd",                  # absolute
        "public/../../etc/passwd",      # traversal
        "..",                           # traversal
        "assets/cover.png",             # legacy root, no longer allowed
        "files/carousel/a.png",         # server-side dir
        "cover.png",                    # preset root
        "public\\cover.png",            # backslash
        "public/logo.svg",              # XSS vector
        "public/notes.md",              # not an image
        "public/noext",                 # no extension
    ])
    def test_invalid_media_src_rejected(self, bad_src):
        _, cover_errors = validate_manifest(valid_manifest_data(media={"cover": bad_src}))
        assert cover_errors, f"cover {bad_src!r} should be rejected"

        _, gallery_errors = validate_manifest(
            valid_manifest_data(media={"gallery": [{"src": bad_src}]})
        )
        assert gallery_errors, f"gallery src {bad_src!r} should be rejected"

    def test_unknown_media_key_rejected(self):
        _, errors = validate_manifest(
            valid_manifest_data(media={"cover": "public/a.png", "banner": "public/b.png"})
        )
        assert errors

    def test_unknown_gallery_key_rejected(self):
        _, errors = validate_manifest(
            valid_manifest_data(media={"gallery": [{"src": "public/a.png", "steps": 8}]})
        )
        assert errors

    def test_undeclared_mode_is_not_a_schema_error(self):
        """A renamed mode must not make the preset unloadable; lint warns instead."""
        manifest, errors = validate_manifest(valid_manifest_data(
            modes=["txt2img"],
            media={"gallery": [{"src": "public/a.png", "mode": "img2img"}]},
        ))
        assert errors == []
        assert manifest.media.gallery[0].mode == "img2img"


class TestPresetRequirements:
    """`requires:` (optional hardware guidance, see docs/presets.md "Hardware
    requirements") - schema-only, never touches pipeline rendering."""

    def test_manifest_without_requires_is_valid(self):
        manifest, errors = validate_manifest(valid_manifest_data())
        assert errors == []
        assert manifest.requires is None

    def test_valid_requires_block(self):
        manifest, errors = validate_manifest(valid_manifest_data(requires={
            "min_vram_gb": 12, "recommended_vram_gb": 16, "min_ram_gb": 16,
        }))
        assert errors == []
        assert manifest.requires.min_vram_gb == 12
        assert manifest.requires.recommended_vram_gb == 16
        assert manifest.requires.min_ram_gb == 16

    def test_requires_block_all_keys_optional(self):
        manifest, errors = validate_manifest(valid_manifest_data(requires={"min_vram_gb": 8}))
        assert errors == []
        assert manifest.requires.min_vram_gb == 8
        assert manifest.requires.recommended_vram_gb is None
        assert manifest.requires.min_ram_gb is None

    def test_empty_requires_block_is_valid(self):
        manifest, errors = validate_manifest(valid_manifest_data(requires={}))
        assert errors == []
        assert manifest.requires.min_vram_gb is None

    def test_requires_forbids_unknown_keys(self):
        manifest, errors = validate_manifest(valid_manifest_data(requires={"min_vram_gb": 8, "bogus": 1}))
        assert manifest is None
        assert errors

    def test_requires_non_numeric_min_vram_is_a_schema_error(self):
        manifest, errors = validate_manifest(valid_manifest_data(requires={"min_vram_gb": "a lot"}))
        assert manifest is None
        assert any("min_vram_gb" in e for e in errors)

    def test_requires_zero_min_vram_rejected(self):
        manifest, errors = validate_manifest(valid_manifest_data(requires={"min_vram_gb": 0}))
        assert manifest is None
        assert any("min_vram_gb" in e for e in errors)

    def test_requires_negative_recommended_vram_rejected(self):
        manifest, errors = validate_manifest(valid_manifest_data(requires={"recommended_vram_gb": -4}))
        assert manifest is None
        assert any("recommended_vram_gb" in e for e in errors)

    def test_requires_negative_min_ram_rejected(self):
        manifest, errors = validate_manifest(valid_manifest_data(requires={"min_ram_gb": -1}))
        assert manifest is None
        assert any("min_ram_gb" in e for e in errors)

    def test_requires_direct_construction(self):
        req = PresetRequirements(min_vram_gb=8)
        assert req.min_vram_gb == 8
        assert req.recommended_vram_gb is None


class TestPipelineFile:
    def test_valid_pipeline(self):
        data = {
            "pipeline": [
                {
                    "id": "checkpoint_loader",
                    "name": "checkpoint_loader/sdxl",
                    "enabled": "true",
                    "cache": ["model", "clip"],
                    "configuration": {"model": {"file_path": "{{ get_form('custom', ['model']) }}"}},
                },
                {
                    "name": "prompt_encoder",
                    "input": [["clip", "checkpoint_loader/sdxl", "clip"]],
                },
            ]
        }
        pipeline, errors = validate_pipeline_file(data)
        assert errors == []
        assert len(pipeline.pipeline) == 2
        assert pipeline.pipeline[0].enabled == "true"

    def test_unknown_pipe_key_rejected(self):
        data = {"pipeline": [{"name": "x", "bogus_key": 1}]}
        pipeline, errors = validate_pipeline_file(data)
        assert pipeline is None
        assert errors

    def test_missing_name_rejected(self):
        data = {"pipeline": [{"id": "x"}]}
        pipeline, errors = validate_pipeline_file(data)
        assert pipeline is None
        assert errors

    def test_enabled_defaults_to_true_when_omitted(self):
        """Omitted `enabled:` means enabled - a real bool, not the string
        'true' the runtime used to string-compare against."""
        data = {"pipeline": [{"name": "x"}]}
        pipeline, errors = validate_pipeline_file(data)
        assert errors == []
        assert pipeline.pipeline[0].enabled is True

    def test_enabled_accepts_explicit_bool(self):
        data = {"pipeline": [{"name": "x", "enabled": False}]}
        pipeline, errors = validate_pipeline_file(data)
        assert errors == []
        assert pipeline.pipeline[0].enabled is False


class TestFormFile:
    def test_valid_form(self):
        data = {
            "name": "custom",
            "fields": [
                {"name": "prompt", "type": "text", "default": "hello"},
                {"type": "tabs", "children": "{{ paths.preset }}/tabs.yml"},
            ],
        }
        form, errors = validate_form_file(data)
        assert errors == []
        assert form.fields[1].children == "{{ paths.preset }}/tabs.yml"

    def test_nested_children(self):
        data = {
            "name": "custom",
            "fields": [
                {
                    "type": "tabs",
                    "children": [
                        {"type": "tab", "label": "A", "children": [{"name": "x", "type": "text"}]}
                    ],
                }
            ],
        }
        form, errors = validate_form_file(data)
        assert errors == []
        assert form.fields[0].children[0].children[0].name == "x"

    def test_field_missing_type_rejected(self):
        data = {"name": "custom", "fields": [{"name": "prompt"}]}
        form, errors = validate_form_file(data)
        assert form is None
        assert errors

    def test_field_value_key_rejected(self):
        """`value:` is removed - the ONE initializer key is `default:`."""
        data = {"name": "custom", "fields": [{"name": "prompt", "type": "string", "value": "hello"}]}
        form, errors = validate_form_file(data)
        assert form is None
        assert errors

    def test_jinja_default_rejected(self):
        data = {
            "name": "custom",
            "fields": [{"name": "steps", "type": "slider", "default": "{{ preset.vars.default_steps }}"}],
        }
        form, errors = validate_form_file(data)
        assert form is None
        assert errors

    def test_quoted_boolean_default_rejected_for_checkbox(self):
        data = {"name": "custom", "fields": [{"name": "use_upscale", "type": "checkbox", "default": "false"}]}
        form, errors = validate_form_file(data)
        assert form is None
        assert errors

    def test_native_boolean_default_accepted_for_checkbox(self):
        data = {"name": "custom", "fields": [{"name": "use_upscale", "type": "checkbox", "default": False}]}
        form, errors = validate_form_file(data)
        assert errors == []
        assert form.fields[0].default is False

    def test_quoted_numeric_default_rejected_for_slider(self):
        data = {"name": "custom", "fields": [{"name": "steps", "type": "slider", "default": "20"}]}
        form, errors = validate_form_file(data)
        assert form is None
        assert errors

    def test_native_numeric_default_accepted_for_slider(self):
        data = {"name": "custom", "fields": [{"name": "steps", "type": "slider", "default": 20}]}
        form, errors = validate_form_file(data)
        assert errors == []
        assert form.fields[0].default == 20

    def test_bool_rejected_as_integer_default(self):
        """`isinstance(True, int)` is True in Python - must not slip through
        the integer-type check."""
        data = {"name": "custom", "fields": [{"name": "count", "type": "integer", "default": True}]}
        form, errors = validate_form_file(data)
        assert form is None
        assert errors

    def test_list_rejected_as_select_default(self):
        data = {"name": "custom", "fields": [{"name": "sampler", "type": "select", "default": ["euler"]}]}
        form, errors = validate_form_file(data)
        assert form is None
        assert errors

    def test_string_default_accepted_for_select(self):
        data = {"name": "custom", "fields": [{"name": "sampler", "type": "select", "default": "euler"}]}
        form, errors = validate_form_file(data)
        assert errors == []
        assert form.fields[0].default == "euler"

    def test_absent_default_always_allowed(self):
        data = {"name": "custom", "fields": [{"name": "steps", "type": "slider"}]}
        form, errors = validate_form_file(data)
        assert errors == []
        assert form.fields[0].default is None

    def test_untyped_field_type_keeps_any_default(self):
        """A field type not in the focused typed-default mapping (e.g. a
        media field) keeps `default: Any` - not this schema's concern."""
        data = {"name": "custom", "fields": [{"name": "source_image", "type": "image", "default": {"nested": "ok"}}]}
        form, errors = validate_form_file(data)
        assert errors == []

    def test_field_audience_defaults_simple(self):
        data = {"name": "custom", "fields": [{"name": "prompt", "type": "text"}]}
        form, errors = validate_form_file(data)
        assert errors == []
        assert form.fields[0].audience == "simple"

    def test_field_audience_advanced_accepted(self):
        data = {
            "name": "custom",
            "fields": [{"name": "steps", "type": "slider", "audience": "advanced"}],
        }
        form, errors = validate_form_file(data)
        assert errors == []
        assert form.fields[0].audience == "advanced"

    def test_field_audience_invalid_value_rejected(self):
        data = {
            "name": "custom",
            "fields": [{"name": "steps", "type": "slider", "audience": "expert"}],
        }
        form, errors = validate_form_file(data)
        assert form is None
        assert errors

    def test_field_hidden_when_video_director_defaults_false(self):
        data = {"name": "custom", "fields": [{"name": "duration", "type": "slider"}]}
        form, errors = validate_form_file(data)
        assert errors == []
        assert form.fields[0].hidden_when_video_director is False

    def test_field_hidden_when_video_director_true_accepted(self):
        data = {
            "name": "custom",
            "fields": [{"name": "duration", "type": "slider", "hidden_when_video_director": True}],
        }
        form, errors = validate_form_file(data)
        assert errors == []
        assert form.fields[0].hidden_when_video_director is True

    def test_field_width_absent_allowed(self):
        data = {"name": "custom", "fields": [{"name": "steps", "type": "slider"}]}
        form, errors = validate_form_file(data)
        assert errors == []
        assert form.fields[0].width is None

    def test_field_width_int_accepted(self):
        data = {"name": "custom", "fields": [{"name": "steps", "type": "slider", "width": 2}]}
        form, errors = validate_form_file(data)
        assert errors == []
        assert form.fields[0].width == 2

    def test_field_width_float_accepted(self):
        data = {"name": "custom", "fields": [{"name": "steps", "type": "slider", "width": 1.5}]}
        form, errors = validate_form_file(data)
        assert errors == []
        assert form.fields[0].width == 1.5

    def test_field_width_fraction_string_accepted(self):
        data = {"name": "custom", "fields": [{"name": "steps", "type": "slider", "width": "3/5"}]}
        form, errors = validate_form_file(data)
        assert errors == []
        assert form.fields[0].width == "3/5"

    def test_field_width_zero_rejected(self):
        data = {"name": "custom", "fields": [{"name": "steps", "type": "slider", "width": 0}]}
        form, errors = validate_form_file(data)
        assert form is None
        assert errors

    def test_field_width_negative_rejected(self):
        data = {"name": "custom", "fields": [{"name": "steps", "type": "slider", "width": -1}]}
        form, errors = validate_form_file(data)
        assert form is None
        assert errors

    def test_field_width_non_numeric_string_rejected(self):
        data = {"name": "custom", "fields": [{"name": "steps", "type": "slider", "width": "big"}]}
        form, errors = validate_form_file(data)
        assert form is None
        assert errors

    def test_field_width_fraction_zero_denominator_rejected(self):
        data = {"name": "custom", "fields": [{"name": "steps", "type": "slider", "width": "3/0"}]}
        form, errors = validate_form_file(data)
        assert form is None
        assert errors

    def test_field_width_empty_string_rejected(self):
        data = {"name": "custom", "fields": [{"name": "steps", "type": "slider", "width": ""}]}
        form, errors = validate_form_file(data)
        assert form is None
        assert errors

    def test_field_full_width_absent_defaults_false(self):
        data = {"name": "custom", "fields": [{"name": "quantity", "type": "stepper"}]}
        form, errors = validate_form_file(data)
        assert errors == []
        assert form.fields[0].full_width is False

    def test_field_full_width_true_accepted(self):
        data = {"name": "custom", "fields": [{"name": "quantity", "type": "stepper", "full_width": True}]}
        form, errors = validate_form_file(data)
        assert errors == []
        assert form.fields[0].full_width is True

    def test_field_full_width_false_accepted(self):
        data = {"name": "custom", "fields": [{"name": "quantity", "type": "stepper", "full_width": False}]}
        form, errors = validate_form_file(data)
        assert errors == []
        assert form.fields[0].full_width is False

    def test_field_full_width_non_boolean_rejected(self):
        data = {"name": "custom", "fields": [{"name": "quantity", "type": "stepper", "full_width": "true"}]}
        form, errors = validate_form_file(data)
        assert form is None
        assert errors

    def test_variant_metadata_defaults(self):
        data = {"name": "custom", "fields": []}
        form, errors = validate_form_file(data)
        assert errors == []
        assert form.label is None
        assert form.description is None
        assert form.examples == []
        assert form.default is False
        assert form.order == 0

    def test_variant_metadata_accepted(self):
        data = {
            "name": "custom",
            "label": "Custom",
            "description": "A **custom** variant.",
            "examples": ["public/gallery/a.png"],
            "default": True,
            "order": 5,
            "fields": [],
        }
        form, errors = validate_form_file(data)
        assert errors == []
        assert form.label == "Custom"
        assert form.description == "A **custom** variant."
        assert form.examples == ["public/gallery/a.png"]
        assert form.default is True
        assert form.order == 5

    def test_variant_examples_must_live_under_public(self):
        data = {"name": "custom", "examples": ["assets/a.png"], "fields": []}
        form, errors = validate_form_file(data)
        assert form is None
        assert any("public/" in e for e in errors)

    def test_variant_examples_reject_unsupported_extension(self):
        data = {"name": "custom", "examples": ["public/a.svg"], "fields": []}
        form, errors = validate_form_file(data)
        assert form is None
        assert errors


class TestReactionSpec:
    def test_sugar_operator_form(self):
        reaction, errors = _validate_reaction(
            {"when": {"field": "sampler", "equals": "EULER"}, "then": {"set_visibility": True}}
        )
        assert errors == []
        assert reaction.when.operator == "equals"
        assert reaction.when.value == "EULER"

    def test_explicit_operator_form(self):
        reaction, errors = _validate_reaction(
            {
                "when": {"field": "sampler", "operator": "equals", "value": "EULER"},
                "then": {"set_visibility": True},
            }
        )
        assert errors == []
        assert reaction.when.operator == "equals"

    def test_unknown_operator_rejected(self):
        _, errors = _validate_reaction(
            {"when": {"field": "sampler", "bogus_op": "EULER"}, "then": {"set_visibility": True}}
        )
        assert errors

    def test_condition_without_operator_rejected(self):
        _, errors = _validate_reaction(
            {"when": {"field": "sampler"}, "then": {"set_visibility": True}}
        )
        assert errors

    def test_and_list_of_conditions(self):
        reaction, errors = _validate_reaction(
            {
                "when": [
                    {"field": "a", "equals": 1},
                    {"field": "b", "equals": 2},
                ],
                "then": {"set_value": "x"},
            }
        )
        assert errors == []
        assert len(reaction.when) == 2

    def test_logical_or(self):
        reaction, errors = _validate_reaction(
            {
                "when": {
                    "logic": "OR",
                    "conditions": [{"field": "a", "equals": 1}, {"field": "b", "equals": 2}],
                },
                "then": {"set_disabled": True},
            }
        )
        assert errors == []
        assert reaction.when.logic == "OR"

    def test_then_accepts_set_filter_tags_literal_list(self):
        reaction, errors = _validate_reaction(
            {"when": {"field": "speed_profile", "equals": "fast"}, "then": {"set_filter_tags": ["tag_1"]}}
        )
        assert errors == []
        assert reaction.then.set_filter_tags == ["tag_1"]

    def test_then_accepts_set_filter_tags_config_indirection(self):
        reaction, errors = _validate_reaction(
            {
                "when": {"field": "speed_profile", "equals": "fast"},
                "then": {"set_filter_tags": "@config:checkpoint_tags_fast"},
            }
        )
        assert errors == []
        assert reaction.then.set_filter_tags == "@config:checkpoint_tags_fast"

    def test_then_requires_at_least_one_action(self):
        _, errors = _validate_reaction({"when": {"field": "a", "equals": 1}, "then": {}})
        assert errors

    def test_then_rejects_unknown_action(self):
        _, errors = _validate_reaction(
            {"when": {"field": "a", "equals": 1}, "then": {"bogus_action": True}}
        )
        assert errors


def _validate_reaction(data):
    try:
        return ReactionSpec.model_validate(data), []
    except Exception as e:
        return None, [str(e)]
