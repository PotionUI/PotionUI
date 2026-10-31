from __future__ import annotations

import pytest
import torch

from src.platform.runtime.native.sampling.denoise_loop import denoise
from src.platform.runtime.native.sampling.latent_mask import (
    LatentMaskGuidance,
    composite_latents,
    restore_kept_latents,
    validate_latent_mask,
)

SETTINGS = {"shift": 2.02, "guidance": None}


def _to_target(target):
    def model_forward(x, sigma, cond):
        s = sigma.reshape(-1)[0].float()
        return (x.float() - target.float()) / s

    return model_forward


def _run(latents, mask, target, sampler="euler", strength=1.0, noise=None):
    return denoise(
        _to_target(target),
        latents,
        cond={},
        uncond=None,
        steps=6,
        sampler_name=sampler,
        sampling_settings=SETTINGS,
        guidance_scale=0.0,
        seed_noise=noise if noise is not None else torch.randn_like(latents),
        denoise_strength=strength,
        latent_mask=mask,
    )


def _half_mask(shape):
    mask = torch.zeros(*([1] * (len(shape) - 2)), shape[-2], shape[-1])
    mask[..., :, shape[-1] // 2:] = 1.0
    return mask


@pytest.mark.parametrize("shape", [(1, 4, 4, 8), (1, 4, 1, 4, 8)])
@pytest.mark.parametrize("sampler", ["euler", "dpmpp_2m"])
def test_kept_latents_are_bit_identical_and_repainted_reach_target(shape, sampler):
    torch.manual_seed(0)
    source = torch.randn(*shape).to(torch.bfloat16)
    target = torch.full(shape, 3.0)
    mask = _half_mask(shape)
    out = _run(source, mask, target, sampler=sampler)
    keep = (mask == 0).expand(shape)
    assert out.dtype == source.dtype
    assert torch.equal(out[keep], source[keep])
    repaint = ~keep
    assert torch.allclose(out[repaint].float(), torch.full_like(out[repaint].float(), 3.0), atol=0.05)


def test_partial_strength_keeps_source_outside_mask():
    source = torch.randn(1, 4, 4, 8)
    out = _run(source, _half_mask(source.shape), torch.zeros_like(source), strength=0.5)
    assert torch.equal(out[..., :4], source[..., :4])


def test_full_mask_matches_unmasked_run():
    source = torch.randn(1, 4, 4, 4)
    noise = torch.randn_like(source)
    target = torch.full_like(source, -1.0)
    masked = _run(source, torch.ones(1, 1, 4, 4), target, noise=noise)
    plain = denoise(
        _to_target(target), source, cond={}, uncond=None, steps=6, sampling_settings=SETTINGS,
        guidance_scale=0.0, seed_noise=noise,
    )
    assert torch.allclose(masked, plain, atol=1e-5)


def test_empty_mask_returns_source():
    source = torch.randn(1, 4, 4, 4)
    out = _run(source, torch.zeros(1, 1, 4, 4), torch.full_like(source, 9.0))
    assert torch.equal(out, source)


def test_soft_mask_blends_between_source_and_target():
    source = torch.zeros(1, 4, 4, 4)
    out = _run(source, torch.full((1, 1, 4, 4), 0.5), torch.full_like(source, 2.0))
    assert torch.allclose(out, torch.full_like(out, 1.0), atol=0.05)


def test_mask_that_does_not_broadcast_is_rejected():
    with pytest.raises(ValueError, match="broadcast"):
        validate_latent_mask(torch.ones(1, 1, 3, 3), torch.zeros(1, 4, 4, 4))
    with pytest.raises(ValueError, match="dims"):
        validate_latent_mask(torch.ones(4, 4), torch.zeros(1, 4, 4, 4))


def test_mask_is_clamped_to_unit_range():
    out = validate_latent_mask(torch.tensor([[[[-2.0, 3.0]]]]), torch.zeros(1, 1, 1, 2))
    assert out.tolist() == [[[[0.0, 1.0]]]]


def test_guidance_wrapper_delegates_attributes_to_inner():
    class Inner:
        last_uncond_v = "marker"

        def __call__(self, model_fn, x, sigma, cond, uncond, step_index):
            return torch.zeros_like(x)

    guidance = LatentMaskGuidance(Inner(), torch.zeros(1, 1, 2, 2), torch.ones(1, 1, 2, 2))
    assert guidance.last_uncond_v == "marker"
    with pytest.raises(AttributeError):
        guidance.missing_attribute


def test_composite_keeps_source_where_mask_is_zero():
    out = composite_latents(torch.tensor([0.0, 1.0]), torch.tensor([5.0, 5.0]), torch.tensor([2.0, 2.0]))
    assert out.tolist() == [2.0, 5.0]


def test_resume_is_rejected_with_a_mask():
    source = torch.zeros(1, 4, 4, 4)
    with pytest.raises(ValueError, match="resume"):
        denoise(
            _to_target(source), source, cond={}, uncond=None, steps=4, sampling_settings=SETTINGS,
            guidance_scale=0.0, resume=(1, source.clone()), latent_mask=torch.ones(1, 1, 4, 4),
        )


def test_restore_kept_latents_only_overrides_zero_mask():
    out = restore_kept_latents(torch.tensor([0.0, 0.3]), torch.tensor([5.0, 5.0]), torch.tensor([2.0, 2.0]))
    assert out.tolist() == [2.0, 5.0]


def test_kept_region_follows_the_noised_source_at_every_model_call():
    torch.manual_seed(1)
    source = torch.randn(1, 4, 4, 8)
    noise = torch.randn_like(source)
    mask = _half_mask(source.shape)
    seen = []

    def model_forward(x, sigma, cond):
        seen.append((x.clone(), float(sigma.reshape(-1)[0])))
        return torch.randn_like(x)

    denoise(
        model_forward, source, cond={}, uncond=None, steps=6, sampling_settings=SETTINGS,
        guidance_scale=0.0, seed_noise=noise, latent_mask=mask,
    )
    assert len(seen) == 6 and seen[0][1] == pytest.approx(1.0)
    keep = (mask == 0).expand(source.shape)
    for x, sigma in seen:
        expected = sigma * noise + (1.0 - sigma) * source
        assert torch.allclose(x[keep], expected[keep], atol=1e-4)
    assert not torch.allclose(seen[-1][0][~keep], (seen[-1][1] * noise + (1 - seen[-1][1]) * source)[~keep], atol=1e-3)
