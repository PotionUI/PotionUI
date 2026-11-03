"""Tests for the latent_upscaler/minimax_h3 pipe."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
import torch

from src.pipelines.contracts import IOType, PipeInput
from src.pipelines.pipes.latent_upscaler.minimax_h3.geometry import resolve_target_geometry
from src.platform.runtime.native.vae.minimax_h3_latent_upsampler import MEAN, STD
from src.pipelines.pipes.latent_upscaler.minimax_h3.main import (
    LatentUpscalerMinimaxH3Pipe,
    _free_room_for_upscale,
    _resize_cover_center_crop_01,
    _upscale_need_gb,
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
        self.eval_calls = 0

    def eval(self):
        self.eval_calls += 1
        return self

    def __call__(self, latent, *, scale, target_size):
        self.calls.append((latent.clone(), scale, target_size))
        b, c, _, _, _ = latent.shape
        target_t, target_h, target_w = target_size
        return torch.zeros(b, c, target_t, target_h, target_w)


class _VaeModule:
    def __init__(self):
        self.encoder = torch.nn.Module()
        self.encoder.conv_in = torch.nn.Conv3d(3, 128, 1)
        self.clip_length = 17
        self.use_tiling = True
        self.tile_sample_min_height = 256
        self.tile_sample_min_width = 256
        self.encode_tile_batch_size = 1
        self.latents_mean = torch.tensor(MEAN)
        self.latents_std = torch.tensor(STD)
        self.encoded = []

    def encode_tile_grid(self, height, width):
        return 1, 1

    def encode(self, pixels):
        self.encoded.append(pixels)
        return torch.zeros(1, 24, 3, 4, 4)


def _bundle(with_upsampler=True, dit=None):
    video_vae = _RecordingComponent(_VaeModule())
    upsampler = _RecordingComponent(_UpsamplerModule()) if with_upsampler else None
    return SimpleNamespace(video_vae=video_vae, upsampler=upsampler, audio_vae=None, dit=dit)


def _pipe(**over):
    cfg = LatentUpscalerMinimaxH3Pipe.get_default_config()
    cfg.update(over)
    return LatentUpscalerMinimaxH3Pipe(config=cfg)


def test_raises_when_bundle_has_no_upsampler():
    bundle = _bundle(with_upsampler=False)
    latent = torch.randn(1, 24, 3, 4, 4)
    with pytest.raises(ValueError, match="upscale_model"):
        _pipe().process(PipeInput(input={"model": bundle, "latent": latent}), lambda o: None)


def test_raises_when_neither_latent_nor_video_given():
    bundle = _bundle()
    with pytest.raises(ValueError, match="requires either a 'latent' or a 'video'"):
        _pipe(device="cpu").process(PipeInput(input={"model": bundle}), lambda o: None)


def test_the_upsampler_sees_the_vae_normalized_latent_normalized_a_second_time_and_hands_back_raw():
    bundle = _bundle()
    torch.manual_seed(6)
    latent = torch.randn(1, 24, 3, 4, 4) * 2.0
    mean = torch.tensor(MEAN).view(1, -1, 1, 1, 1)
    std = torch.tensor(STD).view(1, -1, 1, 1, 1)

    out = _pipe(device="cpu", target_mode="scale", scale=2.0).process(
        PipeInput(input={"model": bundle, "latent": latent}), lambda o: None)

    seen_latent, seen_scale, seen_target = bundle.upsampler.module.calls[0]
    assert torch.allclose(seen_latent, ((latent - mean) / std - mean) / std, atol=1e-5)
    assert seen_scale == pytest.approx(2.0)
    assert seen_target == (3, 8, 8)
    assert torch.allclose(out.output["latent"], (mean * std + mean).expand(1, 24, 3, 8, 8), atol=1e-5)


def test_upsample_moves_and_offloads_the_upsampler():
    bundle = _bundle()
    latent = torch.randn(1, 24, 3, 4, 4)
    _pipe(device="cpu", target_mode="scale", scale=2.0).process(
        PipeInput(input={"model": bundle, "latent": latent}), lambda o: None)
    assert bundle.upsampler.moved_to == ["cpu"]
    assert bundle.upsampler.offloaded == 1


def test_offload_happens_even_if_upsampler_raises():
    bundle = _bundle()

    class _Boom(_UpsamplerModule):
        def __call__(self, latent, *, scale, target_size):
            raise RuntimeError("boom")

    bundle.upsampler.module = _Boom()
    latent = torch.randn(1, 24, 3, 4, 4)
    with pytest.raises(RuntimeError, match="boom"):
        _pipe(device="cpu", target_mode="scale", scale=2.0).process(
            PipeInput(input={"model": bundle, "latent": latent}), lambda o: None)
    assert bundle.upsampler.offloaded == 1


def test_video_input_encodes_then_upsamples():
    bundle = _bundle()
    frames = torch.rand(5, 32, 32, 3)
    with patch(f"{_MOD}.load_video_frames", return_value=frames):
        out = _pipe(device="cpu", target_mode="scale", scale=2.0).process(
            PipeInput(input={"model": bundle, "video": ["fake.mp4"]}), lambda o: None)
    assert out.output["latent"].shape[0] == 1
    assert out.output["source_frame_count"] == 5


def test_latent_input_takes_priority_over_video():
    bundle = _bundle()
    latent = torch.randn(1, 24, 3, 4, 4)
    with patch(f"{_MOD}.load_video_frames") as mock_load:
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
    frames = torch.rand(100, 32, 32, 3)
    with patch(f"{_MOD}.load_video_frames", return_value=frames):
        out = _pipe(device="cpu", target_mode="scale", scale=2.0).process(
            PipeInput(input={"model": bundle, "video": ["fake.mp4"]}), lambda o: None)
    assert out.output["source_frame_count"] == 100


def test_encode_video_raises_a_clear_message_when_even_the_smallest_tile_runs_out_of_vram():
    bundle = _bundle()
    tiles_tried = []

    def oom_encode(pixels):
        tiles_tried.append(bundle.video_vae.module.tile_sample_min_height)
        raise torch.cuda.OutOfMemoryError("boom")

    bundle.video_vae.module.encode = oom_encode
    frames = torch.rand(5, 32, 32, 3)
    with patch(f"{_MOD}.load_video_frames", return_value=frames):
        with pytest.raises(torch.cuda.OutOfMemoryError, match="even at 128px tiles"):
            _pipe(device="cpu", target_mode="scale", scale=2.0).process(
                PipeInput(input={"model": bundle, "video": ["fake.mp4"]}), lambda o: None)
    assert tiles_tried == [256, 192, 128]
    assert bundle.video_vae.offloaded == 1
    assert bundle.video_vae.module.tile_sample_min_height == 256


def test_encode_oom_beside_a_resident_dit_offloads_it_and_retries_at_the_same_tile_size():
    dit = _RecordingComponent(SimpleNamespace(), device="cuda")
    bundle = _bundle(dit=dit)
    tiles_tried = []

    def encode_once_dit_is_gone(pixels):
        tiles_tried.append(bundle.video_vae.module.tile_sample_min_height)
        if dit.device == "cuda":
            raise torch.cuda.OutOfMemoryError("boom")
        return torch.zeros(1, 24, 2, 4, 4)

    bundle.video_vae.module.encode = encode_once_dit_is_gone
    frames = torch.rand(5, 32, 32, 3)
    with patch(f"{_MOD}.load_video_frames", return_value=frames), \
         patch(f"{_MOD}._free_room_for_upscale"), \
         patch(f"{_MOD}.clear_gpu_memory"):
        _pipe(device="cpu", target_mode="scale", scale=2.0).process(
            PipeInput(input={"model": bundle, "video": ["fake.mp4"]}), lambda o: None)
    assert dit.offloaded == 1
    assert tiles_tried == [256, 256]


def test_video_encode_wsl_driver_oom_raises_the_explained_out_of_memory_error():
    bundle = _bundle()

    def driver_oom_encode(pixels):
        raise RuntimeError("CUDA driver error: device not ready")

    bundle.video_vae.module.encode = driver_oom_encode
    with patch(f"{_MOD}.load_video_frames", return_value=torch.rand(5, 32, 32, 3)):
        with pytest.raises(torch.cuda.OutOfMemoryError, match="ran out of VRAM even at 128px tiles"):
            _pipe(device="cpu", target_mode="scale", scale=2.0).process(
                PipeInput(input={"model": bundle, "video": ["fake.mp4"]}), lambda o: None)
    assert bundle.video_vae.offloaded == 1


def test_downscale_request_raises_before_any_gpu_work():
    bundle = _bundle()
    latent = torch.randn(1, 24, 3, 100, 100)
    with pytest.raises(ValueError, match="only upscales"):
        _pipe(device="cpu", target_mode="megapixels", megapixels=0.5).process(
            PipeInput(input={"model": bundle, "latent": latent}), lambda o: None)
    assert bundle.upsampler.module.calls == []


class _FakeResidencyRegistry:
    def __init__(self):
        self.offload_all_calls = []

    def offload_all(self, device, *, exclude=()):
        self.offload_all_calls.append((device, tuple(exclude)))
        return []


def test_free_room_for_upscale_noop_on_non_cuda_device():
    dit = _RecordingComponent(SimpleNamespace(), device="cuda")
    bundle = _bundle(dit=dit)
    registry = _FakeResidencyRegistry()
    with patch(f"{_MOD}.get_residency_registry", return_value=registry), \
         patch(f"{_MOD}.clear_gpu_memory") as mock_clear:
        _free_room_for_upscale(bundle, "cpu")
    assert dit.offloaded == 0
    assert registry.offload_all_calls == []
    mock_clear.assert_not_called()


def test_free_room_for_upscale_offloads_resident_dit_when_upsampler_would_not_fit():
    dit = _RecordingComponent(SimpleNamespace(), device="cuda")
    bundle = _bundle(dit=dit)
    bundle.upsampler.estimated_vram_gb = 0.64
    registry = _FakeResidencyRegistry()
    with patch(f"{_MOD}.get_residency_registry", return_value=registry), \
         patch(f"{_MOD}.effective_free_vram_gb", return_value=0.5), \
         patch(f"{_MOD}.clear_gpu_memory") as mock_clear:
        _free_room_for_upscale(bundle, "cuda")
    assert dit.offloaded == 1
    assert registry.offload_all_calls == [("cuda", (bundle.video_vae, bundle.upsampler))]
    mock_clear.assert_called_once()


def test_free_room_for_upscale_keeps_resident_dit_when_upsampler_fits_beside_it():
    dit = _RecordingComponent(SimpleNamespace(), device="cuda")
    bundle = _bundle(dit=dit)
    bundle.upsampler.estimated_vram_gb = 0.64
    registry = _FakeResidencyRegistry()
    with patch(f"{_MOD}.get_residency_registry", return_value=registry), \
         patch(f"{_MOD}.effective_free_vram_gb", return_value=12.0), \
         patch(f"{_MOD}.clear_gpu_memory") as mock_clear:
        _free_room_for_upscale(bundle, "cuda")
    assert dit.offloaded == 0
    assert registry.offload_all_calls == [("cuda", (bundle.video_vae, bundle.upsampler, bundle.dit))]
    mock_clear.assert_called_once()


def test_free_room_for_upscale_offloads_resident_dit_when_free_vram_unknown():
    dit = _RecordingComponent(SimpleNamespace(), device="cuda")
    bundle = _bundle(dit=dit)
    registry = _FakeResidencyRegistry()
    with patch(f"{_MOD}.get_residency_registry", return_value=registry), \
         patch(f"{_MOD}.effective_free_vram_gb", return_value=None), \
         patch(f"{_MOD}.clear_gpu_memory") as mock_clear:
        _free_room_for_upscale(bundle, "cuda")
    assert dit.offloaded == 1
    mock_clear.assert_called_once()


def test_free_room_for_upscale_skips_dit_offload_when_not_resident():
    dit = _RecordingComponent(SimpleNamespace())
    bundle = _bundle(dit=dit)
    registry = _FakeResidencyRegistry()
    with patch(f"{_MOD}.get_residency_registry", return_value=registry), \
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
    mock_free.assert_called_once()
    assert mock_free.call_args.args == (bundle, "cpu", None)


def _marks(mock_profiler):
    return {c.args[0]: c.kwargs for c in mock_profiler.return_value.mark.call_args_list}


def test_upscale_need_counts_the_video_encode_only_on_the_video_path():
    bundle = _bundle()
    bundle.upsampler.estimated_vram_gb = 0.64
    bundle.video_vae.estimated_vram_gb = 2.95
    geometry = resolve_target_geometry(1344, 768, mode="megapixels", megapixels=2.1)

    latent_only = _upscale_need_gb(bundle, geometry, 32, None)
    with_encode = _upscale_need_gb(bundle, geometry, 32, (1344, 768))

    assert latent_only < 2.95
    assert with_encode > 2.95 + 1.0
    assert with_encode > latent_only


def test_free_room_offloads_a_resident_dit_when_the_encode_need_exceeds_free_vram():
    dit = _RecordingComponent(SimpleNamespace(), device="cuda")
    bundle = _bundle(dit=dit)
    bundle.upsampler.estimated_vram_gb = 0.64
    bundle.video_vae.estimated_vram_gb = 2.95
    geometry = resolve_target_geometry(1344, 768, mode="megapixels", megapixels=2.1)
    need_gb = _upscale_need_gb(bundle, geometry, 32, (1344, 768))
    registry = _FakeResidencyRegistry()
    with patch(f"{_MOD}.get_residency_registry", return_value=registry), \
         patch(f"{_MOD}.effective_free_vram_gb", return_value=need_gb - 0.5), \
         patch(f"{_MOD}.minimum_inference_memory_gb", return_value=0.0), \
         patch(f"{_MOD}.get_profiler") as mock_profiler, \
         patch(f"{_MOD}.clear_gpu_memory"):
        _free_room_for_upscale(bundle, "cuda", need_gb=need_gb)
    assert dit.offloaded == 1
    assert registry.offload_all_calls == [("cuda", (bundle.video_vae, bundle.upsampler))]
    mark = _marks(mock_profiler)["h3_upscale.free_room"]
    assert mark["dit_was_resident"] is True
    assert mark["dit_offloaded"] is True


def test_free_room_keeps_a_resident_dit_when_the_encode_need_fits_beside_it():
    dit = _RecordingComponent(SimpleNamespace(), device="cuda")
    bundle = _bundle(dit=dit)
    registry = _FakeResidencyRegistry()
    with patch(f"{_MOD}.get_residency_registry", return_value=registry), \
         patch(f"{_MOD}.effective_free_vram_gb", return_value=9.0), \
         patch(f"{_MOD}.minimum_inference_memory_gb", return_value=1.0), \
         patch(f"{_MOD}.clear_gpu_memory"):
        _free_room_for_upscale(bundle, "cuda", need_gb=7.9)
    assert dit.offloaded == 0


def test_process_on_the_video_path_offloads_a_resident_dit_the_encode_would_not_fit_beside():
    dit = _RecordingComponent(SimpleNamespace(), device="cuda")
    bundle = _bundle(dit=dit)
    bundle.upsampler.estimated_vram_gb = 0.64
    bundle.video_vae.estimated_vram_gb = 2.95
    frames = torch.rand(5, 32, 32, 3)
    with patch(f"{_MOD}.load_video_frames", return_value=frames), \
         patch(f"{_MOD}.get_residency_registry", return_value=_FakeResidencyRegistry()), \
         patch(f"{_MOD}.effective_free_vram_gb", return_value=4.0), \
         patch(f"{_MOD}.clear_gpu_memory"), \
         patch("torch.cuda.reset_peak_memory_stats"), \
         patch("torch.cuda.synchronize"), \
         patch("torch.cuda.max_memory_allocated", return_value=0), \
         patch.object(LatentUpscalerMinimaxH3Pipe, "_upsample", return_value=torch.zeros(1, 24, 3, 8, 8)):
        _pipe(device="cuda", target_mode="scale", scale=2.0).process(
            PipeInput(input={"model": bundle, "video": ["fake.mp4"]}), lambda o: None)
    assert dit.offloaded == 1


def test_video_path_keeps_the_pixel_clip_off_the_device_until_the_vae_streams_it():
    bundle = _bundle()
    frames = torch.rand(5, 32, 32, 3)
    with patch(f"{_MOD}.load_video_frames", return_value=frames):
        _pipe(device="cpu", target_mode="scale", scale=2.0).process(
            PipeInput(input={"model": bundle, "video": ["fake.mp4"]}), lambda o: None)
    (pixels,) = bundle.video_vae.module.encoded
    assert pixels.device.type == "cpu"
    assert pixels.dtype == torch.float32
    assert pixels.shape[:3] == (1, 3, 5)


def test_prepared_pixels_match_a_one_shot_resize_and_repeat_the_last_frame_up_to_the_17n5_grid():
    torch.manual_seed(4)
    frames = torch.rand(40, 24, 16, 3)
    with patch(f"{_MOD}.load_video_frames", return_value=frames), \
         patch(f"{_MOD}.resolve_canvas_size", return_value=(36, 20)):
        pixels, n0 = _pipe(device="cpu")._prepare_pixels("clip.mp4")

    assert n0 == 40
    assert pixels.shape == (1, 3, 56, 36, 20)
    padded = torch.cat([frames, frames[-1:].expand(16, 24, 16, 3)], dim=0)
    expected = _resize_cover_center_crop_01(padded, 36, 20)
    mean = torch.tensor((0.485, 0.456, 0.406)).view(1, -1, 1, 1, 1)
    std = torch.tensor((0.229, 0.224, 0.225)).view(1, -1, 1, 1, 1)
    assert torch.allclose(pixels, (expected - mean) / std, atol=1e-5)


def test_a_cached_upsampler_left_in_training_mode_still_runs_without_dropout():
    from src.platform.runtime.native.kernels import disable_weight_init
    from src.platform.runtime.native.vae.minimax_h3_latent_upsampler import MiniMaxH3LatentUpsampler

    torch.manual_seed(5)
    module = MiniMaxH3LatentUpsampler.from_config(
        {"in_channels": 24, "channels": 32, "num_res_blocks": 2, "temporal_kernel": 3, "embed_dim": 8}, disable_weight_init,
    )
    with torch.no_grad():
        for p in module.parameters():
            p.normal_(std=0.05)
    module.train()
    bundle = _bundle()
    bundle.upsampler.module = module
    latent = torch.randn(1, 24, 3, 4, 4)
    pipe = _pipe(device="cpu", target_mode="scale", scale=2.0)
    first = pipe.process(PipeInput(input={"model": bundle, "latent": latent}), lambda o: None).output["latent"]
    second = pipe.process(PipeInput(input={"model": bundle, "latent": latent}), lambda o: None).output["latent"]
    assert not module.training
    assert torch.equal(first, second)
