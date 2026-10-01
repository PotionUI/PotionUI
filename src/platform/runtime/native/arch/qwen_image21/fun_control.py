from __future__ import annotations

from typing import Any

import torch
from torch import Tensor

from ...detect.patch_detect import QWEN_IMAGE21_FUN_CONTROL, detect_model_patch_config
from ...errors import NativeEngineUnsupportedError

FUN_CONTROL_PREFIX = "fun_control."


def convert_fun_control_state_dict(sd: dict[str, Tensor]) -> dict[str, Tensor]:
    out = dict(sd)
    i = 0
    while f"control_blocks.{i}.img_mlp.gate_layer.weight" in sd:
        prefix = f"control_blocks.{i}.img_mlp."
        gate = out.pop(prefix + "gate_layer.weight")
        up = out.pop(prefix + "proj.weight")
        out[prefix + "gate_up.weight"] = torch.cat([gate, up], dim=0)
        i += 1
    return out


def attach_fun_control(config: dict[str, Any], sd: dict[str, Tensor],
                       patch_sd: dict[str, Tensor]) -> tuple[dict[str, Any], dict[str, Tensor]]:
    patch = detect_model_patch_config(patch_sd)
    if patch is None or patch["patch"] != QWEN_IMAGE21_FUN_CONTROL:
        raise NativeEngineUnsupportedError("the model patch is not a Qwen-Image-2.1 Fun ControlNet")
    if patch["inner_dim"] != config["inner_dim"] or patch["attention_head_dim"] != config["attention_head_dim"]:
        raise NativeEngineUnsupportedError(
            f"the Fun ControlNet is {patch['inner_dim']} wide with {patch['attention_head_dim']}-dim heads, "
            f"the diffusion model is {config['inner_dim']} wide with {config['attention_head_dim']}-dim heads"
        )
    num_layers = int(config["num_layers"])
    if not 0 < patch["num_blocks"] <= num_layers or num_layers % patch["num_blocks"]:
        raise NativeEngineUnsupportedError(
            f"a {patch['num_blocks']}-block Fun ControlNet cannot pair with a {num_layers}-block diffusion model"
        )
    merged = dict(sd)
    for key, value in convert_fun_control_state_dict(patch_sd).items():
        merged[FUN_CONTROL_PREFIX + key] = value
    fun_control = {
        "num_blocks": patch["num_blocks"],
        "control_in_dim": patch["control_in_dim"],
        "mlp_ratio": patch["mlp_ratio"],
    }
    return {**config, "fun_control": fun_control}, merged
