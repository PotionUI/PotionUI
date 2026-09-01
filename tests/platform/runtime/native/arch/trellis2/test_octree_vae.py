"""Tests for the TRELLIS.2 octree sparse U-Net VAE decoders (shape FDG decoder
+ texture decoder, ``octree_vae.py``).

Coverage:
  * structural key-space (tiny config, hand-derived expected key template —
    always runs, no checkpoint needed)
  * key+shape parity against the real depot checkpoint (skipped unless
    ``POTIONUI_MODEL_TESTS=1`` and the file is present on disk)
  * the shape/texture grid-match contract (guide_subs reproduces the exact
    same coord set)
  * upsample() coords-only growth
  * SparseChannel2Spatial-driven child-coord growth on a hand-set subdivision
  * FDG head math against a hand-computed reference
  * a tiny end-to-end shape-decoder forward smoke test
"""

from __future__ import annotations

from pathlib import Path

import pytest
import torch
import torch.nn.functional as F

from src.platform.runtime.native.arch.trellis2.config import (
    OCTREE_VAE_DECODER_TORSO_PRODUCTION,
    OctreeVaeDecoderConfig,
)
from src.platform.runtime.native.arch.trellis2.octree_vae import (
    FlexiDualGridVaeDecoder,
    SparseUnetVaeDecoder,
    _fdg_head,
)
from src.platform.runtime.native.sparse3d import SparseTensor

_REPO_ROOT = Path(__file__).resolve().parents[6]
_SHAPE_VAE_PATH = _REPO_ROOT / "models" / "vae" / "trellis_2_shape_vae_bf16.safetensors"
_TEXTURE_VAE_PATH = _REPO_ROOT / "models" / "vae" / "trellis_2_texture_vae_bf16.safetensors"

TINY = OctreeVaeDecoderConfig(model_channels=(16, 8), latent_channels=4, num_blocks=(2, 0))


def _tiny_input(n: int = 3, generator: torch.Generator | None = None) -> SparseTensor:
    feats = torch.randn(n, TINY.latent_channels, generator=generator)
    coords = torch.tensor([[0, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 1]][:n])
    return SparseTensor(feats, coords)


# ---------------------------------------------------------------------------
# Structural key-space (route 1: always runs)
# ---------------------------------------------------------------------------


def _expected_keys(config: OctreeVaeDecoderConfig, pred_subdiv: bool) -> set[str]:
    """Hand-derived from ``sparse_unet_vae.py``'s ``SparseConvNeXtBlock3d``
    (norm/conv/mlp.0/mlp.2) and ``SparseResBlockC2S3d`` (norm1 only —
    ``norm2`` is ``elementwise_affine=False`` so has no params; ``to_subdiv``
    only when the whole decoder predicts subdivisions)."""
    keys = {"from_latent.weight", "from_latent.bias", "output_layer.weight", "output_layer.bias"}
    num_blocks = config.num_blocks
    for i in range(len(num_blocks)):
        for j in range(num_blocks[i]):
            p = f"blocks.{i}.{j}"
            keys |= {
                f"{p}.norm.weight", f"{p}.norm.bias",
                f"{p}.conv.weight", f"{p}.conv.bias",
                f"{p}.mlp.0.weight", f"{p}.mlp.0.bias",
                f"{p}.mlp.2.weight", f"{p}.mlp.2.bias",
            }
        if i < len(num_blocks) - 1:
            p = f"blocks.{i}.{num_blocks[i]}"
            keys |= {
                f"{p}.norm1.weight", f"{p}.norm1.bias",
                f"{p}.conv1.weight", f"{p}.conv1.bias",
                f"{p}.conv2.weight", f"{p}.conv2.bias",
            }
            if pred_subdiv:
                keys |= {f"{p}.to_subdiv.weight", f"{p}.to_subdiv.bias"}
    return keys


def test_texture_decoder_key_space_matches_hand_derived_template():
    m = SparseUnetVaeDecoder(TINY, out_channels=6, pred_subdiv=False)
    assert set(m.state_dict().keys()) == _expected_keys(TINY, pred_subdiv=False)


