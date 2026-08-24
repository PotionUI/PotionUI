"""Tests for the latent_upscaler/ltx pipe."""

from __future__ import annotations

import gc
import weakref
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
import torch

from src.pipelines.contracts import IOType, PipeInput
from src.platform.runtime.native.vae.ltx_tiling import LtxTilingConfig
from src.pipelines.pipes.generator.txt2vid_ltx.main import _te_ram_gb
from src.pipelines.pipes.latent_upscaler.ltx.main import (
    LatentUpscalerLtxPipe,
    _free_room_for_upscale,
    _pad_frames_to_temporal_grid,
    _unload_idle_te,
)

_MOD = "src.pipelines.pipes.latent_upscaler.ltx.main"
# The estimate-then-choose-then-retry encode ladder itself
# (get_residency_manager/clear_gpu_memory/free_vram_gb, as used INSIDE the
# ladder) now lives in the shared module `_encode_with_oom_retry` delegates
# to -- patch its namespace, not `_MOD`'s, for encode-ladder tests below.
# `_free_room_for_upscale`'s own (separate) calls to these same helpers still
# live in `_MOD` and are patched there, unchanged.
_SHARED_MOD = "src.pipelines.pipes._shared.vae.ltx_tiled_encode"


class _RecordingStats:
    def __init__(self):
        self.calls = []

    def un_normalize(self, x):
        self.calls.append(("un_normalize", x))
        return x + 1.0

    def normalize(self, x):
        self.calls.append(("normalize", x))
        return x - 1.0


class _RecordingComponent:
    """Stand-in for a `NativeModel` (vae/upsampler/dit): records move_to/offload
    and tracks `.device` like the real `NativeModel` does
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


class _FakeResidencyManager:
    """Records every `offload_all` call; never actually offloads anything --
    mirrors the test double in `test_dit_placement.py`."""

    def __init__(self):
        self.offload_all_calls = []

    def offload_all(self, device, *, exclude=()):
        self.offload_all_calls.append((device, tuple(exclude)))
        return []


class _UpsamplerModule:
    """Callable stand-in for the ``LTXLatentUpsampler`` forward -- records
    every call's input and doubles the spatial dims (frames preserved)."""

    def __init__(self, scale=2):
        self.scale = scale
        self.calls = []

    def __call__(self, z):
        self.calls.append(z.clone())
        b, c, f, h, w = z.shape
        return torch.zeros(b, c, f, h * self.scale, w * self.scale)


def _bundle(with_upsampler=True, dit=None):
    stats = _RecordingStats()
    vae_module = SimpleNamespace(per_channel_statistics=stats, encode=lambda pixels: torch.zeros(1, 8, 3, 4, 4))
    vae = _RecordingComponent(vae_module)
    upsampler = _RecordingComponent(_UpsamplerModule()) if with_upsampler else None
    return SimpleNamespace(vae=vae, upsampler=upsampler, dit=dit)


def _pipe(**over):
    cfg = LatentUpscalerLtxPipe.get_default_config()
    cfg.update(over)
    return LatentUpscalerLtxPipe(config=cfg)


def test_name_and_io():
    assert LatentUpscalerLtxPipe.name == "latent_upscaler"
    inputs = {i.name: i for i in LatentUpscalerLtxPipe.inputs()}
    assert inputs["latent"].io_type == IOType.LATENT
    assert inputs["video"].io_type == IOType.VIDEO
    assert inputs["model"].required is True
    assert inputs["latent"].required is False
    outputs = {o.name: o for o in LatentUpscalerLtxPipe.outputs()}
    assert outputs["latent"].io_type == IOType.LATENT
    assert outputs["source_frame_count"].io_type == IOType.INT


def test_raises_when_bundle_has_no_upsampler():
    bundle = _bundle(with_upsampler=False)
    latent = torch.randn(1, 8, 3, 4, 4)
    with pytest.raises(ValueError, match="no spatial upscaler loaded"):
        _pipe().process(PipeInput(input={"model": bundle, "latent": latent}), lambda o: None)


def test_raises_when_neither_latent_nor_video_given():
    bundle = _bundle()
    with pytest.raises(ValueError, match="requires either a 'latent' or a 'video'"):
        _pipe().process(PipeInput(input={"model": bundle}), lambda o: None)


def test_upsample_calls_unnormalize_then_upsampler_then_normalize_in_order():
    bundle = _bundle()
    latent = torch.randn(1, 8, 3, 4, 4)
    out = _pipe(device="cpu").process(PipeInput(input={"model": bundle, "latent": latent}), lambda o: None)

    calls = bundle.vae.module.per_channel_statistics.calls
    assert [c[0] for c in calls] == ["un_normalize", "normalize"]
    # The upsampler's forward saw the UN-normalized latent (un_normalize output = latent + 1.0).
    assert torch.allclose(bundle.upsampler.module.calls[0], latent + 1.0)
    assert out.output["latent"].shape == (1, 8, 3, 8, 8)  # spatial x2, frames preserved


def test_upsample_moves_and_offloads_both_components():
    bundle = _bundle()
    latent = torch.randn(1, 8, 3, 4, 4)
    _pipe(device="cpu").process(PipeInput(input={"model": bundle, "latent": latent}), lambda o: None)
    assert bundle.vae.moved_to == ["cpu"]
    assert bundle.upsampler.moved_to == ["cpu"]
    assert bundle.vae.offloaded == 1
    assert bundle.upsampler.offloaded == 1


