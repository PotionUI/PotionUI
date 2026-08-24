"""Tests for Normalized Attention Guidance (NAG, arXiv:2505.21179) on
the Krea-2 SingleStream MMDiT.

Krea-2 has no separate cross-attention module (unlike Wan/LTX) -- its block
is JOINT self-attention over ``[text | image]``, so NAG is integrated by
re-attending the SAME (already rope'd) image queries against a second K/V
built from ``[negative_text | same image tokens]`` (see
``Attention.forward``/``SingleStreamBlock.forward`` in ``layers.py`` and
``Krea2.run_blocks``/``forward`` in ``model.py``). The blend itself reuses
``apply_nag`` (``src/platform/runtime/native/nag.py``) unchanged -- only the
attachment point is new.

Coverage:
  (a) the no-nag path is BYTE-IDENTICAL to the pre-change code (fetched via
      ``git show HEAD:...``, git stays read-only);
  (b) ``nag=None`` / ``scale<=1.0`` / no ``nag_context`` are all no-ops;
  (c) an active scale actually changes the output;
  (d) ``alpha=0.0`` reconstructs the exact positive-only output (proves the
      attention-level blend/reassembly is wired correctly, not just "some
      numbers moved");
  (e) mismatched negative-prompt length + padding + multi-block runs stay
      finite.
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

# Mirrors test_krea2_model.py's TINY fixture: headdim 16 -> rope_axes [4,6,6].
TINY = {
    "image_model": "krea2", "features": 32, "heads": 2, "kvheads": 1,
    "channels": 4, "layers": 1, "multiplier": 1, "tdim": 16, "txtdim": 16,
    "txtheads": 2, "txtkvheads": 2, "txtlayers": 3, "patch": 2, "theta": 1000.0,
}
TINY_2LAYER = {**TINY, "layers": 2}


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
    """Import the pre-change ``model.py`` (as of ``HEAD``) to prove the
    no-nag path is byte-identical to the actual historical source, not a
    trusted diff read. Mirrors test_krea2_edit.py's identical helper."""
    src = subprocess.check_output(
        ["git", "show", f"HEAD:{_MODEL_PATH}"], cwd=_REPO_ROOT, text=True,
    )
    name = "krea2_model_head_be148_reference"
    spec = importlib.util.spec_from_loader(name, loader=None)
    module = importlib.util.module_from_spec(spec)
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


def _inputs(seed=0, txt_len=5, neg_txt_len=5):
    g = torch.Generator().manual_seed(seed)
    x = torch.randn(1, 4, 8, 8, generator=g)
    te_hidden = torch.randn(1, txt_len, 3, 16, generator=g)
    neg_hidden = torch.randn(1, neg_txt_len, 3, 16, generator=g)
    t = torch.tensor([0.5])
    return x, t, te_hidden, neg_hidden


# --- (a) inert path is byte-identical to HEAD ------------------------------

def test_no_nag_forward_matches_head_bit_exact(head_krea2_model):
    torch.manual_seed(0)
    m = _build_ready(TINY)
    head_cls = head_krea2_model.Krea2
    m_head = head_cls.from_config(TINY, _fp32_ops())
    load_into_module(m_head, m.state_dict(), match_model_spec(TINY))
    m_head.eval()

    x, t, te_hidden, _ = _inputs()
    out_new = m(x, t, te_hidden)
    out_head = m_head(x, t, te_hidden)
    assert torch.equal(out_new, out_head)


# --- (b) no-op gates --------------------------------------------------------

def test_absent_nag_context_is_default_forward():
    m = _build_ready(TINY)
    x, t, te_hidden, _ = _inputs()
    out_default = m(x, t, te_hidden)
    out_explicit_none = m(x, t, te_hidden, nag_context=None, nag=None)
    assert torch.equal(out_default, out_explicit_none)


def test_scale_one_is_noop():
    m = _build_ready(TINY)
    x, t, te_hidden, neg_hidden = _inputs()
    baseline = m(x, t, te_hidden)
    out = m(x, t, te_hidden, nag_context=neg_hidden, nag={"scale": 1.0})
    assert torch.equal(out, baseline)


def test_nag_context_without_nag_dict_is_noop():
    m = _build_ready(TINY)
    x, t, te_hidden, neg_hidden = _inputs()
    baseline = m(x, t, te_hidden)
    out = m(x, t, te_hidden, nag_context=neg_hidden, nag=None)
    assert torch.equal(out, baseline)


# --- (c)/(d) active NAG changes output, alpha=0 reconstructs positive-only --

