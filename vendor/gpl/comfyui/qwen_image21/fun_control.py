# Derived from: comfy/ldm/qwen_image21/model.py QwenImage21FunControl + comfy_extras/nodes_model_patch.py QwenImage21FunControlPatch (ComfyUI PR #16519, GPL-3.0); skip math cross-checked against VideoX-Fun videox_fun/models/qwenimage21_transformer2d_control.py (Apache-2.0).

from __future__ import annotations

import torch
import torch.nn as nn
from torch import Tensor

from vendor.gpl.comfyui.qwen_image21.layers import _Block


class QwenImage21FunControlBlock(_Block):
    def __init__(self, dim: int, heads: int, head_dim: int, operations, mlp_ratio: int = 3, first: bool = False,
                 dtype=None, device=None):
        super().__init__(dim, heads, head_dim, operations, mlp_ratio=mlp_ratio, dtype=dtype, device=device)
        if first:
            self.before_proj = operations.Linear(dim, dim, dtype=dtype, device=device)
        self.after_proj = operations.Linear(dim, dim, dtype=dtype, device=device)


class QwenImage21FunControl(nn.Module):
    def __init__(self, num_blocks: int, control_in_dim: int, inner_dim: int, heads: int, head_dim: int,
                 operations, mlp_ratio: int = 3, dtype=None, device=None):
        super().__init__()
        self.control_img_in = operations.Linear(control_in_dim, inner_dim, dtype=dtype, device=device)
        self.control_blocks = nn.ModuleList([
            QwenImage21FunControlBlock(inner_dim, heads, head_dim, operations, mlp_ratio=mlp_ratio, first=i == 0,
                                       dtype=dtype, device=device)
            for i in range(num_blocks)
        ])

    def injection_layers(self, num_base_blocks: int) -> list[int]:
        stride = num_base_blocks // len(self.control_blocks)
        return list(range(0, num_base_blocks, stride))[:len(self.control_blocks)]

    def init_stream(self, x: Tensor, control: Tensor, prefix_len: int) -> Tensor:
        joint = torch.zeros_like(x)
        joint[:, prefix_len:] = self.control_img_in(control).to(x.dtype)
        return self.control_blocks[0].before_proj(joint) + x

    def step(self, index: int, c: Tensor, mod, pe: Tensor, mask: Tensor | None, prefix_len: int,
             target_key_mask: Tensor | None) -> tuple[Tensor, Tensor]:
        block = self.control_blocks[index]
        c = block(c, mod, pe, mask, prefix_len, target_key_mask)
        return c, block.after_proj(c)
