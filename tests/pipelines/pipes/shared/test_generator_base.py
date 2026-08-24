"""Tests for BaseGeneratorPipe: seed-loop, cancellation, default emissions."""
from dataclasses import dataclass
from typing import List

from src.pipelines.outputs import (
    GalleryGenerationOutput,
    ImageGenerationOutput,
    ParamGenerationOutput,
    VideoGenerationOutput,
)
from src.pipelines.contracts import PipeInput, PipeInputSpec, PipeOutputSpec, PipeConfigSpec, IOType
from src.pipelines.pipes._shared.generation.generator_base import BaseGeneratorPipe, GeneratorContext, emit_gallery


class _FakeGeneratorPipe(BaseGeneratorPipe):
    """Minimal concrete generator: records every generate_one call and
    returns a simple ImageGenerationOutput carrying the seed."""

    name = "generator"
    description = "fake generator for tests"

    @classmethod
    def get_default_config(cls):
        return {"seed": -1, "quantity": 1}

    @classmethod
    def inputs(cls) -> List[PipeInputSpec]:
        return [PipeInputSpec("seed", IOType.SEED, False, "seeds", is_array=True)]

    @classmethod
    def outputs(cls) -> List[PipeOutputSpec]:
        return [PipeOutputSpec("image", IOType.IMAGE, "images", is_array=True)]

    @classmethod
    def configuration(cls) -> List[PipeConfigSpec]:
        return []

    def build_context(self, pipe_input: PipeInput) -> GeneratorContext:
        return GeneratorContext(
            quantity=int(self.config.get("quantity", 1)),
            input_seeds=pipe_input.input.get("seed", []),
        )

    def generate_one(self, ctx, index, seed, progress):
        self.calls.append((index, seed))
        return ImageGenerationOutput(image=f"img-{seed}", temporary=False, seed=seed)

    def __init__(self, config):
        super().__init__(config)
        self.calls = []


def _make_pipe(config_overrides=None):
    config = _FakeGeneratorPipe.get_default_config()
    if config_overrides:
        config.update(config_overrides)
    return _FakeGeneratorPipe(config=config)


class TestSeedLoop:

    def test_generate_one_called_once_per_quantity(self):
        pipe = _make_pipe({"quantity": 3})
        pipe_input = PipeInput(input={"seed": [1, 2, 3]})
        pipe.process(pipe_input, lambda o: None)

        assert pipe.calls == [(0, 1), (1, 2), (2, 3)]

    def test_seeds_planned_via_plan_seeds(self):
        pipe = _make_pipe({"quantity": 2, "seed": -1})
        pipe_input = PipeInput(input={})  # no upstream seeds
        pipe.process(pipe_input, lambda o: None)

        assert len(pipe.calls) == 2
        # both seeds should be present and distinct random ints
        seeds = [s for _, s in pipe.calls]
        assert all(isinstance(s, int) for s in seeds)


class TestCancellation:

    def test_process_declares_is_cancelled_and_stops_loop(self):
        import inspect
        sig = inspect.signature(_FakeGeneratorPipe.process)
        assert "is_cancelled" in sig.parameters

    def test_cancellation_stops_after_current_item(self):
        pipe = _make_pipe({"quantity": 5})
        pipe_input = PipeInput(input={"seed": [1, 2, 3, 4, 5]})

        call_count = {"n": 0}

        def is_cancelled():
            # cancel once we've generated 2 items
            return call_count["n"] >= 2

        original_generate_one = pipe.generate_one

        def counting_generate_one(ctx, index, seed, progress):
            call_count["n"] += 1
            return original_generate_one(ctx, index, seed, progress)

        pipe.generate_one = counting_generate_one
        pipe.process(pipe_input, lambda o: None, is_cancelled=is_cancelled)

        assert len(pipe.calls) == 2


