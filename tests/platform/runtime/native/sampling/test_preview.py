"""Tests for the cheap latent -> RGB workbench previews.

Covers the vendored per-family factor tables + ``resolve_preview_factors``
keying, the ``latent_to_rgb`` math (finite uint8 HWC of the expected shape for
each family's latent rank), the ``max_size`` scaling, and the belt-and-suspenders
error guard in ``make_preview_hook`` (a preview that raises must never propagate).
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest
import torch

from src.platform.runtime.native.detect.registry import arch_registry
from src.platform.runtime.native.sampling.preview import (
    FLUX,
    FLUX2,
    LTXV,
    MINIMAX_H3,
    WAN21,
    WAN22,
    PreviewFactors,
    latent_to_preview_image,
    latent_to_rgb,
    make_preview_hook,
    resolve_preview_factors,
)


# (table, input latent shape, expected rgb H, expected rgb W)
_FAMILY_CASES = [
    ("flux", FLUX, (1, 16, 64, 48), 64, 48),           # 4D flux1 latent
    ("flux2", FLUX2, (1, 128, 20, 24), 40, 48),        # 128ch -> reshape -> 2x spatial
    ("wan21", WAN21, (1, 16, 1, 32, 40), 32, 40),      # 5D causal still (T=1)
    ("wan22", WAN22, (1, 48, 1, 30, 22), 30, 22),      # 48ch 5D
    ("ltxv", LTXV, (1, 128, 4, 18, 26), 18, 26),       # 5D video (T=4 -> frame 0)
    ("minimax_h3", MINIMAX_H3, (1, 24, 2, 20, 16), 20, 16),  # 24ch 5D video (T=2 -> frame 0)
]


@pytest.mark.parametrize("name,factors,shape,exp_h,exp_w", _FAMILY_CASES)
def test_latent_to_rgb_shape_and_dtype(name, factors, shape, exp_h, exp_w):
    torch.manual_seed(0)
    arr = latent_to_rgb(torch.randn(shape), factors)
    assert arr.dtype == np.uint8
    assert arr.shape == (exp_h, exp_w, 3)
    assert np.isfinite(arr).all()
    assert arr.min() >= 0 and arr.max() <= 255


@pytest.mark.parametrize("name,factors,shape,exp_h,exp_w", _FAMILY_CASES)
def test_factor_table_width_matches_channels(name, factors, shape, exp_h, exp_w):
    # Each row is one input latent channel; every row is a 3-vector (RGB).
    assert all(len(row) == 3 for row in factors.factors)
    assert factors.bias is None or len(factors.bias) == 3


def test_flux2_reshape_unshuffles_128_to_32():
    # Flux2 samples a 128ch latent; the reshape must fold 2x2 spatial back out so
    # the 32-row factor table applies (and the preview is 2x the latent HxW).
    t = torch.randn(1, 128, 10, 12)
    out = FLUX2.reshape(t)
    assert out.shape == (1, 32, 20, 24)


def test_max_size_scales_long_edge():
    img = latent_to_preview_image(torch.randn(1, 16, 40, 20), FLUX, max_size=256)
    assert max(img.size) == 256                    # long edge hits the cap
    assert img.mode == "RGB"


def test_resolve_all_registered_specs_have_factors():
    # Every native family the engine can load must resolve to a preview table.
    for spec in arch_registry.all():
        assert resolve_preview_factors(spec) is not None, f"{spec.family}/{spec.variant}"


def test_resolve_keys_by_format_and_channels():
    # wan21 (krea2 carries no "format", only per-channel latents_mean).
    krea2 = SimpleNamespace(latent_format={"latent_channels": 16, "latents_mean": [0.0] * 16})
    assert resolve_preview_factors(krea2) is WAN21
    # 16ch flux latent (flux1 / z_image): no format, no latents_mean -> FLUX.
    flux = SimpleNamespace(latent_format={"latent_channels": 16, "scale_factor": 0.3611})
    assert resolve_preview_factors(flux) is FLUX
    assert resolve_preview_factors(SimpleNamespace(latent_format={"latent_channels": 32})) is FLUX2
    assert resolve_preview_factors(SimpleNamespace(latent_format={"format": "wan22"})) is WAN22
    assert resolve_preview_factors(SimpleNamespace(latent_format={"format": "ltxv"})) is LTXV
    # 24ch minimax_h3 video latent: explicit format branch (not a channel-count
    # collision with any other family here — 16/32/48/128 are all taken).
    assert resolve_preview_factors(SimpleNamespace(latent_format={"latent_channels": 24})) is MINIMAX_H3
    assert resolve_preview_factors(SimpleNamespace(latent_format={"format": "minimax_h3"})) is MINIMAX_H3


def test_resolve_unknown_returns_none():
    assert resolve_preview_factors(SimpleNamespace(latent_format={"latent_channels": 99})) is None
    assert resolve_preview_factors(SimpleNamespace(latent_format={})) is None
    assert resolve_preview_factors(SimpleNamespace()) is None


def test_make_preview_hook_none_for_unknown_family():
    assert make_preview_hook(SimpleNamespace(latent_format={}), lambda img: None) is None


def test_make_preview_hook_emits_pil_image():
    spec = SimpleNamespace(latent_format={"latent_channels": 16, "format": "wan21"}, variant="krea2_turbo")
    emitted = []
    hook = make_preview_hook(spec, emitted.append, every_n=1)
    assert hook is not None
    hook.on_step(0, 3, torch.zeros(1, 16, 1, 8, 8), 1.0, torch.randn(1, 16, 1, 8, 8))
    assert len(emitted) == 1
    assert emitted[0].size == (512, 512)           # scaled to default max_size


def test_preview_error_never_propagates():
    # A decode that raises (here: a latent whose rank latent_to_rgb rejects) must be
    # swallowed by the hook's guard -- no emit, no exception reaching the sampler.
    spec = SimpleNamespace(latent_format={"latent_channels": 16, "format": "wan21"}, variant="krea2_turbo")
    emitted = []
    hook = make_preview_hook(spec, emitted.append, every_n=1)
    bad_latent = torch.randn(8, 8)                 # 2D -> latent_to_rgb raises
    hook.on_step(0, 1, bad_latent, 1.0, bad_latent)  # must not raise
    assert emitted == []


def test_preview_emit_error_swallowed():
    spec = SimpleNamespace(latent_format={"latent_channels": 16, "format": "wan21"}, variant="krea2_turbo")

    def boom(_img):
        raise RuntimeError("websocket down")

    hook = make_preview_hook(spec, boom, every_n=1)
    hook.on_step(0, 1, torch.zeros(1, 16, 1, 8, 8), 1.0, torch.randn(1, 16, 1, 8, 8))  # must not raise


def test_preview_factors_dataclass_frozen():
    pf = PreviewFactors(name="x", factors=[[0.0, 0.0, 0.0]], bias=None)
    with pytest.raises(Exception):
        pf.name = "y"  # frozen