def test_offload_happens_even_if_upsampler_raises():
    bundle = _bundle()

    def boom(z):
        raise RuntimeError("boom")

    bundle.upsampler.module = boom
    latent = torch.randn(1, 8, 3, 4, 4)
    with pytest.raises(RuntimeError, match="boom"):
        _pipe(device="cpu").process(PipeInput(input={"model": bundle, "latent": latent}), lambda o: None)
    assert bundle.vae.offloaded == 1
    assert bundle.upsampler.offloaded == 1


def test_video_input_encodes_then_upsamples():
    bundle = _bundle()
    frames = torch.rand(9, 64, 96, 3)  # (n, H, W, 3) already-valid granularity
    with patch("src.pipelines.pipes.latent_upscaler.ltx.main._load_video_frames", return_value=frames) as mock_load:
        out = _pipe(device="cpu").process(
            PipeInput(input={"model": bundle, "video": ["fake.mp4"]}), lambda o: None)
    mock_load.assert_called_once_with("fake.mp4", 1001)
    assert out.output["latent"].shape == (1, 8, 3, 8, 8)  # encode() stub returns (1,8,3,4,4) -> upsampled x2


def test_latent_input_takes_priority_over_video():
    bundle = _bundle()
    latent = torch.randn(1, 8, 3, 4, 4)
    with patch("src.pipelines.pipes.latent_upscaler.ltx.main._load_video_frames") as mock_load:
        _pipe(device="cpu").process(
            PipeInput(input={"model": bundle, "latent": latent, "video": ["fake.mp4"]}), lambda o: None)
    mock_load.assert_not_called()


# -- source_frame_count output (audio/video desync -- pad-trim plumbing) --

def test_source_frame_count_is_none_on_direct_latent_path():
    """No video was decoded here (in-flow two-stage) -- there is no pre-padding
    frame count to report, so the output must be None, not e.g. 0."""
    bundle = _bundle()
    latent = torch.randn(1, 8, 3, 4, 4)
    out = _pipe(device="cpu").process(PipeInput(input={"model": bundle, "latent": latent}), lambda o: None)
    assert out.output["source_frame_count"] is None


def test_source_frame_count_reports_pre_padding_count_on_video_path():
    """A 120-frame source pads to 121 before VAE-encode (see
    _pad_frames_to_temporal_grid) -- source_frame_count must surface the
    ORIGINAL 120, not the padded 121, so the downstream generator can trim
    the padding back out of its own decoded output."""
    bundle = _bundle()
    frames = torch.rand(120, 64, 96, 3)  # already-valid spatial dims, invalid temporal count
    with patch("src.pipelines.pipes.latent_upscaler.ltx.main._load_video_frames", return_value=frames):
        out = _pipe(device="cpu").process(
            PipeInput(input={"model": bundle, "video": ["fake.mp4"]}), lambda o: None)
    assert out.output["source_frame_count"] == 120


def test_source_frame_count_matches_source_when_already_on_temporal_grid():
    bundle = _bundle()
    frames = torch.rand(9, 64, 96, 3)  # 9 == 1 + 1*8, already valid -- no padding needed
    with patch("src.pipelines.pipes.latent_upscaler.ltx.main._load_video_frames", return_value=frames):
        out = _pipe(device="cpu").process(
            PipeInput(input={"model": bundle, "video": ["fake.mp4"]}), lambda o: None)
    assert out.output["source_frame_count"] == 9


# -- frame-count / resolution snapping on _encode_video --

def test_pad_frames_to_temporal_grid_pads_by_repeating_last_frame():
    # A real-world case: 120 raw frames, temporal_downscale=8.
    # (120 - 1) % 8 == 7, so pad == 1 -> 121 (== 1 + 15*8), never truncated.
    frames = torch.arange(120).float().reshape(120, 1, 1, 1).expand(120, 2, 2, 3).contiguous()
    padded = _pad_frames_to_temporal_grid(frames, 8)
    assert padded.shape[0] == 121
    assert torch.equal(padded[:120], frames)
    # The padding frame is an exact repeat of the source's last frame.
    assert torch.equal(padded[120], frames[119])


def test_pad_frames_to_temporal_grid_noop_when_already_valid():
    frames = torch.rand(121, 4, 4, 3)  # 121 == 1 + 15*8, already on the grid
    padded = _pad_frames_to_temporal_grid(frames, 8)
    assert padded.shape[0] == 121
    assert torch.equal(padded, frames)


def test_pad_frames_to_temporal_grid_worst_case_pads_seven():
    frames = torch.rand(122, 4, 4, 3)  # next valid count is 129 (1 + 16*8)
    padded = _pad_frames_to_temporal_grid(frames, 8)
    assert padded.shape[0] == 129
    assert torch.equal(padded[:122], frames)
    assert torch.equal(padded[122:], frames[-1:].expand(7, 4, 4, 3))


def test_encode_video_pads_odd_frame_count_before_vae_encode():
    """The exact repro: a 120-frame source must reach the VAE's
    ``encode()`` at a valid T (121), not the raw 120 that crashed live."""
    stats = _RecordingStats()
    seen = {}

    def recording_encode(pixels):
        seen["shape"] = tuple(pixels.shape)
        return torch.zeros(1, 8, 3, 4, 4)

    vae_module = SimpleNamespace(per_channel_statistics=stats, encode=recording_encode)
    vae = _RecordingComponent(vae_module)
    upsampler = _RecordingComponent(_UpsamplerModule())
    bundle = SimpleNamespace(vae=vae, upsampler=upsampler)

    frames = torch.rand(120, 64, 96, 3)  # 120 raw frames, already-valid spatial dims
    with patch("src.pipelines.pipes.latent_upscaler.ltx.main._load_video_frames", return_value=frames):
        _pipe(device="cpu").process(
            PipeInput(input={"model": bundle, "video": ["fake.mp4"]}), lambda o: None)

    # (1, 3, n, H, W) after _resize_cover_center_crop -- n must be 121, not 120.
    assert seen["shape"] == (1, 3, 121, 64, 96)


