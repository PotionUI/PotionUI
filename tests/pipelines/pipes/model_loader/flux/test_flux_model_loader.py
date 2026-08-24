"""Tests for the model_loader/flux pipe.

The pipe acquires TE / VAE / DiT under three independent MODELS keys. The fake
MODELS service below implements REAL hit/miss cache semantics (key+fingerprint
-> cached value, matching ``ModelLifecycleManager.acquire``), because the LoRA
in-place-sync fix under test only shows up across two acquires of the SAME key:
a LoRA-set change must be a cache HIT (same fingerprint, same object, no
``loader()`` re-run) that ``_sync_loras`` reconciles in place, not a
fingerprint-bust that reloads the checkpoint from disk.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.pipelines.contracts import IOType, PipeInput
from src.pipelines.pipes.model_loader.flux import main as flux_main
from src.pipelines.pipes.model_loader.flux.main import ModelLoaderFluxPipe
from src.pipelines.pipes.model_loader.flux.bundle import FluxModelBundle
from src.pipelines.pipes.model_loader.flux.flux_clip import FluxClipTextEncoder
from src.platform.runtime.native.engine import NativeEngineLoader


class _FakeModule:
    """Stand-in for an ``nn.Module``: just enough surface (an empty
    ``named_modules()``) for the REAL ``remove_loras`` to run as a genuine
    no-op instead of needing a real Flux arch instance."""

    def named_modules(self):
        return iter(())


class _FakeModels:
    """Real hit/miss cache semantics (key + fingerprint -> cached value),
    matching ``ModelLifecycleManager.acquire``'s contract: a fingerprint match
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
    cfg = ModelLoaderFluxPipe.get_default_config()
    cfg.update({
        "diffusion_model": {"file_path": "/m/dit.safetensors", "name": "dit"},
        "text_encoder": {"file_path": "/m/te.safetensors", "name": "te"},
        "clip_l": {"file_path": "/m/clip_l.safetensors", "name": "clip_l"},
        "vae": {"file_path": "/m/vae.safetensors", "name": "vae"},
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
    load-bearing assertion (must stay 1 across a LoRA-set change on a warm
    model, proving no re-read of the checkpoint from disk)."""
    counts = {"diffusion_model": 0, "text_encoder": 0, "vae": 0}

    def _fake_load(self, path, kind, **kwargs):
        counts[kind] = counts.get(kind, 0) + 1
        return SimpleNamespace(module=_FakeModule(), spec=None, estimated_vram_gb=1.0, kind=kind)

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

    monkeypatch.setattr(ModelLoaderFluxPipe, "_apply_loras", staticmethod(_fake_apply))
    return calls


# -- metadata --------------------------------------------------------------

def test_name_and_outputs():
    assert ModelLoaderFluxPipe.name == "model_loader"
    out_names = {o.name: o.io_type for o in ModelLoaderFluxPipe.outputs()}
    assert out_names["model"] == IOType.MODEL
    assert out_names["text_encoder"] == IOType.TEXT_ENCODER


# -- three-component acquire ----------------------------------------------

def test_three_distinct_acquire_keys():
    models, _ = _run(ModelLoaderFluxPipe(config=_config()))
    keys = [k for k, _ in models.calls]
    assert len(keys) == 3
    assert len(set(keys)) == 3
    assert keys[0].startswith("native/te/")
    assert keys[1] == "native/vae//m/vae.safetensors"
    assert keys[2] == "native/dit//m/dit.safetensors"


def test_outputs_are_bundle_and_clip():
    _models, out = _run(ModelLoaderFluxPipe(config=_config()))
    assert isinstance(out.output["model"], FluxModelBundle)
    assert isinstance(out.output["text_encoder"], FluxClipTextEncoder)


def test_te_key_includes_clip_l_for_flux1():
    models, _ = _run(ModelLoaderFluxPipe(config=_config()))
    te_key = models.calls[0][0]
    assert "/m/te.safetensors" in te_key and "/m/clip_l.safetensors" in te_key


def test_klein_single_te_no_clip_l():
    # No clip_l -> Klein/Flux2 path; te key still formed, clip_l portion empty.
    cfg = _config()
    cfg["clip_l"] = None
    models, _ = _run(ModelLoaderFluxPipe(config=cfg))
    te_key = models.calls[0][0]
    assert te_key == "native/te//m/te.safetensors|"


def test_bundle_carries_the_te_cache_key():
    """generator/flux's TE eviction reads bundle.te_cache_key -- it must
    match the exact key the TE was acquire()'d under, or evict_dead_weight
    would target the wrong (or no) cache entry."""
    models, out = _run(ModelLoaderFluxPipe(config=_config()))
    te_key = models.calls[0][0]
    assert out.output["model"].te_cache_key == te_key


def test_missing_component_raises():
    cfg = _config()
    cfg["vae"] = None
    with pytest.raises(ValueError, match="requires"):
        _run(ModelLoaderFluxPipe(config=cfg))


# -- fingerprints ------------------------------------------------------------

def _fps(loras):
    models, _ = _run(ModelLoaderFluxPipe(config=_config(loras=loras)))
    return dict(zip([k for k, _ in models.calls], [f for _, f in models.calls]))


def test_lora_change_does_not_bust_dit_fingerprint():
    """The DiT ``MODELS`` fingerprint is path+dtype only now, so a LoRA-set
    change is a cache HIT (see test_lora_change_on_warm_dit_* below for the
    in-place patch that follows a hit), never a fresh fingerprint that forces
    ``ModelLifecycleManager`` to re-run ``loader()`` and re-read the ~24GB
    checkpoint from disk."""
    no_lora = _fps([])
    with_lora = _fps([{"model": "/m/style.safetensors", "strength": 0.8}])
    te_key = next(k for k in no_lora if k.startswith("native/te/"))
    vae_key = "native/vae//m/vae.safetensors"
    dit_key = "native/dit//m/dit.safetensors"
    assert no_lora[te_key] == with_lora[te_key]
    assert no_lora[vae_key] == with_lora[vae_key]
    assert no_lora[dit_key] == with_lora[dit_key]
    assert "style.safetensors" not in with_lora[dit_key]


def test_dtype_in_all_fingerprints():
    models, _ = _run(ModelLoaderFluxPipe(config=_config(dtype="float16")))
    assert all("float16" in fp for _, fp in models.calls)


def test_zero_weight_lora_ignored(_apply_spy):
    _run(ModelLoaderFluxPipe(config=_config(loras=[{"model": "/m/off.safetensors", "strength": 0.0}])))
    # The zero-weight entry never reaches `_apply_loras` at all (filtered by
    # `active_loras()` before the lora_fp/`_sync_loras` machinery sees it).
    assert _apply_spy == [[]]


# -- LoRA sync on an already-cached DiT --------------------------------------

def test_lora_change_on_warm_dit_reuses_cached_weights(_fake_engine, _apply_spy):
    """The core fix: adding a LoRA to an already-loaded Flux DiT reuses the
    SAME cached NativeModel (no second checkpoint read) and only pays for the
    LoRA application itself."""
    models = _FakeModels()
    _, out1 = _run(ModelLoaderFluxPipe(config=_config(loras=[])), models)
    _, out2 = _run(
        ModelLoaderFluxPipe(config=_config(loras=[{"model": "/m/style.safetensors", "strength": 0.8}])),
        models,
    )
    dit1, dit2 = out1.output["model"].dit, out2.output["model"].dit
    assert dit1 is dit2, "expected the SAME DiT wrapper across a LoRA-set change (cache hit, no reload)"
    assert _fake_engine["diffusion_model"] == 1, "the checkpoint must be read from disk exactly once"
    assert dit2._active_lora_fp == "/m/style.safetensors@0.8"
    # apply_loras ran once for the (empty) cold load and once for the sync.
    non_empty_calls = [c for c in _apply_spy if c]
    assert non_empty_calls == [[{"file_path": "/m/style.safetensors", "weight": 0.8}]]


def test_repeat_generation_with_same_loras_is_a_pure_noop(_fake_engine, _apply_spy):
    """Re-running the SAME preset+LoRAs (the common case: another generation
    with nothing changed) must not touch the weights a second time."""
    models = _FakeModels()
    loras = [{"model": "/m/style.safetensors", "strength": 0.8}]
    _run(ModelLoaderFluxPipe(config=_config(loras=loras)), models)
    calls_after_first = len(_apply_spy)
    _run(ModelLoaderFluxPipe(config=_config(loras=loras)), models)
    assert len(_apply_spy) == calls_after_first
    assert _fake_engine["diffusion_model"] == 1


def test_removing_all_loras_on_warm_dit_unpatches_without_reload(_fake_engine, _apply_spy, monkeypatch):
    """Dropping every LoRA from an already-loaded model must unpatch in place
    (``remove_loras``) rather than leaving stale deltas applied or forcing a
    reload just to get back to the bare checkpoint."""
    remove_calls = []
    monkeypatch.setattr(flux_main, "_remove_loras", lambda module: remove_calls.append(module))

    models = _FakeModels()
    _run(ModelLoaderFluxPipe(config=_config(loras=[{"model": "/m/style.safetensors", "strength": 0.8}])), models)
    assert remove_calls == []  # the cold load applies once; nothing patched yet to remove

    _run(ModelLoaderFluxPipe(config=_config(loras=[])), models)
    assert len(remove_calls) == 1
    # No re-apply for an empty target stack.
    assert [c for c in _apply_spy if c] == [[{"file_path": "/m/style.safetensors", "weight": 0.8}]]
    assert _fake_engine["diffusion_model"] == 1


def test_different_dit_path_still_forces_a_real_reload(_fake_engine):
    """Sanity check: the fix narrows cache-busting to real identity changes
    (checkpoint path/dtype), it doesn't disable it -- a different DiT file
    must still be a genuine cache miss."""
    models = _FakeModels()
    _run(ModelLoaderFluxPipe(config=_config(diffusion_model={"file_path": "/m/dit.safetensors"})), models)
    _run(ModelLoaderFluxPipe(config=_config(diffusion_model={"file_path": "/m/dit_v2.safetensors"})), models)
    assert _fake_engine["diffusion_model"] == 2
