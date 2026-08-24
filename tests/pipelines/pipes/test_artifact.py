"""ArtifactPipe (mode=compare) — executes process() with real PIL images.

The pipe's first preset use (Krea2 inline enhance) crashed in
production because its input handling assumed nested image arrays while
generators emit flat lists of PIL images ('Image' object is not iterable).
These tests run the pipe the way the pipeline actually feeds it.
"""

from PIL import Image

from src.pipelines.contracts import PipeInput
from src.pipelines.outputs import CompareImagesGenerationOutput
from src.pipelines.pipes.artifact.main import ArtifactPipe


def _img(color):
    return Image.new("RGB", (8, 8), color)


def _run(before, after, output="right"):
    pipe = ArtifactPipe({"mode": "compare", "left": "Base", "right": "Enhanced", "output": output})
    emitted = []
    result = pipe.process(PipeInput(input={"before_image": before, "after_image": after}), emitted.append)
    return result, emitted


class TestArtifactCompare:
    def test_flat_pil_lists_the_generator_shape(self):
        before = [_img("red"), _img("green")]
        after = [_img("blue"), _img("white")]

        result, emitted = _run(before, after)

        assert result.output["image"] == after
        assert len(emitted) == 2
        for index, out in enumerate(emitted):
            assert isinstance(out, CompareImagesGenerationOutput)
            assert out.index == index
            assert out.compare == ("Base", before[index])
            assert out.to == ("Enhanced", after[index])

    def test_single_quantity_list(self):
        before = [_img("red")]
        after = [_img("blue")]

        result, emitted = _run(before, after)

        assert result.output["image"] == after
        assert len(emitted) == 1
        assert emitted[0].compare == ("Base", before[0])
        assert emitted[0].to == ("Enhanced", after[0])

    def test_bare_images_are_tolerated(self):
        before = _img("red")
        after = _img("blue")

        result, emitted = _run(before, after)

        assert result.output["image"] == [after]
        assert len(emitted) == 1

    def test_output_left_selects_before(self):
        before = [_img("red")]
        after = [_img("blue")]

        result, _ = _run(before, after, output="left")

        assert result.output["image"] == before

    def test_count_mismatch_compares_prefix_without_crashing(self):
        before = [_img("red"), _img("green")]
        after = [_img("blue")]

        result, emitted = _run(before, after)

        assert len(emitted) == 1
        assert result.output["image"] == after

    def test_missing_after_falls_back_to_before(self):
        before = [_img("red")]

        result, emitted = _run(before, None)

        assert emitted == []
        assert result.output["image"] == before
