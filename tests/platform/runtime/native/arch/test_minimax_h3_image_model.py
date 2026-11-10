from __future__ import annotations

import logging

import pytest
import torch

from src.pipelines.pipes.generator.video_minimax_h3.layout import ReferenceBlock, build_ref2va_packed_sequence
from src.platform.runtime.native.arch.minimax_h3.config import MiniMaxH3Config
from src.platform.runtime.native.arch.minimax_h3.image import (
    W4A8_MESSAGE,
    MiniMaxH3ImageModel,
    ReferenceLatents,
    augment_reference_latent,
    build_image_layout,
    convert_minimax_h3_image_state_dict,
    pdd_head,
    pdd_interval,
    row_timesteps,
)
from src.platform.runtime.native.arch.minimax_h3.packing import (
    TEXT_TAG,
    VIDEO_TAG,
    patchify_video_latents,
    unpatchify_video_rows,
)
from src.platform.runtime.native.base import load_into_module
from src.platform.runtime.native.detect.registry import match_model_spec
from src.platform.runtime.native.errors import NativeEngineUnsupportedError
from src.platform.runtime.native.kernels import pick_operations

from .._header_listings import load_header

GRID = (1.0, 0.9675236940383911, 0.8875485062599182, 0.75, 0.5327739119529724, 0.23200887441635132, 0.0)
PATCH = (1, 2, 2)
REAL_CONFIG = {
    "image_model": "minimax_h3_image", "hidden_size": 5376, "num_layers": 50, "num_refiner_layers": 2,
    "num_attention_heads": 56, "attention_head_dim": 128, "ffn_dim": 14336, "in_channels": 24,
    "audio_in_channels": None, "patch_size": PATCH, "text_dim": 5120, "rope_freq_dim": 16,
    "pruned": True, "adaln_curve_grid": 1025, "time_embed_dim": 8, "pdd_steps": 6, "pdd_sigmas": GRID,
}
TINY = {
    "image_model": "minimax_h3_image", "hidden_size": 64, "num_layers": 2, "num_refiner_layers": 1,
    "num_attention_heads": 2, "attention_head_dim": 40, "ffn_dim": 48, "in_channels": 4,
    "audio_in_channels": None, "patch_size": PATCH, "text_dim": 10, "rope_freq_dim": 3,
    "pruned": True, "time_embed_dim": 6, "adaln_curve_grid": 5, "pdd_steps": 6, "pdd_sigmas": GRID,
}
QUANT_SIDECARS = (".weight_scale", ".comfy_quant")


def _meta_state(listing: str) -> dict[str, torch.Tensor]:
    dtypes = {"F32": torch.float32, "F16": torch.float16, "BF16": torch.bfloat16, "I8": torch.int8,
              "U8": torch.uint8, "F8_E4M3": torch.float8_e4m3fn}
    return {name: torch.empty(entry["shape"], dtype=dtypes[entry["dtype"]], device="meta")
            for name, entry in load_header(listing).items()}


def _rope(dim: int) -> torch.Tensor:
    return 1.0 / (10000.0 ** (torch.arange(0, 2 * dim, 2, dtype=torch.float32) / (2 * dim)))


def _tiny_model(**overrides) -> MiniMaxH3ImageModel:
    config = dict(TINY, **overrides)
    model = MiniMaxH3ImageModel(MiniMaxH3Config.from_detect_config(config), pick_operations(torch.float32, torch.float32),
                                dtype=torch.float32)
    generator = torch.Generator().manual_seed(0)
    state = {}
    for key, value in model.state_dict().items():
        if ".norm" in key:
            state[key] = torch.ones_like(value)
        elif key == "rope.inv_freq":
            state[key] = _rope(config["rope_freq_dim"])
        else:
            state[key] = torch.randn(value.shape, generator=generator, dtype=value.dtype) * 0.02
    load_into_module(model, state, match_model_spec(config))
    return model.eval()


def test_image_dit_has_exactly_the_bf16_checkpoints_keys():
    with torch.device("meta"):
        model = MiniMaxH3ImageModel.from_config(REAL_CONFIG, pick_operations(torch.bfloat16, torch.bfloat16))
    checkpoint = convert_minimax_h3_image_state_dict(_meta_state("minimax_h3_image/pruned_bf16"))
    built = {key: tuple(value.shape) for key, value in model.state_dict().items()}
    assert built == {key: tuple(value.shape) for key, value in checkpoint.items()}
    assert built["final_layer.video_out.weight"] == (576, 5376)
    assert not any("audio" in key for key in built)


