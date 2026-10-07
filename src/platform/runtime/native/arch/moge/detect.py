from __future__ import annotations

import math
from typing import Mapping, Sequence, Tuple

from .config import BILINEAR, CONV_TRANSPOSE, MoGe2Config

__all__ = ["detect_moge2_config", "is_moge2_checkpoint"]

_BACKBONE = "encoder.backbone."
_PROBE = _BACKBONE + "cls_token"


def is_moge2_checkpoint(keys) -> bool:
    keys = set(keys)
    return _PROBE in keys and any(key.startswith("points_head.") for key in keys)


def _refuse(reason: str) -> ValueError:
    return ValueError(f"not a MoGe-2 checkpoint this port can load: {reason}")


def _indices(shapes: Mapping[str, Sequence[int]], prefix: str) -> list:
    return sorted({int(key[len(prefix):].split(".", 1)[0]) for key in shapes if key.startswith(prefix)})


def _stack(shapes: Mapping[str, Sequence[int]], prefix: str) -> Tuple[list, list, list, list]:
    levels = _indices(shapes, prefix + "input_blocks.")
    if levels != list(range(len(levels))) or not levels:
        raise _refuse(f"{prefix} has no contiguous input blocks")
    dims = [int(shapes[f"{prefix}input_blocks.{level}.weight"][0]) for level in levels]
    dim_in = [int(shapes[f"{prefix}input_blocks.{level}.weight"][1]) for level in levels]
    blocks = [len(_indices(shapes, f"{prefix}res_blocks.{level}.")) for level in levels]
    resamplers = [
        CONV_TRANSPOSE if f"{prefix}resamplers.{level}.0.weight" in shapes else BILINEAR for level in levels[:-1]
    ]
    if any(key.startswith(prefix + "res_blocks.") and ".layers.0." in key for key in shapes):
        raise _refuse(f"{prefix} residual blocks carry normalisation layers")
    return dims, dim_in, blocks, resamplers


def detect_moge2_config(shapes: Mapping[str, Sequence[int]]) -> MoGe2Config:
    if _PROBE not in shapes:
        if any(key.startswith("backbone.") for key in shapes):
            raise _refuse("this is a MoGe-1 file (backbone.* keys); Pixal3D uses MoGe-2")
        raise _refuse(f"no {_PROBE} key")
    if any(key.startswith("refiner.") for key in shapes):
        raise _refuse("this is a MoGe-3 file (refiner.* keys); Pixal3D uses MoGe-2")
    if any(".mlp.w12." in key for key in shapes) or _BACKBONE + "register_tokens" in shapes:
        raise _refuse("the DINOv2 backbone is not the plain ViT-L/14 MoGe-2 ships with")
    for head in ("neck.", "points_head.", "mask_head."):
        if not any(key.startswith(head) for key in shapes):
            raise _refuse(f"no {head[:-1]} weights")

    embed_dim = int(shapes[_PROBE][-1])
    depth = len(_indices(shapes, _BACKBONE + "blocks."))
    positions = int(shapes[_BACKBONE + "pos_embed"][1]) - 1
    pos_grid = math.isqrt(positions)
    if pos_grid * pos_grid != positions:
        raise _refuse(f"position embedding has {positions} patches, not a square grid")
    projections = len(_indices(shapes, "encoder.output_projections."))
    if projections == 0 or depth % projections:
        raise _refuse(f"{projections} output projections over {depth} blocks")

    neck_dims, neck_in, neck_blocks, resamplers = _stack(shapes, "neck.")
    head_dims, head_in, head_blocks, head_resamplers = _stack(shapes, "points_head.")
    mask_dims, _mask_in, mask_blocks, mask_resamplers = _stack(shapes, "mask_head.")
    projection_dim = int(shapes["encoder.output_projections.0.weight"][0])
    if neck_in != [projection_dim + 2, *([2] * (len(neck_dims) - 1))]:
        raise _refuse(f"neck inputs {neck_in} do not take the encoder features plus view-plane UVs")
    if not (head_dims == mask_dims == neck_dims and head_in == neck_dims):
        raise _refuse("the points and mask heads do not mirror the neck")
    if not (head_blocks == mask_blocks and resamplers == head_resamplers == mask_resamplers):
        raise _refuse("the points and mask heads differ in shape")
    if int(shapes[f"points_head.output_blocks.{len(neck_dims) - 1}.weight"][0]) != 3:
        raise _refuse("the points head does not predict three channels")

    step = depth // projections
    return MoGe2Config(
        embed_dim=embed_dim,
        depth=depth,
        num_heads=embed_dim // 64,
        mlp_hidden=int(shapes[_BACKBONE + "blocks.0.mlp.fc1.weight"][0]),
        patch_size=int(shapes[_BACKBONE + "patch_embed.proj.weight"][-1]),
        pos_grid=pos_grid,
        intermediate_layers=tuple(step * (index + 1) - 1 for index in range(projections)),
        projection_dim=projection_dim,
        neck_dims=tuple(neck_dims),
        neck_blocks=tuple(neck_blocks),
        head_blocks=tuple(head_blocks),
        resamplers=tuple(resamplers),
    )
