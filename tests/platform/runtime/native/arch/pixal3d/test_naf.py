from __future__ import annotations

import math

import pytest
import torch
import torch.nn.functional as F
from safetensors.torch import save_file

from src.platform.runtime.native.arch.pixal3d.naf import (
    NAF,
    NAF_PREFIX,
    has_naf_weights,
    load_naf,
    neighborhood_attention_2d,
)

KERNEL = 9


def _window_start(index: int, length: int, kernel: int) -> int:
    return min(max(index - kernel // 2, 0), length - kernel)


def _natten_reference(q, k_lr, v_lr, kernel, dilation, scale):
    height, width, heads, _ = q.shape
    k = k_lr.repeat_interleave(dilation, 0).repeat_interleave(dilation, 1)
    v = v_lr.repeat_interleave(dilation, 0).repeat_interleave(dilation, 1)
    out = torch.empty(height, width, heads, v.shape[-1], dtype=q.dtype)
    for i in range(height):
        group_i, slot_i, length_i = i % dilation, i // dilation, len(range(i % dilation, height, dilation))
        rows = [group_i + dilation * (_window_start(slot_i, length_i, kernel) + t) for t in range(kernel)]
        for j in range(width):
            group_j, slot_j, length_j = j % dilation, j // dilation, len(range(j % dilation, width, dilation))
            cols = [group_j + dilation * (_window_start(slot_j, length_j, kernel) + t) for t in range(kernel)]
            keys = k[rows][:, cols].reshape(kernel * kernel, heads, -1)
            values = v[rows][:, cols].reshape(kernel * kernel, heads, -1)
            scores = torch.einsum("nd,knd->nk", q[i, j], keys) * scale
            out[i, j] = torch.einsum("nk,knd->nd", scores.softmax(-1), values)
    return out


def _zero_padded(q, k_lr, v_lr, kernel, dilation, scale):
    height, width, heads, qk_dim = q.shape
    k = k_lr.repeat_interleave(dilation, 0).repeat_interleave(dilation, 1).permute(2, 3, 0, 1).reshape(1, -1, height, width)
    v = v_lr.repeat_interleave(dilation, 0).repeat_interleave(dilation, 1).permute(2, 3, 0, 1).reshape(1, -1, height, width)
    pad = (kernel // 2) * dilation
    kw = F.unfold(k, kernel, dilation=dilation, padding=pad).view(heads, qk_dim, kernel * kernel, height * width)
    vw = F.unfold(v, kernel, dilation=dilation, padding=pad).view(heads, -1, kernel * kernel, height * width)
    qq = q.reshape(height * width, heads, qk_dim).permute(1, 0, 2)
    scores = torch.einsum("npd,ndkp->npk", qq, kw) * scale
    out = torch.einsum("npk,ndkp->npd", scores.softmax(-1), vw)
    return out.permute(1, 0, 2).reshape(height, width, heads, -1)


def _inputs(cells, dilation, heads=4, qk_dim=8, value_dim=16, seed=0):
    generator = torch.Generator().manual_seed(seed)
    size = cells * dilation
    q = torch.randn(size, size, heads, qk_dim, generator=generator, dtype=torch.float64)
    k = torch.randn(cells, cells, heads, qk_dim, generator=generator, dtype=torch.float64)
    v = torch.randn(cells, cells, heads, value_dim, generator=generator, dtype=torch.float64)
    return q, k, v


@pytest.mark.parametrize("cells,dilation", [(12, 2), (10, 3), (9, 1)])
def test_neighbourhood_attention_matches_the_natten_window_rule_exactly(cells, dilation):
    q, k, v = _inputs(cells, dilation)
    scale = 8 ** -0.5
    expected = _natten_reference(q, k, v, KERNEL, dilation, scale)
    actual = neighborhood_attention_2d(q, k, v, kernel=KERNEL, scale=scale)
    assert actual.dtype == torch.float64
    assert (actual - expected).abs().max() < 1e-12


def test_the_window_is_shifted_at_the_border_rather_than_zero_padded():
    q, k, v = _inputs(12, 2)
    scale = 8 ** -0.5
    expected = _natten_reference(q, k, v, KERNEL, 2, scale)
    padded = _zero_padded(q, k, v, KERNEL, 2, scale)
    border = torch.zeros(24, 24, dtype=torch.bool)
    band = (KERNEL // 2) * 2
    border[:band] = border[-band:] = True
    border[:, :band] = border[:, -band:] = True
    assert (padded - expected)[~border].abs().max() < 1e-12
    assert (padded - expected)[border].abs().max() > 1e-3
    assert (neighborhood_attention_2d(q, k, v, kernel=KERNEL, scale=scale) - expected)[border].abs().max() < 1e-12


def test_chunking_over_cell_rows_does_not_change_the_result():
    q, k, v = _inputs(11, 2, seed=3)
    whole = neighborhood_attention_2d(q, k, v, kernel=KERNEL)
    chunked = neighborhood_attention_2d(q, k, v, kernel=KERNEL, chunk_bytes=1)
    assert torch.equal(whole, chunked)


def test_the_default_scale_is_the_inverse_root_of_the_query_dim():
    q, k, v = _inputs(9, 2)
    assert torch.allclose(
        neighborhood_attention_2d(q, k, v, kernel=KERNEL),
        neighborhood_attention_2d(q, k, v, kernel=KERNEL, scale=8 ** -0.5),
    )


def test_a_query_grid_that_is_not_a_multiple_of_the_key_grid_is_refused():
    q = torch.randn(19, 19, 4, 8)
    k = torch.randn(9, 9, 4, 8)
    v = torch.randn(9, 9, 4, 16)
    with pytest.raises(ValueError, match="integer multiple"):
        neighborhood_attention_2d(q, k, v)


def test_a_key_grid_smaller_than_the_kernel_is_refused():
    q = torch.randn(16, 16, 4, 8)
    k = torch.randn(8, 8, 4, 8)
    v = torch.randn(8, 8, 4, 16)
    with pytest.raises(ValueError, match="at least 9 cells"):
        neighborhood_attention_2d(q, k, v)


_EXPECTED_KEYS = {
    "image_encoder.rope.periods": (16,),
    "image_encoder.encoder.0.weight": (128, 3, 1, 1),
    "image_encoder.encoder.0.bias": (128,),
    "image_encoder.sem_encoder.0.weight": (128, 3, 3, 3),
    "image_encoder.sem_encoder.0.bias": (128,),
}
for _stack, _kernel in (("encoder", 1), ("sem_encoder", 3)):
    for _block in (1, 2):
        for _norm in ("norm1", "norm2"):
            _EXPECTED_KEYS[f"image_encoder.{_stack}.{_block}.{_norm}.weight"] = (128,)
            _EXPECTED_KEYS[f"image_encoder.{_stack}.{_block}.{_norm}.bias"] = (128,)
        for _conv in ("conv1", "conv2"):
            _EXPECTED_KEYS[f"image_encoder.{_stack}.{_block}.{_conv}.weight"] = (128, 128, _kernel, _kernel)
            _EXPECTED_KEYS[f"image_encoder.{_stack}.{_block}.{_conv}.bias"] = (128,)


def test_the_module_has_exactly_the_37_tensors_of_the_comfy_naf_slice():
    state = {name: tuple(t.shape) for name, t in NAF().state_dict().items()}
    assert len(_EXPECTED_KEYS) == 37
    assert state == _EXPECTED_KEYS


def test_rope_periods_follow_base_100_over_64_dim_heads():
    periods = NAF().image_encoder.rope.periods
    assert torch.allclose(periods, 100.0 ** (2 * torch.arange(16, dtype=torch.float32) / 32))


def test_rope_rotates_each_half_pair_by_its_axial_angle():
    rope = NAF().image_encoder.rope
    x = torch.randn(1, 256, 2, 3)
    out = rope(x)
    head, row, col, index = 1, 1, 2, 5
    y_coord = (row + 0.5) / 2 * 2 - 1
    x_coord = (col + 0.5) / 3 * 2 - 1
    coord = y_coord if index < 16 else x_coord
    angle = 2 * math.pi * coord / float(rope.periods[index % 16])
    first = x[0, head * 64 + index, row, col]
    second = x[0, head * 64 + index + 32, row, col]
    assert torch.isclose(out[0, head * 64 + index, row, col], first * math.cos(angle) - second * math.sin(angle), atol=1e-5)
    assert torch.isclose(out[0, head * 64 + index + 32, row, col], second * math.cos(angle) + first * math.sin(angle), atol=1e-5)


def _randomised(module, seed=0):
    generator = torch.Generator().manual_seed(seed)
    with torch.no_grad():
        for parameter in module.parameters():
            parameter.copy_(torch.randn(parameter.shape, generator=generator) * 0.2)
    return module


def test_the_upsampler_matches_upstream_nearest_exact_dilated_attention():
    naf = _randomised(NAF().double())
    cells, dilation = 9, 2
    size = cells * dilation
    generator = torch.Generator().manual_seed(1)
    image = torch.rand(1, 3, size, size, generator=generator, dtype=torch.float64)
    features = torch.randn(1, 16, cells, cells, generator=generator, dtype=torch.float64)
    out = naf(image, features, (size, size))

    queries = naf.image_encoder(image, (size, size))
    keys = F.adaptive_avg_pool2d(queries, (cells, cells))
    q = queries[0].reshape(4, 64, size, size).permute(2, 3, 0, 1)
    k_hr = F.interpolate(keys, size=(size, size), mode="nearest-exact")[0].reshape(4, 64, size, size).permute(2, 3, 0, 1)
    v_hr = F.interpolate(features, size=(size, size), mode="nearest-exact")[0].reshape(4, 4, size, size).permute(2, 3, 0, 1)
    k_lr = k_hr[::dilation, ::dilation]
    v_lr = v_hr[::dilation, ::dilation]
    assert torch.equal(k_hr, k_lr.repeat_interleave(dilation, 0).repeat_interleave(dilation, 1))
    expected = _natten_reference(q, k_lr, v_lr, KERNEL, dilation, 64 ** -0.5).reshape(size, size, 16)

    assert out.shape == (1, size, size, 16)
    assert (out[0] - expected).abs().max() < 1e-10


def test_the_output_is_channels_last_in_head_major_channel_order():
    naf = _randomised(NAF())
    features = torch.zeros(1, 8, 9, 9)
    features[:, 5] = 3.0
    out = naf(torch.rand(1, 3, 18, 18), features, (18, 18))
    assert torch.allclose(out[..., 5], torch.full((1, 18, 18), 3.0))
    assert torch.allclose(out[..., [0, 1, 2, 3, 4, 6, 7]], torch.zeros(1, 18, 18, 7))


def test_a_large_guide_is_downscaled_to_four_times_the_target_first():
    naf = _randomised(NAF())
    calls = []
    original = F.interpolate

    def spy(x, *args, **kwargs):
        calls.append(kwargs.get("size"))
        return original(x, *args, **kwargs)

    torch.nn.functional.interpolate = spy
    try:
        naf.image_encoder(torch.rand(1, 3, 80, 80), (9, 9))
    finally:
        torch.nn.functional.interpolate = original
    assert calls == [(36, 36)]


def _write(path, tensors):
    save_file({key: value.contiguous() for key, value in tensors.items()}, str(path))
    return path


def test_the_upsampler_loads_from_the_naf_prefix_of_the_dino_file(tmp_path):
    reference = _randomised(NAF(), seed=4)
    tensors = {NAF_PREFIX + key: value for key, value in reference.state_dict().items()}
    tensors["embeddings.patch_embeddings.weight"] = torch.zeros(2, 3, 16, 16)
    tensors["layer.0.norm1.weight"] = torch.zeros(2)
    path = _write(tmp_path / "dino_v3_L_naf_fp32.safetensors", tensors)

    assert has_naf_weights(path)
    loaded = load_naf(path)
    for key, value in reference.state_dict().items():
        assert torch.equal(loaded.state_dict()[key], value)
    assert not any(parameter.requires_grad for parameter in loaded.parameters())


def test_a_dino_file_without_naf_weights_is_named(tmp_path):
    path = _write(tmp_path / "dino_v3_vit_l.safetensors", {"embeddings.patch_embeddings.weight": torch.zeros(2, 3, 16, 16)})
    assert not has_naf_weights(path)
    with pytest.raises(ValueError, match="dino_v3_L_naf_fp32"):
        load_naf(path)


def test_a_partial_naf_slice_is_refused(tmp_path):
    tensors = {NAF_PREFIX + key: value for key, value in NAF().state_dict().items() if "sem_encoder" not in key}
    path = _write(tmp_path / "partial.safetensors", tensors)
    with pytest.raises(ValueError, match="left unfilled"):
        load_naf(path)
