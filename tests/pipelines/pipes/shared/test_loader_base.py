"""Tests for BaseModelLoaderPipe: MODELS-acquire wiring, hit/miss, hooks."""
from unittest.mock import Mock

from src.pipelines.outputs import ModelGenerationOutput, ModelsGenerationOutput, ProgressGenerationOutput
from src.pipelines.contracts import PipeInput, PipeInputSpec, PipeOutputSpec, PipeConfigSpec, IOType
from src.pipelines.pipes._shared.generation.loader_base import BaseModelLoaderPipe
from src.pipelines.pipes._shared.generation.loader_helpers import COLD_LOAD_NOTE


class _FakeLoaderPipe(BaseModelLoaderPipe):
    name = "model_loader"
    description = "fake loader for tests"

    @classmethod
    def get_default_config(cls):
        return {"model_id": "fake-model"}

    @classmethod
    def inputs(cls):
        return [PipeInputSpec("MODELS", IOType.SERVICE, False, "models service", is_array=False)]

    @classmethod
    def outputs(cls):
        return [PipeOutputSpec("model", IOType.MODEL, "loaded model", is_array=False)]

    @classmethod
    def configuration(cls):
        return []

    def progress_message(self) -> str:
        return f"Loading {self.config.get('model_id')}"

    def describe_models(self):
        return [ModelGenerationOutput(name=self.config.get("model_id"), type="other")]

    def cache_key(self) -> str:
        return "fake/loader"

    def fingerprint(self) -> str:
        return self.config.get("model_id")

    def load_model(self, pipe_input: PipeInput):
        self.load_calls += 1
        return f"loaded:{self.config.get('model_id')}"

    def __init__(self, config):
        super().__init__(config)
        self.load_calls = 0


def _make_pipe():
    return _FakeLoaderPipe(config=_FakeLoaderPipe.get_default_config())


class TestLoaderBaseEmissions:

    def test_emits_progress_and_models_before_load(self):
        pipe = _make_pipe()
        emitted = []
        pipe.process(PipeInput(input={}), emitted.append)

        progress_outputs = [o for o in emitted if isinstance(o, ProgressGenerationOutput)]
        models_outputs = [o for o in emitted if isinstance(o, ModelsGenerationOutput)]

        assert len(progress_outputs) == 1
        assert progress_outputs[0].state == "Loading fake-model"
        assert len(models_outputs) == 1
        assert models_outputs[0].models[0].name == "fake-model"


class TestLoaderBaseColdLoadNote:

    def test_appends_cold_note_when_cache_key_is_not_yet_cached(self):
        pipe = _make_pipe()
        fake_models = Mock()
        fake_models.is_cached = Mock(return_value=False)
        fake_models.acquire = Mock(side_effect=lambda key, fingerprint, loader: loader())

        emitted = []
        pipe.process(PipeInput(input={"MODELS": fake_models}), emitted.append)

        progress_outputs = [o for o in emitted if isinstance(o, ProgressGenerationOutput)]
        assert COLD_LOAD_NOTE in progress_outputs[0].state
        fake_models.is_cached.assert_called_once_with("fake/loader")

    def test_omits_cold_note_when_cache_key_is_already_cached(self):
        pipe = _make_pipe()
        fake_models = Mock()
        fake_models.is_cached = Mock(return_value=True)
        fake_models.acquire = Mock(return_value="cached-model")

        emitted = []
        pipe.process(PipeInput(input={"MODELS": fake_models}), emitted.append)

        progress_outputs = [o for o in emitted if isinstance(o, ProgressGenerationOutput)]
        assert COLD_LOAD_NOTE not in progress_outputs[0].state


class TestLoaderBaseNoModelsService:

    def test_loads_directly_when_no_models_service(self):
        pipe = _make_pipe()
        result = pipe.process(PipeInput(input={}), lambda o: None)

        assert pipe.load_calls == 1
        assert result.output == {"model": "loaded:fake-model"}


class TestLoaderBaseAcquireHitMiss:

    def test_cache_hit_skips_loader(self):
        pipe = _make_pipe()
        fake_models = Mock()
        fake_models.acquire = Mock(return_value="cached-model")

        result = pipe.process(PipeInput(input={"MODELS": fake_models}), lambda o: None)

        assert pipe.load_calls == 0
        fake_models.acquire.assert_called_once()
        assert fake_models.acquire.call_args.kwargs["key"] == "fake/loader"
        assert fake_models.acquire.call_args.kwargs["fingerprint"] == "fake-model"
        assert result.output == {"model": "cached-model"}

    def test_cache_miss_invokes_loader_via_acquire(self):
        pipe = _make_pipe()
        fake_models = Mock()
        fake_models.acquire = Mock(side_effect=lambda key, fingerprint, loader: loader())

        result = pipe.process(PipeInput(input={"MODELS": fake_models}), lambda o: None)

        assert pipe.load_calls == 1
        assert result.output == {"model": "loaded:fake-model"}


class TestLoaderBaseHooks:

    def test_after_acquire_and_build_output_hooks_are_invoked(self):
        class HookedPipe(_FakeLoaderPipe):
            def after_acquire(self, model, pipe_input, fingerprint):
                self.after_acquire_called_with = (model, fingerprint)

            def build_output(self, model, pipe_input, fingerprint):
                return {"model": model, "text_encoder": "fake-text-encoder"}

        pipe = HookedPipe(config=HookedPipe.get_default_config())
        result = pipe.process(PipeInput(input={}), lambda o: None)

        assert pipe.after_acquire_called_with == ("loaded:fake-model", "fake-model")
        assert result.output == {"model": "loaded:fake-model", "text_encoder": "fake-text-encoder"}

    def test_validate_hook_runs_before_any_emission(self):
        class ValidatingPipe(_FakeLoaderPipe):
            def validate(self):
                raise ValueError("bad config")

        pipe = ValidatingPipe(config=ValidatingPipe.get_default_config())
        emitted = []
        try:
            pipe.process(PipeInput(input={}), emitted.append)
            assert False, "expected ValueError"
        except ValueError:
            pass

        assert emitted == []
