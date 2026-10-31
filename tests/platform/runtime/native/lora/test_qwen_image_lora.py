from __future__ import annotations

import pytest
import torch
import torch.nn as nn

from src.platform.runtime.native.arch.qwen_image.model import QwenImageDiT
from src.platform.runtime.native.kernels import pick_operations
from src.platform.runtime.native.lora.apply import apply_loras_with_report

TINY = {
    "image_model": "qwen_image", "in_channels": 16, "out_channels": 4, "inner_dim": 128, "num_layers": 2,
    "num_attention_heads": 1, "attention_head_dim": 128, "joint_attention_dim": 12, "patch_size": 2,
    "axes_dims_rope": (16, 56, 56), "theta": 10000, "default_ref_method": "index_timestep_zero",
    "use_additional_t_cond": False,
}
TARGETS = (
    "txt_mod.1", "img_mod.1", "img_mlp.net.0.proj", "img_mlp.net.2", "txt_mlp.net.0.proj", "txt_mlp.net.2",
    "attn.to_q", "attn.to_k", "attn.to_v", "attn.to_out.0",
    "attn.add_q_proj", "attn.add_k_proj", "attn.add_v_proj", "attn.to_add_out",
)
RANK = 16
STRENGTH = 0.9
BF16_TARGET = "transformer_blocks.0.img_mod.1"


@pytest.fixture(autouse=True)
def _on_the_cpu(monkeypatch):
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)


def _dense_state() -> dict[str, torch.Tensor]:
    torch.manual_seed(0)
    shell = QwenImageDiT.from_config(TINY, pick_operations(torch.float32, torch.float32))
    state = {}
    for key, value in shell.state_dict().items():
        if key.endswith(".weight") and (".norm" in key or "txt_norm" in key):
            state[key] = torch.ones_like(value)
        elif value.is_floating_point():
            state[key] = torch.randn_like(value) * 0.05
        else:
            state[key] = value
    return state


def _fp8_mixed(dense: dict[str, torch.Tensor]) -> tuple[QwenImageDiT, dict[str, torch.Tensor]]:
    checkpoint, dequantized = {}, dict(dense)
    for key, value in dense.items():
        stem = key[: -len(".weight")]
        if key.startswith("transformer_blocks.") and key.endswith(".weight") and value.dim() == 2 and stem != BF16_TARGET:
            scale = value.abs().max() / 448.0
            checkpoint[key] = (value / scale).to(torch.float8_e4m3fn)
            checkpoint[f"{stem}.weight_scale"] = scale.reshape(())
            dequantized[key] = checkpoint[key].float() * scale
        else:
            checkpoint[key] = value.clone()
    module = QwenImageDiT.from_config(TINY, pick_operations(torch.float8_e4m3fn, torch.float32))
    module.load_state_dict(checkpoint, strict=False, assign=True)
    return module, dequantized


def _reference(state: dict[str, torch.Tensor]) -> QwenImageDiT:
    module = QwenImageDiT.from_config(TINY, pick_operations(torch.float32, torch.float32))
    module.load_state_dict({key: value.clone() for key, value in state.items()}, strict=True, assign=True)
    return module


def _fal_multiple_angles_lora(module: nn.Module, alpha: float | None = None) -> dict[str, torch.Tensor]:
    linears = dict(module.named_modules())
    generator = torch.Generator().manual_seed(3)
    lora = {}
    for block in range(TINY["num_layers"]):
        for target in TARGETS:
            linear = linears[f"transformer_blocks.{block}.{target}"]
            stem = f"transformer.transformer_blocks.{block}.{target}"
            lora[f"{stem}.lora_A.weight"] = (torch.randn(RANK, linear.in_features, generator=generator) * 0.05).bfloat16()
            lora[f"{stem}.lora_B.weight"] = (torch.randn(linear.out_features, RANK, generator=generator) * 0.05).bfloat16()
            if alpha is not None:
                lora[f"{stem}.alpha"] = torch.tensor(alpha)
    return lora


def _merge(reference: QwenImageDiT, lora: dict[str, torch.Tensor], coefficient: float) -> None:
    linears = dict(reference.named_modules())
    with torch.no_grad():
        for block in range(TINY["num_layers"]):
            for target in TARGETS:
                stem = f"transformer.transformer_blocks.{block}.{target}"
                up = lora[f"{stem}.lora_B.weight"].float()
                down = lora[f"{stem}.lora_A.weight"].float()
                linears[f"transformer_blocks.{block}.{target}"].weight.add_(coefficient * (up @ down))


def _edit_forward(module: QwenImageDiT) -> torch.Tensor:
    generator = torch.Generator().manual_seed(5)
    x = torch.randn(1, 4, 1, 8, 8, generator=generator)
    reference_latent = torch.randn(1, 4, 1, 8, 8, generator=generator)
    context = torch.randn(1, 5, 12, generator=generator)
    with torch.no_grad():
        return module(x, torch.tensor([0.7]), context, ref_latents=[reference_latent])


def test_every_multiple_angles_key_lands_on_its_own_linear():
    module, _dequantized = _fp8_mixed(_dense_state())
    lora = _fal_multiple_angles_lora(module)
    patched, unmatched, reports = apply_loras_with_report(module, [(lora, STRENGTH)], names=["angles"])
    assert unmatched == []
    assert patched == len(TARGETS) * TINY["num_layers"]
    assert (reports[0].inplace_params, reports[0].runtime_params) == (1, patched - 1)


@pytest.mark.parametrize("alpha", [None, 16.0, 4.0])
def test_fp8_mixed_edit_forward_matches_the_merged_lora(alpha):
    dense = _dense_state()
    module, dequantized = _fp8_mixed(dense)
    reference = _reference(dequantized)
    lora = _fal_multiple_angles_lora(module, alpha)
    apply_loras_with_report(module, [(lora, STRENGTH)], names=["angles"])
    _merge(reference, lora, STRENGTH * (RANK if alpha is None else alpha) / RANK)
    expected = _edit_forward(reference)
    assert torch.allclose(_edit_forward(module), expected, atol=1e-5, rtol=1e-4)
    assert not torch.allclose(_edit_forward(_reference(dequantized)), expected, atol=1e-3)
