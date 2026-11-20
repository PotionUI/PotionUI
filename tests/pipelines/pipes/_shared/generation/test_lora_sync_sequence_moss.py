from __future__ import annotations

import pytest
import torch

from src.platform.runtime.native.arch.moss_image.config import MossImageConfig
from src.platform.runtime.native.arch.moss_image.model import MossImage
from src.platform.runtime.native.engine import NativeModel
from src.platform.runtime.native.kernels import pick_operations
from src.pipelines.pipes._shared.generation import loader_helpers
from src.pipelines.pipes._shared.generation.loader_helpers import apply_loras_to, lora_stack_fingerprint
from src.pipelines.pipes._shared.generation.loader_lifecycle import NO_LORAS, sync_loras
from src.platform.runtime.native.lora import apply as lora_apply

DIM = 16
CFG = MossImageConfig(
    dim=DIM, heads=2, head_dim=8, ffn_dim=24, depth=2, text_dim=12,
    in_channels=4, freq_dim=8, rope_base=10000.0,
)
X = "/m/x.safetensors"
Y = "/m/y.safetensors"
STEMS = (
    ("diffusion_model.layers.0.proj.q", DIM, DIM),
    ("diffusion_model.layers.0.proj.v", DIM, DIM),
    ("diffusion_model.layers.1.proj.ffn_up_gate", 48, DIM),
)


def _lora(seed: int) -> dict:
    gen = torch.Generator().manual_seed(seed)
    sd = {}
    for stem, out, inf in STEMS:
        sd[f"{stem}.lora_up.weight"] = torch.randn(out, 4, generator=gen) * 0.1
        sd[f"{stem}.lora_down.weight"] = torch.randn(4, inf, generator=gen) * 0.1
        sd[f"{stem}.alpha"] = torch.tensor(4.0)
    return sd


@pytest.fixture
def files(monkeypatch):
    table = {X: _lora(1), Y: _lora(2)}
    monkeypatch.setattr(loader_helpers, "load_torch_file", lambda path, device="cpu": (table[path], {}))
    monkeypatch.setattr(loader_helpers, "file_identity", lambda path: (path, 1, 1))
    return table


def _model(dtype=torch.float32) -> NativeModel:
    module = MossImage(CFG, operations=pick_operations(dtype, dtype))
    with torch.no_grad():
        for p in module.parameters():
            p.copy_(torch.randn_like(p) * 0.02)
    module.eval()
    model = NativeModel("diffusion_model", module)
    model._active_lora_fp = NO_LORAS
    return model


def _probe(model: NativeModel) -> torch.Tensor:
    outs = []
    gen = torch.Generator().manual_seed(5)
    for layer, out_dim in ((0, 3 * DIM), (1, 2 * 24)):
        proj = model.module.layers[layer].proj
        name = "qkv" if layer == 0 else "ffn_up_gate"
        lin = proj[name]
        x = torch.randn(3, DIM, generator=gen).to(lin.weight.dtype)
        with torch.no_grad():
            outs.append(lin(x).float())
    return torch.cat(outs, dim=-1)


def _sync(model, loras):
    entries = [{"file_path": p, "weight": w} for p, w in loras]
    fp = lora_stack_fingerprint(entries) or NO_LORAS
    sync_loras(model, entries, fp, lambda m, l: apply_loras_to(m, l, "TEST"))


MODES = ("inplace", "runtime")


@pytest.fixture(params=MODES)
def mode(request, monkeypatch):
    if request.param == "runtime":
        monkeypatch.setattr(lora_apply, "_needs_runtime_deltas", lambda linear: True)
    return request.param


def test_stack_sequence_returns_to_base_and_matches_a_fresh_apply(files, mode):
    model = _model()
    base = _probe(model)

    _sync(model, [(X, 1.0)])
    with_x = _probe(model)
    assert not torch.allclose(with_x, base, atol=1e-6)

    _sync(model, [])
    assert torch.allclose(_probe(model), base, atol=1e-6)

    _sync(model, [(Y, 1.0)])
    with_y = _probe(model)
    assert not torch.allclose(with_y, base, atol=1e-6)
    assert not torch.allclose(with_y, with_x, atol=1e-6)

    _sync(model, [(X, 0.5)])
    half = _probe(model)

    _sync(model, [])
    assert torch.allclose(_probe(model), base, atol=1e-6)
    _sync(model, [(X, 0.5)])
    assert torch.allclose(_probe(model), half, atol=1e-6)


def test_strength_only_change_reapplies(files, mode):
    model = _model()
    _sync(model, [(X, 1.0)])
    full = _probe(model)
    _sync(model, [(X, 0.5)])
    half = _probe(model)
    assert not torch.allclose(full, half, atol=1e-6)
    _sync(model, [(X, 1.0)])
    assert torch.allclose(_probe(model), full, atol=1e-6)


def test_unchanged_stack_does_not_reapply(files, mode, monkeypatch):
    model = _model()
    _sync(model, [(X, 1.0)])
    calls = []
    real = lora_apply.apply_loras_with_report
    monkeypatch.setattr(loader_helpers, "apply_loras_with_report", lambda *a, **k: calls.append(1) or real(*a, **k))
    _sync(model, [(X, 1.0)])
    assert calls == []


def test_reordered_stack_is_a_new_request(files, mode):
    model = _model()
    _sync(model, [(X, 1.0), (Y, 1.0)])
    first = model._active_lora_fp
    _sync(model, [(Y, 1.0), (X, 1.0)])
    assert model._active_lora_fp != first
    _sync(model, [])
    assert model._active_lora_fp == NO_LORAS


def test_bf16_cycles_do_not_accumulate_drift(files):
    model = _model(torch.bfloat16)
    base = _probe(model)
    qkv = model.module.layers[0].proj["qkv"].weight
    before = qkv.detach().clone()
    for _ in range(6):
        _sync(model, [(X, 1.0)])
        _sync(model, [(Y, 0.7)])
        _sync(model, [])
    drift = (qkv.float() - before.float()).abs().max().item()
    assert drift <= 2 * torch.finfo(torch.bfloat16).eps * before.abs().max().item()
    assert torch.allclose(_probe(model), base, atol=5e-2)