def test_active_scale_changes_output():
    m = _build_ready(TINY)
    x, t, te_hidden, neg_hidden = _inputs()
    baseline = m(x, t, te_hidden)
    out = m(x, t, te_hidden, nag_context=neg_hidden, nag={"scale": 2.0, "tau": 3.5, "alpha": 0.5})
    assert not torch.equal(out, baseline)
    assert torch.isfinite(out).all()


def test_alpha_zero_reconstructs_positive_only_output_bit_exact():
    # apply_nag(pos, neg, scale, tau, alpha=0.0) == pos exactly (see
    # src/platform/runtime/native/nag.py) -- proves the attention-level
    # slice/blend/reassembly is wired correctly, not just "different".
    m = _build_ready(TINY)
    x, t, te_hidden, neg_hidden = _inputs()
    baseline = m(x, t, te_hidden)
    out = m(x, t, te_hidden, nag_context=neg_hidden, nag={"scale": 5.0, "tau": 0.1, "alpha": 0.0})
    assert torch.equal(out, baseline)


def test_higher_scale_diverges_further_from_baseline():
    m = _build_ready(TINY)
    x, t, te_hidden, neg_hidden = _inputs()
    baseline = m(x, t, te_hidden)
    out_low = m(x, t, te_hidden, nag_context=neg_hidden, nag={"scale": 1.2, "tau": 3.5, "alpha": 0.5})
    out_high = m(x, t, te_hidden, nag_context=neg_hidden, nag={"scale": 4.0, "tau": 3.5, "alpha": 0.5})
    d_low = (out_low - baseline).abs().sum()
    d_high = (out_high - baseline).abs().sum()
    assert d_high > d_low


# --- (e) robustness: mismatched length / padding / multi-block ------------

def test_negative_prompt_shorter_than_positive_runs_finite():
    m = _build_ready(TINY)
    x, t, te_hidden, neg_hidden = _inputs(txt_len=6, neg_txt_len=2)
    out = m(x, t, te_hidden, nag_context=neg_hidden, nag={"scale": 2.0})
    assert out.shape == x.shape
    assert torch.isfinite(out).all()


def test_negative_prompt_longer_than_positive_runs_finite():
    m = _build_ready(TINY)
    x, t, te_hidden, neg_hidden = _inputs(txt_len=2, neg_txt_len=6)
    out = m(x, t, te_hidden, nag_context=neg_hidden, nag={"scale": 2.0})
    assert out.shape == x.shape
    assert torch.isfinite(out).all()


def test_nag_attention_mask_padding_runs_finite():
    m = _build_ready(TINY)
    x, t, te_hidden, neg_hidden = _inputs(txt_len=5, neg_txt_len=5)
    mask = torch.ones(1, 5, dtype=torch.long)
    mask[0, 3:] = 0
    out = m(x, t, te_hidden, nag_context=neg_hidden, nag={"scale": 2.0}, nag_attention_mask=mask)
    assert torch.isfinite(out).all()


def test_nag_attention_mask_all_valid_matches_no_mask():
    m = _build_ready(TINY)
    x, t, te_hidden, neg_hidden = _inputs()
    nag = {"scale": 2.0}
    out_none = m(x, t, te_hidden, nag_context=neg_hidden, nag=nag)
    out_ones = m(x, t, te_hidden, nag_context=neg_hidden, nag=nag,
                 nag_attention_mask=torch.ones(1, neg_hidden.shape[1], dtype=torch.long))
    assert torch.equal(out_none, out_ones)


def test_multi_block_nag_runs_finite_and_differs_from_baseline():
    m = _build_ready(TINY_2LAYER)
    x, t, te_hidden, neg_hidden = _inputs()
    baseline = m(x, t, te_hidden)
    out = m(x, t, te_hidden, nag_context=neg_hidden, nag={"scale": 2.0})
    assert torch.isfinite(out).all()
    assert not torch.equal(out, baseline)


def test_batched_nag_runs_finite():
    m = _build_ready(TINY)
    x = torch.randn(2, 4, 8, 8)
    te_hidden = torch.randn(2, 5, 3, 16)
    neg_hidden = torch.randn(2, 4, 3, 16)
    out = m(x, torch.tensor([0.5]), te_hidden, nag_context=neg_hidden, nag={"scale": 2.0})
    assert out.shape == x.shape
    assert torch.isfinite(out).all()


# --- orthogonality vs ref_boost --------------------------------

def test_nag_and_ref_boost_together_runs_finite():
    m = _build_ready(TINY)
    x, t, te_hidden, neg_hidden = _inputs()
    ref = torch.randn(1, 4, 8, 8)
    out = m(x, t, te_hidden, ref_latents=ref, ref_boost=2.0,
            nag_context=neg_hidden, nag={"scale": 2.0})
    assert out.shape == x.shape
    assert torch.isfinite(out).all()
