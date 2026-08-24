"""Tests for the vendored LTX-2 / 2.3 audio-video DiT (construction + load path).

The intricate AV forward is deferred to the LTX generator slice (needs the LTX
CausalVideoAutoencoder + Gemma3-12B TE for golden validation), so these tests
cover the load path: construction, detection, variant handling (LTX-2 19b vs the
gated + prompt-adaLN LTX-2.3 22b), and exact key parity against the real headers.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

import pytest
import torch

from src.platform.runtime.native.arch.ltx.config import LTXAVConfig
from src.platform.runtime.native.arch.ltx.model import LTXAVModel
from src.platform.runtime.native.base import NativeArchModule, load_into_module
from src.platform.runtime.native.detect.registry import match_model_spec
from src.platform.runtime.native.detect.unet_detect import detect_unet_config
from vendor.gpl.comfyui.ops import pick_operations

_CKPT_DIR = Path("models/checkpoints")
_DIT_PREFIX = "model.diffusion_model."

# Tiny AV config (small dims; head-dim relationships preserved).
TINY = {
    "image_model": "ltxav", "in_channels": 16, "out_channels": 16,
    "num_attention_heads": 2, "attention_head_dim": 4, "cross_attention_dim": 8,
    "caption_channels": 12, "num_layers": 1,
    "audio_num_attention_heads": 2, "audio_attention_head_dim": 2,
    "audio_cross_attention_dim": 4, "audio_in_channels": 16,
    "use_embeddings_connector": True, "connector_attention_head_dim": 4,
    "video_connector_inner": 8, "audio_connector_inner": 8, "connector_num_layers": 1,
    "connector_num_learnable_registers": 4,
}

# LTX-2.5-shaped: gated + prompt-adaLN blocks (like 2.3) but with the video FFN
# bias dropped and the timestep-dependent prompt-adaLN MLP dropped (KV-cacheable
# cross-attention) -- the per-block prompt_scale_shift_table stays. Also carries
# the 2.5.1+ generated-keyframe absolute-position embedding.
TINY_25 = dict(
    TINY,
    blocks_gated=True, block_gate_dim=2, has_prompt_adaln=True,
    use_cross_timestep=True, connector_gated=True, connector_gate_dim=2,
    ff_bias=False, audio_ff_bias=True, use_prompt_adaln_single=False,
    use_keyframes_abs_pos_embedding=True, model_version=(2, 5),
)


def _fp32_ops():
    return pick_operations(torch.float32, torch.float32)


def _real_dit_tensors(path: Path) -> dict[str, tuple]:
    """Weight key -> (shape, dtype) of the DiT (strip the model.diffusion_model.
    prefix + quant sidecars)."""
    with open(path, "rb") as f:
        n = struct.unpack("<Q", f.read(8))[0]
        header = json.loads(f.read(n))
    header.pop("__metadata__", None)

    def is_weight(k: str) -> bool:
        return not any(k.endswith(s) for s in (".weight_scale", ".weight_scale_2", ".input_scale", ".scale_input"))

    return {k[len(_DIT_PREFIX):]: (tuple(v["shape"]), v["dtype"])
            for k, v in header.items() if k.startswith(_DIT_PREFIX) and is_weight(k)}


def _detect_from_header(path: Path) -> dict:
    with open(path, "rb") as f:
        n = struct.unpack("<Q", f.read(8))[0]
        header = json.loads(f.read(n))
    header.pop("__metadata__", None)
    sd = {k[len(_DIT_PREFIX):]: torch.empty(v["shape"], device="meta")
          for k, v in header.items() if k.startswith(_DIT_PREFIX)}
    return detect_unet_config(sd)


# --- construction ---------------------------------------------------------

def test_tiny_construction_and_contract():
    with torch.device("meta"):
        m = LTXAVModel.from_config(TINY, _fp32_ops())
    assert isinstance(m, NativeArchModule)
    assert m.post_load() is None
    assert list(m.named_buffers()) == []          # no computed buffers
    assert m.patch_size == (1, 1, 1)              # load-time contract


def test_config_rejects_unknown_model():
    with pytest.raises(ValueError, match="image_model"):
        LTXAVConfig.from_detect_config(dict(TINY, image_model="flux"))


# --- LTX-2.5 construction (ff_bias / use_prompt_adaln_single / keyframes) ---

def test_tiny_25_construction_drops_ff_bias_keeps_static_prompt_table():
    with torch.device("meta"):
        m = LTXAVModel.from_config(TINY_25, _fp32_ops())
    keys = set(m.state_dict().keys())
    # ff_bias=False: the video FFN's two Linears lose their bias entirely.
    assert "transformer_blocks.0.ff.net.0.proj.bias" not in keys
    assert "transformer_blocks.0.ff.net.2.bias" not in keys
    assert "transformer_blocks.0.ff.net.0.proj.weight" in keys
    # audio_ff_bias=True (default): the audio FFN keeps its bias.
    assert "transformer_blocks.0.audio_ff.net.0.proj.bias" in keys
    assert "transformer_blocks.0.audio_ff.net.2.bias" in keys
    # use_prompt_adaln_single=False: the timestep-dependent MLP is dropped...
    assert "prompt_adaln_single.linear.weight" not in keys
    assert "audio_prompt_adaln_single.linear.weight" not in keys
    # ...but the per-block static table (has_prompt_adaln) is still present,
    # at the widened 9-row size (has_prompt_adaln, independent of the MLP).
    assert m.transformer_blocks[0].scale_shift_table.shape[0] == 9
    assert "transformer_blocks.0.prompt_scale_shift_table" in keys
    assert "transformer_blocks.0.audio_prompt_scale_shift_table" in keys
    # LTX-2.5.1+ generated-keyframe embedding.
    assert "keyframes_abs_pos_embedding" in keys
    assert m.config.model_version == (2, 5)


def test_use_prompt_adaln_single_true_keeps_the_mlp():
    cfg = dict(TINY_25, use_prompt_adaln_single=True)
    with torch.device("meta"):
        m = LTXAVModel.from_config(cfg, _fp32_ops())
    keys = set(m.state_dict().keys())
    assert "prompt_adaln_single.linear.weight" in keys
    assert "audio_prompt_adaln_single.linear.weight" in keys


def test_use_keyframes_abs_pos_embedding_false_omits_key():
    cfg = dict(TINY_25, use_keyframes_abs_pos_embedding=False)
    with torch.device("meta"):
        m = LTXAVModel.from_config(cfg, _fp32_ops())
    assert "keyframes_abs_pos_embedding" not in set(m.state_dict().keys())


def _tiny_25_sd(**overrides: torch.Tensor) -> dict[str, torch.Tensor]:
    src = LTXAVModel.from_config(TINY_25, _fp32_ops())
    sd = {k: torch.zeros(v.shape, dtype=v.dtype) for k, v in src.state_dict().items()}
    sd.update(overrides)
    return sd


def test_25_checkpoint_without_keyframes_weight_loads_and_zero_materialises():
    # Shipped 2.5.0 checkpoints declare use_keyframes_abs_pos_embedding in their
    # embedded config while only 2.5.1+ carries the weight: the load must accept
    # the missing key (ltxav expected_missing_keys) and post_load must leave a
    # REAL zero parameter, or _assert_no_meta rejects the module.
    sd = _tiny_25_sd()
    del sd["keyframes_abs_pos_embedding"]
    with torch.device("meta"):
        m = LTXAVModel.from_config(TINY_25, _fp32_ops())
    load_into_module(m, sd, match_model_spec({"image_model": "ltxav"}))
    p = m.keyframes_abs_pos_embedding
    assert not p.is_meta
    assert torch.all(p == 0)


def test_25_checkpoint_with_trained_keyframes_weight_keeps_it():
    trained = torch.full((1, TINY_25["num_attention_heads"] * TINY_25["attention_head_dim"]), 0.5)
    sd = _tiny_25_sd(keyframes_abs_pos_embedding=trained)
    with torch.device("meta"):
        m = LTXAVModel.from_config(TINY_25, _fp32_ops())
    load_into_module(m, sd, match_model_spec({"image_model": "ltxav"}))
    assert torch.all(m.keyframes_abs_pos_embedding == 0.5)


def test_get_ada_values_none_timestep_uses_static_table():
    """Unit-level check of the ``use_prompt_adaln_single=False`` fallback: with
    ``timestep=None``, ``get_ada_values`` must broadcast the table directly (no
    per-token addition), cast to ``ref``'s dtype/device -- diffusers
    ``transformer_ltx2.py``'s ``temb_prompt is None`` branch."""
    from src.platform.runtime.native.arch.ltx.model import BasicAVTransformerBlock

    block = BasicAVTransformerBlock(
        v_dim=8, a_dim=4, v_heads=2, a_heads=2, vd_head=4, ad_head=2,
        v_context_dim=8, a_context_dim=4, operations=_fp32_ops(), has_prompt_adaln=True,
    )
    with torch.no_grad():
        for p in block.parameters():
            p.copy_(torch.randn_like(p) * 0.02)
    ref = torch.randn(1, 5, 8)
    shift, scale = block.get_ada_values(
        block.prompt_scale_shift_table, 1, None, slice(0, 2), ref=ref)
    expected = block.prompt_scale_shift_table[0:2][None, None].to(device=ref.device, dtype=ref.dtype)
    assert torch.allclose(shift, expected[:, :, 0])
    assert torch.allclose(scale, expected[:, :, 1])
    assert shift.dtype == ref.dtype and shift.device == ref.device


