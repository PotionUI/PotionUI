"""Leak-hunt regression (LTX upscale-mode
incident): drives the REAL ``GenerationManager.generate()`` -> REAL
``ModelLifecycleManager.acquire()`` -> a bundle using the REAL ``WeakModelRef``
pattern every native family's bundle uses, then asserts the loaded model is
actually garbage-collectable once the generation ends and once a fingerprint
bust (e.g. a LoRA change between two modes' independent forms) reloads it.

This corroborates ``tests/platform/runtime/model_lifecycle/test_lora_swap_rss.py``'s
own finding: the CORE cache + weak-bundle + generate() mechanism, exercised
through the real production code path, holds NO leak in isolation. If a real
incident still shows stuck RAM, the holder is somewhere the full pipe/hook/
closure machinery introduces that this minimal-but-faithful harness doesn't
exercise -- see ``ModelLifecycleManager._log_referrer_diagnostic``, which logs
"referrer diagnostic: held by [...]" at INFO level any time an eviction can't
unload because of a live reference. That log line is the fastest path to a
definitive answer on a real box; this file only pins the already-fixed cases
so they can't silently regress.
"""
from __future__ import annotations

import gc
import weakref
from unittest.mock import Mock

import torch

from src.features.generation.generation import GenerationManager
from src.pipelines.contracts import (
    BasePipe, IOType, PipeInput, PipeInputSpec, PipeOutput, PipeOutputSpec,
)
from src.platform.runtime.model_lifecycle.manager import ModelLifecycleManager
from src.pipelines.pipes._shared.generation.weak_model_ref import WeakModelRef

from dataclasses import dataclass, field
from typing import Any, Optional

_captured: dict = {}


class _SyntheticNativeModel:
    """Stand-in for engine.py's NativeModel: a tiny real nn.Module wrapper
    with an ``unload()`` the lifecycle manager's ``_best_effort_unload`` can
    call, matching the real shape (``.module``, ``.unload()``)."""

    def __init__(self):
        self.module = torch.nn.Linear(4, 4)

    def unload(self):
        self.module = None


@dataclass
class _FakeBundle:
    """Mirrors every native family's bundle shape: a
    lightweight VIEW over independently-cached components via WeakModelRef,
    never a strong owner."""

    dit: Any = field(default=WeakModelRef())
    vae: Optional[Any] = field(default=WeakModelRef())


@dataclass
class _FakeGeneratorCtx:
    """Mirrors ``_LTXCtx`` (generator/txt2vid_ltx/main.py): holds the WHOLE
    bundle AND a SEPARATELY dereferenced component field, both as plain
    (strong) dataclass fields -- the sanctioned "dereference into your own
    strong local for the life of one generation" pattern documented in
    weak_model_ref.py."""

    bundle: Any
    vae: Any  # = bundle.vae, dereferenced once at construction


class _FakeModelLoaderPipe(BasePipe):
    name = "fake_model_loader"
    description = "test-only model loader"
    fingerprint_suffix = "none"  # overridden per-test to simulate a LoRA swap

    @classmethod
    def get_default_config(cls):
        return {"fingerprint_suffix": "none"}

    @classmethod
    def configuration(cls):
        return []

    @classmethod
    def inputs(cls):
        return [PipeInputSpec(name="MODELS", io_type=IOType.SERVICE, required=False)]

    @classmethod
    def outputs(cls):
        return [PipeOutputSpec(name="model", io_type=IOType.MODEL)]

    def process(self, pipe_input: PipeInput, generation_outputs: callable, is_cancelled=None) -> PipeOutput:
        models = pipe_input.input.get("MODELS")

        def make_dit():
            m = _SyntheticNativeModel()
            _captured["dit_ref"] = weakref.ref(m)
            return m

        def make_vae():
            m = _SyntheticNativeModel()
            return m

        suffix = self.config.get("fingerprint_suffix", "none")
        dit = models.acquire(
            key="native/dit/fake.safetensors",
            fingerprint=f"fake.safetensors|bf16|{suffix}",
            loader=make_dit,
        )
        vae = models.acquire(
            key="native/vae/fake.safetensors",
            fingerprint="fake.safetensors|bf16",
            loader=make_vae,
        )
        bundle = _FakeBundle(dit=dit, vae=vae)
        return PipeOutput(output={"model": bundle})


