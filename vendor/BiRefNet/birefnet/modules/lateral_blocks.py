# Vendored from BiRefNet - https://github.com/ZhengPeng7/BiRefNet
# Source file: models/modules/lateral_blocks.py at commit
# 25cb9309bacf3dde954e4584594e16e142c51de5.
# License: MIT (see LICENSE in this directory). Copyright (c) 2024 ZhengPeng.
# Local modifications: the unused module-level `Config()` instantiation is
# dropped; nothing in this file reads it.

import torch.nn as nn


class BasicLatBlk(nn.Module):
    def __init__(self, in_channels=64, out_channels=64, ks=1, s=1, p=0):
        super(BasicLatBlk, self).__init__()
        self.conv = nn.Conv2d(in_channels, out_channels, ks, s, p)

    def forward(self, x):
        x = self.conv(x)
        return x
