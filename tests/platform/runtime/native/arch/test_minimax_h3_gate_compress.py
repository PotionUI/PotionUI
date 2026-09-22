"""FastVideo FastH3's ``to_gate_compress`` projection -- the VSA coarse-
attention gate present on FastH3's block stack and absent from every other
released MiniMax-H3 checkpoint (fl2va/ref2va, full or pruned).

This module is built and loaded so a FastH3 checkpoint's key set is
satisfied, but read nowhere in the dense/Sol/SLA forward paths -- VSA's own
sparse routing (coarse gate + 3D cube tiling) is a separate, not-yet-ported
feature. These tests cover exactly that contract: construction gated on the
config flag, real weight loading through the production ``load_into_module``
path, and forward-output parity between a model with the module present
(random weights) and one without it -- proving it is truly inert.
"""

from __future__ import annotations

import torch

from src.platform.runtime.native.arch.minimax_h3.config import MiniMaxH3Config
from src.platform.runtime.native.arch.minimax_h3.model import MiniMaxH3Model
from src.platform.runtime.native.base import load_into_module
from src.platform.runtime.native.detect.registry import match_model_spec
from vendor.gpl.comfyui.ops import pick_operations

TINY_COMMON = {
    "image_model": "minimax_h3", "hidden_size": 64, "num_layers": 2, "num_refiner_layers": 1,
    "num_attention_heads": 2, "attention_head_dim": 40, "ffn_dim": 48, "in_channels": 4,
    "audio_in_channels": 6, "patch_size": (1, 2, 2), "text_dim": 10, "rope_freq_dim": 3,
}
TINY_PRUNED = dict(TINY_COMMON, pruned=True, time_embed_dim=6, adaln_curve_grid=5)
TINY_PRUNED_GATED = dict(TINY_PRUNED, gate_compress=True)


def _fp32_ops():
    return pick_operations(torch.float32, torch.float32)


def _tiny_layout(text_n: int = 2, video_n: int = 3, audio_n: int = 2) -> dict[str, torch.Tensor]:
    seq_len = text_n + video_n + audio_n
    text_indices = torch.arange(0, text_n)
    video_indices = torch.arange(text_n, text_n + video_n)
    audio_indices = torch.arange(text_n + video_n, seq_len)
    token_tags = torch.zeros(seq_len, dtype=torch.long)
    token_tags[text_indices] = 1
    token_tags[audio_indices] = 2
    timestep_indices = torch.zeros(seq_len, dtype=torch.long)
    timestep_indices[audio_indices] = 1
    position_ids = torch.rand(seq_len, 3, dtype=torch.float64)
    return dict(
        text_indices=text_indices, video_indices=video_indices, audio_indices=audio_indices,
        token_tags=token_tags, timestep_indices=timestep_indices, position_ids=position_ids,
    )


def _forward(m: MiniMaxH3Model, config: dict, layout: dict, timestep: torch.Tensor,
             hidden_states: torch.Tensor, audio_hidden_states: torch.Tensor,
             encoder_hidden_states: torch.Tensor):
    return m(
        hidden_states, audio_hidden_states, encoder_hidden_states,
        timestep, layout["timestep_indices"], layout["token_tags"], layout["position_ids"],
        layout["video_indices"], layout["audio_indices"], layout["text_indices"],
    )


# --- construction -------------------------------------------------------------

def test_gate_compress_module_built_when_flag_true():
    with torch.device("meta"):
        m = MiniMaxH3Model.from_config(TINY_PRUNED_GATED, _fp32_ops())
    inner_dim = TINY_COMMON["num_attention_heads"] * TINY_COMMON["attention_head_dim"]
    for block in m.blocks:
        assert hasattr(block.attn, "to_gate_compress")
        assert tuple(block.attn.to_gate_compress.weight.shape) == (inner_dim, TINY_COMMON["hidden_size"])
    # never on the token refiner -- the real checkpoint carries no such key there.
    for block in m.token_refiner.blocks:
        assert not hasattr(block.attn, "to_gate_compress")