def test_shape_decoder_key_space_matches_hand_derived_template():
    m = FlexiDualGridVaeDecoder(TINY, resolution=8)
    assert set(m.state_dict().keys()) == _expected_keys(TINY, pred_subdiv=True)


# ---------------------------------------------------------------------------
# Real checkpoint key+shape parity (route 2: skipped unless present + opted in)
# ---------------------------------------------------------------------------


def _checkpoint_prefixed_keys(path: Path, prefix: str) -> dict[str, tuple[int, ...]]:
    from safetensors import safe_open

    with safe_open(str(path), framework="pt") as f:
        return {
            k[len(prefix):]: tuple(f.get_slice(k).get_shape())
            for k in f.keys()
            if k.startswith(prefix)
        }


@pytest.mark.requires_models
@pytest.mark.skipif(not _TEXTURE_VAE_PATH.exists(), reason="needs the real trellis2 texture VAE checkpoint on disk")
def test_texture_decoder_production_config_matches_real_checkpoint():
    m = SparseUnetVaeDecoder(OCTREE_VAE_DECODER_TORSO_PRODUCTION, out_channels=6, pred_subdiv=False)
    ours = {k: tuple(v.shape) for k, v in m.state_dict().items()}
    checkpoint = _checkpoint_prefixed_keys(_TEXTURE_VAE_PATH, "txt_dec.")
    assert ours == checkpoint


@pytest.mark.requires_models
@pytest.mark.skipif(not _SHAPE_VAE_PATH.exists(), reason="needs the real trellis2 shape VAE checkpoint on disk")
def test_shape_decoder_production_config_matches_real_checkpoint():
    m = FlexiDualGridVaeDecoder(OCTREE_VAE_DECODER_TORSO_PRODUCTION, resolution=256)
    ours = {k: tuple(v.shape) for k, v in m.state_dict().items()}
    checkpoint = _checkpoint_prefixed_keys(_SHAPE_VAE_PATH, "shape_dec.")
    assert ours == checkpoint


# ---------------------------------------------------------------------------
# subs contract: guide_subs reproduces the exact shape-decoder coord set
# ---------------------------------------------------------------------------


def test_guide_subs_reproduces_the_shape_decoders_coord_set():
    torch.manual_seed(0)
    shape = FlexiDualGridVaeDecoder(TINY, resolution=8).eval()
    texture = SparseUnetVaeDecoder(TINY, out_channels=6, pred_subdiv=False).eval()

    x = _tiny_input(generator=torch.Generator().manual_seed(1))

    with torch.no_grad():
        shape_out = shape(x, return_subs=True)
        texture_out = texture(x, guide_subs=shape_out.subs)

    shape_coords = {tuple(c) for c in shape_out.coords.tolist()}
    texture_coords = {tuple(c) for c in texture_out.coords.tolist()}
    assert shape_coords == texture_coords
    assert len(shape_coords) > 0


def test_guide_subs_is_rejected_by_a_pred_subdiv_true_decoder():
    pred_subdiv_true = SparseUnetVaeDecoder(TINY, out_channels=7, pred_subdiv=True)
    x = _tiny_input()
    with pytest.raises(AssertionError):
        pred_subdiv_true(x, guide_subs=[None])


def test_return_subs_is_rejected_by_a_pred_subdiv_false_decoder():
    texture = SparseUnetVaeDecoder(TINY, out_channels=6, pred_subdiv=False)
    x = _tiny_input()
    with pytest.raises(AssertionError):
        texture(x, return_subs=True)


# ---------------------------------------------------------------------------
# upsample(): coords-only octree growth
# ---------------------------------------------------------------------------


def test_upsample_grows_coords_by_two_to_the_levels_and_contains_the_input():
    config = OctreeVaeDecoderConfig(model_channels=(16, 8, 8, 8), latent_channels=4, num_blocks=(1, 1, 1, 0))
    m = FlexiDualGridVaeDecoder(config, resolution=32).eval()
    x = _tiny_input(n=2, generator=torch.Generator().manual_seed(2))

    with torch.no_grad():
        coords_0 = m.upsample(x, upsample_times=0)
        coords_2 = m.upsample(x, upsample_times=2)

    assert torch.equal(coords_0, x.coords)
    # each of the 2 upsample levels multiplies the active set by up to 8
    # (octree factor 2, 3 spatial axes) and doubles every spatial coordinate.
    assert coords_2.shape[0] <= x.coords.shape[0] * 8 * 8
    assert coords_2.shape[0] >= x.coords.shape[0]
    parent_of = coords_2.clone()
    parent_of[:, 1:] //= 4  # two levels of factor-2 growth
    assert set(map(tuple, parent_of.tolist())) <= set(map(tuple, x.coords.tolist()))


