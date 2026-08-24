"""Unit tests for the shared native model-loader helpers (no GPU, no real checkpoints)."""

import types

from src.pipelines.outputs import ProgressGenerationOutput
from src.pipelines.pipes._shared.generation import loader_helpers as lh


class TestPathOf:
    def test_none_component_returns_none(self):
        assert lh.path_of(None) is None

    def test_empty_dict_returns_none(self):
        assert lh.path_of({}) is None

    def test_missing_file_path_returns_none(self):
        assert lh.path_of({"name": "foo"}) is None

    def test_blank_file_path_returns_none(self):
        assert lh.path_of({"file_path": "   "}) is None

    def test_valid_file_path_returned_as_str(self):
        assert lh.path_of({"file_path": "/models/foo.safetensors"}) == "/models/foo.safetensors"


class TestComponentProgress:
    def test_advance_emits_a_fraction_and_labelled_state(self):
        emitted = []
        progress = lh.ComponentProgress(emitted.append, models=None, label="Loading Flux model", total=3)

        progress.advance("text encoder", "native/te/foo")
        progress.advance("VAE", "native/vae/bar")
        progress.advance("DiT", "native/dit/baz")

        assert len(emitted) == 3
        assert all(isinstance(o, ProgressGenerationOutput) for o in emitted)
        assert emitted[0].state == "Loading Flux model — text encoder (1 of 3)"
        assert (emitted[0].progress.current, emitted[0].progress.max) == (0, 3)
        assert emitted[1].state == "Loading Flux model — VAE (2 of 3)"
        assert (emitted[1].progress.current, emitted[1].progress.max) == (1, 3)
        assert emitted[2].state == "Loading Flux model — DiT (3 of 3)"
        assert (emitted[2].progress.current, emitted[2].progress.max) == (2, 3)

    def test_appends_cold_load_note_when_component_key_is_not_cached(self):
        class _FakeModels:
            def is_cached(self, key):
                return False

        emitted = []
        progress = lh.ComponentProgress(emitted.append, models=_FakeModels(), label="Loading X", total=1)
        progress.advance("DiT", "native/dit/foo")

        assert lh.COLD_LOAD_NOTE in emitted[0].state

    def test_omits_cold_load_note_when_component_key_is_already_cached(self):
        class _FakeModels:
            def is_cached(self, key):
                return True

        emitted = []
        progress = lh.ComponentProgress(emitted.append, models=_FakeModels(), label="Loading X", total=1)
        progress.advance("DiT", "native/dit/foo")

        assert lh.COLD_LOAD_NOTE not in emitted[0].state

    def test_tolerates_a_models_service_without_is_cached(self):
        class _BareModels:
            def acquire(self, **kwargs):
                return None

        emitted = []
        progress = lh.ComponentProgress(emitted.append, models=_BareModels(), label="Loading X", total=1)
        progress.advance("DiT", "native/dit/foo")

        assert lh.COLD_LOAD_NOTE not in emitted[0].state


class TestActiveLoras:
    def test_none_input_returns_empty(self):
        assert lh.active_loras(None) == []

    def test_filters_missing_path(self):
        loras = [{"weight": 1.0}]
        assert lh.active_loras(loras) == []

    def test_filters_zero_weight(self):
        loras = [{"file_path": "/a.safetensors", "weight": 0.0}]
        assert lh.active_loras(loras) == []

    def test_filters_non_numeric_weight(self):
        loras = [{"file_path": "/a.safetensors", "weight": "not-a-number"}]
        assert lh.active_loras(loras) == []

    def test_keeps_active_entries_with_normalized_shape(self):
        loras = [{"file_path": "/a.safetensors", "weight": 0.8}]
        assert lh.active_loras(loras) == [{"file_path": "/a.safetensors", "weight": 0.8}]

    def test_falls_back_to_model_and_strength_keys(self):
        loras = [{"model": "/b.safetensors", "strength": 0.5}]
        assert lh.active_loras(loras) == [{"file_path": "/b.safetensors", "weight": 0.5}]

    def test_mixed_active_and_inactive(self):
        loras = [
            {"file_path": "/a.safetensors", "weight": 0.8},
            {"file_path": "/b.safetensors", "weight": 0.0},
            {"weight": 0.5},
        ]
        assert lh.active_loras(loras) == [{"file_path": "/a.safetensors", "weight": 0.8}]


class TestVramBudget:
    def test_missing_gpu_service_returns_none(self):
        pipe_input = types.SimpleNamespace(input={})
        assert lh.vram_budget(pipe_input, None, "TEST") is None

    def test_delegates_to_gpu_service_and_returns_budget(self):
        class _FakeGpu:
            def get_vram_budget(self, limit, safety_margin=0.85):
                assert limit == 8.0
                # Native callers use a gentle margin — the tiering layer owns
                # the activation reserve (see lh._NATIVE_SAFETY_MARGIN).
                assert safety_margin == 0.97
                return 12.5

        pipe_input = types.SimpleNamespace(input={"GPU": _FakeGpu()})
        assert lh.vram_budget(pipe_input, 8.0, "TEST") == 12.5


class TestApplyLorasTo:
    def test_empty_loras_is_a_noop(self, monkeypatch):
        called = []
        monkeypatch.setattr(lh, "load_torch_file", lambda *a, **kw: called.append("load"))
        monkeypatch.setattr(lh, "apply_loras", lambda *a, **kw: called.append("apply"))
        lh.apply_loras_to(object(), [], "TEST")
        assert called == []

    def test_loads_and_applies_each_lora_in_order(self, monkeypatch):
        loaded_paths = []

        def fake_load(path, device="cpu"):
            loaded_paths.append(path)
            return ({"tensor": path}, {})

        applied = {}

        def fake_apply(module, stack):
            applied["module"] = module
            applied["stack"] = stack
            return (3, ["unmatched.key"])

        monkeypatch.setattr(lh, "load_torch_file", fake_load)
        monkeypatch.setattr(lh, "apply_loras", fake_apply)

        dit_model = types.SimpleNamespace(module="THE_MODULE")
        loras = [
            {"file_path": "/a.safetensors", "weight": 0.8},
            {"file_path": "/b.safetensors", "weight": 0.5},
        ]
        lh.apply_loras_to(dit_model, loras, "TEST")

        assert loaded_paths == ["/a.safetensors", "/b.safetensors"]
        assert applied["module"] == "THE_MODULE"
        assert applied["stack"] == [
            ({"tensor": "/a.safetensors"}, 0.8),
            ({"tensor": "/b.safetensors"}, 0.5),
        ]
