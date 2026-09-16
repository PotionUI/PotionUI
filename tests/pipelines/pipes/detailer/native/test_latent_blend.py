import numpy as np
import pytest
import torch
from PIL import Image

from src.pipelines.pipes.detailer.native.latent_blend import LatentInpaintFilter, latent_mask_tensor
from src.platform.runtime.native.sampling.algorithms.euler import sample_euler
from src.platform.runtime.native.sampling.cfg import NoCFG


def _filter(mask_values, shape=(1, 4, 2, 2)):
    original = torch.full(shape, 3.0)
    noise = torch.full(shape, -1.0)
    mask = torch.tensor(mask_values, dtype=torch.float32).reshape(1, 1, *shape[-2:])
    return LatentInpaintFilter(original, noise, mask), original, noise


def _expected_original_at(sigma, original, noise):
    return (1.0 - sigma) * original + sigma * noise


def test_outside_mask_is_exactly_the_renoised_original_at_every_sigma():
    filt, original, noise = _filter([[1.0, 0.0], [0.0, 1.0]])
    working = torch.randn(1, 4, 2, 2)

    for sigma in (0.9, 0.6, 0.3, 0.0):
        out = filt.filter_latent(0, 4, working, sigma)
        expected = _expected_original_at(sigma, original, noise)
        assert torch.equal(out[..., 0, 1], expected[..., 0, 1])
        assert torch.equal(out[..., 1, 0], expected[..., 1, 0])


def test_inside_mask_is_exactly_the_working_latent():
    filt, _, _ = _filter([[1.0, 0.0], [0.0, 1.0]])
    working = torch.randn(1, 4, 2, 2)

    out = filt.filter_latent(0, 4, working, 0.5)
    assert torch.equal(out[..., 0, 0], working[..., 0, 0])
    assert torch.equal(out[..., 1, 1], working[..., 1, 1])


def test_half_mask_is_the_midpoint():
    filt, original, noise = _filter([[0.5, 0.5], [0.5, 0.5]])
    working = torch.randn(1, 4, 2, 2)

    sigma = 0.4
    out = filt.filter_latent(0, 4, working, sigma)
    midpoint = 0.5 * working + 0.5 * _expected_original_at(sigma, original, noise)
    assert torch.allclose(out, midpoint, atol=0, rtol=0)


def test_euler_loop_pins_the_outside_at_every_descending_sigma():
    shape = (1, 4, 2, 2)
    filt, original, noise = _filter([[1.0, 0.0], [0.0, 0.0]], shape)
    sigmas = torch.tensor([1.0, 0.75, 0.5, 0.25, 0.0])
    seen = []

    class _Watch:
        priority = -10

        def on_start(self, total_steps):
            pass

        def on_step(self, step_index, total_steps, x, sigma, denoised_x0):
            seen.append((step_index, x.clone()))

        def on_end(self):
            pass

    def model_fn(x, sigma, conditioning):
        return torch.full_like(x, 0.5)

    x0 = 1.0 * noise + 0.0 * original
    sample_euler(model_fn, x0.clone(), sigmas, NoCFG(), {}, None, hooks=(filt, _Watch()))

    assert [i for i, _ in seen] == [0, 1, 2, 3]
    for step_index, x in seen:
        sigma_next = float(sigmas[step_index + 1])
        expected = _expected_original_at(sigma_next, original, noise)
        assert torch.equal(x[..., 0, 1], expected[..., 0, 1])
        assert torch.equal(x[..., 1, 0], expected[..., 1, 0])
        assert torch.equal(x[..., 1, 1], expected[..., 1, 1])
        assert not torch.equal(x[..., 0, 0], expected[..., 0, 0])


def test_euler_loop_is_unchanged_without_a_filter():
    sigmas = torch.tensor([1.0, 0.5, 0.0])
    start = torch.randn(1, 4, 2, 2)

    def model_fn(x, sigma, conditioning):
        return x * 0.25

    with_hook = sample_euler(model_fn, start.clone(), sigmas, NoCFG(), {}, None, hooks=())
    reference = start.clone()
    for i in range(2):
        v = model_fn(reference, sigmas[i], {})
        reference = reference + (sigmas[i + 1] - sigmas[i]) * v
    assert torch.equal(with_hook, reference)


def test_latent_mask_tensor_matches_latent_rank_and_size():
    mask = Image.new("L", (64, 64), 255)
    for shape in ((1, 16, 8, 8), (1, 16, 1, 8, 8)):
        tensor = latent_mask_tensor(mask, shape)
        assert tensor.ndim == len(shape)
        assert tensor.shape[-2:] == (8, 8)
        assert torch.allclose(tensor, torch.ones_like(tensor))


def test_latent_mask_tensor_preserves_the_feather_gradient():
    arr = np.tile(np.linspace(0, 255, 64, dtype=np.uint8), (64, 1))
    tensor = latent_mask_tensor(Image.fromarray(arr, mode="L"), (1, 16, 8, 8))
    row = tensor[0, 0, 0]
    assert torch.all(row[1:] > row[:-1])
    assert float(row[0]) < 0.1 and float(row[-1]) > 0.9