def test_encode_video_frees_source_frames_before_vae_encode():
    """Host-RAM audit: `_encode_video` used to keep the loaded
    CPU ``frames`` tensor (and the resize/crop ``pixels`` tensor) referenced
    as local variables for the entire method body -- including through the
    VAE ``encode()`` call itself -- since nothing ever dereferenced them
    early. For a long/high-res standalone-upscale source that's multiple GB
    of host RAM held alive for no reason once the tensor has been handed off
    to the compute device. This proves the ORIGINAL loaded frames tensor is
    unreachable (refcount-collectable) by the time ``encode()`` runs.

    The mock's ``side_effect`` (not ``return_value``) is load-bearing: it
    must not itself retain a strong reference to the tensor it hands back,
    or the weakref below could never clear regardless of what `_encode_video`
    does with its own local.
    """
    frames_ref: dict = {}

    def fake_load(path, max_frames):
        t = torch.rand(9, 64, 96, 3)  # already-valid frame count (1 + 1*8)
        frames_ref["ref"] = weakref.ref(t)
        return t

    def recording_encode(pixels):
        gc.collect()
        assert frames_ref["ref"]() is None, (
            "source frames tensor is still alive when encode() runs -- "
            "_encode_video must del its 'frames' local once pixels are derived"
        )
        return torch.zeros(1, 8, 3, 4, 4)

    stats = _RecordingStats()
    vae_module = SimpleNamespace(per_channel_statistics=stats, encode=recording_encode)
    vae = _RecordingComponent(vae_module)
    upsampler = _RecordingComponent(_UpsamplerModule())
    bundle = SimpleNamespace(vae=vae, upsampler=upsampler)

    with patch("src.pipelines.pipes.latent_upscaler.ltx.main._load_video_frames", side_effect=fake_load):
        _pipe(device="cpu").process(
            PipeInput(input={"model": bundle, "video": ["fake.mp4"]}), lambda o: None)


def test_encode_video_snaps_arbitrary_resolution_to_32px_grid():
    """An 811x455 upload (not on the 32px grid) must not reach the VAE raw --
    snap_resolution rounds each axis to the nearest 32px multiple before the
    resize+crop, so encode() always sees a valid (H, W)."""
    stats = _RecordingStats()
    seen = {}

    def recording_encode(pixels):
        seen["shape"] = tuple(pixels.shape)
        return torch.zeros(1, 8, 3, 4, 4)

    vae_module = SimpleNamespace(per_channel_statistics=stats, encode=recording_encode)
    vae = _RecordingComponent(vae_module)
    upsampler = _RecordingComponent(_UpsamplerModule())
    bundle = SimpleNamespace(vae=vae, upsampler=upsampler)

    frames = torch.rand(9, 455, 811, 3)  # (n, H0, W0, 3), 9 already a valid frame count
    with patch("src.pipelines.pipes.latent_upscaler.ltx.main._load_video_frames", return_value=frames):
        _pipe(device="cpu").process(
            PipeInput(input={"model": bundle, "video": ["fake.mp4"]}), lambda o: None)

    _, _, n, h, w = seen["shape"]
    assert n == 9
    assert h % 32 == 0 and w % 32 == 0


# -- VRAM-OOM fix: evict the parked DiT before this pipe's own
# GPU work, and retry the VAE encode once after an OOM. No real GPU/CUDA
# needed -- `get_residency_manager`/`clear_gpu_memory`/`free_vram_gb` are
# patched at the module boundary (same style as `test_dit_placement.py`), and
# OOM is simulated by raising the real `torch.cuda.OutOfMemoryError` class
# directly (constructing/raising it needs no actual device).

def test_free_room_for_upscale_noop_on_non_cuda_device():
    dit = _RecordingComponent(SimpleNamespace(), device="cuda")  # parked resident
    bundle = _bundle(dit=dit)
    manager = _FakeResidencyManager()
    with patch(f"{_MOD}.get_residency_manager", return_value=manager), \
         patch(f"{_MOD}.clear_gpu_memory") as mock_clear:
        _free_room_for_upscale(bundle, "cpu")
    assert dit.offloaded == 0
    assert manager.offload_all_calls == []
    mock_clear.assert_not_called()


def test_free_room_for_upscale_offloads_resident_dit_and_evicts_foreign_residents():
    dit = _RecordingComponent(SimpleNamespace(), device="cuda")  # dit_restore.py's warm-start
    bundle = _bundle(dit=dit)
    manager = _FakeResidencyManager()
    with patch(f"{_MOD}.get_residency_manager", return_value=manager), \
         patch(f"{_MOD}.clear_gpu_memory") as mock_clear:
        _free_room_for_upscale(bundle, "cuda")
    assert dit.offloaded == 1
    assert manager.offload_all_calls == [("cuda", (bundle.vae, bundle.upsampler))]
    mock_clear.assert_called_once()