# --- registry -------------------------------------------------------------

def test_registry_specs():
    # ltxav (2.x): shift is exp(2.05), pinned by diffusers LTX2Pipeline's
    # dynamic-shifting mu (see registry.py comment). ltxv (0.9.x) keeps the
    # ComfyUI-LTXV 2.37 constant.
    expected_shift = {"ltxav": 7.767901106306771, "ltxv": 2.37}
    for image_model, variant in (("ltxav", "ltxav"), ("ltxv", "ltxv")):
        spec = match_model_spec({"image_model": image_model})
        assert spec.family == "ltx" and spec.variant == variant
        assert spec.sampling_settings["shift"] == pytest.approx(expected_shift[variant])
        assert spec.sampling_settings["guidance"] == "cfg"
        # nvfp4 second-level scale is allowlisted.
        assert spec.key_is_expected_unexpected("transformer_blocks.0.attn1.to_q.weight_scale_2")


# --- detection + real-header parity --------------------------------------

@pytest.mark.requires_models
@pytest.mark.parametrize("name,gated,prompt", [
    ("ltx-2-19b-dev-fp8.safetensors", False, False),
    ("ltx-2.3-22b-dev-fp8.safetensors", True, True),
    ("ltx-2.3-22b-dev-nvfp4.safetensors", True, True),
])
def test_real_checkpoint_detect_and_parity(name, gated, prompt):
    path = _CKPT_DIR / name
    if not path.is_file():
        pytest.skip(f"real checkpoint not present: {name}")
    cfg = _detect_from_header(path)
    assert cfg["image_model"] == "ltxav"
    assert cfg["blocks_gated"] is gated
    assert cfg["has_prompt_adaln"] is prompt
    assert cfg["use_cross_timestep"] is prompt          # rides the 2.3 marker
    # 2.3's per-stream audio connector runs at the audio head dim (64), the
    # 19b shared 3840 connector at 128.
    assert cfg["audio_connector_attention_head_dim"] == (64 if prompt else 128)
    ops = pick_operations(torch.float8_e4m3fn, torch.bfloat16)
    with torch.device("meta"):
        m = LTXAVModel.from_config(cfg, ops)
    built = {k: tuple(v.shape) for k, v in m.state_dict().items()}
    real = _real_dit_tensors(path)
    missing, extra = sorted(real.keys() - built.keys()), sorted(built.keys() - real.keys())
    assert not missing and not extra, f"missing={missing[:12]} extra={extra[:12]}"
    # Shape parity too (names alone let a 6-row-vs-9-row table mismatch through).
    # nvfp4 packs two 4-bit values per U8 byte — the stored last dim is halved,
    # so shape-compare only tensors stored unpacked.
    mismatched = [
        (k, built[k], shape) for k, (shape, dtype) in real.items()
        if dtype != "U8" and built[k] != shape
    ]
    assert not mismatched, f"shape mismatches: {mismatched[:8]}"


