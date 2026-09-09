"""Tests for the model_loader/krea2 pipe.

Mirrors the flux loader test: a fake MODELS service records the three
independent acquires (TE / VAE / DiT) so the cache-key scheme + fingerprints
are asserted without touching real checkpoints.

The fake MODELS service below implements REAL hit/miss cache
semantics (key+fingerprint -> cached value, matching
``ModelLifecycle.acquire``) rather than just recording calls, because
the fix under test only shows up across two acquires of the SAME key: a
LoRA-set change must be a cache HIT (same fingerprint, same object, no
``loader()`` re-run) that ``_sync_loras`` reconciles in place, not a
fingerprint-bust that reloads the checkpoint from disk.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.pipelines.contracts import IOType, PipeInput
from src.pipelines.pipes._shared.generation.loader_helpers import (
    windowed_stack_fingerprint as _window_fingerprint,
)
from src.platform.runtime.native.lora import LoraStepWindow
from src.pipelines.pipes._shared.generation.loader_helpers import (
    lora_stack_fingerprint as _lora_stack_fingerprint,
)
from src.pipelines.pipes.model_loader.krea2.main import ModelLoaderKrea2Pipe
from src.pipelines.pipes.model_loader.krea2.bundle import Krea2ModelBundle
from src.pipelines.pipes.model_loader.krea2.krea2_clip import Krea2ClipTextEncoder
from src.platform.runtime.native.engine import NativeEngineLoader, NativeModel


class _FakeModule:
    """Stand-in for an ``nn.Module``: just enough surface (an empty
    ``named_modules()``) for the REAL ``remove_loras`` to run as a genuine
    no-op instead of needing a real Krea-2 arch instance."""

    def named_modules(self):
        return iter(())


class _FakeModels:
    """Real hit/miss cache semantics (key + fingerprint -> cached value),
    matching ``ModelLifecycle.acquire``'s contract: a fingerprint match
    returns the SAME cached object without re-running ``loader()``; a
    mismatch (new key, or a busted fingerprint) runs ``loader()`` and caches
    the result. ``self.calls`` still records every acquire() invocation
    (hit or miss) for the existing key/fingerprint assertions."""

    def __init__(self):
        self.calls = []
        self._entries = {}

    def acquire(self, key, fingerprint, loader, estimated_vram_gb=None):
        self.calls.append((key, fingerprint))
        entry = self._entries.get(key)
        if entry is not None and entry[0] == fingerprint:
            return entry[1]
        value = loader()
        self._entries[key] = (fingerprint, value)
        return value


def _config(loras=None, **over):
    cfg = ModelLoaderKrea2Pipe.get_default_config()
    cfg.update({
        "diffusion_model": {"file_path": "/m/krea2_dit.safetensors", "name": "dit"},
        "text_encoder": {"file_path": "/m/qwen3vl.safetensors", "name": "te"},
        "vae": {"file_path": "/m/qwen_vae.safetensors", "name": "vae"},
        "loras": loras or [],
    })
    cfg.update(over)
    return cfg


def _run(pipe, models=None):
    models = models if models is not None else _FakeModels()
    out = pipe.process(PipeInput(input={"MODELS": models}), lambda o: None)
    return models, out


@pytest.fixture(autouse=True)
def _fake_engine(monkeypatch):
    """Replace real checkpoint I/O (``NativeEngineLoader.load``) with a cheap
    fake, and count invocations per component kind -- the DiT count is the
    load-bearing assertion (must stay 1 across a LoRA-set change on
    a warm model, proving no re-read of the checkpoint from disk)."""
    counts = {"diffusion_model": 0, "text_encoder": 0, "vae": 0}

    def _fake_load(self, path, kind, **kwargs):
        counts[kind] = counts.get(kind, 0) + 1
        # A real wrapper, not a stand-in: the loaders revise its weight identity.
        return NativeModel(kind, _FakeModule(), spec=None, estimated_vram_gb=1.0)

    monkeypatch.setattr(NativeEngineLoader, "load", _fake_load)
    return counts


@pytest.fixture(autouse=True)
def _apply_spy(monkeypatch):
    """Replace the pipe's own ``_apply_loras`` (which would otherwise read a
    real LoRA file off disk via ``load_torch_file``) with a spy recording the
    (possibly empty) LoRA list it was asked to apply."""
    calls = []

    def _fake_apply(dit_model, loras):
        calls.append(list(loras))

    monkeypatch.setattr(ModelLoaderKrea2Pipe, "_apply_loras", staticmethod(_fake_apply))
    return calls


def test_name_and_outputs():
    assert ModelLoaderKrea2Pipe.name == "model_loader"
    out_names = {o.name: o.io_type for o in ModelLoaderKrea2Pipe.outputs()}
    assert out_names["model"] == IOType.MODEL
    assert out_names["text_encoder"] == IOType.TEXT_ENCODER


def test_process_eagerly_acquires_only_vae_and_dit():
    """The TE's acquire() is deferred (see Krea2ClipTextEncoder) -- process()
    itself must only ever touch VAE + DiT, never the TE, so a generation whose
    whole prompt batch hits the embed cache never loads/acquires it at all."""
    models, _ = _run(ModelLoaderKrea2Pipe(config=_config()))
    keys = [k for k, _ in models.calls]
    assert len(keys) == 2 and len(set(keys)) == 2
    assert keys[0] == "native/vae//m/qwen_vae.safetensors"
    assert keys[1] == "native/dit//m/krea2_dit.safetensors"


def test_te_acquire_deferred_until_encoder_is_read():
    """The TE is acquired the first time (and only the first time) something
    reads ``Krea2ClipTextEncoder.encoder`` -- the stand-in for prompt_encoder
    actually needing it on an embed-cache miss."""
    models, out = _run(ModelLoaderKrea2Pipe(config=_config()))
    assert [k for k, _ in models.calls] == [
        "native/vae//m/qwen_vae.safetensors", "native/dit//m/krea2_dit.safetensors",
    ]

    clip = out.output["text_encoder"]
    _ = clip.encoder
    _ = clip.encoder  # a second read must not re-acquire

    te_calls = [(k, fp) for k, fp in models.calls if k == "native/te//m/qwen3vl.safetensors"]
    assert len(te_calls) == 1
    assert te_calls[0][1] == "/m/qwen3vl.safetensors|bfloat16|vision=False"


def test_te_never_acquired_when_encoder_is_never_read():
    """The direct regression guard for this fix: a batch that never needs the
    TE (every request served from the embed cache) must never call
    MODELS.acquire() for it at all -- not "acquired then evicted", not
    acquired at all."""
    models, out = _run(ModelLoaderKrea2Pipe(config=_config()))
    assert not any(k == "native/te//m/qwen3vl.safetensors" for k, _ in models.calls)
    # Bite check: the assertion actually distinguishes the fixed behaviour --
    # forcing a read flips it (proves the assertion isn't vacuous).
    _ = out.output["text_encoder"].encoder
    assert any(k == "native/te//m/qwen3vl.safetensors" for k, _ in models.calls)


def test_outputs_are_bundle_and_clip():
    _models, out = _run(ModelLoaderKrea2Pipe(config=_config()))
    assert isinstance(out.output["model"], Krea2ModelBundle)
    assert isinstance(out.output["text_encoder"], Krea2ClipTextEncoder)


def test_bundle_unload_tolerates_a_never_acquired_te():
    """bundle.te stays unset when the TE's acquire() was deferred and never
    triggered -- unload() (the "Clear VRAM" / teardown path) must not raise
    over a component it never got."""
    _models, out = _run(ModelLoaderKrea2Pipe(config=_config()))
    out.output["model"].unload()


def test_bundle_carries_the_te_cache_key():
    """generator/krea2's TE eviction reads bundle.te_cache_key -- it
    must match the exact key the TE was acquire()'d under, or evict_dead_weight
    would target the wrong (or no) cache entry."""
    _models, out = _run(ModelLoaderKrea2Pipe(config=_config()))
    assert out.output["model"].te_cache_key == "native/te//m/qwen3vl.safetensors"


def test_dtype_in_all_fingerprints():
    models, _ = _run(ModelLoaderKrea2Pipe(config=_config(dtype="float16")))
    assert all("float16" in fp for _, fp in models.calls)


def test_missing_component_raises():
    cfg = _config()
    cfg["vae"] = None
    with pytest.raises(ValueError, match="requires"):
        _run(ModelLoaderKrea2Pipe(config=cfg))


def _fps(loras):
    models, out = _run(ModelLoaderKrea2Pipe(config=_config(loras=loras)))
    _ = out.output["text_encoder"].encoder  # force the deferred TE acquire
    return dict(zip([k for k, _ in models.calls], [f for _, f in models.calls]))


def test_lora_change_does_not_bust_dit_fingerprint():
    """The DiT ``MODELS`` fingerprint is path+dtype only now,
    so a LoRA-set change is a cache HIT (see test_lora_change_on_warm_dit_*
    below for the in-place patch that follows a hit), never a fresh
    fingerprint that forces ``ModelLifecycle`` to re-run ``loader()``
    and re-read the ~24.5GB checkpoint from disk."""
    no_lora = _fps([])
    with_lora = _fps([{"model": "/m/style.safetensors", "strength": 0.8}])
    te_key = "native/te//m/qwen3vl.safetensors"
    vae_key = "native/vae//m/qwen_vae.safetensors"
    dit_key = "native/dit//m/krea2_dit.safetensors"
    assert no_lora[te_key] == with_lora[te_key]
    assert no_lora[vae_key] == with_lora[vae_key]
    assert no_lora[dit_key] == with_lora[dit_key]
    assert "style.safetensors" not in with_lora[dit_key]


def test_zero_weight_lora_ignored(_apply_spy):
    _run(ModelLoaderKrea2Pipe(config=_config(loras=[{"model": "/m/off.safetensors", "strength": 0.0}])))
    # The zero-weight entry never reaches `_apply_loras` at all (filtered by
    # `active_loras()` before the lora_fp/`_sync_loras` machinery sees it).
    assert _apply_spy == [[]]


def test_lora_change_on_warm_dit_reuses_cached_weights(_fake_engine, _apply_spy):
    """The core fix: adding a LoRA to an already-loaded Krea-2 DiT
    reuses the SAME cached NativeModel (no second checkpoint read) and only
    pays for the LoRA application itself."""
    models = _FakeModels()
    _, out1 = _run(ModelLoaderKrea2Pipe(config=_config(loras=[])), models)
    _, out2 = _run(
        ModelLoaderKrea2Pipe(config=_config(loras=[{"model": "/m/style.safetensors", "strength": 0.8}])),
        models,
    )
    dit1, dit2 = out1.output["model"].dit, out2.output["model"].dit
    assert dit1 is dit2, "expected the SAME DiT wrapper across a LoRA-set change (cache hit, no reload)"
    assert _fake_engine["diffusion_model"] == 1, "the checkpoint must be read from disk exactly once"
    # Compared against the shared stamp rather than a literal: it carries the
    # file's own identity, so a retrained adapter at this path is a new stack.
    assert dit2._active_lora_fp == _lora_stack_fingerprint(
        [{"file_path": "/m/style.safetensors", "weight": 0.8}])
    # apply_loras ran once for the (empty) cold load and once for the sync.
    non_empty_calls = [c for c in _apply_spy if c]
    assert non_empty_calls == [[{"file_path": "/m/style.safetensors", "weight": 0.8, "window": None}]]


def test_repeat_generation_with_same_loras_is_a_pure_noop(_fake_engine, _apply_spy):
    """Re-running the SAME preset+LoRAs (the common case: another generation
    with nothing changed) must not touch the weights a second time."""
    models = _FakeModels()
    loras = [{"model": "/m/style.safetensors", "strength": 0.8}]
    _run(ModelLoaderKrea2Pipe(config=_config(loras=loras)), models)
    calls_after_first = len(_apply_spy)
    _run(ModelLoaderKrea2Pipe(config=_config(loras=loras)), models)
    assert len(_apply_spy) == calls_after_first
    assert _fake_engine["diffusion_model"] == 1


def test_removing_all_loras_on_warm_dit_unpatches_without_reload(_fake_engine, _apply_spy, monkeypatch):
    """Dropping every LoRA from an already-loaded model must unpatch in place
    (``remove_loras``) rather than leaving stale deltas applied or forcing a
    reload just to get back to the bare checkpoint."""
    remove_calls = []
    monkeypatch.setattr("src.pipelines.pipes._shared.generation.loader_lifecycle.remove_loras", lambda module: remove_calls.append(module))

    models = _FakeModels()
    _run(ModelLoaderKrea2Pipe(config=_config(loras=[{"model": "/m/style.safetensors", "strength": 0.8}])), models)
    assert remove_calls == []  # the cold load applies once; nothing patched yet to remove

    _run(ModelLoaderKrea2Pipe(config=_config(loras=[])), models)
    assert len(remove_calls) == 1
    # No re-apply for an empty target stack.
    assert [c for c in _apply_spy if c] == [[{"file_path": "/m/style.safetensors", "weight": 0.8, "window": None}]]
    assert _fake_engine["diffusion_model"] == 1


def test_different_dit_path_still_forces_a_real_reload(_fake_engine):
    """Sanity check: the fix narrows cache-busting to real identity changes
    (checkpoint path/dtype), it doesn't disable it -- a different DiT file
    must still be a genuine cache miss."""
    models = _FakeModels()
    _run(ModelLoaderKrea2Pipe(config=_config(diffusion_model={"file_path": "/m/krea2_dit.safetensors"})), models)
    _run(ModelLoaderKrea2Pipe(config=_config(diffusion_model={"file_path": "/m/krea2_dit_v2.safetensors"})), models)
    assert _fake_engine["diffusion_model"] == 2


class TestWindowedStackRevision:
    """``_sync_lora_windows`` revises a cache-HIT DiT's weight identity when the
    step-windowed request changes. Windowed entries are never patched into the
    cached module, so the stamp is the only thing that notices."""

    def _dit(self):
        return SimpleNamespace(bumps=[], bump_weight_revision=lambda reason: None)

    def _model(self):
        model = SimpleNamespace()
        model.bumps = []
        model.bump_weight_revision = model.bumps.append
        return model

    def _entry(self, start, end, path="/m/turbo-sda.safetensors", weight=1.0):
        return {"file_path": path, "weight": weight, "window": LoraStepWindow(start, end)}

    def test_an_unchanged_windowed_request_does_not_revise(self):
        model = self._model()
        stamp = _window_fingerprint([self._entry(1, 2)])

        ModelLoaderKrea2Pipe._sync_lora_windows(model, stamp)
        ModelLoaderKrea2Pipe._sync_lora_windows(model, stamp)

        assert len(model.bumps) == 1

    def test_a_changed_window_revises_even_at_the_same_file_and_weight(self):
        model = self._model()

        ModelLoaderKrea2Pipe._sync_lora_windows(model, _window_fingerprint([self._entry(1, 2)]))
        ModelLoaderKrea2Pipe._sync_lora_windows(model, _window_fingerprint([self._entry(1, 5)]))

        assert len(model.bumps) == 2