# ---------------------------------------------------------------------------
# SparseChannel2Spatial-based growth: hand-set subdivision mask
# ---------------------------------------------------------------------------


def test_c2s_transition_grows_only_the_marked_children():
    from src.platform.runtime.native.arch.trellis2.octree_vae import _SparseResBlockC2S3d

    torch.manual_seed(3)
    block = _SparseResBlockC2S3d(channels=16, out_channels=8, pred_subdiv=False).eval()

    x = SparseTensor(torch.randn(1, 16), torch.tensor([[0, 2, 2, 2]]))
    # child bit order matches SparseChannel2Spatial: index = dx + 2*dy + 4*dz.
    mask = torch.zeros(1, 8, dtype=torch.bool)
    mask[0, 0] = True  # child (0,0,0) -> voxel (4,4,4)
    mask[0, 3] = True  # child (1,1,0) -> voxel (5,5,4)
    subdiv = x.replace(mask)

    with torch.no_grad():
        h, sub_out = block(x, subdiv=subdiv)

    assert sub_out is subdiv
    expected = {(0, 4, 4, 4), (0, 5, 5, 4)}
    assert set(map(tuple, h.coords.tolist())) == expected
    assert h.feats.shape == (2, 8)


# ---------------------------------------------------------------------------
# Head math: hand-computed vertices/intersected/quad_lerp
# ---------------------------------------------------------------------------


def test_fdg_head_math_matches_hand_computation():
    feats = torch.tensor([
        [2.0, -1.0, 0.5, 1.0, -1.0, 0.0, 3.0],
        [-3.0, 0.0, 10.0, -5.0, 2.0, -0.1, -2.0],
    ])
    coords = torch.tensor([[0, 0, 0, 0], [0, 1, 1, 1]])
    h = SparseTensor(feats, coords)

    vertices, intersected, quad_lerp = _fdg_head(h, voxel_margin=0.5)

    expected_vertices = 2.0 * torch.sigmoid(feats[:, 0:3]) - 0.5
    expected_intersected = feats[:, 3:6] > 0
    expected_quad_lerp = F.softplus(feats[:, 6:7])

    torch.testing.assert_close(vertices.feats, expected_vertices)
    assert torch.equal(intersected.feats, expected_intersected)
    torch.testing.assert_close(quad_lerp.feats, expected_quad_lerp)
    assert torch.equal(vertices.coords, coords)


# ---------------------------------------------------------------------------
# Shape smoke: tiny full decoder forward, output dims + monotonic growth
# ---------------------------------------------------------------------------


def test_shape_decoder_forward_smoke_end_to_end():
    config = OctreeVaeDecoderConfig(model_channels=(16, 8, 8), latent_channels=4, num_blocks=(1, 1, 0))
    m = FlexiDualGridVaeDecoder(config, resolution=16).eval()
    x = _tiny_input(n=2, generator=torch.Generator().manual_seed(4))

    with torch.no_grad():
        out = m(x, return_subs=True)

    n = out.coords.shape[0]
    assert out.vertices.feats.shape == (n, 3)
    assert out.intersected.feats.shape == (n, 3)
    assert out.quad_lerp.feats.shape == (n, 1)
    assert (out.quad_lerp.feats >= 0).all()
    assert torch.equal(out.vertices.coords, out.coords)
    assert len(out.subs) == len(config.num_blocks) - 1

    level_sizes = [x.coords.shape[0]]
    for sub in out.subs:
        level_sizes.append(int((sub.feats > 0).sum().item()))
    assert level_sizes[-1] == n or level_sizes[-1] >= level_sizes[0]
