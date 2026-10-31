# Vendored from: https://github.com/DepthAnything/Video-Depth-Anything (Apache-2.0, see LICENSE)
import torch.nn as nn
import torch.nn.functional as F

from .dinov2 import DINOv2
from .dpt_temporal import DPTHeadTemporal

INTERMEDIATE_LAYERS = {
    "vits": [2, 5, 8, 11],
    "vitb": [2, 5, 8, 11],
    "vitl": [4, 11, 17, 23],
}


class VideoDepthAnything(nn.Module):
    def __init__(
        self,
        encoder="vits",
        features=64,
        out_channels=(48, 96, 192, 384),
        use_bn=False,
        use_clstoken=False,
        num_frames=32,
        pe="ape",
    ):
        super().__init__()
        self.encoder = encoder
        self.pretrained = DINOv2(model_name=encoder)
        self.head = DPTHeadTemporal(
            self.pretrained.embed_dim, features, use_bn, out_channels=list(out_channels),
            use_clstoken=use_clstoken, num_frames=num_frames, pe=pe,
        )

    def forward(self, x):
        batch, frames, _, height, width = x.shape
        patch_h, patch_w = height // 14, width // 14
        features = self.pretrained.get_intermediate_layers(
            x.flatten(0, 1), INTERMEDIATE_LAYERS[self.encoder], return_class_token=True,
        )
        depth = self.head(features, patch_h, patch_w, frames)[0]
        depth = F.interpolate(depth, size=(height, width), mode="bilinear", align_corners=True)
        depth = F.relu(depth)
        return depth.squeeze(1).unflatten(0, (batch, frames))
