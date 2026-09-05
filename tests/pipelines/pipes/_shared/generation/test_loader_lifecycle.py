"""Contract tests for the shared model-loader component lifecycle.

The fake ``MODELS`` below implements REAL hit/miss semantics (key + fingerprint
-> cached value, matching ``ModelLifecycle.acquire``) and counts acquisitions
and loads separately, because the properties under test only show up across two
acquires of the same key: a deferred component that is never asked for must not
be acquired at all, and a bundle sharing a TE or VAE with another component must
resolve to the SAME object without re-running the loader.
"""

from __future__ import annotations

import pytest

from src.pipelines.outputs import ProgressGenerationOutput
from src.pipelines.pipes._shared.generation.loader_helpers import ComponentProgress
from src.pipelines.pipes._shared.generation.loader_lifecycle import (
    Component,
    ComponentLifecycle,
)


class _FakeModule:
    def named_modules(self):
        return iter(())


class _Loaded:
    """Stand-in for a ``NativeModel``: only ``.module`` is read here."""

    def __init__(self, tag: str) -> None:
        self.tag = tag
        self.module = _FakeModule()


class _FakeModels:
    def __init__(self) -> None:
        self.acquires: list[tuple[str, str, float | None]] = []
        self.evicted: list[str] = []
        self._entries: dict[str, tuple[str, object]] = {}

    def acquire(self, key, fingerprint, loader, estimated_vram_gb=None):
        self.acquires.append((key, fingerprint, estimated_vram_gb))
        entry = self._entries.get(key)
        if entry is not None and entry[0] == fingerprint:
            return entry[1]
        value = loader()
        self._entries[key] = (fingerprint, value)
        return value

    def is_cached(self, key: str) -> bool:
        return key in self._entries

    def evict_dead_weight(self, key: str) -> bool:
        self.evicted.append(key)
        return self._entries.pop(key, None) is not None


class _Recorder:
    """Collects emitted progress states and the loads that ran, in order."""

    def __init__(self) -> None:
        self.events: list[str] = []

    def outputs(self, output):
        if isinstance(output, ProgressGenerationOutput):
            self.events.append(f"progress:{output.state}")

    def loader(self, tag: str):
        def _load() -> _Loaded:
            self.events.append(f"load:{tag}")
            return _Loaded(tag)

        return _load


def _lifecycle(models, recorder, total=3, label="Loading"):
    progress = ComponentProgress(recorder.outputs, models, label, total=total)
    return ComponentLifecycle(models, progress)


# -- eager acquisition ------------------------------------------------------

def test_eager_acquire_announces_then_loads_through_models():
    models, rec = _FakeModels(), _Recorder()
    lifecycle = _lifecycle(models, rec)

    loaded = lifecycle.acquire(
        Component("VAE", "native/vae/v.safetensors", "v|bf16", rec.loader("vae"), 2.5)
    )

    assert loaded.tag == "vae"
    assert models.acquires == [("native/vae/v.safetensors", "v|bf16", 2.5)]
    assert rec.events == [
        "progress:Loading — VAE (1 of 3). Loading this model for the first time — "
        "this can take a few minutes. Later runs start in seconds.",
        "load:vae",
    ]


def test_eager_acquire_without_models_loads_directly():
    rec = _Recorder()
    lifecycle = _lifecycle(None, rec)

    loaded = lifecycle.acquire(Component("DiT", "native/dit/d", "d|bf16", rec.loader("dit")))

    assert loaded.tag == "dit"
    assert not lifecycle.caching
    assert rec.events == ["progress:Loading — DiT (1 of 3)", "load:dit"]


# -- deferred acquisition ---------------------------------------------------

def test_deferred_component_is_not_acquired_until_the_thunk_runs():
    """The whole point of deferral: a generation whose consumer never needs the
    encoder must not announce, admit or load it."""
    models, rec = _FakeModels(), _Recorder()
    lifecycle = _lifecycle(models, rec)

    te = Component("text encoder", "native/te/t", "t|bf16", rec.loader("te"), 21.0)
    thunk = lifecycle.deferred_module(te)

    assert models.acquires == []
    assert rec.events == []

    module = thunk()

    assert isinstance(module, _FakeModule)
    assert models.acquires == [("native/te/t", "t|bf16", 21.0)]
    assert rec.events[-1] == "load:te"


