from __future__ import annotations

import hashlib
from dataclasses import replace

import pytest
import torch

from src.platform.runtime.native.arch.pixal3d.config import (
    PIXAL3D_SHAPE_SLAT_FLOW_512,
    PIXAL3D_SHAPE_SLAT_FLOW_1024,
    PIXAL3D_SS_FLOW,
    PIXAL3D_TEX_SLAT_FLOW_1024,
)
from src.platform.runtime.native.arch.pixal3d.conditioning import ProjectedCondition
from src.platform.runtime.native.arch.trellis2.config import (
    SHAPE_SLAT_FLOW_1024,
    SS_FLOW_PRODUCTION,
    SSFlowConfig,
    SLatFlowConfig,
)
from src.platform.runtime.native.arch.trellis2.slat_flow import SLatFlowModel
from src.platform.runtime.native.arch.trellis2.ss_flow import SSFlowDiT
from src.platform.runtime.native.sparse3d import SparseTensor
from vendor.gpl.comfyui.ops import manual_cast, pick_operations

TINY_SS = SSFlowConfig(
    resolution=2, in_channels=4, model_channels=16, cond_channels=8, out_channels=4, num_blocks=2,
    num_heads=4, mlp_ratio=2.0, share_mod=True, qk_rms_norm=True, qk_rms_norm_cross=True,
)
TINY_SS_PROJ = replace(TINY_SS, image_attn_mode="proj", proj_in_channels=6)

TINY_SLAT = dict(
    resolution=4, in_channels=8, model_channels=16, cond_channels=12, out_channels=8, num_blocks=2,
    num_heads=2, mlp_ratio=2.0, share_mod=True, qk_rms_norm=True, qk_rms_norm_cross=True,
)

_SS_GOLDEN_KEYS = ("d7f6c84e513ff475c46f9c2c4135e2aa0a0912753880d87ca95c987235e36478", 53)
_SS_GOLDEN = [
    -0.161361, -0.777805, -0.333468, -0.408929, -0.618219, -0.577313, -0.709302, -0.624643,
    0.691792, 0.777593, 0.657385, 0.204163, 0.572077, 0.384739, 0.1335, 0.151881,
    0.195224, -0.077422, 0.122734, 0.412922, -0.074625, 0.358564, 0.195436, 0.406115,
    0.199769, 0.373239, 0.084547, 0.083258, -0.061901, 0.420077, -0.104742, 0.16829,
]
_SLAT_GOLDEN_KEYS = ("ed422ef4bee7e149bf60f103f517dc971e9260ec28a0d3b2dc631a885136afa8", 52)
_SLAT_GOLDEN = [
    0.321244, 0.144207, 0.262095, -0.983979, 0.082285, 0.10346, -0.429001, 0.157267,
    0.040028, -0.054902, 0.370662, -0.605634, 0.133252, -0.010915, 0.249692, 0.016404,
    0.160487, 0.093452, -0.028527, -0.21717, -0.712666, 0.192644, -0.007929, -0.327243,
    0.04315, 0.209535, -0.059776, 0.009258, -0.350613, -0.363801, 0.492287, -0.08556,
    -0.056931, -0.024004, -0.185244, -0.017973, -0.413015, 0.547988, -0.028174, 0.478242,
]

_COORDS = torch.tensor([[0, 0, 0, 0], [0, 1, 0, 2], [0, 3, 1, 1], [0, 2, 2, 3], [0, 0, 3, 1]], dtype=torch.int32)


def _fill(module, seed):
    generator = torch.Generator().manual_seed(seed)
    with torch.no_grad():
        for _, parameter in sorted(module.named_parameters()):
            parameter.copy_(torch.randn(parameter.shape, generator=generator, dtype=torch.float64).to(parameter.dtype) * 0.1)
    return module


def _key_digest(module):
    rows = [f"{key}:{tuple(value.shape)}" for key, value in sorted(module.state_dict().items())]
    return hashlib.sha256("\n".join(rows).encode()).hexdigest(), len(rows)


def _ss(config=TINY_SS, seed=1):
    module = SSFlowDiT(config, pick_operations(torch.float32, torch.float32))
    module.post_load()
    return _fill(module.eval(), seed)


def _slat(seed=3, **overrides):
    return _fill(SLatFlowModel(**{**TINY_SLAT, **overrides}).eval(), seed)


