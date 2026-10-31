from __future__ import annotations

import logging

import torch

from ..io.state_dict_utils import count_blocks

logger = logging.getLogger(__name__)

QWEN_IMAGE21_FUN_CONTROL = "qwen_image21_fun_control"
ZIMAGE_FUN_CONTROL = "z_image_fun_control"


def _is_qwen_image21_fun_control(sd: dict[str, torch.Tensor]) -> bool:
    return (
        "control_img_in.weight" in sd
        and "control_blocks.0.img_mlp.out.weight" in sd
        and "control_blocks.0.after_proj.weight" in sd
        and "control_blocks.0.attn.norm_q.weight" in sd
        and "control_blocks.0.img_mod.1.weight" not in sd
    )


def _is_z_image_fun_control(sd: dict[str, torch.Tensor]) -> bool:
    return (
        "control_all_x_embedder.2-1.weight" in sd
        and "control_layers.0.before_proj.weight" in sd
        and "control_layers.0.after_proj.weight" in sd
        and ("control_layers.0.attention.to_q.weight" in sd or "control_layers.0.attention.qkv.weight" in sd)
    )


def _any_nonzero(sd: dict[str, torch.Tensor], keys: list[str]) -> bool:
    return any(bool(sd[key].any()) for key in keys if key in sd)


def _detect_z_image_fun_control(sd: dict[str, torch.Tensor]) -> dict:
    embedder = sd["control_all_x_embedder.2-1.weight"]
    refiner_blocks = count_blocks(sd, "control_noise_refiner.{}.")
    refiner_keys = [
        f"control_noise_refiner.{i}.{name}.{kind}"
        for i in range(refiner_blocks)
        for name in ("after_proj", "before_proj")
        for kind in ("weight", "bias")
    ]
    config = {
        "patch": ZIMAGE_FUN_CONTROL,
        "num_blocks": count_blocks(sd, "control_layers.{}."),
        "num_refiner_blocks": refiner_blocks,
        "inner_dim": int(embedder.shape[0]),
        "embedder_in": int(embedder.shape[1]),
        "refiner_controlled": refiner_blocks > 0 and all(
            f"control_noise_refiner.{i}.after_proj.weight" in sd for i in range(refiner_blocks)
        ),
        "refiner_trained": _any_nonzero(sd, refiner_keys),
        "fused_qkv": "control_layers.0.attention.qkv.weight" in sd,
    }
    logger.debug(
        "detected z_image fun control patch: blocks=%d refiner=%d in=%d inner=%d trained_refiner=%s",
        config["num_blocks"], refiner_blocks, config["embedder_in"], config["inner_dim"], config["refiner_trained"],
    )
    return config


def detect_model_patch_config(sd: dict[str, torch.Tensor]) -> dict | None:
    if _is_z_image_fun_control(sd):
        return _detect_z_image_fun_control(sd)
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
