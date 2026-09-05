"""SeedVR2 varlen geometry: block boundaries resolved once, reused per block.

The NaDiT builds its shape tensors on the compute device, so every read of
``cu_seqlens`` from Python is a device sync. Before the geometry, the flash path
did two of them per attention block (``max().item()`` on q and k) and the
fallback copied the split points to the host per block. These tests pin the
numerics against verbatim copies of those old helpers and count what the new
path actually touches.
"""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest
import torch

import vendor.seedvr2.attention as sv_att
from src.platform.runtime.native.arch.seedvr2.config import SeedVR2Config
from src.platform.runtime.native.arch.seedvr2.model import SeedVR2
from src.platform.runtime.native.arch.seedvr2_7b.config import SeedVR27BConfig
from src.platform.runtime.native.arch.seedvr2_7b.model import SeedVR27B
from src.platform.runtime.native.attention import attention as _dispatch_attention
from vendor.gpl.comfyui.ops import pick_operations

TINY_3B = dict(
    vid_in_channels=8, vid_out_channels=4, vid_dim=32, txt_in_dim=16, emb_dim=192,
    num_layers=6, mm_layers=1, heads=4, head_dim=8, mlp_hidden=64,
    patch_size=(1, 2, 2), window=(1, 1, 1), rope_dim=8,
)
TINY_7B = dict(
    vid_in_channels=8, vid_out_channels=4, vid_dim=96, txt_in_dim=16, emb_dim=576,
    num_layers=6, heads=4, head_dim=24, mlp_hidden=64,
    patch_size=(1, 2, 2), window=(1, 1, 1),
)


@pytest.fixture(autouse=True)
def _wire_backend():
    sv_att.set_attention_backend(_dispatch_attention)
    sv_att.reset_flash_varlen_cache()
    yield
    sv_att.reset_flash_varlen_cache()


def _cu(lens, dtype=torch.int32):
    return torch.tensor([0] + torch.tensor(lens).cumsum(0).tolist(), dtype=dtype)


# --------------------------------------------------------------------------- #
# Oracles: the pre-geometry helpers, copied verbatim.
# --------------------------------------------------------------------------- #

def _legacy_flash_arguments(q, cu_seqlens_q, cu_seqlens_k):
    cu_q = cu_seqlens_q.to(device=q.device, dtype=torch.int32)
    cu_k = cu_seqlens_k.to(device=q.device, dtype=torch.int32)
    max_seqlen_q = int((cu_q[1:] - cu_q[:-1]).max().item())
    max_seqlen_k = int((cu_k[1:] - cu_k[:-1]).max().item())
    return cu_q, cu_k, max_seqlen_q, max_seqlen_k


def _legacy_fallback(q, k, v, cu_seqlens_q, cu_seqlens_k):
    q_bounds = cu_seqlens_q[1:-1].to(dtype=torch.long, device="cpu")
    k_bounds = cu_seqlens_k[1:-1].to(dtype=torch.long, device="cpu")
    q_blocks = torch.tensor_split(q, q_bounds, dim=0)
    k_blocks = torch.tensor_split(k, k_bounds, dim=0)
    v_blocks = torch.tensor_split(v, k_bounds, dim=0)

    outs = []
    for qi, ki, vi in zip(q_blocks, k_blocks, v_blocks):
        qi = qi.transpose(0, 1).unsqueeze(0)
        ki = ki.transpose(0, 1).unsqueeze(0)
        vi = vi.transpose(0, 1).unsqueeze(0)
        oi = _dispatch_attention(qi, ki, vi, mask=None)
        outs.append(oi.squeeze(0).transpose(0, 1))
    return torch.cat(outs, dim=0)


@contextmanager
def _count_tensor_ops():
    """Count the tensor reads that are device→host syncs on CUDA."""
    counts = {"item": 0, "tolist": 0, "max": 0, "to_device": 0}
    originals = {n: getattr(torch.Tensor, n) for n in ("item", "tolist", "max", "to")}

    def make(name, orig):
        def wrapper(self, *args, **kwargs):
            counts[name] += 1
            return orig(self, *args, **kwargs)
        return wrapper

    def to_wrapper(self, *args, **kwargs):
        if "device" in kwargs or (args and isinstance(args[0], (str, torch.device))):
            counts["to_device"] += 1
        return originals["to"](self, *args, **kwargs)

    for name in ("item", "tolist", "max"):
        setattr(torch.Tensor, name, make(name, originals[name]))
    torch.Tensor.to = to_wrapper
    try:
        yield counts
    finally:
        for name, fn in originals.items():
            setattr(torch.Tensor, name, fn)


