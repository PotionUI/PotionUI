# Vendored from BiRefNet - https://github.com/ZhengPeng7/BiRefNet
# Source file: models/birefnet.py at commit
# 25cb9309bacf3dde954e4584594e16e142c51de5.
# License: MIT (see LICENSE in this directory). Copyright (c) 2024 ZhengPeng.
# Local modifications, all of them removals of code a forward pass cannot
# reach with config.py pinned as it is:
#
#   - `PyTorchModelHubMixin` is gone. It is the download vector: it is what
#     makes `BiRefNet.from_pretrained(<repo id>)` work, and this plugin loads
#     weights only from the user's own model depot.
#   - `bb_pretrained` is gone with it - see backbones/build_backbone.py.
#   - Every `self.training` branch is gone, and with it the `kornia` laplacian
#     import (gradient supervision) and the `dataset` import that supplied the
#     auxiliary classifier's label list (`auxiliary_classification` is False).
#   - The vgg/resnet encoder branch and the vit/dino pyramid neck are gone;
#     config.py pins the backbone to swin_v1, which produces its own pyramid.
#
# `conv_ms_spvn_*` and `gdt_convs_pred_*` are still CONSTRUCTED though nothing
# calls them: the checkpoints carry their weights, and a model that omitted
# them would leave those tensors unaccounted for at load time.

import torch
import torch.nn as nn
import torch.nn.functional as F
from einops import rearrange

from .backbones.build_backbone import build_backbone
from .config import Config
from .modules.decoder_blocks import BasicDecBlk
from .modules.lateral_blocks import BasicLatBlk


def image2patches(image, grid_h=2, grid_w=2, patch_ref=None, transformation='b c (hg h) (wg w) -> (b hg wg) c h w'):
    if patch_ref is not None:
        grid_h, grid_w = image.shape[-2] // patch_ref.shape[-2], image.shape[-1] // patch_ref.shape[-1]
    patches = rearrange(image, transformation, hg=grid_h, wg=grid_w)
    return patches


