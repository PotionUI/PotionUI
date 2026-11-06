from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest
import torch

from src.platform.runtime.native.sampling import ProgressHook, preview_reserve_gb
from src.platform.runtime.native.sampling import preview as preview_module
from src.platform.runtime.native.sampling import preview_decoder as pd
from src.platform.runtime.native.sampling.preview import CLIP_MAX_SIZE, PREVIEW_MAX_SIZE, make_preview_hook
from vendor.taehv.taehv import TAEHV

GIB = 1024 ** 3
H3_SPEC = SimpleNamespace(latent_format={"latent_channels": 24, "format": "minimax_h3"}, variant="h3")


@pytest.fixture(scope="module")
def taeh3():
    torch.manual_seed(0)
    return pd.build_preview_decoder(TAEHV("taeh3").state_dict())


@pytest.fixture()
def decoder_from_file(monkeypatch, taeh3):
    monkeypatch.setattr(preview_module, "acquire_preview_decoder", lambda path: taeh3)
    return taeh3


def _hook(*, clip: bool):
    return make_preview_hook(
        H3_SPEC, lambda image: None, decoder_path="taeh3.safetensors",
        emit_clip=(lambda clip_: None) if clip else None,
    )


def test_resident_size_is_measured_from_the_module_at_the_cuda_compute_dtype(taeh3):
    elements = sum(t.numel() for t in taeh3.parameters()) + sum(t.numel() for t in taeh3.buffers())
    assert pd.decoder_resident_gb(taeh3) == pytest.approx(elements * 2 / GIB)
    assert 0.018 < pd.decoder_resident_gb(taeh3) < 0.025


def test_resident_size_follows_what_the_module_actually_holds():
    decoder = TAEHV("taeh3")
    before = pd.decoder_resident_gb(decoder)
    decoder.register_buffer("extra", torch.zeros(1024, 1024))
    assert pd.decoder_resident_gb(decoder) - before == pytest.approx(1024 * 1024 * 2 / GIB)


def test_the_clip_transient_covers_the_clip_the_real_decode_path_puts_on_the_device(taeh3):
    x0 = torch.randn(1, 24, 7, 6, 10)
    upscales = pd.pick_space_upscales(taeh3, 6, 10, CLIP_MAX_SIZE)
    clip = pd.decode_clip(taeh3, x0, parallel=True, space_upscales=upscales)
    transient = pd.preview_decode_transient_gb(
        taeh3, 7, 6, 10, frame_size=PREVIEW_MAX_SIZE, clip_size=CLIP_MAX_SIZE,
    )
    assert transient * GIB >= clip.numel() * clip.element_size()


def test_the_clip_transient_grows_with_the_clip_and_with_the_cap(taeh3):
    short = pd.preview_decode_transient_gb(taeh3, 17, 12, 20, frame_size=PREVIEW_MAX_SIZE, clip_size=CLIP_MAX_SIZE)
    long = pd.preview_decode_transient_gb(taeh3, 37, 12, 20, frame_size=PREVIEW_MAX_SIZE, clip_size=CLIP_MAX_SIZE)
    capped = pd.preview_decode_transient_gb(taeh3, 37, 12, 20, frame_size=PREVIEW_MAX_SIZE, clip_size=64)
    assert 0.0 < short < long
    assert capped < long


def test_without_clips_only_the_middle_frame_window_is_decoded(taeh3):
    few = pd.preview_decode_transient_gb(taeh3, 8, 12, 20, frame_size=PREVIEW_MAX_SIZE)
    many = pd.preview_decode_transient_gb(taeh3, 40, 12, 20, frame_size=PREVIEW_MAX_SIZE)
    with_clips = pd.preview_decode_transient_gb(taeh3, 40, 12, 20, frame_size=PREVIEW_MAX_SIZE, clip_size=CLIP_MAX_SIZE)
    assert few == pytest.approx(many)
    assert with_clips > many


def test_no_decoder_hook_reserves_nothing():
    projection = make_preview_hook(H3_SPEC, lambda image: None)
    hooks = [ProgressHook(lambda *a: None), projection]
    assert preview_reserve_gb(hooks, (1, 24, 37, 48, 84)) == 0.0
    assert preview_reserve_gb([], (1, 24, 37, 48, 84)) == 0.0


@pytest.mark.parametrize("clip", [True, False])
def test_a_decoder_hook_reserves_its_weights_plus_one_decode(decoder_from_file, clip):
    hook = _hook(clip=clip)
    expected = pd.decoder_resident_gb(decoder_from_file) + pd.preview_decode_transient_gb(
        decoder_from_file, 9, 12, 20, frame_size=PREVIEW_MAX_SIZE, clip_size=CLIP_MAX_SIZE if clip else None,
    )
    reserve = preview_reserve_gb([ProgressHook(lambda *a: None), hook], (1, 24, 9, 12, 20))
    assert reserve == pytest.approx(expected)


def test_an_image_latent_reserves_a_single_frame_decode(decoder_from_file):
    hook = _hook(clip=True)
    expected = pd.decoder_resident_gb(decoder_from_file) + pd.preview_decode_transient_gb(
        decoder_from_file, 1, 12, 20, frame_size=PREVIEW_MAX_SIZE, clip_size=CLIP_MAX_SIZE,
    )
    assert preview_reserve_gb([hook], (1, 24, 12, 20)) == pytest.approx(expected)


def test_the_profiled_h3_clip_reserves_about_what_the_preview_held(decoder_from_file):
    reserve = preview_reserve_gb([_hook(clip=True)], (1, 24, 37, 48, 84))
    assert 0.45 < reserve < 0.65


def test_a_failed_estimate_falls_back_to_the_weights_and_never_raises(decoder_from_file, monkeypatch, caplog):
    def broken(*_a, **_k):
        raise RuntimeError("trace failed")

    monkeypatch.setattr(preview_module, "preview_decode_transient_gb", broken)
    with caplog.at_level(logging.WARNING, logger=preview_module.__name__):
        reserve = preview_reserve_gb([_hook(clip=True)], (1, 24, 9, 12, 20))
    assert reserve == pytest.approx(pd.decoder_resident_gb(decoder_from_file))
    assert "preview" in caplog.text
