"""Curation of generation history's display parameters.

`generation_parameters` (written here) is a display-only projection of a
run - `generations.form_data` (untouched by this handler) is what powers
"reuse". Two things this module must guarantee:

- an unrendered `{{ ... }}` template value (a preset templating bug, e.g. a
  literal `@loop` items list whose own elements never got template-processed -
  see `src/features/presets/processor.py`) must never be written as a
  parameter value.
- only the "usual" display parameters (`display_parameters.py`'s allowlist)
  are recorded at all; anything else a preset's `param_emitter` emits is
  dropped.
"""

from unittest.mock import Mock, patch

from src.features.generation.handlers.param_handler import ParamGenerationOutputHandler
from src.pipelines.outputs import ParamGenerationOutput


def make_handler():
    return ParamGenerationOutputHandler(generation_id="gen-1")


@patch("src.features.generation.parameter_repository.generation_parameter_repo")
def test_unrendered_template_value_is_not_recorded(mock_repo):
    handler = make_handler()
    output = ParamGenerationOutput(
        name="enhance_detail",
        values=["{{ form.enhance_detail | default('balanced') }}"],
    )

    metadata = handler.handle(output)

    mock_repo.create_batch.assert_not_called()
    assert metadata["saved_count"] == 0
    assert metadata["parameter_ids"] == []


@patch("src.features.generation.parameter_repository.generation_parameter_repo")
def test_rendered_value_for_a_display_parameter_is_recorded(mock_repo):
    mock_repo.create_batch.return_value = [Mock(id="p1")]
    handler = make_handler()
    output = ParamGenerationOutput(name="enhance_detail", values=["balanced"])

    metadata = handler.handle(output)

    mock_repo.create_batch.assert_called_once_with("gen-1", "enhance_detail", ["balanced"])
    assert metadata["saved_count"] == 1
    assert metadata["parameter_ids"] == ["p1"]


@patch("src.features.generation.parameter_repository.generation_parameter_repo")
def test_non_display_parameter_name_is_not_recorded(mock_repo):
    handler = make_handler()
    output = ParamGenerationOutput(name="matte_feather", values=[4])

    metadata = handler.handle(output)

    mock_repo.create_batch.assert_not_called()
    assert metadata["saved_count"] == 0


@patch("src.features.generation.parameter_repository.generation_parameter_repo")
def test_usual_display_parameters_are_recorded(mock_repo):
    mock_repo.create_batch.return_value = [Mock(id="p1")]
    handler = make_handler()

    for name in ("seed", "cfg", "steps", "sampler", "resolution", "positive_prompt", "negative_prompt", "model"):
        mock_repo.reset_mock()
        output = ParamGenerationOutput(name=name, values=["value"])

        handler.handle(output)

        mock_repo.create_batch.assert_called_once_with("gen-1", name, ["value"])
