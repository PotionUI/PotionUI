"""Key names and tensor shapes of the PUBLISHED Practical-RIFE checkpoints.

Transcribed by reading the files themselves, so the tests can build a state dict
that matches a real checkpoint without shipping 21MB of weights:

    rife-4.6  120 tensors  no `encode.*`
    rife47    124 tensors  sha256 6a8a825ab2750558bdd20dcced386fd82b7222c7ba58c11d3b611d9c44f1be63
    rife49    124 tensors  sha256 e55fd00f3cc184e3c65961f4bb827a9da022e78eed36b055242c0ac30000d533

Nothing here may be derived from `IFNet`/`load_ifnet`: these tables are the
specification the loader is tested against, and generating them from the code
under test is what let a loader that rejected every real checkpoint look green.
"""

from collections import OrderedDict
from typing import Dict, List, Tuple

import torch

# (in_planes, c) per IFBlock. in_planes counts the channels the block's conv0
# actually receives: img0[:3] + img1[:3] + timestep (+ mask on blocks 1-3, + the
# flow IFBlock.forward concatenates itself, + 2x the encoder's features when the
# variant has one).
REAL_NO_ENCODER_BLOCKS: List[Tuple[int, int]] = [(7, 192), (12, 128), (12, 96), (12, 64)]
REAL_ENCODER_BLOCKS: List[Tuple[int, int]] = [(15, 192), (20, 128), (20, 96), (20, 64)]

# The 4.7/4.9 encoder: Conv2d(3, 16, 3, 2, 1) -> ConvTranspose2d(16, 4, 4, 2, 1),
# a bare two-layer Sequential, hence the `encode.0`/`encode.1` names.
ENCODER_LAYOUT: Dict[str, tuple] = {
    "encode.0.weight": (16, 3, 3, 3),
    "encode.0.bias": (16,),
    "encode.1.weight": (16, 4, 4, 4),
    "encode.1.bias": (4,),
}

# Same key structure at narrow `c`, for forward passes that would be needlessly
# slow at the published 192/128/96/64 widths. in_planes and the encoder shapes
# stay real -- only the widths the loader infers per checkpoint are reduced.
NARROW_NO_ENCODER_BLOCKS: List[Tuple[int, int]] = [(7, 16), (12, 16), (12, 16), (12, 16)]
NARROW_ENCODER_BLOCKS: List[Tuple[int, int]] = [(15, 16), (20, 16), (20, 16), (20, 16)]

_RESCONVS_PER_BLOCK = 8
_FLOW_AND_MASK_OUT = 24  # lastconv emits 4*6 channels ahead of PixelShuffle(2)


def block_layout(idx: int, in_planes: int, c: int) -> Dict[str, tuple]:
    shapes: Dict[str, tuple] = {
        f"block{idx}.conv0.0.0.weight": (c // 2, in_planes, 3, 3),
        f"block{idx}.conv0.0.0.bias": (c // 2,),
        f"block{idx}.conv0.1.0.weight": (c, c // 2, 3, 3),
        f"block{idx}.conv0.1.0.bias": (c,),
    }
    for res in range(_RESCONVS_PER_BLOCK):
        shapes[f"block{idx}.convblock.{res}.beta"] = (1, c, 1, 1)
        shapes[f"block{idx}.convblock.{res}.conv.weight"] = (c, c, 3, 3)
        shapes[f"block{idx}.convblock.{res}.conv.bias"] = (c,)
    shapes[f"block{idx}.lastconv.0.weight"] = (c, _FLOW_AND_MASK_OUT, 4, 4)
    shapes[f"block{idx}.lastconv.0.bias"] = (_FLOW_AND_MASK_OUT,)
    return shapes


def real_layout(block_specs: List[Tuple[int, int]], with_encoder: bool) -> Dict[str, tuple]:
    shapes: Dict[str, tuple] = OrderedDict()
    for idx, (in_planes, c) in enumerate(block_specs):
        shapes.update(block_layout(idx, in_planes, c))
    if with_encoder:
        shapes.update(ENCODER_LAYOUT)
    return shapes


def head_style_encoder_layout() -> Dict[str, tuple]:
    """The rife4.10+ `Head` encoder: four named convs, multi-scale features, and
    a forward this IFNet does not implement."""
    return {
        "encode.cnn0.weight": (32, 3, 3, 3), "encode.cnn0.bias": (32,),
        "encode.cnn1.weight": (32, 32, 3, 3), "encode.cnn1.bias": (32,),
        "encode.cnn2.weight": (32, 32, 3, 3), "encode.cnn2.bias": (32,),
        "encode.cnn3.weight": (32, 8, 4, 4), "encode.cnn3.bias": (8,),
    }


def state_dict_for(shapes: Dict[str, tuple], seed: int = 0) -> "OrderedDict[str, torch.Tensor]":
    generator = torch.Generator().manual_seed(seed)
    return OrderedDict(
        (key, torch.randn(*shape, generator=generator) * 0.02)
        for key, shape in shapes.items()
    )
