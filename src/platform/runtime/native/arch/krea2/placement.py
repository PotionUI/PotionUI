# Derived from: https://github.com/lbouaraba/comfyui-krea2edit (Apache-2.0, _place_refs)
from __future__ import annotations

from typing import Sequence

import torch

REF_POSITIONS = ("aligned", "beside", "bag")


def normalize_ref_position(value: object) -> str:
    text = str(value or "aligned").strip().lower()
    for mode in REF_POSITIONS:
        if text.startswith(mode):
            return mode
    raise ValueError(f"unknown ref_position {value!r}; expected one of {REF_POSITIONS}")


def ref_position_modes(count: int, ref_position: object = "aligned", ref_position_a: object = "aligned") -> tuple[str, ...]:
    if count <= 0:
        return ()
    last = normalize_ref_position(ref_position)
    first = normalize_ref_position(ref_position_a)
    return tuple([first] * (count - 1) + [last])


def place_refs(ref_ids: Sequence[torch.Tensor], modes: Sequence[str], grid_h: int, grid_w: int) -> list[torch.Tensor]:
    out: list[torch.Tensor] = []
    x0 = float(grid_w)
    for ids, mode in zip(ref_ids, modes):
        if mode == "aligned":
            out.append(ids)
            continue
        ids = ids.clone()
        if mode == "beside":
            ids[..., 2] = ids[..., 2] - ids[..., 2].min() + x0
            x0 = float(ids[..., 2].max()) + 1.0
        elif mode == "bag":
            ids[..., 1] = (grid_h - 1) / 2.0
            ids[..., 2] = (grid_w - 1) / 2.0
        else:
            raise ValueError(f"unknown ref_position {mode!r}")
        out.append(ids)
    return out
