"""Engine wiring for temporal chunked decode (NativeGenerator, CPU + fake VAEs).

Covers the WHEN-to-chunk sizing (`_causal3d_chunk_frames`), the decode() routing
(untiled / temporal-chunked / spatial-tiled), engine-level chunked==single
exactness for both wan v1 and v2 VAEs, and the shrink-on-OOM whole-clip restart.

Fake VAEs subclass the real causal-3D classes (so the primitive's ``isinstance``
v1/v2 dispatch is exercised) but override ``decode``/``new_feat_cache`` with a
pure per-frame function, which makes chunked-vs-whole exactness a real property.
``free_vram_gb`` is monkeypatched (CPU reports no VRAM) to drive the sizing math.
"""

from __future__ import annotations

import types

import torch
import pytest

import src.platform.runtime.native.engine as engine
from src.platform.runtime.native.engine import NativeGenerator
from src.platform.runtime.native.vae.causal_3d import AutoEncoderCausal3D
from src.platform.runtime.native.vae.causal_3d_v2 import AutoEncoderCausal3D_2_2


# --- fake VAEs ----------------------------------------------------------------


class _FakeV1(AutoEncoderCausal3D):
    """wan-2.1-shaped causal VAE: pure per-frame 8x upsample decode."""

    def __init__(self) -> None:
        torch.nn.Module.__init__(self)
        self.chunk_sizes: list[int] = []
        self.new_cache_calls = 0

    def new_feat_cache(self) -> list:
        self.new_cache_calls += 1
        return []

    def decode(self, z: torch.Tensor, feat_cache=None) -> torch.Tensor:  # type: ignore[override]
        self.chunk_sizes.append(z.shape[2])
        return z[:, :3].repeat_interleave(8, dim=-1).repeat_interleave(8, dim=-2)

    def decode_image(self, latent: torch.Tensor) -> torch.Tensor:  # marks causal-3D
        z = latent if latent.ndim == 5 else latent.unsqueeze(2)
        return self.decode(z)


class _FakeV2(AutoEncoderCausal3D_2_2):
    """wan-2.2-shaped causal VAE: records the ``first_chunk`` flag per call."""

    def __init__(self) -> None:
        torch.nn.Module.__init__(self)
        self.first_chunk_flags: list[bool] = []

    def new_feat_cache(self) -> list:
        return []

    def decode(self, z: torch.Tensor, feat_cache=None, first_chunk: bool = True) -> torch.Tensor:  # type: ignore[override]
        self.first_chunk_flags.append(first_chunk)
        return z[:, :3].repeat_interleave(8, dim=-1).repeat_interleave(8, dim=-2)

    def decode_image(self, latent: torch.Tensor) -> torch.Tensor:
        z = latent if latent.ndim == 5 else latent.unsqueeze(2)
        return self.decode(z)


class _SelfNormVae:
    """SeedVR2-shaped: causal-3D (has decode_image) but NO new_feat_cache."""

    def decode_image(self, latent):
        return latent


def _gen(vae_module, *, latent_format=None, estimated_vram_gb=0.3):
    gen = NativeGenerator.__new__(NativeGenerator)
    gen.vae = types.SimpleNamespace(
        module=vae_module, estimated_vram_gb=estimated_vram_gb, compute_dtype=torch.float32,
        move_to=lambda d: None, offload=lambda: None,
    )
    gen.spec = types.SimpleNamespace(latent_format=latent_format or {})
    gen.placement = None
    gen.device_plan = types.SimpleNamespace(vae_device="cpu")
    return gen


# --- _causal3d_chunk_frames: WHEN to chunk ------------------------------------


def test_chunk_frames_none_for_self_normalizing_vae(monkeypatch):
    monkeypatch.setattr(engine, "free_vram_gb", lambda d: 8.0)
    gen = _gen(_SelfNormVae(), latent_format={"format": "seedvr2"})
    latent = torch.randn(1, 16, 5, 32, 32)
    assert gen._causal3d_chunk_frames(latent, "cuda:0") is None


def test_chunk_frames_none_without_feat_cache(monkeypatch):
    monkeypatch.setattr(engine, "free_vram_gb", lambda d: 8.0)

    class _NoCache:
        def decode_image(self, x):
            return x

    gen = _gen(_NoCache())
    latent = torch.randn(1, 16, 5, 32, 32)
    assert gen._causal3d_chunk_frames(latent, "cuda:0") is None


