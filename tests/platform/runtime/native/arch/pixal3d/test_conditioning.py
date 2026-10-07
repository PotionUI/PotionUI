from __future__ import annotations

import math

import pytest
import torch
from PIL import Image

from src.platform.runtime.native.arch.pixal3d.conditioning import (
    ConditioningView,
    EncodedView,
    encode_views,
    project_features,
    stage_condition,
    view_pixels,
)
from src.platform.runtime.native.arch.pixal3d.config import StageConditioning
from src.platform.runtime.native.arch.pixal3d.projection import (
    dense_grid_coords,
    distance_from_fov,
    front_camera,
    orbit_camera,
    projection_grid,
    relative_cameras,
    sample_bilinear,
)
from src.platform.runtime.native.sparse3d import SparseTensor

FOV = math.radians(20.0)
SIZE = 64
LR_STAGE = StageConditioning(SIZE, None)
NAF_STAGE = StageConditioning(SIZE, 32)


def _view(camera, patches, tokens):
    return EncodedView(
        camera=camera,
        pixels={SIZE: torch.rand(3, SIZE, SIZE)},
        tokens={SIZE: tokens},
        patches={SIZE: patches},
    )


def _rig(names=(0.0, 90.0, 180.0)):
    distance = 1.1 * distance_from_fov(FOV)
    cameras = torch.stack([orbit_camera(a, 0.0, distance) for a in names])
    return relative_cameras(cameras, distance)


def _coords(resolution=8, count=30, seed=0):
    generator = torch.Generator().manual_seed(seed)
    xyz = torch.randint(0, resolution, (count, 3), generator=generator)
    return torch.cat([torch.zeros(count, 1, dtype=torch.long), xyz], dim=1).unique(dim=0)


def test_views_are_averaged_after_each_is_projected_through_its_own_camera():
    generator = torch.Generator().manual_seed(1)
    cameras = _rig()
    patches = [torch.randn(4, 4, 6, generator=generator) for _ in cameras]
    views = [_view(camera, patch, torch.randn(5, 6, generator=generator)) for camera, patch in zip(cameras, patches)]
    coords = _coords()

    cond = stage_condition(views, LR_STAGE, FOV, coords=coords, resolution=8)

    expected = sum(
        sample_bilinear(patch, projection_grid(coords[:, 1:], 8, camera, FOV, SIZE))
        for camera, patch in zip(cameras, patches)
    ) / 3
    assert isinstance(cond.proj, SparseTensor)
    assert cond.proj.coords is coords
    assert torch.allclose(cond.proj.feats, expected, atol=1e-6)
    assert torch.allclose(cond.tokens[0], sum(view.tokens[SIZE] for view in views) / 3, atol=1e-6)


def test_the_views_really_sample_different_pixels():
    cameras = _rig()
    coords = _coords()
    grids = [projection_grid(coords[:, 1:], 8, camera, FOV, SIZE) for camera in cameras]
    assert not torch.allclose(grids[0], grids[1])
    assert not torch.allclose(grids[0], grids[2])


def test_one_front_view_is_exactly_the_single_view_condition():
    generator = torch.Generator().manual_seed(2)
    patches = torch.randn(4, 4, 6, generator=generator)
    tokens = torch.randn(5, 6, generator=generator)
    coords = _coords()
    rig = stage_condition([_view(_rig((0.0,))[0], patches, tokens)], LR_STAGE, FOV, coords=coords, resolution=8)
    distance = 1.1 * distance_from_fov(FOV)
    single = stage_condition([_view(front_camera(distance), patches, tokens)], LR_STAGE, FOV, coords=coords, resolution=8)
    assert torch.allclose(rig.proj.feats, single.proj.feats, atol=1e-6)


