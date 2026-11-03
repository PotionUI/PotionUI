import pytest
import torch

from src.platform.runtime.native.cancellation import cancellation_scope
from src.platform.runtime.native.errors import SamplingCancelled
from src.platform.runtime.native.vae.ltx_tiling import LtxTilingConfig, SpatialTilingConfig, TemporalTilingConfig
from src.platform.runtime.native.vae.minimax_h3_video import MiniMaxH3VideoVAE
from src.platform.runtime.native.vae.tiling import tiled_decode_causal3d, tiled_encode, tiled_encode_causal3d

from .test_ltx_diffusion_video import _build_tiny as _build_ltx_diffusion
from .test_ltx_tiling import _build_real_scale_tiny as _build_ltx_causal
from .test_minimax_music3_dav import _build as _build_music3_dav


class _CountingVae:
    z_channels = 16

    def __init__(self):
        self.calls = 0

    def encode(self, x):
        self.calls += 1
        if x.ndim == 4:
            b, _c, h, w = x.shape
            return torch.zeros((b, 16, h // 8, w // 8))
        b, _c, t, h, w = x.shape
        return torch.zeros((b, 16, t, h // 8, w // 8))

    def decode(self, z):
        self.calls += 1
        b, _c, t, h, w = z.shape
        return torch.zeros((b, 3, t, h * 8, w * 8))


@pytest.mark.parametrize(
    ("run", "tensor"),
    [
        (lambda vae, x: tiled_encode_causal3d(vae, x, tile_size=32, overlap=8), torch.zeros((1, 3, 1, 64, 64))),
        (lambda vae, x: tiled_decode_causal3d(vae, x, tile_size=8, overlap=2), torch.zeros((1, 16, 1, 16, 16))),
        (lambda vae, x: tiled_encode(vae, x, tile_size=32, overlap=8), torch.zeros((1, 3, 64, 64))),
    ],
)
def test_a_cancelled_generation_stops_between_two_tiles(run, tensor):
    vae = _CountingVae()

    with pytest.raises(SamplingCancelled) as caught:
        with cancellation_scope(lambda: vae.calls >= 1):
            run(vae, tensor)

    assert vae.calls == 1
    assert caught.value.block_index == 1


@pytest.mark.parametrize(
    ("run", "tensor"),
    [
        (lambda vae, x: tiled_encode_causal3d(vae, x, tile_size=32, overlap=8), torch.zeros((1, 3, 1, 64, 64))),
        (lambda vae, x: tiled_decode_causal3d(vae, x, tile_size=8, overlap=2), torch.zeros((1, 16, 1, 16, 16))),
    ],
)
def test_without_a_cancellation_scope_every_tile_runs(run, tensor):
    vae = _CountingVae()

    run(vae, tensor)

    assert vae.calls > 1


def test_a_scope_that_is_not_cancelled_lets_every_tile_run():
    vae = _CountingVae()

    with cancellation_scope(lambda: False):
        tiled_encode_causal3d(vae, torch.zeros((1, 3, 1, 64, 64)), tile_size=32, overlap=8)

    assert vae.calls > 1


def _count_calls(module, name: str, seen: list):
    original = getattr(module, name)

    def counting(*args, **kwargs):
        seen.append(name)
        return original(*args, **kwargs)

    setattr(module, name, counting)


def test_the_ltx_tiled_encode_stops_between_two_tiles():
    vae = _build_ltx_causal()
    seen: list = []
    _count_calls(vae, "encode", seen)
    tiling = LtxTilingConfig(
        spatial=SpatialTilingConfig(tile_size_in_pixels=96, tile_overlap_in_pixels=64),
        temporal=TemporalTilingConfig(tile_size_in_frames=24, tile_overlap_in_frames=16),
    )

    with torch.no_grad(), pytest.raises(SamplingCancelled) as caught:
        with cancellation_scope(lambda: len(seen) >= 1):
            vae.tiled_encode(torch.rand(1, 3, 33, 128, 128) * 2.0 - 1.0, tiling)

    assert len(seen) == 1
    assert caught.value.block_index == 1


def test_the_ltx_diffusion_tiled_decode_stops_between_two_tiles():
    module = _build_ltx_diffusion()
    module.enable_tiling(
        tile_sample_min_height=128, tile_sample_stride_height=96,
        tile_sample_min_width=128, tile_sample_stride_width=96,
        tile_sample_min_num_frames=24, tile_sample_stride_num_frames=24,
    )
    seen: list = []
    _count_calls(module.decoder, "denoise", seen)

    with torch.no_grad(), pytest.raises(SamplingCancelled) as caught:
        with cancellation_scope(lambda: len(seen) >= 1):
            module.decode(torch.randn(1, 8, 3, 8, 8), generator=torch.Generator().manual_seed(0))

    assert len(seen) == 1
    assert caught.value.block_index == 1


def test_the_minimax_h3_tile_batches_stop_between_two_groups():
    tiles = [torch.zeros(1, 1), torch.zeros(1, 1), torch.zeros(1, 1)]
    seen: list = []

    def forward(group):
        seen.append(len(group))
        return list(group)

    with pytest.raises(SamplingCancelled) as caught:
        with cancellation_scope(lambda: len(seen) >= 1):
            MiniMaxH3VideoVAE._run_tile_batches(tiles, 2, forward, "decode")

    assert seen == [2]
    assert caught.value.block_index == 2


def test_the_music3_tiled_decode_stops_between_two_tiles():
    module = _build_music3_dav()
    module.use_tiling = True
    module.tile_latents = 6
    seen: list = []
    _count_calls(module, "_decode_core", seen)

    with torch.no_grad(), pytest.raises(SamplingCancelled) as caught:
        with cancellation_scope(lambda: len(seen) >= 1):
            module.decode(torch.randn(1, module.latent_channels, 20))

    assert len(seen) == 1
    assert caught.value.block_index == 1