# --- LTX-2.5 detection (metadata-driven + shape-sniff fallback) -----------

def _minimal_ltx_sd(*, gated: bool = False, has_prompt_adaln: bool = False,
                     use_prompt_adaln_single: bool = True, ff_bias: bool = True,
                     audio_ff_bias: bool = True, keyframes: bool = False) -> dict:
    """A hand-built (meta-device) AV state dict with only the keys ``_detect_ltx``
    reads, real head-dim arch constants (128 video / 64 audio, 1 head each) so
    the derived config is internally consistent."""
    inner, audio_inner = 128, 64
    sd = {
        "adaln_single.emb.timestep_embedder.linear_1.weight": torch.empty(inner, 256, device="meta"),
        "patchify_proj.weight": torch.empty(inner, 16, device="meta"),
        "proj_out.weight": torch.empty(16, inner, device="meta"),
        "transformer_blocks.0.attn2.to_k.weight": torch.empty(inner, 16, device="meta"),
        "transformer_blocks.0.ff.net.2.weight": torch.empty(inner, inner * 4, device="meta"),
        "audio_adaln_single.linear.weight": torch.empty(audio_inner, audio_inner, device="meta"),
        "audio_patchify_proj.weight": torch.empty(audio_inner, 16, device="meta"),
        "transformer_blocks.0.audio_attn2.to_k.weight": torch.empty(audio_inner, 8, device="meta"),
        "transformer_blocks.0.audio_ff.net.2.weight": torch.empty(audio_inner, audio_inner * 4, device="meta"),
    }
    if gated:
        sd["transformer_blocks.0.attn1.to_gate_logits.weight"] = torch.empty(2, inner, device="meta")
    if has_prompt_adaln:
        sd["transformer_blocks.0.prompt_scale_shift_table"] = torch.empty(2, inner, device="meta")
        sd["transformer_blocks.0.audio_prompt_scale_shift_table"] = torch.empty(2, audio_inner, device="meta")
        if use_prompt_adaln_single:
            sd["prompt_adaln_single.linear.weight"] = torch.empty(2 * inner, inner, device="meta")
            sd["audio_prompt_adaln_single.linear.weight"] = torch.empty(2 * audio_inner, audio_inner, device="meta")
    if ff_bias:
        sd["transformer_blocks.0.ff.net.2.bias"] = torch.empty(inner, device="meta")
    if audio_ff_bias:
        sd["transformer_blocks.0.audio_ff.net.2.bias"] = torch.empty(audio_inner, device="meta")
    if keyframes:
        sd["keyframes_abs_pos_embedding"] = torch.empty(1, inner, device="meta")
    return sd


