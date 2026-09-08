"""The value recorded for a display parameter is never a depot/backend path.

`_resolve_model` (tested in test_param_handler_model_resolution.py) already maps an
emitted `model` value to a catalog row for the `generation_models` association. This
covers the other half: what `handle()` writes to `generation_parameters` for a person
to read back in generation history must be a name, not the path or ref a preset's
`param_emitter` happened to emit it as.
"""

from unittest.mock import Mock, patch

from src.features.generation.handlers.param_handler import (
    ParamGenerationOutputHandler,
    _reduce_leaked_model_path,
)
from src.pipelines.outputs import ParamGenerationOutput


def make_handler():
    return ParamGenerationOutputHandler(generation_id="gen-1")


def make_model(display_name, model_id="m1"):
    model = Mock()
    model.id = model_id
    model.display_name = display_name
    return model


@patch("src.features.generation.model_repository.generation_model_repo")
@patch("src.features.models.repository.model_repo")
@patch("src.features.generation.parameter_repository.generation_parameter_repo")
def test_resolved_depot_path_records_the_catalog_display_name(
    mock_param_repo, mock_model_repo, mock_gen_model_repo
):
    mock_param_repo.create_batch.return_value = [Mock(id="p1")]
    model = make_model("Qwen Image Lightning 8-steps V2.0")
    mock_model_repo.get_by_file_path.return_value = model

    handler = make_handler()
    output = ParamGenerationOutput(
        name="model",
        values=["QWEN/Qwen-Image-Lightning-8steps-V2.0.safetensors"],
    )

    handler.handle(output)

    mock_param_repo.create_batch.assert_called_once_with(
        "gen-1", "model", ["Qwen Image Lightning 8-steps V2.0"]
    )


@patch("src.features.generation.model_repository.generation_model_repo")
@patch("src.features.models.repository.model_repo")
@patch("src.features.generation.parameter_repository.generation_parameter_repo")
def test_unresolvable_path_records_the_basename(
    mock_param_repo, mock_model_repo, mock_gen_model_repo
):
    mock_param_repo.create_batch.return_value = [Mock(id="p1")]
    mock_model_repo.get_by_file_path.return_value = None
    mock_model_repo.get_by_filename.return_value = []

    handler = make_handler()
    output = ParamGenerationOutput(
        name="model",
        values=["models/diffusion_models/qwen_image_2512_fp8_e4m3fn.safetensors"],
    )

    handler.handle(output)

    mock_param_repo.create_batch.assert_called_once_with(
        "gen-1", "model", ["qwen_image_2512_fp8_e4m3fn.safetensors"]
    )


@patch("src.features.generation.model_repository.generation_model_repo")
@patch("src.features.models.repository.model_repo")
@patch("src.features.generation.parameter_repository.generation_parameter_repo")
def test_bare_filename_is_unchanged_when_unresolved(
    mock_param_repo, mock_model_repo, mock_gen_model_repo
):
    mock_param_repo.create_batch.return_value = [Mock(id="p1")]
    mock_model_repo.get_by_file_path.return_value = None
    mock_model_repo.get_by_filename.return_value = []

    handler = make_handler()
    output = ParamGenerationOutput(name="model", values=["detail.safetensors"])

    handler.handle(output)

    mock_param_repo.create_batch.assert_called_once_with(
        "gen-1", "model", ["detail.safetensors"]
    )


@patch("src.features.generation.parameter_repository.generation_parameter_repo")
def test_non_model_parameter_with_a_leaked_model_path_is_reduced_to_its_basename(
    mock_param_repo,
):
    """A preset can attach a LoRA/upscaler ref to a display name other than
    `model` (e.g. `upscale_by`); that leaks the same way and must be caught
    even though no catalog lookup runs for it."""
    mock_param_repo.create_batch.return_value = [Mock(id="p1")]

    handler = make_handler()
    output = ParamGenerationOutput(
        name="upscale_by", values=["models/upscalers/4x_esrgan.safetensors"]
    )

    handler.handle(output)

    mock_param_repo.create_batch.assert_called_once_with(
        "gen-1", "upscale_by", ["4x_esrgan.safetensors"]
    )


@patch("src.features.generation.parameter_repository.generation_parameter_repo")
def test_non_model_looking_value_under_another_name_is_untouched(mock_param_repo):
    """A value that merely contains a slash but isn't model-shaped (no
    recognised model extension) is left exactly as emitted."""
    mock_param_repo.create_batch.return_value = [Mock(id="p1")]

    handler = make_handler()
    output = ParamGenerationOutput(name="resolution", values=["1024x1024"])

    handler.handle(output)

    mock_param_repo.create_batch.assert_called_once_with(
        "gen-1", "resolution", ["1024x1024"]
    )


@patch("src.features.generation.model_repository.generation_model_repo")
@patch("src.features.models.repository.model_repo")
@patch("src.features.generation.parameter_repository.generation_parameter_repo")
def test_index_alignment_is_preserved_across_resolved_and_unresolved_values(
    mock_param_repo, mock_model_repo, mock_gen_model_repo
):
    """One recorded value per emitted value, in the same order - even when
    some indices resolve to a catalog model and others don't."""
    mock_param_repo.create_batch.return_value = [Mock(id=f"p{i}") for i in range(3)]
    checkpoint = make_model("SDXL Base", model_id="ckpt-1")
    lora = make_model("Detail LoRA", model_id="lora-1")

    def by_path(path, **kwargs):
        return {
            "models/checkpoints/sdxl_base.safetensors": checkpoint,
            "models/loras/detail.safetensors": lora,
        }.get(path)

    mock_model_repo.get_by_file_path.side_effect = by_path
    mock_model_repo.get_by_filename.return_value = []

    handler = make_handler()
    output = ParamGenerationOutput(
        name="model",
        values=[
            "models/checkpoints/sdxl_base.safetensors",
            "models/never_indexed/mystery.safetensors",
            "models/loras/detail.safetensors",
        ],
    )

    handler.handle(output)

    mock_param_repo.create_batch.assert_called_once_with(
        "gen-1",
        "model",
        ["SDXL Base", "mystery.safetensors", "Detail LoRA"],
    )
    mock_gen_model_repo.create_batch.assert_called_once_with(
        "gen-1", ["ckpt-1", "lora-1"]
    )


def test_reduce_leaked_model_path_is_a_pure_basename_reduction():
    assert (
        _reduce_leaked_model_path("models/loras/detail.safetensors")
        == "detail.safetensors"
    )
    assert _reduce_leaked_model_path("detail.safetensors") == "detail.safetensors"
    assert _reduce_leaked_model_path("1024x1024") == "1024x1024"
    assert _reduce_leaked_model_path(42) == 42
    assert _reduce_leaked_model_path(None) is None
