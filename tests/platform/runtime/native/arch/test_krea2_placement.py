from __future__ import annotations

from types import SimpleNamespace

import pytest
import torch

from src.platform.runtime.native.arch.krea2.model import Krea2
from src.platform.runtime.native.arch.krea2.placement import (
    normalize_ref_position,
    place_refs,
    ref_position_modes,
)
from src.platform.runtime.native.base import load_into_module
from src.platform.runtime.native.detect.registry import match_model_spec
from src.platform.runtime.native.engine import NativeGenerator, NativeModel
from .._native_ops import fp32_ops

TINY = {
    "image_model": "krea2", "features": 32, "heads": 2, "kvheads": 1,
    "channels": 4, "layers": 1, "multiplier": 1, "tdim": 16, "txtdim": 16,
    "txtheads": 2, "txtkvheads": 2, "txtlayers": 3, "patch": 2, "theta": 1000.0,
}


def _build_ready() -> Krea2:
    m = Krea2.from_config(TINY, fp32_ops())
    sd = {}
    for k, v in m.state_dict().items():
        if k.endswith(".scale") or k.endswith(".mod.lin") or k.endswith(".modulation.lin"):
            sd[k] = torch.zeros_like(v)
        elif v.is_floating_point():
            sd[k] = torch.randn_like(v) * 0.02
        else:
            sd[k] = v.clone()
    load_into_module(m, sd, match_model_spec(TINY))
    m.eval()
    return m


def _grid_ids(frame, gh, gw):
    ids = torch.zeros(gh, gw, 3)
    ids[..., 0] = frame
    ids[..., 1] = torch.arange(gh, dtype=torch.float32)[:, None]
    ids[..., 2] = torch.arange(gw, dtype=torch.float32)[None, :]
    return ids


def test_normalize_accepts_upstream_labels():
    assert normalize_ref_position("aligned (trained)") == "aligned"
    assert normalize_ref_position("beside (right of target)") == "beside"
    assert normalize_ref_position("bag of features") == "bag"
    assert normalize_ref_position(None) == "aligned"


def test_normalize_rejects_unknown():
    with pytest.raises(ValueError, match="unknown ref_position"):
        normalize_ref_position("sideways")


def test_modes_last_is_subject_and_earlier_are_scene():
    assert ref_position_modes(2, "beside", "aligned") == ("aligned", "beside")
    assert ref_position_modes(1, "beside", "bag") == ("beside",)
    assert ref_position_modes(0) == ()


def test_beside_shifts_columns_right_of_the_target_and_keeps_rows():
    ids = _grid_ids(1, 3, 3)
    [placed] = place_refs([ids], ["beside"], grid_h=4, grid_w=5)
    assert placed[..., 2].min().item() == 5.0
    assert placed[..., 2].max().item() == 7.0
    assert torch.equal(placed[..., 1], ids[..., 1])
    assert torch.equal(placed[..., 0], ids[..., 0])


def test_two_besides_do_not_overlap():
    a, b = _grid_ids(1, 2, 2), _grid_ids(2, 2, 2)
    pa, pb = place_refs([a, b], ["beside", "beside"], grid_h=4, grid_w=4)
    assert pb[..., 2].min().item() == pa[..., 2].max().item() + 1.0


def test_aligned_ref_is_returned_unchanged():
    ids = _grid_ids(1, 3, 3)
    [placed] = place_refs([ids], ["aligned"], grid_h=4, grid_w=4)
    assert placed is ids


def test_bag_collapses_a_ref_to_the_target_centre():
    [placed] = place_refs([_grid_ids(1, 3, 3)], ["bag"], grid_h=5, grid_w=7)
    assert (placed[..., 1] == 2.0).all()
    assert (placed[..., 2] == 3.0).all()


def test_stream_geometry_beside_moves_only_the_subject_ref_span():
    m = _build_ready()
    latent = torch.randn(1, 4, 8, 8)
    refs = [torch.randn(1, 4, 8, 8), torch.randn(1, 4, 8, 8)]
    txt_len = 3
    _, aligned, _ = m.build_stream_inputs(latent, txt_len=txt_len, ref_latents=refs)
    pos, _ = m._stream_geometry(latent, txt_len, None, refs, ("aligned", "beside"))
    scene = slice(txt_len, txt_len + 16)
    subject = slice(txt_len + 16, txt_len + 32)
    assert torch.equal(pos[:, scene], aligned[:, scene])
    assert pos[:, subject, 2].min().item() == 4.0
    assert torch.equal(pos[:, txt_len + 32:], aligned[:, txt_len + 32:])


def test_forward_default_placement_is_bit_identical_to_omitting_it():
    m = _build_ready()
    x = torch.randn(1, 4, 8, 8)
    ref = torch.randn(1, 4, 8, 8)
    te = torch.randn(1, 5, 3, 16)
    t = torch.tensor([0.5])
    plain = m(x, t, te, ref_latents=ref)
    explicit = m(x, t, te, ref_latents=ref, ref_position="aligned", ref_position_a="aligned")
    assert torch.equal(plain, explicit)


def test_forward_beside_changes_the_output():
    torch.manual_seed(1)
    m = _build_ready()
    x = torch.randn(1, 4, 8, 8)
    ref = torch.randn(1, 4, 8, 8)
    te = torch.randn(1, 5, 3, 16)
    t = torch.tensor([0.5])
    aligned = m(x, t, te, ref_latents=ref)
    beside = m(x, t, te, ref_latents=ref, ref_position="beside")
    assert beside.shape == aligned.shape
    assert torch.isfinite(beside).all()
    assert not torch.allclose(aligned, beside)


def test_a_changed_placement_is_never_answered_from_the_run_cache():
    m = _build_ready()
    x = torch.randn(1, 4, 8, 8)
    ref = torch.randn(1, 4, 8, 8)
    te = torch.randn(1, 5, 3, 16)
    t = torch.tensor([0.5])
    want_aligned = m(x, t, te, ref_latents=ref)
    want_beside = m(x, t, te, ref_latents=ref, ref_position="beside")
    assert not torch.equal(want_aligned, want_beside)

    scope = NativeGenerator._run_cache(SimpleNamespace(dit=NativeModel("diffusion_model", m)))
    with scope:
        assert torch.equal(m(x, t, te, ref_latents=ref), want_aligned)
        assert torch.equal(m(x, t, te, ref_latents=ref, ref_position="beside"), want_beside)
