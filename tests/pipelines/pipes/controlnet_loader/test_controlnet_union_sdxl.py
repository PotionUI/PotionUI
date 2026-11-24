import json
from unittest.mock import Mock

import pytest
import torch
from safetensors.torch import load_file, save_file

from src.pipelines.contracts import PipeInput
from src.pipelines.outputs import GenerationExecutionError
from src.pipelines.pipes.controlnet_loader.sdxl import union
from src.pipelines.pipes.controlnet_loader.sdxl.main import (
    ASSETS,
    LARGE_MARKER_KEY,
    SDXL_MARKER_KEY,
    ControlNetLoaderSDXLPipe,
)
from src.pipelines.pipes.controlnet_loader.sdxl.union import (
    UNION_CONTROL_TYPES,
    is_union_controlnet,
    union_config,
    union_control_index,
)

COND_CHANNELS = [4, 8, 16, 32]


def _tiny_config(num_control_type):
    config = json.loads((ASSETS / "large" / "config.json").read_text())
    config.update(
        _class_name="ControlNetUnionModel",
        block_out_channels=[32, 64, 128],
        attention_head_dim=[1, 2, 4],
        transformer_layers_per_block=[1, 1, 1],
        conditioning_embedding_out_channels=COND_CHANNELS,
        norm_num_groups=8,
        addition_time_embed_dim=32,
        projection_class_embeddings_input_dim=1472,
        layers_per_block=1,
        num_control_type=num_control_type,
        num_trans_channel=32,
        num_proj_channel=32,
        num_trans_head=2,
        num_trans_layer=2,
    )
    return config


@pytest.fixture
def tiny_union(tmp_path, monkeypatch):
    def build(num_control_type=8):
        from diffusers import ControlNetUnionModel

        config = _tiny_config(num_control_type)
        base = tmp_path / "base.json"
        base.write_text(json.dumps({**config, "num_control_type": 6, "num_trans_layer": 1}))
        monkeypatch.setattr(union, "BASE_CONFIG", base)
        model = ControlNetUnionModel.from_config(config)
        path = tmp_path / f"union{num_control_type}.safetensors"
        save_file({k: v.contiguous() for k, v in model.state_dict().items()}, str(path))
        return str(path), model

    return build


def _shapes(model):
    return {key: tuple(value.shape) for key, value in model.state_dict().items()}


def _loader(path, control_type):
    return ControlNetLoaderSDXLPipe(
        {"controlnets": [{"file_path": path, "name": "u", "type": control_type, "enabled": True}],
         "device": "cpu", "dtype": "float32"}
    )


def test_union_checkpoint_is_detected_from_its_keys(tiny_union):
    _, model = tiny_union()
    assert is_union_controlnet(model.state_dict().keys())


def test_a_plain_controlnet_is_not_a_union():
    assert not is_union_controlnet([SDXL_MARKER_KEY, LARGE_MARKER_KEY, "controlnet_cond_embedding.conv_in.weight"])


def test_partial_union_markers_are_not_enough():
    assert not is_union_controlnet(["task_embedding", "control_add_embedding.linear_1.weight"])


@pytest.mark.parametrize(
    ("name", "index"),
    [
        ("openpose", 0), ("depth", 1), ("hed", 2), ("scribble", 2), ("canny", 3), ("lineart", 3),
        ("mlsd", 3), ("normal", 4), ("segment", 5), ("tile", 6), ("repaint", 7),
    ],
)
def test_control_types_follow_the_model_card(name, index):
    assert union_control_index(name, 8) == index


def test_control_type_lookup_ignores_case_and_space():
    assert union_control_index(" Depth ", 8) == 1


def test_unknown_control_type_is_rejected_with_the_choices():
    with pytest.raises(ValueError, match="openpose"):
        union_control_index("sepia", 8)


@pytest.mark.parametrize("name", ["tile", "repaint"])
def test_promax_only_types_are_rejected_on_the_six_type_model(name):
    with pytest.raises(ValueError, match="ProMax"):
        union_control_index(name, 6)


def test_every_mapped_index_fits_the_promax_head():
    assert max(UNION_CONTROL_TYPES.values()) == 7


def test_config_is_inferred_from_the_state_dict_shapes(tiny_union):
    _, model = tiny_union(8)
    config = union_config(_shapes(model))
    assert config["num_control_type"] == 8
    assert config["num_trans_channel"] == 32
    assert config["num_proj_channel"] == 32
    assert config["num_trans_layer"] == 2
    assert config["conditioning_embedding_out_channels"] == COND_CHANNELS


def test_six_type_model_infers_six(tiny_union):
    _, model = tiny_union(6)
    assert union_config(_shapes(model))["num_control_type"] == 6


def test_inferred_config_rebuilds_the_exact_state_dict_layout(tiny_union):
    from diffusers import ControlNetUnionModel

    _, model = tiny_union(8)
    with torch.device("meta"):
        rebuilt = ControlNetUnionModel.from_config(union_config(_shapes(model), base=_tiny_config(8)))
    assert _shapes(rebuilt) == _shapes(model)


def test_loader_returns_the_union_model_with_the_chosen_control_index(tiny_union):
    path, _ = tiny_union(8)
    loaded = _loader(path, "depth").process(PipeInput(input={}), Mock()).output["controlnet"]
    assert len(loaded) == 1
    assert loaded[0]["union"] is True
    assert loaded[0]["control_type_idx"] == 1
    assert type(loaded[0]["model"]).__name__ == "ControlNetUnionModel"
    assert loaded[0]["model"].config.num_control_type == 8


def test_loader_restores_the_saved_weights(tiny_union):
    path, model = tiny_union(8)
    loaded = _loader(path, "canny").process(PipeInput(input={}), Mock()).output["controlnet"][0]["model"]
    assert torch.equal(loaded.task_embedding, model.task_embedding)
    assert torch.equal(loaded.control_add_embedding.linear_1.weight, model.control_add_embedding.linear_1.weight)


def test_loader_rejects_tile_on_a_six_type_union(tiny_union):
    path, _ = tiny_union(6)
    with pytest.raises(GenerationExecutionError, match="ProMax"):
        _loader(path, "tile").process(PipeInput(input={}), Mock())


def test_loader_reports_a_truncated_union_file(tiny_union, tmp_path):
    path, _ = tiny_union(8)
    state = load_file(path)
    state.pop("mid_block.resnets.0.norm1.weight")
    broken = tmp_path / "broken.safetensors"
    save_file(state, str(broken))
    with pytest.raises(GenerationExecutionError, match="missing weights"):
        _loader(str(broken), "depth").process(PipeInput(input={}), Mock())