def test_the_trellis2_dense_flow_is_unchanged_by_the_projection_variant():
    model = _ss()
    generator = torch.Generator().manual_seed(2)
    x = torch.randn(1, 4, 2, 2, 2, generator=generator)
    cond = torch.randn(1, 5, 8, generator=generator)
    with torch.no_grad():
        out = model(x, torch.tensor([500.0]), cond)
    assert _key_digest(model) == _SS_GOLDEN_KEYS
    assert torch.allclose(out.flatten(), torch.tensor(_SS_GOLDEN), atol=2e-6)


def test_the_trellis2_sparse_flow_is_unchanged_by_the_projection_variant():
    model = _slat()
    generator = torch.Generator().manual_seed(4)
    x = SparseTensor(feats=torch.randn(5, 8, generator=generator), coords=_COORDS)
    cond = torch.randn(1, 5, 12, generator=generator)
    with torch.no_grad():
        out = model(x, torch.tensor([500.0]), cond)
    assert _key_digest(model) == _SLAT_GOLDEN_KEYS
    assert torch.allclose(out.feats.flatten(), torch.tensor(_SLAT_GOLDEN), atol=2e-6)


def _renamed(key):
    head, _, tail = key.partition(".cross_attn.")
    return f"{head}.cross_attn.cross_attn_block.{tail}" if tail else key


def _parameter_shapes(module):
    return {name: tuple(parameter.shape) for name, parameter in module.named_parameters()}


@pytest.mark.parametrize(
    "trellis2,pixal3d,proj_in",
    [
        (SS_FLOW_PRODUCTION, PIXAL3D_SS_FLOW, 1024),
        (SHAPE_SLAT_FLOW_1024, PIXAL3D_SHAPE_SLAT_FLOW_1024, 2048),
    ],
)
def test_the_production_variant_matches_the_comfy_bundle_key_space(trellis2, pixal3d, proj_in):
    with torch.device("meta"):
        if isinstance(trellis2, SSFlowConfig):
            base, variant = SSFlowDiT(trellis2, manual_cast), SSFlowDiT(pixal3d, manual_cast)
        else:
            base, variant = SLatFlowModel(**trellis2.as_kwargs()), SLatFlowModel(**pixal3d.as_kwargs())
    base_keys = _parameter_shapes(base)
    variant_keys = _parameter_shapes(variant)

    assert len(base_keys) == 640
    assert len(variant_keys) == 700
    expected = {_renamed(key): shape for key, shape in base_keys.items()}
    for block in range(30):
        expected[f"blocks.{block}.cross_attn.proj_linear.weight"] = (1536, proj_in)
        expected[f"blocks.{block}.cross_attn.proj_linear.bias"] = (1536,)
    assert variant_keys == expected
    assert "blocks.0.cross_attn.to_q.weight" not in variant_keys
    assert variant_keys["blocks.29.cross_attn.cross_attn_block.to_kv.weight"] == (3072, 1024)


def test_the_four_pixal3d_flow_configs_are_projection_variants_of_the_trellis2_ones():
    assert PIXAL3D_SS_FLOW.proj_in_channels == 1024
    for config in (PIXAL3D_SHAPE_SLAT_FLOW_512, PIXAL3D_SHAPE_SLAT_FLOW_1024, PIXAL3D_TEX_SLAT_FLOW_1024):
        assert config.image_attn_mode == "proj"
        assert config.proj_in_channels == 2048
    assert (PIXAL3D_SHAPE_SLAT_FLOW_512.resolution, PIXAL3D_SHAPE_SLAT_FLOW_1024.resolution) == (32, 64)
    assert (PIXAL3D_TEX_SLAT_FLOW_1024.resolution, PIXAL3D_TEX_SLAT_FLOW_1024.in_channels) == (64, 64)


def test_an_unknown_attention_mode_is_refused():
    with pytest.raises(ValueError, match="image_attn_mode"):
        replace(TINY_SS, image_attn_mode="gated_proj")
    with pytest.raises(ValueError, match="image_attn_mode"):
        SLatFlowConfig(resolution=4, in_channels=8, out_channels=8, image_attn_mode="gated_proj")


def test_the_dense_projection_block_adds_the_projected_feature_to_cross_attention():
    model = _ss(TINY_SS_PROJ)
    block = model.blocks[0].cross_attn
    x = torch.randn(1, 8, 16)
    tokens = torch.randn(1, 5, 8)
    proj = torch.randn(1, 8, 6)
    with torch.no_grad():
        expected = block.proj_linear(proj) + block.cross_attn_block(x, tokens)
        assert torch.allclose(block(x, (tokens, proj)), expected)


