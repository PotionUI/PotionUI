"""Tests for the latent_upscaler/minimax_h3 pipe."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
import torch

from src.pipelines.contracts import IOType, PipeInput
from src.pipelines.pipes.latent_upscaler.minimax_h3.main import (
    LatentUpscalerMinimaxH3Pipe,
    _free_room_for_upscale,
)

_MOD = "src.pipelines.pipes.latent_upscaler.minimax_h3.main"


class _RecordingComponent:
    """Stand-in for a `NativeModel` (video_vae/upsampler/dit): records
    move_to/offload and tracks `.device` like the real `NativeModel` does
    (`_free_room_for_upscale`'s residency check reads this attribute)."""

    def __init__(self, module, device="cpu"):
        self.module = module
        self.compute_dtype = torch.float32
        self.moved_to = []
        self.offloaded = 0
        self.device = device

    def move_to(self, device):
        self.moved_to.append(device)
        self.device = str(device)

    def offload(self):
        self.offloaded += 1
        self.device = "cpu"


class _UpsamplerModule:
    """Callable stand-in for `MiniMaxH3LatentUpsampler`'s forward -- records
    every call and resizes to whatever `target_size` it was given (frames
    unchanged, per this pipe's contract)."""

    def __init__(self):
        self.calls = []

    def __call__(self, latent, *, scale, target_size):
        self.calls.append((latent.clone(), scale, target_size))
        b, c, _, _, _ = latent.shape
        target_t, target_h, target_w = target_size
        return torch.zeros(b, c, target_t, target_h, target_w)


def _bundle(with_upsampler=True, dit=None):
    video_vae_module = SimpleNamespace(
        encode=lambda pixels: torch.zeros(1, 24, 3, 4, 4),
        parameters=lambda: iter([torch.zeros(1)]),
    )
    video_vae = _RecordingComponent(video_vae_module)
    upsampler = _RecordingComponent(_UpsamplerModule()) if with_upsampler else None
    return SimpleNamespace(video_vae=video_vae, upsampler=upsampler, audio_vae=None, dit=dit)


def _pipe(**over):
    cfg = LatentUpscalerMinimaxH3Pipe.get_default_config()
    cfg.update(over)
    return LatentUpscalerMinimaxH3Pipe(config=cfg)


def test_name_and_io():
    assert LatentUpscalerMinimaxH3Pipe.name == "latent_upscaler"
    inputs = {i.name: i for i in LatentUpscalerMinimaxH3Pipe.inputs()}
    assert inputs["latent"].io_type == IOType.LATENT
    assert inputs["video"].io_type == IOType.VIDEO
    assert inputs["model"].required is True
    assert inputs["latent"].required is False
    outputs = {o.name: o for o in LatentUpscalerMinimaxH3Pipe.outputs()}
    assert outputs["latent"].io_type == IOType.LATENT
    assert outputs["source_frame_count"].io_type == IOType.INT


def test_raises_when_bundle_has_no_upsampler():
    bundle = _bundle(with_upsampler=False)
    latent = torch.randn(1, 24, 3, 4, 4)
    with pytest.raises(ValueError, match="upscale_model"):
        _pipe().process(PipeInput(input={"model": bundle, "latent": latent}), lambda o: None)


def test_raises_when_neither_latent_nor_video_given():
    bundle = _bundle()
    with pytest.raises(ValueError, match="requires either a 'latent' or a 'video'"):
        _pipe(device="cpu").process(PipeInput(input={"model": bundle}), lambda o: None)


def test_upsample_calls_normalize_then_upsampler_then_denormalize_in_order():
    bundle = _bundle()
    latent = torch.randn(1, 24, 3, 4, 4)
    calls = []

    def fake_normalize(x):
        calls.append("normalize")
        return x + 1.0

    def fake_denormalize(x):
        calls.append("denormalize")
        return x - 1.0

    with patch(f"{_MOD}.normalize_h3_latent", side_effect=fake_normalize), \
         patch(f"{_MOD}.denormalize_h3_latent", side_effect=fake_denormalize):
        out = _pipe(device="cpu", target_mode="scale", scale=2.0).process(
            PipeInput(input={"model": bundle, "latent": latent}), lambda o: None)

    assert calls == ["normalize", "denormalize"]
    # The upsampler's forward saw the NORMALIZED latent (normalize output = latent + 1.0).
    seen_latent, seen_scale, seen_target = bundle.upsampler.module.calls[0]
    assert torch.allclose(seen_latent, latent + 1.0)
    assert seen_scale == pytest.approx(2.0)
    assert seen_target == (3, 8, 8)  # latent H/W: 4*16=64px source -> x2 = 128px -> /16 = 8
    assert out.output["latent"].shape == (1, 24, 3, 8, 8)


def test_upsample_moves_and_offloads_the_upsampler():
    bundle = _bundle()
    latent = torch.randn(1, 24, 3, 4, 4)
    _pipe(device="cpu", target_mode="scale", scale=2.0).process(
        PipeInput(input={"model": bundle, "latent": latent}), lambda o: None)
    assert bundle.upsampler.moved_to == ["cpu"]
    assert bundle.upsampler.offloaded == 1


def test_offload_happens_even_if_upsampler_raises():
    bundle = _bundle()

    def boom(latent, *, scale, target_size):
        raise RuntimeError("boom")

    bundle.upsampler.module = boom
    latent = torch.randn(1, 24, 3, 4, 4)
    with pytest.raises(RuntimeError, match="boom"):
        _pipe(device="cpu", target_mode="scale", scale=2.0).process(
            PipeInput(input={"model": bundle, "latent": latent}), lambda o: None)
    assert bundle.upsampler.offloaded == 1


def test_video_input_encodes_then_upsamples():
    bundle = _bundle()
    # (n, H, W, 3) already-valid frame count (17*0+5=5); resolve_canvas_size
    # resolves the (square) aspect onto MiniMax-H3's own 768x768 canvas.
    frames = torch.rand(5, 32, 32, 3)
    with patch(f"{_MOD}._load_video_frames", return_value=frames):
        out = _pipe(device="cpu", target_mode="scale", scale=2.0).process(
            PipeInput(input={"model": bundle, "video": ["fake.mp4"]}), lambda o: None)
    assert out.output["latent"].shape[0] == 1
    assert out.output["source_frame_count"] == 5


def test_latent_input_takes_priority_over_video():
    bundle = _bundle()
    latent = torch.randn(1, 24, 3, 4, 4)
    with patch(f"{_MOD}._load_video_frames") as mock_load:
        _pipe(device="cpu", target_mode="scale", scale=2.0).process(
            PipeInput(input={"model": bundle, "latent": latent, "video": ["fake.mp4"]}), lambda o: None)
    mock_load.assert_not_called()


def test_source_frame_count_is_none_on_direct_latent_path():
    bundle = _bundle()
    latent = torch.randn(1, 24, 3, 4, 4)
    out = _pipe(device="cpu", target_mode="scale", scale=2.0).process(
        PipeInput(input={"model": bundle, "latent": latent}), lambda o: None)
    assert out.output["source_frame_count"] is None


def test_source_frame_count_reports_pre_padding_count_on_video_path():
    bundle = _bundle()
    # 100 frames pads to 107 (17*6+5) -- source_frame_count must report 100.
    frames = torch.rand(100, 32, 32, 3)
    with patch(f"{_MOD}._load_video_frames", return_value=frames):
        out = _pipe(device="cpu", target_mode="scale", scale=2.0).process(
            PipeInput(input={"model": bundle, "video": ["fake.mp4"]}), lambda o: None)
    assert out.output["source_frame_count"] == 100


def test_encode_video_raises_a_clear_message_on_oom():
    bundle = _bundle()

    def oom_encode(pixels):
        raise torch.cuda.OutOfMemoryError("boom")

    bundle.video_vae.module.encode = oom_encode
    frames = torch.rand(5, 32, 32, 3)
    with patch(f"{_MOD}._load_video_frames", return_value=frames):
        with pytest.raises(torch.cuda.OutOfMemoryError, match="no tiled encode fallback"):
            _pipe(device="cpu", target_mode="scale", scale=2.0).process(
                PipeInput(input={"model": bundle, "video": ["fake.mp4"]}), lambda o: None)
    # The VAE must still be offloaded on the way out of the OOM.
    assert bundle.video_vae.offloaded == 1


def test_downscale_request_raises_before_any_gpu_work():
    """A latent source large enough that even the default megapixels target
    would shrink it -- the geometry refusal must fire before the upsampler is
    ever touched."""
    bundle = _bundle()
    latent = torch.randn(1, 24, 3, 100, 100)  # source pixels: 1600x1600 = 2.56MP
    with pytest.raises(ValueError, match="only upscales"):
        _pipe(device="cpu", target_mode="megapixels", megapixels=0.5).process(
            PipeInput(input={"model": bundle, "latent": latent}), lambda o: None)
    assert bundle.upsampler.module.calls == []


# -- VRAM eviction (mirrors latent_upscaler/ltx's own `_free_room_for_upscale` tests) --

class _FakeResidencyManager:
    def __init__(self):
        self.offload_all_calls = []

    def offload_all(self, device, *, exclude=()):
        self.offload_all_calls.append((device, tuple(exclude)))
        return []


def test_free_room_for_upscale_noop_on_non_cuda_device():
    dit = _RecordingComponent(SimpleNamespace(), device="cuda")
    bundle = _bundle(dit=dit)
    manager = _FakeResidencyManager()
    with patch(f"{_MOD}.get_residency_manager", return_value=manager), \
         patch(f"{_MOD}.clear_gpu_memory") as mock_clear:
        _free_room_for_upscale(bundle, "cpu")
    assert dit.offloaded == 0
    assert manager.offload_all_calls == []
    mock_clear.assert_not_called()


def test_free_room_for_upscale_offloads_resident_dit_and_excludes_own_components():
    dit = _RecordingComponent(SimpleNamespace(), device="cuda")
    bundle = _bundle(dit=dit)
    manager = _FakeResidencyManager()
    with patch(f"{_MOD}.get_residency_manager", return_value=manager), \
         patch(f"{_MOD}.clear_gpu_memory") as mock_clear:
        _free_room_for_upscale(bundle, "cuda")
    assert dit.offloaded == 1
    assert manager.offload_all_calls == [("cuda", (bundle.video_vae, bundle.upsampler))]
    mock_clear.assert_called_once()


def test_free_room_for_upscale_skips_dit_offload_when_not_resident():
    dit = _RecordingComponent(SimpleNamespace())  # device defaults to "cpu"
    bundle = _bundle(dit=dit)
    manager = _FakeResidencyManager()
    with patch(f"{_MOD}.get_residency_manager", return_value=manager), \
         patch(f"{_MOD}.clear_gpu_memory") as mock_clear:
        _free_room_for_upscale(bundle, "cuda")
    assert dit.offloaded == 0
    mock_clear.assert_called_once()


def test_process_calls_free_room_for_upscale_exactly_once():
    bundle = _bundle()
    latent = torch.randn(1, 24, 3, 4, 4)
    with patch(f"{_MOD}._free_room_for_upscale") as mock_free:
        _pipe(device="cpu", target_mode="scale", scale=2.0).process(
            PipeInput(input={"model": bundle, "latent": latent}), lambda o: None)
    mock_free.assert_called_once_with(bundle, "cpu", None)
