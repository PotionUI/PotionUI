# Vendored from BiRefNet - https://github.com/ZhengPeng7/BiRefNet
# Source file: models/modules/decoder_blocks.py at commit
# 25cb9309bacf3dde954e4584594e16e142c51de5.
# License: MIT (see LICENSE in this directory). Copyright (c) 2024 ZhengPeng.
# Local modifications: `ResBlk` is dropped (config.py pins `dec_blk` to
# `BasicDecBlk` and `squeeze_block` to one of them), and with it the
# non-deformable `ASPP` branch, which aspp.py no longer carries.

import torch.nn as nn

from .aspp import ASPPDeformable
from ..config import Config


config = Config()


class BasicDecBlk(nn.Module):
    def __init__(self, in_channels=64, out_channels=64, inter_channels=64):
        super(BasicDecBlk, self).__init__()
        inter_channels = in_channels // 4 if config.dec_channels_inter == 'adap' else 64
        self.conv_in = nn.Conv2d(in_channels, inter_channels, 3, 1, padding=1)
        self.relu_in = nn.ReLU(inplace=True)
        if config.dec_att == 'ASPPDeformable':
            self.dec_att = ASPPDeformable(in_channels=inter_channels)
        self.conv_out = nn.Conv2d(inter_channels, out_channels, 3, 1, padding=1)
        self.bn_in = nn.BatchNorm2d(inter_channels) if config.batch_size > 1 else nn.Identity()
        self.bn_out = nn.BatchNorm2d(out_channels) if config.batch_size > 1 else nn.Identity()

    def forward(self, x):
        x = self.conv_in(x)
        x = self.bn_in(x)
        x = self.relu_in(x)
        if hasattr(self, 'dec_att'):
            x = self.dec_att(x)
        x = self.conv_out(x)
        x = self.bn_out(x)
        return x