def test_detect_ltx25_shape_sniff_without_metadata():
    """No embedded metadata: the new 2.5 flags must still be read correctly
    from key presence/absence alone, and has_prompt_adaln must come from the
    per-block table, not the (here absent) top-level MLP."""
    sd = _minimal_ltx_sd(gated=True, has_prompt_adaln=True, use_prompt_adaln_single=False,
                         ff_bias=False, audio_ff_bias=True)
    cfg = detect_unet_config(sd)
    assert cfg["image_model"] == "ltxav"
    assert cfg["blocks_gated"] is True
    assert cfg["has_prompt_adaln"] is True
    assert cfg["use_prompt_adaln_single"] is False
    assert cfg["ff_bias"] is False
    assert cfg["audio_ff_bias"] is True
    assert cfg["use_keyframes_abs_pos_embedding"] is False
    assert cfg["model_version"] is None
    # Must build without error (state-dict key parity is the real proof).
    ops = pick_operations(torch.float32, torch.float32)
    with torch.device("meta"):
        LTXAVModel.from_config(cfg, ops)


def test_detect_ltx_prompt_adaln_without_mlp_is_not_misdetected_as_absent():
    """The bug this guards: has_prompt_adaln must stay True even when the
    top-level prompt_adaln_single MLP is absent (LTX-2.5's
    use_prompt_adaln_single=False) -- reading the old top-level-only signal
    would silently undersize the per-block scale_shift_table (6 rows instead
    of the real checkpoint's 9) and drop prompt_scale_shift_table entirely."""
    sd = _minimal_ltx_sd(gated=True, has_prompt_adaln=True, use_prompt_adaln_single=False)
    cfg = detect_unet_config(sd)
    assert cfg["has_prompt_adaln"] is True
    assert cfg["use_cross_timestep"] is True  # rides has_prompt_adaln, unaffected


