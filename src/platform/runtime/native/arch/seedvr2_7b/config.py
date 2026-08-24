"""``SeedVR27BConfig`` — construction config for the SeedVR2 **7B** NaDiT.

The 7B checkpoint (ByteDance ``configs_7b/main.yaml``) shares the 3B's I/O contract
(33-channel latent in -> 16-channel v-target out, patch ``[1,2,2]``, window ``(4,3,3)``
with alternating aligned/shifted 720p windows, ``AdaSingle`` timestep modulation) but
is a wider, deeper, structurally distinct backbone:

    vid_dim      3072   (== ``vid_in.proj`` rows)
    heads        24     (proj_qkv rows // (3 * head_dim))
    head_dim     128
    num_layers   36      — every block is multimodal (split ``.vid``/``.txt``)
    emb_dim      18432   (== 6 * vid_dim, AdaSingle)
    txt_in_dim   5120    (fixed prompt-embedding width, == the 3B's)
    mlp_hidden   12288   (== expand_ratio 4 * vid_dim; plain GELU MLP, not SwiGLU)

All load-bearing values are shape-derivable from the checkpoint (see the detector
``detect/unet_detect.py::_detect_seedvr2``, which emits ``seedvr2_variant="7b"``);
the patch size, window scheme and RoPE constants are architecture fixtures defaulted
here. Unlike the 3B there is **no** ``vid_out_norm``/``vid_out_ada`` head.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Tuple

SEEDVR2 = "seedvr2"
SEEDVR2_7B = "7b"


@dataclass(frozen=True)
class SeedVR27BConfig:
    """Fully-resolved SeedVR2 7B NaDiT hyper-parameters."""

    vid_in_channels: int = 33
    vid_out_channels: int = 16
    vid_dim: int = 3072
    txt_in_dim: int = 5120
    emb_dim: int = 18432
    num_layers: int = 36
    heads: int = 24
    head_dim: int = 128
    mlp_hidden: int = 12288
    norm_eps: float = 1e-5
    patch_size: Tuple[int, int, int] = (1, 2, 2)
    window: Tuple[int, int, int] = (4, 3, 3)
    # Even layers use aligned windows, odd layers half-shifted windows.
    window_methods: Tuple[str, str] = ("720pwin_by_size_bysize", "720pswin_by_size_bysize")

    def __post_init__(self) -> None:
        if self.emb_dim != 6 * self.vid_dim:
            raise ValueError(
                f"emb_dim {self.emb_dim} must equal 6 * vid_dim ({6 * self.vid_dim}) for AdaSingle"
            )
        if self.heads * self.head_dim != self.vid_dim:
            raise ValueError(
                f"heads*head_dim ({self.heads * self.head_dim}) must equal vid_dim {self.vid_dim}"
            )

    def window_method(self, layer_index: int) -> str:
        """Alternating aligned/shifted window scheme (even -> aligned, odd -> shifted)."""
        return self.window_methods[layer_index % 2]

    @classmethod
    def from_detect_config(cls, config: dict[str, Any]) -> "SeedVR27BConfig":
        if config.get("image_model") != SEEDVR2:
            raise ValueError(
                f"SeedVR27BConfig: unsupported image_model {config.get('image_model')!r}"
            )
        if config.get("seedvr2_variant") not in (None, SEEDVR2_7B):
            raise ValueError(
                f"SeedVR27BConfig: expected seedvr2_variant '7b', got "
                f"{config.get('seedvr2_variant')!r}"
            )
        defaults = cls()
        return cls(
            vid_in_channels=int(config.get("vid_in_channels", defaults.vid_in_channels)),
            vid_out_channels=int(config.get("vid_out_channels", defaults.vid_out_channels)),
            vid_dim=int(config.get("vid_dim", defaults.vid_dim)),
            txt_in_dim=int(config.get("txt_in_dim", defaults.txt_in_dim)),
            emb_dim=int(config.get("emb_dim", defaults.emb_dim)),
            num_layers=int(config.get("num_layers", defaults.num_layers)),
            heads=int(config.get("heads", defaults.heads)),
            head_dim=int(config.get("head_dim", defaults.head_dim)),
            mlp_hidden=int(config.get("mlp_hidden", defaults.mlp_hidden)),
            norm_eps=float(config.get("norm_eps", defaults.norm_eps)),
            patch_size=tuple(config.get("patch_size", defaults.patch_size)),
            window=tuple(config.get("window", defaults.window)),
            window_methods=tuple(config.get("window_methods", defaults.window_methods)),
        )
