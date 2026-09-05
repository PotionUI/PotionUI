"""Recovery from a failed in-place LoRA reconciliation on a cache-HIT DiT.

The Flux and Krea-2 loaders key their DiT cache entry on ``path|dtype`` only, so
a changed LoRA stack is reconciled IN PLACE on the resident module. A
reconciliation that dies midway therefore leaves real weights in a state no
request describes, and the stamp the next request is compared against is the
only thing standing between that module and a generation nobody asked for.

Everything here drives the pipes' own ``_sync_loras`` (and, for the no-reload
assertions, the whole ``process()``) against a REAL tiny Flux DiT patched by
REAL kohya LoRAs: "rolled back" and "reapplied" are checked by reading the
weight tensor, not by counting calls. Only the LoRA files' disk reads and the
checkpoint load are faked.
"""

from __future__ import annotations

import pytest
import torch

from src.platform.runtime.native.arch.flux.model import Flux
from src.platform.runtime.native.base import load_into_module
from src.platform.runtime.native.detect.registry import match_model_spec
from src.platform.runtime.native.engine import NativeEngineLoader, NativeModel
from src.platform.runtime.native.lora import remove_loras
from src.pipelines.contracts import PipeInput
from src.pipelines.pipes.model_loader.flux.main import ModelLoaderFluxPipe
from src.pipelines.pipes.model_loader.krea2.main import ModelLoaderKrea2Pipe
from vendor.gpl.comfyui.ops import pick_operations

TINY = {
    "image_model": "flux2", "hidden_size": 64, "num_heads": 2, "depth": 1,
    "depth_single_blocks": 1, "in_channels": 16, "out_channels": 16,
    "context_in_dim": 32, "axes_dim": [8, 8, 8, 8], "mlp_ratio": 3.0,
    "theta": 2000, "patch_size": 1, "qkv_bias": False, "guidance_embed": False,
}
# Two targets, so an apply that dies between them is a genuinely PARTIAL one.
QKV = "double_blocks.0.img_attn.qkv"
PROJ = "double_blocks.0.img_attn.proj"

LORA_A = "/m/a.safetensors"
LORA_B = "/m/b.safetensors"


def _build_dit() -> torch.nn.Module:
    m = Flux.from_config(TINY, pick_operations(torch.float32, torch.float32))
    sd = {}
    for k, v in m.state_dict().items():
        if k.endswith(".scale") and "norm" in k:
            sd[k] = torch.ones_like(v)
        elif v.is_floating_point():
            sd[k] = torch.randn_like(v) * 0.05
        else:
            sd[k] = v.clone()
    load_into_module(m, sd, match_model_spec(TINY))
    m.eval()
    return m


def _kohya_lora(seed: int, rank: int = 4, scale: float = 0.1) -> dict:
    """A kohya-dialect LoRA touching BOTH tiny targets."""
    g = torch.Generator().manual_seed(seed)
    sd = {}
    for stem, out_features in (
        ("lora_unet_double_blocks_0_img_attn_qkv", 192),
        ("lora_unet_double_blocks_0_img_attn_proj", 64),
    ):
        sd[f"{stem}.lora_up.weight"] = torch.randn(out_features, rank, generator=g) * scale
        sd[f"{stem}.lora_down.weight"] = torch.randn(rank, 64, generator=g) * scale
        sd[f"{stem}.alpha"] = torch.tensor(float(rank))
    return sd


def _weights(module: torch.nn.Module) -> dict:
    subs = dict(module.named_modules())
    return {name: subs[name].weight.detach().clone() for name in (QKV, PROJ)}


def _same(module: torch.nn.Module, expected: dict) -> bool:
    """Tolerance, not equality: an in-place remove restores to ~1 ulp of storage
    rounding rather than bit-identically (see ``remove_loras``' docstring)."""
    return all(torch.allclose(w, expected[name], atol=1e-6)
               for name, w in _weights(module).items())


@pytest.fixture
def fake_lora_files(monkeypatch):
    """Serve the two in-memory kohya LoRAs by path, so the pipes' real
    ``_apply_loras`` -> ``load_lora_stack`` -> ``apply_loras`` chain runs
    untouched with no file on disk."""
    files = {LORA_A: _kohya_lora(seed=1), LORA_B: _kohya_lora(seed=2)}
    monkeypatch.setattr(
        "src.pipelines.pipes._shared.generation.loader_helpers.load_torch_file",
        lambda path, device="cpu": (files[path], {}),
    )
    return files


@pytest.fixture
def dit() -> NativeModel:
    """A DiT wrapper standing in for a MODELS cache HIT carrying no adapters."""
    model = NativeModel("diffusion_model", _build_dit())
    model._active_lora_fp = "none"
    return model


