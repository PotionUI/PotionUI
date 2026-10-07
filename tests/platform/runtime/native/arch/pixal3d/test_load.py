from __future__ import annotations

from dataclasses import replace

import pytest
import torch
from safetensors.torch import save_file

from src.platform.runtime.native.arch.pixal3d import load as pixal3d_load
from src.platform.runtime.native.arch.pixal3d.config import (
    PIXAL3D_SHAPE_SLAT_FLOW_512,
    PIXAL3D_SHAPE_SLAT_FLOW_1024,
    PIXAL3D_SS_FLOW,
    PIXAL3D_TEX_SLAT_FLOW_1024,
)
from src.platform.runtime.native.arch.trellis2 import load as trellis2_load
from src.platform.runtime.native.arch.trellis2.config import SLatFlowConfig, SSFlowConfig
from src.platform.runtime.native.arch.trellis2.detect import (
    FLOW_BUNDLE,
    FLOW_PREFIXES,
    PIXAL3D_FLOW_BUNDLE,
    detect_trellis2_role,
    detect_trellis2_role_from_filename,
)
from src.platform.runtime.native.arch.trellis2.slat_flow import SLatFlowModel
from src.platform.runtime.native.arch.trellis2.ss_flow import SSFlowDiT
from vendor.gpl.comfyui.ops import pick_operations

TINY_SS = SSFlowConfig(
    resolution=2, in_channels=4, model_channels=16, cond_channels=8, out_channels=4, num_blocks=2,
    num_heads=4, mlp_ratio=2.0, share_mod=True, qk_rms_norm=True, qk_rms_norm_cross=True,
)
TINY_SHAPE = SLatFlowConfig(
    resolution=4, in_channels=8, out_channels=8, model_channels=16, cond_channels=8, num_blocks=2,
    num_heads=2, mlp_ratio=2.0,
)
TINY_TEX = replace(TINY_SHAPE, in_channels=16)


def _proj(config, proj_in):
    return replace(config, image_attn_mode="proj", proj_in_channels=proj_in)


def _randomised(module, seed):
    generator = torch.Generator().manual_seed(seed)
    with torch.no_grad():
        for parameter in module.parameters():
            parameter.copy_(torch.randn(parameter.shape, generator=generator))
    return module


def _sources(projected):
    ss = _proj(TINY_SS, 6) if projected else TINY_SS
    shape = _proj(TINY_SHAPE, 10) if projected else TINY_SHAPE
    tex = _proj(TINY_TEX, 10) if projected else TINY_TEX
    return {
        "structure": _randomised(SSFlowDiT(ss, pick_operations(torch.float32, torch.float32)), 1),
        "shape_512": _randomised(SLatFlowModel(**shape.as_kwargs()), 2),
        "shape_1024": _randomised(SLatFlowModel(**shape.as_kwargs()), 3),
        "texture": _randomised(SLatFlowModel(**tex.as_kwargs()), 4),
    }, (ss, shape, tex)


def _bundle(tmp_path, projected=True, name="pixal3d_bf16.safetensors", extra=None):
    sources, configs = _sources(projected)
    tensors = {}
    for role, module in sources.items():
        for key, value in module.state_dict().items():
            if key == "rope_phases":
                continue
            tensors[FLOW_PREFIXES[role] + key] = value.to(torch.bfloat16).contiguous()
    tensors.update(extra or {})
    path = tmp_path / name
    save_file(tensors, str(path))
    return path, sources, configs


def _assert_filled_from(loaded, source):
    expected = {key: value for key, value in source.state_dict().items() if key != "rope_phases"}
    actual = dict(loaded.named_parameters())
    assert set(actual) == {key for key in expected if key in dict(source.named_parameters())}
    for key, parameter in actual.items():
        assert torch.equal(parameter, expected[key].to(torch.bfloat16)), key


def test_every_flow_fills_every_weight_from_a_pixal3d_layout_bundle(tmp_path):
    path, sources, (ss, shape, tex) = _bundle(tmp_path)

    _assert_filled_from(trellis2_load.load_ss_flow(path, ss), sources["structure"])
    _assert_filled_from(trellis2_load.load_shape_slat_flow(path, "512", shape), sources["shape_512"])
    _assert_filled_from(trellis2_load.load_shape_slat_flow(path, "1024", shape), sources["shape_1024"])
    _assert_filled_from(trellis2_load.load_tex_slat_flow(path, "1024", tex), sources["texture"])


def test_the_projection_weights_are_read_out_of_every_block(tmp_path):
    path, sources, (ss, shape, _) = _bundle(tmp_path)
    loaded = trellis2_load.load_shape_slat_flow(path, "1024", shape)
    for block in (0, 1):
        assert loaded.blocks[block].cross_attn.proj_linear.weight.shape == (16, 10)
        assert torch.equal(
            loaded.blocks[block].cross_attn.proj_linear.weight,
            sources["shape_1024"].blocks[block].cross_attn.proj_linear.weight.to(torch.bfloat16),
        )
    ss_loaded = trellis2_load.load_ss_flow(path, ss)
    assert ss_loaded.blocks[1].cross_attn.proj_linear.weight.shape == (16, 6)


