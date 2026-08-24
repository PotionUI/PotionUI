"""SeedVR2 7B NaDiT arch package (ByteDance native-resolution restoration DiT).

The 7B backbone is a *second* SeedVR2 architecture, not a config resize of the 3B
one (``arch/seedvr2/``). It differs structurally in three load-bearing ways:

  * **Plain-MLP blocks** (``mlp_type="normal"``): GELU-tanh ``proj_in``/``proj_out``
    with bias and ``expand_ratio == 4`` — not the 3B's biasless SwiGLU.
  * **Video-only pixel RoPE**: a single per-block ``rope`` (``freqs_for="pixel"``,
    ``max_freq=256``) applied to the video q/k *within each window*; text tokens get
    NO rotary term and there is no multimodal-offset joint RoPE (the 3B ropes both
    streams with a language-basis ``mmrope3d``).
  * **All blocks are multimodal** (``shared_qkv=False``, ``shared_mlp=False`` for
    every layer): each of the 36 blocks keeps split ``.vid``/``.txt`` weights — the
    3B's ``.all`` weight-sharing for later layers is absent. There is also no
    ``vid_out_norm``/``vid_out_ada`` head, so the 3B's ``vid_out_ada`` cache-collision
    quirk has no analogue here.

Everything else (native-resolution packing ``na``, Swin windows ``window``, the
per-forward ``cache``, the varlen attention seam, and the leaf ``TimeEmbedding`` /
``AdaSingle`` / ``NaPatchIn`` / ``NaPatchOut`` / ``MMModule`` modules) is identical
to the 3B and is imported from ``vendor.seedvr2`` rather than duplicated.
"""

from .config import SeedVR27BConfig
from .model import SeedVR27B

__all__ = ["SeedVR27B", "SeedVR27BConfig"]
