"""Tests for the shared Wan i2v-concat VAE-encode OOM ladder
(``make_wan_vae_encode``): untiled pass-through, and shrink-on-OOM tiled
fallback -- moved here from generator/img2vid_wan22 when the encode was
hoisted so generator/chain_video_wan22 could share it."""

from __future__ import annotations

from types import SimpleNamespace

import torch

from src.pipelines.pipes._shared.vae.wan_tiled_encode import make_wan_vae_encode


def test_vae_encode_falls_back_to_tiled_on_oom():
    """make_wan_vae_encode returns the untiled result normally, and on a CUDA
    OOM retries via the spatial-tiling path (temporal chunking untouched)."""
    from src.platform.runtime.native.kernels import disable_weight_init
    from src.platform.runtime.native.vae.causal_3d import AutoEncoderCausal3D

    vae = AutoEncoderCausal3D.from_config({}, disable_weight_init)
    vae.eval()
    with torch.no_grad():
        for p in vae.parameters():
            if p.is_floating_point():
                p.normal_(std=0.02)

    real_encode = vae.encode
    calls = {"n": 0}

    def flaky_encode(px):
        calls["n"] += 1
        if calls["n"] == 1:  # the untiled whole-frame attempt OOMs once
            raise torch.cuda.OutOfMemoryError("mock OOM")
        return real_encode(px)

    vae.encode = flaky_encode
    encode = make_wan_vae_encode(vae, 64, 64)
    pixels = torch.rand(1, 3, 1, 64, 64) * 2.0 - 1.0

    with torch.no_grad():
        out = encode(pixels)

    assert calls["n"] == 2                 # one OOM, one successful retry
    assert out.shape == (1, 16, 1, 8, 8)   # correct latent shape from the fallback
    assert torch.isfinite(out).all()


