from __future__ import annotations

import pytest
import torch

from src.platform.runtime.native.arch.minimax_h3.config import MiniMaxH3Config
from src.platform.runtime.native.arch.minimax_h3.image import MiniMaxH3ImageModel
from src.platform.runtime.native.base import load_into_module
from src.platform.runtime.native.detect.registry import match_model_spec
from src.platform.runtime.native.kernels import pick_operations
from src.platform.runtime.native.lora import apply_loras, apply_loras_with_report, map_lora_keys, remove_loras
from src.platform.runtime.native.lora.key_mapping import _SWIGLU_HALF_SWAP, _select_key_map

GRID = (1.0, 0.9675236940383911, 0.8875485062599182, 0.75, 0.5327739119529724, 0.23200887441635132, 0.0)
HIDDEN = 64
INNER = 80
FFN = 48
TIME_EMBED = 6
RANK = 4
TINY = {
    "image_model": "minimax_h3_image", "hidden_size": HIDDEN, "num_layers": 2, "num_refiner_layers": 1,
    "num_attention_heads": 2, "attention_head_dim": 40, "ffn_dim": FFN, "in_channels": 4,
    "audio_in_channels": None, "patch_size": (1, 2, 2), "text_dim": 10, "rope_freq_dim": 3,
    "pruned": True, "time_embed_dim": TIME_EMBED, "adaln_curve_grid": 5, "pdd_steps": 6, "pdd_sigmas": GRID,
}
QKV = "blocks.0.attn.qkv_proj.weight"


def _rope(dim: int) -> torch.Tensor:
    return 1.0 / (10000.0 ** (torch.arange(0, 2 * dim, 2, dtype=torch.float32) / (2 * dim)))


def _build() -> MiniMaxH3ImageModel:
    model = MiniMaxH3ImageModel(
        MiniMaxH3Config.from_detect_config(TINY), pick_operations(torch.float32, torch.float32), dtype=torch.float32,
    )
    generator = torch.Generator().manual_seed(0)
    state = {}
    for key, value in model.state_dict().items():
        if ".norm" in key:
            state[key] = torch.ones_like(value)
        elif key == "rope.inv_freq":
            state[key] = _rope(TINY["rope_freq_dim"])
        else:
            state[key] = torch.randn(value.shape, generator=generator, dtype=value.dtype) * 0.02
    load_into_module(model, state, match_model_spec(TINY))
    return model.eval()


def _forward(model: MiniMaxH3ImageModel) -> torch.Tensor:
    generator = torch.Generator().manual_seed(3)
    x = torch.randn(1, 4, 1, 4, 6, generator=generator)
    context = torch.randn(1, 3, 10, generator=generator)
    with torch.no_grad():
        return model(x, torch.tensor([GRID[3]]), context, flow_map_target=torch.tensor([GRID[4]]))


def _pair(stem: str, out: int, inf: int, up: str, down: str, seed: int, alpha: bool = False) -> dict:
    generator = torch.Generator().manual_seed(seed)
    sd = {
        f"{stem}.{up}.weight": torch.randn(out, RANK, generator=generator) * 0.1,
        f"{stem}.{down}.weight": torch.randn(RANK, inf, generator=generator) * 0.1,
    }
    if alpha:
        sd[f"{stem}.alpha"] = torch.tensor(float(RANK))
    return sd


def _comfy(stem: str, out: int, inf: int, seed: int = 1) -> dict:
    return _pair(stem, out, inf, "lora_up", "lora_down", seed, alpha=True)


def _peft(stem: str, out: int, inf: int, seed: int = 1) -> dict:
    return _pair(stem, out, inf, "lora_B.default", "lora_A.default", seed)


def test_the_image_dit_takes_the_minimax_h3_key_map():
    key_map = _select_key_map(_build())
    assert key_map["transformer.transformer_blocks.0.attn.to_k"] == (QKV, (0, INNER, INNER))
    assert key_map["transformer.transformer_blocks.0.ff.net.0.proj"] == ("blocks.0.mlp.fc1.weight", _SWIGLU_HALF_SWAP)
    assert key_map["lora_unet_blocks_0_attn_qkv_proj"] == (QKV, None)


def test_diffusers_peft_keys_map_split_qkv_onto_three_row_slices():
    sd = {}
    for part, seed in (("to_q", 1), ("to_k", 2), ("to_v", 3)):
        sd.update(_peft(f"transformer.transformer_blocks.0.attn.{part}", INNER, HIDDEN, seed))
    sd.update(_peft("transformer.transformer_blocks.0.attn.to_out.0", HIDDEN, INNER, 4))
    sd.update(_peft("transformer.transformer_blocks.1.ff.net.2", HIDDEN, FFN, 5))
    sd.update(_peft("transformer.token_refiner.refiner_blocks.0.attn.to_q", INNER, HIDDEN, 6))
    mapped, unmatched = map_lora_keys(sd, _build())
    assert unmatched == []
    assert sorted(d.target_slice for d in mapped[QKV]) == [(0, 0, INNER), (0, INNER, INNER), (0, 2 * INNER, INNER)]
    assert set(mapped) == {
        QKV, "blocks.0.attn.out_proj.weight", "blocks.1.mlp.fc2.weight", "token_refiner.blocks.0.attn.qkv_proj.weight",
    }


