from __future__ import annotations

import pytest
import torch

from src.platform.runtime.native.arch.qwen_image21.config import QwenImage21Config
from src.platform.runtime.native.arch.qwen_image21.model import QwenImage21DiT
from src.platform.runtime.native.kernels import pick_operations
from src.platform.runtime.native.lora import apply_loras, map_lora_keys, remove_loras
from src.platform.runtime.native.lora.key_mapping import _select_key_map, build_qwen_image21_lora_key_map

RANK = 4
CFG = QwenImage21Config(
    in_channels=8, out_channels=8, inner_dim=128, num_layers=2, num_attention_heads=1,
    attention_head_dim=128, joint_attention_dim=32,
)
HIDDEN = CFG.inner_dim * CFG.mlp_ratio


def _build() -> QwenImage21DiT:
    module = QwenImage21DiT(CFG, operations=pick_operations(torch.float32, torch.float32))
    with torch.no_grad():
        for param in module.parameters():
            param.copy_(torch.randn_like(param) * 0.02)
    module.eval()
    return module


def _peft(stem: str, out: int, inf: int, seed: int = 1):
    gen = torch.Generator().manual_seed(seed)
    return {
        f"{stem}.lora_A.weight": torch.randn(RANK, inf, generator=gen) * 0.1,
        f"{stem}.lora_B.weight": torch.randn(out, RANK, generator=gen) * 0.1,
    }


def test_select_key_map_routes_the_single_stream_module():
    km = _select_key_map(_build())
    assert km["transformer.transformer_blocks.0.img_mlp.gate_layer"] == (
        "transformer_blocks.0.img_mlp.gate_up.weight", (0, 0, HIDDEN),
    )


def test_split_mlp_spellings_map_onto_the_two_halves_of_gate_up():
    km = build_qwen_image21_lora_key_map(_build())
    for prefix in ("", "diffusion_model.", "transformer.", "base_model.model."):
        assert km[f"{prefix}transformer_blocks.1.img_mlp.gate_layer"] == (
            "transformer_blocks.1.img_mlp.gate_up.weight", (0, 0, HIDDEN),
        )
        assert km[f"{prefix}transformer_blocks.1.img_mlp.proj"] == (
            "transformer_blocks.1.img_mlp.gate_up.weight", (0, HIDDEN, HIDDEN),
        )


def test_the_fused_and_attention_spellings_still_map_whole():
    km = build_qwen_image21_lora_key_map(_build())
    assert km["transformer.transformer_blocks.0.img_mlp.gate_up"] == (
        "transformer_blocks.0.img_mlp.gate_up.weight", None,
    )
    assert km["transformer.transformer_blocks.0.attn.to_q"][1] is None


def test_a_diffusers_lora_with_split_mlp_maps_with_nothing_unmatched():
    module = _build()
    sd = {}
    sd.update(_peft("transformer.transformer_blocks.0.img_mlp.gate_layer", HIDDEN, CFG.inner_dim, 1))
    sd.update(_peft("transformer.transformer_blocks.0.img_mlp.proj", HIDDEN, CFG.inner_dim, 2))
    sd.update(_peft("transformer.transformer_blocks.0.attn.to_q", CFG.inner_dim, CFG.inner_dim, 3))
    mapped, unmatched = map_lora_keys(sd, module)
    assert unmatched == []
    assert sorted(d.target_slice for d in mapped["transformer_blocks.0.img_mlp.gate_up.weight"]) == [
        (0, 0, HIDDEN), (0, HIDDEN, HIDDEN),
    ]
    assert "transformer_blocks.0.attn.to_q.weight" in mapped


@pytest.mark.parametrize("part,row", [("gate_layer", 0), ("proj", 1)])
def test_apply_patches_only_its_half_of_gate_up_and_remove_restores(part, row):
    module = _build()
    weight = module.transformer_blocks[0].img_mlp.gate_up.weight
    before = weight.detach().clone()
    stem = f"transformer.transformer_blocks.0.img_mlp.{part}"
    sd = _peft(stem, HIDDEN, CFG.inner_dim, seed=7)
    patched, unmatched = apply_loras(module, [(sd, 0.5)])
    assert patched == 1 and unmatched == []
    scale = 0.5 * (sd[f"{stem}.lora_B.weight"] @ sd[f"{stem}.lora_A.weight"])
    expected = before.clone()
    expected[row * HIDDEN:(row + 1) * HIDDEN] += scale
    assert torch.allclose(weight, expected, atol=1e-5)
    other = 1 - row
    assert torch.equal(weight[other * HIDDEN:(other + 1) * HIDDEN], before[other * HIDDEN:(other + 1) * HIDDEN])
    remove_loras(module)
    assert torch.allclose(weight, before, atol=1e-6)
