"""Tests for DynamicPromptsRenderedPipe.

The pipe does no expansion of its own - it surfaces the per-image pairs the
backend already resolved (src/features/prompt/expander.py) as
RenderedPromptGenerationOutput artifacts, the prompt counterpart of the seed
artifact. These tests cover:

- one artifact per image, carrying the resolved text and its index
- the ComfyUI single-pair-broadcast case
- silence when no pairs were wired
- layering: the pipe module imports nothing from src.features
- an end-to-end check that what the real expander produces is exactly what the
  pipe emits (guards counts / per-image re-rolls without duplicating logic)
"""

import pytest

from src.pipelines.contracts import PipeInput
from src.pipelines.outputs import (
    RenderedPromptGenerationOutput,
    ProgressGenerationOutput,
)
from src.pipelines.pipes.dynamic_prompts_renderer.main import DynamicPromptsRenderedPipe


def _run(config):
    """Run the pipe with `config` and return the emitted RenderedPromptGenerationOutputs."""
    pipe = DynamicPromptsRenderedPipe(config)
    emitted = []
    pipe.process(PipeInput(input={}), emitted.append)
    return [o for o in emitted if isinstance(o, RenderedPromptGenerationOutput)]


class TestEmission:
    def test_one_artifact_per_image_in_order(self):
        pairs = [
            {"positive": "a red dress", "negative": "blurry"},
            {"positive": "a blue dress", "negative": "ugly"},
            {"positive": "a green dress", "negative": ""},
        ]
        rendered = _run({"pairs": pairs, "quantity": 3})

        assert [r.index for r in rendered] == [0, 1, 2]
        assert [r.positive for r in rendered] == [
            "a red dress", "a blue dress", "a green dress",
        ]
        assert [r.negative for r in rendered] == ["blurry", "ugly", ""]

    def test_missing_channels_default_to_empty_string(self):
        rendered = _run({"pairs": [{"positive": "cat"}], "quantity": 1})
        assert rendered[0].positive == "cat"
        assert rendered[0].negative == ""

    def test_none_channel_values_become_empty_string(self):
        rendered = _run({"pairs": [{"positive": None, "negative": None}], "quantity": 1})
        assert rendered[0].positive == ""
        assert rendered[0].negative == ""

    def test_single_pair_broadcasts_across_quantity(self):
        # ComfyUI produces only pairs[0] but runs `quantity` images off it; every
        # image must report the prompt it actually ran with.
        rendered = _run({"pairs": [{"positive": "one", "negative": "neg"}], "quantity": 4})
        assert [r.index for r in rendered] == [0, 1, 2, 3]
        assert {r.positive for r in rendered} == {"one"}
        assert all(r.negative == "neg" for r in rendered)

    def test_more_pairs_than_quantity_emits_every_pair(self):
        pairs = [{"positive": f"p{i}"} for i in range(3)]
        rendered = _run({"pairs": pairs, "quantity": 1})
        assert [r.positive for r in rendered] == ["p0", "p1", "p2"]

    def test_no_pairs_emits_nothing(self):
        assert _run({"pairs": [], "quantity": 4}) == []
        assert _run({"quantity": 4}) == []

    def test_non_mapping_pair_is_tolerated(self):
        rendered = _run({"pairs": ["not-a-dict"], "quantity": 1})
        assert rendered[0].positive == ""
        assert rendered[0].negative == ""

    def test_emits_a_progress_line(self):
        pipe = DynamicPromptsRenderedPipe({"pairs": [{"positive": "x"}], "quantity": 1})
        emitted = []
        pipe.process(PipeInput(input={}), emitted.append)
        assert any(isinstance(o, ProgressGenerationOutput) for o in emitted)


class TestContract:
    def test_is_a_pure_emitter(self):
        assert DynamicPromptsRenderedPipe.inputs() == []
        assert DynamicPromptsRenderedPipe.outputs() == []

    def test_process_returns_empty_output(self):
        pipe = DynamicPromptsRenderedPipe({"pairs": [{"positive": "x"}], "quantity": 1})
        result = pipe.process(PipeInput(input={}), lambda _o: None)
        assert result.output == {}

    def test_declares_pairs_and_quantity_config(self):
        keys = {spec.name for spec in DynamicPromptsRenderedPipe.configuration()}
        assert {"pairs", "quantity"} <= keys

    def test_module_imports_nothing_from_features(self):
        # Layering: a pipe must not depend on src.features. The expansion lives
        # in features; this pipe only consumes its already-resolved results.
        import ast
        import inspect
        import src.pipelines.pipes.dynamic_prompts_renderer.main as module

        tree = ast.parse(inspect.getsource(module))
        imported = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)
        assert not any(name.startswith("src.features") for name in imported)


class TestMatchesRealExpander:
    """The pipe must surface exactly what the backend expander produced - no
    re-rolling, no re-sampling. Feeding the real expander's output through the
    pipe guards that end-to-end (and, by using the real expander, that counts /
    per-image variable re-rolls are unchanged)."""

    def test_surfaces_expander_output_verbatim(self):
        from src.features.prompt.expander import expand_prompts

        expanded = expand_prompts(
            "a {red|blue|green} dress",
            "blurry, {low|bad} quality",
            count=5,
            base_seed=4242,
            variables={"mood": "{serene|moody}"},
        )
        pairs = [{"positive": e.positive, "negative": e.negative} for e in expanded]

        rendered = _run({"pairs": pairs, "quantity": len(pairs)})

        assert [r.positive for r in rendered] == [e.positive for e in expanded]
        assert [r.negative for r in rendered] == [e.negative for e in expanded]
        assert [r.index for r in rendered] == list(range(len(expanded)))