def test_int8_checkpoint_has_the_same_keys_plus_quant_sidecars():
    with torch.device("meta"):
        model = MiniMaxH3ImageModel.from_config(REAL_CONFIG, pick_operations(torch.bfloat16, torch.bfloat16))
    checkpoint = convert_minimax_h3_image_state_dict(_meta_state("minimax_h3_image/pruned_int8_convrot"))
    weights = {key for key in checkpoint if not key.endswith(QUANT_SIDECARS)}
    assert weights == set(model.state_dict())
    sidecar_layers = {key.rsplit(".", 1)[0] for key in checkpoint if key.endswith(".comfy_quant")}
    assert len(sidecar_layers) == 50 * 4


def test_attention_hints_are_dropped_and_the_w4a8_file_is_refused():
    bf16 = _meta_state("minimax_h3_image/pruned_bf16")
    assert any(key.endswith(".comfy_attention.config") for key in bf16)
    assert not any(key.endswith(".comfy_attention.config") for key in convert_minimax_h3_image_state_dict(bf16))
    with pytest.raises(NativeEngineUnsupportedError, match="w4a8") as raised:
        convert_minimax_h3_image_state_dict(_meta_state("minimax_h3_image/pruned_w4a8"))
    assert str(raised.value) == W4A8_MESSAGE


def test_text_then_pictures_then_image_sit_on_the_reference_clock():
    tags = torch.full((5,), TEXT_TAG, dtype=torch.long)
    tags[2] = VIDEO_TAG
    layout = build_image_layout(tags, [(64, 64)], 128, 128, PATCH, "cpu")
    positions = layout.position_ids
    assert positions.shape == (5 + 32 * 32 + 64 * 64, 3)
    assert torch.equal(positions[:5], torch.tensor([[k, 0.0, 0.0] for k in range(5)], dtype=torch.float64))
    reference = positions[5:5 + 1024]
    assert torch.all(reference[:, 0] == 5.0)
    assert torch.equal(reference[:32, 2], torch.arange(32, dtype=torch.float64))
    target = positions[5 + 1024:]
    assert torch.all(target[:, 0] == 6.0)
    assert torch.equal(target[:64, 2], torch.arange(64, dtype=torch.float64) * 0.5)
    assert layout.target_slice == slice(1029, 1029 + 4096)
    assert layout.num_reference_rows == 1024
    assert torch.equal(layout.token_tags[:5], tags)
    assert torch.all(layout.token_tags[5:] == VIDEO_TAG)


def test_a_non_square_image_spans_the_same_area_normalised_grid():
    layout = build_image_layout(torch.full((1,), TEXT_TAG), [], 64, 128, PATCH, "cpu")
    target = layout.position_ids[1:]
    assert target[0, 1].item() == pytest.approx(4.686291501, abs=1e-6)
    assert target[0, 2].item() == pytest.approx(-6.627416998, abs=1e-6)
    assert (target[1, 2] - target[0, 2]).item() == pytest.approx(0.707106781, abs=1e-6)
    assert (target[64, 1] - target[0, 1]).item() == pytest.approx(0.707106781, abs=1e-6)


def test_pictures_stay_near_clean_while_text_and_image_follow_the_step():
    tags = torch.full((5,), TEXT_TAG, dtype=torch.long)
    layout = build_image_layout(tags, [(4, 4)], 4, 4, PATCH, "cpu")
    timesteps, indices = row_timesteps(layout, 0.75)
    assert timesteps.tolist() == pytest.approx([0.25, 0.999])
    adaln = indices * 3 + layout.token_tags
    assert set(adaln[:5].tolist()) == {1}
    assert set(adaln[5:9].tolist()) == {3}
    assert set(adaln[9:].tolist()) == {0}
    late, _ = row_timesteps(layout, 0.0)
    assert late.tolist() == pytest.approx([1.0])


def test_image_layout_matches_the_video_reference_layout_without_audio():
    tags = torch.full((7,), TEXT_TAG, dtype=torch.long)
    references = [torch.zeros(1, 4, 1, 6, 4), torch.zeros(1, 4, 1, 4, 8)]
    video = build_ref2va_packed_sequence(
        tags, [ReferenceBlock(kind="image"), ReferenceBlock(kind="image")], references, [],
        num_latent_frames=1, latent_height=8, latent_width=6, num_audio_latents=0, patch_size=PATCH, device="cpu",
    )
    image = build_image_layout(tags, [(6, 4), (4, 8)], 8, 6, PATCH, "cpu")
    assert torch.equal(image.position_ids, video.position_ids)
    assert torch.equal(image.token_tags, video.token_tags)
    assert torch.equal(image.video_indices, video.video_indices)
    assert image.num_reference_rows == video.num_condition_video_rows


def test_each_default_step_uses_its_own_output_head():
    assert [pdd_interval(GRID, GRID[i], GRID[i + 1]) for i in range(6)] == [(i, i + 1) for i in range(6)]
    three = [1.0, GRID[2], GRID[4], 0.0]
    assert [pdd_interval(GRID, a, b) for a, b in zip(three, three[1:])] == [(0, 2), (2, 4), (4, 6)]
    assert pdd_interval(GRID, 0.98, 0.97) == (1, 2)