def test_free_room_for_upscale_skips_dit_offload_when_not_resident():
    dit = _RecordingComponent(SimpleNamespace())  # device defaults to "cpu" -- nothing to evict
    bundle = _bundle(dit=dit)
    manager = _FakeResidencyManager()
    with patch(f"{_MOD}.get_residency_manager", return_value=manager), \
         patch(f"{_MOD}.clear_gpu_memory") as mock_clear:
        _free_room_for_upscale(bundle, "cuda")
    assert dit.offloaded == 0
    # offload_all/clear_gpu_memory still run unconditionally -- other foreign
    # residents besides the DiT may exist.
    assert manager.offload_all_calls == [("cuda", (bundle.vae, bundle.upsampler))]
    mock_clear.assert_called_once()


def test_free_room_for_upscale_handles_bundle_with_no_dit_attribute():
    bundle = _bundle()  # dit=None (the pipe's own bundle never carries a dit)
    manager = _FakeResidencyManager()
    with patch(f"{_MOD}.get_residency_manager", return_value=manager), \
         patch(f"{_MOD}.clear_gpu_memory") as mock_clear:
        _free_room_for_upscale(bundle, "cuda")
    assert manager.offload_all_calls == [("cuda", (bundle.vae, bundle.upsampler))]
    mock_clear.assert_called_once()


def test_process_calls_free_room_for_upscale_exactly_once():
    """Covers both entry shapes with one call site: the
    freed window from a single call must cover the encode (video path) AND
    the upsampler forward that always follows, without a second eviction
    pass in between."""
    bundle = _bundle()
    latent = torch.randn(1, 8, 3, 4, 4)
    with patch(f"{_MOD}._free_room_for_upscale") as mock_free:
        _pipe(device="cpu").process(PipeInput(input={"model": bundle, "latent": latent}), lambda o: None)
    mock_free.assert_called_once_with(bundle, "cpu", None)  # no MODELS service wired in this test


def test_process_offloads_resident_dit_before_video_encode_runs():
    """Integration repro of the live crash: a parked-resident DiT
    (dit_restore.py's warm-start) must be offloaded by `process()` BEFORE
    `_encode_video`'s `vae.encode()` call actually runs."""
    dit = _RecordingComponent(SimpleNamespace(), device="cuda")
    seen = {}

    def recording_encode(pixels):
        seen["dit_device_at_encode"] = dit.device
        return torch.zeros(1, 8, 3, 4, 4)

    stats = _RecordingStats()
    vae_module = SimpleNamespace(per_channel_statistics=stats, encode=recording_encode)
    vae = _RecordingComponent(vae_module)
    upsampler = _RecordingComponent(_UpsamplerModule())
    bundle = SimpleNamespace(vae=vae, upsampler=upsampler, dit=dit)

    frames = torch.rand(9, 64, 96, 3)  # already-valid granularity
    manager = _FakeResidencyManager()
    with patch(f"{_MOD}._load_video_frames", return_value=frames), \
         patch(f"{_MOD}.get_residency_manager", return_value=manager), \
         patch(f"{_MOD}.clear_gpu_memory"), \
         patch(f"{_SHARED_MOD}.free_vram_gb", return_value=10.0):
        _pipe(device="cuda").process(
            PipeInput(input={"model": bundle, "video": ["fake.mp4"]}), lambda o: None)

    assert dit.offloaded == 1
    assert seen["dit_device_at_encode"] == "cpu"
    # The very first eviction sweep (process()'s single _free_room_for_upscale
    # call) excludes this pipe's own vae/upsampler, not the dit.
    assert manager.offload_all_calls[0] == ("cuda", (vae, upsampler))


def test_encode_with_oom_retry_succeeds_after_one_eviction():
    calls = {"n": 0}

    def flaky_encode(pixels):
        calls["n"] += 1
        if calls["n"] == 1:
            raise torch.cuda.OutOfMemoryError("boom")
        return torch.zeros(1, 8, 3, 4, 4)

    stats = _RecordingStats()
    vae_module = SimpleNamespace(per_channel_statistics=stats, encode=flaky_encode)
    vae = _RecordingComponent(vae_module)
    bundle = SimpleNamespace(vae=vae, upsampler=_RecordingComponent(_UpsamplerModule()))
    pixels = torch.rand(1, 3, 9, 64, 96)

    manager = _FakeResidencyManager()
    with patch(f"{_SHARED_MOD}.get_residency_manager", return_value=manager), \
         patch(f"{_SHARED_MOD}.clear_gpu_memory") as mock_clear, \
         patch(f"{_SHARED_MOD}.free_vram_gb", return_value=1.0):
        latent = LatentUpscalerLtxPipe._encode_with_oom_retry(bundle, pixels, "cuda")

    assert calls["n"] == 2  # first call OOM'd, retry succeeded
    assert manager.offload_all_calls == [("cuda", (vae,))]
    mock_clear.assert_called_once()
    assert latent.shape == (1, 8, 3, 4, 4)


def test_encode_with_oom_retry_raises_clear_error_if_still_oom_after_retry():
    """When whole-clip encode OOMs even after the eviction
    retry, the ladder falls back to `tiled_encode` -- only when THAT also
    OOMs does this finally raise."""
    def always_oom(pixels):
        raise torch.cuda.OutOfMemoryError("boom")

    def tiled_always_oom(pixels, tiling_config):
        raise torch.cuda.OutOfMemoryError("boom (tiled)")

    stats = _RecordingStats()
    vae_module = SimpleNamespace(per_channel_statistics=stats, encode=always_oom, tiled_encode=tiled_always_oom)
    vae = _RecordingComponent(vae_module)
    bundle = SimpleNamespace(vae=vae, upsampler=_RecordingComponent(_UpsamplerModule()))
    pixels = torch.rand(1, 3, 9, 64, 96)

    manager = _FakeResidencyManager()
    with patch(f"{_SHARED_MOD}.get_residency_manager", return_value=manager), \
         patch(f"{_SHARED_MOD}.clear_gpu_memory"), \
         patch(f"{_SHARED_MOD}.free_vram_gb", return_value=1.0):
        with pytest.raises(torch.cuda.OutOfMemoryError, match="even with tiled encoding"):
            LatentUpscalerLtxPipe._encode_with_oom_retry(bundle, pixels, "cuda")

    # Only the single eviction pass from the whole-clip retry -- no second
    # eviction call before the tiled attempt (see _encode_with_oom_retry).
    assert manager.offload_all_calls == [("cuda", (vae,))]


