"""
End-to-end coverage for `speed_profiles:` (roadmap 3.6): schema -> loader ->
PresetProcessor Jinja context -> `get_speed_profile()` lookup / `generation.profile`
resolution, using an in-memory PresetTemplate (no real preset files on disk needed).

`preset.speed_profiles` direct dot access is gone from the render context -
`get_speed_profile(name)` (an explicit lookup) and `generation.profile` (the
profile resolved for this request) are the only ways to reach a preset's
declared profiles now; see TestGenerationProfileResolution below and
docs/presets.md "Speed profiles".

Complements:
- tests/core/preset/test_schema.py::TestSpeedProfile (schema validation)
- tests/core/preset/test_linter.py::TestLintSpeedProfiles (lint rules)
- tests/core/template/test_dict_utils.py::TestGetSpeedProfileValue (the lookup helper)
- tests/core/test_template.py (TemplateProcessor.get_speed_profile + Jinja global)
"""

from unittest.mock import Mock

import pytest

from src.features.presets.loader import PresetTemplateLoader
from src.features.presets.processor import PresetProcessor
from src.platform.templating.errors import TemplateEvaluationError
from src.platform.templating.processor import TemplateProcessor
from src.features.presets.templates import FormTemplate, ModeTemplate, PipeTemplate, PresetTemplate


def _preset(speed_profiles, pipe_configuration):
    return PresetTemplate(
        id="01NNNNNNNNNNNNNNNNNNNNNNNNN",
        name="Test Preset",
        version="1.0.0",
        path="/tmp/does-not-matter",
        modes={
            "txt2img": ModeTemplate(
                forms=[FormTemplate(name="custom", fields=[])],
                pipes=[PipeTemplate(name="generator", configuration=pipe_configuration)],
            ),
        },
        vars={},
        speed_profiles=speed_profiles,
        engine="native",
    )


def _generation_data(**form_overrides):
    return {
        "mode": "txt2img",
        "form_data": {"quantity": 1, "seed": -1, **form_overrides},
    }


def _processor():
    return PresetProcessor(
        template_processor=TemplateProcessor(settings=Mock()),
        model_directories=Mock(),
        settings=Mock(),
        preset_template_loader=Mock(),
    )


class TestSpeedProfilesInProcessorContext:
    def test_preset_speed_profiles_direct_access_is_gone(self):
        """Direct dot access on `preset.speed_profiles` is removed from the
        render context - use `get_speed_profile()` or `generation.profile`
        (TestGenerationProfileResolution below) instead."""
        template = _preset(
            speed_profiles={"draft": {"steps": 6, "guidance": 1.0}},
            pipe_configuration={"steps": "{{ preset.speed_profiles.draft.steps }}"},
        )
        with pytest.raises(TemplateEvaluationError):
            _processor().process(template, _generation_data())

    def test_get_speed_profile_global_resolves_selected_profile(self):
        template = _preset(
            speed_profiles={"draft": {"steps": 6}, "standard": {"steps": 28}},
            pipe_configuration={
                "steps": "{{ get_speed_profile(form.speed_profile | default('standard'))['steps'] }}",
            },
        )
        pipes = _processor().process(
            template, _generation_data(speed_profile="draft")
        )
        # Exact-expression path -> native int, not a rendered string.
        assert pipes[0]["config"]["steps"] == 6

    def test_get_speed_profile_falls_back_to_default_form_value(self):
        template = _preset(
            speed_profiles={"draft": {"steps": 6}, "standard": {"steps": 28}},
            pipe_configuration={
                "steps": "{{ get_speed_profile(form.speed_profile | default('standard'))['steps'] }}",
            },
        )
        # No 'speed_profile' key submitted -> the `| default('standard')` filter applies.
        pipes = _processor().process(template, _generation_data())
        assert pipes[0]["config"]["steps"] == 28

    def test_missing_profile_name_raises_loudly(self):
        """get_speed_profile raises ValueError for a genuinely missing profile
        with no explicit default; the templating rework no longer swallows
        this into a rendered None - it propagates as a TemplateEvaluationError
        that fails the pipeline build (see error enrichment tests)."""
        template = _preset(
            speed_profiles={"draft": {"steps": 6}},
            pipe_configuration={
                "steps": "{{ get_speed_profile(form.speed_profile | default('nonexistent'))['steps'] }}",
            },
        )
        with pytest.raises(TemplateEvaluationError) as exc_info:
            _processor().process(template, _generation_data())
        assert "nonexistent" in str(exc_info.value)

    def test_no_speed_profiles_declared_generation_profile_is_empty_dict(self):
        """`generation.profile` must always be a dict in context (never None
        and never absent) so `{{ generation.profile | length }}`-style
        templates don't need a None-guard for presets that don't use the
        feature at all - a `generation.profile.<key>` reference still fails
        loudly (StrictUndefined on an empty dict), it just doesn't need a
        separate "is this even a dict" guard first."""
        template = _preset(
            speed_profiles=None,
            pipe_configuration={"count": "{{ generation.profile | length }}"},
        )
        pipes = _processor().process(template, _generation_data())
        # Exact-expression path -> native int, not the string "0".
        assert pipes[0]["config"]["count"] == 0


