from __future__ import annotations

from pathlib import Path

import pytest
import torch
from safetensors.torch import save_file

from src.platform.runtime.native.arch.moge import (
    MOGE2_VITL,
    MoGe2Config,
    MoGe2Model,
    detect_moge2_config,
    load_moge2,
    load_moge2_state_dict,
    map_moge2_state_dict,
)

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"

TINY = MoGe2Config(
    embed_dim=64,
    depth=4,
    num_heads=1,
    mlp_hidden=128,
    pos_grid=4,
    intermediate_layers=(0, 1, 2, 3),
    projection_dim=16,
    neck_dims=(16, 8, 8, 8, 4),
)

_COMFY_EXTRAS = {
    "encoder.backbone.mask_token": (1, 64),
    "encoder.image_mean": (1, 3, 1, 1),
    "encoder.image_std": (1, 3, 1, 1),
    "normal_head.output_blocks.4.weight": (3, 4, 1, 1),
    "scale_head.0.weight": (64, 64),
}


def _comfy_shapes() -> dict:
    shapes = {}
    for line in (_FIXTURES / "moge_2_vitl_normal_fp16.keys.txt").read_text().splitlines():
        key, dims = line.split()
        shapes[key] = tuple(int(dim) for dim in dims.split(","))
    return shapes


def _tiny_model(seed: int = 0) -> MoGe2Model:
    model = MoGe2Model(TINY)
    generator = torch.Generator().manual_seed(seed)
    with torch.no_grad():
        for parameter in model.parameters():
            parameter.copy_(torch.randn(parameter.shape, generator=generator) * 0.1)
    return model.eval()


def _tiny_checkpoint(model: MoGe2Model) -> dict:
    state = {key: value.to(torch.float16) for key, value in model.state_dict().items()}
    state.update({key: torch.zeros(shape, dtype=torch.float16) for key, shape in _COMFY_EXTRAS.items()})
    return state


def test_the_comfy_org_moge2_checkpoint_maps_onto_the_port_key_for_key():
    shapes = _comfy_shapes()
    mapped = map_moge2_state_dict(shapes)
    assert detect_moge2_config(mapped) == MOGE2_VITL
    with torch.device("meta"):
        model = MoGe2Model(MOGE2_VITL)
    ours = {key: tuple(value.shape) for key, value in model.state_dict().items()}
    assert ours == {key: shapes[key] for key in mapped}
    dropped = sorted(set(shapes) - set(mapped))
    assert {key.split(".")[0] for key in dropped} == {"encoder", "normal_head", "scale_head"}
    assert [key for key in dropped if key.startswith("encoder.")] == [
        "encoder.backbone.mask_token", "encoder.image_mean", "encoder.image_std",
    ]


def test_the_port_reads_the_tiny_configuration_back_from_its_shapes():
    shapes = {key: tuple(value.shape) for key, value in _tiny_model().state_dict().items()}
    assert detect_moge2_config(shapes) == TINY


def test_a_tiny_fp16_checkpoint_loads_every_weight_and_predicts_like_its_source(tmp_path):
    source = _tiny_model(3)
    path = tmp_path / "moge_tiny_fp16.safetensors"
    save_file(_tiny_checkpoint(source), str(path))
    loaded = load_moge2(path)
    assert isinstance(loaded, MoGe2Model)
    assert not loaded.training
    assert not any(parameter.requires_grad for parameter in loaded.parameters())
    for key, value in source.state_dict().items():
        assert torch.equal(loaded.state_dict()[key], value.to(torch.float16).float()), key
    rounded = _tiny_model(3)
    rounded.load_state_dict({key: value.to(torch.float16).float() for key, value in source.state_dict().items()})
    image = torch.rand(1, 3, 42, 70)
    with torch.no_grad():
        expected = rounded(image, 15)
        actual = loaded(image, 15)
    assert torch.equal(expected[0], actual[0])
    assert torch.equal(expected[1], actual[1])


def test_the_loaded_dtype_is_selectable():
    model = load_moge2_state_dict(_tiny_checkpoint(_tiny_model()), dtype=torch.float16)
    assert {parameter.dtype for parameter in model.parameters()} == {torch.float16}


def test_a_missing_weight_is_named():
    state = _tiny_checkpoint(_tiny_model())
    del state["points_head.res_blocks.1.0.layers.2.weight"]
    with pytest.raises(ValueError, match="1 missing.*points_head.res_blocks.1.0.layers.2.weight"):
        load_moge2_state_dict(state)


def test_an_unknown_weight_is_named():
    state = _tiny_checkpoint(_tiny_model())
    state["neck.extra.weight"] = torch.zeros(1)
    with pytest.raises(ValueError, match="1 unexpected.*neck.extra.weight"):
        load_moge2_state_dict(state)


def test_a_moge1_file_is_refused_by_name():
    with pytest.raises(ValueError, match="MoGe-1"):
        detect_moge2_config({"backbone.cls_token": (1, 1, 1024), "head.projects.0.weight": (512, 1024, 1, 1)})


def test_a_moge3_file_is_refused_by_name():
    shapes = {key: tuple(value.shape) for key, value in _tiny_model().state_dict().items()}
    shapes["refiner.encoder_fuse.weight"] = (512, 18)
    with pytest.raises(ValueError, match="MoGe-3"):
        detect_moge2_config(shapes)


def test_a_file_that_is_not_moge_is_refused_with_the_file_to_pick():
    with pytest.raises(ValueError, match="moge_2_vitl_normal_fp16.safetensors"):
        load_moge2_state_dict({"model.diffusion_model.x": torch.zeros(1)}, name="other.safetensors")


def test_only_safetensors_files_load(tmp_path):
    with pytest.raises(ValueError, match="safetensors only"):
        load_moge2(tmp_path / "model.pt")
