"""Loader-level test: the MiniMax-H3 nvfp4_awq TE's quant sidecars must not
trip the ``qwen3vl`` load-integrity allowlist.

Real checkpoint key pattern (``ai/minimax_h3/te_nvfp4_awq_header.json``, read
directly in ``test_te_detect.py``'s detection-level test): every
``self_attn.{q,k,v,o}_proj``/``mlp.{gate,up,down}_proj`` in every one of the 50
layers is nvfp4 (``weight``/``weight_scale``/``weight_scale_2``/``comfy_quant``),
``mlp.down_proj``/``self_attn.o_proj`` additionally carry an AWQ
``pre_quant_scale`` BF16 sidecar, and ``model.embed_tokens`` is
int8_tensorwise (``weight`` I8 + per-row ``weight_scale`` + ``comfy_quant``).
This test mirrors that KEY PATTERN at tiny widths — not the real 5120-hidden /
50-layer / 151936-vocab checkpoint, which would be far too slow to build here —
and drives the actual key-integrity gate ``_SPECS["qwen3vl"]``/
``load_text_encoder`` apply for the real file (the missing/unexpected-key
check portion of ``base.load_into_module``; see
``_load_state_dict_gate_only``'s docstring for why the ``post_load()`` call
itself is reproduced separately rather than invoked directly here), so a
regression in either the ops-level consumption or the allowlist shows up here
as an integrity-gate failure, not just a unit-level assertion.
"""

from __future__ import annotations

import json

import pytest

torch = pytest.importorskip("torch")

from src.platform.runtime.native.text_encoders.loader import _SPECS  # noqa: E402
from src.platform.runtime.native.text_encoders.qwen3 import Qwen3Model  # noqa: E402
from vendor.gpl.comfyui.ops import fp8_ops, pick_operations  # noqa: E402

from .._nvfp4_ref import default_tensor_scale, quantize_nvfp4  # noqa: E402

_CFG = {
    "hidden_size": 16, "intermediate_size": 32, "num_layers": 1, "vocab_size": 24,
    "num_attention_heads": 2, "num_key_value_heads": 1, "head_dim": 8, "rope_theta": 5000000.0,
}


def _blob(conf: dict) -> torch.Tensor:
    return torch.tensor(list(json.dumps(conf).encode("utf-8")), dtype=torch.uint8)


def _quantize_linear_to_nvfp4(sd: dict, key: str, *, pre_quant_scale: bool) -> None:
    w = sd[key].to(torch.float32)
    out_f, in_f = w.shape
    pts = default_tensor_scale(w)
    packed, block_sw, _, _ = quantize_nvfp4(w, pts)
    prefix = key.removesuffix("weight")
    sd[key] = packed
    sd[f"{prefix}weight_scale"] = block_sw
    sd[f"{prefix}weight_scale_2"] = pts.clone()
    sd[f"{prefix}comfy_quant"] = _blob({"format": "nvfp4"})
    if pre_quant_scale:
        sd[f"{prefix}pre_quant_scale"] = torch.rand(in_f, dtype=torch.bfloat16) * 0.5 + 0.75


def _quantize_embedding_to_int8(sd: dict, key: str) -> None:
    w = sd[key].to(torch.float32)
    row_absmax = w.abs().amax(dim=-1, keepdim=True).clamp(min=1e-12)
    scale = row_absmax / 127.0
    q = torch.round(w / scale).clamp(-127, 127).to(torch.int8)
    prefix = key.removesuffix("weight")
    sd[key] = q
    sd[f"{prefix}weight_scale"] = scale
    sd[f"{prefix}comfy_quant"] = _blob({"format": "int8_tensorwise"})