def test_a_pixal3d_bundle_is_refused_by_the_trellis2_loaders(tmp_path):
    path, _, _ = _bundle(tmp_path)
    with pytest.raises(ValueError, match="is a Pixal3D bundle"):
        trellis2_load.load_ss_flow(path, TINY_SS)
    with pytest.raises(ValueError, match="is a Pixal3D bundle"):
        trellis2_load.load_shape_slat_flow(path, "512", TINY_SHAPE)
    with pytest.raises(ValueError, match="is a Pixal3D bundle"):
        trellis2_load.load_tex_slat_flow(path, "1024", TINY_TEX)


def test_a_trellis2_bundle_is_refused_by_the_pixal3d_configs(tmp_path):
    path, _, _ = _bundle(tmp_path, projected=False, name="trellis_2_bf16.safetensors")
    with pytest.raises(ValueError, match="is a TRELLIS.2 bundle"):
        trellis2_load.load_ss_flow(path, _proj(TINY_SS, 6))
    with pytest.raises(ValueError, match="is a TRELLIS.2 bundle"):
        trellis2_load.load_shape_slat_flow(path, "1024", _proj(TINY_SHAPE, 10))


def test_a_trellis2_bundle_still_loads_with_the_trellis2_configs(tmp_path):
    path, sources, (ss, shape, tex) = _bundle(tmp_path, projected=False, name="trellis_2_bf16.safetensors")
    _assert_filled_from(trellis2_load.load_ss_flow(path, ss), sources["structure"])
    _assert_filled_from(trellis2_load.load_tex_slat_flow(path, "1024", tex), sources["texture"])


def test_a_quantised_bundle_is_refused_with_a_pointer_to_the_bf16_one(tmp_path):
    scale = {FLOW_PREFIXES["structure"] + "blocks.0.mlp.mlp.0.weight_scale": torch.ones(1)}
    path, _, (ss, _, _) = _bundle(tmp_path, name="pixal3d_int8_convrot.safetensors", extra=scale)
    with pytest.raises(ValueError, match="quantised"):
        trellis2_load.load_ss_flow(path, ss)


def test_the_bundle_is_detected_as_pixal3d_from_its_keys(tmp_path):
    pixal3d, _, _ = _bundle(tmp_path)
    trellis2, _, _ = _bundle(tmp_path, projected=False, name="trellis_2_bf16.safetensors")
    from safetensors import safe_open

    with safe_open(str(pixal3d), framework="pt") as handle:
        assert detect_trellis2_role(handle.keys()) == PIXAL3D_FLOW_BUNDLE
    with safe_open(str(trellis2), framework="pt") as handle:
        assert detect_trellis2_role(handle.keys()) == FLOW_BUNDLE


def test_the_bundle_name_alone_hints_pixal3d():
    assert detect_trellis2_role_from_filename("pixal3d_multiview_bf16.safetensors") == PIXAL3D_FLOW_BUNDLE
    assert detect_trellis2_role_from_filename("trellis_2_bf16.safetensors") == FLOW_BUNDLE


def test_the_pixal3d_loaders_pass_the_pixal3d_configs(monkeypatch):
    calls = []
    monkeypatch.setattr(trellis2_load, "load_ss_flow", lambda path, config, dtype=None: calls.append(("ss", config)))
    monkeypatch.setattr(
        trellis2_load, "load_shape_slat_flow",
        lambda path, tier, config, dtype=None: calls.append((f"shape_{tier}", config)),
    )
    monkeypatch.setattr(
        trellis2_load, "load_tex_slat_flow",
        lambda path, tier, config, dtype=None: calls.append((f"tex_{tier}", config)),
    )
    pixal3d_load.load_pixal3d_ss_flow("x")
    pixal3d_load.load_pixal3d_shape_flow("x", "512")
    pixal3d_load.load_pixal3d_shape_flow("x", "1024")
    pixal3d_load.load_pixal3d_tex_flow("x")
    assert calls == [
        ("ss", PIXAL3D_SS_FLOW),
        ("shape_512", PIXAL3D_SHAPE_SLAT_FLOW_512),
        ("shape_1024", PIXAL3D_SHAPE_SLAT_FLOW_1024),
        ("tex_1024", PIXAL3D_TEX_SLAT_FLOW_1024),
    ]


def test_an_unknown_shape_tier_is_refused():
    with pytest.raises(ValueError, match="tier"):
        pixal3d_load.load_pixal3d_shape_flow("x", "1536")