class _FakeGeneratorPipe(BasePipe):
    name = "fake_generator"
    description = "test-only generator, mirrors BaseGeneratorPipe's ctx pattern"

    @classmethod
    def get_default_config(cls):
        return {}

    @classmethod
    def configuration(cls):
        return []

    @classmethod
    def inputs(cls):
        return [PipeInputSpec(name="model", io_type=IOType.MODEL, required=True)]

    @classmethod
    def outputs(cls):
        return [PipeOutputSpec(name="result", io_type=IOType.TEXT)]

    def process(self, pipe_input: PipeInput, generation_outputs: callable, is_cancelled=None) -> PipeOutput:
        bundle = pipe_input.input["model"]
        # Exactly _LTXCtx's shape: bundle + a separately-dereferenced field.
        ctx = _FakeGeneratorCtx(bundle=bundle, vae=bundle.vae)

        # Mirror generate_one's `dit_module = c.bundle.dit.module` + a
        # model_forward-style closure capturing it, run across "multiple
        # seeds" (quantity>1) the way a real video generation loops.
        for _ in range(3):
            dit_module = ctx.bundle.dit.module

            def model_forward(x, _dm=dit_module):
                return _dm(x)

            _ = model_forward(torch.zeros(1, 4))

        return PipeOutput(output={"result": "ok"})


_PIPE_CLASSES = {"fake_model_loader": _FakeModelLoaderPipe, "fake_generator": _FakeGeneratorPipe}


def _build_manager():
    models = ModelLifecycleManager()
    pipe_catalog = Mock()
    pipe_catalog.get_pipe.side_effect = lambda name: _PIPE_CLASSES[name]
    manager = GenerationManager(
        gpu=Mock(), model_manager=Mock(), pipe_catalog=pipe_catalog,
        settings_manager=Mock(), system_monitor=Mock(), memory_manager=Mock(),
        llm_service=Mock(), models=models,
    )
    return manager, models


def _run(manager: GenerationManager, generation_id: str, fingerprint_suffix: str = "none"):
    pipes = [
        {"name": "fake_model_loader", "id": "loader", "enabled": True, "input": [],
         "config": {"fingerprint_suffix": fingerprint_suffix}},
        {"name": "fake_generator", "id": "gen", "enabled": True,
         "input": [["model", "loader", "model"]], "config": {}},
    ]
    manager.generate(pipes, lambda o: None, generation_id=generation_id, cache_owner="preset-1")


class TestModelCacheGarbageCollection:
    def test_weakref_dead_after_invalidate_following_a_generation(self):
        """The DiT loaded by one generation must be collectable once the
        generation ends AND the cache entry is explicitly invalidated (the
        real "Clear VRAM & Cache (RAM)" action) -- reproduces the maintainer's
        report using the REAL generate()/acquire()/WeakModelRef-bundle code
        path, with a ctx shaped exactly like _LTXCtx (bundle + a separately
        dereferenced component field) and per-seed closures capturing the
        raw nn.Module, mirroring generate_one's `dit_module = c.bundle.dit.module`.
        """
        manager, models = _build_manager()
        _run(manager, "gen-1")

        ref = _captured["dit_ref"]
        gc.collect()
        assert ref() is not None, "sanity: the cache itself should still hold it"

        models.invalidate(None)
        gc.collect()
        assert ref() is None, (
            "invalidate() ('Clear VRAM & Cache (RAM)') did not free the model after "
            "the generation completed -- something is still holding a strong reference"
        )

    def test_fingerprint_bust_between_generations_does_not_double_load(self):
        """A second generation on the SAME cache with a DIFFERENT DiT
        fingerprint (e.g. the upscale mode's independent LoRA-tab form
        producing a different active-LoRA set than the prior txt2vid
        generation) must free the FIRST generation's DiT once its own
        generation has ended, not hold both resident at once."""
        manager, models = _build_manager()
        _run(manager, "gen-A", fingerprint_suffix="none")
        ref_a = _captured["dit_ref"]
        gc.collect()
        assert ref_a() is not None

        _run(manager, "gen-B", fingerprint_suffix="SOME_LORA@0.8")
        gc.collect()

        assert ref_a() is None, (
            "generation A's DiT is still alive after generation B's fingerprint-bust "
            "reload -- this is the +26GB-class double-residency incident: the cache "
            "correctly evicts the old entry on a fingerprint mismatch, but something "
            "outside the cache is still holding the actual NativeModel/nn.Module"
        )
        assert models.stats()["entries"] == 2  # gen-B's fresh dit + the (unchanged, shared) vae entry