def test_encode_with_oom_retry_falls_back_to_tiled_when_whole_clip_still_oom():
    """The tiled fallback actually gets used (and returns its result) when
    whole-clip encode OOMs both times."""
    whole_calls = {"n": 0}

    def whole_always_oom(pixels):
        whole_calls["n"] += 1
        raise torch.cuda.OutOfMemoryError("boom")

    tiled_calls = []

    def tiled_encode(pixels, tiling_config):
        tiled_calls.append(tiling_config)
        return torch.zeros(1, 8, 3, 4, 4)

    stats = _RecordingStats()
    vae_module = SimpleNamespace(per_channel_statistics=stats, encode=whole_always_oom, tiled_encode=tiled_encode)
    vae = _RecordingComponent(vae_module)
    bundle = SimpleNamespace(vae=vae, upsampler=_RecordingComponent(_UpsamplerModule()))
    pixels = torch.rand(1, 3, 9, 64, 96)

    manager = _FakeResidencyManager()
    with patch(f"{_SHARED_MOD}.get_residency_manager", return_value=manager), \
         patch(f"{_SHARED_MOD}.clear_gpu_memory"), \
         patch(f"{_SHARED_MOD}.free_vram_gb", return_value=1.0):
        latent = LatentUpscalerLtxPipe._encode_with_oom_retry(bundle, pixels, "cuda")

    assert whole_calls["n"] == 2  # initial attempt + the one eviction retry
    assert len(tiled_calls) == 1
    assert tiled_calls[0] == LtxTilingConfig.default()  # default tiling config when none passed
    assert latent.shape == (1, 8, 3, 4, 4)


def test_encode_with_oom_retry_skips_whole_clip_when_estimate_exceeds_budget():
    """When the T*H*W activation estimate exceeds the free-VRAM
    budget, the ladder skips the whole-clip attempt entirely and goes
    straight to tiled_encode -- `encode` must never be called."""
    encode_calls = []

    def whole_encode(pixels):
        encode_calls.append(pixels)
        return torch.zeros(1, 8, 3, 4, 4)  # would succeed if ever called

    tiled_calls = []

    def tiled_encode(pixels, tiling_config):
        tiled_calls.append(tiling_config)
        return torch.zeros(1, 8, 3, 4, 4)

    stats = _RecordingStats()
    vae_module = SimpleNamespace(per_channel_statistics=stats, encode=whole_encode, tiled_encode=tiled_encode)
    vae = _RecordingComponent(vae_module)
    bundle = SimpleNamespace(vae=vae, upsampler=_RecordingComponent(_UpsamplerModule()))
    pixels = torch.rand(1, 3, 9, 64, 96)  # T*H*W=55296 -> ~0.03GB estimate

    manager = _FakeResidencyManager()
    # free_vram=0.01GB -> budget=0.0075GB, well under the ~0.03GB estimate.
    with patch(f"{_SHARED_MOD}.get_residency_manager", return_value=manager), \
         patch(f"{_SHARED_MOD}.clear_gpu_memory") as mock_clear, \
         patch(f"{_SHARED_MOD}.free_vram_gb", return_value=0.01):
        latent = LatentUpscalerLtxPipe._encode_with_oom_retry(bundle, pixels, "cuda")

    assert encode_calls == []  # whole-clip attempt never made
    assert len(tiled_calls) == 1
    assert manager.offload_all_calls == [("cuda", (vae,))]
    mock_clear.assert_called_once()
    assert latent.shape == (1, 8, 3, 4, 4)


def test_encode_with_oom_retry_uses_provided_tiling_config():
    """A caller-supplied tiling_config is threaded through to tiled_encode
    rather than always substituting the default."""
    custom = LtxTilingConfig.default()
    seen = {}

    def tiled_encode(pixels, tiling_config):
        seen["tiling_config"] = tiling_config
        return torch.zeros(1, 8, 3, 4, 4)

    stats = _RecordingStats()
    vae_module = SimpleNamespace(
        per_channel_statistics=stats,
        encode=lambda pixels: (_ for _ in ()).throw(torch.cuda.OutOfMemoryError("boom")),
        tiled_encode=tiled_encode,
    )
    vae = _RecordingComponent(vae_module)
    bundle = SimpleNamespace(vae=vae, upsampler=_RecordingComponent(_UpsamplerModule()))
    pixels = torch.rand(1, 3, 9, 64, 96)

    manager = _FakeResidencyManager()
    with patch(f"{_SHARED_MOD}.get_residency_manager", return_value=manager), \
         patch(f"{_SHARED_MOD}.clear_gpu_memory"), \
         patch(f"{_SHARED_MOD}.free_vram_gb", return_value=1.0):
        LatentUpscalerLtxPipe._encode_with_oom_retry(bundle, pixels, "cuda", tiling_config=custom)

    assert seen["tiling_config"] is custom


