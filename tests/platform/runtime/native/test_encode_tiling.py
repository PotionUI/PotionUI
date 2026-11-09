from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import numpy as np
import pytest
import torch
import torch.nn.functional as F

import src.platform.runtime.native.engine as engine_mod
from src.platform.runtime.native.engine import (
    NativeGenerator,
    _MIN_ENCODE_TILE_PX,
    _causal3d_encode_tile,
)
from src.platform.runtime.native.kernels import disable_weight_init
from src.platform.runtime.native.vae import causal_3d
from src.platform.runtime.native.vae.causal_3d import LATENTS_MEAN, LATENTS_STD, AutoEncoderCausal3D
from src.platform.runtime.native.vae.tiling import tiled_encode_causal3d


class _PoolVae(torch.nn.Module):
    def __init__(self, factor: int = 8, channels: int = 16):
        super().__init__()
        self.factor = factor
        self.proj = torch.nn.Conv3d(3, channels, 1)
        self.encode_shapes: list[tuple[int, ...]] = []

    def encode(self, pixels: torch.Tensor) -> torch.Tensor:
        self.encode_shapes.append(tuple(pixels.shape))
        return self.proj(F.avg_pool3d(pixels, (1, self.factor, self.factor)))

    def encode_image(self, pixels: torch.Tensor) -> torch.Tensor:
        return self.encode(pixels.unsqueeze(2)).squeeze(2)

    def decode_image(self, latent: torch.Tensor) -> torch.Tensor:
        return latent


def _tiny_causal3d_vae() -> AutoEncoderCausal3D:
    with mock.patch.object(causal_3d, "_DIM", 8):
        module = AutoEncoderCausal3D.from_config({}, disable_weight_init)
    module.eval()
    with torch.no_grad():
        for p in module.parameters():
            if p.is_floating_point():
                p.normal_(std=0.02)
    return module


def _gen(module, *, latent_format=None, vae_device="cuda:0", vae_gb=0.25):
    gen = object.__new__(NativeGenerator)
    gen.placement = None
    gen.spec = SimpleNamespace(latent_format=dict(latent_format or {"latent_channels": 16}))
    gen.vae = SimpleNamespace(module=module, estimated_vram_gb=vae_gb, device=vae_device)
    return gen


def _shape_only(h: int, w: int) -> torch.Tensor:
    return torch.empty((1, 3, h, w), device="meta")


def test_tile_for_krea2_enhance_2048_on_a_12gb_card():
    tile, overlap = _causal3d_encode_tile(10.0, (2048, 2048), 8)
    assert tile == 880
    assert overlap == 104
    assert tile % 8 == 0 and overlap % 8 == 0
    assert overlap < tile


def test_tile_is_capped_at_half_the_long_axis_when_vram_is_plentiful():
    tile, _ = _causal3d_encode_tile(80.0, (2048, 1024), 8)
    assert tile == 1024


def test_tile_floors_when_almost_nothing_is_free():
    tile, overlap = _causal3d_encode_tile(0.0, (2048, 2048), 8)
    assert tile == _MIN_ENCODE_TILE_PX
    assert 0 < overlap < tile


def test_tile_honours_the_wan22_sixteen_pixel_grid():
    for free in (0.5, 3.0, 7.7, 10.0, 20.0):
        tile, overlap = _causal3d_encode_tile(free, (1504, 2048), 16)
        assert tile % 16 == 0
        assert overlap % 16 == 0
        assert overlap < tile


def test_tile_respects_an_explicit_ceiling():
    tile, _ = _causal3d_encode_tile(10.0, (2048, 2048), 8, max_tile=445)
    assert tile == 440


def test_tile_shrinks_as_free_vram_shrinks():
    sizes = [_causal3d_encode_tile(free, (4096, 4096), 8)[0] for free in (12.0, 8.0, 4.0, 2.0)]
    assert sizes == sorted(sizes, reverse=True)
    assert sizes[0] > sizes[-1]


def test_krea2_enhance_2048_takes_the_tiled_path(monkeypatch):
    monkeypatch.setattr(engine_mod, "free_vram_gb", lambda device: 10.5)
    gen = _gen(_PoolVae())
    tile = gen._causal3d_encode_tile_for(_shape_only(2048, 2048), "cuda:0")
    assert tile is not None
    assert tile[0] < 2048


