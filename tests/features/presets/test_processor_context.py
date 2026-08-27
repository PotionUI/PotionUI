"""
Coverage for the templating rework's new pipeline context and the parts of
PresetProcessor.process() it touches: the context shape itself,
native `enabled:` handling, native `@loop` items, `@object:`/`@dict:` being gone, and
TemplateEvaluationError location enrichment.

Complements:
- tests/core/test_template.py (the evaluator itself: exact-expression typing, strict
  errors, sandbox denial - owned by W1)
- tests/core/preset/test_processor_form_name.py, test_speed_profiles.py (other facets
  of the same new context)
"""

from unittest.mock import Mock

import pytest

from src.features.presets.processor import PresetProcessor
from src.platform.templating.errors import TemplateEvaluationError
from src.platform.templating.processor import TemplateProcessor
from src.features.presets.templates import FormTemplate, ModeTemplate, PipeTemplate, PresetTemplate


def _preset(pipe_configuration=None, pipes=None, pipe_enabled=None):
    if pipes is None:
        kwargs = {"name": "generator", "configuration": pipe_configuration}
        if pipe_enabled is not None:
            kwargs["enabled"] = pipe_enabled
        pipes = [PipeTemplate(**kwargs)]
    return PresetTemplate(
        id="01RRRRRRRRRRRRRRRRRRRRRRRRR",
        name="Test Preset",
        version="1.0.0",
        path="/tmp/does-not-matter",
        modes={
            "txt2img": ModeTemplate(
                forms=[FormTemplate(name="custom", fields=[])],
                pipes=pipes,
            ),
        },
        vars={"a_var": "value"},
        engine="native",
    )


def _generation_data(**form_overrides):
    return {
        "mode": "txt2img",
        "form_data": {"quantity": 1, "seed": 5, **form_overrides},
    }


def _processor():
    return PresetProcessor(
        template_processor=TemplateProcessor(settings=Mock()),
        model_directories=Mock(),
        settings=Mock(),
        preset_template_loader=Mock(),
    )


class TestContextShape:
    def test_context_top_level_keys_present(self):
        """form/request/generation/preset/runtime/paths reach every pipe config
        template; input.* is gone entirely (KeyError/UndefinedError, not a
        silently-empty dict)."""
        template = _preset(
            pipe_configuration={
                "form_ok": "{{ form is defined }}",
                "request_ok": "{{ request.mode }}",
                "generation_ok": "{{ generation.prompts.pairs | length }}",
                "preset_ok": "{{ preset.name }}",
                "runtime_ok": "{{ runtime.settings is defined }}",
                "paths_ok": "{{ paths.preset }}",
            },
        )
        pipes = _processor().process(template, _generation_data())
        cfg = pipes[0]["config"]
        assert cfg["form_ok"] is True
        assert cfg["request_ok"] == "txt2img"
        assert cfg["generation_ok"] == 1
        assert cfg["preset_ok"] == "Test Preset"
        assert cfg["runtime_ok"] is True
        assert cfg["paths_ok"] == "/tmp/does-not-matter"

    def test_input_namespace_is_gone(self):
        template = _preset(pipe_configuration={"x": "{{ input.form.quantity }}"})
        with pytest.raises(TemplateEvaluationError):
            _processor().process(template, _generation_data())

    def test_form_is_the_bound_form_values_dict_verbatim(self):
        template = _preset(pipe_configuration={"steps": "{{ form.steps }}"})
        pipes = _processor().process(template, _generation_data(steps=28))
        assert pipes[0]["config"]["steps"] == 28
        assert type(pipes[0]["config"]["steps"]) is int

    def test_generation_prompts_shape(self):
        template = _preset(
            pipe_configuration={
                "first": "{{ generation.prompts.first }}",
                "pairs": "{{ generation.prompts.pairs }}",
                "positives": "{{ generation.prompts.positives }}",
                "negatives": "{{ generation.prompts.negatives }}",
            }
        )
        generation_data = _generation_data()
        generation_data["prompts"] = [
            {"positive": "a cat", "negative": "blurry"},
            {"positive": "a dog", "negative": "ugly"},
        ]
        pipes = _processor().process(template, generation_data)
        cfg = pipes[0]["config"]
        assert cfg["first"] == {"positive": "a cat", "negative": "blurry"}
        assert cfg["pairs"] == generation_data["prompts"]
        assert cfg["positives"] == ["a cat", "a dog"]
        assert cfg["negatives"] == ["blurry", "ugly"]

    def test_runtime_settings_resolved_once_via_settings(self):
        settings = Mock()
        settings.get_file_storage_directory.return_value = "/data/storage"
        settings.is_nsfw_enabled.return_value = True
        processor = PresetProcessor(
            template_processor=TemplateProcessor(settings=Mock()),
            model_directories=Mock(),
            settings=settings,
            preset_template_loader=Mock(),
        )
        template = _preset(
            pipe_configuration={
                "dir": "{{ runtime.settings.file_storage_directory }}",
                "nsfw": "{{ runtime.settings.nsfw }}",
            }
        )
        pipes = processor.process(template, _generation_data(), user_id="user-1")
        assert pipes[0]["config"]["dir"] == "/data/storage"
        assert pipes[0]["config"]["nsfw"] is True
        settings.get_file_storage_directory.assert_called_once_with("user-1")
        settings.is_nsfw_enabled.assert_called_once_with("user-1")


