from __future__ import annotations

from types import SimpleNamespace

import pytest
import torch

from src.platform.runtime.native import engine as engine_mod
from src.platform.runtime.native.arch.ltx.model import LTXAVModel
from src.platform.runtime.native.arch.qwen_image21.model import QwenImage21DiT
from src.platform.runtime.native.engine import NativeGenerator
from src.platform.runtime.native.memory import residency
from src.platform.runtime.native.memory.device_plan import DevicePlan
from src.platform.runtime.native.memory.tiering import ref_latents_headroom_gb, sampling_headroom_gb

from ._native_ops import fp32_ops
from .arch.test_ltx_model import TINY as LTX_TINY

QWEN21_LATENT = (1, 64, 1, 67, 120)
QWEN21_TEXT_TOKENS = 105
GIB = 1024 ** 3


class _Spec:
    latent_format: dict = {}
    sampling_settings: dict = {}


class _Qwen21Module:
    patch_size = 1

    def __init__(self):
        self.config = SimpleNamespace(inner_dim=4096)
        self.transformer_blocks = [None] * 32

    def _prefix_kv_bytes(self, batch, prefix_len, itemsize):
        return QwenImage21DiT._prefix_kv_bytes(self, batch, prefix_len, itemsize)

    def run_cache_reserve_gb(self, latents_shape, ref_hw_frames, context_tokens, itemsize):
        return QwenImage21DiT.run_cache_reserve_gb(self, latents_shape, ref_hw_frames, context_tokens, itemsize)


class _Dit:
    kind = "dit"
    spec = _Spec()
    compute_dtype = torch.bfloat16
    quant_format = None
    device = "cpu"

    def __init__(self, gb, module):
        self.estimated_vram_gb = gb
        self.module = module
        self.moved_to: list[str] = []

    def move_to(self, device):
        self.moved_to.append(str(device))
        self.device = str(device)

    def offload(self):
        self.device = "cpu"


class _Vae:
    kind = "vae"
    spec = None
    estimated_vram_gb = 0.63
    module = object()
    device = "cpu"


class _CoResidentTextEncoder:
    def __init__(self):
        self.offloaded = False

    def offload(self):
        self.offloaded = True


def _ref():
    return torch.zeros(QWEN21_LATENT)


