# Vendored from BiRefNet - https://github.com/ZhengPeng7/BiRefNet
# Source file: config.py at commit 25cb9309bacf3dde954e4584594e16e142c51de5.
# License: MIT (see LICENSE in this directory). Copyright (c) 2024 ZhengPeng.
# Local modifications: reduced to the switches an inference forward pass reads,
# each pinned to the value the published swin_v1_l checkpoints were trained
# under. Upstream's Config also walks the filesystem (a dataset root, a weights
# root, and a parse of train.sh), picks a task, and carries the optimiser and
# loss schedule; none of that is reachable from a forward pass. The backbone
# table is trimmed to the swin_v1 family, which is the only one whose code is
# vendored here.
#
# `batch_size` keeps its upstream name and a value > 1 because that is how six
# construction sites decide `nn.BatchNorm2d` over `nn.Identity`, and the
# checkpoints carry BatchNorm parameters. It is not a runtime batch size.


class Config:
    def __init__(self) -> None:
        self.batch_size = 8
        self.SDPA_enabled = True

        self.ms_supervision = True
        self.out_ref = True
        self.dec_ipt = True
        self.dec_ipt_split = True
        self.cxt_num = 3
        self.mul_scl_ipt = 'cat'
        self.dec_att = 'ASPPDeformable'
        self.dec_blk = 'BasicDecBlk'
        self.lat_blk = 'BasicLatBlk'
        self.dec_channels_inter = 'fixed'
        self.auxiliary_classification = False

        self.bb = 'swin_v1_l'
        self.lateral_channels_in_collection = {
            'swin_v1_l': [1536, 768, 384, 192], 'swin_v1_b': [1024, 512, 256, 128],
            'swin_v1_s': [768, 384, 192, 96], 'swin_v1_t': [768, 384, 192, 96],
        }[self.bb]
        if self.mul_scl_ipt == 'cat':
            self.lateral_channels_in_collection = [channel * 2 for channel in self.lateral_channels_in_collection]
        self.cxt = self.lateral_channels_in_collection[1:][::-1][-self.cxt_num:] if self.cxt_num else []
