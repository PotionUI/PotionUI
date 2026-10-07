from __future__ import annotations

import math

import pytest
import torch
import torch.nn.functional as F

from src.platform.runtime.native.arch.moge import MOGE2_VITL, FovEstimate, MoGe2Model, normalized_view_plane_uv

from .test_load import TINY, _tiny_model


def test_the_resolution_level_spans_upstreams_token_range():
    assert MOGE2_VITL.num_tokens(0) == 1200
    assert MOGE2_VITL.num_tokens(9) == 3600
    assert MOGE2_VITL.num_tokens(5) == 2533
    with pytest.raises(ValueError, match="between 0 and 9"):
        MOGE2_VITL.num_tokens(10)


def test_forward_predicts_a_point_map_and_mask_at_the_input_size():
    model = _tiny_model()
    with torch.no_grad():
        points, mask = model(torch.rand(2, 3, 40, 56), 12)
    assert points.shape == (2, 40, 56, 3)
    assert mask.shape == (2, 40, 56)
    assert bool((points[..., 2] > 0).all())
    assert bool(((mask > 0) & (mask < 1)).all())


@pytest.mark.parametrize("size,tokens,grid", [((1024, 1024), 3600, (60, 60)), ((768, 1024), 3600, (52, 69))])
def test_the_token_grid_follows_the_aspect_ratio(monkeypatch, size, tokens, grid):
    model = _tiny_model()
    seen = []
    encoder_forward = model.encoder.forward

    def _record(image, rows, cols):
        seen.append((rows, cols, tuple(image.shape[-2:])))
        return encoder_forward(torch.rand(1, 3, 28, 28), 2, 2).mean((2, 3), keepdim=True).expand(-1, -1, rows, cols)

    monkeypatch.setattr(model.encoder, "forward", _record)
    with torch.no_grad():
        model(torch.rand(1, 3, *size), tokens)
    assert seen == [(*grid, size)]


def test_the_position_embedding_is_used_as_is_on_the_pretraining_grid():
    backbone = _tiny_model().encoder.backbone
    side = TINY.pos_grid * TINY.patch_size
    assert torch.equal(backbone._position_embedding(TINY.pos_grid ** 2, side, side, torch.float32), backbone.pos_embed)


def test_the_position_embedding_is_resampled_bicubically_with_dinov2s_offset_rows_first():
    backbone = _tiny_model().encoder.backbone
    rows, cols = 3, 5
    grid = TINY.pos_grid
    patch = backbone.pos_embed[:, 1:].reshape(1, grid, grid, -1).permute(0, 3, 1, 2)
    expected = F.interpolate(patch, scale_factor=((rows + 0.1) / grid, (cols + 0.1) / grid), mode="bicubic")
    sized = F.interpolate(patch, size=(rows, cols), mode="bicubic")
    assert not torch.allclose(expected, sized)
    resampled = backbone._position_embedding(rows * cols, rows * 14, cols * 14, torch.float32)
    assert resampled.shape == (1, 1 + rows * cols, TINY.embed_dim)
    assert torch.equal(resampled[:, :1], backbone.pos_embed[:, :1])
    assert torch.allclose(resampled[:, 1:], expected.permute(0, 2, 3, 1).flatten(1, 2), atol=1e-6)


class _PinholeMoGe(MoGe2Model):
    def __init__(self, fov_x_deg: float, mask_value: float = 0.9) -> None:
        super().__init__(TINY)
        self.fov_x_deg = fov_x_deg
        self.mask_value = mask_value
        self.calls = []

    def forward(self, image, num_tokens):
        self.calls.append((tuple(image.shape), num_tokens))
        batch, _, height, width = image.shape
        aspect = width / height
        focal = 0.5 / math.tan(math.radians(self.fov_x_deg) / 2) * 2 * aspect / (1 + aspect ** 2) ** 0.5
        uv = normalized_view_plane_uv(width, height)
        depth = 3.0 + 0.5 * uv[..., 0]
        points = torch.cat([uv * depth[..., None] / focal, (depth - 1.0)[..., None]], dim=-1)
        return points.expand(batch, -1, -1, -1), torch.full((batch, height, width), self.mask_value)


def test_estimate_fov_reads_the_horizontal_fov_off_the_predicted_point_map():
    model = _PinholeMoGe(38.5)
    [estimate] = model.estimate_fov(torch.rand(3, 96, 160))
    assert isinstance(estimate, FovEstimate)
    assert estimate.fov_x_deg == pytest.approx(38.5, abs=1e-3)
    aspect = 160 / 96
    expected_y = math.degrees(2 * math.atan(math.tan(math.radians(38.5) / 2) / aspect))
    assert estimate.fov_y_deg == pytest.approx(expected_y, abs=1e-3)
    assert model.calls == [((1, 3, 96, 160), 3600)]


def test_estimate_fov_runs_at_the_requested_resolution_level():
    model = _PinholeMoGe(30.0)
    model.estimate_fov(torch.rand(1, 3, 64, 64), resolution_level=0)
    assert model.calls[0][1] == 1200


def test_a_mask_below_the_threshold_everywhere_estimates_nothing():
    assert _PinholeMoGe(30.0, mask_value=0.4).estimate_fov(torch.rand(1, 3, 64, 64)) == [None]
