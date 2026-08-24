# Vendored from Practical-RIFE — https://github.com/hzwer/Practical-RIFE
# Source file: model/warplayer.py at branch master (commit fetched 2026-07-23;
# the repo is single-branch, ~207 commits, tag-less — pin is the master tip).
# License: MIT (see LICENSE in this directory). Copyright (c) 2021 hzwer.
# Local modifications: the backward-warp grid is built on the flow tensor's own
# device instead of a module-level CUDA `device`, so warp runs on CPU inputs
# (upstream hard-coded a global `device = cuda if available else cpu`, which
# placed the cached grid on CUDA even for CPU tensors). The cached grid is also
# built/keyed in the flow tensor's dtype: this engine runs fp16 via explicit
# .half() (no autocast), so a float32 grid would promote `grid + flow` to
# float32 and grid_sample rejects the Half-input/Float-grid mix (upstream only
# avoids this under autocast, which recasts grid_sample operands). Behaviour on
# CUDA fp32 is unchanged.

import torch

backwarp_tenGrid = {}


def warp(tenInput, tenFlow):
    device = tenFlow.device
    k = (str(tenFlow.device), str(tenFlow.dtype), str(tenFlow.size()))
    if k not in backwarp_tenGrid:
        tenHorizontal = torch.linspace(-1.0, 1.0, tenFlow.shape[3], device=device).view(
            1, 1, 1, tenFlow.shape[3]).expand(tenFlow.shape[0], -1, tenFlow.shape[2], -1)
        tenVertical = torch.linspace(-1.0, 1.0, tenFlow.shape[2], device=device).view(
            1, 1, tenFlow.shape[2], 1).expand(tenFlow.shape[0], -1, -1, tenFlow.shape[3])
        backwarp_tenGrid[k] = torch.cat(
            [tenHorizontal, tenVertical], 1).to(device=device, dtype=tenFlow.dtype)

    tenFlow = torch.cat([tenFlow[:, 0:1, :, :] / ((tenInput.shape[3] - 1.0) / 2.0),
                         tenFlow[:, 1:2, :, :] / ((tenInput.shape[2] - 1.0) / 2.0)], 1)

    g = (backwarp_tenGrid[k] + tenFlow).permute(0, 2, 3, 1)
    return torch.nn.functional.grid_sample(input=tenInput, grid=g, mode='bilinear', padding_mode='border', align_corners=True)
