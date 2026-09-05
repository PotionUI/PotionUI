"""In-place LoRA reconciliation must invalidate the trajectory warm-start cache.

The Flux and Krea-2 loaders key their DiT cache entry on ``path|dtype`` only, so
a changed LoRA stack is a cache HIT whose weights are then reconciled IN PLACE.
``id(module)`` is unchanged across that, which is why the warm-start key also
carries ``NativeModel.weight_revision`` — without it a resumed trajectory can be
half-computed under one adapter stack and finished under another.

These drive the real ``_sync_loras`` and the real ``NativeGenerator._plan_warm_start``;
only the weight I/O (reading and patching LoRA tensors) is faked.
"""

from types import SimpleNamespace

import pytest
import torch

from src.platform.runtime.native.engine import NativeGenerator, NativeModel
from src.platform.runtime.native.sampling.trajectory_cache import get_trajectory_cache
from src.pipelines.pipes.model_loader.flux.main import ModelLoaderFluxPipe
from src.pipelines.pipes.model_loader.krea2.main import ModelLoaderKrea2Pipe

_LORA_A = {"file_path": "a.safetensors", "weight": 0.8}


@pytest.fixture(autouse=True)
def _clean_cache():
    get_trajectory_cache().clear()
    yield
    get_trajectory_cache().clear()


@pytest.fixture
def fake_lora_io(monkeypatch):
    """Replace the two weight-touching calls in ``_sync_loras`` with recorders."""
    calls = {"removed": 0, "applied": []}

    def _remove(module):
        calls["removed"] += 1

    def _apply(dit_model, loras):
        calls["applied"].append(list(loras))

    for pipe, module_path in (
        (ModelLoaderFluxPipe, "src.pipelines.pipes.model_loader.flux.main"),
        (ModelLoaderKrea2Pipe, "src.pipelines.pipes.model_loader.krea2.main"),
    ):
        monkeypatch.setattr(f"{module_path}._remove_loras", _remove)
        monkeypatch.setattr(pipe, "_apply_loras", staticmethod(_apply))
    return calls


def _cached_dit(lora_fp: str = "none") -> NativeModel:
    """A DiT wrapper standing in for a MODELS cache HIT already stamped ``lora_fp``."""
    dit = NativeModel("diffusion_model", object())
    dit._active_lora_fp = lora_fp
    return dit


def _generator(dit: NativeModel) -> SimpleNamespace:
    return SimpleNamespace(
        spec=SimpleNamespace(family="flux", variant="dev", sampling_settings={}),
        dit=dit,
    )


def _resume_step(gen, cond):
    """Plan a warm start and return the step it would resume at, or ``None`` (cold)."""
    resume, _ = NativeGenerator._plan_warm_start(
        gen, True, "euler", None, cond, None, 1234, (1, 4, 4, 4), 8, None, (),
        {"guidance": None, "shift": 2.02}, {}, None, None, sigmas=None,
    )
    return resume[0] if resume is not None else None


def _prime(gen, cond) -> None:
    """One cold plan, then fill the entry it created as a finished run would."""
    assert _resume_step(gen, cond) is None
    cache = get_trajectory_cache()
    entry = cache.get(next(iter(cache._entries)))
    entry.checkpoints = {6: torch.full((1, 4, 4, 4), 2.0)}


def test_unchanged_stack_still_resumes(fake_lora_io):
    """(d) Same stack, same fingerprint: a pure no-op, reuse retained."""
    dit = _cached_dit("a.safetensors@0.8")
    gen = _generator(dit)
    cond = {"context": torch.randn(1, 4, 8)}
    _prime(gen, cond)
    revision = dit.weight_revision

    ModelLoaderFluxPipe._sync_loras(dit, [_LORA_A], "a.safetensors@0.8")

    assert dit.weight_revision == revision
    assert fake_lora_io["removed"] == 0
    assert _resume_step(gen, cond) == 6


