"""Krea-2 NAG negative pass reuses the positive pass's image K/V rows.

``Attention.forward``'s NAG branch used to project k/v over the whole
``[nag_ctx | image]`` sequence, re-doing the image rows the positive pass had
just projected. Those rows are the same input rows under the same rotary
positions (``Krea2.run_blocks`` builds ``nag_freqs`` as
``[zeros(neg_txt) | pos[txt_len:]]``), so the positive pass's post-rope k/v
image rows are reusable verbatim and only the negative TEXT rows need
projecting.

The equivalence oracle here is ``_old_attention_forward`` below: a verbatim
copy of the pre-change branch, exercised both directly on ``Attention`` and,
monkeypatched over the class, through the real ``Krea2`` entry point.
"""

from __future__ import annotations

import pytest
import torch
from einops import rearrange

from src.platform.runtime.native.arch.krea2.layers import (
    Attention,
    PositionalEncoding,
    _nag_active,
    _sdpa_gqa,
    apply_nag,
    apply_rope_inplace,
    ropeapply,
)
from src.platform.runtime.native.arch.krea2.model import Krea2
from src.platform.runtime.native.base import load_into_module
from src.platform.runtime.native.detect.registry import match_model_spec
from vendor.gpl.comfyui.ops import pick_operations
import torch.nn.functional as F

TINY = {
    "image_model": "krea2", "features": 32, "heads": 2, "kvheads": 1,
    "channels": 4, "layers": 1, "multiplier": 1, "tdim": 16, "txtdim": 16,
    "txtheads": 2, "txtkvheads": 2, "txtlayers": 3, "patch": 2, "theta": 1000.0,
}
TINY_2LAYER = {**TINY, "layers": 2}

AXDIMS = [4, 6, 6]


def _old_attention_forward(self, x, freqs=None, mask=None, nag_ctx=None, nag_freqs=None,
                           nag_mask=None, nag=None, txt_len=None):
    """Verbatim pre-change ``Attention.forward`` — the equivalence oracle."""
    q = rearrange(self.wq(x), "B L (H D) -> B H L D", H=self.heads)
    k = rearrange(self.wk(x), "B L (H D) -> B H L D", H=self.kvheads)
    v = rearrange(self.wv(x), "B L (H D) -> B H L D", H=self.kvheads)
    q = self.qknorm.qnorm(q)
    k = self.qknorm.knorm(k)
    if freqs is not None:
        q, k = ropeapply(q, k, freqs)
    out = _sdpa_gqa(q, k, v, self.heads, self.kvheads, mask)
    if nag_ctx is not None and _nag_active(nag):
        img_len = x.shape[1] - txt_len
        q_img = q[:, :, -img_len:, :]
        x_neg = torch.cat([nag_ctx, x[:, -img_len:]], dim=1)
        k_neg = rearrange(self.wk(x_neg), "B L (H D) -> B H L D", H=self.kvheads)
        v_neg = rearrange(self.wv(x_neg), "B L (H D) -> B H L D", H=self.kvheads)
        k_neg = self.qknorm.knorm(k_neg)
        if nag_freqs is not None:
            k_neg = apply_rope_inplace(k_neg, nag_freqs)
        neg_img = _sdpa_gqa(q_img, k_neg, v_neg, self.heads, self.kvheads, nag_mask)
        pos_img = out[:, -img_len:, :]
        blended = apply_nag(pos_img, neg_img, nag["scale"], nag.get("tau", 3.5), nag.get("alpha", 0.5))
        out = torch.cat([out[:, :-img_len, :], blended], dim=1)
    gate = F.sigmoid(self.gate(x))
    out = out * gate
    return self.wo(out)


def _fp64_ops():
    return pick_operations(torch.float64, torch.float64)


def _fp32_ops():
    return pick_operations(torch.float32, torch.float32)


HEADDIM = sum(AXDIMS)  # apply_rope_inplace pairs headdim/2 features against AXDIMS


def _build_attention(heads=4, kvheads=2, seed=0):
    torch.manual_seed(seed)
    attn = Attention(heads * HEADDIM, heads, kvheads, operations=_fp64_ops(), dtype=torch.float64)
    for p in attn.parameters():
        with torch.no_grad():
            p.copy_(torch.randn_like(p) * 0.2)
    return attn.eval()


