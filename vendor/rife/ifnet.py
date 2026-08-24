# Architecture of hzwer's RIFE 4.x IFNet (Practical-RIFE — MIT, Copyright (c)
# 2021 hzwer; see LICENSE in this directory).
#
# PROVENANCE NOTE. The RIFE 4.x network definition (`IFNet_HDv3.py` /
# `RIFE_HDv3.py`) is NOT committed to the Practical-RIFE git tree — only
# `model/warplayer.py` (vendored verbatim as `warp.py`) and `model/loss.py`
# are. hzwer distributes the network definition inside the downloadable
# `train_log/` bundle (the `*.py` files shipped next to `flownet.pkl`), which is
# the author's own MIT-licensed code. This module follows those bundles: the
# v4.6 and v4.9 `IFNet_HDv3.py` were read from mirrors of the author's own
# bundles (checkpoint hashes recorded in NOTICE.md) and the module names/shapes
# here match them, so the published checkpoints load by their own key names.
# Per-block channel widths and the presence of the feature encoder are read FROM
# the checkpoint (see loader.py) rather than hard-coded. GPL RIFE forks
# (ComfyUI-Frame-Interpolation, vs-mlrt, ...) were NOT consulted.
#
# The two generations differ in two ways that are NOT visible in the state dict
# key names, so both are encoded here explicitly:
#   * rife46 has no feature encoder; rife47-49 do.
#   * rife46 accumulates each block's mask residual (`mask = mask + m`);
#     rife47-49 replace it (`mask = m`). Only the flow accumulates in both.

from __future__ import annotations

from typing import List, Optional

import torch
import torch.nn as nn
import torch.nn.functional as F

from .warp import warp


def conv(in_planes, out_planes, kernel_size=3, stride=1, padding=1, dilation=1):
    return nn.Sequential(
        nn.Conv2d(in_planes, out_planes, kernel_size=kernel_size, stride=stride,
                  padding=padding, dilation=dilation, bias=True),
        nn.LeakyReLU(0.2, True),
    )


class ResConv(nn.Module):
    def __init__(self, c, dilation=1):
        super().__init__()
        self.conv = nn.Conv2d(c, c, 3, 1, dilation, dilation=dilation, groups=1)
        self.beta = nn.Parameter(torch.ones((1, c, 1, 1)), requires_grad=True)
        self.relu = nn.LeakyReLU(0.2, True)

    def forward(self, x):
        return self.relu(self.conv(x) * self.beta + x)


class IFBlock(nn.Module):
    def __init__(self, in_planes, c=64):
        super().__init__()
        self.conv0 = nn.Sequential(
            conv(in_planes, c // 2, 3, 2, 1),
            conv(c // 2, c, 3, 2, 1),
        )
        self.convblock = nn.Sequential(*[ResConv(c) for _ in range(8)])
        self.lastconv = nn.Sequential(
            nn.ConvTranspose2d(c, 4 * 6, 4, 2, 1),
            nn.PixelShuffle(2),
        )

    def forward(self, x, flow=None, scale=1):
        x = F.interpolate(x, scale_factor=1.0 / scale, mode="bilinear", align_corners=False)
        if flow is not None:
            flow = F.interpolate(flow, scale_factor=1.0 / scale, mode="bilinear",
                                 align_corners=False) * (1.0 / scale)
            x = torch.cat((x, flow), 1)
        feat = self.conv0(x)
        feat = self.convblock(feat)
        tmp = self.lastconv(feat)
        tmp = F.interpolate(tmp, scale_factor=scale, mode="bilinear", align_corners=False)
        flow = tmp[:, :4] * scale
        mask = tmp[:, 4:5]
        return flow, mask


def encoder(mid_ch: int, out_ch: int) -> nn.Sequential:
    """The RIFE 4.7+ per-image feature encoder (absent in rife46): one stride-2
    conv straight into one stride-2 transpose, with no activation between them
    — that is upstream's `nn.Sequential`, and it is why the published weights
    spell these two layers `encode.0` and `encode.1`. ``mid_ch``/``out_ch`` are
    read from the checkpoint; the shipped 4.7/4.9 weights use 16 and 4."""
    return nn.Sequential(
        nn.Conv2d(3, mid_ch, 3, 2, 1),
        nn.ConvTranspose2d(mid_ch, out_ch, 4, 2, 1),
    )


class IFNet(nn.Module):
    """RIFE 4.x flow network. Built from per-block ``(in_planes, c)`` specs and an
    optional encoder spec so one class covers the rife46 (no encoder) and
    rife47-49 (feature-encoder) variants; loader.py derives the specs from a
    checkpoint's state dict."""

    def __init__(self, block_specs: List[tuple], encode_spec: Optional[tuple] = None):
        super().__init__()
        self.num_blocks = len(block_specs)
        for i, (in_planes, c) in enumerate(block_specs):
            setattr(self, f"block{i}", IFBlock(in_planes, c=c))
        self.encode = encoder(*encode_spec) if encode_spec is not None else None
        self.mask_is_residual = self.encode is None

    def forward(self, x, timestep=0.5, scale_list=(8, 4, 2, 1)):
        channel = x.shape[1] // 2
        img0 = x[:, :channel]
        img1 = x[:, channel:]
        if not torch.is_tensor(timestep):
            timestep = (x[:, :1].clone() * 0 + 1) * timestep
        else:
            timestep = timestep.repeat(1, 1, img0.shape[2], img0.shape[3])

        f0 = self.encode(img0[:, :3]) if self.encode is not None else None
        f1 = self.encode(img1[:, :3]) if self.encode is not None else None

        blocks = [getattr(self, f"block{i}") for i in range(self.num_blocks)]
        flow = None
        mask = None
        warped_img0 = img0
        warped_img1 = img1
        for i, block in enumerate(blocks):
            if flow is None:
                if self.encode is not None:
                    inp = torch.cat((img0[:, :3], img1[:, :3], f0, f1, timestep), 1)
                else:
                    inp = torch.cat((img0[:, :3], img1[:, :3], timestep), 1)
                flow, mask = block(inp, None, scale=scale_list[i])
            else:
                if self.encode is not None:
                    wf0 = warp(f0, flow[:, :2])
                    wf1 = warp(f1, flow[:, 2:4])
                    inp = torch.cat((warped_img0[:, :3], warped_img1[:, :3], wf0, wf1, timestep, mask), 1)
                else:
                    inp = torch.cat((warped_img0[:, :3], warped_img1[:, :3], timestep, mask), 1)
                f_d, m_d = block(inp, flow, scale=scale_list[i])
                flow = flow + f_d
                mask = (mask + m_d) if self.mask_is_residual else m_d
            warped_img0 = warp(img0, flow[:, :2])
            warped_img1 = warp(img1, flow[:, 2:4])

        mask = torch.sigmoid(mask)
        merged = warped_img0 * mask + warped_img1 * (1 - mask)
        return flow, mask, merged