def test_deferred_miss_then_hit_loads_once():
    models, rec = _FakeModels(), _Recorder()
    te = Component("text encoder", "native/te/t", "t|bf16", rec.loader("te"), 21.0)

    first = _lifecycle(models, rec).deferred_module(te)()
    second = _lifecycle(models, rec).deferred_module(te)()

    assert first is second
    assert len(models.acquires) == 2
    assert rec.events.count("load:te") == 1


def test_never_acquired_te_is_evictable_by_key_alone():
    """A bundle holds the TE's cache key as a plain string, so the generator's
    idle-TE release addresses a never-acquired encoder without error."""
    models, rec = _FakeModels(), _Recorder()
    lifecycle = _lifecycle(models, rec)

    te = Component("text encoder", "native/te/t", "t|bf16", rec.loader("te"))
    lifecycle.deferred_module(te)

    assert models.evict_dead_weight(te.key) is False
    assert models.acquires == []
    assert rec.events == []


# -- sharing ----------------------------------------------------------------

def test_te_shared_with_another_component_resolves_to_the_same_module():
    """Two bundles naming the same TE key and fingerprint share one module —
    the reuse the per-component cache key exists for."""
    models, rec = _FakeModels(), _Recorder()
    te = Component("text encoder", "native/te/shared", "shared|bf16", rec.loader("te"), 8.0)

    first = _lifecycle(models, rec).acquire(te)
    second = _lifecycle(models, rec).acquire(te)

    assert first is second
    assert rec.events.count("load:te") == 1


def test_vae_from_checkpoint_footprint_passes_no_estimate():
    """A component sliced out of a checkpoint another component already
    estimated passes None, so admission does not count the same file twice."""
    models, rec = _FakeModels(), _Recorder()
    lifecycle = _lifecycle(models, rec, total=2)

    lifecycle.acquire(Component("DiT", "native/dit/all", "all|bf16", rec.loader("dit"), 40.0))
    lifecycle.acquire(Component("VAE", "native/vae/all", "all|bf16", rec.loader("vae"), None))

    assert [estimate for _, _, estimate in models.acquires] == [40.0, None]


def test_fingerprint_change_reloads_under_the_same_key():
    models, rec = _FakeModels(), _Recorder()

    first = _lifecycle(models, rec).acquire(
        Component("DiT", "native/dit/d", "d|bf16|lora:none", rec.loader("dit"))
    )
    second = _lifecycle(models, rec).acquire(
        Component("DiT", "native/dit/d", "d|bf16|lora:a@0.8", rec.loader("dit"))
    )

    assert first is not second
    assert rec.events.count("load:dit") == 2


# -- failure ----------------------------------------------------------------

def test_failure_mid_bundle_leaves_nothing_half_acquired():
    """A raising loader must not leave its own key cached, and must not have
    reached the components after it."""
    models, rec = _FakeModels(), _Recorder()
    lifecycle = _lifecycle(models, rec)

    def _boom():
        rec.events.append("load:dit")
        raise RuntimeError("checkpoint is truncated")

    lifecycle.acquire(Component("VAE", "native/vae/v", "v|bf16", rec.loader("vae")))
    with pytest.raises(RuntimeError, match="truncated"):
        lifecycle.acquire(Component("DiT", "native/dit/d", "d|bf16", _boom))

    assert models.is_cached("native/vae/v")
    assert not models.is_cached("native/dit/d")
    assert "load:te" not in rec.events


# -- progress semantics -----------------------------------------------------

def test_progress_advances_once_per_component_in_acquire_order():
    models, rec = _FakeModels(), _Recorder()
    lifecycle = _lifecycle(models, rec, total=3, label="Loading X")

    te = Component("text encoder", "native/te/t", "t|bf16", rec.loader("te"))
    lifecycle.acquire(Component("VAE", "native/vae/v", "v|bf16", rec.loader("vae")))
    lifecycle.acquire(Component("DiT", "native/dit/d", "d|bf16", rec.loader("dit")))
    lifecycle.deferred_module(te)()

    states = [e for e in rec.events if e.startswith("progress:")]
    assert [s.split(" (")[0] for s in states] == [
        "progress:Loading X — VAE",
        "progress:Loading X — DiT",
        "progress:Loading X — text encoder",
    ]
    assert [s.split("(")[1].split(")")[0] for s in states] == ["1 of 3", "2 of 3", "3 of 3"]
