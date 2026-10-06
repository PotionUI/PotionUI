"""Tests for the TRELLIS.2 sparse-structure flow DiT (``SSFlowDiT``).

Coverage: shape invariants + timestep handling (pure-native, always run), and
key-space/numeric parity against the vendored ``SparseStructureFlowModel``
(skipped when the gitignored vendor checkout is absent).
"""

from __future__ import annotations

import torch

from src.platform.runtime.native.arch.trellis2.config import SSFlowConfig
from src.platform.runtime.native.arch.trellis2.ss_flow import SSFlowDiT
from vendor.gpl.comfyui.ops import pick_operations

from ._vendor import import_vendor_models

TINY = SSFlowConfig(
    resolution=2,
    in_channels=4,
    model_channels=16,
    cond_channels=8,
    out_channels=4,
    num_blocks=2,
    num_heads=4,
    mlp_ratio=2.0,
    pe_mode="rope",
    share_mod=True,
    qk_rms_norm=True,
    qk_rms_norm_cross=True,
)


def _fp32_ops():
    return pick_operations(torch.float32, torch.float32)


def _build(config: SSFlowConfig = TINY) -> SSFlowDiT:
    m = SSFlowDiT(config, _fp32_ops())
    m.post_load()
    m.eval()
    return m


# ---------------------------------------------------------------------------
# Pure-native: always run, no vendor checkout required.
# ---------------------------------------------------------------------------

def test_shape_invariants():
    m = _build()
    x = torch.randn(2, TINY.in_channels, TINY.resolution, TINY.resolution, TINY.resolution)
    t = torch.tensor([250.0, 900.0])  # already-scaled (t * 1000), as the sampler passes it
    cond = torch.randn(2, 5, TINY.cond_channels)

    out = m(x, t, cond)

    assert out.shape == (2, TINY.out_channels, TINY.resolution, TINY.resolution, TINY.resolution)


def test_token_count_matches_resolution_cubed():
    m = _build()
    assert m.rope_phases.shape[0] == TINY.resolution**3
    assert TINY.num_tokens == TINY.resolution**3


def test_timestep_used_as_is_no_internal_rescale():
    m = _build()
    x = torch.randn(1, TINY.in_channels, TINY.resolution, TINY.resolution, TINY.resolution)
    cond = torch.randn(1, 5, TINY.cond_channels)
    seen = []
    handle = m.t_embedder.register_forward_pre_hook(lambda _mod, args: seen.append(args[0].detach().clone()))

    try:
        m(x, torch.tensor([100.0]), cond)
        m(x, torch.tensor([900.0]), cond)
    finally:
        handle.remove()

    assert len(seen) == 2
    assert torch.equal(seen[0], torch.tensor([100.0]))
    assert torch.equal(seen[1], torch.tensor([900.0]))


def test_post_load_rebuilds_complex_rope_buffer():
    m = _build()
    before = m.rope_phases.clone()
    m.post_load()
    after = m.rope_phases

    assert after.dtype.is_complex
    assert after.shape == before.shape
    assert torch.allclose(torch.view_as_real(before), torch.view_as_real(after))


# ---------------------------------------------------------------------------
# Vendor parity: skipped when the vendor checkout is absent.
# ---------------------------------------------------------------------------

def _vendor_kwargs(config: SSFlowConfig) -> dict:
    return dict(
        resolution=config.resolution,
        in_channels=config.in_channels,
        model_channels=config.model_channels,
        cond_channels=config.cond_channels,
        out_channels=config.out_channels,
        num_blocks=config.num_blocks,
        num_heads=config.num_heads,
        mlp_ratio=config.mlp_ratio,
        pe_mode=config.pe_mode,
        rope_freq=config.rope_freq,
        share_mod=config.share_mod,
        qk_rms_norm=config.qk_rms_norm,
        qk_rms_norm_cross=config.qk_rms_norm_cross,
        dtype="float32",
    )


def test_key_space_matches_vendor():
    SparseStructureFlowModel, _ = import_vendor_models()

    vendor = SparseStructureFlowModel(**_vendor_kwargs(TINY))
    native = _build()

    assert sorted(native.state_dict().keys()) == sorted(vendor.state_dict().keys())


def test_numeric_parity_forward():
    SparseStructureFlowModel, _ = import_vendor_models()

    torch.manual_seed(0)
    vendor = SparseStructureFlowModel(**_vendor_kwargs(TINY))
    # ``initialize_weights`` (vanilla init) zeroes ``out_layer`` and the
    # share_mod ``adaLN_modulation``'s last Linear so a freshly-constructed
    # model's output is identically 0 regardless of everything upstream —
    # overwrite every parameter with real values so the forward pass (incl.
    # the output projection) is actually exercised.
    with torch.no_grad():
        for p in vendor.parameters():
            p.normal_(0.0, 0.02)
    vendor.eval()

    native = SSFlowDiT(TINY, _fp32_ops())
    missing, unexpected = native.load_state_dict(vendor.state_dict(), strict=False)
    assert not missing and not unexpected
    native.post_load()
    native.eval()

    torch.manual_seed(1)
    x = torch.randn(2, TINY.in_channels, TINY.resolution, TINY.resolution, TINY.resolution)
    t = torch.tensor([250.0, 900.0])
    cond = torch.randn(2, 5, TINY.cond_channels)

    with torch.no_grad():
        out_vendor = vendor(x, t, cond)
        out_native = native(x, t, cond)

    torch.testing.assert_close(out_native, out_vendor, atol=1e-5, rtol=1e-5)


def test_timestep_embedders_run_on_bf16_weights_without_manual_cast():
    import torch
    from vendor.gpl.comfyui.ops import disable_weight_init
    from src.platform.runtime.native.arch.trellis2.ss_flow import _TimestepEmbedder as SSEmbedder
    from src.platform.runtime.native.arch.trellis2.slat_flow import _TimestepEmbedder as SLatEmbedder

    t = torch.tensor([0.5, 0.25])
    ss = SSEmbedder(8, disable_weight_init, frequency_embedding_size=16, dtype=torch.bfloat16)
    slat = SLatEmbedder(8, frequency_embedding_size=16).to(torch.bfloat16)
    assert ss(t).dtype == torch.bfloat16
    assert slat(t).dtype == torch.bfloat16