class BiRefNet(nn.Module):
    def __init__(self):
        super(BiRefNet, self).__init__()
        self.config = Config()
        self.bb = build_backbone(self.config.bb)

        channels = self.config.lateral_channels_in_collection

        self.squeeze_module = nn.Sequential(*[
            BasicDecBlk(channels[0] + sum(self.config.cxt), channels[0])
        ])

        self.decoder = Decoder(channels)

    def forward_enc(self, x):
        x1, x2, x3, x4 = self.bb(x)

        B, C, H, W = x.shape
        x_pyramid = F.interpolate(x, size=(H//2, W//2), mode='bilinear', align_corners=True)
        x1_, x2_, x3_, x4_ = self.bb(x_pyramid)
        x1 = torch.cat([x1, F.interpolate(x1_, size=x1.shape[2:], mode='bilinear', align_corners=True)], dim=1)
        x2 = torch.cat([x2, F.interpolate(x2_, size=x2.shape[2:], mode='bilinear', align_corners=True)], dim=1)
        x3 = torch.cat([x3, F.interpolate(x3_, size=x3.shape[2:], mode='bilinear', align_corners=True)], dim=1)
        x4 = torch.cat([x4, F.interpolate(x4_, size=x4.shape[2:], mode='bilinear', align_corners=True)], dim=1)

        x4 = torch.cat(
            (
                *[
                    F.interpolate(x1, size=x4.shape[2:], mode='bilinear', align_corners=True),
                    F.interpolate(x2, size=x4.shape[2:], mode='bilinear', align_corners=True),
                    F.interpolate(x3, size=x4.shape[2:], mode='bilinear', align_corners=True),
                ][-len(self.config.cxt):],
                x4
            ),
            dim=1
        )
        return x1, x2, x3, x4

    def forward(self, x):
        x1, x2, x3, x4 = self.forward_enc(x)
        x4 = self.squeeze_module(x4)
        return self.decoder([x, x1, x2, x3, x4])


class Decoder(nn.Module):
    def __init__(self, channels):
        super(Decoder, self).__init__()
        self.config = Config()

        self.split = self.config.dec_ipt_split
        N_dec_ipt = 64
        ic = 64
        ipt_cha_opt = 1
        ipt_blk_in_channels = [2**i*3 for i in (10, 8, 6, 4, 0)] if self.split else [3] * 5
        ipt_blk_out_channels = [[N_dec_ipt, channels[i]//8][ipt_cha_opt] for i in range(4)]
        self.ipt_blk5 = SimpleConvs(ipt_blk_in_channels[0], ipt_blk_out_channels[0], inter_channels=ic)
        self.ipt_blk4 = SimpleConvs(ipt_blk_in_channels[1], ipt_blk_out_channels[0], inter_channels=ic)
        self.ipt_blk3 = SimpleConvs(ipt_blk_in_channels[2], ipt_blk_out_channels[1], inter_channels=ic)
        self.ipt_blk2 = SimpleConvs(ipt_blk_in_channels[3], ipt_blk_out_channels[2], inter_channels=ic)
        self.ipt_blk1 = SimpleConvs(ipt_blk_in_channels[4], ipt_blk_out_channels[3], inter_channels=ic)

        bb_neck_out_channels = channels.copy()
        dec_blk_out_channels = [c for c in bb_neck_out_channels[1:]] + [bb_neck_out_channels[-1] // 2]
        dec_blk_in_channels = [bb_neck_out_channels[i] + ipt_blk_out_channels[max(0, i - 1)] for i in range(len(bb_neck_out_channels))]

        self.decoder_block4 = BasicDecBlk(dec_blk_in_channels[0], dec_blk_out_channels[0])
        self.decoder_block3 = BasicDecBlk(dec_blk_in_channels[1], dec_blk_out_channels[1])
        self.decoder_block2 = BasicDecBlk(dec_blk_in_channels[2], dec_blk_out_channels[2])
        self.decoder_block1 = BasicDecBlk(dec_blk_in_channels[3], dec_blk_out_channels[3])
        self.conv_out1 = nn.Sequential(nn.Conv2d(dec_blk_out_channels[3] + ipt_blk_out_channels[3], 1, 1, 1, 0))

        self.lateral_block4 = BasicLatBlk(bb_neck_out_channels[1], dec_blk_out_channels[0])
        self.lateral_block3 = BasicLatBlk(bb_neck_out_channels[2], dec_blk_out_channels[1])
        self.lateral_block2 = BasicLatBlk(bb_neck_out_channels[3], dec_blk_out_channels[2])

        self.conv_ms_spvn_4 = nn.Conv2d(dec_blk_out_channels[0], 1, 1, 1, 0)
        self.conv_ms_spvn_3 = nn.Conv2d(dec_blk_out_channels[1], 1, 1, 1, 0)
        self.conv_ms_spvn_2 = nn.Conv2d(dec_blk_out_channels[2], 1, 1, 1, 0)

        _N = 16
        self.gdt_convs_4 = nn.Sequential(nn.Conv2d(dec_blk_out_channels[0], _N, 3, 1, 1), nn.BatchNorm2d(_N) if self.config.batch_size > 1 else nn.Identity(), nn.ReLU(inplace=True))
        self.gdt_convs_3 = nn.Sequential(nn.Conv2d(dec_blk_out_channels[1], _N, 3, 1, 1), nn.BatchNorm2d(_N) if self.config.batch_size > 1 else nn.Identity(), nn.ReLU(inplace=True))
        self.gdt_convs_2 = nn.Sequential(nn.Conv2d(dec_blk_out_channels[2], _N, 3, 1, 1), nn.BatchNorm2d(_N) if self.config.batch_size > 1 else nn.Identity(), nn.ReLU(inplace=True))

        self.gdt_convs_pred_4 = nn.Sequential(nn.Conv2d(_N, 1, 1, 1, 0))
        self.gdt_convs_pred_3 = nn.Sequential(nn.Conv2d(_N, 1, 1, 1, 0))
        self.gdt_convs_pred_2 = nn.Sequential(nn.Conv2d(_N, 1, 1, 1, 0))

        self.gdt_convs_attn_4 = nn.Sequential(nn.Conv2d(_N, 1, 1, 1, 0))
        self.gdt_convs_attn_3 = nn.Sequential(nn.Conv2d(_N, 1, 1, 1, 0))
        self.gdt_convs_attn_2 = nn.Sequential(nn.Conv2d(_N, 1, 1, 1, 0))

    def forward(self, features):
        x, x1, x2, x3, x4 = features
        outs = []

        patches_batch = image2patches(x, patch_ref=x4, transformation='b c (hg h) (wg w) -> b (c hg wg) h w') if self.split else x
        x4 = torch.cat((x4, self.ipt_blk5(F.interpolate(patches_batch, size=x4.shape[2:], mode='bilinear', align_corners=True))), 1)
        p4 = self.decoder_block4(x4)
        p4 = p4 * self.gdt_convs_attn_4(self.gdt_convs_4(p4)).sigmoid()
        _p4 = F.interpolate(p4, size=x3.shape[2:], mode='bilinear', align_corners=True)
        _p3 = _p4 + self.lateral_block4(x3)

        patches_batch = image2patches(x, patch_ref=_p3, transformation='b c (hg h) (wg w) -> b (c hg wg) h w') if self.split else x
        _p3 = torch.cat((_p3, self.ipt_blk4(F.interpolate(patches_batch, size=x3.shape[2:], mode='bilinear', align_corners=True))), 1)
        p3 = self.decoder_block3(_p3)
        p3 = p3 * self.gdt_convs_attn_3(self.gdt_convs_3(p3)).sigmoid()
        _p3 = F.interpolate(p3, size=x2.shape[2:], mode='bilinear', align_corners=True)
        _p2 = _p3 + self.lateral_block3(x2)

        patches_batch = image2patches(x, patch_ref=_p2, transformation='b c (hg h) (wg w) -> b (c hg wg) h w') if self.split else x
        _p2 = torch.cat((_p2, self.ipt_blk3(F.interpolate(patches_batch, size=x2.shape[2:], mode='bilinear', align_corners=True))), 1)
        p2 = self.decoder_block2(_p2)
        p2 = p2 * self.gdt_convs_attn_2(self.gdt_convs_2(p2)).sigmoid()
        _p2 = F.interpolate(p2, size=x1.shape[2:], mode='bilinear', align_corners=True)
        _p1 = _p2 + self.lateral_block2(x1)

        patches_batch = image2patches(x, patch_ref=_p1, transformation='b c (hg h) (wg w) -> b (c hg wg) h w') if self.split else x
        _p1 = torch.cat((_p1, self.ipt_blk2(F.interpolate(patches_batch, size=x1.shape[2:], mode='bilinear', align_corners=True))), 1)
        _p1 = self.decoder_block1(_p1)
        _p1 = F.interpolate(_p1, size=x.shape[2:], mode='bilinear', align_corners=True)

        patches_batch = image2patches(x, patch_ref=_p1, transformation='b c (hg h) (wg w) -> b (c hg wg) h w') if self.split else x
        _p1 = torch.cat((_p1, self.ipt_blk1(F.interpolate(patches_batch, size=x.shape[2:], mode='bilinear', align_corners=True))), 1)
        p1_out = self.conv_out1(_p1)

        outs.append(p1_out)
        return outs


class SimpleConvs(nn.Module):
    def __init__(
        self, in_channels: int, out_channels: int, inter_channels=64
    ) -> None:
        super().__init__()
        self.conv1 = nn.Conv2d(in_channels, inter_channels, 3, 1, 1)
        self.conv_out = nn.Conv2d(inter_channels, out_channels, 3, 1, 1)

    def forward(self, x):
        return self.conv_out(self.conv1(x))