def test_vae_encode_passes_through_when_no_oom():
    """No OOM -> the wrapper is a transparent pass-through (single encode call)."""
    calls = {"n": 0}

    def ok_encode(px):
        calls["n"] += 1
        return torch.zeros(1, 16, 1, px.shape[3] // 8, px.shape[4] // 8)

    vae = SimpleNamespace(encode=ok_encode)
    encode = make_wan_vae_encode(vae, 64, 64)
    out = encode(torch.rand(1, 3, 1, 64, 64))

    assert calls["n"] == 1
    assert out.shape == (1, 16, 1, 8, 8)


class _Component:
    def __init__(self, name, registry, log):
        self.name = name
        self._registry = registry
        self._log = log

    def offload(self):
        self._log.append(("offload", self.name))
        self._registry.note_offloaded(self)


def _registry_with(names, monkeypatch):
    from src.pipelines.pipes._shared.vae import wan_tiled_encode
    from src.platform.runtime.native.memory.residency import GpuResidencyRegistry

    registry = GpuResidencyRegistry()
    log = []
    components = {name: _Component(name, registry, log) for name in names}
    for component in components.values():
        registry.note_resident(component, "cuda:0", 1.0)
    monkeypatch.setattr(wan_tiled_encode, "get_residency_registry", lambda: registry)
    monkeypatch.setattr(wan_tiled_encode.torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(wan_tiled_encode.torch.cuda, "empty_cache", lambda: log.append(("empty_cache", None)))
    return registry, components, log


def test_make_room_offloads_the_text_encoder_and_keeps_the_vae_and_the_dit(monkeypatch):
    from src.pipelines.pipes._shared.vae.wan_tiled_encode import make_room_for_wan_vae_encode

    registry, parts, log = _registry_with(("umt5", "vae", "dit"), monkeypatch)
    make_room_for_wan_vae_encode("cuda:0", keep=(parts["vae"], parts["dit"], None))

    assert log == [("offload", "umt5"), ("empty_cache", None)]
    assert registry.resident_gb("cuda:0") == 2.0


def test_make_room_leaves_a_parked_expert_alone(monkeypatch):
    from src.pipelines.pipes._shared.vae.wan_tiled_encode import make_room_for_wan_vae_encode

    registry, parts, log = _registry_with(("umt5", "vae", "parked"), monkeypatch)
    registry.note_offloaded(parts["parked"])
    make_room_for_wan_vae_encode("cuda:0", keep=(parts["vae"],))

    assert log == [("offload", "umt5"), ("empty_cache", None)]


def test_make_room_does_nothing_off_cuda(monkeypatch):
    from src.pipelines.pipes._shared.vae.wan_tiled_encode import make_room_for_wan_vae_encode

    registry, parts, log = _registry_with(("umt5",), monkeypatch)
    make_room_for_wan_vae_encode("cpu", keep=())

    assert log == []
    assert registry.resident_gb("cuda:0") == 1.0


def _concat_room(raw_free_gb, monkeypatch, effective_free_gb=None, names=("umt5", "vae", "high", "low")):
    from src.pipelines.pipes._shared.vae import wan_tiled_encode

    registry, parts, log = _registry_with(names, monkeypatch)
    monkeypatch.setattr(wan_tiled_encode, "_release_idle_pool", lambda device: log.append(("release_idle_pool", device)))
    raw_reads = iter(raw_free_gb if isinstance(raw_free_gb, list) else [raw_free_gb])

    def raw_free(device):
        log.append(("read_raw_free", device))
        return next(raw_reads)

    def effective_free(device):
        log.append(("read_effective_free", device))
        return effective_free_gb

    def registry_lookup():
        log.append(("registry", None))
        return registry

    ensure_free = registry.ensure_free

    def recording_ensure_free(device, need_gb, current_free_gb, *, exclude=()):
        log.append(("ensure_free", current_free_gb))
        return ensure_free(device, need_gb, current_free_gb, exclude=exclude)

    monkeypatch.setattr(wan_tiled_encode, "free_vram_gb", raw_free)
    monkeypatch.setattr(wan_tiled_encode, "effective_free_vram_gb", effective_free)
    monkeypatch.setattr(wan_tiled_encode, "get_residency_registry", registry_lookup)
    monkeypatch.setattr(registry, "ensure_free", recording_ensure_free)
    return registry, parts, log


def test_concat_encode_room_returns_before_any_flush_when_raw_free_covers_the_estimate(monkeypatch):
    from src.pipelines.pipes._shared.vae.wan_tiled_encode import make_room_for_wan_concat_encode

    registry, parts, log = _concat_room(20.0, monkeypatch, effective_free_gb=24.0)
    make_room_for_wan_concat_encode("cuda:0", parts["vae"], 960, 544, keep=(parts["high"], parts["low"]))

    assert log == [("read_raw_free", "cuda:0")]
    assert registry.resident_gb("cuda:0") == 4.0


def test_concat_encode_room_evicts_nothing_when_the_idle_pool_alone_makes_the_encode_fit(monkeypatch):
    from src.pipelines.pipes._shared.vae.wan_tiled_encode import make_room_for_wan_concat_encode

    registry, parts, log = _concat_room([0.4, 6.0], monkeypatch, effective_free_gb=6.0)
    make_room_for_wan_concat_encode("cuda:0", parts["vae"], 832, 480, keep=(parts["high"], parts["low"]))

    assert log == [("read_raw_free", "cuda:0"), ("read_effective_free", "cuda:0"), ("release_idle_pool", "cuda:0"),
                   ("read_raw_free", "cuda:0")]
    assert registry.resident_gb("cuda:0") == 4.0


def test_concat_encode_room_hands_the_idle_pool_back_before_it_evicts_the_text_encoder(monkeypatch):
    from src.pipelines.pipes._shared.vae.wan_tiled_encode import make_room_for_wan_concat_encode

    registry, parts, log = _concat_room([0.39, 0.58], monkeypatch, effective_free_gb=0.58)
    make_room_for_wan_concat_encode("cuda:0", parts["vae"], 832, 480, keep=(parts["high"], parts["low"], None))

    assert log == [("read_raw_free", "cuda:0"), ("read_effective_free", "cuda:0"), ("release_idle_pool", "cuda:0"),
                   ("read_raw_free", "cuda:0"), ("registry", None), ("ensure_free", 0.58), ("offload", "umt5"),
                   ("release_idle_pool", "cuda:0")]
    assert registry.resident_gb("cuda:0") == 3.0


def test_concat_encode_room_evicts_without_a_flush_when_the_allocator_holds_no_idle_pool(monkeypatch):
    from src.pipelines.pipes._shared.vae.wan_tiled_encode import make_room_for_wan_concat_encode

    registry, parts, log = _concat_room(0.39, monkeypatch, effective_free_gb=0.39)
    make_room_for_wan_concat_encode("cuda:0", parts["vae"], 832, 480, keep=(parts["high"], parts["low"]))

    assert log == [("read_raw_free", "cuda:0"), ("read_effective_free", "cuda:0"), ("registry", None),
                   ("ensure_free", 0.39), ("offload", "umt5"), ("release_idle_pool", "cuda:0")]
    assert registry.resident_gb("cuda:0") == 3.0


def test_concat_encode_room_does_nothing_when_free_vram_cannot_be_read(monkeypatch):
    from src.pipelines.pipes._shared.vae.wan_tiled_encode import make_room_for_wan_concat_encode

    registry, parts, log = _concat_room(None, monkeypatch, effective_free_gb=0.39)
    make_room_for_wan_concat_encode("cuda:0", parts["vae"], 832, 480, keep=(parts["high"], parts["low"]))

    assert log == [("read_raw_free", "cuda:0")]
    assert registry.resident_gb("cuda:0") == 4.0


def test_concat_encode_room_does_nothing_off_cuda(monkeypatch):
    from src.pipelines.pipes._shared.vae.wan_tiled_encode import make_room_for_wan_concat_encode

    registry, parts, log = _concat_room(0.0, monkeypatch)
    make_room_for_wan_concat_encode("cpu", parts["vae"], 832, 480)

    assert log == []
    assert registry.resident_gb("cuda:0") == 4.0


def test_concat_encode_need_counts_the_vae_weights_only_while_they_are_off_the_card():
    from src.pipelines.pipes._shared.vae.wan_tiled_encode import wan_concat_encode_need_gb

    on_card = wan_concat_encode_need_gb(SimpleNamespace(device="cuda:0", estimated_vram_gb=0.25), 832, 480)
    off_card = wan_concat_encode_need_gb(SimpleNamespace(device="cpu", estimated_vram_gb=0.25), 832, 480)

    assert on_card == 2.0 + 0.5 * (60 * 104) / 1024
    assert off_card == on_card + 0.25
    assert wan_concat_encode_need_gb(SimpleNamespace(device="cuda:0"), 960, 544) > on_card