def _attention_case(txt_len, img_len, neg_txt_len, batch=1, heads=4, kvheads=2,
                    with_mask=False, with_freqs=True, seed=0):
    dim = heads * HEADDIM
    attn = _build_attention(heads=heads, kvheads=kvheads, seed=seed)
    g = torch.Generator().manual_seed(seed + 100)
    x = torch.randn(batch, txt_len + img_len, dim, generator=g, dtype=torch.float64)
    nag_ctx = torch.randn(batch, neg_txt_len, dim, generator=g, dtype=torch.float64)
    freqs = nag_freqs = None
    if with_freqs:
        posemb = PositionalEncoding(AXDIMS, 1000.0, 1.0)
        pos = torch.randn(batch, txt_len + img_len, 3, generator=g, dtype=torch.float64)
        neg_pos = torch.cat([torch.zeros(batch, neg_txt_len, 3, dtype=torch.float64),
                             pos[:, txt_len:]], dim=1)
        freqs = posemb(pos)
        nag_freqs = posemb(neg_pos)
    nag_mask = None
    if with_mask:
        keep = torch.ones(batch, neg_txt_len + img_len, dtype=torch.bool)
        keep[:, neg_txt_len - 1:neg_txt_len] = False
        nag_mask = keep[:, None, None, :]
    kwargs = dict(freqs=freqs, mask=None, nag_ctx=nag_ctx, nag_freqs=nag_freqs,
                  nag_mask=nag_mask, nag={"scale": 2.5, "tau": 3.5, "alpha": 0.5},
                  txt_len=txt_len)
    return attn, x, kwargs


# --- the premise the reuse rests on ---------------------------------------

def test_nag_freqs_image_rows_are_bit_identical_to_positive_freqs():
    posemb = PositionalEncoding(AXDIMS, 1000.0, 1.0)
    txt_len, img_len, neg_txt_len = 5, 9, 3
    pos = torch.randn(2, txt_len + img_len, 3)
    neg_pos = torch.cat([torch.zeros(2, neg_txt_len, 3), pos[:, txt_len:]], dim=1)
    assert torch.equal(posemb(pos)[:, txt_len:], posemb(neg_pos)[:, neg_txt_len:])


# --- equivalence against the pre-change branch ----------------------------

@pytest.mark.parametrize(
    "txt_len,img_len,neg_txt_len,batch,heads,kvheads,with_mask",
    [
        (5, 9, 5, 1, 4, 4, False),      # equal text lengths, no GQA
        (7, 9, 3, 1, 4, 2, False),      # negative prompt shorter
        (3, 9, 7, 1, 4, 2, False),      # negative prompt longer
        (4, 12, 6, 1, 4, 2, True),      # padded negative key mask
        (4, 9, 6, 3, 4, 2, True),       # batch > 1
        (4, 9, 6, 2, 8, 1, True),       # kvheads == 1, wide GQA fan-out
    ],
)
def test_matches_old_formula(txt_len, img_len, neg_txt_len, batch, heads, kvheads, with_mask):
    attn, x, kwargs = _attention_case(txt_len, img_len, neg_txt_len, batch=batch,
                                      heads=heads, kvheads=kvheads, with_mask=with_mask)
    expected = _old_attention_forward(attn, x.clone(), **kwargs)
    actual = attn(x.clone(), **kwargs)
    assert torch.allclose(actual, expected, rtol=0, atol=1e-12)


def test_matches_old_formula_without_rope():
    attn, x, kwargs = _attention_case(4, 9, 6, with_freqs=False)
    expected = _old_attention_forward(attn, x.clone(), **kwargs)
    actual = attn(x.clone(), **kwargs)
    assert torch.allclose(actual, expected, rtol=0, atol=1e-12)


def test_reference_tokens_are_covered_by_the_image_span():
    # Krea-2 edit prepends ref tokens to the image span; from Attention's view
    # they are just more rows after ``txt_len``, positioned identically in both
    # passes. Extending img_len must keep the equivalence.
    attn, x, kwargs = _attention_case(4, 20, 6, with_mask=True)
    expected = _old_attention_forward(attn, x.clone(), **kwargs)
    actual = attn(x.clone(), **kwargs)
    assert torch.allclose(actual, expected, rtol=0, atol=1e-12)