def _flash_mock(monkeypatch, out_fn=None):
    mock = MagicMock(side_effect=out_fn or (lambda *a, **k: torch.zeros_like(a[0])))
    monkeypatch.setattr(sv_att, "_probe_flash_varlen", lambda: mock)
    monkeypatch.setattr(sv_att, "_flash_varlen_func", mock)
    monkeypatch.setattr(sv_att, "_flash_varlen_available", lambda t: True)
    return mock


# --------------------------------------------------------------------------- #
# Parity with the pre-geometry helpers
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("lens", [[3, 4, 2], [9], [1, 1, 1, 1], [5, 5]])
@pytest.mark.parametrize("dtype", [torch.int32, torch.long])
def test_geometry_matches_legacy_flash_arguments(monkeypatch, lens, dtype):
    total = sum(lens)
    q = torch.randn(total, 2, 4, dtype=torch.float16)
    cu = _cu(lens, dtype=dtype)
    mock = _flash_mock(monkeypatch)

    sv_att.varlen_attention(q, q.clone(), q.clone(), cu, cu)

    exp_cu_q, exp_cu_k, exp_max_q, exp_max_k = _legacy_flash_arguments(q, cu, cu)
    kwargs = mock.call_args.kwargs
    assert torch.equal(kwargs["cu_seqlens_q"], exp_cu_q)
    assert torch.equal(kwargs["cu_seqlens_k"], exp_cu_k)
    assert kwargs["cu_seqlens_q"].dtype == torch.int32
    assert kwargs["max_seqlen_q"] == exp_max_q and isinstance(kwargs["max_seqlen_q"], int)
    assert kwargs["max_seqlen_k"] == exp_max_k
    assert kwargs["causal"] is False


@pytest.mark.parametrize("lens_q,lens_k", [([3, 4, 2], [3, 4, 2]), ([2, 2], [5, 3]), ([9], [9])])
def test_fallback_output_matches_legacy_fallback(lens_q, lens_k):
    torch.manual_seed(0)
    q = torch.randn(sum(lens_q), 2, 4, dtype=torch.float64)
    k = torch.randn(sum(lens_k), 2, 4, dtype=torch.float64)
    v = torch.randn(sum(lens_k), 2, 4, dtype=torch.float64)
    cu_q, cu_k = _cu(lens_q).long(), _cu(lens_k).long()

    out = sv_att.varlen_attention(q, k, v, sv_att.VarlenGeometry(cu_q), sv_att.VarlenGeometry(cu_k))
    assert torch.equal(out, _legacy_fallback(q, k, v, cu_q, cu_k))


# --------------------------------------------------------------------------- #
# The geometry itself
# --------------------------------------------------------------------------- #

def test_geometry_exposes_python_side_offsets_and_maxima():
    geo = sv_att.VarlenGeometry(_cu([3, 4, 2]))
    assert geo.offsets == [0, 3, 7, 9]
    assert geo.lengths == [3, 4, 2]
    assert geo.max_seqlen == 4
    assert geo.split_bounds == [3, 7]
    assert geo.blocks == 3
    assert all(isinstance(x, int) for x in geo.offsets + geo.split_bounds)


def test_geometry_caches_one_device_tensor_per_device():
    geo = sv_att.VarlenGeometry(_cu([3, 4, 2], dtype=torch.long))
    cpu = torch.device("cpu")

    first = geo.cu_on(cpu)
    assert first.dtype == torch.int32
    assert torch.equal(first, torch.tensor([0, 3, 7, 9], dtype=torch.int32))
    for _ in range(10):
        assert geo.cu_on(cpu) is first

    geo.cu_on(torch.device("meta"))
    assert len(geo._by_device) == 2  # one entry per live device, nothing per block

    retained = sum(t.numel() * t.element_size() for t in geo._by_device.values())
    assert retained <= 2 * (geo.blocks + 1) * 4


def test_repeated_blocks_read_nothing_from_the_device(monkeypatch):
    q = torch.randn(9, 2, 4, dtype=torch.float16)
    geo = sv_att.VarlenGeometry(_cu([3, 4, 2]))
    mock = _flash_mock(monkeypatch)

    with _count_tensor_ops() as counts:
        for _ in range(8):
            sv_att.varlen_attention(q, q, q, geo, geo)

    assert counts == {"item": 0, "tolist": 0, "max": 0, "to_device": 0}
    handed = [c.kwargs["cu_seqlens_q"] for c in mock.call_args_list]
    assert all(t is handed[0] for t in handed)