def test_a_1024_source_keeps_the_untiled_path(monkeypatch):
    monkeypatch.setattr(engine_mod, "free_vram_gb", lambda device: 10.5)
    gen = _gen(_PoolVae())
    assert gen._causal3d_encode_tile_for(_shape_only(1024, 1024), "cuda:0") is None


def test_force_tiles_even_when_the_estimate_fits(monkeypatch):
    monkeypatch.setattr(engine_mod, "free_vram_gb", lambda device: 10.5)
    gen = _gen(_PoolVae())
    tile = gen._causal3d_encode_tile_for(_shape_only(1024, 1024), "cuda:0", force=True, max_tile=256)
    assert tile is not None
    assert tile[0] == 256


def test_no_tiling_decision_on_cpu(monkeypatch):
    monkeypatch.setattr(engine_mod, "free_vram_gb", lambda device: (_ for _ in ()).throw(AssertionError))
    gen = _gen(_PoolVae(), vae_device="cpu")
    assert gen._causal3d_encode_tile_for(_shape_only(2048, 2048), "cpu") is None


def test_no_tiling_decision_when_vram_is_unknown(monkeypatch):
    monkeypatch.setattr(engine_mod, "free_vram_gb", lambda device: None)
    gen = _gen(_PoolVae())
    assert gen._causal3d_encode_tile_for(_shape_only(2048, 2048), "cuda:0") is None


def test_the_2d_autoencoder_never_takes_the_causal3d_tiling(monkeypatch):
    monkeypatch.setattr(engine_mod, "free_vram_gb", lambda device: 0.5)
    gen = _gen(SimpleNamespace(encode=lambda x: x))
    assert gen._causal3d_encode_tile_for(_shape_only(2048, 2048), "cuda:0") is None


def test_encode_need_does_not_count_vae_weights_already_on_the_gpu():
    pixels = _shape_only(1024, 1024)
    on_gpu = _gen(_PoolVae(), vae_device="cuda:0", vae_gb=4.0)
    on_gpu.placement = SimpleNamespace(vae=SimpleNamespace(resident=False))
    on_cpu = _gen(_PoolVae(), vae_device="cpu", vae_gb=4.0)
    on_cpu.placement = SimpleNamespace(vae=SimpleNamespace(resident=False))
    assert on_cpu._encode_need_gb(pixels) == pytest.approx(on_gpu._encode_need_gb(pixels) + 4.0)


def test_tiled_engine_encode_matches_untiled_on_a_local_vae():
    torch.manual_seed(0)
    vae = _PoolVae()
    gen = _gen(vae)
    pixels = torch.rand(1, 3, 96, 160) * 2.0 - 1.0
    with torch.no_grad():
        whole = gen._encode_causal3d(vae, pixels, "cpu")
        vae.encode_shapes.clear()
        tiled = gen._encode_causal3d(vae, pixels, "cpu", (48, 16))
    assert len(vae.encode_shapes) > 1
    assert all(s[-2] <= 48 and s[-1] <= 48 for s in vae.encode_shapes)
    assert tiled.shape == whole.shape == (1, 16, 1, 12, 20)
    assert torch.allclose(tiled, whole, atol=1e-5)


def test_tiled_engine_encode_matches_untiled_on_a_wan22_grid():
    torch.manual_seed(0)
    vae = _PoolVae(factor=16, channels=48)
    lf = {
        "latent_channels": 48, "format": "wan22", "spatial_downscale": 16,
        "latents_mean": [0.0] * 48, "latents_std": [1.0] * 48,
    }
    gen = _gen(vae, latent_format=lf)
    pixels = torch.rand(1, 3, 128, 192) * 2.0 - 1.0
    with torch.no_grad():
        whole = gen._encode_causal3d(vae, pixels, "cpu")
        tiled = gen._encode_causal3d(vae, pixels, "cpu", (64, 32))
    assert tiled.shape == whole.shape == (1, 48, 1, 8, 12)
    assert torch.allclose(tiled, whole, atol=1e-5)


