# Derived from: https://github.com/aigc-apps/VideoX-Fun/blob/main/videox_fun/models/z_image_transformer2d_control.py (Apache-2.0)

from __future__ import annotations

from typing import Any, Iterator

import torch
import torch.nn as nn
from torch import Tensor

from vendor.gpl.comfyui.z_image.layers import _JointTransformerBlock

from ...cancellation import raise_if_cancelled
from ...detect.patch_detect import ZIMAGE_FUN_CONTROL, detect_model_patch_config
from ...errors import NativeEngineUnsupportedError

FUN_CONTROL_PREFIX = "fun_control."

_ATTENTION_RENAMES = {
    "attention.to_out.0.weight": "attention.out.weight",
    "attention.norm_q.weight": "attention.q_norm.weight",
    "attention.norm_k.weight": "attention.k_norm.weight",
}


def control_channels(in_channels: int) -> int:
    return 2 * in_channels + 1


class ZImageFunControlBlock(_JointTransformerBlock):
    def __init__(self, cfg, operations, first: bool = False, dtype=None, device=None) -> None:
        super().__init__(cfg, operations, modulation=True, dtype=dtype, device=device)
        if first:
            self.before_proj = operations.Linear(cfg.dim, cfg.dim, bias=True, dtype=dtype, device=device)
        self.after_proj = operations.Linear(cfg.dim, cfg.dim, bias=True, dtype=dtype, device=device)


class ZImageFunControl(nn.Module):
    def __init__(self, cfg, num_layers: int, control_in_dim: int, operations, dtype=None, device=None) -> None:
        super().__init__()
        self.control_in_dim = control_in_dim
        self.x_embedder = operations.Linear(
            cfg.patch_size * cfg.patch_size * control_in_dim, cfg.dim, bias=True, dtype=dtype, device=device
        )
        self.noise_refiner = nn.ModuleList([
            ZImageFunControlBlock(cfg, operations, first=i == 0, dtype=dtype, device=device)
            for i in range(cfg.n_refiner_layers)
        ])
        self.layers = nn.ModuleList([
            ZImageFunControlBlock(cfg, operations, first=i == 0, dtype=dtype, device=device)
            for i in range(num_layers)
        ])

    def injection_layers(self, num_base_layers: int) -> list[int]:
        stride = num_base_layers // len(self.layers)
        return list(range(0, num_base_layers, stride))[:len(self.layers)]

    def refine(self, tokens: Tensor, img: Tensor, rope: Tensor, adaln_input: Tensor) -> tuple[list[Tensor], Tensor]:
        c = tokens
        hints = []
        for i, block in enumerate(self.noise_refiner):
            raise_if_cancelled(i)
            if i == 0:
                c = block.before_proj(c) + img
            c = block(c, rope, adaln_input)
            hints.append(block.after_proj(c))
        return hints, c

    def hints(self, c: Tensor, joint: Tensor, rope: Tensor, adaln_input: Tensor) -> Iterator[Tensor]:
        for i, block in enumerate(self.layers):
            if i == 0:
                c = block.before_proj(c) + joint
            c = block(c, rope, adaln_input)
            yield block.after_proj(c)


def convert_fun_control_state_dict(sd: dict[str, Tensor]) -> dict[str, Tensor]:
    out: dict[str, Tensor] = {}
    for key, value in sd.items():
        if key == "control_all_x_embedder.2-1.weight":
            out["x_embedder.weight"] = value
        elif key == "control_all_x_embedder.2-1.bias":
            out["x_embedder.bias"] = value
        elif key.startswith("control_layers."):
            out["layers." + key.removeprefix("control_layers.")] = value
        elif key.startswith("control_noise_refiner."):
            out["noise_refiner." + key.removeprefix("control_noise_refiner.")] = value
        else:
            out[key] = value
    for prefix in ("layers", "noise_refiner"):
        index = 0
        while f"{prefix}.{index}.attention.to_q.weight" in out:
            base = f"{prefix}.{index}.attention."
            out[base + "qkv.weight"] = torch.cat(
                [out.pop(base + "to_q.weight"), out.pop(base + "to_k.weight"), out.pop(base + "to_v.weight")], dim=0
            )
            for old, new in _ATTENTION_RENAMES.items():
                key = f"{prefix}.{index}.{old}"
                if key in out:
                    out[f"{prefix}.{index}.{new}"] = out.pop(key)
            index += 1
    return out


def check_fun_control_patch(patch: dict[str, Any] | None, config: dict[str, Any]) -> dict[str, Any]:
    if patch is None or patch["patch"] != ZIMAGE_FUN_CONTROL:
        raise NativeEngineUnsupportedError("the model patch is not a Z-Image Fun ControlNet")
    expected = control_channels(int(config["in_channels"])) * int(config.get("patch_size", 2)) ** 2
    if patch["embedder_in"] != expected:
        raise NativeEngineUnsupportedError(
            f"this Z-Image Fun ControlNet takes {patch['embedder_in']} control features per patch, "
            f"Union 2.1 takes {expected}; the older Union 1.0 is not supported, use a Union 2.1 file"
        )
    if not patch["refiner_controlled"] or patch["num_refiner_blocks"] != int(config["n_refiner_layers"]):
        raise NativeEngineUnsupportedError(
            "this Z-Image Fun ControlNet has no noise-refiner control blocks, use a Union 2.1 file"
        )
    if not patch["refiner_trained"]:
        raise NativeEngineUnsupportedError(
            "this is the Z-Image Fun ControlNet Union 2.0, whose noise-refiner control blocks were never "
            "trained (an upstream bug fixed in 2.1); use a Union 2.1 file"
        )
    if patch["inner_dim"] != int(config["dim"]):
        raise NativeEngineUnsupportedError(
            f"the Fun ControlNet is {patch['inner_dim']} wide, the diffusion model is {config['dim']} wide"
        )
    num_layers = int(config["n_layers"])
    if not 0 < patch["num_blocks"] <= num_layers or num_layers % patch["num_blocks"]:
        raise NativeEngineUnsupportedError(
            f"a {patch['num_blocks']}-block Fun ControlNet cannot pair with a {num_layers}-block diffusion model"
        )
    return patch


def attach_fun_control(config: dict[str, Any], sd: dict[str, Tensor],
                       patch_sd: dict[str, Tensor]) -> tuple[dict[str, Any], dict[str, Tensor]]:
    patch = check_fun_control_patch(detect_model_patch_config(patch_sd), config)
    merged = dict(sd)
    for key, value in convert_fun_control_state_dict(patch_sd).items():
        merged[FUN_CONTROL_PREFIX + key] = value
    fun_control = {
        "num_blocks": patch["num_blocks"],
        "control_in_dim": control_channels(int(config["in_channels"])),
    }
    return {**config, "fun_control": fun_control}, merged
