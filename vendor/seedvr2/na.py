# Vendored from ByteDance's SeedVR2 — https://github.com/ByteDance-Seed/SeedVR
# Upstream path: models/dit_v2/na.py @ unknown; vendored ~2025 (moved into
# vendor/seedvr2/ from src/platform/runtime/native/arch/seedvr2/ as part of
# the license-relocation workstream, BE-97).
# License: Apache-2.0 (see LICENSE).

"""Native-resolution (variable-length) sequence packing helpers.

Verbatim port of the subset of SeedVR2's ``models/dit_v2/na.py`` the 3B NaDiT
uses: samples of different (T, H, W) are flattened into one long ``(L, C)``
sequence with a companion ``(B, n)`` shape tensor, and window/concat transforms
are expressed as index-select closures over that flat layout. Pure tensor ops,
no model weights — kept faithful so the window partitioning and the video/text
varlen interleave match the reference exactly.
"""

from __future__ import annotations

from itertools import chain
from typing import Callable, List, Tuple

import torch


def flatten(hid: List[torch.Tensor]) -> Tuple[torch.Tensor, torch.LongTensor]:
    """List of ``(*dims, c)`` -> ``((L, c), shape)`` where ``shape`` is ``(b, n)``."""
    assert len(hid) > 0
    shape = torch.stack([torch.tensor(x.shape[:-1], device=hid[0].device) for x in hid])
    hid = torch.cat([x.flatten(0, -2) for x in hid])
    return hid, shape


def unflatten(hid: torch.Tensor, hid_shape: torch.LongTensor) -> List[torch.Tensor]:
    """Inverse of :func:`flatten`: split ``(L, c)`` back into per-sample tensors."""
    hid_len = hid_shape.prod(-1)
    hid = hid.split(hid_len.tolist())
    return [x.unflatten(0, s.tolist()) for x, s in zip(hid, hid_shape)]


def concat(vid: torch.Tensor, txt: torch.Tensor, vid_len: torch.LongTensor, txt_len: torch.LongTensor) -> torch.Tensor:
    vid = torch.split(vid, vid_len.tolist())
    txt = torch.split(txt, txt_len.tolist())
    return torch.cat(list(chain(*zip(vid, txt))))


def concat_idx(vid_len: torch.LongTensor, txt_len: torch.LongTensor) -> Tuple[Callable, Callable]:
    """Build (interleave, split) closures joining video+text per sample."""
    device = vid_len.device
    vid_idx = torch.arange(vid_len.sum(), device=device)
    txt_idx = torch.arange(len(vid_idx), len(vid_idx) + txt_len.sum(), device=device)
    tgt_idx = concat(vid_idx, txt_idx, vid_len, txt_len)
    src_idx = torch.argsort(tgt_idx)
    return (
        lambda vid, txt: torch.index_select(torch.cat([vid, txt]), 0, tgt_idx),
        lambda all: torch.index_select(all, 0, src_idx).split([len(vid_idx), len(txt_idx)]),
    )


def repeat_concat(vid, txt, vid_len, txt_len, txt_repeat) -> torch.Tensor:
    vid = torch.split(vid, vid_len.tolist())
    txt = torch.split(txt, txt_len.tolist())
    txt = [[x] * n for x, n in zip(txt, txt_repeat)]
    txt = list(chain(*txt))
    return torch.cat(list(chain(*zip(vid, txt))))


def repeat_concat_idx(
    vid_len: torch.LongTensor,
    txt_len: torch.LongTensor,
    txt_repeat: torch.LongTensor,
) -> Tuple[Callable, Callable]:
    """(interleave, split+coalesce) closures for windowed attention.

    Video is already window-partitioned (``vid_len`` has one entry per window);
    the full text is repeated once per window so every window attends the whole
    caption. On the way back the repeated text copies are averaged (coalesced).
    """
    device = vid_len.device
    vid_idx = torch.arange(vid_len.sum(), device=device)
    txt_idx = torch.arange(len(vid_idx), len(vid_idx) + txt_len.sum(), device=device)
    tgt_idx = repeat_concat(vid_idx, txt_idx, vid_len, txt_len, txt_repeat)
    src_idx = torch.argsort(tgt_idx)
    txt_idx_len = len(tgt_idx) - len(vid_idx)
    repeat_txt_len = (txt_len * txt_repeat).tolist()
    txt_repeat_list = txt_repeat.tolist()

    def unconcat_coalesce(all):
        vid_out, txt_out = all[src_idx].split([len(vid_idx), txt_idx_len])
        txt_out_coalesced = []
        for txt, repeat_time in zip(txt_out.split(repeat_txt_len), txt_repeat_list):
            txt = txt.reshape(-1, repeat_time, *txt.shape[1:]).mean(1)
            txt_out_coalesced.append(txt)
        return vid_out, torch.cat(txt_out_coalesced)

    return (
        lambda vid, txt: torch.cat([vid, txt])[tgt_idx],
        lambda all: unconcat_coalesce(all),
    )


def window(hid: torch.Tensor, hid_shape: torch.LongTensor, window_fn: Callable) -> Tuple:
    hid = unflatten(hid, hid_shape)
    hid = list(map(window_fn, hid))
    hid_windows = torch.tensor(list(map(len, hid)), device=hid_shape.device)
    hid, hid_shape = flatten(list(chain(*hid)))
    return hid, hid_shape, hid_windows


def window_idx(hid_shape: torch.LongTensor, window_fn: Callable) -> Tuple:
    """(partition, reverse) index closures + per-window shapes + per-sample counts."""
    hid_idx = torch.arange(hid_shape.prod(-1).sum(), device=hid_shape.device).unsqueeze(-1)
    tgt_idx, tgt_shape, tgt_windows = window(hid_idx, hid_shape, window_fn)
    tgt_idx = tgt_idx.squeeze(-1)
    src_idx = torch.argsort(tgt_idx)
    return (
        lambda hid: torch.index_select(hid, 0, tgt_idx),
        lambda hid: torch.index_select(hid, 0, src_idx),
        tgt_shape,
        tgt_windows,
    )