def test_gate_compress_module_absent_when_flag_false():
    with torch.device("meta"):
        m = MiniMaxH3Model.from_config(TINY_PRUNED, _fp32_ops())
    for block in m.blocks:
        assert not hasattr(block.attn, "to_gate_compress")


# --- weight loading -------------------------------------------------------------

def test_gate_compress_weights_load_from_a_header_shaped_state_dict():
    """Build a state dict shaped exactly like a real FastH3 header entry
    (``blocks.N.attn.to_gate_compress.weight`` present, real key spelling) and
    drive it through the production ``load_into_module`` path."""
    m = MiniMaxH3Model(MiniMaxH3Config.from_detect_config(TINY_PRUNED_GATED), _fp32_ops())
    sd = {}
    for k, v in m.state_dict().items():
        sd[k] = torch.ones_like(v) if ".norm" in k else torch.randn_like(v) * 0.02
    inner_dim = TINY_COMMON["num_attention_heads"] * TINY_COMMON["attention_head_dim"]
    gate_weight = torch.randn(inner_dim, TINY_COMMON["hidden_size"]) * 0.02
    sd["blocks.0.attn.to_gate_compress.weight"] = gate_weight.clone()

    load_into_module(m, sd, match_model_spec(TINY_PRUNED_GATED))

    torch.testing.assert_close(m.blocks[0].attn.to_gate_compress.weight, gate_weight)


# --- forward parity -- unused, so byte-identical output -------------------------

def test_forward_unchanged_with_gate_compress_present():
    """Two models sharing every OTHER weight, one carrying a (randomly
    weighted) ``to_gate_compress`` and one not -- since no forward path reads
    it, both must produce byte-identical output on the same input."""
    torch.manual_seed(0)
    base = MiniMaxH3Model(MiniMaxH3Config.from_detect_config(TINY_PRUNED), _fp32_ops())
    base_sd = {}
    for k, v in base.state_dict().items():
        base_sd[k] = torch.ones_like(v) if ".norm" in k else torch.randn_like(v) * 0.02
    load_into_module(base, base_sd, match_model_spec(TINY_PRUNED))
    base.eval()

    gated = MiniMaxH3Model(MiniMaxH3Config.from_detect_config(TINY_PRUNED_GATED), _fp32_ops())
    gated_sd = dict(base_sd)
    inner_dim = TINY_COMMON["num_attention_heads"] * TINY_COMMON["attention_head_dim"]
    for i in range(TINY_COMMON["num_layers"]):
        gated_sd[f"blocks.{i}.attn.to_gate_compress.weight"] = (
            torch.randn(inner_dim, TINY_COMMON["hidden_size"]) * 0.02
        )
    load_into_module(gated, gated_sd, match_model_spec(TINY_PRUNED_GATED))
    gated.eval()

    layout = _tiny_layout()
    video_patch_dim = TINY_COMMON["in_channels"] * 4
    hidden_states = torch.randn(1, layout["video_indices"].numel(), video_patch_dim)
    audio_hidden_states = torch.randn(1, layout["audio_indices"].numel(), TINY_COMMON["audio_in_channels"])
    encoder_hidden_states = torch.randn(1, layout["text_indices"].numel(), TINY_COMMON["text_dim"])
    timestep = torch.tensor([0.2, 0.9])

    base_video, base_audio = _forward(base, TINY_PRUNED, layout, timestep,
                                       hidden_states, audio_hidden_states, encoder_hidden_states)
    gated_video, gated_audio = _forward(gated, TINY_PRUNED_GATED, layout, timestep,
                                         hidden_states, audio_hidden_states, encoder_hidden_states)

    torch.testing.assert_close(base_video, gated_video)
    torch.testing.assert_close(base_audio, gated_audio)