def _build_awq_sd() -> dict[str, torch.Tensor]:
    ops = pick_operations(torch.float32, torch.float32)
    m = Qwen3Model.from_config(_CFG, ops)
    # disable_weight_init skips reset_parameters, so every tensor in the fresh
    # module's state dict is uninitialized (torch.empty) garbage. Give
    # everything real values first: norms get 1.0 (a sane RMSNorm scale),
    # everything else (the tensors quantized below) gets randn — quantizing
    # actual garbage risked feeding inf/huge magnitudes into the nvfp4/int8
    # quantizers and NaN-ing the forward for reasons unrelated to this test.
    torch.manual_seed(0)
    sd = {}
    for k, v in m.state_dict().items():
        sd[k] = torch.ones_like(v) if k.endswith("norm.weight") else torch.randn_like(v) * 0.05

    for proj in ("q_proj", "k_proj", "v_proj"):
        _quantize_linear_to_nvfp4(sd, f"model.layers.0.self_attn.{proj}.weight", pre_quant_scale=False)
    _quantize_linear_to_nvfp4(sd, "model.layers.0.self_attn.o_proj.weight", pre_quant_scale=True)
    for proj in ("gate_proj", "up_proj"):
        _quantize_linear_to_nvfp4(sd, f"model.layers.0.mlp.{proj}.weight", pre_quant_scale=False)
    _quantize_linear_to_nvfp4(sd, "model.layers.0.mlp.down_proj.weight", pre_quant_scale=True)
    _quantize_embedding_to_int8(sd, "model.embed_tokens.weight")
    return sd


def _load_state_dict_gate_only(module, sd, spec):
    """The key-integrity portion of ``base.load_into_module``, WITHOUT the
    ``post_load()`` call.

    ``Qwen3Model.post_load`` -> ``recompute_inv_freq`` reads
    ``self.embed_tokens.weight.device`` unconditionally — a pre-existing
    assumption in ``text_encoders/qwen3.py`` (not owned by this change) that
    breaks once ``embed_tokens`` is genuinely quantised and ``.weight`` is
    ``None`` (see this module's docstring). Reproducing only the gate here
    isolates "do the sidecars load cleanly" from that separate, already-latent
    bug so a regression in either one is not masked by the other.
    """
    module.requires_grad_(False)
    result = module.load_state_dict(sd, strict=False, assign=True)
    bad_missing = [k for k in result.missing_keys if not spec.key_is_expected_missing(k)]
    bad_unexpected = [k for k in result.unexpected_keys if not spec.key_is_expected_unexpected(k)]
    return bad_missing, bad_unexpected


def test_awq_nvfp4_and_int8_embedding_sidecars_load_without_integrity_error():
    sd = _build_awq_sd()
    m = Qwen3Model.from_config(_CFG, fp8_ops)
    bad_missing, bad_unexpected = _load_state_dict_gate_only(m, sd, _SPECS["qwen3vl"])
    assert bad_missing == []
    assert bad_unexpected == []

    assert m.model.layers[0].self_attn.o_proj.pre_quant_scale is not None
    assert m.model.layers[0].mlp.down_proj.pre_quant_scale is not None
    # q/k/v/gate/up carry no AWQ sidecar in the real checkpoint either.
    assert m.model.layers[0].self_attn.q_proj.pre_quant_scale is None
    assert m.model.embed_tokens._int8_weight is not None


def test_awq_quantised_layers_run_a_real_forward():
    """Beyond the integrity gate: the quantised layers (int8 embedding lookup,
    nvfp4 dequant + AWQ smoothing) must actually compose into a working
    forward, not just an accepted load. Runs the transformer body directly
    (bypassing ``Qwen3Model.post_load``/``recompute_inv_freq`` — see
    ``_load_state_dict_gate_only``'s docstring) with ``inv_freq`` recomputed
    by hand instead."""
    sd = _build_awq_sd()
    m = Qwen3Model.from_config(_CFG, fp8_ops)
    bad_missing, bad_unexpected = _load_state_dict_gate_only(m, sd, _SPECS["qwen3vl"])
    assert bad_missing == [] and bad_unexpected == []

    half = torch.arange(0, _CFG["head_dim"], 2, dtype=torch.float32)
    m.model.inv_freq = 1.0 / (_CFG["rope_theta"] ** (half / _CFG["head_dim"]))
    m.eval()

    ids = torch.tensor([[1, 2, 3, 4]])
    with torch.inference_mode():
        out = m(ids, attention_mask=None, layers_to_extract=(0,), capture="output")
    assert torch.isfinite(out).all()
