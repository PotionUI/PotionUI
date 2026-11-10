from __future__ import annotations

import pytest
import torch

from src.platform.runtime.native.sampling.flow_schedule import (
    H3_IMAGE_PDD_SIGMAS,
    build_sigmas,
    h3_image_pdd_sigmas,
)

GRID = [1.0, 0.9675236940383911, 0.8875485062599182, 0.75, 0.5327739119529724, 0.23200887441635132, 0.0]


def test_six_steps_land_exactly_on_the_released_grid():
    assert list(H3_IMAGE_PDD_SIGMAS) == GRID
    sigmas = build_sigmas(6, schedule="h3_image_pdd")
    assert sigmas.dtype == torch.float32
    assert torch.equal(sigmas, torch.tensor(GRID, dtype=torch.float32))


def test_fewer_steps_skip_grid_points_and_more_steps_interpolate_between_them():
    assert h3_image_pdd_sigmas(3).tolist() == pytest.approx([1.0, GRID[2], GRID[4], 0.0])
    twelve = h3_image_pdd_sigmas(12).tolist()
    assert twelve[::2] == pytest.approx(GRID)
    assert twelve[1] == pytest.approx((GRID[0] + GRID[1]) / 2)
    assert twelve[-2] == pytest.approx(GRID[5] / 2)


def test_the_checkpoint_grid_option_replaces_the_released_one():
    custom = [1.0, 0.6, 0.2, 0.0]
    from_list = build_sigmas(3, schedule="h3_image_pdd", schedule_options={"grid": custom})
    from_text = build_sigmas(3, schedule="h3_image_pdd", schedule_options={"grid": "1.0, 0.6, 0.2, 0.0"})
    assert from_list.tolist() == pytest.approx(custom)
    assert torch.equal(from_list, from_text)


def test_a_grid_that_is_not_strictly_descending_is_refused():
    with pytest.raises(ValueError, match="descending"):
        h3_image_pdd_sigmas(6, [1.0, 0.5, 0.5, 0.0])