def _entry(path: str, weight: float = 0.8) -> dict:
    return {"file_path": path, "weight": weight, "window": None}


def _fp(path: str, weight: float = 0.8) -> str:
    return f"{path}@{weight}"


def _fail_second_apply(mpatch) -> list:
    """Let the real in-place patch land on the FIRST target Linear, then raise —
    the half-applied stack the recovery contract exists for."""
    from src.platform.runtime.native.lora import apply as apply_mod

    real = apply_mod._apply_inplace
    patched: list = []

    def _boom(linear, deltas, pool=None):
        if patched:
            raise RuntimeError("unreadable LoRA tensor")
        real(linear, deltas, pool)
        patched.append(linear)

    mpatch.setattr(apply_mod, "_apply_inplace", _boom)
    return patched


def _always_fails(module):
    raise RuntimeError("removal is impossible")


# -- a partial apply, and the stack the module came from ---------------------

@pytest.mark.parametrize("pipe", [ModelLoaderFluxPipe, ModelLoaderKrea2Pipe])
def test_partial_apply_rolls_back_and_lets_the_previous_stack_be_reapplied(pipe, dit, fake_lora_files):
    """A -> failing B -> A. The bug this pins: B's failure used to leave the
    stamp reading A, so the request for A that follows is a no-op comparison
    and samples the half-applied weights."""
    base = _weights(dit.module)
    pipe._sync_loras(dit, [_entry(LORA_A)], _fp(LORA_A))
    with_a = _weights(dit.module)
    assert not _same(dit.module, base)

    revisions = (dit.weight_revision, dit.effective_revision)
    with pytest.MonkeyPatch.context() as failing:
        patched = _fail_second_apply(failing)
        with pytest.raises(RuntimeError, match="unreadable LoRA tensor"):
            pipe._sync_loras(dit, [_entry(LORA_B)], _fp(LORA_B))

    assert len(patched) == 1, "expected a genuinely partial apply (one of two leaves)"
    assert _same(dit.module, base), "a failed reconciliation must leave the base weights"
    assert dit._active_lora_fp == "none"
    assert dit.weight_revision > revisions[0]
    assert dit.effective_revision > revisions[1]

    pipe._sync_loras(dit, [_entry(LORA_A)], _fp(LORA_A))
    assert _same(dit.module, with_a), "the previous stack must come back in full"


@pytest.mark.parametrize("pipe", [ModelLoaderFluxPipe, ModelLoaderKrea2Pipe])
def test_partial_apply_rolls_back_and_lets_the_requested_stack_be_applied(pipe, dit, fake_lora_files):
    """A -> failing B -> B: the retry must build B on the base weights, not on
    top of the leaf the failed attempt already patched."""
    base = _weights(dit.module)
    pipe._sync_loras(dit, [_entry(LORA_B)], _fp(LORA_B))
    with_b = _weights(dit.module)
    pipe._sync_loras(dit, [_entry(LORA_A)], _fp(LORA_A))

    with pytest.MonkeyPatch.context() as failing:
        _fail_second_apply(failing)
        with pytest.raises(RuntimeError, match="unreadable LoRA tensor"):
            pipe._sync_loras(dit, [_entry(LORA_B)], _fp(LORA_B))
    assert _same(dit.module, base)

    pipe._sync_loras(dit, [_entry(LORA_B)], _fp(LORA_B))
    assert _same(dit.module, with_b)
    assert dit._active_lora_fp == _fp(LORA_B)


# -- a partial removal -------------------------------------------------------

def test_partial_removal_failure_is_completed_by_the_rollback(dit, fake_lora_files):
    """The removal half can die midway too: one leaf stripped, one still
    carrying A. The rollback's own removal finishes the job."""
    base = _weights(dit.module)
    ModelLoaderFluxPipe._sync_loras(dit, [_entry(LORA_A)], _fp(LORA_A))
    with_a = _weights(dit.module)

    calls = []

    def _flaky_remove(module):
        calls.append(module)
        if len(calls) == 1:
            remove_loras(dict(module.named_modules())[PROJ])
            raise RuntimeError("removal died midway")
        remove_loras(module)

    with pytest.MonkeyPatch.context() as failing:
        failing.setattr(
            "src.pipelines.pipes._shared.generation.loader_lifecycle.remove_loras", _flaky_remove,
        )
        with pytest.raises(RuntimeError, match="removal died midway"):
            ModelLoaderFluxPipe._sync_loras(dit, [_entry(LORA_B)], _fp(LORA_B))

    assert len(calls) == 2, "the rollback must attempt its own removal"
    assert _same(dit.module, base)
    assert dit._active_lora_fp == "none"
    assert dit.unusable_reason is None

    ModelLoaderFluxPipe._sync_loras(dit, [_entry(LORA_A)], _fp(LORA_A))
    assert _same(dit.module, with_a)


