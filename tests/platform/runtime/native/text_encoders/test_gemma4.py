"""Gemma4-Unified-12B text encoder (LTX-2.5) tests.

Covers: the (still) 4-norm block + trailing `layer_scalar`, plain-weight RMS
norm (not gemma3's weight+1), the scale-less `v_norm`, per-layer-type
head_dim + partial ("proportional") global RoPE with its zero-padded NoPE
tail, normalize_in, layer="all" (num_layers+1) state stack, detection
disambiguation vs gemma3 and qwen3, config shape-recovery for the two
distinct head dims, and the tokenizer built from the checkpoint's own embedded
`tokenizer_json` blob (BPE, via the `tokenizers` library — see module
docstring on `Gemma4Tokenizer`).
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

import pytest

torch = pytest.importorskip("torch")

from src.platform.runtime.native.detect.te_detect import detect_te_config  # noqa: E402
from vendor.gpl.comfyui.ops import disable_weight_init as ops  # noqa: E402
from src.platform.runtime.native.text_encoders.gemma4 import (  # noqa: E402
    Gemma4Model, _Gemma4RMSNorm, _rotate_half, is_global_layer,
)
from src.platform.runtime.native.text_encoders.loader import _SPECS, _build_config  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parents[5]
_REAL_TE = _REPO_ROOT / "models/clip/gemma4-12b-with-proj-ltx-2.5-comfy-int8-convrot.safetensors"


def _tiny_tokenizer_json_bytes() -> bytes:
    """A hand-built minimal BPE tokenizer (few tokens, one merge), no
    normalizer/pre_tokenizer/post_processor — enough to exercise the
    `Gemma4Tokenizer` construction + BOS-prepend + pad/mask contract without
    the real 32MB checkpoint blob. Matches the real tokenizer's special-token
    layout (pad 0, eos 1, bos 2, unk 3) but is otherwise unrelated to it."""
    from tokenizers import Tokenizer
    from tokenizers.models import BPE

    vocab = {"<pad>": 0, "<eos>": 1, "<bos>": 2, "<unk>": 3, "a": 4, "b": 5, "c": 6, "ab": 7}
    merges = [("a", "b")]
    tok = Tokenizer(BPE(vocab=vocab, merges=merges, unk_token="<unk>"))
    return tok.to_str().encode("utf-8")

# 8 layers: (i+1)%6==0 -> index 5 is global via the modulo pattern; index 7
# (the last) is ALSO forced global (num_layers isn't a multiple of 6) — so
# this TINY config exercises both the modulo branch and the "last layer"
# force, with sliding (head_dim 32) and global (global_head_dim 64) DIFFERING,
# unlike gemma3's single uniform head_dim.
TINY = {
    "hidden_size": 64, "num_layers": 8, "vocab_size": 1000, "intermediate_size": 128,
    "num_attention_heads": 2, "num_key_value_heads": 1, "head_dim": 32, "global_head_dim": 64,
}

# The real LTX-2.5 TE's per-layer-type asymmetry, scaled down: the global
# layers use FEWER kv heads (num_global_key_value_heads) at a WIDER head_dim
# than the sliding layers, and carry no v_proj at all (attention_k_eq_v).
# Widths are chosen so sliding kv (2*16=32) and global kv (1*64=64) DIFFER —
# and so the pre-fix sizing (num_key_value_heads on a global layer, 2*64=128)
# differs from both.
TINY_K_EQ_V = {
    "hidden_size": 64, "num_layers": 8, "vocab_size": 1000, "intermediate_size": 128,
    "num_attention_heads": 4, "num_key_value_heads": 2, "num_global_key_value_heads": 1,
    "attention_k_eq_v": True, "head_dim": 16, "global_head_dim": 64,
}


def _build(cfg, seed=42):
    g = torch.Generator().manual_seed(seed)
    m = Gemma4Model.from_config(cfg, ops)
    for p in m.parameters():
        torch.nn.init.normal_(p, std=0.02, generator=g)
    m.post_load()
    return m


def test_is_global_layer_modulo_and_last_layer_forced():
    # 8 layers: only index 5 satisfies (i+1)%6==0; index 7 (last) is forced
    # global even though 8%6 != 0.
    assert [is_global_layer(i, 8) for i in range(8)] == [
        False, False, False, False, False, True, False, True,
    ]
    # 48 layers (the real gemma3/gemma4-12b scale): every 6th, and the last
    # (47) already satisfies the modulo -- the "force" is a no-op here.
    globals_48 = [i for i in range(48) if is_global_layer(i, 48)]
    assert globals_48 == [5, 11, 17, 23, 29, 35, 41, 47]


def test_block_has_four_norms_and_layer_scalar():
    m = _build(TINY)
    b = m.model.layers[0]
    for n in ("input_layernorm", "post_attention_layernorm", "pre_feedforward_layernorm", "post_feedforward_layernorm"):
        assert hasattr(b, n)
    assert "model.layers.0.layer_scalar" in m.state_dict()
    assert torch.equal(b.layer_scalar, torch.ones(1))


def test_v_norm_has_no_learned_weight():
    # with_scale=False upstream -> no nn.Module/parameter, hence no checkpoint
    # key anywhere for v_norm (unlike q_norm/k_norm, which do have weights).
    m = _build(TINY)
    keys = set(m.state_dict())
    assert not any("v_norm" in k for k in keys)
    assert any(k.endswith("self_attn.q_norm.weight") for k in keys)
    assert any(k.endswith("self_attn.k_norm.weight") for k in keys)


def test_rms_norm_is_plain_weight_not_plus_one():
    # gemma4's RMS norm is x/rms(x) * weight -- zero weight means the WHOLE
    # output is zero, unlike gemma3's weight+1 (zero weight -> unit scale).
    norm = _Gemma4RMSNorm(8, 1e-6)
    torch.nn.init.zeros_(norm.weight)
    x = torch.randn(2, 4, 8)
    out = norm(x)
    assert torch.allclose(out, torch.zeros_like(out), atol=1e-6)

    torch.nn.init.ones_(norm.weight)
    out = norm(x)
    expected = torch.nn.functional.rms_norm(x, (8,), weight=torch.ones(8), eps=1e-6)
    assert torch.allclose(out, expected, atol=1e-5)


def test_per_layer_head_dim_differs_local_vs_global():
    m = _build(TINY)
    sliding_layer = m.model.layers[0]   # is_global[0] is False
    global_layer = m.model.layers[5]    # is_global[5] is True
    assert sliding_layer.self_attn.head_dim == TINY["head_dim"]
    assert global_layer.self_attn.head_dim == TINY["global_head_dim"]
    assert sliding_layer.self_attn.q_norm.weight.shape == (32,)
    assert global_layer.self_attn.q_norm.weight.shape == (64,)


def test_global_layers_size_kv_from_global_head_count_and_drop_v_proj():
    m = _build(TINY_K_EQ_V)
    sliding = m.model.layers[0].self_attn
    glob = m.model.layers[5].self_attn
    h = TINY_K_EQ_V["hidden_size"]

    assert sliding.k_proj.weight.shape == (2 * 16, h)      # num_key_value_heads * head_dim
    assert sliding.v_proj is not None
    assert sliding.v_proj.weight.shape == (2 * 16, h)

    # 1 * 64, NOT num_key_value_heads * global_head_dim (2 * 64) — the shape
    # the real checkpoint failed to load into.
    assert glob.k_proj.weight.shape == (1 * 64, h)
    assert glob.q_proj.weight.shape == (4 * 64, h)
    assert glob.v_proj is None

    keys = set(m.state_dict())
    assert "model.layers.0.self_attn.v_proj.weight" in keys
    assert not any(f"model.layers.{i}.self_attn.v_proj.weight" in keys for i in (5, 7))


def _reference_attention(attn, x, cos, sin, mask=None, scaling: float = 1.0):
    """transformers' ``Gemma4UnifiedTextAttention.forward`` + ``eager_attention_forward``
    written out independently (modeling_gemma4_unified.py:437-462 and :328-357).

    The load-bearing details: V is the k_proj output taken BEFORE k_norm and
    BEFORE RoPE (upstream aliases ``value_states = key_states`` at line 442,
    then rebinds ``key_states`` at 444-446), only the scale-less v_norm is
    applied to it (448), and the softmax is UNSCALED (``self.scaling = 1.0``,
    line 378). Layout is [B, H, S, D] here rather than upstream's [B, S, H, D];
    RoPE and the norms are last-dim ops, so the two agree.
    """
    b, s, _ = x.shape
    hd, heads, kv = attn.head_dim, attn.num_heads, attn.num_kv_heads
    q = attn.q_proj(x).view(b, s, heads, hd).transpose(1, 2)
    k_raw = attn.k_proj(x).view(b, s, kv, hd).transpose(1, 2)
    v = k_raw if attn.v_proj is None else attn.v_proj(x).view(b, s, kv, hd).transpose(1, 2)

    q = attn.q_norm(q)
    k = attn.k_norm(k_raw)
    q = q * cos + _rotate_half(q) * sin
    k = k * cos + _rotate_half(k) * sin
    v = torch.nn.functional.rms_norm(v, (hd,), weight=None, eps=attn.eps)

    n_rep = heads // kv
    k = k.repeat_interleave(n_rep, dim=1)
    v = v.repeat_interleave(n_rep, dim=1)
    w = torch.matmul(q, k.transpose(2, 3)) * scaling
    if mask is not None:
        w = w + mask
    w = torch.softmax(w, dim=-1, dtype=torch.float32).to(q.dtype)
    out = torch.matmul(w, v).transpose(1, 2).reshape(b, s, heads * hd)
    return attn.o_proj(out)


def test_k_eq_v_global_layer_matches_eager_reference():
    m = _build(TINY_K_EQ_V)
    attn = m.model.layers[5].self_attn
    assert attn.v_proj is None
    x = torch.randn(2, 6, TINY_K_EQ_V["hidden_size"], generator=torch.Generator().manual_seed(7))
    cos, sin = m.model._rope(m.model.inv_freq_global, 6, x.device, x.dtype)

    out = attn(x, cos, sin, None)
    assert torch.isfinite(out).all()
    assert torch.allclose(out, _reference_attention(attn, x, cos, sin), atol=1e-5)
    # ...and NOT the 1/sqrt(head_dim) softmax torch's SDPA applies by default:
    # gemma4's learned q/k norms carry the temperature (scaling = 1.0 upstream).
    assert not torch.allclose(
        out, _reference_attention(attn, x, cos, sin, scaling=attn.head_dim ** -0.5), atol=1e-3
    )


def test_k_eq_v_off_reproduces_symmetric_attention():
    cfg = {**TINY_K_EQ_V, "attention_k_eq_v": False,
           "num_global_key_value_heads": TINY_K_EQ_V["num_key_value_heads"]}
    m = _build(cfg)
    glob = m.model.layers[5].self_attn
    assert glob.v_proj is not None
    assert glob.k_proj.weight.shape == (2 * 64, cfg["hidden_size"])
    assert glob.v_proj.weight.shape == (2 * 64, cfg["hidden_size"])

    x = torch.randn(2, 6, cfg["hidden_size"], generator=torch.Generator().manual_seed(11))
    cos, sin = m.model._rope(m.model.inv_freq_global, 6, x.device, x.dtype)
    out = glob(x, cos, sin, None)
    assert torch.isfinite(out).all()
    assert torch.allclose(out, _reference_attention(glob, x, cos, sin), atol=1e-5)


def test_dual_rope_recomputed_with_nope_tail():
    m = _build(TINY)
    local, glob = m.model.inv_freq_local, m.model.inv_freq_global
    assert local.shape == (TINY["head_dim"] // 2,)       # 16
    assert glob.shape == (TINY["global_head_dim"] // 2,)  # 32
    assert torch.isfinite(local).all() and torch.isfinite(glob).all()
    # partial_rotary_factor 0.25 of global_head_dim=64 -> rope_angles = 8 ->
    # only the first 8 of 32 global inv_freq entries are non-zero rotary; the
    # rest are the zero-padded "NoPE" tail (proportional RoPE).
    rope_angles = int(0.25 * TINY["global_head_dim"] // 2)
    assert rope_angles == 8
    assert torch.all(glob[:rope_angles] != 0)
    assert torch.all(glob[rope_angles:] == 0)
    # local (sliding) rope has no NoPE tail -- fully rotary.
    assert torch.all(local != 0)


def test_forward_stacks_all_states():
    m = _build(TINY)
    ids = torch.randint(0, 1000, (2, 10))
    mask = torch.ones(2, 10, dtype=torch.long)
    mask[:, 7:] = 0
    out = m(ids, attention_mask=mask)
    assert out.shape == (2, TINY["num_layers"] + 1, 10, 64)   # 8+1 states
    assert torch.isfinite(out).all()


def test_detection_gemma4_before_gemma3_before_qwen3():
    def sd(pre_ff: bool, layer_scalar: bool):
        d = {"model.embed_tokens.weight": torch.zeros(1000, 64),
             "model.layers.0.self_attn.q_norm.weight": torch.zeros(32)}
        if pre_ff:
            d["model.layers.0.pre_feedforward_layernorm.weight"] = torch.zeros(64)
        if layer_scalar:
            d["model.layers.0.layer_scalar"] = torch.ones(1)
        return d

    # Both norms present -> gemma4 (layer_scalar is the discriminator).
    assert detect_te_config(sd(pre_ff=True, layer_scalar=True))["te_type"] == "gemma4"
    assert detect_te_config(sd(pre_ff=True, layer_scalar=True))["variant"] == "gemma4_12b"
    # 4th norm but no layer_scalar -> falls through to gemma3, NOT gemma4.
    assert detect_te_config(sd(pre_ff=True, layer_scalar=False))["te_type"] == "gemma3"
    # Neither 4th norm nor layer_scalar, plain qwen3-shaped vocab -> qwen3.
    qwen = {"model.embed_tokens.weight": torch.zeros(151936, 64),
            "model.layers.0.self_attn.q_norm.weight": torch.zeros(128)}
    assert detect_te_config(qwen)["te_type"] == "qwen3"


def test_detection_tolerates_vision_and_audio_towers_present_or_absent():
    base = {
        "model.embed_tokens.weight": torch.zeros(1000, 64),
        "model.layers.0.self_attn.q_norm.weight": torch.zeros(32),
        "model.layers.0.pre_feedforward_layernorm.weight": torch.zeros(64),
        "model.layers.0.layer_scalar": torch.ones(1),
    }
    assert detect_te_config(dict(base))["te_type"] == "gemma4"
    with_towers = dict(base)
    with_towers["vision_model.embeddings.patch_embedding.weight"] = torch.zeros(4)
    with_towers["multi_modal_projector.embedding_projection.weight"] = torch.zeros(4)
    with_towers["audio_projector.embedding_projection.weight"] = torch.zeros(4)
    assert detect_te_config(with_towers)["te_type"] == "gemma4"


def test_build_config_recovers_both_head_dims_from_shapes():
    sd = {
        "model.layers.0.mlp.gate_proj.weight": torch.zeros(128, 64),
        "model.layers.0.self_attn.q_proj.weight": torch.zeros(2 * 32, 64),
        "model.layers.0.self_attn.k_proj.weight": torch.zeros(1 * 32, 64),
        "model.layers.0.self_attn.q_norm.weight": torch.zeros(32),
        "model.layers.5.self_attn.q_norm.weight": torch.zeros(64),
    }
    te_config = {"te_type": "gemma4", "variant": "gemma4_12b", "num_layers": 8,
                 "hidden_size": 64, "vocab_size": 1000}
    config = _build_config(te_config, sd)
    assert config["intermediate_size"] == 128
    assert config["head_dim"] == 32
    assert config["num_attention_heads"] == 2
    assert config["num_key_value_heads"] == 1
    assert config["global_head_dim"] == 64


def test_build_config_recovers_global_kv_heads_and_k_eq_v_from_shapes():
    # The real file's per-layer-type pattern, scaled to TINY_K_EQ_V: a global
    # layer whose k_proj is 1 kv head wide and which carries NO v_proj. Both
    # facts must come from SHAPES — the embedded `gemma_config` metadata is
    # never read, so an int8/fp8 repack that drops it still builds correctly.
    sd = {
        "model.layers.0.mlp.gate_proj.weight": torch.zeros(128, 64),
        "model.layers.0.self_attn.q_proj.weight": torch.zeros(4 * 16, 64),
        "model.layers.0.self_attn.k_proj.weight": torch.zeros(2 * 16, 64),
        "model.layers.0.self_attn.v_proj.weight": torch.zeros(2 * 16, 64),
        "model.layers.0.self_attn.q_norm.weight": torch.zeros(16),
        "model.layers.5.self_attn.q_proj.weight": torch.zeros(4 * 64, 64),
        "model.layers.5.self_attn.k_proj.weight": torch.zeros(1 * 64, 64),
        "model.layers.5.self_attn.q_norm.weight": torch.zeros(64),
    }
    te_config = {"te_type": "gemma4", "variant": "gemma4_12b", "num_layers": 8,
                 "hidden_size": 64, "vocab_size": 1000}
    config = _build_config(te_config, sd)
    assert config["head_dim"] == 16
    assert config["global_head_dim"] == 64
    assert config["num_key_value_heads"] == 2
    assert config["num_global_key_value_heads"] == 1
    assert config["attention_k_eq_v"] is True

    # A global layer that DOES carry a v_proj is the symmetric variant.
    sd["model.layers.5.self_attn.v_proj.weight"] = torch.zeros(1 * 64, 64)
    assert _build_config(te_config, sd)["attention_k_eq_v"] is False


def test_build_config_global_head_dim_falls_back_when_too_few_layers():
    # num_layers=1 -> the single layer is forced full_attention (it's also the
    # last), so index 0 IS the global layer -- global_head_dim reads the SAME
    # q_norm as the (only) local recovery would have.
    sd = {
        "model.layers.0.self_attn.q_norm.weight": torch.zeros(32),
        "model.layers.0.self_attn.q_proj.weight": torch.zeros(64, 64),
        "model.layers.0.self_attn.k_proj.weight": torch.zeros(32, 64),
    }
    te_config = {"te_type": "gemma4", "variant": "gemma4_12b", "num_layers": 1,
                 "hidden_size": 64, "vocab_size": 1000}
    config = _build_config(te_config, sd)
    assert config["head_dim"] == 32
    assert config["global_head_dim"] == 32


def test_synthetic_load_and_encode(tmp_path):
    pytest.importorskip("tokenizers")
    from safetensors.torch import save_file

    from src.platform.runtime.native.text_encoders.loader import load_text_encoder

    m = _build({**TINY, "num_layers": 4}, seed=12345)
    sd = {k: v.detach().clone().to(torch.bfloat16) for k, v in m.state_dict().items()}
    sd["vision_model.embeddings.patch_embedding.weight"] = torch.zeros(4, 4, dtype=torch.bfloat16)
    sd["multi_modal_projector.mm_soft_emb_norm.weight"] = torch.zeros(4, dtype=torch.bfloat16)
    sd["audio_projector.embedding_projection.weight"] = torch.zeros(4, dtype=torch.bfloat16)
    sd["text_embedding_projection.video_aggregate_embed.weight"] = torch.zeros(4, 4, dtype=torch.bfloat16)
    tok_bytes = _tiny_tokenizer_json_bytes()
    sd["tokenizer_json"] = torch.frombuffer(bytearray(tok_bytes), dtype=torch.uint8).clone()
    path = tmp_path / "gemma4.safetensors"
    save_file(sd, str(path))

    enc = load_text_encoder(str(path))
    assert enc.role == "gemma4_12b"
    out = enc.encode(["ab c"])
    assert set(out) == {"context", "attention_mask"}
    assert torch.isfinite(out["context"]).all()


def test_load_raises_crisp_error_when_tokenizer_json_missing(tmp_path):
    from safetensors.torch import save_file

    from src.platform.runtime.native.errors import NativeEngineUnsupportedError
    from src.platform.runtime.native.text_encoders.loader import load_text_encoder

    m = _build({**TINY, "num_layers": 4}, seed=12345)
    sd = {k: v.detach().clone().to(torch.bfloat16) for k, v in m.state_dict().items()}
    path = tmp_path / "gemma4_no_tokenizer.safetensors"
    save_file(sd, str(path))

    with pytest.raises(NativeEngineUnsupportedError, match="tokenizer_json") as exc_info:
        load_text_encoder(str(path))
    assert path.name in str(exc_info.value)


def test_real_pattern_state_dict_loads_through_the_te_path(tmp_path):
    """A synthetic checkpoint carrying the REAL per-layer-type shape pattern
    (scaled down) goes detect -> _build_config -> build -> integrity-load with
    no size mismatch, alongside the towers/projection keys the loader strips."""
    from safetensors.torch import save_file

    from src.platform.runtime.native.text_encoders.loader import _load_one

    m = _build(TINY_K_EQ_V, seed=999)
    sd = {k: v.detach().clone().to(torch.bfloat16) for k, v in m.state_dict().items()}
    assert not any("layers.5.self_attn.v_proj" in k for k in sd)
    sd["vision_model.patch_dense.weight"] = torch.zeros(4, 4, dtype=torch.bfloat16)
    sd["multi_modal_projector.embedding_projection.weight"] = torch.zeros(4, 4, dtype=torch.bfloat16)
    sd["audio_projector.embedding_projection.weight"] = torch.zeros(4, 4, dtype=torch.bfloat16)
    sd["text_embedding_projection.video_aggregate_embed.weight"] = torch.zeros(4, 4, dtype=torch.bfloat16)
    tok_bytes = _tiny_tokenizer_json_bytes()
    sd["tokenizer_json"] = torch.frombuffer(bytearray(tok_bytes), dtype=torch.uint8).clone()
    path = tmp_path / "gemma4_k_eq_v.safetensors"
    save_file(sd, str(path))

    te_type, variant, module, config, _ = _load_one(
        path, operations=None, device="cpu", compute_dtype=torch.bfloat16,
    )
    assert (te_type, variant) == ("gemma4", "gemma4_12b")
    assert config["num_global_key_value_heads"] == 1
    assert config["attention_k_eq_v"] is True
    loaded = module.model.layers[5].self_attn
    assert loaded.v_proj is None
    assert loaded.k_proj.weight.shape == (64, TINY_K_EQ_V["hidden_size"])
    assert torch.equal(loaded.k_proj.weight, sd["model.layers.5.self_attn.k_proj.weight"])


def _real_header() -> dict[str, dict]:
    """The shipped TE's safetensors JSON header — shapes/dtypes only, no tensor
    data (same header-only pattern as `test_ltx_model.py::_real_dit_tensors`)."""
    with open(_REAL_TE, "rb") as f:
        n = struct.unpack("<Q", f.read(8))[0]
        header = json.loads(f.read(n))
    return {k: v for k, v in header.items() if k != "__metadata__"}


@pytest.mark.requires_models
@pytest.mark.skipif(not _REAL_TE.is_file(), reason="real gemma4 checkpoint absent")
def test_real_header_shapes_drive_config_recovery():
    """Detection + shape recovery against the REAL key/shape layout: the config
    the loader would build must match the checkpoint's own `gemma_config`
    (hidden 3840, 16 heads, kv 8 @ head_dim 256, 1 global kv head @ 512,
    attention_k_eq_v) without ever reading that metadata."""
    sd = {
        k: torch.empty(v["shape"], device="meta")
        for k, v in _real_header().items()
        if k.startswith("model.") and not k.endswith((".comfy_quant", ".weight_scale"))
    }
    te_config = detect_te_config(sd)
    assert te_config["te_type"] == "gemma4"
    assert te_config["num_layers"] == 48

    config = _build_config(te_config, sd)
    assert config["hidden_size"] == 3840
    assert config["intermediate_size"] == 15360
    assert config["num_attention_heads"] == 16
    assert config["num_key_value_heads"] == 8
    assert config["head_dim"] == 256
    assert config["global_head_dim"] == 512
    assert config["num_global_key_value_heads"] == 1
    assert config["attention_k_eq_v"] is True


@pytest.mark.requires_models
@pytest.mark.skipif(not _REAL_TE.is_file(), reason="real gemma4 checkpoint absent")
def test_real_header_key_and_shape_parity():
    """Header-only parity against the shipped LTX-2.5 TE (never reads tensors;
    the module is built on the meta device, so neither side allocates 15GB)."""
    header = _real_header()
    spec = _SPECS["gemma4"]
    # Quant sidecars are load-time artifacts of the int8 repack, not arch keys;
    # the quantised `weight` entries already carry the LOGICAL shape.
    real = {
        k: tuple(v["shape"]) for k, v in header.items()
        if not spec.key_is_expected_unexpected(k)
    }
    # Everything the loader strips before building the LM (towers, the LTX
    # text_embedding_projection read by model_loader/ltx/projection.py, and the
    # embedded tokenizer/HF asset blobs) is not part of this module.
    real = {k: v for k, v in real.items() if k.startswith("model.")}

    cfg = {"hidden_size": 3840, "num_layers": 48, "vocab_size": 262144,
           "intermediate_size": 15360, "num_attention_heads": 16,
           "num_key_value_heads": 8, "num_global_key_value_heads": 1,
           "attention_k_eq_v": True, "head_dim": 256, "global_head_dim": 512}
    with torch.device("meta"):
        module = Gemma4Model.from_config(cfg, ops)
    mine = {k: tuple(v.shape) for k, v in module.state_dict().items()}

    assert set(mine) == set(real), (sorted(set(mine) - set(real))[:10],
                                    sorted(set(real) - set(mine))[:10])
    mismatched = {k: (mine[k], real[k]) for k in mine if mine[k] != real[k]}
    assert not mismatched, dict(list(mismatched.items())[:10])
    # 40 sliding layers x 14 keys (6 attn incl. v_proj + 3 mlp + 4 norms +
    # layer_scalar) + 8 global x 13 (no v_proj) + embed_tokens + final norm.
    assert len(mine) == 40 * 14 + 8 * 13 + 2 == 666


def test_gemma4_tokenizer_constructs_from_blob_and_prepends_bos_once():
    """Construction from raw `tokenizer_json` bytes (the checkpoint-blob
    contract, no bundled asset): BOS/PAD ids are read from the blob's own
    vocab (2/0 here, matching the tiny fixture's Gemma-style layout), BOS is
    prepended exactly once per row, and the underlying encode() adds nothing
    extra (the real blob's post_processor has an empty special_tokens map)."""
    pytest.importorskip("tokenizers")
    from src.platform.runtime.native.text_encoders.tokenization import Gemma4Tokenizer

    tok = Gemma4Tokenizer(_tiny_tokenizer_json_bytes())
    assert tok._bos_id == 2
    assert tok._pad_id == 0

    ids, mask = tok(["ab", "c"])
    assert ids.shape == (2, 2)                 # [BOS, ab] / [BOS, c] -- no padding needed
    assert ids[:, 0].tolist() == [2, 2]        # BOS at the front of every row
    assert (ids == 2).sum(dim=1).tolist() == [1, 1]   # exactly once per row
    assert ids[0].tolist() == [2, 7]
    assert ids[1].tolist() == [2, 6]
    assert mask.tolist() == [[1, 1], [1, 1]]


def test_gemma4_tokenizer_pads_shorter_rows_and_masks_them():
    pytest.importorskip("tokenizers")
    from src.platform.runtime.native.text_encoders.tokenization import Gemma4Tokenizer

    tok = Gemma4Tokenizer(_tiny_tokenizer_json_bytes())
    ids, mask = tok(["c", "abc"])
    assert ids.shape == (2, 3)                 # longest row: [BOS, ab, c] = 3
    assert ids[0].tolist() == [2, 6, 0]         # padded with the derived PAD id (0)
    assert mask[0].tolist() == [1, 1, 0]
    assert ids[1].tolist() == [2, 7, 6]
    assert mask[1].tolist() == [1, 1, 1]


def test_gemma4_tokenizer_empty_string_is_bos_only():
    pytest.importorskip("tokenizers")
    from src.platform.runtime.native.text_encoders.tokenization import Gemma4Tokenizer

    tok = Gemma4Tokenizer(_tiny_tokenizer_json_bytes())
    ids, mask = tok([""])
    assert ids.tolist() == [[2]]
    assert mask.tolist() == [[1]]


def test_gemma4_tokenizer_raises_on_vocab_missing_bos_or_pad():
    pytest.importorskip("tokenizers")
    from tokenizers import Tokenizer
    from tokenizers.models import BPE

    from src.platform.runtime.native.text_encoders.tokenization import Gemma4Tokenizer

    tok = Tokenizer(BPE(vocab={"<unk>": 0, "a": 1}, merges=[], unk_token="<unk>"))
    with pytest.raises(ValueError, match="bos"):
        Gemma4Tokenizer(tok.to_str().encode("utf-8"))


@pytest.mark.requires_models
@pytest.mark.skipif(not _REAL_TE.is_file(), reason="real gemma4 checkpoint absent")
def test_real_tokenizer_json_blob_matches_golden_ids():
    """End-to-end against the REAL checkpoint's embedded blob: read only the
    `tokenizer_json` tensor via `safe_open` (never the 15GB of weights),
    build `Gemma4Tokenizer` from its bytes, and pin the resulting ids for a
    handful of fixed strings (including unicode and empty) -- golden values
    were produced by this exact construction path against the shipped LTX-2.5
    TE (`gemma4-12b-with-proj-ltx-2.5-comfy-int8-convrot.safetensors`)."""
    from safetensors import safe_open

    from src.platform.runtime.native.text_encoders.tokenization import Gemma4Tokenizer

    with safe_open(str(_REAL_TE), framework="pt", device="cpu") as f:
        tok_bytes = f.get_tensor("tokenizer_json").numpy().tobytes()

    tok = Gemma4Tokenizer(tok_bytes)
    assert tok._bos_id == 2
    assert tok._pad_id == 0

    golden = {
        "a cat": [2, 236746, 5866],
        "hello, world!": [2, 23391, 236764, 1902, 236888],
        "": [2],
        "café ñoño \U0001F600": [2, 123125, 236859, 236743, 6650, 6650, 163543],
    }
    for text, expected in golden.items():
        ids, mask = tok([text])
        assert ids[0].tolist() == expected
        assert mask[0].tolist() == [1] * len(expected)
        assert ids[0].tolist()[0] == 2
        assert ids[0].tolist().count(2) == 1
