from __future__ import annotations

import logging

import torch

from ..io.state_dict_utils import count_blocks

logger = logging.getLogger(__name__)

QWEN_IMAGE21_FUN_CONTROL = "qwen_image21_fun_control"


def _is_qwen_image21_fun_control(sd: dict[str, torch.Tensor]) -> bool:
    return (
        "control_img_in.weight" in sd
        and "control_blocks.0.img_mlp.out.weight" in sd
        and "control_blocks.0.after_proj.weight" in sd
        and "control_blocks.0.attn.norm_q.weight" in sd
        and "control_blocks.0.img_mod.1.weight" not in sd
    )


def detect_model_patch_config(sd: dict[str, torch.Tensor]) -> dict | None:
    if not _is_qwen_image21_fun_control(sd):
        return None
    inner_dim = int(sd["control_img_in.weight"].shape[0])
    fused_mlp = "control_blocks.0.img_mlp.gate_up.weight" in sd
    if fused_mlp:
        mlp_hidden = int(sd["control_blocks.0.img_mlp.gate_up.weight"].shape[0]) // 2
    else:
        mlp_hidden = int(sd["control_blocks.0.img_mlp.gate_layer.weight"].shape[0])
    config = {
        "patch": QWEN_IMAGE21_FUN_CONTROL,
        "num_blocks": count_blocks(sd, "control_blocks.{}."),
        "control_in_dim": int(sd["control_img_in.weight"].shape[1]),
        "inner_dim": inner_dim,
        "attention_head_dim": int(sd["control_blocks.0.attn.norm_q.weight"].shape[0]),
        "mlp_ratio": round(mlp_hidden / inner_dim),
        "fused_mlp": fused_mlp,
    }
    logger.debug(
        "detected qwen_image21 fun control patch: blocks=%d in=%d inner=%d headdim=%d fused_mlp=%s",
        config["num_blocks"], config["control_in_dim"], inner_dim, config["attention_head_dim"], fused_mlp,
    )
    return config