def test_forward_does_not_mutate_the_positive_key_rows():
    attn, x, kwargs = _attention_case(4, 9, 6)
    disabled = dict(kwargs, nag={"scale": 1.0})
    baseline_positive = attn(x.clone(), **disabled)
    attn(x.clone(), **kwargs)
    assert torch.equal(attn(x.clone(), **disabled), baseline_positive)


# --- disabled NAG: identical output, no extra projection ------------------

def test_nag_disabled_is_identical_and_projects_nothing_extra():
    attn, x, kwargs = _attention_case(4, 9, 6)
    disabled = dict(kwargs, nag={"scale": 1.0})
    expected = _old_attention_forward(attn, x.clone(), **disabled)
    with _projection_rows(attn) as rows:
        actual = attn(x.clone(), **disabled)
    assert torch.equal(actual, expected)
    assert rows["wk"] == [x.shape[1]]
    assert rows["wv"] == [x.shape[1]]


# --- projection accounting through the real model -------------------------

class _projection_rows:
    """Records the sequence length passed to each k/v projection."""

    def __init__(self, *modules):
        self._modules = modules
        self._handles = []
        self.rows: dict[str, list[int]] = {"wk": [], "wv": []}

    def __enter__(self):
        for module in self._modules:
            for name in ("wk", "wv"):
                proj = getattr(module, name)
                self._handles.append(
                    proj.register_forward_pre_hook(
                        lambda _m, args, _n=name: self.rows[_n].append(args[0].shape[1])
                    )
                )
        return self.rows

    def __exit__(self, *exc):
        for handle in self._handles:
            handle.remove()
        return False


def _randomised_state_dict(module):
    sd = {}
    for k, v in module.state_dict().items():
        if k.endswith(".scale") or k.endswith(".mod.lin") or k.endswith(".modulation.lin"):
            sd[k] = torch.zeros_like(v)
        elif v.is_floating_point():
            sd[k] = torch.randn_like(v) * 0.02
        else:
            sd[k] = v.clone()
    return sd


def _build_model(config=TINY):
    torch.manual_seed(0)
    m = Krea2.from_config(config, _fp32_ops())
    load_into_module(m, _randomised_state_dict(m), match_model_spec(config))
    return m.eval()


def _model_inputs(seed=0, txt_len=5, neg_txt_len=3, batch=1):
    g = torch.Generator().manual_seed(seed)
    x = torch.randn(batch, 4, 8, 8, generator=g)
    te_hidden = torch.randn(batch, txt_len, 3, 16, generator=g)
    neg_hidden = torch.randn(batch, neg_txt_len, 3, 16, generator=g)
    return x, torch.tensor([0.5]), te_hidden, neg_hidden


def test_image_rows_are_projected_once_per_block_per_evaluation():
    m = _build_model(TINY_2LAYER)
    x, t, te_hidden, neg_hidden = _model_inputs(txt_len=5, neg_txt_len=3)
    blocks = list(m.blocks)
    txt_len, neg_txt_len = 5, 3
    img_len = (8 // TINY["patch"]) ** 2
    for block in blocks:
        with _projection_rows(block.attn) as rows:
            m(x, t, te_hidden, nag_context=neg_hidden, nag={"scale": 2.0})
        # One joint pass over [text | image] plus the negative text rows only:
        # any second appearance of ``img_len`` means the image K/V were rebuilt.
        assert rows["wk"] == [txt_len + img_len, neg_txt_len]
        assert rows["wv"] == [txt_len + img_len, neg_txt_len]


def test_model_output_matches_old_formula(monkeypatch):
    m = _build_model(TINY_2LAYER)
    x, t, te_hidden, neg_hidden = _model_inputs(txt_len=6, neg_txt_len=3)
    nag = {"scale": 2.5, "tau": 3.5, "alpha": 0.5}
    nag_mask = torch.ones(1, 3, dtype=torch.long)
    nag_mask[0, 2:] = 0
    ref = torch.randn(1, 4, 8, 8, generator=torch.Generator().manual_seed(7))

    def run():
        return m(x, t, te_hidden, ref_latents=ref, nag_context=neg_hidden, nag=nag,
                 nag_attention_mask=nag_mask)

    actual = run()
    monkeypatch.setattr(Attention, "forward", _old_attention_forward)
    expected = run()
    assert torch.allclose(actual, expected, rtol=0, atol=1e-6)
    assert not torch.equal(actual, m(x, t, te_hidden, ref_latents=ref))
