"""Tests for A1111 prompt weighting on the native text encoders.

Covers the shared primitives (``parse_a1111`` / ``apply_token_weights`` /
``weighted_token_ids``) and the ``encode_weighted`` template method on the
qwen-family encoders, with the critical no-op guarantee: a prompt with no weight
syntax must be bit-identical to a plain ``encode``.
"""

from __future__ import annotations

import torch

from src.platform.runtime.native.base import load_into_module
from vendor.gpl.comfyui.ops import pick_operations
from src.platform.runtime.native.text_encoders.loader import _SPECS
from src.platform.runtime.native.text_encoders.prompt_weights import (
    apply_token_weights,
    has_weights,
    parse_a1111,
    strip_syntax,
    transform_weight,
    weighted_token_ids,
)
from src.platform.runtime.native.text_encoders.qwen3 import (
    Qwen3Model,
    Qwen3TextEncoder,
    Qwen3VLTextEncoder,
)
from src.platform.runtime.native.text_encoders.tokenization import Qwen3Tokenizer, Qwen3VLTokenizer


# --- parser ---------------------------------------------------------------

def test_parse_simple_and_weighted():
    assert parse_a1111("a cat") == [["a cat", 1.0]]
    assert parse_a1111("a (cat:1.3) dog") == [["a ", 1.0], ["cat", 1.3], [" dog", 1.0]]


def test_parse_nesting_and_brackets():
    # ((x)) -> 1.1 * 1.1
    assert parse_a1111("((cat))")[2] == ["cat", 1.1 * 1.1]
    # [x] -> 1/1.1
    assert parse_a1111("[cat]")[1] == ["cat", 1.0 / 1.1]


def test_parse_escapes_and_empty():
    assert parse_a1111(r"a \(lit\) b") == [["a (lit) b", 1.0]]
    assert parse_a1111("") == [["", 1.0]]


def test_has_weights_and_strip():
    assert has_weights(parse_a1111("(cat:1.3)")) is True
    assert has_weights(parse_a1111("a cat")) is False
    assert strip_syntax(parse_a1111("a (cat:1.3) dog")) == "a cat dog"


def test_transform_weight():
    assert transform_weight(1.0) == 1.0
    assert transform_weight(-2.0) == 1.0             # negatives clamped
    assert transform_weight(1.3) > 1.3               # w**1.2 amplifies above 1
    assert transform_weight(0.5) > 0.5               # w**0.8 softens below 1


# --- apply_token_weights --------------------------------------------------

def test_apply_identity_and_formula():
    e, b = torch.randn(1, 4, 8), torch.randn(1, 4, 8)
    assert torch.equal(apply_token_weights(e, torch.ones(1, 4), b), e)  # all-1.0 no-op
    w = torch.tensor([[1.0, 1.5, 1.0, 1.0]])
    out = apply_token_weights(e, w, b)
    assert torch.equal(out[0, 0], e[0, 0])           # unweighted position unchanged
    assert torch.allclose(out[0, 1], (e[0, 1] - b[0, 1]) * 1.5 + b[0, 1])


def test_apply_layered_context():
    # Krea-2 (B, S, L, D): weight broadcasts over the layer + feature dims.
    e, b = torch.randn(1, 4, 3, 8), torch.randn(1, 4, 3, 8)
    out = apply_token_weights(e, torch.tensor([[1.0, 2.0, 1.0, 1.0]]), b)
    assert torch.equal(out[0, 0], e[0, 0]) and not torch.allclose(out[0, 1], e[0, 1])


# --- weighted_token_ids ---------------------------------------------------

def test_weighted_token_ids_prefix_suffix_are_unweighted():
    tok = Qwen3Tokenizer()._tok
    ids, weights = weighted_token_ids(tok, "a (cat:1.5) dog", prefix="PRE ", suffix=" POST")
    assert len(ids) == len(weights)
    # some interior weight is non-1.0 (the cat segment), boundaries stay 1.0.
    assert any(w != 1.0 for w in weights)
    assert weights[0] == 1.0 and weights[-1] == 1.0


# --- encode_weighted (tiny qwen encoders) --------------------------------

def _tiny_qwen3() -> Qwen3Model:
    cfg = {"hidden_size": 16, "num_layers": 36, "vocab_size": 151936,
           "num_attention_heads": 2, "num_key_value_heads": 1, "head_dim": 8, "intermediate_size": 32}
    m = Qwen3Model.from_config(cfg, pick_operations(torch.float32, torch.float32))
    sd = {k: (torch.ones_like(v) if k.endswith("norm.weight")
              else (torch.randn_like(v) * 0.02 if v.is_floating_point() else v.clone()))
          for k, v in m.state_dict().items()}
    load_into_module(m, sd, _SPECS["qwen3"])
    m.eval()
    return m


def _encoders():
    m = _tiny_qwen3()
    return {
        "klein": Qwen3TextEncoder(m, Qwen3Tokenizer(), variant="qwen3_4b"),
        "krea": Qwen3VLTextEncoder(m, Qwen3VLTokenizer()),
    }


def test_encode_weighted_noop_is_bit_identical():
    for name, enc in _encoders().items():
        p = "a cat sitting on a mat"
        plain = enc.encode([p])["context"]
        weighted = enc.encode_weighted(p)["context"]
        assert torch.equal(plain, weighted), f"{name}: no-weight prompt not bit-identical"


def test_encode_weighted_changes_output():
    for name, enc in _encoders().items():
        un = enc.encode_weighted("a cat sitting on a mat")["context"]
        w = enc.encode_weighted("a (cat:1.5) sitting on a mat")["context"]
        # The weighted path tokenizes segments separately (accepted A1111/ComfyUI
        # BPE-boundary drift), and Krea trims its padding tail — so lengths may
        # differ by a token or two. Compare the overlapping region.
        assert abs(un.shape[1] - w.shape[1]) <= 2, f"{name}: unexpected length drift"
        n = min(un.shape[1], w.shape[1])
        assert not torch.allclose(un[:, :n], w[:, :n], atol=1e-6), f"{name}: weight had no effect"
        assert torch.isfinite(w).all()


def test_encode_weighted_falls_back_when_unsupported():
    # a bare encoder with no hooks -> weights ignored, no crash, plain encode.
    from src.platform.runtime.native.text_encoders.base import NativeTextEncoder

    class _Bare(NativeTextEncoder):
        role = "bare"

        def encode(self, texts):
            return {"context": torch.zeros(1, 3, 4)}

    out = _Bare().encode_weighted("a (cat:1.5) dog")
    assert out["context"].shape == (1, 3, 4)
