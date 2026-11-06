from __future__ import annotations

from unittest.mock import patch

import pytest
import torch
from torch.multiprocessing.reductions import StorageWeakRef
from torch.utils._python_dispatch import TorchDispatchMode
from torch.utils._pytree import tree_flatten

from src.pipelines.pipes._shared.generation import dit_placement
from src.platform.runtime.native.arch.minimax_h3 import model as h3_model
from src.platform.runtime.native.kernels import disable_weight_init

_HEADS, _HEAD_DIM, _HIDDEN = 4, 32, 96
_INNER = _HEADS * _HEAD_DIM


class _LiveBytes(TorchDispatchMode):
    def __init__(self) -> None:
        super().__init__()
        self.live: dict = {}
        self.peak = 0

    def __torch_dispatch__(self, func, types, args=(), kwargs=None):
        self.live = {key: entry for key, entry in self.live.items() if not entry[0].expired()}
        inputs = {t.untyped_storage().data_ptr() for t in tree_flatten((args, kwargs))[0]
                  if isinstance(t, torch.Tensor)}
        out = func(*args, **(kwargs or {}))
        for t in tree_flatten(out)[0]:
            if isinstance(t, torch.Tensor):
                storage = t.untyped_storage()
                key = storage.data_ptr()
                if key not in inputs and key not in self.live:
                    self.live[key] = (StorageWeakRef(storage), storage.nbytes())
        self.peak = max(self.peak, sum(nbytes for _ref, nbytes in self.live.values()))
        return out


def _attention():
    torch.manual_seed(11)
    attn = h3_model.MiniMaxH3Attention(_HIDDEN, _HEADS, _HEAD_DIM, 1e-6, disable_weight_init, dtype=torch.bfloat16)
    for p in attn.parameters():
        p.data.normal_(0.0, 0.05)
    return attn


def _inputs(rows):
    torch.manual_seed(12)
    x = torch.randn(1, rows, _HIDDEN, dtype=torch.bfloat16)
    cos = torch.randn(rows, _HEAD_DIM, dtype=torch.bfloat16)
    sin = torch.randn(rows, _HEAD_DIM, dtype=torch.bfloat16)
    return x, (cos, sin)


def _flash_like(q, k, v, **kwargs):
    return (q.float() * 0.5 + v.float() * 0.25).to(q.dtype)


@pytest.mark.parametrize("chunk_rows", [128, 100, 37])
def test_a_chunked_attention_prep_and_out_projection_are_bit_exact(chunk_rows):
    attn = _attention()
    x, rope = _inputs(512)
    with torch.no_grad(), patch.object(h3_model, "_dispatch_attention", _flash_like):
        whole = attn(x, rope, None, 0)
        chunked = attn(x, rope, None, chunk_rows)
    assert torch.equal(chunked, whole)


def _peak_widths(rows, chunk_rows):
    attn = _attention()
    x, rope = _inputs(rows)
    with torch.no_grad(), patch.object(h3_model, "_dispatch_attention", lambda q, k, v, **kw: torch.empty_like(q)), \
         _LiveBytes() as tracker:
        attn(x, rope, None, chunk_rows)
    return tracker.peak / (_INNER * dit_placement._COMPUTE_DTYPE_BYTES)


def _reserve_widths(rows, chunk_rows):
    hidden_input = rows
    return (dit_placement._attention_bytes(rows, _INNER, chunk_rows)
            / (_INNER * dit_placement._COMPUTE_DTYPE_BYTES)) - hidden_input


def test_the_unchunked_reserve_covers_the_measured_attention_peak():
    measured = _peak_widths(2048, 0)
    assert measured <= _reserve_widths(2048, 0) * dit_placement._ACTIVATION_SAFETY_MARGIN
    assert measured > _reserve_widths(2048, 0) * 0.9


@pytest.mark.parametrize("chunk_rows", [128, 512])
def test_the_chunked_reserve_covers_the_measured_attention_peak(chunk_rows):
    measured = _peak_widths(2048, chunk_rows)
    assert measured <= _reserve_widths(2048, chunk_rows)
    assert measured < _peak_widths(2048, 0) * 0.6
