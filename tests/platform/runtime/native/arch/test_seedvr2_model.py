"""Forward-pass smoke tests for the SeedVR2 3B/7B NaDiT arch classes.

Neither backbone had a construct+forward test anywhere in the suite before
this: every other seedvr2 test exercises the generator/model_loader
pipes or the VAE, never the arch classes themselves. Added alongside the
vendor relocation (block-level building blocks moved to
vendor/seedvr2/, vendor/seedvr2/seedvr2_7b/) specifically to catch a
refactor mistake in that extraction.
"""

from __future__ import annotations

import torch

from src.platform.runtime.native.arch.seedvr2.config import SeedVR2Config
from src.platform.runtime.native.arch.seedvr2.model import SeedVR2
from src.platform.runtime.native.arch.seedvr2_7b.config import SeedVR27BConfig
from src.platform.runtime.native.arch.seedvr2_7b.model import SeedVR27B
from vendor.gpl.comfyui.ops import pick_operations

# Tiny 3B config. head_dim=8 is fine here (the 3B's language-basis RoPE
# doesn't degenerate at this width the way the 7B's pixel-basis one does).
TINY_3B = dict(
    vid_in_channels=8, vid_out_channels=4, vid_dim=32, txt_in_dim=16, emb_dim=192,
    num_layers=3, mm_layers=1, heads=4, head_dim=8, mlp_hidden=64,
    patch_size=(1, 2, 2), window=(1, 1, 1), rope_dim=8,
)

# Tiny 7B config. head_dim must satisfy (head_dim // 2) // 3 >= 1 or the
# pixel-basis rotary's per-axis freq table collapses to 0 elements — 24 is
# the smallest head_dim clearing that bound.
TINY_7B = dict(
    vid_in_channels=8, vid_out_channels=4, vid_dim=96, txt_in_dim=16, emb_dim=576,
    num_layers=3, heads=4, head_dim=24, mlp_hidden=64,
    patch_size=(1, 2, 2), window=(1, 1, 1),
)


def _fp32_ops():
    return pick_operations(torch.float32, torch.float32)


def _randomize(m: torch.nn.Module) -> None:
    with torch.no_grad():
        for p in m.parameters():
            if p.dim() >= 2:
                p.normal_(0.0, 0.02)
            else:
                p.zero_()


def test_seedvr2_3b_forward_shape():
    torch.manual_seed(0)
    cfg = SeedVR2Config(**TINY_3B)
    m = SeedVR2(cfg, _fp32_ops())
    _randomize(m)
    m.eval()

    vid = torch.randn(1, 8, 2, 8, 8)
    txt = torch.randn(5, 16)
    with torch.no_grad():
        out = m(vid, torch.tensor(0.5), txt)

    assert out.shape == (1, 4, 2, 8, 8)
    assert torch.isfinite(out).all()


def test_seedvr2_7b_forward_shape():
    torch.manual_seed(0)
    cfg = SeedVR27BConfig(**TINY_7B)
    m = SeedVR27B(cfg, _fp32_ops())
    _randomize(m)
    m.eval()

    vid = torch.randn(1, 8, 2, 8, 8)
    txt = torch.randn(5, 16)
    with torch.no_grad():
        out = m(vid, torch.tensor(0.5), txt)

    assert out.shape == (1, 4, 2, 8, 8)
    assert torch.isfinite(out).all()


def test_seedvr2_3b_is_native_arch_module():
    from src.platform.runtime.native.base import NativeArchModule

    with torch.device("meta"):
        m = SeedVR2(SeedVR2Config(**TINY_3B), _fp32_ops())
    assert isinstance(m, NativeArchModule)


def test_seedvr2_7b_is_native_arch_module():
    from src.platform.runtime.native.base import NativeArchModule

    with torch.device("meta"):
        m = SeedVR27B(SeedVR27BConfig(**TINY_7B), _fp32_ops())
    assert isinstance(m, NativeArchModule)