def test_the_dense_projection_flow_runs_and_depends_on_the_projected_features():
    model = _ss(TINY_SS_PROJ)
    x = torch.randn(1, 4, 2, 2, 2)
    tokens = torch.randn(1, 5, 8)
    proj = torch.randn(1, 8, 6)
    with torch.no_grad():
        out = model(x, torch.tensor([500.0]), ProjectedCondition(tokens, proj))
        changed = model(x, torch.tensor([500.0]), ProjectedCondition(tokens, proj + 1.0))
    assert out.shape == x.shape
    assert not torch.allclose(out, changed)


def test_the_dense_projection_flow_casts_both_condition_parts_to_its_compute_dtype():
    model = _ss(TINY_SS_PROJ)
    with torch.no_grad():
        out = model(
            torch.randn(1, 4, 2, 2, 2), torch.tensor([500.0]),
            ProjectedCondition(torch.randn(1, 5, 8).double(), torch.randn(1, 8, 6).double()),
        )
    assert out.dtype == torch.float32


def test_the_dense_projection_flow_refuses_features_that_do_not_cover_the_grid():
    model = _ss(TINY_SS_PROJ)
    with pytest.raises(ValueError, match="one row per voxel"):
        model(torch.randn(1, 4, 2, 2, 2), torch.tensor([500.0]), (torch.randn(1, 5, 8), torch.randn(1, 7, 6)))


def _proj_slat():
    return _slat(image_attn_mode="proj", proj_in_channels=10)


def _sparse_inputs(seed=5):
    generator = torch.Generator().manual_seed(seed)
    x = SparseTensor(feats=torch.randn(5, 8, generator=generator), coords=_COORDS.clone())
    tokens = torch.randn(1, 5, 12, generator=generator)
    proj = SparseTensor(feats=torch.randn(5, 10, generator=generator), coords=x.coords)
    return x, tokens, proj


def test_the_sparse_projection_flow_runs_on_features_carried_on_the_latents_coords():
    model = _proj_slat()
    x, tokens, proj = _sparse_inputs()
    with torch.no_grad():
        out = model(x, torch.tensor([500.0]), ProjectedCondition(tokens, proj))
        changed = model(x, torch.tensor([500.0]), ProjectedCondition(tokens, proj.replace(proj.feats * 2.0)))
    assert out.feats.shape == (5, 8)
    assert not torch.allclose(out.feats, changed.feats)


def test_equal_but_distinct_coordinate_tensors_count_as_aligned():
    model = _proj_slat()
    x, tokens, proj = _sparse_inputs()
    with torch.no_grad():
        same = model(x, torch.tensor([500.0]), (tokens, proj))
        copied = model(x, torch.tensor([500.0]), (tokens, SparseTensor(feats=proj.feats, coords=x.coords.clone())))
    assert torch.equal(same.feats, copied.feats)


def test_permuted_projection_rows_are_refused_rather_than_misconditioning():
    model = _proj_slat()
    x, tokens, proj = _sparse_inputs()
    order = torch.tensor([1, 0, 2, 3, 4])
    permuted = SparseTensor(feats=proj.feats[order], coords=proj.coords[order])
    with pytest.raises(ValueError, match="not row-aligned"):
        model(x, torch.tensor([500.0]), (tokens, permuted))


def test_a_projection_with_a_different_row_count_is_refused():
    model = _proj_slat()
    x, tokens, proj = _sparse_inputs()
    with pytest.raises(ValueError, match="not row-aligned"):
        model(x, torch.tensor([500.0]), (tokens, SparseTensor(feats=proj.feats[:4], coords=proj.coords[:4])))


def test_projected_features_without_coordinates_are_refused():
    model = _proj_slat()
    x, tokens, proj = _sparse_inputs()
    with pytest.raises(TypeError, match="SparseTensor"):
        model(x, torch.tensor([500.0]), (tokens, proj.feats))


def test_the_texture_flow_checks_alignment_against_the_noise_before_concatenating_the_shape():
    model = _fill(SLatFlowModel(**{**TINY_SLAT, "in_channels": 16}, image_attn_mode="proj", proj_in_channels=10).eval(), 6)
    x, tokens, proj = _sparse_inputs()
    shape = x.replace(torch.randn(5, 8))
    with torch.no_grad():
        out = model(x, torch.tensor([500.0]), (tokens, proj), concat_cond=shape)
    assert out.feats.shape == (5, 8)


def test_the_negative_condition_keeps_the_coordinates_and_zeroes_both_parts():
    _, tokens, proj = _sparse_inputs()
    negative = ProjectedCondition(tokens, proj).negative()
    assert negative.proj.coords is proj.coords
    assert torch.count_nonzero(negative.proj.feats) == 0
    assert torch.count_nonzero(negative.tokens) == 0
    assert negative.dtype == tokens.dtype