def test_chunk_frames_none_for_still_image(monkeypatch):
    monkeypatch.setattr(engine, "free_vram_gb", lambda d: 8.0)
    gen = _gen(_FakeV1())
    assert gen._causal3d_chunk_frames(torch.randn(1, 16, 1, 32, 32), "cuda:0") is None


def test_chunk_frames_none_when_vram_unqueryable(monkeypatch):
    monkeypatch.setattr(engine, "free_vram_gb", lambda d: None)
    gen = _gen(_FakeV1())
    assert gen._causal3d_chunk_frames(torch.randn(1, 16, 8, 32, 32), "cuda:0") is None


def test_chunk_frames_none_when_single_frame_too_big(monkeypatch):
    # A huge frame (256x256 latent) against tiny free VRAM: even one frame's spike
    # exceeds the budget -> spatial-tiling territory, not temporal chunking.
    monkeypatch.setattr(engine, "free_vram_gb", lambda d: 0.4)
    gen = _gen(_FakeV1(), estimated_vram_gb=0.3)
    assert gen._causal3d_chunk_frames(torch.randn(1, 16, 8, 256, 256), "cuda:0") is None


def test_chunk_frames_picks_count_for_long_clip(monkeypatch):
    # Per-frame spike ~ 32*32 * 1.2MB /1024 = ~1.2GB. free=8, fraction 0.75 -> ~6GB,
    # minus weights(0.3) minus base -> a few frames fit, and it's < clip length (16).
    monkeypatch.setattr(engine, "free_vram_gb", lambda d: 8.0)
    gen = _gen(_FakeV1(), estimated_vram_gb=0.3)
    k = gen._causal3d_chunk_frames(torch.randn(1, 16, 16, 32, 32), "cuda:0")
    assert isinstance(k, int)
    assert 1 <= k < 16


def test_chunk_frames_none_when_whole_clip_fits(monkeypatch):
    # Enormous free VRAM: the whole clip fits temporally -> no chunking needed.
    monkeypatch.setattr(engine, "free_vram_gb", lambda d: 1000.0)
    gen = _gen(_FakeV1())
    assert gen._causal3d_chunk_frames(torch.randn(1, 16, 8, 32, 32), "cuda:0") is None


# --- exactness: chunked == single (both v1 and v2) ----------------------------


@pytest.mark.parametrize("vae_cls", [_FakeV1, _FakeV2])
def test_chunked_matches_single_decode(vae_cls):
    gen = _gen(vae_cls())
    latent = torch.randn(1, 16, 5, 8, 8)
    single = gen._decode_causal3d_chunked(latent, "cpu", chunk_frames=99)   # >=T -> one call
    chunked = gen._decode_causal3d_chunked(latent, "cpu", chunk_frames=2)   # multi-call
    assert (single == chunked).all()                                        # byte-exact


def test_v2_first_chunk_flag_only_on_first():
    vae = _FakeV2()
    gen = _gen(vae)
    gen._decode_causal3d_chunked(torch.randn(1, 16, 5, 8, 8), "cpu", chunk_frames=2)
    # 5 frames / 2 per chunk -> 3 calls; only the first carries the clip's first frame.
    assert vae.first_chunk_flags == [True, False, False]


def test_chunked_applies_wan21_denorm_once():
    # A custom latent_format's mean/std must be applied (once) before decoding.
    vae = _FakeV1()
    gen = _gen(vae, latent_format={"latents_mean": [0.0] * 16, "latents_std": [2.0] * 16})
    latent = torch.ones(1, 16, 3, 4, 4)
    out = gen._decode_causal3d_chunked(latent, "cpu", chunk_frames=2)
    # denorm = 1*2 + 0 = 2 -> clamp(-1,1)=1 -> uint8 255 on the RGB channels.
    assert out.dtype.name == "uint8"
    assert int(out.max()) == 255


# --- shrink-on-OOM whole-clip restart -----------------------------------------


def test_chunked_shrinks_on_oom_and_restarts():
    class _OomOnceV1(_FakeV1):
        def __init__(self):
            super().__init__()
            self.oomed = False

        def decode(self, z, feat_cache=None):
            # OOM on the first (largest) chunk of the first attempt, then succeed.
            if not self.oomed:
                self.oomed = True
                raise torch.cuda.OutOfMemoryError("simulated decode spike")
            return super().decode(z, feat_cache)

    vae = _OomOnceV1()
    gen = _gen(vae)
    latent = torch.randn(1, 16, 8, 8, 8)
    out = gen._decode_causal3d_chunked(latent, "cpu", chunk_frames=4)
    assert out.shape == (1, 64, 64, 3)      # 8 latent px * 8 -> 64, HWC uint8 (frame 0)
    # Restart rebuilt a fresh feat_cache (new_feat_cache called again after the OOM).
    assert vae.new_cache_calls >= 2