# -- Host-RAM fix: unload the idle TE + release the stale
# pinned-host pool as part of the same free-room pass. No real GPU/CUDA
# needed -- `get_residency_manager`/`clear_gpu_memory`/`get_profiler` are
# patched at the module boundary, same style as the tests above.

class _FakeModels:
    """Stand-in for the MODELS lifecycle service: records every
    `evict_dead_weight` call and returns a caller-controlled result."""

    def __init__(self, unloads=True):
        self._unloads = unloads
        self.evict_calls = []

    def evict_dead_weight(self, key):
        self.evict_calls.append(key)
        return self._unloads


class _TeModule:
    def __init__(self, n=4, dim=8):
        self._params = [torch.randn(dim, dim) for _ in range(n)]

    def parameters(self):
        return iter(self._params)


def _te(dim=8, n=4):
    return _RecordingComponent(_TeModule(n=n, dim=dim))


def test_te_ram_gb_sums_parameter_bytes():
    te = _te(dim=8, n=4)  # 4 * 8*8 * 4 bytes (float32) = 1024 bytes
    assert _te_ram_gb(te) == pytest.approx(1024 / (1 << 30))


def test_te_ram_gb_handles_missing_module():
    assert _te_ram_gb(None) == 0.0
    assert _te_ram_gb(SimpleNamespace(module=None)) == 0.0


def test_unload_idle_te_noop_when_bundle_has_no_cache_key():
    bundle = _bundle()  # SimpleNamespace, no te_cache_key attribute
    models = _FakeModels()
    assert _unload_idle_te(bundle, models) == 0.0
    assert models.evict_calls == []


def test_unload_idle_te_noop_when_models_not_injected():
    bundle = SimpleNamespace(te_cache_key="native/te/foo.safetensors", te=_te())
    assert _unload_idle_te(bundle, None) == 0.0


def test_unload_idle_te_evicts_by_cache_key_and_returns_freed_gb():
    te = _te(dim=8, n=4)
    bundle = SimpleNamespace(te_cache_key="native/te/foo.safetensors", te=te)
    models = _FakeModels(unloads=True)

    freed = _unload_idle_te(bundle, models)

    assert models.evict_calls == ["native/te/foo.safetensors"]
    assert freed == pytest.approx(_te_ram_gb(te))
    assert freed > 0.0


def test_unload_idle_te_returns_zero_when_entry_was_not_unloaded():
    """The TE was still referenced elsewhere (or already gone) -- evict_dead_
    weight reports False; nothing should be counted as freed."""
    te = _te()
    bundle = SimpleNamespace(te_cache_key="native/te/foo.safetensors", te=te)
    models = _FakeModels(unloads=False)

    freed = _unload_idle_te(bundle, models)

    assert models.evict_calls == ["native/te/foo.safetensors"]
    assert freed == 0.0


def test_unload_idle_te_survives_models_without_evict_dead_weight():
    """A MODELS stand-in that doesn't (yet) implement evict_dead_weight must
    degrade to a no-op rather than raising AttributeError."""
    bundle = SimpleNamespace(te_cache_key="native/te/foo.safetensors", te=_te())
    assert _unload_idle_te(bundle, object()) == 0.0


def test_unload_idle_te_survives_eviction_raising():
    class _BoomModels:
        def evict_dead_weight(self, key):
            raise RuntimeError("boom")

    bundle = SimpleNamespace(te_cache_key="native/te/foo.safetensors", te=_te())
    assert _unload_idle_te(bundle, _BoomModels()) == 0.0


def test_free_room_for_upscale_unloads_te_and_empties_pinned_cache_on_cuda():
    te = _te()
    dit = _RecordingComponent(SimpleNamespace(), device="cuda")
    bundle = _bundle(dit=dit)
    bundle.te = te
    bundle.te_cache_key = "native/te/foo.safetensors"
    models = _FakeModels(unloads=True)
    residency = _FakeResidencyManager()

    with patch(f"{_MOD}.get_residency_manager", return_value=residency), \
         patch(f"{_MOD}.clear_gpu_memory"), \
         patch(f"{_MOD}.empty_pinned_host_cache") as mock_empty:
        _free_room_for_upscale(bundle, "cuda", models)

    assert models.evict_calls == ["native/te/foo.safetensors"]
    mock_empty.assert_called_once()


def test_free_room_for_upscale_unloads_te_even_on_non_cuda_device():
    """The TE unload / pinned-cache release are HOST-RAM work, independent of
    `device` -- must still run on a CPU-configured pipe (unlike the GPU
    eviction sweep, which is a genuine no-op there)."""
    bundle = _bundle()
    bundle.te = _te()
    bundle.te_cache_key = "native/te/foo.safetensors"
    models = _FakeModels(unloads=True)

    with patch(f"{_MOD}.empty_pinned_host_cache") as mock_empty:
        _free_room_for_upscale(bundle, "cpu", models)

    assert models.evict_calls == ["native/te/foo.safetensors"]
    mock_empty.assert_called_once()