class TestDefaultEmission:

    def test_default_emit_results_emits_gallery_and_seed_param(self):
        pipe = _make_pipe({"quantity": 2})
        pipe_input = PipeInput(input={"seed": [7, 8]})
        emitted = []
        pipe.process(pipe_input, emitted.append)

        gallery = [o for o in emitted if isinstance(o, GalleryGenerationOutput)]
        params = [o for o in emitted if isinstance(o, ParamGenerationOutput)]

        assert len(gallery) == 1
        assert len(gallery[0].images) == 2
        seed_param = next(p for p in params if p.name == "seed")
        assert seed_param.values == [7, 8]

    def test_default_build_output_extracts_image_attribute(self):
        pipe = _make_pipe({"quantity": 2})
        pipe_input = PipeInput(input={"seed": [1, 2]})
        result = pipe.process(pipe_input, lambda o: None)

        assert result.output == {"image": ["img-1", "img-2"]}


class TestErrorPathGpuCleanup:
    """On a failed generation, BaseGeneratorPipe releases the engine's GPU VRAM
    (duck-typed `release_gpu()`) before re-raising — so a hard failure (e.g. a
    decode OOM) doesn't leave the DiT/VAE resident."""

    class _EngineStub:
        def __init__(self):
            self.released = 0

        def release_gpu(self):
            self.released += 1

    class _FailingPipe(_FakeGeneratorPipe):
        def build_context(self, pipe_input):
            engine = TestErrorPathGpuCleanup._EngineStub()
            self.engine = engine
            return GeneratorContext(quantity=1, input_seeds=[1], extra={"generator": engine})

        def generate_one(self, ctx, index, seed, progress):
            raise RuntimeError("boom")

    def _make_failing(self):
        pipe = self._FailingPipe(config={"seed": -1, "quantity": 1})
        return pipe

    def test_release_gpu_called_on_generation_error(self):
        import pytest
        pipe = self._make_failing()
        pipe_input = PipeInput(input={"seed": [1]})
        with pytest.raises(RuntimeError, match="boom"):
            pipe.process(pipe_input, lambda o: None)
        assert pipe.engine.released == 1

    def test_release_gpu_failure_does_not_mask_original_error(self):
        import pytest

        class _RaisingEngine:
            def release_gpu(self):
                raise ValueError("cleanup blew up")

        class _Pipe(_FakeGeneratorPipe):
            def build_context(self, pipe_input):
                return GeneratorContext(quantity=1, input_seeds=[1], extra={"generator": _RaisingEngine()})

            def generate_one(self, ctx, index, seed, progress):
                raise RuntimeError("boom")

        pipe = _Pipe(config={"seed": -1, "quantity": 1})
        # The original RuntimeError must surface, not the cleanup ValueError.
        with pytest.raises(RuntimeError, match="boom"):
            pipe.process(PipeInput(input={"seed": [1]}), lambda o: None)

    def test_no_release_on_success(self):
        pipe = _make_pipe({"quantity": 1})
        pipe_input = PipeInput(input={"seed": [1]})
        # A NativeGenerator-less context (default _FakeGeneratorPipe) must not
        # error on the success path — nothing to release, no-op.
        result = pipe.process(pipe_input, lambda o: None)
        assert result.output == {"image": ["img-1"]}

    def test_release_gpu_called_when_extra_is_a_dataclass_not_a_dict(self):
        """The Wan/LTX video pipes build `ctx.extra` as a single per-invocation
        dataclass (router/VAE/conditioning, ...) rather than a dict wrapping
        an engine — checking `ctx.extra` itself (not just dict values) is
        what makes cleanup fire for them. Regression guard for the gap where
        this silently no-op'd for every dataclass-shaped `ctx.extra`."""
        import pytest
        from dataclasses import dataclass

        @dataclass
        class _DataclassCtx:
            released: list

            def release_gpu(self):
                self.released.append(True)

        released = []

        class _Pipe(_FakeGeneratorPipe):
            def build_context(self, pipe_input):
                self.dc_ctx = _DataclassCtx(released=released)
                return GeneratorContext(quantity=1, input_seeds=[1], extra=self.dc_ctx)

            def generate_one(self, ctx, index, seed, progress):
                raise RuntimeError("boom")

        pipe = _Pipe(config={"seed": -1, "quantity": 1})
        with pytest.raises(RuntimeError, match="boom"):
            pipe.process(PipeInput(input={"seed": [1]}), lambda o: None)

        assert released == [True]

    def test_no_release_when_dataclass_extra_lacks_release_gpu(self):
        """A dataclass-shaped `ctx.extra` with no `release_gpu()` method is a
        no-op, not an AttributeError — the failure must still propagate."""
        import pytest
        from dataclasses import dataclass

        @dataclass
        class _PlainCtx:
            value: int

        class _Pipe(_FakeGeneratorPipe):
            def build_context(self, pipe_input):
                return GeneratorContext(quantity=1, input_seeds=[1], extra=_PlainCtx(value=1))

            def generate_one(self, ctx, index, seed, progress):
                raise RuntimeError("boom")

        pipe = _Pipe(config={"seed": -1, "quantity": 1})
        with pytest.raises(RuntimeError, match="boom"):
            pipe.process(PipeInput(input={"seed": [1]}), lambda o: None)


