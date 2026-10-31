import json
from pathlib import Path
from unittest.mock import Mock

import pytest
import torch
from safetensors.torch import save_file

from src.pipelines.contracts import PipeInput
from src.pipelines.outputs import GenerationExecutionError
from src.pipelines.pipes.controlnet_loader.sdxl import main as loader
from src.pipelines.pipes.controlnet_loader.sdxl.main import (
    ASSETS,
    LARGE_MARKER_KEY,
    LDM_LARGE_MARKER_KEY,
    LDM_MID_MARKER_KEY,
    LDM_PREFIX,
    LDM_SDXL_MARKER_KEY,
    MID_MARKER_KEY,
    SDXL_MARKER_KEY,
    ControlNetLoaderSDXLPipe,
    controlnet_config_dir,
    controlnet_layout_folder,
)


def _write(path: Path, keys):
    save_file({key: torch.zeros(1) for key in keys}, str(path))
    return str(path)


def _pipe(file_path, dtype="float32"):
    return ControlNetLoaderSDXLPipe(
        {
            "controlnets": [
                {"file_path": file_path, "name": "cn", "type": "openpose", "enabled": True}
            ],
            "device": "cpu",
            "dtype": dtype,
        }
    )


def _tiny_config():
    config = json.loads((ASSETS / "large" / "config.json").read_text())
    config.update(
        block_out_channels=[32, 64, 128],
        attention_head_dim=[1, 2, 4],
        transformer_layers_per_block=[1, 1, 1],
        conditioning_embedding_out_channels=[4, 8],
        cross_attention_dim=2048,
        norm_num_groups=8,
        projection_class_embeddings_input_dim=2816,
        addition_time_embed_dim=32,
        layers_per_block=1,
    )
    return config


@pytest.mark.parametrize(
    ("keys", "folder"),
    [
        ([SDXL_MARKER_KEY, LARGE_MARKER_KEY], "large"),
        ([SDXL_MARKER_KEY, MID_MARKER_KEY], "mid"),
        ([SDXL_MARKER_KEY], "small"),
    ],
)
def test_config_dir_follows_checkpoint_layout(tmp_path, keys, folder):
    path = _write(tmp_path / "cn.safetensors", keys)
    assert Path(controlnet_config_dir(path)) == ASSETS / folder


@pytest.mark.parametrize("prefix", ["", LDM_PREFIX])
@pytest.mark.parametrize(
    ("keys", "folder"),
    [
        ([LDM_SDXL_MARKER_KEY, LDM_LARGE_MARKER_KEY], "large"),
        ([LDM_SDXL_MARKER_KEY, LDM_MID_MARKER_KEY], "mid"),
        ([LDM_SDXL_MARKER_KEY], "small"),
    ],
)
def test_ldm_layout_checkpoint_selects_config(tmp_path, prefix, keys, folder):
    path = _write(tmp_path / "cn.safetensors", [prefix + key for key in keys])
    assert Path(controlnet_config_dir(path)) == ASSETS / folder


@pytest.mark.parametrize("folder", ["large", "mid", "small"])
def test_shipped_config_builds_the_layout_its_markers_select(folder):
    config = json.loads((ASSETS / folder / "config.json").read_text())
    with torch.device("meta"):
        model = diffusers_module().ControlNetModel.from_config(config)
    keys = list(model.state_dict().keys())
    assert SDXL_MARKER_KEY in keys
    assert controlnet_layout_folder(keys) == folder


def diffusers_module():
    import diffusers

    return diffusers


def test_sd15_checkpoint_is_rejected(tmp_path):
    path = _write(tmp_path / "cn.safetensors", ["controlnet_cond_embedding.conv_in.weight"])
    with pytest.raises(ValueError, match="not an SDXL ControlNet"):
        controlnet_config_dir(path)


@pytest.mark.parametrize("folder", ["large", "mid", "small"])
def test_shipped_configs_are_complete_controlnet_configs(folder):
    config = json.loads((ASSETS / folder / "config.json").read_text())
    assert config["_class_name"] == "ControlNetModel"
    assert config["cross_attention_dim"] == 2048
    assert config["addition_embed_type"] == "text_time"


def test_load_passes_local_config_and_stays_offline(tmp_path, monkeypatch):
    import diffusers

    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    path = _write(tmp_path / "cn.safetensors", [SDXL_MARKER_KEY, LARGE_MARKER_KEY])
    seen = {}

    def from_single_file(file_path, **kwargs):
        seen.update(kwargs)
        model = Mock()
        model.config.cross_attention_dim = 2048
        model.to.return_value = model
        return model

    monkeypatch.setattr(diffusers.ControlNetModel, "from_single_file", from_single_file)
    result = _pipe(path).process(PipeInput(input={}), Mock())
    assert len(result.output["controlnet"]) == 1
    assert Path(seen["config"]) == ASSETS / "large"
    assert (Path(seen["config"]) / "config.json").is_file()
    assert seen["local_files_only"] is True


def test_real_load_makes_no_hub_call(tmp_path, monkeypatch):
    import diffusers
    import huggingface_hub

    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    calls = []

    def blocked(*args, **kwargs):
        calls.append(args)
        raise AssertionError("hub access attempted")

    monkeypatch.setattr(huggingface_hub, "hf_hub_download", blocked)
    monkeypatch.setattr(huggingface_hub, "snapshot_download", blocked)
    monkeypatch.setattr(huggingface_hub, "model_info", blocked)

    config = _tiny_config()
    model = diffusers.ControlNetModel.from_config(config)
    state = {key: value.clone() for key, value in model.state_dict().items()}
    path = tmp_path / "tiny.safetensors"
    save_file(state, str(path))

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "config.json").write_text(json.dumps(config))

    monkeypatch.setattr(loader, "controlnet_config_dir", lambda file_path: str(config_dir))
    result = _pipe(str(path)).process(PipeInput(input={}), Mock())
    loaded = result.output["controlnet"][0]["model"]
    assert calls == []
    assert loaded.config.cross_attention_dim == 2048
    for key, value in loaded.state_dict().items():
        assert torch.equal(value, state[key])


def test_missing_file_raises_naming_the_file(tmp_path):
    missing = str(tmp_path / "gone.safetensors")
    with pytest.raises(GenerationExecutionError, match="gone.safetensors"):
        _pipe(missing).process(PipeInput(input={}), Mock())


def test_failed_load_raises_instead_of_falling_back(tmp_path):
    path = _write(tmp_path / "broken.safetensors", ["unrelated.weight"])
    with pytest.raises(GenerationExecutionError, match="broken.safetensors"):
        _pipe(path).process(PipeInput(input={}), Mock())


def test_blank_path_is_still_skipped():
    pipe = _pipe("")
    assert pipe.process(PipeInput(input={}), Mock()).output["controlnet"] == []
