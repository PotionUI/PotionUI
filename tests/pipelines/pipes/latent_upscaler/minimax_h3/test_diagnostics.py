from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import torch
from PIL import Image

from src.pipelines.contracts import PipeInput
from src.pipelines.pipes.latent_upscaler.minimax_h3 import diagnostics
from src.pipelines.pipes.latent_upscaler.minimax_h3.diagnostics import (
    ENCODED_PREVIEW,
    SOURCE_PREVIEW,
    UPSCALED_PREVIEW,
    diagnostics_dir,
    dump_upscale_previews,
    latent_stats,
    preview_latent_frames,
)
from src.pipelines.pipes.latent_upscaler.minimax_h3.main import LatentUpscalerMinimaxH3Pipe
from src.platform.runtime.native.kernels import disable_weight_init
from src.platform.runtime.native.vae.minimax_h3_latent_upsampler import MEAN, STD
from src.platform.runtime.native.vae.minimax_h3_video import MiniMaxH3VideoVAE

_MOD = "src.pipelines.pipes.latent_upscaler.minimax_h3.main"
_DIAG = "src.pipelines.pipes.latent_upscaler.minimax_h3.diagnostics"

_TINY_VAE = dict(
    latent_channels=24,
    block_out_channels=(8, 16),
    layers_per_block=1,
    spatial_downsample_factors=(2, 1),
    temporal_downsample_factors=(1, 2),
    norm_num_groups=2,
    decoder_num_layers=1,
    decoder_num_attention_heads=2,
    decoder_attention_head_dim=8,
    decoder_num_register_tokens=2,
    decoder_ffn_mult=2,
    clip_length=5,
    token_drop=1,
    tile_sample_min_height=8,
    tile_sample_min_width=8,
    tile_sample_min_overlap_height=4,
    tile_sample_min_overlap_width=4,
)


class _Component:
    def __init__(self, module):
        self.module = module
        self.compute_dtype = torch.float32
        self.moved_to = []
        self.offloaded = 0
        self.device = "cpu"

    def move_to(self, device):
        self.moved_to.append(device)

    def offload(self):
        self.offloaded += 1


def _tiny_vae() -> MiniMaxH3VideoVAE:
    torch.manual_seed(0)
    module = MiniMaxH3VideoVAE.from_config(_TINY_VAE, disable_weight_init)
    with torch.no_grad():
        for p in module.parameters():
            if p.is_floating_point():
                p.normal_(std=0.02)
    return module


class _Passthrough:
    def eval(self):
        return self

    def __call__(self, latent, *, scale, target_size):
        return latent


class _Profiler:
    def __init__(self, out_dir):
        self.out_dir = out_dir
        self.marks = []

    def mark(self, event, **fields):
        self.marks.append((event, fields))


@pytest.fixture
def profiled(tmp_path):
    profiler = _Profiler(tmp_path)
    with patch(f"{_DIAG}.profiling_enabled", return_value=True), \
         patch(f"{_DIAG}.get_profiler", return_value=profiler):
        yield profiler


def test_diagnostics_are_off_when_profiling_is_disabled(tmp_path):
    with patch(f"{_DIAG}.profiling_enabled", return_value=False), \
         patch(f"{_DIAG}.get_profiler", return_value=_Profiler(tmp_path)):
        assert diagnostics_dir() is None
        assert dump_upscale_previews(SimpleNamespace(), "cpu", None, None, torch.zeros(1, 24, 7, 2, 2)) == []
    assert list(tmp_path.iterdir()) == []


def test_diagnostics_are_off_while_no_profile_is_being_written():
    with patch(f"{_DIAG}.profiling_enabled", return_value=True), \
         patch(f"{_DIAG}.get_profiler", return_value=_Profiler(None)):
        assert diagnostics_dir() is None


def test_pipe_never_dumps_without_a_profile_dir():
    bundle = SimpleNamespace(
        video_vae=_Component(_tiny_vae()), upsampler=_Component(_Passthrough()),
        audio_vae=None, dit=None,
    )
    bundle.upsampler.compute_dtype = torch.float32
    with patch(f"{_MOD}.diagnostics_dir", return_value=None), \
         patch(f"{_MOD}.dump_upscale_previews") as dump:
        LatentUpscalerMinimaxH3Pipe(config={**LatentUpscalerMinimaxH3Pipe.get_default_config(), "device": "cpu",
                                            "target_mode": "scale", "scale": 2.0}).process(
            PipeInput(input={"model": bundle, "latent": torch.zeros(1, 24, 7, 2, 2)}), lambda o: None)
    dump.assert_not_called()