class TestGenerationProfileResolution:
    """`generation.profile` (PresetProcessor._resolve_generation_profile) -
    the single speed-profile mapping config values source their baseline
    from, replacing direct `preset.speed_profiles.<name>` dot access."""

    def test_declared_profiles_form_value_names_one_resolves_it(self):
        template = _preset(
            speed_profiles={"draft": {"steps": 6}, "standard": {"steps": 28}},
            pipe_configuration={"steps": "{{ generation.profile.steps }}"},
        )
        pipes = _processor().process(template, _generation_data(speed_profile="draft"))
        assert pipes[0]["config"]["steps"] == 6

    def test_declared_profiles_no_form_value_defaults_to_first_declared(self):
        """No `speed_profile` field/value submitted - falls back to the
        FIRST declared profile (`speed_profiles:` preserves YAML declaration
        order end to end; SpeedProfile has no default: marker to prefer)."""
        template = _preset(
            speed_profiles={"draft": {"steps": 6}, "standard": {"steps": 28}},
            pipe_configuration={"steps": "{{ generation.profile.steps }}"},
        )
        pipes = _processor().process(template, _generation_data())
        assert pipes[0]["config"]["steps"] == 6

    def test_declared_profiles_form_value_names_none_of_them_defaults_to_first_declared(self):
        """A `speed_profile` value that doesn't name any declared profile
        falls back the same way an absent one does, rather than raising -
        `get_speed_profile()` is the idiom for a hard failure on a bad name."""
        template = _preset(
            speed_profiles={"draft": {"steps": 6}, "standard": {"steps": 28}},
            pipe_configuration={"steps": "{{ generation.profile.steps }}"},
        )
        pipes = _processor().process(template, _generation_data(speed_profile="nonexistent"))
        assert pipes[0]["config"]["steps"] == 6

    def test_no_profiles_declared_reference_fails_loudly(self):
        template = _preset(
            speed_profiles=None,
            pipe_configuration={"steps": "{{ generation.profile.steps }}"},
        )
        with pytest.raises(TemplateEvaluationError):
            _processor().process(template, _generation_data())


class TestSpeedProfilesLoaderWiring:
    def test_loader_converts_pydantic_profiles_to_plain_dicts(self, tmp_path):
        preset_dir = tmp_path / "presets/native/Foo/std"
        preset_dir.mkdir(parents=True)
        (preset_dir / "preset.yml").write_text(
            """schema: 1
id: "01OOOOOOOOOOOOOOOOOOOOOOOOO"
name: "Test"
version: "1.0.0"
category: "image"
engine: "native"
speed_profiles:
  draft:
    steps: 6
    guidance: 1.0
modes:
  - txt2img
"""
        )
        mode_dir = preset_dir / "modes" / "txt2img"
        mode_dir.mkdir(parents=True)
        (mode_dir / "pipeline.yml").write_text("pipeline: []\n")

        loader = PresetTemplateLoader([str(tmp_path)])
        loader.load_presets()
        assert loader.load_errors == {}
        preset = loader.presets[0]

        assert preset.speed_profiles == {"draft": {"steps": 6, "guidance": 1.0}}
        # Plain dict, not a SpeedProfile pydantic instance - matches `vars`' shape.
        assert isinstance(preset.speed_profiles["draft"], dict)

    def test_loader_leaves_speed_profiles_none_when_undeclared(self, tmp_path):
        preset_dir = tmp_path / "presets/native/Bar/std"
        preset_dir.mkdir(parents=True)
        (preset_dir / "preset.yml").write_text(
            """schema: 1
id: "01PPPPPPPPPPPPPPPPPPPPPPPPP"
name: "Test"
version: "1.0.0"
category: "image"
engine: "native"
modes:
  - txt2img
"""
        )
        mode_dir = preset_dir / "modes" / "txt2img"
        mode_dir.mkdir(parents=True)
        (mode_dir / "pipeline.yml").write_text("pipeline: []\n")

        loader = PresetTemplateLoader([str(tmp_path)])
        loader.load_presets()
        assert loader.presets[0].speed_profiles is None
