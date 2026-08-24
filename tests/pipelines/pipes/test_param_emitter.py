"""`param_emitter` writes one parameter row per SAVED index.

Position in a generation's file list is how history, the export endpoint and
the frontend address an image, and a file whose index has no parameter row
carries no history at all. A preset that saves the batch more than once (the
Krea-2 inline enhance pass saves the base batch and the enhanced batch) must
therefore emit `quantity x passes` rows, and per-index arrays that only cover
one pass are tiled across the passes.
"""
from src.pipelines.contracts import PipeInput
from src.pipelines.outputs import ParamGenerationOutput
from src.pipelines.pipes.param_emitter.main import ParamEmitterPipe


def _emit(config, pipe_input=None):
    emitted = []
    ParamEmitterPipe(config=config).process(
        pipe_input or PipeInput(input={}), emitted.append
    )
    return {o.name: o.values for o in emitted if isinstance(o, ParamGenerationOutput)}


def test_single_pass_is_the_default():
    params = _emit({
        "quantity": 2,
        "parameters": [["steps", 8], ["positive_prompt", ["a", "b"]]],
    })

    assert params["steps"] == [8, 8]
    assert params["positive_prompt"] == ["a", "b"]


def test_scalars_broadcast_across_every_pass():
    params = _emit({
        "quantity": 2,
        "passes": 2,
        "parameters": [["steps", 8], ["sampler", "euler"]],
    })

    assert params["steps"] == [8, 8, 8, 8]
    assert params["sampler"] == ["euler"] * 4


def test_per_index_arrays_are_tiled_across_passes():
    params = _emit({
        "quantity": 2,
        "passes": 2,
        "parameters": [["positive_prompt", ["a cat", "a dog"]]],
    })

    assert params["positive_prompt"] == ["a cat", "a dog", "a cat", "a dog"]


def test_input_arrays_are_tiled_too():
    """`seed` reaches the pipe as a runtime input, not as config - the pass
    count has to reach it there as well or the second pass's files lose their
    seed."""
    params = _emit(
        {"quantity": 2, "passes": 2, "parameters": []},
        PipeInput(input={"seed": [111, 222]}),
    )

    assert params["seed"] == [111, 222, 111, 222]


def test_array_already_covering_every_pass_is_used_as_is():
    params = _emit({
        "quantity": 2,
        "passes": 2,
        "parameters": [["resolution", ["1024x1024", "1024x1024", "2048x2048", "2048x2048"]]],
    })

    assert params["resolution"] == ["1024x1024", "1024x1024", "2048x2048", "2048x2048"]


def test_mismatched_array_length_is_still_left_alone():
    params = _emit({
        "quantity": 4,
        "passes": 2,
        "parameters": [["positive_prompt", ["a", "b", "c"]]],
    })

    assert params["positive_prompt"] == ["a", "b", "c"]


def test_multiple_values_for_one_name_are_untouched_by_passes():
    """Model rows are a list of distinct models, not per-index values."""
    params = _emit({
        "quantity": 2,
        "passes": 2,
        "parameters": [["model", "unet.safetensors"], ["model", "vae.safetensors"]],
    })

    assert params["model"] == ["unet.safetensors", "vae.safetensors"]