def test_detect_ltx_metadata_overrides_shape_sniff():
    """Embedded config.transformer JSON must win over shape-sniffed defaults for
    the fields it declares (shape-sniffing alone can't distinguish "explicitly
    True" from "defaulted True")."""
    sd = _minimal_ltx_sd(gated=True, has_prompt_adaln=True, use_prompt_adaln_single=True,
                         ff_bias=True, audio_ff_bias=True)
    metadata = {
        "config": json.dumps({"transformer": {
            "ff_bias": False, "audio_ff_bias": False,
            "use_prompt_adaln_single": False,
            "use_keyframes_abs_pos_embedding": True,
        }}),
        "model_version": "2.5",
    }
    cfg = detect_unet_config(sd, metadata)
    assert cfg["ff_bias"] is False
    assert cfg["audio_ff_bias"] is False
    assert cfg["use_prompt_adaln_single"] is False
    assert cfg["use_keyframes_abs_pos_embedding"] is True
    assert cfg["model_version"] == (2, 5)


def test_detect_ltx_model_version_falls_back_to_raw_string():
    sd = _minimal_ltx_sd()
    cfg = detect_unet_config(sd, {"model_version": "2.5.1-rc"})
    assert cfg["model_version"] == "2.5.1-rc"


def test_detect_ltx_no_metadata_is_none_version():
    sd = _minimal_ltx_sd()
    assert detect_unet_config(sd)["model_version"] is None
    assert detect_unet_config(sd, {})["model_version"] is None


def test_detect_ltx_malformed_metadata_config_falls_back_to_shape_sniff():
    sd = _minimal_ltx_sd(ff_bias=False)
    cfg = detect_unet_config(sd, {"config": "not json"})
    assert cfg["ff_bias"] is False   # shape-sniffed value survives a bad metadata blob


def test_ltx_detection_no_collision():
    # a flux2 signature must not detect as LTX.
    flux2 = {
        "double_stream_modulation_img.lin.weight": torch.empty(4, 4, device="meta"),
        "double_blocks.0.img_attn.norm.key_norm.scale": torch.empty(4, device="meta"),
        "img_in.weight": torch.empty(8, 8, device="meta"),
        "txt_in.weight": torch.empty(8, 8, device="meta"),
    }
    assert detect_unet_config(flux2)["image_model"] == "flux2"