def test_pipe_dumps_the_encoded_and_the_upscaled_latent_when_profiling(tmp_path):
    bundle = SimpleNamespace(
        video_vae=_Component(_tiny_vae()), upsampler=_Component(_Passthrough()),
        audio_vae=None, dit=None,
    )
    upsampled = torch.ones(1, 24, 7, 4, 4)
    with patch(f"{_MOD}.diagnostics_dir", return_value=tmp_path), \
         patch.object(LatentUpscalerMinimaxH3Pipe, "_upsample", return_value=upsampled), \
         patch(f"{_MOD}.dump_upscale_previews") as dump:
        source = torch.zeros(1, 24, 7, 2, 2)
        LatentUpscalerMinimaxH3Pipe(config={**LatentUpscalerMinimaxH3Pipe.get_default_config(), "device": "cpu",
                                            "target_mode": "scale", "scale": 2.0}).process(
            PipeInput(input={"model": bundle, "latent": source}), lambda o: None)
    dump.assert_called_once()
    _, device, source_frame, encoded, upscaled = dump.call_args.args
    assert device == "cpu"
    assert source_frame is None
    assert encoded is source
    assert upscaled is upsampled


def test_preview_decodes_at_most_one_decoder_chunk_and_skips_an_undecodable_head():
    assert preview_latent_frames(32, 5, 3) == 7
    assert preview_latent_frames(7, 5, 3) == 7
    assert preview_latent_frames(4, 5, 3) == 4
    assert preview_latent_frames(3, 5, 3) == 3
    assert preview_latent_frames(2, 5, 3) == 0
    assert preview_latent_frames(5, 3, 1) == 5
    assert preview_latent_frames(2, 3, 1) == 0


def test_every_previewed_head_really_decodes_to_one_chunk():
    module = _tiny_vae()
    for frames in range(1, 13):
        head = preview_latent_frames(frames, module.tokens_chunk_size, module.token_drop)
        if head:
            assert module.decode_chunk_count(head) == 1
        else:
            assert module.decode_chunk_count(frames) == 1


def test_latent_stats_report_the_normalized_space_the_upsampler_and_refine_work_in():
    mean = torch.tensor(MEAN).view(1, -1, 1, 1, 1)
    std = torch.tensor(STD).view(1, -1, 1, 1, 1)
    torch.manual_seed(1)
    raw = torch.randn(1, 24, 4, 16, 16) * std + mean
    stats = latent_stats(raw)
    assert stats["norm_mean"] == pytest.approx(0.0, abs=0.05)
    assert stats["norm_std"] == pytest.approx(1.0, abs=0.05)
    assert stats["finite"] is True


def test_dump_writes_source_encoded_and_upscaled_previews_next_to_the_profile(profiled, tmp_path):
    vae = _Component(_tiny_vae())
    bundle = SimpleNamespace(video_vae=vae)
    torch.manual_seed(2)
    source_frame = torch.randn(3, 20, 12)
    encoded = torch.randn(1, 24, 5, 10, 6)
    upscaled = torch.randn(1, 24, 5, 20, 12)

    written = dump_upscale_previews(bundle, "cpu", source_frame, encoded, upscaled)

    names = sorted(Path(p).name for p in written)
    assert names == sorted([SOURCE_PREVIEW, ENCODED_PREVIEW, UPSCALED_PREVIEW])
    with Image.open(tmp_path / SOURCE_PREVIEW) as image:
        assert image.size == (12, 20)
    with Image.open(tmp_path / ENCODED_PREVIEW) as image:
        assert image.size == (12, 20)
    with Image.open(tmp_path / UPSCALED_PREVIEW) as image:
        assert image.size == (24, 40)
    stages = [fields["stage"] for event, fields in profiled.marks if event == "h3_upscale.latent_stats"]
    assert stages == ["encoded", "upscaled"]
    assert vae.moved_to == ["cpu"]
    assert vae.offloaded == 1


def test_dump_skips_a_latent_too_short_to_decode_and_never_raises(profiled, tmp_path):
    vae = _Component(_tiny_vae())
    written = dump_upscale_previews(
        SimpleNamespace(video_vae=vae), "cpu", None, torch.randn(1, 24, 2, 4, 4), torch.randn(1, 24, 2, 8, 8),
    )
    assert written == []
    assert vae.offloaded == 1


def test_dump_failure_is_logged_not_raised(profiled):
    def boom(device):
        raise RuntimeError("no device")

    vae = _Component(_tiny_vae())
    vae.move_to = boom
    assert dump_upscale_previews(SimpleNamespace(video_vae=vae), "cpu", None, None, torch.randn(1, 24, 5, 4, 4)) == []


def test_preview_images_are_capped_to_a_small_side():
    image = diagnostics.to_preview_image(torch.zeros(3, 1952, 1088))
    assert max(image.size) == diagnostics.PREVIEW_MAX_SIDE
