from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

import torch
import torch.nn as nn
import torch.nn.functional as F

from .ifnet import ResConv, conv
from .warp import warp

BLOCK_CHANNELS = 13


class Head(nn.Module):
    def __init__(self, mid_ch: int = 16, out_ch: int = 4):
        super().__init__()
        self.cnn0 = nn.Conv2d(3, mid_ch, 3, 2, 1)
        self.cnn1 = nn.Conv2d(mid_ch, mid_ch, 3, 1, 1)
        self.cnn2 = nn.Conv2d(mid_ch, mid_ch, 3, 1, 1)
        self.cnn3 = nn.ConvTranspose2d(mid_ch, out_ch, 4, 2, 1)
        self.relu = nn.LeakyReLU(0.2, True)

    def forward(self, x):
        x = self.relu(self.cnn0(x))
        x = self.relu(self.cnn1(x))
        x = self.relu(self.cnn2(x))
        return self.cnn3(x)


class HeadIFBlock(nn.Module):
    def __init__(self, in_planes: int, c: int = 64):
        super().__init__()
        self.conv0 = nn.Sequential(
            conv(in_planes, c // 2, 3, 2, 1),
            conv(c // 2, c, 3, 2, 1),
        )
        self.convblock = nn.Sequential(*[ResConv(c) for _ in range(8)])
        self.lastconv = nn.Sequential(
            nn.ConvTranspose2d(c, 4 * BLOCK_CHANNELS, 4, 2, 1),
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
        return tmp[:, :4] * scale, tmp[:, 4:5], tmp[:, 5:]


class HeadIFNet(nn.Module):
    mask_is_residual = False

    def __init__(self, block_specs: List[Tuple[int, int]], encode_spec: Tuple[int, int],
                 scale_base: Sequence[float], pad_modulo: int = 64):
        super().__init__()
        self.num_blocks = len(block_specs)
        for i, (in_planes, c) in enumerate(block_specs):
            setattr(self, f"block{i}", HeadIFBlock(in_planes, c=c))
        self.encode = Head(*encode_spec)
        self.scale_base = tuple(float(s) for s in scale_base)
        self.pad_modulo = int(pad_modulo)

    def forward(self, x, timestep=0.5, scale_list: Optional[Sequence[float]] = None, f0=None, f1=None):
        scale_list = list(scale_list) if scale_list is not None else list(self.scale_base)
        channel = x.shape[1] // 2
        img0 = x[:, :channel]
        img1 = x[:, channel:]
        if not torch.is_tensor(timestep):
            timestep = (x[:, :1].clone() * 0 + 1) * timestep
        else:
            timestep = timestep.repeat(1, 1, img0.shape[2], img0.shape[3])
        if f0 is None:
            f0 = self.encode(img0[:, :3])
        if f1 is None:
            f1 = self.encode(img1[:, :3])

        flow = mask = feat = None
        warped_img0 = img0
        warped_img1 = img1
        for i in range(self.num_blocks):
            block = getattr(self, f"block{i}")
            if flow is None:
                flow, mask, feat = block(
                    torch.cat((img0[:, :3], img1[:, :3], f0, f1, timestep), 1), None, scale=scale_list[i]
                )
            else:
                wf0 = warp(f0, flow[:, :2])
                wf1 = warp(f1, flow[:, 2:4])
                fd, mask, feat = block(
                    torch.cat((warped_img0[:, :3], warped_img1[:, :3], wf0, wf1, timestep, mask, feat), 1),
                    flow,
                    scale=scale_list[i],
                )
                flow = flow + fd
            warped_img0 = warp(img0, flow[:, :2])
            warped_img1 = warp(img1, flow[:, 2:4])

        mask = torch.sigmoid(mask)
        merged = warped_img0 * mask + warped_img1 * (1 - mask)
        return flow, mask, merged