def test_a_head_bank_step_adds_its_offsets_to_head_zero():
    rows = torch.randn(6, 3, 5, dtype=torch.float64)
    biases = torch.randn(6, 3, dtype=torch.float64)
    weight, bias = rows.reshape(18, 5), biases.reshape(18)
    first, first_bias = pdd_head(weight, bias, GRID, 0, 1)
    assert torch.equal(first, rows[0]) and torch.equal(first_bias, biases[0])
    third, third_bias = pdd_head(weight, bias, GRID, 2, 3)
    assert torch.allclose(third, rows[0] + rows[2]) and torch.allclose(third_bias, biases[0] + biases[2])
    merged, _ = pdd_head(weight, bias, GRID, 0, 2)
    share = (GRID[1] - GRID[2]) / (GRID[0] - GRID[2])
    assert torch.allclose(merged, rows[0] + share * rows[1])


def test_the_step_target_picks_the_head_and_the_output_is_negated():
    model = _tiny_model()
    with torch.no_grad():
        model.final_layer.video_out.weight.zero_()
        model.final_layer.video_out.bias.copy_(torch.arange(6, dtype=torch.float32).repeat_interleave(16) + 1.0)
    x = torch.randn(1, 4, 1, 4, 6)
    context = torch.randn(1, 3, 10)
    with torch.no_grad():
        third = model(x, torch.tensor([GRID[2]]), context, flow_map_target=torch.tensor([GRID[3]]))
        first = model(x, torch.tensor([GRID[0]]), context, flow_map_target=torch.tensor([GRID[1]]))
    assert third.shape == x.shape
    assert torch.allclose(third, torch.full_like(third, -4.0))
    assert torch.allclose(first, torch.full_like(first, -1.0))
    with pytest.raises(ValueError, match="target sigma"):
        model(x, torch.tensor([GRID[2]]), context)


def test_pictures_change_the_image_but_not_its_shape():
    model = _tiny_model()
    x = torch.randn(1, 4, 1, 4, 6)
    context = torch.randn(1, 3, 10)
    target = torch.tensor([GRID[4]])
    with torch.no_grad():
        plain = model(x, torch.tensor([GRID[3]]), context, flow_map_target=target)
        edited = model(x, torch.tensor([GRID[3]]), context, flow_map_target=target,
                       ref_latents=ReferenceLatents((torch.randn(1, 4, 1, 6, 2),)))
    assert edited.shape == plain.shape
    assert torch.isfinite(edited).all()
    assert not torch.allclose(edited, plain)


def test_the_prompt_is_refined_once_per_generation():
    model = _tiny_model()
    calls = []
    original = model._prepare_context

    def counting(context, dtype=None):
        calls.append(1)
        return original(context, dtype)

    model._prepare_context = counting
    x = torch.randn(1, 4, 1, 4, 4)
    context = torch.randn(1, 3, 10)
    with torch.no_grad():
        for i in range(3):
            model(x, torch.tensor([GRID[i]]), context, flow_map_target=torch.tensor([GRID[i + 1]]))
        model(x, torch.tensor([GRID[3]]), torch.randn(1, 3, 10), flow_map_target=torch.tensor([GRID[4]]))
    assert len(calls) == 2


def test_reference_noise_follows_the_generation_seed():
    latent = torch.randn(1, 4, 1, 4, 6)
    first = augment_reference_latent(latent, 7, PATCH)
    assert torch.equal(first, augment_reference_latent(latent, 7, PATCH))
    assert not torch.equal(first, augment_reference_latent(latent, 8, PATCH))
    rows = patchify_video_latents(latent, PATCH)
    noise = torch.randn(rows.shape, generator=torch.Generator("cpu").manual_seed(7))
    expected = unpatchify_video_rows(0.999 * rows + (1.0 - 0.999) * noise, num_latent_frames=1, latent_height=4,
                                     latent_width=6, channels=4, patch_size=PATCH)
    assert torch.allclose(first, expected)


def test_rope_frequencies_follow_the_checkpoint_when_they_differ(caplog):
    model = _tiny_model()
    assert torch.allclose(model.rope.inv_freq, _rope(3))
    model.rope.inv_freq = _rope(3) * 2.0
    with caplog.at_level(logging.WARNING):
        model.post_load()
    assert torch.allclose(model.rope.inv_freq, _rope(3) * 2.0)
    assert "rope.inv_freq" in caplog.text


def test_image_config_has_no_audio_branch_and_six_heads():
    model = _tiny_model()
    assert model.audio_patch_proj is None
    assert model.final_layer.audio_out is None
    assert model.final_layer.video_out.weight.shape == (6 * 16, 64)
    assert model.patch_size == 2
