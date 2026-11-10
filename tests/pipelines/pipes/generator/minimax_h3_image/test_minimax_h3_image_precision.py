from __future__ import annotations

import torch

from src.pipelines.pipes.generator.minimax_h3_image.engine import MiniMaxH3ImageNativeGenerator
from src.platform.runtime.native.engine import NativeGenerator
from src.platform.runtime.native.sampling.denoise_loop import denoise
from src.platform.runtime.native.sampling.flow_schedule import H3_IMAGE_PDD_SIGMAS


def _run(generator_class):
    seen = []

    def model_forward(x, sigma, cond):
        seen.append((sigma.dtype, float(sigma[0]), float(cond["flow_map_target"][0]), x.dtype))
        return torch.zeros_like(x)

    latent = denoise(
        model_forward,
        torch.zeros(1, 4, 1, 2, 2, dtype=torch.bfloat16),
        {"context": torch.zeros(1, 3, 8, dtype=torch.bfloat16)},
        steps=6,
        sampler_name="euler",
        sampling_settings={"guidance": "flow_map"},
        guidance_scale=1.0,
        seed_noise=torch.full((1, 4, 1, 2, 2), 0.3, dtype=torch.bfloat16),
        sigmas=torch.tensor(H3_IMAGE_PDD_SIGMAS, dtype=torch.float32),
        model_sigma_dtype=generator_class.model_sigma_dtype,
        output_dtype=generator_class.latent_output_dtype,
    )
    return seen, latent


def test_the_image_model_sees_each_grid_sigma_and_its_target_exactly_in_fp32():
    seen, _ = _run(MiniMaxH3ImageNativeGenerator)
    grid = torch.tensor(H3_IMAGE_PDD_SIGMAS, dtype=torch.float32).tolist()
    assert [entry[0] for entry in seen] == [torch.float32] * 6
    assert [entry[1] for entry in seen] == grid[:-1]
    assert [entry[2] for entry in seen] == grid[1:]
    assert {entry[3] for entry in seen} == {torch.bfloat16}


def test_the_image_latent_leaves_sampling_in_fp32():
    _, latent = _run(MiniMaxH3ImageNativeGenerator)
    assert latent.dtype is torch.float32


def test_other_families_still_get_the_trajectory_dtype_for_sigma_and_latent():
    seen, latent = _run(NativeGenerator)
    assert [entry[0] for entry in seen] == [torch.bfloat16] * 6
    assert seen[1][1] == float(torch.tensor(H3_IMAGE_PDD_SIGMAS[1]).to(torch.bfloat16))
    assert latent.dtype is torch.bfloat16