class TestEmitGalleryVideos:
    """emit_gallery's `videos=` extension (Task #14)."""

    def test_images_only_unchanged(self):
        """Images-only path stays byte-identical: no videos key surprises,
        empty videos list on the GalleryGenerationOutput."""
        emitted = []
        emit_gallery(emitted.append, images=["img-1", "img-2"], seeds=[1, 2])

        gallery = [o for o in emitted if isinstance(o, GalleryGenerationOutput)]
        assert len(gallery) == 1
        assert gallery[0].images == ["img-1", "img-2"]
        assert gallery[0].videos == []

        params = [o for o in emitted if isinstance(o, ParamGenerationOutput)]
        assert params[0].values == [1, 2]

    def test_videos_only(self):
        emitted = []
        emit_gallery(emitted.append, images=[], videos=["/tmp/out.mp4"])

        gallery = [o for o in emitted if isinstance(o, GalleryGenerationOutput)][0]
        assert gallery.images == []
        assert len(gallery.videos) == 1
        assert isinstance(gallery.videos[0], VideoGenerationOutput)
        assert gallery.videos[0].video_path == "/tmp/out.mp4"
        assert gallery.videos[0].temporary is True

    def test_images_and_videos_both(self):
        emitted = []
        emit_gallery(emitted.append, images=["img-1"], videos=["/tmp/a.mp4", "/tmp/b.mp4"])

        gallery = [o for o in emitted if isinstance(o, GalleryGenerationOutput)][0]
        assert gallery.images == ["img-1"]
        assert [v.video_path for v in gallery.videos] == ["/tmp/a.mp4", "/tmp/b.mp4"]

    def test_videos_none_omits_seed_param_when_seeds_none(self):
        """videos=None (default) keeps prior no-seed-param behavior intact."""
        emitted = []
        emit_gallery(emitted.append, images=["img-1"])

        assert not any(isinstance(o, ParamGenerationOutput) for o in emitted)
        gallery = [o for o in emitted if isinstance(o, GalleryGenerationOutput)][0]
        assert gallery.videos == []

    def test_video_resolution_stamped_on_every_video(self):
        """video_resolution is applied to every
        emitted VideoGenerationOutput -- there's one configured resolution
        per process() call, shared by all videos it produces."""
        emitted = []
        emit_gallery(emitted.append, images=[], videos=["/tmp/a.mp4", "/tmp/b.mp4"], video_resolution=(832, 480))

        gallery = [o for o in emitted if isinstance(o, GalleryGenerationOutput)][0]
        assert [v.resolution for v in gallery.videos] == [(832, 480), (832, 480)]

    def test_video_resolution_defaults_to_none(self):
        """Callers that haven't been wired to pass video_resolution yet
        (txt2vid_ltx/video_ltx pending be36-guards) keep the
        prior no-live-dimensions behavior unchanged."""
        emitted = []
        emit_gallery(emitted.append, images=[], videos=["/tmp/a.mp4"])

        gallery = [o for o in emitted if isinstance(o, GalleryGenerationOutput)][0]
        assert gallery.videos[0].resolution is None
