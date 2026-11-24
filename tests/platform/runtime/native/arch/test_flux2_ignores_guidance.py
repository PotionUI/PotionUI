from __future__ import annotations

import torch

from src.platform.runtime.native.arch.flux.model import Flux

from .test_flux_model import TINY_FLUX2, _build_ready


def _forward(model: Flux, guidance: float) -> torch.Tensor:
    generator = torch.Generator().manual_seed(7)
    x = torch.randn(1, 16, 16, 16, generator=generator)
    context = torch.randn(1, 7, 32, generator=generator)
    with torch.no_grad():
        return model(x, torch.tensor([0.5]), context, guidance=torch.tensor([guidance]))


def test_flux2_without_guidance_embed_ignores_guidance_value():
    model = _build_ready(TINY_FLUX2)
    assert isinstance(model.guidance_in, torch.nn.Identity)
    assert torch.equal(_forward(model, 1.0), _forward(model, 9.0))


def test_flux2_guidance_embed_makes_guidance_matter():
    model = _build_ready({**TINY_FLUX2, "guidance_embed": True})
    assert not torch.equal(_forward(model, 1.0), _forward(model, 9.0))