def test_free_room_for_upscale_reports_te_and_pinned_fields_on_profiler_mark():
    te = _te()
    dit = _RecordingComponent(SimpleNamespace(), device="cuda")
    bundle = _bundle(dit=dit)
    bundle.te = te
    bundle.te_cache_key = "native/te/foo.safetensors"
    models = _FakeModels(unloads=True)
    residency = _FakeResidencyManager()
    fake_profiler = Mock()

    with patch(f"{_MOD}.get_residency_manager", return_value=residency), \
         patch(f"{_MOD}.clear_gpu_memory"), \
         patch(f"{_MOD}.get_profiler", return_value=fake_profiler):
        _free_room_for_upscale(bundle, "cuda", models)

    marks = {call.args[0]: call.kwargs for call in fake_profiler.mark.call_args_list}
    assert "ltx_upscale.free_room" in marks
    fields = marks["ltx_upscale.free_room"]
    assert fields["te_unloaded_gb"] == pytest.approx(_te_ram_gb(te), abs=1e-6)
    assert fields["pinned_emptied"] is True
    assert fields["dit_was_resident"] is True


def test_free_room_for_upscale_te_unloaded_gb_zero_when_no_models_service():
    bundle = _bundle()  # no te_cache_key, no MODELS
    residency = _FakeResidencyManager()
    fake_profiler = Mock()

    with patch(f"{_MOD}.get_residency_manager", return_value=residency), \
         patch(f"{_MOD}.clear_gpu_memory"), \
         patch(f"{_MOD}.get_profiler", return_value=fake_profiler):
        _free_room_for_upscale(bundle, "cuda")  # models defaults to None

    fields = {c.args[0]: c.kwargs for c in fake_profiler.mark.call_args_list}["ltx_upscale.free_room"]
    assert fields["te_unloaded_gb"] == 0.0


def test_free_room_for_upscale_fires_a_mid_run_census_right_after_te_eviction():
    """The "34GB conditioning zombie" follow-up: a killed run never
    reaches GenerationProfiler.stop() (SIGKILL skips it entirely), so this
    fires an ad-hoc census right after `_unload_idle_te` -- the exact moment
    a future regression of the same kind (a reported-unloaded component that
    didn't actually free RAM) would need to be visible in profile.jsonl for
    a run that gets killed moments later."""
    te = _te()
    dit = _RecordingComponent(SimpleNamespace(), device="cuda")
    bundle = _bundle(dit=dit)
    bundle.te = te
    bundle.te_cache_key = "native/te/foo.safetensors"
    models = _FakeModels(unloads=True)
    residency = _FakeResidencyManager()
    fake_profiler = Mock()

    with patch(f"{_MOD}.get_residency_manager", return_value=residency), \
         patch(f"{_MOD}.clear_gpu_memory"), \
         patch(f"{_MOD}.get_profiler", return_value=fake_profiler):
        _free_room_for_upscale(bundle, "cuda", models)

    fake_profiler.census_now.assert_called_once_with("ltx_upscale.te_evicted")


def test_free_room_for_upscale_fires_census_even_on_non_cuda_device():
    """The census (like the TE unload itself) is HOST-RAM diagnostics,
    independent of `device` -- must still fire on a CPU-configured pipe."""
    bundle = _bundle()
    bundle.te = _te()
    bundle.te_cache_key = "native/te/foo.safetensors"
    models = _FakeModels(unloads=True)
    fake_profiler = Mock()

    with patch(f"{_MOD}.empty_pinned_host_cache"), \
         patch(f"{_MOD}.get_profiler", return_value=fake_profiler):
        _free_room_for_upscale(bundle, "cpu", models)

    fake_profiler.census_now.assert_called_once_with("ltx_upscale.te_evicted")


def test_encode_with_oom_retry_no_eviction_needed_on_first_success():
    stats = _RecordingStats()
    vae_module = SimpleNamespace(per_channel_statistics=stats, encode=lambda pixels: torch.zeros(1, 8, 3, 4, 4))
    vae = _RecordingComponent(vae_module)
    bundle = SimpleNamespace(vae=vae, upsampler=_RecordingComponent(_UpsamplerModule()))
    pixels = torch.rand(1, 3, 9, 64, 96)

    manager = _FakeResidencyManager()
    with patch(f"{_SHARED_MOD}.get_residency_manager", return_value=manager), \
         patch(f"{_SHARED_MOD}.clear_gpu_memory") as mock_clear, \
         patch(f"{_SHARED_MOD}.free_vram_gb", return_value=20.0):
        latent = LatentUpscalerLtxPipe._encode_with_oom_retry(bundle, pixels, "cuda")

    assert manager.offload_all_calls == []
    mock_clear.assert_not_called()
    assert latent.shape == (1, 8, 3, 4, 4)


class _TemporalUpsamplerModule:
    """Stand-in for a temporal-declaring ``LTXLatentUpsampler``: T -> 2T-1
    frames, spatial dims untouched (see that arch module's docstring)."""

    temporal_upsample = True
    spatial_upsample = False

    def __init__(self):
        self.calls = []

    def __call__(self, z):
        self.calls.append(z.clone())
        b, c, f, h, w = z.shape
        return torch.zeros(b, c, 2 * f - 1, h, w)


class _SpatialDeclaringModule(_UpsamplerModule):
    temporal_upsample = False
    spatial_upsample = True


def _dual_bundle(*, spatial=True, temporal=True):
    stats = _RecordingStats()
    vae_module = SimpleNamespace(per_channel_statistics=stats,
                                 encode=lambda pixels: torch.zeros(1, 8, 3, 4, 4))
    return SimpleNamespace(
        vae=_RecordingComponent(vae_module),
        upsampler=_RecordingComponent(_SpatialDeclaringModule()) if spatial else None,
        temporal_upsampler=_RecordingComponent(_TemporalUpsamplerModule()) if temporal else None,
        dit=None,
    )


