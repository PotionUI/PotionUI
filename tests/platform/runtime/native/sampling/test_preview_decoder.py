from __future__ import annotations

import logging
from types import SimpleNamespace

import numpy as np
import pytest
import torch
from safetensors.torch import save_file

from src.platform.runtime.native.sampling import preview as preview_module
from src.platform.runtime.native.sampling import preview_decoder as pd
from src.platform.runtime.native.sampling.preview import make_preview_hook
from vendor.taehv.taehv import TAEHV

WAN_SPEC = SimpleNamespace(latent_format={"latent_channels": 16, "format": "wan21"}, variant="wan_14b")


@pytest.fixture(scope="module")
def state_dicts():
    torch.manual_seed(0)
    return {arch: TAEHV(arch).state_dict() for arch in ("taew2_1", "taeh3", "taeltx", "taew2_2")}


@pytest.fixture(scope="module")
def decoder(state_dicts):
    return pd.build_preview_decoder(state_dicts["taew2_1"])


@pytest.fixture()
def wan_file(tmp_path, state_dicts):
    path = tmp_path / "taew2_1.safetensors"
    save_file({k: v.contiguous() for k, v in state_dicts["taew2_1"].items()}, str(path))
    return path


@pytest.fixture()
def no_lifecycle(monkeypatch):
    monkeypatch.setattr(pd, "get_model_lifecycle", lambda: None)


@pytest.mark.parametrize(
    "arch,channels,patch,t_upscale",
    [("taew2_1", 16, 1, 4), ("taew2_2", 48, 2, 4), ("taeh3", 24, 2, 4), ("taeltx", 128, 4, 8)],
)
def test_architecture_is_read_from_the_weights_not_the_file_name(state_dicts, arch, channels, patch, t_upscale):
    assert pd.arch_name_from_state_dict(state_dicts[arch]) == arch
    built = pd.build_preview_decoder(state_dicts[arch])
    assert (built.latent_channels, built.patch_size, built.t_upscale) == (channels, patch, t_upscale)
    assert not built.training
    assert all(not p.requires_grad for p in built.parameters())


def test_unknown_latent_width_is_rejected():
    sd = {"decoder.1.weight": torch.zeros(256, 7, 3, 3)}
    with pytest.raises(ValueError, match="7 latent channels"):
        pd.arch_name_from_state_dict(sd)


def test_load_from_safetensors_file(wan_file, state_dicts):
    loaded = pd.load_preview_decoder(wan_file)
    assert loaded.latent_channels == 16
    for key, value in loaded.state_dict().items():
        assert torch.equal(value, state_dicts["taew2_1"][key])


def test_latent_ranks_normalise_to_ntchw():
    assert pd.latent_to_ntchw(torch.zeros(1, 16, 3, 4, 5), 16).shape == (1, 3, 16, 4, 5)
    assert pd.latent_to_ntchw(torch.zeros(1, 16, 4, 5), 16).shape == (1, 1, 16, 4, 5)
    assert pd.latent_to_ntchw(torch.zeros(16, 4, 5), 16).shape == (1, 1, 16, 4, 5)
    with pytest.raises(ValueError, match="channels"):
        pd.latent_to_ntchw(torch.zeros(1, 8, 4, 5), 16)
    with pytest.raises(ValueError, match="3/4/5D"):
        pd.latent_to_ntchw(torch.zeros(4, 5), 16)


def test_decode_clip_returns_every_frame_of_the_clip(decoder):
    clip = pd.decode_clip(decoder, torch.randn(1, 16, 3, 4, 6))
    assert clip.shape == (9, 3, 32, 48)
    assert clip.dtype == torch.float32
    assert clip.min() >= 0.0 and clip.max() <= 1.0


def test_decode_clip_can_skip_spatial_upscaling(decoder):
    clip = pd.decode_clip(decoder, torch.randn(1, 16, 3, 4, 6), space_upscales=1)
    assert clip.shape == (9, 3, 8, 12)
    full = pd.decode_clip(decoder, torch.randn(1, 16, 3, 4, 6))
    assert full.shape[-2:] == (32, 48)


def test_sequential_and_parallel_decode_agree(decoder):
    latent = torch.randn(1, 16, 3, 4, 4)
    parallel = pd.decode_clip(decoder, latent, parallel=True)
    sequential = pd.decode_clip(decoder, latent, parallel=False)
    assert torch.allclose(parallel, sequential, atol=1e-4)


