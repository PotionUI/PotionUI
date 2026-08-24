"""``SeedVR2Config`` — construction config for the SeedVR2 NaDiT.

SeedVR2 (ByteDance) is a diffusion **video/image restoration** transformer:
a Native-resolution DiT (NaDiT) with joint video+text attention, 3D Swin
windows, ``AdaSingle`` timestep modulation and a SwiGLU MLP. The 3B checkpoint
ingests a 33-channel latent (16 noisy + 16 low-res conditioning + 1 mask) and
predicts a 16-channel v-target.

All load-bearing hyper-parameters are shape-derivable from the checkpoint, so the
detector (a sibling task, ``detect/``) sniffs them and hands this config a plain
dict via :meth:`from_detect_config`. The detector dict keys are:

    image_model       str, must be ``"seedvr2"``
    vid_in_channels   int  (33)  — proj_in cols // (patch_t*patch_h*patch_w)
    vid_out_channels  int  (16)  — vid_out proj rows // (patch_t*patch_h*patch_w)
    vid_dim           int  (2560) — model width (== ``vid_in.proj`` rows)
    txt_in_dim        int  (5120) — text-encoder width (== ``txt_in`` cols)
    emb_dim           int  (15360) — timestep embed width (== 6 * vid_dim)
    num_layers        int  (32)  — count of ``blocks.*``
    mm_layers         int  (10)  — count of blocks with split ``.vid``/``.txt``
                                    weights (the rest share ``.all`` weights)
    heads             int  (20)  — proj_qkv rows // (3 * head_dim)
    head_dim          int  (128) — from ``norm_q`` weight length
    mlp_hidden        int  (6912) — SwiGLU inner dim (``mlp...proj_in`` rows)

Everything else (patch size ``[1,2,2]``, window ``(4,3,3)``, the alternating
window methods, ``rope_dim`` 128, RMS norm eps, ``vid_out_norm`` present) is fixed
for this architecture and defaulted here; the detector may override any of them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Tuple

SEEDVR2 = "seedvr2"


@dataclass(frozen=True)
class SeedVR2Config:
    """Fully-resolved SeedVR2 NaDiT hyper-parameters."""

    vid_in_channels: int = 33
    vid_out_channels: int = 16
    vid_dim: int = 2560
    txt_in_dim: int = 5120
    emb_dim: int = 15360
    num_layers: int = 32
    mm_layers: int = 10
    heads: int = 20
    head_dim: int = 128
    mlp_hidden: int = 6912
    norm_eps: float = 1e-5
    rope_dim: int = 128
    patch_size: Tuple[int, int, int] = (1, 2, 2)
    window: Tuple[int, int, int] = (4, 3, 3)
    # Even layers use aligned windows, odd layers half-shifted windows.
    window_methods: Tuple[str, str] = ("720pwin_by_size_bysize", "720pswin_by_size_bysize")
    vid_out_norm: bool = True

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

    def is_mm_layer(self, layer_index: int) -> bool:
        """First ``mm_layers`` blocks keep separate video/text weights (``.vid``/
        ``.txt``); later blocks share one set (``.all``)."""
        return layer_index < self.mm_layers

    @classmethod
    def from_detect_config(cls, config: dict[str, Any]) -> "SeedVR2Config":
        if config.get("image_model") != SEEDVR2:
            raise ValueError(f"SeedVR2Config: unsupported image_model {config.get('image_model')!r}")
        defaults = cls()
        return cls(
            vid_in_channels=int(config.get("vid_in_channels", defaults.vid_in_channels)),
            vid_out_channels=int(config.get("vid_out_channels", defaults.vid_out_channels)),
            vid_dim=int(config.get("vid_dim", defaults.vid_dim)),
            txt_in_dim=int(config.get("txt_in_dim", defaults.txt_in_dim)),
            emb_dim=int(config.get("emb_dim", defaults.emb_dim)),
            num_layers=int(config.get("num_layers", defaults.num_layers)),
            mm_layers=int(config.get("mm_layers", defaults.mm_layers)),
            heads=int(config.get("heads", defaults.heads)),
            head_dim=int(config.get("head_dim", defaults.head_dim)),
            mlp_hidden=int(config.get("mlp_hidden", defaults.mlp_hidden)),
            norm_eps=float(config.get("norm_eps", defaults.norm_eps)),
            rope_dim=int(config.get("rope_dim", defaults.rope_dim)),
            patch_size=tuple(config.get("patch_size", defaults.patch_size)),
            window=tuple(config.get("window", defaults.window)),
            window_methods=tuple(config.get("window_methods", defaults.window_methods)),
            vid_out_norm=bool(config.get("vid_out_norm", defaults.vid_out_norm)),
        )
