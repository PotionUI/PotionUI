"""
Promptless modes: a mode (upscale, slow-motion, ...) that submits no prompt must
still process cleanly. The empty-prompt contract is:

- an empty/absent `prompts` list defaults to `[{'positive': '', 'negative': ''}]`
  (PresetProcessor.process, src/core/preset/processor.py), so
  `generation.prompts.first` is always a well-formed pair;
- the `promptless_modes` var rides through as an ordinary `preset.vars` entry,
  needing no schema change (src/core/preset/schema.py `vars: Dict[str, Any]`).

Mirrors the minimal-fixture style of test_processor_context.py.
"""

from unittest.mock import Mock

from src.features.presets.processor import PresetProcessor
from src.platform.templating.processor import TemplateProcessor
from src.features.presets.templates import FormTemplate, ModeTemplate, PipeTemplate, PresetTemplate


def _preset(pipe_configuration, vars_=None):
    return PresetTemplate(
        id="01RRRRRRRRRRRRRRRRRRRRRRRRR",
        name="Promptless Preset",
        version="1.0.0",
        path="/tmp/does-not-matter",
        modes={
            "upscale": ModeTemplate(
                forms=[FormTemplate(name="custom", fields=[])],
                pipes=[PipeTemplate(name="upscaler", configuration=pipe_configuration)],
            ),
        },
        vars=vars_ if vars_ is not None else {},
        engine="native",
    )


def _processor():
    return PresetProcessor(
        template_processor=TemplateProcessor(settings_manager=Mock()),
        model_manager=Mock(),
        settings_manager=Mock(),
        preset_template_loader=Mock(),
    )


def _generation_data(prompts):
    data = {
        "mode": "upscale",
        "form_data": {"quantity": 1, "seed": 5},
    }
    if prompts is not None:
        data["prompts"] = prompts
    return data


class TestPromptlessProcessing:
    def test_empty_prompts_list_defaults_first_pair_to_empty_strings(self):
        template = _preset(
            {
                "pos": "{{ generation.prompts.first.positive }}",
                "neg": "{{ generation.prompts.first.negative }}",
            }
        )
        pipes = _processor().process(template, _generation_data(prompts=[]))
        cfg = pipes[0]["config"]
        assert cfg["pos"] == ""
        assert cfg["neg"] == ""

    def test_absent_prompts_key_also_defaults_to_empty_pair(self):
        template = _preset(
            {"first": "{{ generation.prompts.first }}"}
        )
        pipes = _processor().process(template, _generation_data(prompts=None))
        assert pipes[0]["config"]["first"] == {"positive": "", "negative": ""}

    def test_pairs_is_a_single_empty_pair(self):
        template = _preset(
            {
                "count": "{{ generation.prompts.pairs | length }}",
                "positives": "{{ generation.prompts.positives }}",
            }
        )
        pipes = _processor().process(template, _generation_data(prompts=[]))
        cfg = pipes[0]["config"]
        assert cfg["count"] == 1
        assert cfg["positives"] == [""]

    def test_promptless_modes_var_is_exposed_to_pipeline(self):
        template = _preset(
            {"modes": "{{ preset.vars.promptless_modes }}"},
            vars_={"promptless_modes": ["upscale", "slowmo"]},
        )
        pipes = _processor().process(template, _generation_data(prompts=[]))
        assert pipes[0]["config"]["modes"] == ["upscale", "slowmo"]