def test_a_single_frame_latent_decodes_to_one_frame(decoder):
    assert pd.decode_clip(decoder, torch.randn(1, 16, 4, 4)).shape == (1, 3, 32, 32)


def test_middle_frame_decodes_only_a_window_of_the_clip(decoder, monkeypatch):
    seen = []
    real = pd.apply_model_with_memblocks

    def spy(model, x, parallel):
        seen.append(x.shape[1])
        return real(model, x, parallel)

    monkeypatch.setattr(pd, "apply_model_with_memblocks", spy)
    frame = pd.decode_middle_frame(decoder, torch.randn(1, 16, 20, 4, 4), max_size=32)
    assert seen == [pd.WINDOW_CONTEXT_TOKENS + pd.WINDOW_LOOKAHEAD_TOKENS + 1]
    assert frame.shape == (3, 32, 32)


@pytest.mark.parametrize("latent_size,max_size,edge", [(4, 32, 32), (4, 8, 8), (4, 1000, 32), (16, 32, 32)])
def test_middle_frame_is_decoded_no_larger_than_the_preview_needs(decoder, latent_size, max_size, edge):
    frame = pd.decode_middle_frame(decoder, torch.randn(1, 16, 5, latent_size, latent_size), max_size=max_size)
    assert frame.shape == (3, edge, edge)


def test_frame_to_image_caps_the_long_edge():
    image = pd.frame_to_image(torch.rand(3, 100, 40), max_size=50)
    assert image.size == (20, 50)
    small = pd.frame_to_image(torch.rand(3, 10, 20), max_size=50)
    assert small.size == (20, 10)
    assert small.mode == "RGB"


def test_decoder_goes_through_the_model_lifecycle(monkeypatch, wan_file):
    calls = []
    cache = {}

    class FakeLifecycle:
        def acquire(self, key, fingerprint, loader, estimated_vram_gb=None):
            calls.append((key, fingerprint))
            if key not in cache:
                cache[key] = loader()
            return cache[key]

    monkeypatch.setattr(pd, "get_model_lifecycle", lambda: FakeLifecycle())
    first = pd.acquire_preview_decoder(wan_file)
    second = pd.acquire_preview_decoder(wan_file)
    assert first is second
    assert calls[0][0] == f"{pd.PREVIEW_DECODER_SLOT}/{wan_file}"
    assert str(wan_file.stat().st_size) in calls[0][1]


def test_hook_decodes_with_the_tiny_decoder_when_a_file_is_given(no_lifecycle, wan_file):
    emitted = []
    hook = make_preview_hook(WAN_SPEC, emitted.append, every_n=1, decoder_path=wan_file)
    hook.on_step(0, 1, torch.zeros(1, 16, 1, 8, 8), 1.0, torch.randn(1, 16, 3, 8, 8))
    assert [image.size for image in emitted] == [(64, 64)]


def test_hook_without_a_decoder_keeps_the_latent_projection(no_lifecycle):
    emitted = []
    hook = make_preview_hook(WAN_SPEC, emitted.append, every_n=1, decoder_path="")
    hook.on_step(0, 1, torch.zeros(1, 16, 1, 8, 8), 1.0, torch.randn(1, 16, 3, 8, 8))
    assert [image.size for image in emitted] == [(512, 512)]
    assert type(hook).__name__ == "PreviewHook"


def test_hook_keeps_the_512_cap_for_large_latents(no_lifecycle, wan_file):
    emitted = []
    hook = make_preview_hook(WAN_SPEC, emitted.append, every_n=1, decoder_path=wan_file)
    hook.on_step(0, 1, torch.zeros(1, 16, 1, 8, 8), 1.0, torch.randn(1, 16, 1, 90, 160))
    assert max(emitted[0].size) == 512


def test_hook_falls_back_when_the_file_is_missing(no_lifecycle, tmp_path):
    emitted = []
    hook = make_preview_hook(WAN_SPEC, emitted.append, every_n=1, decoder_path=tmp_path / "gone.safetensors")
    hook.on_step(0, 1, torch.zeros(1, 16, 1, 8, 8), 1.0, torch.randn(1, 16, 3, 8, 8))
    assert [image.size for image in emitted] == [(512, 512)]


