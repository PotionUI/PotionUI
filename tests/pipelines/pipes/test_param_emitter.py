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


def test_empty_loop_expansion_is_silently_skipped(caplog):
    """An `@loop` whose `when` is false or whose items list is empty expands to
    `[]` in the rendered `parameters` list - a legitimate "nothing to emit"
    (an unselected LoRA slot, a gated enhance-only row), not malformed input."""
    params = _emit({
        "quantity": 1,
        "parameters": [["steps", 8], [], []],
    })

    assert params["steps"] == [8]
    assert "Invalid parameter format" not in caplog.text


def test_multiple_values_for_one_name_are_untouched_by_passes():
    """Model rows are a list of distinct models, not per-index values."""
    params = _emit({
        "quantity": 2,
        "passes": 2,
        "parameters": [["model", "unet.safetensors"], ["model", "vae.safetensors"]],
    })

    assert params["model"] == ["unet.safetensors", "vae.safetensors"]


def _emitted(config, pipe_input=None):
    emitted = []
    ParamEmitterPipe(config=config).process(pipe_input or PipeInput(input={}), emitted.append)
    return {o.name: o for o in emitted if isinstance(o, ParamGenerationOutput)}


def test_two_element_pairs_carry_no_meta_and_are_declared():
    outputs = _emitted({"quantity": 1, "parameters": [["steps", 8]]})

    assert outputs["steps"].values == [8]
    assert outputs["steps"].meta is None
    assert outputs["steps"].declared is True


def test_three_element_pairs_carry_label_unit_and_type():
    outputs = _emitted({
        "quantity": 2,
        "parameters": [["duration", 5, {"label": "Length", "unit": "s", "type": "number"}]],
    })

    assert outputs["duration"].values == [5, 5]
    assert outputs["duration"].meta == {"label": "Length", "unit": "s", "type": "number"}


def test_unknown_keys_and_non_string_values_in_the_third_element_are_ignored():
    outputs = _emitted({
        "quantity": 1,
        "parameters": [["a", 1, {"label": "A", "color": "red", "unit": 5, "type": "  "}]],
    })

    assert outputs["a"].meta == {"label": "A"}


def test_meta_strings_are_length_capped():
    outputs = _emitted({
        "quantity": 1,
        "parameters": [["a", 1, {"label": "L" * 500, "unit": "u" * 500, "type": "t" * 500}]],
    })

    assert len(outputs["a"].meta["label"]) == 80
    assert len(outputs["a"].meta["unit"]) == 16
    assert len(outputs["a"].meta["type"]) == 40


def test_a_malformed_third_element_is_dropped_but_the_value_is_kept():
    outputs = _emitted({"quantity": 1, "parameters": [["a", 1, "not a dict"], ["b", 2, {}]]})

    assert outputs["a"].values == [1]
    assert outputs["a"].meta is None
    assert outputs["b"].meta is None


def test_pairs_of_other_lengths_are_still_skipped():
    outputs = _emitted({"quantity": 1, "parameters": [["a"], ["b", 1, {}, 4], ["c", 3]]})

    assert set(outputs) == {"c"}


def test_loop_expanded_nested_pairs_keep_their_meta():
    outputs = _emitted({
        "quantity": 1,
        "parameters": [[["a", 1, {"label": "A"}], ["b", 2]]],
    })

    assert outputs["a"].meta == {"label": "A"}
    assert outputs["b"].meta is None


def test_a_name_overridden_by_an_input_is_no_longer_declared():
    outputs = _emitted(
        {"quantity": 1, "parameters": [["secret", 1], ["steps", 2]]},
        PipeInput(input={"secret": "from-input"}),
    )

    assert outputs["secret"].values == ["from-input"]
    assert outputs["secret"].declared is False
    assert outputs["steps"].declared is True


def test_a_name_that_only_arrives_as_an_input_is_not_declared():
    outputs = _emitted({"quantity": 1, "parameters": []}, PipeInput(input={"internal": "x"}))

    assert outputs["internal"].declared is False
