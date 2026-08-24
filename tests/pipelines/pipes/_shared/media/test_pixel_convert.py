"""Tests for the chunked (3,T,H,W) -> (T,H,W,3) uint8 converter.

The converter replaced a full-tensor op chain that OOM'd the GPU on long
high-resolution clips AFTER a successful VAE decode (2026-07-16 maintainer
repro: 10s 1056x1920, `(pixels + 1.0)` tried to allocate 5.64GiB). The
contract here is byte-identity with the old chain, for every dtype the
decoders emit, at every chunk boundary shape.
"""

import numpy as np
import pytest
import torch

from src.pipelines.pipes._shared.media.pixel_convert import pixels_3thw_to_uint8_frames


def _naive_reference(pixels: torch.Tensor) -> np.ndarray:
    """The exact pre-fix idiom from txt2vid_ltx/_decode_video."""
    p = pixels.clamp(-1.0, 1.0).float()
    p = ((p + 1.0) * 127.5).round().to(torch.uint8)
    return p.permute(1, 2, 3, 0).contiguous().cpu().numpy()


def _naive_reference_unit(pixels: torch.Tensor) -> np.ndarray:
    """The exact pre-fix idiom from video_minimax_h3's `_pixels_5d_to_uint8_frames`."""
    p = pixels.permute(1, 2, 3, 0).clamp(0.0, 1.0).mul(255.0).round().to(torch.uint8)
    return p.contiguous().cpu().numpy()


@pytest.mark.parametrize("value_range", ["signed", "unit"])
def test_chunked_matches_naive_for_both_ranges(value_range):
    torch.manual_seed(11)
    # values deliberately spill outside the valid range to exercise the clamp
    pixels = torch.randn(3, 11, 16, 24) * 1.5
    out = pixels_3thw_to_uint8_frames(pixels, chunk_frames=4, value_range=value_range)
    ref = _naive_reference(pixels) if value_range == "signed" else _naive_reference_unit(pixels)
    assert out.shape == (11, 16, 24, 3)
    assert out.dtype == np.uint8
    np.testing.assert_array_equal(out, ref)


def test_rejects_invalid_value_range():
    with pytest.raises(ValueError):
        pixels_3thw_to_uint8_frames(torch.zeros(3, 2, 2, 2), value_range="bogus")


@pytest.mark.parametrize("dtype", [torch.float32, torch.bfloat16, torch.float16])
def test_byte_identical_to_naive_chain(dtype):
    torch.manual_seed(7)
    # values deliberately spill outside [-1, 1] to exercise the clamp
    pixels = (torch.randn(3, 11, 16, 24) * 1.5).to(dtype)
    out = pixels_3thw_to_uint8_frames(pixels, chunk_frames=4)
    ref = _naive_reference(pixels)
    assert out.shape == (11, 16, 24, 3)
    assert out.dtype == np.uint8
    np.testing.assert_array_equal(out, ref)


@pytest.mark.parametrize("t,chunk", [(1, 32), (8, 8), (9, 8), (7, 3), (5, 1)])
def test_chunk_boundaries(t, chunk):
    torch.manual_seed(t)
    pixels = torch.rand(3, t, 8, 8) * 2.0 - 1.0
    out = pixels_3thw_to_uint8_frames(pixels, chunk_frames=chunk)
    np.testing.assert_array_equal(out, _naive_reference(pixels))


def test_source_tensor_not_mutated():
    pixels = torch.full((3, 4, 4, 4), 0.5, dtype=torch.float32)
    before = pixels.clone()
    pixels_3thw_to_uint8_frames(pixels, chunk_frames=2)
    # copy=True must protect the fp32 fast path from the in-place ops
    torch.testing.assert_close(pixels, before)


def test_extreme_values_clamped_not_wrapped():
    pixels = torch.tensor([10.0, -10.0, 1.0, -1.0, 0.0]).reshape(1, 1, 1, 5).repeat(3, 1, 1, 1)
    out = pixels_3thw_to_uint8_frames(pixels)
    assert out[0, 0, 0].tolist() == [255] * 3
    assert out[0, 0, 1].tolist() == [0] * 3
    assert out[0, 0, 2].tolist() == [255] * 3
    assert out[0, 0, 3].tolist() == [0] * 3
    assert out[0, 0, 4].tolist() == [128] * 3  # round(127.5) banker's-rounds to 128


def test_rejects_wrong_shape():
    with pytest.raises(ValueError):
        pixels_3thw_to_uint8_frames(torch.zeros(1, 3, 4, 4, 4))
    with pytest.raises(ValueError):
        pixels_3thw_to_uint8_frames(torch.zeros(4, 5, 6, 7))