def test_hook_falls_back_when_the_decoder_belongs_to_another_family(no_lifecycle, tmp_path, state_dicts, caplog):
    path = tmp_path / "taeh3.safetensors"
    save_file({k: v.contiguous() for k, v in state_dicts["taeh3"].items()}, str(path))
    emitted = []
    with caplog.at_level(logging.WARNING):
        hook = make_preview_hook(WAN_SPEC, emitted.append, every_n=1, decoder_path=path)
    hook.on_step(0, 1, torch.zeros(1, 16, 1, 8, 8), 1.0, torch.randn(1, 16, 3, 8, 8))
    assert [image.size for image in emitted] == [(512, 512)]
    assert "24 latent channels" in caplog.text


def test_hook_falls_back_per_frame_when_the_decode_raises(no_lifecycle, wan_file, monkeypatch):
    def boom(*_args, **_kwargs):
        raise RuntimeError("out of memory")

    monkeypatch.setattr(preview_module, "decode_middle_frame", boom)
    emitted = []
    hook = make_preview_hook(WAN_SPEC, emitted.append, every_n=1, decoder_path=wan_file)
    hook.on_step(0, 1, torch.zeros(1, 16, 1, 8, 8), 1.0, torch.randn(1, 16, 3, 8, 8))
    assert [image.size for image in emitted] == [(512, 512)]


def test_decoder_hook_works_for_a_family_without_a_projection_table(no_lifecycle, wan_file):
    spec = SimpleNamespace(latent_format={}, variant="unknown")
    emitted = []
    hook = make_preview_hook(spec, emitted.append, every_n=1, decoder_path=wan_file)
    assert hook is not None
    hook.on_step(0, 1, torch.zeros(1, 16, 1, 8, 8), 1.0, torch.randn(1, 16, 3, 8, 8))
    assert len(emitted) == 1
    assert make_preview_hook(spec, emitted.append, every_n=1) is None


def test_latent_transform_runs_before_the_decoder(no_lifecycle, wan_file):
    emitted = []
    hook = make_preview_hook(
        WAN_SPEC, emitted.append, every_n=1, decoder_path=wan_file,
        latent_transform=lambda x: x.reshape(1, 16, 2, 8, 8),
    )
    hook.on_step(0, 1, torch.zeros(1), 1.0, torch.randn(2 * 16 * 64))
    assert [image.size for image in emitted] == [(64, 64)]


def test_decoder_returns_to_the_cpu_when_sampling_ends(no_lifecycle, wan_file, monkeypatch):
    moves = []
    real_acquire = pd.acquire_preview_decoder

    def tracked(path):
        built = real_acquire(path)
        original = built.to
        built.to = lambda *args, **kwargs: (moves.append(args), original(*args, **kwargs))[1]
        return built

    monkeypatch.setattr(preview_module, "acquire_preview_decoder", tracked)
    hook = make_preview_hook(WAN_SPEC, lambda image: None, every_n=1, decoder_path=wan_file)
    hook.on_end()
    assert moves == [("cpu",)]


@pytest.mark.parametrize(
    "total,count,expected",
    [(0, 25, []), (5, 25, [0, 1, 2, 3, 4]), (25, 25, list(range(25))), (49, 25, list(range(0, 49, 2)))],
)
def test_even_frame_indices(total, count, expected):
    assert pd.even_frame_indices(total, count) == expected


def test_even_frame_indices_keep_both_ends_of_a_long_clip():
    indices = pd.even_frame_indices(121, 25)
    assert len(indices) == 25 and indices[0] == 0 and indices[-1] == 120
    assert indices == sorted(set(indices))


def test_preview_clip_is_capped_in_frames_and_size(decoder):
    frames, indices = pd.decode_preview_clip(decoder, torch.randn(1, 16, 10, 6, 4), max_frames=5, max_size=24)
    assert len(frames) == len(indices) == 5
    assert indices[0] == 0 and indices[-1] == 4 * 10 - 4
    for frame in frames:
        assert frame.dtype == np.uint8 and frame.shape[2] == 3
        assert max(frame.shape[:2]) <= 24


def test_preview_clip_of_a_still_latent_is_one_frame(decoder):
    frames, indices = pd.decode_preview_clip(decoder, torch.randn(1, 16, 4, 4))
    assert len(frames) == 1 and indices == [0]


def test_preview_clip_defaults_are_25_frames_and_384_px(decoder):
    frames, indices = pd.decode_preview_clip(decoder, torch.randn(1, 16, 9, 8, 8))
    assert len(frames) == 25 and len(indices) == 25
    assert max(frames[0].shape[:2]) <= 384
