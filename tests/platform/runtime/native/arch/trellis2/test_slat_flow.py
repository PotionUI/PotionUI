"""Key-space and numeric parity for the native SLat flow DiT vs vendored
microsoft/TRELLIS.2 (MIT). Vendor-import tests SKIP cleanly when the
gitignored ``content/plugins/local/trellis2/vendor/TRELLIS.2`` checkout is
absent; the ragged-batch / no-leakage test needs only the native module and
always runs.
"""

import os
import sys
from pathlib import Path

import pytest
import torch

from src.platform.runtime.native.arch.trellis2.slat_flow import SLatFlowModel

_VENDOR_ROOT = (
    Path(__file__).resolve().parents[6]
    / "content"
    / "plugins"
    / "local"
    / "trellis2"
    / "vendor"
    / "TRELLIS.2"
)


def _load_vendored_slat_flow_model():
    if not (_VENDOR_ROOT / "trellis2").is_dir():
        pytest.skip(f"vendored TRELLIS.2 checkout absent at {_VENDOR_ROOT}")
    os.environ.setdefault("SPARSE_ATTN_BACKEND", "sdpa")
    vendor_path = str(_VENDOR_ROOT)
    added = vendor_path not in sys.path
    if added:
        sys.path.insert(0, vendor_path)
    try:
        from trellis2.models.structured_latent_flow import SLatFlowModel as VendoredSLatFlowModel
        from trellis2.modules.sparse import SparseTensor as VendoredSparseTensor
    finally:
        if added:
            sys.path.remove(vendor_path)
    return VendoredSLatFlowModel, VendoredSparseTensor


_TINY_CONFIG = dict(
    resolution=4,
    in_channels=8,
    model_channels=16,
    cond_channels=12,
    out_channels=8,
    num_blocks=2,
    num_heads=2,
    mlp_ratio=2.0,
    pe_mode="rope",
    share_mod=True,
    qk_rms_norm=True,
    qk_rms_norm_cross=True,
)


def _randomize_parameters(module: torch.nn.Module, generator: torch.Generator) -> None:
    """Vendored init zeroes ``out_layer`` and the shared ``adaLN_modulation``
    (train-from-scratch convention), which would make every forward output
    identically zero regardless of anything upstream of it - a parity test
    built on that init can't fail. Overwrite every parameter with noise first
    so the numeric-parity assertions actually exercise the full graph."""
    with torch.no_grad():
        for p in module.parameters():
            p.copy_(torch.randn(p.shape, generator=generator) * 0.02)


def _random_sparse_inputs(batch_sizes, in_channels, resolution, generator):
    feats_list, coords_list = [], []
    for n in batch_sizes:
        feats_list.append(torch.randn(n, in_channels, generator=generator))
        coords_list.append(torch.randint(0, resolution, (n, 3), generator=generator))
    feats = torch.cat(feats_list, dim=0)
    coords = []
    for b, c in enumerate(coords_list):
        coords.append(torch.cat([torch.full((c.shape[0], 1), b, dtype=torch.long), c], dim=1))
    coords = torch.cat(coords, dim=0)
    return feats, coords


def test_key_space_matches_vendor():
    VendoredSLatFlowModel, _ = _load_vendored_slat_flow_model()
    torch.manual_seed(0)
    vendored = VendoredSLatFlowModel(**_TINY_CONFIG)
    native = SLatFlowModel(**_TINY_CONFIG)

    vendored_keys = {k: tuple(v.shape) for k, v in vendored.state_dict().items()}
    native_keys = {k: tuple(v.shape) for k, v in native.state_dict().items()}
    assert native_keys == vendored_keys


def test_numeric_parity_matches_vendor():
    VendoredSLatFlowModel, VendoredSparseTensor = _load_vendored_slat_flow_model()
    torch.manual_seed(1)
    vendored = VendoredSLatFlowModel(**_TINY_CONFIG).eval()
    native = SLatFlowModel(**_TINY_CONFIG).eval()
    _randomize_parameters(vendored, torch.Generator().manual_seed(100))
    native.load_state_dict(vendored.state_dict())

    gen = torch.Generator().manual_seed(2)
    feats, coords = _random_sparse_inputs([5, 3], _TINY_CONFIG["in_channels"], _TINY_CONFIG["resolution"], gen)
    cond = torch.randn(2, 7, _TINY_CONFIG["cond_channels"], generator=gen)
    t = torch.rand(2, generator=gen) * 1000.0

    with torch.no_grad():
        vendored_out = vendored(VendoredSparseTensor(feats.clone(), coords.clone()), t.clone(), cond.clone())
        native_out = native(_native_sparse_tensor(feats.clone(), coords.clone()), t.clone(), cond.clone())

    torch.testing.assert_close(native_out.feats, vendored_out.feats, atol=1e-5, rtol=1e-4)


def test_concat_cond_parity_matches_vendor():
    VendoredSLatFlowModel, VendoredSparseTensor = _load_vendored_slat_flow_model()
    config = dict(_TINY_CONFIG, in_channels=64, out_channels=32)
    torch.manual_seed(3)
    vendored = VendoredSLatFlowModel(**config).eval()
    native = SLatFlowModel(**config).eval()
    _randomize_parameters(vendored, torch.Generator().manual_seed(101))
    native.load_state_dict(vendored.state_dict())

    gen = torch.Generator().manual_seed(4)
    resolution = config["resolution"]
    n = [4, 6]
    feats, coords = _random_sparse_inputs(n, 32, resolution, gen)
    concat_feats = torch.randn(sum(n), 32, generator=gen)
    cond = torch.randn(2, 5, config["cond_channels"], generator=gen)
    t = torch.rand(2, generator=gen) * 1000.0

    with torch.no_grad():
        vendored_out = vendored(
            VendoredSparseTensor(feats.clone(), coords.clone()),
            t.clone(),
            cond.clone(),
            concat_cond=VendoredSparseTensor(concat_feats.clone(), coords.clone()),
        )
        native_out = native(
            _native_sparse_tensor(feats.clone(), coords.clone()),
            t.clone(),
            cond.clone(),
            concat_cond=_native_sparse_tensor(concat_feats.clone(), coords.clone()),
        )

    torch.testing.assert_close(native_out.feats, vendored_out.feats, atol=1e-5, rtol=1e-4)


def _native_sparse_tensor(feats, coords):
    from src.platform.runtime.native.sparse3d import SparseTensor

    return SparseTensor(feats, coords)


def test_ragged_batch_no_cross_sequence_leakage():
    config = dict(_TINY_CONFIG, num_blocks=1)
    torch.manual_seed(5)
    model = SLatFlowModel(**config).eval()

    gen = torch.Generator().manual_seed(6)
    n = [4, 6]
    feats, coords = _random_sparse_inputs(n, config["in_channels"], config["resolution"], gen)
    cond = torch.randn(2, 5, config["cond_channels"], generator=gen)
    t = torch.rand(2, generator=gen) * 1000.0

    feats_perturbed = feats.clone()
    feats_perturbed[n[0]:] += 10.0  # perturb only sequence B's tokens

    with torch.no_grad():
        out_a = model(_native_sparse_tensor(feats, coords), t.clone(), cond.clone())
        out_b = model(_native_sparse_tensor(feats_perturbed, coords), t.clone(), cond.clone())

    torch.testing.assert_close(out_a.feats[: n[0]], out_b.feats[: n[0]])
    assert not torch.allclose(out_a.feats[n[0]:], out_b.feats[n[0]:])