@pytest.mark.parametrize("stem", [
    "diffusion_model.blocks.0.attn.qkv_proj",
    "lora_unet_blocks_0_attn_qkv_proj",
    "base_model.model.blocks.0.attn.qkv_proj",
    "blocks.0.attn.qkv_proj",
])
def test_comfy_and_kohya_spellings_map_the_fused_projection_whole(stem):
    mapped, unmatched = map_lora_keys(_comfy(stem, 3 * INNER, HIDDEN), _build())
    assert unmatched == []
    assert [d.target_slice for d in mapped[QKV]] == [None]


@pytest.mark.parametrize("part,row", [("to_q", 0), ("to_k", 1), ("to_v", 2)])
def test_apply_patches_only_the_matching_fused_rows_and_remove_restores(part, row):
    model = _build()
    weight = model.blocks[0].attn.qkv_proj.weight
    before = weight.detach().clone()
    stem = f"transformer.transformer_blocks.0.attn.{part}"
    sd = _peft(stem, INNER, HIDDEN, seed=7)
    patched, unmatched = apply_loras(model, [(sd, 0.5)])
    assert patched == 1 and unmatched == []
    expected = before.clone()
    expected[row * INNER:(row + 1) * INNER] += 0.5 * (
        sd[f"{stem}.lora_B.default.weight"] @ sd[f"{stem}.lora_A.default.weight"]
    )
    assert torch.allclose(weight, expected, atol=1e-6)
    untouched = torch.ones(3 * INNER, dtype=torch.bool)
    untouched[row * INNER:(row + 1) * INNER] = False
    assert torch.equal(weight[untouched], before[untouched])
    remove_loras(model)
    assert torch.allclose(weight, before, atol=1e-6)


def test_a_lora_changes_the_image_forward_and_removing_it_restores_it():
    model = _build()
    baseline = _forward(model)
    sd = _peft("transformer.transformer_blocks.0.attn.to_out.0", HIDDEN, INNER, seed=9)
    sd.update(_peft("transformer.transformer_blocks.1.ff.net.2", HIDDEN, FFN, seed=10))
    patched, unmatched = apply_loras(model, [(sd, 1.0)])
    assert patched == 2 and unmatched == []
    assert not torch.allclose(_forward(model), baseline)
    remove_loras(model)
    assert torch.allclose(_forward(model), baseline, atol=1e-6)


def test_a_video_lora_degrades_to_skipped_keys_instead_of_failing():
    model = _build()
    snapshot = {name: tensor.clone() for name, tensor in model.state_dict().items()}
    sd = {}
    sd.update(_peft("transformer.transformer_blocks.0.attn.to_q", INNER, HIDDEN, seed=1))
    sd.update(_peft("transformer.transformer_blocks.1.ff.net.2", HIDDEN, FFN, seed=2))
    sd.update(_peft("transformer.audio_patch_proj", HIDDEN, 6, seed=3))
    sd.update(_peft("transformer.final_layer.audio_out", 24, HIDDEN, seed=4))
    sd.update(_peft("transformer.time_embedder.linear_1", HIDDEN, 12, seed=5))
    sd.update(_peft("blocks.0.adaln_proj.linear", 6 * HIDDEN * 3, 12, seed=6))
    patched, unmatched, reports = apply_loras_with_report(model, [(sd, 1.0)], names=["video.safetensors"])
    assert patched == 2
    assert len(unmatched) == 4
    joined = " ".join(unmatched)
    for stem in ("audio_patch_proj", "audio_out", "time_embedder", "blocks.0.adaln_proj.linear"):
        assert stem in joined
    assert len(reports) == 1
    assert torch.equal(model.final_layer.video_out.weight, snapshot["final_layer.video_out.weight"])
    assert torch.equal(model.blocks[0].adaln_proj.linear.weight, snapshot["blocks.0.adaln_proj.linear.weight"])
    assert not torch.equal(model.blocks[1].mlp.fc2.weight, snapshot["blocks.1.mlp.fc2.weight"])


def test_a_lora_with_no_matching_key_applies_nothing_and_does_not_raise():
    model = _build()
    snapshot = {name: tensor.clone() for name, tensor in model.state_dict().items()}
    sd = _peft("transformer.audio_patch_proj", HIDDEN, 6)
    patched, unmatched = apply_loras(model, [(sd, 1.0)])
    assert patched == 0 and len(unmatched) == 1
    assert all(torch.equal(model.state_dict()[name], tensor) for name, tensor in snapshot.items())
