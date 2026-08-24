"""
End-to-end coverage for the `form_name` variable (roadmap "preset variants"):
GenerationRequest.form_name -> PipelineBuilder.build_pipeline -> PresetProcessor
Jinja context, using an in-memory PresetTemplate (no real preset files on disk
needed). Mirrors tests/core/preset/test_speed_profiles.py's shape.

Complements:
- tests/core/preset/test_schema.py::TestFormFile (variant metadata schema)
- tests/core/preset_manager/test_manager.py (get_available_modes variants shape)
- tests/api/dto/test_generation_dto.py (GenerationRequest.form_name)
"""

from unittest.mock import Mock

from src.features.presets.processor import PresetProcessor
from src.features.generation.pipeline_builder import PipelineBuilder
from src.platform.templating.processor import TemplateProcessor
from src.features.presets.templates import FormTemplate, ModeTemplate, PipeTemplate, PresetTemplate


def _preset(forms, pipe_configuration):
    return PresetTemplate(
        id="01QQQQQQQQQQQQQQQQQQQQQQQQQ",
        name="Test Preset",
        version="1.0.0",
        path="/tmp/does-not-matter",
        modes={
            "txt2img": ModeTemplate(
                forms=forms,
                pipes=[PipeTemplate(name="generator", configuration=pipe_configuration)],
            ),
        },
        vars={},
        engine="native",
    )


def _generation_data(**overrides):
    data = {
        "mode": "txt2img",
        "form_data": {"quantity": 1, "seed": -1},
    }
    data.update(overrides)
    return data


def _processor():
    return PresetProcessor(
        template_processor=TemplateProcessor(settings_manager=Mock()),
        model_manager=Mock(),
        settings_manager=Mock(),
        preset_template_loader=Mock(),
    )


class TestFormNameInProcessorContext:
    def test_explicit_form_name_is_exposed_on_request(self):
        template = _preset(
            forms=[
                FormTemplate(name="custom", fields=[]),
                FormTemplate(name="advanced", fields=[]),
            ],
            pipe_configuration={"variant": "{{ request.form_name }}"},
        )
        pipes = _processor().process(template, _generation_data(form_name="advanced"))
        assert pipes[0]["config"]["variant"] == "advanced"

    def test_missing_form_name_falls_back_to_the_first_form_after_sorting(self):
        template = _preset(
            forms=[
                FormTemplate(name="zeta", fields=[]),
                FormTemplate(name="alpha", fields=[]),
            ],
            pipe_configuration={"variant": "{{ request.form_name }}"},
        )
        pipes = _processor().process(template, _generation_data())
        assert pipes[0]["config"]["variant"] == "alpha"

    def test_missing_form_name_falls_back_to_the_explicit_default_flag(self):
        template = _preset(
            forms=[
                FormTemplate(name="alpha", fields=[]),
                FormTemplate(name="beta", fields=[], default=True),
            ],
            pipe_configuration={"variant": "{{ request.form_name }}"},
        )
        pipes = _processor().process(template, _generation_data())
        assert pipes[0]["config"]["variant"] == "beta"

    def test_no_forms_at_all_renders_none_not_a_crash(self):
        template = _preset(
            forms=[],
            pipe_configuration={"variant": "{{ request.form_name }}"},
        )
        pipes = _processor().process(template, _generation_data())
        # Exact-expression path -> native None, not the string "None".
        assert pipes[0]["config"]["variant"] is None


class TestFormNameThreadedThroughPipelineBuilder:
    def test_build_pipeline_threads_form_name_into_the_processor_context(self):
        template = _preset(
            forms=[
                FormTemplate(name="custom", fields=[]),
                FormTemplate(name="advanced", fields=[]),
            ],
            pipe_configuration={"variant": "{{ request.form_name }}"},
        )
        loader = Mock()
        loader.load_preset_by_id.return_value = template
        builder = PipelineBuilder(
            preset_template_loader=loader,
            preset_processor=_processor(),
        )

        built = builder.build_pipeline(
            preset_id=template,
            form_data={},
            mode="txt2img",
            form_name="advanced",
        )

        assert built.pipes[0]["config"]["variant"] == "advanced"

    def test_build_pipeline_defaults_form_name_when_not_given(self):
        template = _preset(
            forms=[
                FormTemplate(name="zeta", fields=[]),
                FormTemplate(name="alpha", fields=[]),
            ],
            pipe_configuration={"variant": "{{ request.form_name }}"},
        )
        builder = PipelineBuilder(
            preset_template_loader=Mock(),
            preset_processor=_processor(),
        )

        built = builder.build_pipeline(preset_id=template, form_data={}, mode="txt2img")

        assert built.pipes[0]["config"]["variant"] == "alpha"