# -- nothing left to roll back to --------------------------------------------

class _FakeModels:
    """Real hit/miss cache semantics keyed on (key, fingerprint), plus the
    ``evict_dead_weight`` the recovery path drops an unusable entry through."""

    def __init__(self):
        self.loads = 0
        self.evicted = []
        self._entries = {}

    def acquire(self, key, fingerprint, loader, estimated_vram_gb=None):
        entry = self._entries.get(key)
        if entry is not None and entry[0] == fingerprint:
            return entry[1]
        value = loader()
        self._entries[key] = (fingerprint, value)
        return value

    def evict_dead_weight(self, key):
        self.evicted.append(key)
        return self._entries.pop(key, None) is not None


DIT_KEY = "native/dit//m/dit.safetensors"


def _flux_config(loras):
    cfg = ModelLoaderFluxPipe.get_default_config()
    cfg.update({
        "diffusion_model": {"file_path": "/m/dit.safetensors", "name": "dit"},
        "text_encoder": {"file_path": "/m/te.safetensors", "name": "te"},
        "vae": {"file_path": "/m/vae.safetensors", "name": "vae"},
        "loras": [{"model": path, "strength": 0.8} for path in loras],
    })
    return cfg


@pytest.fixture
def fake_checkpoint_load(monkeypatch):
    """Replace the checkpoint read with a fresh real tiny DiT, counting reads —
    the count is what proves an in-place reconciliation never re-read the file."""
    counts = {"diffusion_model": 0, "text_encoder": 0, "vae": 0}

    def _load(self, path, kind, **kwargs):
        counts[kind] = counts.get(kind, 0) + 1
        module = _build_dit() if kind == "diffusion_model" else object()
        return NativeModel(kind, module, estimated_vram_gb=1.0)

    monkeypatch.setattr(NativeEngineLoader, "load", _load)
    return counts


def _run_flux(loras, models):
    pipe = ModelLoaderFluxPipe(config=_flux_config(loras))
    return pipe.process(PipeInput(input={"MODELS": models}), lambda o: None)


def test_an_unrecoverable_reconciliation_evicts_the_entry_and_reloads_next_time(
    fake_lora_files, fake_checkpoint_load,
):
    """When the rollback cannot restore a known state either, the wrapper must
    never be sampled again: it is marked unusable and its cache entry dropped,
    so the next generation re-reads the checkpoint instead of hitting it."""
    models = _FakeModels()
    out = _run_flux([LORA_A], models)
    poisoned = out.output["model"].dit
    assert fake_checkpoint_load["diffusion_model"] == 1

    with pytest.MonkeyPatch.context() as failing:
        failing.setattr(
            "src.pipelines.pipes._shared.generation.loader_lifecycle.remove_loras", _always_fails,
        )
        with pytest.raises(RuntimeError, match="removal is impossible"):
            _run_flux([LORA_B], models)

    assert poisoned.unusable_reason is not None
    assert models.evicted == [DIT_KEY]
    # Even if the entry somehow survived, the wrapper itself refuses.
    with pytest.raises(RuntimeError, match="unusable"):
        ModelLoaderFluxPipe._sync_loras(poisoned, [_entry(LORA_A)], _fp(LORA_A))

    out = _run_flux([LORA_B], models)
    assert out.output["model"].dit is not poisoned
    assert fake_checkpoint_load["diffusion_model"] == 2


# -- the success paths this must not cost ------------------------------------

def test_swap_and_repeat_reconcile_in_place_without_rereading_the_checkpoint(
    fake_lora_files, fake_checkpoint_load,
):
    """The whole point of the LoRA-independent DiT fingerprint: a swap patches
    the resident weights and a repeat touches nothing, both on ONE disk read."""
    models = _FakeModels()
    dit_a = _run_flux([LORA_A], models).output["model"].dit
    with_a = _weights(dit_a.module)

    dit_b = _run_flux([LORA_B], models).output["model"].dit
    assert dit_b is dit_a, "expected a cache HIT reconciled in place"
    assert not _same(dit_b.module, with_a)
    with_b = _weights(dit_b.module)
    revisions = (dit_b.weight_revision, dit_b.effective_revision)

    dit_again = _run_flux([LORA_B], models).output["model"].dit
    assert dit_again is dit_a
    assert _same(dit_again.module, with_b), "a repeat must not re-patch the weights"
    assert (dit_again.weight_revision, dit_again.effective_revision) == revisions
    assert fake_checkpoint_load["diffusion_model"] == 1
