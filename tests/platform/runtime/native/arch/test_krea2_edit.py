"""Tests for Krea-2 instruction-based edit (optional ``ref_latents``).

Coverage:
  (a) no-ref path is BYTE-IDENTICAL to the pre-change code, exercised against
      the ACTUAL pre-change source (fetched via ``git show HEAD:...``, never a
      checkout) rather than trusted from a diff read;
  (b) sequence-length accounting: [text | src | tgt] in, only tgt tokens out;
  (c) source tokens get RoPE frame index 1 (2 for a second source), never 0;
  (d) a mismatched ref grid raises rather than silently misaligning;
  (e) the attention-mask path also accounts for the prepended ref span.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest
import torch

from src.platform.runtime.native.arch.krea2.model import Krea2
from src.platform.runtime.native.base import load_into_module
from src.platform.runtime.native.detect.registry import match_model_spec
from vendor.gpl.comfyui.ops import pick_operations

_REPO_ROOT = Path(__file__).resolve().parents[5]
_MODEL_PATH = "src/platform/runtime/native/arch/krea2/model.py"

# Tiny Krea-2: headdim 16 -> rope_axes [4,6,6] (sum 16); GQA 2:1. Mirrors
# test_krea2_model.py's TINY fixture.
TINY = {
    "image_model": "krea2", "features": 32, "heads": 2, "kvheads": 1,
    "channels": 4, "layers": 1, "multiplier": 1, "tdim": 16, "txtdim": 16,
    "txtheads": 2, "txtkvheads": 2, "txtlayers": 3, "patch": 2, "theta": 1000.0,
}


def _fp32_ops():
    return pick_operations(torch.float32, torch.float32)


def _randomised_state_dict(module) -> dict[str, torch.Tensor]:
    sd: dict[str, torch.Tensor] = {}
    for k, v in module.state_dict().items():
        if k.endswith(".scale") or k.endswith(".mod.lin") or k.endswith(".modulation.lin"):
            sd[k] = torch.zeros_like(v)
        elif v.is_floating_point():
            sd[k] = torch.randn_like(v) * 0.02
        else:
            sd[k] = v.clone()
    return sd


def _build_ready(config) -> Krea2:
    m = Krea2.from_config(config, _fp32_ops())
    load_into_module(m, _randomised_state_dict(m), match_model_spec(config))
    m.eval()
    return m


def _load_head_krea2_model():
    """Import the pre-change ``model.py`` (as of ``HEAD``) as its own module,
    with relative imports resolved against the REAL (unchanged) sibling
    modules (``config.py``/``layers.py``/``base.py``) — proves byte-identity
    against the actual historical source, not a trusted diff read. Git stays
    read-only: no checkout, no stash, just ``git show``."""
    src = subprocess.check_output(
        ["git", "show", f"HEAD:{_MODEL_PATH}"], cwd=_REPO_ROOT, text=True,
    )
    name = "krea2_model_head_be104_reference"
    spec = importlib.util.spec_from_loader(name, loader=None)
    module = importlib.util.module_from_spec(spec)
    # Three dots in the source (`from ...base import ...`) resolve relative to
    # this package, exactly like the real module — so the relative imports
    # inside HEAD's source hit the same (unchanged) real config/layers/base.
    module.__package__ = "src.platform.runtime.native.arch.krea2"
    sys.modules[name] = module
    exec(compile(src, f"<git show HEAD:{_MODEL_PATH}>", "exec"), module.__dict__)
    return module


@pytest.fixture(scope="module")
def head_krea2_model():
    try:
        yield _load_head_krea2_model()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"could not load HEAD's model.py via git show: {exc}")


def test_no_ref_forward_matches_head_bit_exact(head_krea2_model):
    torch.manual_seed(7)
    config = TINY
    m_new = Krea2.from_config(config, _fp32_ops())
    sd = _randomised_state_dict(m_new)
    load_into_module(m_new, sd, match_model_spec(config))
    m_new.eval()

    m_head = head_krea2_model.Krea2.from_config(config, _fp32_ops())
    load_into_module(m_head, {k: v.clone() for k, v in sd.items()}, match_model_spec(config))
    m_head.eval()

    x = torch.randn(2, 4, 8, 12)
    te_hidden = torch.randn(2, 4, 3, 16)
    t = torch.tensor([0.3, 0.7])

    out_new = m_new(x, t, te_hidden)
    out_head = m_head(x, t, te_hidden)
    assert torch.equal(out_new, out_head)


def test_no_ref_build_stream_inputs_matches_head_bit_exact(head_krea2_model):
    torch.manual_seed(8)
    m_new = _build_ready(TINY)
    m_head = head_krea2_model.Krea2.from_config(TINY, _fp32_ops())
    load_into_module(m_head, {k: v.clone() for k, v in m_new.state_dict().items()}, match_model_spec(TINY))
    m_head.eval()

    latent = torch.randn(2, 4, 8, 8)
    img_new, pos_new, mask_new = m_new.build_stream_inputs(latent, txt_len=5)
    img_head, pos_head, mask_head = m_head.build_stream_inputs(latent, txt_len=5)
    assert torch.equal(img_new, img_head)
    assert torch.equal(pos_new, pos_head)
    assert mask_new is None and mask_head is None


# --- (b) sequence-length accounting ----------------------------------------

def test_build_stream_inputs_with_ref_prepends_and_extends_pos():
    m = _build_ready(TINY)
    latent = torch.randn(1, 4, 8, 8)          # 4x4 = 16 target tokens
    ref = torch.randn(1, 4, 8, 8)              # same grid -> 16 ref tokens
    txt_len = 5

    img, pos, mask = m.build_stream_inputs(latent, txt_len=txt_len, ref_latents=ref)
    assert img.shape == (1, 16 + 16, 4 * 2 * 2)   # [ref | target] raw patches
    assert pos.shape == (1, txt_len + 16 + 16, 3)  # [text | ref | target] ids
    assert mask is None


def test_run_blocks_ref_len_slices_only_target_tokens():
    m = _build_ready(TINY)
    latent = torch.randn(1, 4, 8, 8)
    ref = torch.randn(1, 4, 8, 8)
    txt_len = 5
    te_hidden = torch.randn(1, txt_len, 3, 16)

    img, pos, mask = m.build_stream_inputs(latent, txt_len=txt_len, ref_latents=ref)
    t_emb, tvec = m.prepare_timestep(torch.tensor([0.5]), torch.float32)
    context = m.prepare_context(te_hidden, mask)
    out = m.run_blocks(img, context, t_emb, tvec, pos, mask, ref_len=16)
    # output token count == target only (16), never target+ref (32).
    assert out.shape == (1, 16, 4 * 2 * 2)


def test_forward_with_ref_latents_returns_target_shape():
    m = _build_ready(TINY)
    x = torch.randn(1, 4, 8, 8)
    ref = torch.randn(1, 4, 8, 8)
    te_hidden = torch.randn(1, 5, 3, 16)
    out = m(x, torch.tensor([0.5]), te_hidden, ref_latents=ref)
    assert out.shape == x.shape
    assert torch.isfinite(out).all()


def test_forward_with_ref_latents_list_two_sources():
    m = _build_ready(TINY)
    x = torch.randn(1, 4, 8, 8)
    refs = [torch.randn(1, 4, 8, 8), torch.randn(1, 4, 8, 8)]
    te_hidden = torch.randn(1, 5, 3, 16)
    out = m(x, torch.tensor([0.5]), te_hidden, ref_latents=refs)
    assert out.shape == x.shape
    assert torch.isfinite(out).all()


def test_forward_with_ref_latents_5d_squeezes_like_target():
    m = _build_ready(TINY)
    x = torch.randn(1, 4, 1, 8, 8)          # causal-3D VAE shape
    ref = torch.randn(1, 4, 1, 8, 8)
    te_hidden = torch.randn(1, 5, 3, 16)
    out = m(x, torch.tensor([0.5]), te_hidden, ref_latents=ref)
    assert out.shape == x.shape


def test_ref_latents_changes_output_vs_no_ref():
    m = _build_ready(TINY)
    x = torch.randn(1, 4, 8, 8)
    ref = torch.randn(1, 4, 8, 8)
    te_hidden = torch.randn(1, 5, 3, 16)
    t = torch.tensor([0.5])
    out_no_ref = m(x, t, te_hidden)
    out_ref = m(x, t, te_hidden, ref_latents=ref)
    assert not torch.allclose(out_no_ref, out_ref)


# --- (c) frame ids -----------------------------------------------------

def test_ref_tokens_get_frame_index_one_not_zero():
    m = _build_ready(TINY)
    latent = torch.randn(1, 4, 8, 8)
    ref = torch.randn(1, 4, 8, 8)
    txt_len = 3

    _, pos, _ = m.build_stream_inputs(latent, txt_len=txt_len, ref_latents=ref)
    ref_span = pos[:, txt_len : txt_len + 16, :]
    target_span = pos[:, txt_len + 16 :, :]
    assert (ref_span[..., 0] == 1.0).all()
    assert (target_span[..., 0] == 0.0).all()


def test_two_ref_sources_get_frames_one_and_two():
    m = _build_ready(TINY)
    latent = torch.randn(1, 4, 8, 8)
    refs = [torch.randn(1, 4, 8, 8), torch.randn(1, 4, 8, 8)]
    txt_len = 3

    _, pos, _ = m.build_stream_inputs(latent, txt_len=txt_len, ref_latents=refs)
    first_ref = pos[:, txt_len : txt_len + 16, 0]
    second_ref = pos[:, txt_len + 16 : txt_len + 32, 0]
    target = pos[:, txt_len + 32 :, 0]
    assert (first_ref == 1.0).all()
    assert (second_ref == 2.0).all()
    assert (target == 0.0).all()


def test_ref_row_col_ids_match_target_grid():
    """The ref span's own (row, col) ids mirror the target grid layout (only
    the frame axis differs) -- both are h_=4, w_=4 for an 8x8/patch-2 latent."""
    m = _build_ready(TINY)
    latent = torch.randn(1, 4, 8, 8)
    ref = torch.randn(1, 4, 8, 8)
    txt_len = 3

    _, pos, _ = m.build_stream_inputs(latent, txt_len=txt_len, ref_latents=ref)
    ref_rowcol = pos[:, txt_len : txt_len + 16, 1:]
    target_rowcol = pos[:, txt_len + 16 :, 1:]
    assert torch.equal(ref_rowcol, target_rowcol)


# --- (d) mismatched ref grid --------------------------------------------

def test_oversized_ref_grid_raises():
    m = _build_ready(TINY)
    latent = torch.randn(1, 4, 8, 8)
    ref = torch.randn(1, 4, 16, 16)   # ref grid LARGER than the target
    with pytest.raises(ValueError, match="exceeds the target grid"):
        m.build_stream_inputs(latent, txt_len=3, ref_latents=ref)


# --- (d2) smaller ref grid -> centered-offset "fit" positioning ----

def test_smaller_ref_grid_gets_centered_offset_ids():
    m = _build_ready(TINY)
    latent = torch.randn(1, 4, 16, 16)   # target grid 8x8 (patch 2)
    ref = torch.randn(1, 4, 8, 8)         # ref grid 4x4 -> centered offset (8-4)//2 = 2
    txt_len = 3
    img, pos, _ = m.build_stream_inputs(latent, txt_len=txt_len, ref_latents=ref)
    assert img.shape[1] == 16 + 64        # 4*4 ref tokens + 8*8 target tokens
    ref_pos = pos[:, txt_len:txt_len + 16, :]
    assert (ref_pos[..., 0] == 1.0).all()
    rows = ref_pos[0, :, 1].reshape(4, 4)
    cols = ref_pos[0, :, 2].reshape(4, 4)
    assert rows[:, 0].tolist() == [2.0, 3.0, 4.0, 5.0]
    assert cols[0, :].tolist() == [2.0, 3.0, 4.0, 5.0]


def test_smaller_ref_grid_forward_runs():
    m = _build_ready(TINY)
    x = torch.randn(1, 4, 16, 16)
    ref = torch.randn(1, 4, 8, 8)
    te_hidden = torch.randn(1, 5, 3, 16)
    out = m(x, torch.tensor([0.5]), te_hidden, ref_latents=ref)
    assert out.shape == x.shape
    assert torch.isfinite(out).all()


# --- (e) mask accounts for the ref span ---------------------------------

def test_build_stream_inputs_with_ref_and_padded_text_mask():
    m = _build_ready(TINY)
    latent = torch.randn(1, 4, 8, 8)
    ref = torch.randn(1, 4, 8, 8)
    txt_len = 5
    pad = torch.ones(1, txt_len, dtype=torch.long)
    pad[0, 3:] = 0

    img, pos, mask = m.build_stream_inputs(latent, txt_len=txt_len, txt_mask=pad, ref_latents=ref)
    assert mask is not None
    assert mask.shape == (1, txt_len + 16 + 16)
    assert mask[0, :txt_len].tolist() == [True, True, True, False, False]
    assert mask[0, txt_len:].all()  # ref + target tokens always valid


def test_build_stream_inputs_with_ref_all_valid_mask_shortcircuits():
    m = _build_ready(TINY)
    latent = torch.randn(1, 4, 8, 8)
    ref = torch.randn(1, 4, 8, 8)
    _, _, mask = m.build_stream_inputs(
        latent, txt_len=5, txt_mask=torch.ones(1, 5, dtype=torch.long), ref_latents=ref,
    )
    assert mask is None


# --- (f) ref_boost attention bias ------------------------------
#
# The reference-fidelity dial: an additive log-space target->reference
# attention bias. Defaults (1.0/1.0) MUST build no bias at all so the fast
# sage/flash attention path survives; a boost != 1.0 biases only the
# target->ref block, aligned last-ref = subject.

import math

from src.platform.runtime.native.arch.krea2.layers import ref_attn_bias


def test_ref_attn_bias_none_at_neutral_dials():
    assert ref_attn_bias([1.0], txt_len=2, ref_lens=[3], tgt_len=4,
                          device=torch.device("cpu"), dtype=torch.float32) is None
    assert ref_attn_bias([1.0, 1.0], txt_len=2, ref_lens=[3, 2], tgt_len=4,
                          device=torch.device("cpu"), dtype=torch.float32) is None


def test_ref_attn_bias_single_ref_hand_computed():
    txt_len, ref_lens, tgt_len, boost = 2, [3], 4, 2.0
    bias = ref_attn_bias([boost], txt_len, ref_lens, tgt_len,
                         device=torch.device("cpu"), dtype=torch.float32)
    L = txt_len + sum(ref_lens) + tgt_len
    assert bias.shape == (1, 1, L, L)
    rows0 = txt_len + sum(ref_lens)          # target rows start here (5)
    expected = torch.zeros(1, 1, L, L)
    expected[:, :, rows0:, txt_len:txt_len + ref_lens[0]] = math.log(boost)
    assert torch.equal(bias, expected)
    # nothing outside the target->ref block is touched.
    assert bias[:, :, :rows0, :].abs().sum() == 0.0
    assert bias[:, :, rows0:, :txt_len].abs().sum() == 0.0     # target->text untouched
    assert bias[:, :, rows0:, rows0:].abs().sum() == 0.0       # target->target untouched


def test_ref_attn_bias_multi_ref_scene_and_subject_columns():
    # boosts list is [scene(ref_boost_a), subject(ref_boost)] in sequence order.
    txt_len, ref_lens, tgt_len = 2, [3, 2], 4
    scene_b, subject_b = 3.0, 2.0
    bias = ref_attn_bias([scene_b, subject_b], txt_len, ref_lens, tgt_len,
                         device=torch.device("cpu"), dtype=torch.float32)
    rows0 = txt_len + sum(ref_lens)          # 7
    scene0, scene1 = txt_len, txt_len + ref_lens[0]           # cols 2..5
    subj0, subj1 = scene1, scene1 + ref_lens[1]              # cols 5..7
    assert torch.allclose(bias[:, :, rows0:, scene0:scene1],
                          torch.full((1, 1, tgt_len, ref_lens[0]), math.log(scene_b)))
    assert torch.allclose(bias[:, :, rows0:, subj0:subj1],
                          torch.full((1, 1, tgt_len, ref_lens[1]), math.log(subject_b)))


def test_maybe_ref_bias_none_at_defaults_keeps_sage_path():
    m = _build_ready(TINY)
    refs = [torch.randn(1, 4, 8, 8)]
    # default dials -> no bias object exists at all (the sage-safe invariant).
    assert m._maybe_ref_bias(refs, txt_len=5, tgt_len=16, ref_boost=1.0, ref_boost_a=1.0,
                             device=torch.device("cpu"), dtype=torch.float32) is None


def test_maybe_ref_bias_subject_is_last_ref():
    m = _build_ready(TINY)
    refs = [torch.randn(1, 4, 8, 8), torch.randn(1, 4, 8, 8)]   # [scene, subject]
    bias = m._maybe_ref_bias(refs, txt_len=3, tgt_len=16, ref_boost=2.0, ref_boost_a=4.0,
                             device=torch.device("cpu"), dtype=torch.float32)
    rows0 = 3 + 16 + 16
    scene_cols = slice(3, 3 + 16)
    subj_cols = slice(3 + 16, 3 + 32)
    assert torch.allclose(bias[:, :, rows0:, scene_cols][0, 0, 0, 0], torch.tensor(math.log(4.0)))
    assert torch.allclose(bias[:, :, rows0:, subj_cols][0, 0, 0, 0], torch.tensor(math.log(2.0)))


def test_forward_default_boost_is_bit_identical_to_no_boost_kwarg():
    m = _build_ready(TINY)
    x = torch.randn(1, 4, 8, 8)
    ref = torch.randn(1, 4, 8, 8)
    te_hidden = torch.randn(1, 5, 3, 16)
    t = torch.tensor([0.5])
    out_plain = m(x, t, te_hidden, ref_latents=ref)
    out_default_boost = m(x, t, te_hidden, ref_latents=ref, ref_boost=1.0, ref_boost_a=1.0)
    assert torch.equal(out_plain, out_default_boost)


def test_forward_active_boost_changes_output():
    m = _build_ready(TINY)
    x = torch.randn(1, 4, 8, 8)
    ref = torch.randn(1, 4, 8, 8)
    te_hidden = torch.randn(1, 5, 3, 16)
    t = torch.tensor([0.5])
    out_plain = m(x, t, te_hidden, ref_latents=ref)
    out_boosted = m(x, t, te_hidden, ref_latents=ref, ref_boost=4.0)
    assert not torch.allclose(out_plain, out_boosted)


def test_forward_boost_without_ref_is_noop():
    # boost dials with no ref_latents must not build a bias (nothing to boost).
    m = _build_ready(TINY)
    x = torch.randn(1, 4, 8, 8)
    te_hidden = torch.randn(1, 5, 3, 16)
    t = torch.tensor([0.5])
    out_plain = m(x, t, te_hidden)
    out_boost = m(x, t, te_hidden, ref_boost=4.0, ref_boost_a=2.0)
    assert torch.equal(out_plain, out_boost)


def test_forward_boost_with_padded_text_mask_runs_finite():
    # boost bias + a genuinely padded text mask must combine (bias forces sdpa,
    # padding stays masked) and still produce finite target tokens.
    m = _build_ready(TINY)
    x = torch.randn(1, 4, 8, 8)
    ref = torch.randn(1, 4, 8, 8)
    te_hidden = torch.randn(1, 5, 3, 16)
    pad = torch.ones(1, 5, dtype=torch.long)
    pad[0, 3:] = 0
    out = m(x, torch.tensor([0.5]), te_hidden, ref_latents=ref, ref_boost=3.0,
            attention_mask=pad)
    assert out.shape == x.shape
    assert torch.isfinite(out).all()


# --- (g) ref_boost REGION MASK ---------------------------------
#
# comfyui-krea2edit's `_ref_attn_bias` MASK branch, ported: the LAST ref's
# boost (the same ref `ref_boost` targets) can be restricted to a painted
# region instead of applying to the whole reference. The mask is area-
# interpolated down to that ref's own token grid, then thresholded at
# `> 0.5` -- a HARD threshold, never a soft/continuous weight. No mask is
# BYTE-IDENTICAL to the scalar-only path (proved against HEAD's
# actual pre-change layers.py, not a trusted diff read).

_LAYERS_PATH = "src/platform/runtime/native/arch/krea2/layers.py"


def _load_head_ref_attn_bias():
    src = subprocess.check_output(
        ["git", "show", f"HEAD:{_LAYERS_PATH}"], cwd=_REPO_ROOT, text=True,
    )
    name = "krea2_layers_head_be121_reference"
    spec = importlib.util.spec_from_loader(name, loader=None)
    module = importlib.util.module_from_spec(spec)
    module.__package__ = "src.platform.runtime.native.arch.krea2"
    sys.modules[name] = module
    exec(compile(src, f"<git show HEAD:{_LAYERS_PATH}>", "exec"), module.__dict__)
    return module.ref_attn_bias


def test_ref_attn_bias_scalar_path_matches_head_bit_exact():
    try:
        head_ref_attn_bias = _load_head_ref_attn_bias()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"could not load HEAD's layers.py via git show: {exc}")
    cases = [
        ([2.0], 2, [3], 4),
        ([3.0, 2.0], 2, [3, 2], 4),
        ([1.0, 1.0], 2, [3, 2], 4),
        ([1.0], 0, [4], 2),
    ]
    for boosts, txt_len, ref_lens, tgt_len in cases:
        old = head_ref_attn_bias(boosts, txt_len, ref_lens, tgt_len,
                                 device=torch.device("cpu"), dtype=torch.float32)
        new = ref_attn_bias(boosts, txt_len, ref_lens, tgt_len,
                            device=torch.device("cpu"), dtype=torch.float32)
        if old is None:
            assert new is None
        else:
            assert torch.equal(old, new)


def test_ref_attn_bias_mask_synthetic_4x4_half_covering():
    # 4x4 = 16 token ref, mask covers the LEFT HALF of the grid (columns 0-1
    # of every row) at the ref's own native resolution -- no interpolation
    # needed, so the selected columns are exact.
    txt_len, ref_lens, tgt_len = 1, [16], 4
    ref_grids = [(4, 4)]
    boost = 2.0
    mask = torch.zeros(4, 4)
    mask[:, :2] = 1.0
    bias = ref_attn_bias([boost], txt_len, ref_lens, tgt_len,
                         device=torch.device("cpu"), dtype=torch.float32,
                         ref_grids=ref_grids, boost_mask=mask)
    off = txt_len
    rows0 = txt_len + sum(ref_lens)
    expected_cols = {off + r * 4 + c for r in range(4) for c in range(2)}
    for col in range(off, off + 16):
        val = bias[0, 0, rows0, col].item()
        if col in expected_cols:
            assert val == pytest.approx(math.log(boost))
        else:
            assert val == 0.0
    # every target row sees the identical bias (broadcast, not per-row).
    assert torch.equal(bias[:, :, rows0, :], bias[:, :, rows0 + 1, :])
    # nothing outside the target->ref block is touched.
    assert bias[:, :, :rows0, :].abs().sum() == 0.0


def test_ref_attn_bias_mask_only_applies_to_last_ref():
    # Two refs, both boosted: the mask must restrict ONLY the subject (last
    # ref) -- the scene (first ref) gets its full, unmasked boost regardless.
    txt_len, ref_lens, tgt_len = 0, [4, 4], 2
    ref_grids = [(2, 2), (2, 2)]
    scene_b, subject_b = 3.0, 2.0
    mask = torch.zeros(2, 2)
    mask[0, 0] = 1.0   # a single covered token in the subject's grid
    bias = ref_attn_bias([scene_b, subject_b], txt_len, ref_lens, tgt_len,
                         device=torch.device("cpu"), dtype=torch.float32,
                         ref_grids=ref_grids, boost_mask=mask)
    rows0 = txt_len + sum(ref_lens)
    scene_cols = slice(txt_len, txt_len + 4)
    subj_cols = slice(txt_len + 4, txt_len + 8)
    # scene: every column boosted (mask never applies to a non-last ref).
    assert torch.allclose(bias[:, :, rows0:, scene_cols],
                          torch.full((1, 1, tgt_len, 4), math.log(scene_b)))
    # subject: only the single masked token (index 0) is boosted.
    subj_vals = bias[0, 0, rows0, subj_cols]
    assert subj_vals[0].item() == pytest.approx(math.log(subject_b))
    assert torch.equal(subj_vals[1:], torch.zeros(3))


def test_ref_attn_bias_mask_without_ref_grids_falls_back_to_full_boost():
    # boost_mask given but ref_grids omitted -> the mask cannot be resolved
    # (no (h, w) to interpolate against), so the ref falls back to the plain
    # full-column scalar path rather than crashing.
    txt_len, ref_lens, tgt_len = 0, [4], 2
    boost = 2.0
    mask = torch.zeros(2, 2)   # would otherwise mask everything out
    bias = ref_attn_bias([boost], txt_len, ref_lens, tgt_len,
                         device=torch.device("cpu"), dtype=torch.float32,
                         boost_mask=mask)
    scalar_bias = ref_attn_bias([boost], txt_len, ref_lens, tgt_len,
                                device=torch.device("cpu"), dtype=torch.float32)
    assert torch.equal(bias, scalar_bias)


def test_ref_attn_bias_mask_area_interpolate_nonsquare_grid():
    # 3x5 = 15 token ref; mask supplied at 6x10 (an exact 2x oversample) with
    # the first 5 of 10 columns painted. Area-interpolate averages 2x2
    # blocks: output cols 0,1 land fully inside the painted region (avg 1.0);
    # col 2 straddles the boundary (avg exactly 0.5, excluded by the STRICT
    # `> 0.5`); cols 3,4 are fully outside (avg 0.0).
    txt_len, tgt_len = 0, 2
    gh, gw = 3, 5
    ref_lens = [gh * gw]
    ref_grids = [(gh, gw)]
    boost = 3.0
    mask = torch.zeros(6, 10)
    mask[:, :5] = 1.0
    bias = ref_attn_bias([boost], txt_len, ref_lens, tgt_len,
                         device=torch.device("cpu"), dtype=torch.float32,
                         ref_grids=ref_grids, boost_mask=mask)
    rows0 = txt_len + sum(ref_lens)
    expected_cols = {txt_len + r * gw + c for r in range(gh) for c in (0, 1)}
    for col in range(txt_len, txt_len + gh * gw):
        val = bias[0, 0, rows0, col].item()
        if col in expected_cols:
            assert val == pytest.approx(math.log(boost))
        else:
            assert val == 0.0


def test_ref_attn_bias_mask_is_a_hard_threshold_not_soft_weighted():
    # A uniformly "soft" mask value (neither ~0 nor ~1) still yields the FULL
    # log(boost) wherever it exceeds 0.5, never a scaled/partial value --
    # matching upstream exactly: no continuous soft-mask support, only a
    # binary threshold after the area-interpolate downsample.
    txt_len, ref_lens, tgt_len = 0, [4], 2
    ref_grids = [(2, 2)]
    boost = 5.0
    mask = torch.full((2, 2), 0.7)
    bias = ref_attn_bias([boost], txt_len, ref_lens, tgt_len,
                         device=torch.device("cpu"), dtype=torch.float32,
                         ref_grids=ref_grids, boost_mask=mask)
    rows0 = txt_len + sum(ref_lens)
    vals = bias[0, 0, rows0, txt_len:txt_len + 4]
    assert torch.allclose(vals, torch.full((4,), math.log(boost)))


def test_ref_attn_bias_mask_below_threshold_yields_no_bias():
    txt_len, ref_lens, tgt_len = 0, [4], 2
    ref_grids = [(2, 2)]
    boost = 5.0
    mask = torch.full((2, 2), 0.3)
    bias = ref_attn_bias([boost], txt_len, ref_lens, tgt_len,
                         device=torch.device("cpu"), dtype=torch.float32,
                         ref_grids=ref_grids, boost_mask=mask)
    rows0 = txt_len + sum(ref_lens)
    vals = bias[0, 0, rows0, txt_len:txt_len + 4]
    assert torch.equal(vals, torch.zeros(4))


def test_maybe_ref_bias_forwards_mask_to_last_ref_only():
    m = _build_ready(TINY)
    refs = [torch.randn(1, 4, 8, 8), torch.randn(1, 4, 8, 8)]   # [scene, subject]
    mask = torch.zeros(4, 4)   # patch=2 on an 8x8 latent -> 4x4 token grid
    mask[:, :2] = 1.0
    bias = m._maybe_ref_bias(refs, txt_len=3, tgt_len=16, ref_boost=2.0, ref_boost_a=4.0,
                             device=torch.device("cpu"), dtype=torch.float32,
                             ref_boost_mask=mask)
    rows0 = 3 + 16 + 16
    scene_cols = slice(3, 3 + 16)
    subj_start = 3 + 16
    # scene (ref_boost_a) unaffected by the mask -- the full block is boosted.
    assert torch.allclose(bias[:, :, rows0:, scene_cols], torch.full((1, 1, 16, 16), math.log(4.0)))
    # subject (ref_boost, the last ref) is masked: only its left-half columns
    # (8 of 16 tokens, row-major over the 4x4 grid) get the boost.
    subj_bias = bias[0, 0, rows0, subj_start:subj_start + 16]
    expected = {r * 4 + c for r in range(4) for c in range(2)}
    for i in range(16):
        val = subj_bias[i].item()
        if i in expected:
            assert val == pytest.approx(math.log(2.0))
        else:
            assert val == 0.0


def test_forward_default_mask_is_bit_identical_to_no_mask_kwarg():
    m = _build_ready(TINY)
    x = torch.randn(1, 4, 8, 8)
    ref = torch.randn(1, 4, 8, 8)
    te_hidden = torch.randn(1, 5, 3, 16)
    t = torch.tensor([0.5])
    out_plain = m(x, t, te_hidden, ref_latents=ref, ref_boost=2.0)
    out_default_mask = m(x, t, te_hidden, ref_latents=ref, ref_boost=2.0, ref_boost_mask=None)
    assert torch.equal(out_plain, out_default_mask)


def test_forward_full_mask_matches_no_mask_output():
    # An all-ones mask covering the entire ref selects every token -> the
    # SAME bias (and output) as no mask at all.
    torch.manual_seed(3)
    m = _build_ready(TINY)
    x = torch.randn(1, 4, 8, 8)
    ref = torch.randn(1, 4, 8, 8)
    te_hidden = torch.randn(1, 5, 3, 16)
    t = torch.tensor([0.5])
    full_mask = torch.ones(4, 4)
    out_plain = m(x, t, te_hidden, ref_latents=ref, ref_boost=3.0)
    out_masked = m(x, t, te_hidden, ref_latents=ref, ref_boost=3.0, ref_boost_mask=full_mask)
    assert torch.equal(out_plain, out_masked)


def test_forward_empty_mask_is_a_noop_boost():
    # An all-zero mask selects nothing -> the additive bias is all-zero,
    # numerically equivalent to running with no boost at all (adding 0 to
    # softmax logits changes nothing).
    torch.manual_seed(5)
    m = _build_ready(TINY)
    x = torch.randn(1, 4, 8, 8)
    ref = torch.randn(1, 4, 8, 8)
    te_hidden = torch.randn(1, 5, 3, 16)
    t = torch.tensor([0.5])
    empty_mask = torch.zeros(4, 4)
    out_noboost = m(x, t, te_hidden, ref_latents=ref)   # default 1.0 dials -> None bias
    out_masked = m(x, t, te_hidden, ref_latents=ref, ref_boost=3.0, ref_boost_mask=empty_mask)
    assert torch.allclose(out_noboost, out_masked, atol=1e-5)


def test_forward_mask_at_different_resolution_runs_finite():
    # mask given at an arbitrary native resolution, not the (4,4) token grid
    # -- exercises the area-interpolate resize path end to end.
    m = _build_ready(TINY)
    x = torch.randn(1, 4, 8, 8)
    ref = torch.randn(1, 4, 8, 8)
    te_hidden = torch.randn(1, 5, 3, 16)
    mask = torch.zeros(32, 40)
    mask[:16, :20] = 1.0
    out = m(x, torch.tensor([0.5]), te_hidden, ref_latents=ref, ref_boost=2.0,
            ref_boost_mask=mask)
    assert out.shape == x.shape
    assert torch.isfinite(out).all()