class TestObjectAndDictDirectivesAreGone:
    def test_object_prefixed_string_is_no_longer_special_cased(self):
        """`@object:` used to short-circuit into a raw context path lookup;
        now it's just an ordinary string with no `{{ }}` markers, passed
        through unchanged rather than resolved."""
        template = _preset(pipe_configuration={"pairs": "@object:generation.prompts.pairs"})
        pipes = _processor().process(template, _generation_data())
        assert pipes[0]["config"]["pairs"] == "@object:generation.prompts.pairs"

    def test_dict_prefixed_string_is_no_longer_special_cased(self):
        template = _preset(pipe_configuration={"x": "@dict:preset.vars.a_var"})
        pipes = _processor().process(template, _generation_data())
        assert pipes[0]["config"]["x"] == "@dict:preset.vars.a_var"


class TestNativeEnabledHandling:
    def test_bool_true_passes_through(self):
        template = _preset(pipe_configuration={}, pipe_enabled=True)
        pipes = _processor().process(template, _generation_data())
        assert pipes[0]["enabled"] is True

    def test_bool_false_passes_through(self):
        template = _preset(pipe_configuration={}, pipe_enabled=False)
        pipes = _processor().process(template, _generation_data())
        assert pipes[0]["enabled"] is False

    def test_omitted_enabled_defaults_to_true(self):
        template = _preset(pipe_configuration={})
        pipes = _processor().process(template, _generation_data())
        assert pipes[0]["enabled"] is True

    def test_exact_expression_string_evaluates_to_native_bool(self):
        template = _preset(pipe_configuration={}, pipe_enabled="{{ form.steps > 10 }}")
        pipes = _processor().process(template, _generation_data(steps=20))
        assert pipes[0]["enabled"] is True
        pipes = _processor().process(template, _generation_data(steps=5))
        assert pipes[0]["enabled"] is False

    def test_legacy_string_true_literal_now_raises(self):
        """`enabled: "true"` (a bare quoted string, not a `{{ }}` expression)
        is no longer special-cased - it must be migrated to a native
        `enabled: true` or an exact-expression string."""
        template = _preset(pipe_configuration={}, pipe_enabled="true")
        with pytest.raises(TemplateEvaluationError):
            _processor().process(template, _generation_data())

    def test_templated_if_else_string_bool_now_raises(self):
        """The old `{% if %}true{% else %}false{% endif %}` idiom renders a
        *string* template, not an exact expression - no longer accepted."""
        template = _preset(
            pipe_configuration={},
            pipe_enabled="{% if form.steps > 10 %}true{% else %}false{% endif %}",
        )
        with pytest.raises(TemplateEvaluationError):
            _processor().process(template, _generation_data(steps=20))

    def test_non_bool_expression_result_raises(self):
        template = _preset(pipe_configuration={}, pipe_enabled="{{ form.steps }}")
        with pytest.raises(TemplateEvaluationError):
            _processor().process(template, _generation_data(steps=20))