def test_the_dense_stage_projects_the_whole_grid_in_latent_order():
    patches = torch.randn(4, 4, 6)
    view = _view(front_camera(distance_from_fov(FOV)), patches, torch.randn(5, 6))
    cond = stage_condition([view], LR_STAGE, FOV, resolution=4)
    expected = sample_bilinear(patches, projection_grid(dense_grid_coords(4), 4, view.camera, FOV, SIZE))
    assert cond.proj.shape == (1, 64, 6)
    assert torch.allclose(cond.proj[0], expected)


def test_naf_stages_concatenate_the_patch_and_upsampled_samples():
    class _NAF:
        def __call__(self, image, features, size, out_dtype=None):
            return torch.full((1, *size, features.shape[1]), 2.0)

    patches = torch.zeros(4, 4, 6)
    view = _view(front_camera(distance_from_fov(FOV)), patches, torch.zeros(5, 6))
    feats = project_features([view], _coords()[:, 1:], 8, NAF_STAGE, FOV, naf=_NAF())
    assert feats.shape[1] == 12
    assert torch.count_nonzero(feats[:, :6]) == 0
    assert torch.allclose(feats[:, 6:], torch.full_like(feats[:, 6:], 2.0))


def test_a_naf_stage_without_the_upsampler_is_refused():
    view = _view(front_camera(1.0), torch.zeros(4, 4, 6), torch.zeros(5, 6))
    with pytest.raises(ValueError, match="NAF"):
        project_features([view], _coords()[:, 1:], 8, NAF_STAGE, FOV)


def test_a_batched_coordinate_set_is_refused():
    view = _view(front_camera(1.0), torch.zeros(4, 4, 6), torch.zeros(5, 6))
    coords = _coords()
    coords[-1, 0] = 1
    with pytest.raises(ValueError, match="one object"):
        stage_condition([view], LR_STAGE, FOV, coords=coords, resolution=8)


def test_features_are_cast_to_the_token_dtype():
    view = _view(front_camera(1.0), torch.randn(4, 4, 6).to(torch.bfloat16), torch.randn(5, 6).to(torch.bfloat16))
    cond = stage_condition([view], LR_STAGE, FOV, coords=_coords(), resolution=8)
    assert cond.proj.feats.dtype == torch.bfloat16
    assert cond.dtype == torch.bfloat16


def test_encoding_splits_cls_and_registers_from_the_patch_grid():
    class _Conditioner:
        def __init__(self):
            self.inputs = []

        def encode(self, pixels, size):
            self.inputs.append(pixels)
            count = (size // 16) ** 2
            rows = torch.arange(5 + count, dtype=torch.float32)[None, :, None].expand(pixels.shape[0], -1, 3)
            return rows + torch.arange(pixels.shape[0])[:, None, None] * 1000

    conditioner = _Conditioner()
    image = Image.new("RGB", (40, 30), (255, 0, 0))
    views = [ConditioningView(image, front_camera(1.0)), ConditioningView(image, front_camera(2.0))]
    encoded = encode_views(conditioner, views, (32, 64), "cpu")

    assert [tuple(x.shape) for x in conditioner.inputs] == [(2, 3, 32, 32), (2, 3, 64, 64)]
    assert torch.allclose(conditioner.inputs[0][0, 0], torch.full((32, 32), (1.0 - 0.485) / 0.229), atol=1e-4)
    second = encoded[1]
    assert torch.equal(second.tokens[64][:, 0], torch.arange(5.0) + 1000)
    assert second.patches[64].shape == (4, 4, 3)
    assert second.patches[64][0, 1, 0] == 1000 + 6
    assert torch.allclose(second.pixels[64][0], torch.ones(64, 64))
    assert torch.equal(second.camera, front_camera(2.0))


def test_view_pixels_are_unnormalised_rgb_in_unit_range():
    pixels = view_pixels(Image.new("RGBA", (10, 10), (255, 128, 0, 255)), 16)
    assert pixels.shape == (3, 16, 16)
    assert pixels.max() <= 1.0 and pixels.min() >= 0.0
    assert pixels[0, 0, 0] == pytest.approx(1.0)