class TestModeSelection:
    def test_mode_defaults_to_spatial(self):
        assert LatentUpscalerLtxPipe.get_default_config()["mode"] == "spatial"
        spec = {c.name: c for c in LatentUpscalerLtxPipe.configuration()}["mode"]
        assert spec.choices == ["spatial", "temporal"]

    def test_spatial_mode_runs_the_spatial_slot(self):
        bundle = _dual_bundle()
        latent = torch.randn(1, 8, 3, 4, 4)
        out = _pipe(device="cpu", mode="spatial").process(
            PipeInput(input={"model": bundle, "latent": latent}), lambda o: None)
        assert out.output["latent"].shape == (1, 8, 3, 8, 8)
        assert bundle.upsampler.module.calls
        assert not bundle.temporal_upsampler.module.calls

    def test_temporal_mode_runs_the_temporal_slot(self):
        bundle = _dual_bundle()
        latent = torch.randn(1, 8, 3, 4, 4)
        out = _pipe(device="cpu", mode="temporal").process(
            PipeInput(input={"model": bundle, "latent": latent}), lambda o: None)
        # T=3 -> 2T-1 = 5 latent frames; spatial untouched.
        assert out.output["latent"].shape == (1, 8, 5, 4, 4)
        assert bundle.temporal_upsampler.module.calls
        assert not bundle.upsampler.module.calls

    def test_temporal_mode_moves_and_offloads_the_temporal_slot_only(self):
        bundle = _dual_bundle()
        latent = torch.randn(1, 8, 3, 4, 4)
        _pipe(device="cpu", mode="temporal").process(
            PipeInput(input={"model": bundle, "latent": latent}), lambda o: None)
        assert bundle.temporal_upsampler.moved_to == ["cpu"]
        assert bundle.temporal_upsampler.offloaded == 1
        assert bundle.upsampler.moved_to == []

    def test_temporal_mode_without_the_slot_loaded_raises(self):
        bundle = _dual_bundle(temporal=False)
        latent = torch.randn(1, 8, 3, 4, 4)
        with pytest.raises(ValueError, match="temporal_upscale_model"):
            _pipe(device="cpu", mode="temporal").process(
                PipeInput(input={"model": bundle, "latent": latent}), lambda o: None)

    def test_temporal_mode_against_a_spatial_checkpoint_raises(self):
        """The silently-wrong case: a spatial checkpoint in the temporal slot
        would leave the frame count untouched."""
        bundle = _dual_bundle()
        bundle.temporal_upsampler = _RecordingComponent(_SpatialDeclaringModule())
        latent = torch.randn(1, 8, 3, 4, 4)
        with pytest.raises(ValueError, match="declares temporal_upsample=false"):
            _pipe(device="cpu", mode="temporal").process(
                PipeInput(input={"model": bundle, "latent": latent}), lambda o: None)

    def test_spatial_mode_against_a_temporal_checkpoint_raises(self):
        bundle = _dual_bundle()
        bundle.upsampler = _RecordingComponent(_TemporalUpsamplerModule())
        latent = torch.randn(1, 8, 3, 4, 4)
        with pytest.raises(ValueError, match="declares temporal_upsample=true"):
            _pipe(device="cpu", mode="spatial").process(
                PipeInput(input={"model": bundle, "latent": latent}), lambda o: None)

    def test_the_mismatch_check_runs_before_any_gpu_work(self):
        """A crossed slot must cost nothing -- no VAE move, no eviction."""
        bundle = _dual_bundle()
        bundle.temporal_upsampler = _RecordingComponent(_SpatialDeclaringModule())
        latent = torch.randn(1, 8, 3, 4, 4)
        with pytest.raises(ValueError):
            _pipe(device="cpu", mode="temporal").process(
                PipeInput(input={"model": bundle, "latent": latent}), lambda o: None)
        assert bundle.vae.moved_to == []

    def test_unknown_mode_raises(self):
        bundle = _dual_bundle()
        latent = torch.randn(1, 8, 3, 4, 4)
        with pytest.raises(ValueError, match="unknown mode"):
            _pipe(device="cpu", mode="diagonal").process(
                PipeInput(input={"model": bundle, "latent": latent}), lambda o: None)

    def test_temporal_mode_maps_source_frame_count_onto_the_new_timeline(self):
        """A 9-frame source already on the 1+8k lattice: no padding, but the
        content now spans 2*9-1 = 17 frames after the temporal round."""
        bundle = _dual_bundle()
        frames = torch.rand(9, 64, 96, 3)
        with patch(f"{_MOD}._load_video_frames", return_value=frames):
            out = _pipe(device="cpu", mode="temporal").process(
                PipeInput(input={"model": bundle, "video": ["v.mp4"]}), lambda o: None)
        assert out.output["source_frame_count"] == 17

    def test_spatial_mode_leaves_source_frame_count_alone(self):
        bundle = _dual_bundle()
        frames = torch.rand(9, 64, 96, 3)
        with patch(f"{_MOD}._load_video_frames", return_value=frames):
            out = _pipe(device="cpu", mode="spatial").process(
                PipeInput(input={"model": bundle, "video": ["v.mp4"]}), lambda o: None)
        assert out.output["source_frame_count"] == 9

    def test_latent_input_reports_no_source_frame_count_in_either_mode(self):
        for mode in ("spatial", "temporal"):
            bundle = _dual_bundle()
            out = _pipe(device="cpu", mode=mode).process(
                PipeInput(input={"model": bundle, "latent": torch.randn(1, 8, 3, 4, 4)}), lambda o: None)
            assert out.output["source_frame_count"] is None, mode