@pytest.mark.parametrize("before,after,loras", [
    ("none", "a.safetensors@0.8", [_LORA_A]),                          # (a) added
    ("a.safetensors@0.8", "none", []),                                 # (b) removed
    ("a.safetensors@0.8", "a.safetensors@0.5",                         # (c) rescaled
     [{"file_path": "a.safetensors", "weight": 0.5}]),
])
def test_adapter_change_on_a_cache_hit_dit_forces_a_cold_start(fake_lora_io, before, after, loras):
    dit = _cached_dit(before)
    gen = _generator(dit)
    cond = {"context": torch.randn(1, 4, 8)}
    _prime(gen, cond)
    assert _resume_step(gen, cond) == 6  # unrevised weights: the trajectory is reusable
    revision = dit.weight_revision

    ModelLoaderFluxPipe._sync_loras(dit, loras, after)

    assert dit.weight_revision > revision
    assert _resume_step(gen, cond) is None


def test_failed_reconciliation_revises_and_leaves_the_stamp_stale(monkeypatch):
    """(e) A half-applied stack must not keep serving the old trajectory.

    The loader's rollback contract is unchanged: ``_active_lora_fp`` is NOT
    advanced past a raising ``_apply_loras``, so the next call retries the whole
    reconciliation.
    """
    monkeypatch.setattr("src.pipelines.pipes.model_loader.flux.main._remove_loras", lambda m: None)

    def _boom(dit_model, loras):
        raise RuntimeError("unreadable LoRA")

    monkeypatch.setattr(ModelLoaderFluxPipe, "_apply_loras", staticmethod(_boom))

    dit = _cached_dit("none")
    gen = _generator(dit)
    cond = {"context": torch.randn(1, 4, 8)}
    _prime(gen, cond)
    revision = dit.weight_revision

    with pytest.raises(RuntimeError, match="unreadable LoRA"):
        ModelLoaderFluxPipe._sync_loras(dit, [_LORA_A], "a.safetensors@0.8")

    assert dit.weight_revision > revision
    assert dit._active_lora_fp == "none"
    assert _resume_step(gen, cond) is None


def test_returning_to_the_original_stack_does_not_revive_the_stale_entry(fake_lora_io):
    """(f) Revisions are monotonic: add-then-remove is not a rollback."""
    dit = _cached_dit("none")
    gen = _generator(dit)
    cond = {"context": torch.randn(1, 4, 8)}
    _prime(gen, cond)

    ModelLoaderFluxPipe._sync_loras(dit, [_LORA_A], "a.safetensors@0.8")
    ModelLoaderFluxPipe._sync_loras(dit, [], "none")

    assert dit._active_lora_fp == "none"  # same stack as when the entry was primed
    assert _resume_step(gen, cond) is None


def test_krea2_sync_loras_revises_like_flux(fake_lora_io):
    dit = _cached_dit("none")
    revision = dit.weight_revision

    ModelLoaderKrea2Pipe._sync_loras(dit, [_LORA_A], "a.safetensors@0.8")

    assert dit.weight_revision > revision
    assert dit._active_lora_fp == "a.safetensors@0.8"


def test_krea2_step_window_change_revises_without_touching_the_module():
    """A windowed LoRA is never baked in, but the sampler toggles it mid-run."""
    dit = _cached_dit("none")
    gen = _generator(dit)
    cond = {"context": torch.randn(1, 4, 8)}
    ModelLoaderKrea2Pipe._sync_lora_windows(dit, "turbo.safetensors@1.0[1:2]")
    _prime(gen, cond)
    revision = dit.weight_revision

    ModelLoaderKrea2Pipe._sync_lora_windows(dit, "turbo.safetensors@1.0[1:2]")
    assert dit.weight_revision == revision
    assert _resume_step(gen, cond) == 6

    ModelLoaderKrea2Pipe._sync_lora_windows(dit, "turbo.safetensors@1.0[1:4]")
    assert dit.weight_revision > revision
    assert _resume_step(gen, cond) is None