def _generator(monkeypatch, *, free_gb, module):
    monkeypatch.setattr(residency.torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(residency.torch.cuda, "empty_cache", lambda: None)
    monkeypatch.setattr(engine_mod.torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(engine_mod.torch.cuda, "empty_cache", lambda: None)
    monkeypatch.setattr(engine_mod, "free_vram_gb", lambda dev: free_gb)
    monkeypatch.setattr(engine_mod, "minimum_inference_memory_gb", lambda: 1.0)
    registry = residency.get_residency_registry()
    registry.clear()
    te = _CoResidentTextEncoder()
    registry.note_resident(te, "cuda:0", 15.17)
    dit = _Dit(6.76, module)
    gen = NativeGenerator(dit, te=object(), vae=_Vae(), device_plan=DevicePlan("cuda:0", "cuda:0", "cuda:0"))
    return gen, dit, te, registry


@pytest.fixture(autouse=True)
def _clear_registry():
    yield
    residency.get_residency_registry().clear()


def test_a_patch_one_dit_reserves_four_times_per_latent_pixel():
    patch2 = sampling_headroom_gb((134, 240))
    patch1 = sampling_headroom_gb((134, 240), patch=1)
    assert patch1 == pytest.approx(4 * patch2)
    refs = [((67, 120), 1)]
    assert ref_latents_headroom_gb(refs, patch=1) == pytest.approx(4 * ref_latents_headroom_gb(refs))


def test_the_same_token_count_reserves_the_same_headroom_whatever_the_vae_factor():
    eight_x_patch_two = sampling_headroom_gb((134, 240))
    sixteen_x_patch_one = sampling_headroom_gb((67, 120), patch=1)
    assert sixteen_x_patch_one == pytest.approx(eight_x_patch_two, rel=0.01)


def test_ltx_keeps_the_calibrated_headroom_despite_its_patch_one_patchifier(monkeypatch):
    with torch.device("meta"):
        module = LTXAVModel.from_config(LTX_TINY, fp32_ops())
    assert module.patch_size == (1, 1, 1)
    gen, *_ = _generator(monkeypatch, free_gb=11.0, module=module)
    base = (1, 128, 16, 15, 26)
    refine = (1, 128, 16, 30, 52)
    assert gen._sampling_headroom_gb(base) == pytest.approx(0.75)
    assert gen._sampling_headroom_gb(refine) == pytest.approx(sampling_headroom_gb((30, 52), latent_frames=16))
    assert gen._sampling_headroom_gb(refine) < 2.5


@pytest.mark.parametrize("patch", [2, (1, 2, 2), None, "x"])
def test_patch_two_and_unknown_patches_keep_the_calibrated_headroom(monkeypatch, patch):
    module = SimpleNamespace(patch_size=patch) if patch is not None else object()
    gen, *_ = _generator(monkeypatch, free_gb=20.0, module=module)
    shape = (1, 16, 1, 134, 240)
    assert gen._sampling_headroom_gb(shape) == pytest.approx(sampling_headroom_gb((134, 240)))


def test_qwen21_prefix_kv_reserve_covers_text_and_reference_tokens():
    module = _Qwen21Module()
    reserve = module.run_cache_reserve_gb(QWEN21_LATENT, [((67, 120), 1)], QWEN21_TEXT_TOKENS, 2)
    expected = 2 * (QWEN21_TEXT_TOKENS + 67 * 120) * 4096 * 2 * 32 / GIB
    assert reserve == pytest.approx(expected)
    assert reserve > 3.5
    assert module.run_cache_reserve_gb(QWEN21_LATENT, [], 0, 2) == 0.0


def test_run_cache_reserve_counts_the_uncond_branch_only_when_cfg_runs_it(monkeypatch):
    gen, *_ = _generator(monkeypatch, free_gb=20.0, module=_Qwen21Module())
    cond = {"context": torch.zeros(1, QWEN21_TEXT_TOKENS, 8), "ref_latents": [_ref()]}
    uncond = {"context": torch.zeros(1, 20, 8), "ref_latents": [_ref()]}
    one = gen._run_cache_reserve_gb(QWEN21_LATENT, cond, uncond, 1.0)
    two = gen._run_cache_reserve_gb(QWEN21_LATENT, cond, uncond, 4.0)
    assert one > 3.5
    assert two > 2 * one - 0.1


def test_run_cache_reserve_is_zero_for_a_dit_without_a_run_cache(monkeypatch):
    gen, *_ = _generator(monkeypatch, free_gb=20.0, module=SimpleNamespace(patch_size=2))
    cond = {"context": torch.zeros(1, 50, 8), "ref_latents": [_ref()]}
    assert gen._run_cache_reserve_gb(QWEN21_LATENT, cond, None, 4.0) == 0.0


def test_qwen21_edit_at_1072x1920_evicts_the_co_resident_text_encoder(monkeypatch):
    gen, dit, te, _ = _generator(monkeypatch, free_gb=10.1, module=_Qwen21Module())
    seen: list[float] = []
    real_move = gen._move_dit_to_gpu

    def spy(device, headroom_gb=0.0):
        seen.append(headroom_gb)
        real_move(device, headroom_gb=headroom_gb)
        raise _Stop

    class _Stop(Exception):
        pass

    monkeypatch.setattr(gen, "_move_dit_to_gpu", spy)
    monkeypatch.setattr(gen, "_build_placement", lambda *a, **k: None)
    cond = {"context": torch.zeros(1, QWEN21_TEXT_TOKENS, 8), "ref_latents": [_ref()]}
    with pytest.raises(_Stop):
        gen.sample(cond, QWEN21_LATENT, steps=1, seed=0, cfg_scale=1.0)

    assert seen and seen[0] > 9.0
    assert te.offloaded is True
    assert dit.moved_to == ["cuda:0"]


def test_qwen21_edit_placement_keeps_the_dit_resident_and_drops_the_text_encoder(monkeypatch):
    gen, *_ = _generator(monkeypatch, free_gb=10.1, module=_Qwen21Module())
    monkeypatch.setattr(gen, "_budget_gb", lambda: 31.37)
    monkeypatch.setattr(gen, "_te_size_gb", lambda: 15.17)
    cond = {"context": torch.zeros(1, QWEN21_TEXT_TOKENS, 8), "ref_latents": [_ref()]}
    reserve = gen._run_cache_reserve_gb(QWEN21_LATENT, cond, None, 1.0)
    plan = gen._build_placement(QWEN21_LATENT, ref_latents=[_ref()], preview_reserve_gb=reserve)
    assert plan.dit.resident is True
    assert plan.text_encoder.resident is False