def test_tiled_engine_encode_keeps_seedvr2_self_normalized():
    torch.manual_seed(0)
    vae = _PoolVae()
    gen = _gen(vae, latent_format={"latent_channels": 16, "format": "seedvr2"})
    pixels = torch.rand(1, 3, 64, 96) * 2.0 - 1.0
    with torch.no_grad():
        whole = gen._encode_causal3d(vae, pixels, "cpu")
        tiled = gen._encode_causal3d(vae, pixels, "cpu", (32, 8))
    assert torch.allclose(whole, vae.encode(pixels.unsqueeze(2)), atol=1e-6)
    assert torch.allclose(tiled, whole, atol=1e-5)


def test_tiled_engine_encode_stays_close_on_the_real_causal3d_vae():
    torch.manual_seed(0)
    vae = _tiny_causal3d_vae()
    gen = _gen(vae)
    pixels = torch.rand(1, 3, 64, 64) * 2.0 - 1.0
    with torch.no_grad():
        whole = gen._encode_causal3d(vae, pixels, "cpu")
        tiled = gen._encode_causal3d(vae, pixels, "cpu", (32, 8))
    std = torch.tensor(LATENTS_STD).view(1, -1, 1, 1, 1)
    assert tiled.shape == whole.shape == (1, 16, 1, 8, 8)
    assert torch.isfinite(tiled).all()
    assert torch.allclose(tiled * std, whole * std, atol=1e-2)
    raw = vae.encode(pixels.unsqueeze(2))
    mean = torch.tensor(LATENTS_MEAN).view(1, -1, 1, 1, 1)
    assert torch.allclose(whole, (raw - mean) / std, atol=1e-6)


def test_tiled_encode_causal3d_accepts_a_sixteen_pixel_scale():
    torch.manual_seed(0)
    vae = _PoolVae(factor=16)
    pixels = torch.rand(1, 3, 1, 128, 160) * 2.0 - 1.0
    with torch.no_grad():
        whole = vae.encode(pixels)
        tiled = tiled_encode_causal3d(vae, pixels, tile_size=64, overlap=32, scale=16)
    assert tiled.shape == whole.shape == (1, 16, 1, 8, 10)
    assert torch.allclose(tiled, whole, atol=1e-5)


def _wired_gen(tiles, dispatch):
    gen = object.__new__(NativeGenerator)
    gen.device_plan = SimpleNamespace(vae_device="cpu")
    gen.vae = SimpleNamespace(compute_dtype=torch.float32, move_to=lambda d: None, offload=lambda: None)
    gen._ensure_room_for_encode = lambda pixels, device: None
    gen.tile_requests = []

    def tile_for(pixels, device, *, force=False, max_tile=None):
        gen.tile_requests.append((force, max_tile))
        return tiles.pop(0)

    gen._causal3d_encode_tile_for = tile_for
    gen._encode_dispatch = dispatch
    gen._free_for_decode_retry = lambda device: None
    return gen


def test_encode_image_hands_the_chosen_tile_to_the_dispatch():
    seen = []

    def dispatch(pixels, device, vram, tile=None):
        seen.append(tile)
        return torch.zeros(1, 16, 1, 8, 8)

    gen = _wired_gen([(256, 32)], dispatch)
    gen.encode_image(np.zeros((64, 64, 3), dtype=np.uint8))
    assert seen == [(256, 32)]
    assert gen.tile_requests == [(False, None)]


def test_encode_image_retry_forces_a_halved_tile():
    seen = []

    def dispatch(pixels, device, vram, tile=None):
        seen.append(tile)
        if len(seen) == 1:
            raise RuntimeError("CUDA driver error: device not ready")
        return torch.zeros(1, 16, 1, 8, 8)

    gen = _wired_gen([(512, 64), (256, 32)], dispatch)
    gen.encode_image(np.zeros((64, 64, 3), dtype=np.uint8))
    assert seen == [(512, 64), (256, 32)]
    assert gen.tile_requests == [(False, None), (True, 256)]


def test_encode_image_retry_after_an_untiled_oom_forces_tiling():
    def dispatch(pixels, device, vram, tile=None):
        if tile is None:
            raise torch.cuda.OutOfMemoryError("boom")
        return torch.zeros(1, 16, 1, 8, 8)

    gen = _wired_gen([None, (384, 48)], dispatch)
    gen.encode_image(np.zeros((64, 64, 3), dtype=np.uint8))
    assert gen.tile_requests == [(False, None), (True, None)]
