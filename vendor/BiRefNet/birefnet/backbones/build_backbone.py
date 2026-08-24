# Vendored from BiRefNet - https://github.com/ZhengPeng7/BiRefNet
# Source file: models/backbones/build_backbone.py at commit
# 25cb9309bacf3dde954e4584594e16e142c51de5.
# License: MIT (see LICENSE in this directory). Copyright (c) 2024 ZhengPeng.
# Local modifications: reduced to the swin_v1 family, and the `pretrained`
# parameter together with `load_weights` is gone rather than defaulted off.
# Upstream would either download torchvision weights or read a .pth from a
# hard-coded `~/weights/cv` path; this plugin never fetches weights at run time
# and the backbone is filled entirely from the user's own checkpoint, so there
# is deliberately no code path here that can load anything.

from .swin_v1 import swin_v1_b, swin_v1_l, swin_v1_s, swin_v1_t


BACKBONES = {
    'swin_v1_t': swin_v1_t,
    'swin_v1_s': swin_v1_s,
    'swin_v1_b': swin_v1_b,
    'swin_v1_l': swin_v1_l,
}


def build_backbone(bb_name):
    if bb_name not in BACKBONES:
        raise ValueError(
            f"Unsupported backbone {bb_name!r}; vendored BiRefNet carries "
            f"{sorted(BACKBONES)}"
        )
    return BACKBONES[bb_name]()
