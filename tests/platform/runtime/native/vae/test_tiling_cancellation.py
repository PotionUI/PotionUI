import pytest
import torch

from src.platform.runtime.native.cancellation import cancellation_scope
from src.platform.runtime.native.errors import SamplingCancelled
from src.platform.runtime.native.vae.tiling import tiled_decode_causal3d, tiled_encode, tiled_encode_causal3d


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
