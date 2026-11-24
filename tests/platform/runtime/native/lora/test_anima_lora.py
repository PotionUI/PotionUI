from __future__ import annotations

import pytest
import torch
import torch.nn as nn

from src.platform.runtime.native.arch.anima.model import Anima
from src.platform.runtime.native.lora import map_lora_keys
from src.platform.runtime.native.lora.key_mapping import _select_key_map
from .._native_ops import fp32_ops

TINY = {
    "image_model": "anima", "in_channels": 8, "out_channels": 8,
    "model_channels": 32, "num_blocks": 2, "num_heads": 2,
    "crossattn_emb_channels": 16, "patch_spatial": 2, "patch_temporal": 1,
    "concat_padding_mask": True, "mlp_ratio": 4.0, "use_adaln_lora": True,
    "adaln_lora_dim": 16, "max_img_h": 64, "max_img_w": 64, "max_frames": 8,
    "min_fps": 1, "max_fps": 30, "rope_h_extrapolation_ratio": 4.0,
    "rope_w_extrapolation_ratio": 4.0, "rope_t_extrapolation_ratio": 1.0,
    "rope_enable_fps_modulation": False, "llm_source_dim": 16,
    "llm_target_dim": 16, "llm_model_dim": 16, "llm_num_layers": 2,
    "llm_num_heads": 2, "llm_vocab_size": 100,
}
RANK = 4


@pytest.fixture(scope="module")
def module():
    with torch.device("meta"):
        return Anima.from_config(TINY, fp32_ops())


def _lora(stem, out_features, in_features):
    return {
        f"{stem}.lora_up.weight": torch.zeros(out_features, RANK),
        f"{stem}.lora_down.weight": torch.zeros(RANK, in_features),
        f"{stem}.alpha": torch.tensor(float(RANK)),
    }


def _linears(module):
    return {name: sub for name, sub in module.named_modules() if isinstance(sub, nn.Linear) and name}


def test_every_linear_of_the_dit_and_its_adapter_is_reachable_by_its_native_name(module):
    key_map = _select_key_map(module)
    missing = [
        name for name in _linears(module)
        if key_map.get(f"diffusion_model.{name}") != (f"{name}.weight", None)
    ]
    assert missing == []


@pytest.mark.parametrize("dialect", [
    "diffusion_model.{name}",
    "lora_unet_{kohya}",
    "base_model.model.{name}",
    "{name}",
])
def test_each_trainer_dialect_lands_on_the_same_parameters(module, dialect):
    linears = _linears(module)
    names = [
        "blocks.1.self_attn.q_proj", "blocks.0.cross_attn.output_proj", "blocks.1.mlp.layer1",
        "blocks.0.adaln_modulation_mlp.2", "final_layer.linear", "llm_adapter.blocks.0.self_attn.o_proj",
    ]
    sd = {}
    for name in names:
        stem = dialect.format(name=name, kohya=name.replace(".", "_"))
        sd.update(_lora(stem, linears[name].out_features, linears[name].in_features))
    mapped, unmatched = map_lora_keys(sd, module)
    assert unmatched == []
    assert set(mapped) == {f"{name}.weight" for name in names}


def test_a_lora_aimed_at_the_wrong_width_is_reported_unmatched(module):
    linears = _linears(module)
    name = "blocks.0.self_attn.q_proj"
    sd = _lora(f"diffusion_model.{name}", linears[name].out_features, linears[name].in_features + 1)
    mapped, unmatched = map_lora_keys(sd, module)
    assert mapped == {} and len(unmatched) == 1