def test_legacy_helpers_synced_on_every_block():
    """The bar the geometry clears: the old helpers read the device per call."""
    q = torch.randn(9, 2, 4, dtype=torch.float16)
    k = v = torch.randn(9, 2, 4, dtype=torch.float64)
    cu = _cu([3, 4, 2]).long()

    with _count_tensor_ops() as flash_counts:
        for _ in range(8):
            _legacy_flash_arguments(q, cu, cu)
    assert flash_counts["item"] == 16 and flash_counts["max"] == 16

    with _count_tensor_ops() as fb_counts:
        for _ in range(8):
            _legacy_fallback(q.double(), k, v, cu, cu)
    assert fb_counts["to_device"] == 16


def test_fallback_reads_nothing_from_the_device_per_block():
    torch.manual_seed(0)
    q = torch.randn(9, 2, 4, dtype=torch.float64)
    geo = sv_att.VarlenGeometry(_cu([3, 4, 2]).long())

    with _count_tensor_ops() as counts:
        for _ in range(8):
            sv_att.varlen_attention(q, q, q, geo, geo)

    assert counts["item"] == 0 and counts["tolist"] == 0 and counts["to_device"] == 0


def test_changed_shapes_need_a_new_geometry(monkeypatch):
    mock = _flash_mock(monkeypatch)
    for lens in ([3, 4, 2], [5, 4]):
        q = torch.randn(sum(lens), 2, 4, dtype=torch.float16)
        geo = sv_att.VarlenGeometry(_cu(lens))
        sv_att.varlen_attention(q, q, q, geo, geo)

    first, second = mock.call_args_list
    assert first.kwargs["max_seqlen_q"] == 4
    assert second.kwargs["max_seqlen_q"] == 5
    assert not torch.equal(first.kwargs["cu_seqlens_q"], second.kwargs["cu_seqlens_q"])


def test_raw_cu_seqlens_tensor_is_still_accepted():
    torch.manual_seed(0)
    q = torch.randn(9, 2, 4, dtype=torch.float64)
    cu = _cu([3, 4, 2]).long()
    assert torch.equal(
        sv_att.varlen_attention(q, q, q, cu, cu),
        sv_att.varlen_attention(q, q, q, sv_att.VarlenGeometry(cu), sv_att.VarlenGeometry(cu)),
    )


# --------------------------------------------------------------------------- #
# Through the real entry point: a whole NaDiT forward
# --------------------------------------------------------------------------- #

def _tiny_model(kind):
    torch.manual_seed(0)
    ops = pick_operations(torch.float32, torch.float32)
    if kind == "3b":
        m = SeedVR2(SeedVR2Config(**TINY_3B), ops)
    else:
        m = SeedVR27B(SeedVR27BConfig(**TINY_7B), ops)
    with torch.no_grad():
        for p in m.parameters():
            p.normal_(0.0, 0.02) if p.dim() >= 2 else p.zero_()
    return m.eval()


@pytest.mark.parametrize("kind", ["3b", "7b"])
def test_forward_builds_one_geometry_per_window_configuration(monkeypatch, kind):
    """Six blocks alternate between two window methods, so a forward needs two
    geometries — not one per block — and no per-block device read."""
    m = _tiny_model(kind)
    vid = torch.randn(1, 8, 2, 8, 8)
    txt = torch.randn(5, 16)

    built = []
    real_init = sv_att.VarlenGeometry.__init__

    def counting_init(self, cu_seqlens):
        built.append(tuple(cu_seqlens.tolist()))
        real_init(self, cu_seqlens)

    monkeypatch.setattr(sv_att.VarlenGeometry, "__init__", counting_init)

    with torch.no_grad(), _count_tensor_ops() as counts:
        out = m(vid, torch.tensor(0.5), txt)

    assert out.shape == (1, 4, 2, 8, 8)
    assert torch.isfinite(out).all()
    assert len(built) == 2  # one per window method, not one per block
    assert counts["item"] == 0 and counts["max"] == 0


@pytest.mark.parametrize("kind", ["3b", "7b"])
def test_forward_geometry_is_released_with_the_forward(kind):
    """Nothing survives the forward: no module-level cache holds a geometry."""
    import gc

    m = _tiny_model(kind)
    vid = torch.randn(1, 8, 2, 8, 8)
    with torch.no_grad():
        m(vid, torch.tensor(0.5), torch.randn(5, 16))

    gc.collect()
    assert [o for o in gc.get_objects() if isinstance(o, sv_att.VarlenGeometry)] == []