def test_chunked_propagates_oom_at_min_chunk():
    class _AlwaysOomV1(_FakeV1):
        def decode(self, z, feat_cache=None):
            raise torch.cuda.OutOfMemoryError("won't fit even at 1 frame")

    gen = _gen(_AlwaysOomV1())
    with pytest.raises(torch.cuda.OutOfMemoryError):
        gen._decode_causal3d_chunked(torch.randn(1, 16, 4, 8, 8), "cpu", chunk_frames=2)


# --- decode() routing ---------------------------------------------------------


def _routing_gen(vae_module):
    gen = _gen(vae_module)
    calls = {"once": 0, "chunked": 0, "tiled": 0}
    gen._decode_once = lambda latents, *, vram_free_gb=None: calls.__setitem__("once", calls["once"] + 1) or _RET
    gen._decode_causal3d_chunked = lambda l, d, c: calls.__setitem__("chunked", calls["chunked"] + 1) or _RET
    gen._decode_causal3d_tiled = lambda l, d: calls.__setitem__("tiled", calls["tiled"] + 1) or _RET
    gen._ensure_room_for = lambda need, device: None
    return gen, calls


_RET = object()


def test_decode_routes_to_chunked_for_long_clip(monkeypatch):
    gen, calls = _routing_gen(_FakeV1())
    gen._causal3d_decode_fits = lambda l, d: False      # doesn't fit untiled
    gen._causal3d_chunk_frames = lambda l, d: 2         # but temporal chunking applies
    out = gen.decode(torch.randn(1, 16, 8, 32, 32))
    assert out is _RET
    assert (calls["chunked"], calls["tiled"], calls["once"]) == (1, 0, 0)


def test_decode_routes_to_tiled_when_chunking_not_applicable(monkeypatch):
    gen, calls = _routing_gen(_FakeV1())
    gen._causal3d_decode_fits = lambda l, d: False
    gen._causal3d_chunk_frames = lambda l, d: None      # per-frame too big / still image
    out = gen.decode(torch.randn(1, 16, 8, 32, 32))
    assert out is _RET
    assert (calls["chunked"], calls["tiled"], calls["once"]) == (0, 1, 0)


def test_decode_untiled_when_it_fits(monkeypatch):
    gen, calls = _routing_gen(_FakeV1())
    gen._causal3d_decode_fits = lambda l, d: True        # whole thing fits -> byte-identical path
    gen._causal3d_chunk_frames = lambda l, d: 2
    out = gen.decode(torch.randn(1, 16, 8, 32, 32))
    assert out is _RET
    assert (calls["chunked"], calls["tiled"], calls["once"]) == (0, 0, 1)


def test_decode_chunked_oom_falls_back_to_tiled(monkeypatch):
    gen, calls = _routing_gen(_FakeV1())
    gen._causal3d_decode_fits = lambda l, d: False
    gen._causal3d_chunk_frames = lambda l, d: 2
    gen._free_for_decode_retry = lambda device: None

    def _chunked_oom(l, d, c):
        calls["chunked"] += 1
        raise torch.cuda.OutOfMemoryError("chunk still too big")

    gen._decode_causal3d_chunked = _chunked_oom
    out = gen.decode(torch.randn(1, 16, 8, 32, 32))
    assert out is _RET
    assert (calls["chunked"], calls["tiled"]) == (1, 1)   # chunked tried, then tiled


# --- Codex E19: an ACTUAL untiled OOM re-tries temporal chunking -------------


def test_decode_untiled_oom_retries_chunked_before_tiled(monkeypatch):
    gen, calls = _routing_gen(_FakeV1())
    gen._causal3d_decode_fits = lambda l, d: True        # optimistic preflight says it fits

    def _once_oom(latents, *, vram_free_gb=None):
        calls["once"] += 1
        raise torch.cuda.OutOfMemoryError("fragmentation defeated the estimate")

    gen._decode_once = _once_oom
    gen._causal3d_chunk_frames = lambda l, d: 2          # chunking applies against freed VRAM
    out = gen.decode(torch.randn(1, 16, 8, 32, 32))
    assert out is _RET
    # untiled OOM -> temporal chunk (exact, no seams), NOT straight to spatial tiling.
    assert (calls["once"], calls["chunked"], calls["tiled"]) == (1, 1, 0)
