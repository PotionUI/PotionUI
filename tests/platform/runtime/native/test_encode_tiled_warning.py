from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest
import torch

import src.platform.runtime.native.engine as engine_mod
from src.platform.runtime.native.detect.registry import arch_registry
from src.platform.runtime.native.engine import NativeGenerator


class _Causal3DVae:
    def decode_image(self, latent):
        return latent


class _Pixels:
    def __init__(self, h: int, w: int):
        self.h = h
        self.w = w

    def to(self, **kwargs):
        return torch.empty((1, 3, self.h, self.w), device="meta")


def _spec(family: str, variant: str):
    spec = arch_registry.get(family, variant)
    assert spec is not None
    return spec


def _gen(spec):
    gen = object.__new__(NativeGenerator)
    gen.placement = None
    gen.spec = spec
    gen.vae = SimpleNamespace(
        module=_Causal3DVae(), estimated_vram_gb=0.25, device="cuda:0", compute_dtype=torch.bfloat16,
        move_to=lambda device: None, offload=lambda: None,
    )
    gen.device_plan = SimpleNamespace(vae_device="cuda:0")
    return gen


def _shape_only(h: int, w: int) -> torch.Tensor:
    return torch.empty((1, 3, h, w), device="meta")


def _warnings(caplog) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == engine_mod.logger.name and r.levelno >= logging.WARNING]


@pytest.fixture
def free_10_5(monkeypatch):
    monkeypatch.setattr(engine_mod, "free_vram_gb", lambda device: 10.5)


def test_a_tiled_qwen_image21_reference_encode_logs_a_warning_with_tile_free_and_estimate(free_10_5, caplog):
    gen = _gen(_spec("qwen_image21", "qwen_image21"))
    with caplog.at_level(logging.INFO, logger=engine_mod.logger.name):
        tile = gen._causal3d_encode_tile_for(_shape_only(2048, 2048), "cuda:0")
    assert tile is not None
    need = gen._encode_need_gb(_shape_only(2048, 2048))
    records = _warnings(caplog)
    assert len(records) == 1
    message = records[0].getMessage()
    assert f"tile={tile[0]}px" in message
    assert "10.5GB free" in message
    assert f"~{need:.1f}GB" in message
    assert "smaller reference image" in message
    assert "free VRAM" in message


def test_a_qwen_image21_reference_encoded_in_one_piece_logs_no_warning(free_10_5, caplog):
    gen = _gen(_spec("qwen_image21", "qwen_image21"))
    with caplog.at_level(logging.INFO, logger=engine_mod.logger.name):
        tile = gen._causal3d_encode_tile_for(_shape_only(1024, 1024), "cuda:0")
    assert tile is None
    assert _warnings(caplog) == []


@pytest.mark.parametrize(("family", "variant"), [("qwen_image", "qwen_image"), ("krea2", "krea2_turbo")])
def test_a_tiled_qwen_image_or_krea2_reference_encode_stays_at_info(free_10_5, caplog, family, variant):
    gen = _gen(_spec(family, variant))
    with caplog.at_level(logging.INFO, logger=engine_mod.logger.name):
        tile = gen._causal3d_encode_tile_for(_shape_only(2048, 2048), "cuda:0")
    assert tile is not None
    assert _warnings(caplog) == []
    assert any(
        r.levelno == logging.INFO and "encoding tiled" in r.getMessage()
        for r in caplog.records if r.name == engine_mod.logger.name
    )


def test_the_oom_retry_of_a_qwen_image21_reference_encode_warns_when_it_tiles(free_10_5, monkeypatch, caplog):
    gen = _gen(_spec("qwen_image21", "qwen_image21"))
    monkeypatch.setattr(gen, "_image_to_pixels", lambda image: _Pixels(1024, 1024))
    monkeypatch.setattr(gen, "_ensure_room_for_encode", lambda pixels, device: None)
    monkeypatch.setattr(gen, "_free_for_decode_retry", lambda device: None)
    tiles: list = []

    def _dispatch(pixels, device, vram_free_gb, tile=None):
        tiles.append(tile)
        if len(tiles) == 1:
            raise torch.cuda.OutOfMemoryError("fake encode OOM")
        return torch.zeros(1)

    monkeypatch.setattr(gen, "_encode_dispatch", _dispatch)
    with caplog.at_level(logging.INFO, logger=engine_mod.logger.name):
        gen.encode_image(object())
    assert tiles[0] is None
    assert tiles[1] is not None
    tiled = [r for r in _warnings(caplog) if "encoding tiled" in r.getMessage()]
    assert len(tiled) == 1
    assert f"tile={tiles[1][0]}px" in tiled[0].getMessage()


def test_the_oom_retry_of_a_qwen_image_reference_encode_does_not_warn_about_tiling(free_10_5, monkeypatch, caplog):
    gen = _gen(_spec("qwen_image", "qwen_image"))
    monkeypatch.setattr(gen, "_image_to_pixels", lambda image: _Pixels(1024, 1024))
    monkeypatch.setattr(gen, "_ensure_room_for_encode", lambda pixels, device: None)
    monkeypatch.setattr(gen, "_free_for_decode_retry", lambda device: None)
    tiles: list = []

    def _dispatch(pixels, device, vram_free_gb, tile=None):
        tiles.append(tile)
        if len(tiles) == 1:
            raise torch.cuda.OutOfMemoryError("fake encode OOM")
        return torch.zeros(1)

    monkeypatch.setattr(gen, "_encode_dispatch", _dispatch)
    with caplog.at_level(logging.INFO, logger=engine_mod.logger.name):
        gen.encode_image(object())
    assert tiles[1] is not None
    assert [r for r in _warnings(caplog) if "encoding tiled" in r.getMessage()] == []


def test_only_the_qwen_image21_vae_declares_an_unfaithful_tiled_encode():
    unfaithful = [
        (spec.family, spec.variant) for spec in arch_registry.all()
        if not (spec.latent_format or {}).get("tiled_encode_faithful", True)
    ]
    assert unfaithful == [("qwen_image21", "qwen_image21")]
