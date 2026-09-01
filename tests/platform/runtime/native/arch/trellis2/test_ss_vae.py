"""Tests for the TRELLIS.2 sparse-structure VAE decoder (``SSVAEDecoder``).

Coverage: shape invariants (resolution doubling per upsample level, output
channel count; pure-native, always run) and key-space/numeric parity against
the vendored ``SparseStructureDecoder`` (skipped when the gitignored vendor
checkout is absent).
"""

from __future__ import annotations

import torch

from src.platform.runtime.native.arch.trellis2.config import SSVAEDecoderConfig
from src.platform.runtime.native.arch.trellis2.ss_vae import SSVAEDecoder
from vendor.gpl.comfyui.ops import pick_operations

from ._vendor import import_vendor_models

TINY = SSVAEDecoderConfig(
    out_channels=1,
    latent_channels=4,
    num_res_blocks=1,
    channels=(8, 4),
    num_res_blocks_middle=1,
    norm_type="layer",
)


def _fp32_ops():
    return pick_operations(torch.float32, torch.float32)


def _build(config: SSVAEDecoderConfig = TINY) -> SSVAEDecoder:
    m = SSVAEDecoder(config, _fp32_ops())
    m.post_load()
    m.eval()
    return m


# ---------------------------------------------------------------------------
# Pure-native: always run, no vendor checkout required.
# ---------------------------------------------------------------------------

def test_shape_invariants_resolution_doubles_per_upsample_level():
    m = _build()
    r = 2
    x = torch.randn(1, TINY.latent_channels, r, r, r)

    out = m(x)

    upsample_levels = len(TINY.channels) - 1
    expected_r = r * (2**upsample_levels)
    assert out.shape == (1, TINY.out_channels, expected_r, expected_r, expected_r)


def test_group_norm_variant_also_runs():
    # GroupNorm32 is hardcoded to 32 groups (matching upstream's norm_layer),
    # so the channel counts here must be multiples of 32.
    config = SSVAEDecoderConfig(
        out_channels=1, latent_channels=4, num_res_blocks=1, channels=(64, 32),
        num_res_blocks_middle=1, norm_type="group",
    )
    m = _build(config)
    x = torch.randn(1, config.latent_channels, 2, 2, 2)

    out = m(x)

    assert out.shape == (1, config.out_channels, 4, 4, 4)


# ---------------------------------------------------------------------------
# Vendor parity: skipped when the vendor checkout is absent.
# ---------------------------------------------------------------------------

def _vendor_kwargs(config: SSVAEDecoderConfig) -> dict:
    return dict(
        out_channels=config.out_channels,
        latent_channels=config.latent_channels,
        num_res_blocks=config.num_res_blocks,
        channels=list(config.channels),
        num_res_blocks_middle=config.num_res_blocks_middle,
        norm_type=config.norm_type,
    )


def test_key_space_matches_vendor():
    _, SparseStructureDecoder = import_vendor_models()

    vendor = SparseStructureDecoder(**_vendor_kwargs(TINY))
    native = _build()

    assert sorted(native.state_dict().keys()) == sorted(vendor.state_dict().keys())


def test_numeric_parity_forward():
    _, SparseStructureDecoder = import_vendor_models()

    torch.manual_seed(0)
    vendor = SparseStructureDecoder(**_vendor_kwargs(TINY))
    vendor.eval()

    native = SSVAEDecoder(TINY, _fp32_ops())
    missing, unexpected = native.load_state_dict(vendor.state_dict(), strict=False)
    assert not missing and not unexpected
    native.post_load()
    native.eval()

    torch.manual_seed(1)
    x = torch.randn(2, TINY.latent_channels, 2, 2, 2)

    with torch.no_grad():
        out_vendor = vendor(x)
        out_native = native(x)

    torch.testing.assert_close(out_native, out_vendor, atol=1e-5, rtol=1e-5)