class TestLoopNativeItems:
    def test_items_native_list_expression(self):
        template = _preset(
            pipe_configuration={
                "@loop": {
                    "items": "{{ form.loras }}",
                    "template": {"name": "{{ item }}", "index": "{{ loop.index }}"},
                }
            }
        )
        pipes = _processor().process(template, _generation_data(loras=["a", "b", "c"]))
        assert pipes[0]["config"] == [
            {"name": "a", "index": 1},
            {"name": "b", "index": 2},
            {"name": "c", "index": 3},
        ]

    def test_items_native_dict_expression_unpacks_key_value(self):
        template = _preset(
            pipe_configuration={
                "@loop": {
                    "items": "{{ form.mapping }}",
                    "as": "key,value",
                    "template": {"k": "{{ key }}", "v": "{{ value }}"},
                }
            }
        )
        pipes = _processor().process(template, _generation_data(mapping={"x": 1, "y": 2}))
        assert pipes[0]["config"] == [{"k": "x", "v": 1}, {"k": "y", "v": 2}]

    def test_items_resolving_to_a_non_iterable_raises(self):
        template = _preset(
            pipe_configuration={
                "@loop": {"items": "{{ form.not_a_list }}", "template": {"x": 1}}
            }
        )
        with pytest.raises(TemplateEvaluationError):
            _processor().process(template, _generation_data(not_a_list=42))

    def test_items_over_the_cap_raises(self):
        template = _preset(
            pipe_configuration={
                "@loop": {"items": "{{ range(0, 10001) | list }}", "template": {"x": "{{ item }}"}}
            }
        )
        with pytest.raises(TemplateEvaluationError):
            _processor().process(template, _generation_data())

    def test_count_over_the_cap_raises(self):
        template = _preset(
            pipe_configuration={"@loop": {"count": 10001, "template": {"x": "{{ loop.index }}"}}}
        )
        with pytest.raises(TemplateEvaluationError):
            _processor().process(template, _generation_data())

    def test_mixed_text_items_no_longer_round_tripped_through_literal_eval(self):
        """The old code rendered `items:` as a string template then ran
        `ast.literal_eval` on it; that round-trip is gone - `items:` must
        itself be an exact `{{ expression }}`."""
        template = _preset(
            pipe_configuration={
                "@loop": {"items": "prefix {{ form.loras }}", "template": {"x": 1}}
            }
        )
        with pytest.raises(TemplateEvaluationError):
            _processor().process(template, _generation_data(loras=[1, 2]))


class TestErrorEnrichment:
    def test_missing_variable_error_carries_pipe_id_and_config_path(self):
        template = _preset(
            pipes=[
                PipeTemplate(
                    id="generator-1",
                    name="generator",
                    configuration={"nested": {"steps": "{{ form.missing_field }}"}},
                )
            ]
        )
        with pytest.raises(TemplateEvaluationError) as exc_info:
            _processor().process(template, _generation_data())
        err = exc_info.value
        assert err.pipe_id == "generator-1"
        assert err.preset_id == template.id
        assert err.mode == "txt2img"
        assert err.config_path == "config.nested.steps"
        assert err.source_file is not None
        assert "modes/txt2img/pipeline.yml" in err.source_file

    def test_enabled_error_carries_config_path_enabled(self):
        template = _preset(
            pipes=[
                PipeTemplate(id="generator-1", name="generator", configuration={}, enabled="true"),
            ]
        )
        with pytest.raises(TemplateEvaluationError) as exc_info:
            _processor().process(template, _generation_data())
        assert exc_info.value.config_path == "enabled"
        assert exc_info.value.pipe_id == "generator-1"

    def test_list_index_is_part_of_the_config_path(self):
        template = _preset(
            pipe_configuration={"items": [{"a": "{{ form.ok }}"}, {"b": "{{ form.missing }}"}]}
        )
        with pytest.raises(TemplateEvaluationError) as exc_info:
            _processor().process(template, _generation_data(ok=1))
        assert exc_info.value.config_path == "config.items.1.b"


class TestDisabledPipesSkipConfigRendering:
    """A disabled pipe's config is never rendered: under StrictUndefined,
    rendering configs of pipes that will not run turns optional features
    (e.g. ControlNet slots referenced only when enable_controlnet is on)
    into spurious build failures."""

    def test_disabled_pipe_with_strict_failing_config_builds_fine(self):
        template = _preset(
            pipes=[
                PipeTemplate(
                    id="controlnet-1",
                    name="controlnet_loader",
                    enabled=False,
                    configuration={"file_path": "{{ form.controlnet_1_model }}"},
                )
            ]
        )
        pipes = _processor().process(template, _generation_data())
        assert pipes[0]["enabled"] is False
        assert pipes[0]["config"] == {}

    def test_expression_disabled_pipe_also_skips_config(self):
        template = _preset(
            pipes=[
                PipeTemplate(
                    id="controlnet-1",
                    name="controlnet_loader",
                    enabled="{{ form.enable_controlnet | default(false) }}",
                    configuration={"file_path": "{{ form.controlnet_1_model }}"},
                )
            ]
        )
        pipes = _processor().process(template, _generation_data())
        assert pipes[0]["enabled"] is False
        assert pipes[0]["config"] == {}

    def test_enabled_pipe_still_renders_and_fails_loudly(self):
        template = _preset(
            pipes=[
                PipeTemplate(
                    id="controlnet-1",
                    name="controlnet_loader",
                    enabled=True,
                    configuration={"file_path": "{{ form.controlnet_1_model }}"},
                )
            ]
        )
        with pytest.raises(TemplateEvaluationError):
            _processor().process(template, _generation_data())
