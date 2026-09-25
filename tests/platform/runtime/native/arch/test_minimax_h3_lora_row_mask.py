from __future__ import annotations

import copy

import torch

from src.platform.runtime.native.lora import FULL, ROWS, SKIP, apply_loras

from tests.platform.runtime.native.arch.test_minimax_h3_model import TINY_FULL, _build_ready

HIDDEN = TINY_FULL["hidden_size"]
INNER = TINY_FULL["num_attention_heads"] * TINY_FULL["attention_head_dim"]
FFN = TINY_FULL["ffn_dim"]
LAST = TINY_FULL["num_layers"] - 1
SHAPES = {
    "attn.qkv_proj": (3 * INNER, HIDDEN),
    "attn.out_proj": (HIDDEN, INNER),
    "mlp.fc1": (2 * FFN, HIDDEN),
    "mlp.fc2": (HIDDEN, FFN),
}


def _layout():
    text = torch.tensor([0, 1])
    video = torch.tensor([2, 4, 5, 7])
    audio = torch.tensor([3, 6, 8])
    seq_len = 9
    tags = torch.zeros(seq_len, dtype=torch.long)
    tags[text] = 1
    tags[audio] = 2
    return dict(
        text_indices=text, video_indices=video, audio_indices=audio, token_tags=tags,
        timestep_indices=torch.zeros(seq_len, dtype=torch.long),
        position_ids=torch.rand(seq_len, 3, generator=torch.Generator().manual_seed(7), dtype=torch.float64),
    )


def _inputs(layout):
    g = torch.Generator().manual_seed(11)
    return (
        torch.randn(1, layout["video_indices"].numel(), TINY_FULL["in_channels"] * 4, generator=g),
        torch.randn(1, layout["audio_indices"].numel(), TINY_FULL["audio_in_channels"], generator=g),
        torch.randn(1, layout["text_indices"].numel(), TINY_FULL["text_dim"], generator=g),
    )


def _forward(model, **kwargs):
    layout = _layout()
    video, audio, text = _inputs(layout)
    with torch.no_grad():
        return model(
            video, audio, text, torch.tensor([0.4]), layout["timestep_indices"], layout["token_tags"],
            layout["position_ids"], layout["video_indices"], layout["audio_indices"],
            layout["text_indices"], **kwargs,
        )


def _lora(blocks, tails, seed=3):
    g = torch.Generator().manual_seed(seed)
    sd = {}
    for block in blocks:
        for tail in tails:
            out, inf = SHAPES[tail]
            stem = f"diffusion_model.blocks.{block}.{tail}"
            sd[f"{stem}.lora_down.weight"] = torch.randn(4, inf, generator=g) * 0.5
            sd[f"{stem}.lora_up.weight"] = torch.randn(out, 4, generator=g) * 0.5
    return sd


def _models():
    torch.manual_seed(0)
    base = _build_ready(TINY_FULL)
    return base, copy.deepcopy(base), copy.deepcopy(base)


def test_a_masked_per_row_lora_leaves_audio_bit_identical_and_matches_the_merge_on_video(monkeypatch):
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    base, merged, masked = _models()
    sd = _lora([LAST], ["attn.out_proj", "mlp.fc1", "mlp.fc2"])
    apply_loras(merged, [(sd, 1.0)])
    apply_loras(masked, [(sd, 1.0)], row_masked=[True])

    base_video, base_audio = _forward(base)
    merged_video, _ = _forward(merged)
    masked_video, masked_audio = _forward(masked)

    assert torch.equal(masked_audio, base_audio)
    assert torch.allclose(masked_video, merged_video, atol=1e-5)
    assert not torch.allclose(masked_video, base_video, atol=1e-3)


def test_the_masked_path_survives_sequence_chunking(monkeypatch):
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    _, _, masked = _models()
    sd = _lora(range(TINY_FULL["num_layers"]), list(SHAPES))
    apply_loras(masked, [(sd, 1.0)], row_masked=[True])

    whole_video, whole_audio = _forward(masked)
    chunked_video, chunked_audio = _forward(masked, seq_chunk_rows=2)

    assert torch.allclose(chunked_video, whole_video, atol=1e-5)
    assert torch.allclose(chunked_audio, whole_audio, atol=1e-5)


def test_the_masked_lora_changes_audio_only_through_attention(monkeypatch):
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    base, merged, masked = _models()
    sd = _lora([0], ["attn.qkv_proj"])
    apply_loras(merged, [(sd, 1.0)])
    apply_loras(masked, [(sd, 1.0)], row_masked=[True])

    _, base_audio = _forward(base)
    _, merged_audio = _forward(merged)
    _, masked_audio = _forward(masked)

    assert not torch.allclose(masked_audio, merged_audio, atol=1e-4)
    assert not torch.equal(masked_audio, base_audio)


def test_row_mask_targets():
    model = _build_ready(TINY_FULL)
    target = model.lora_row_mask_target
    for tail in ("attn.qkv_proj", "attn.out_proj", "mlp.fc1", "mlp.fc2", "attn.linear.softmax_gate.up"):
        assert target(f"blocks.0.{tail}") is ROWS
    for stem in ("audio_patch_proj", "final_layer.audio_out", "final_layer.adaln_proj.linear",
                 "time_embedder.proj_in"):
        assert target(stem) is SKIP
    for stem in ("video_patch_proj", "condition_proj", "final_layer.video_out",
                 "token_refiner.blocks.0.attn.qkv_proj", "blocks.0.attn.linear.to_out_linear"):
        assert target(stem) is FULL

    adaln = target("blocks.1.adaln_proj.linear")
    width = 6 * HIDDEN
    assert adaln.mode == "full"
    assert adaln.keep_columns.shape == (3 * width,)
    assert adaln.keep_columns[: 2 * width].all()
    assert not adaln.keep_columns[2 * width:].any()


def test_a_masked_lora_never_touches_audio_only_weights_or_the_audio_modulation(monkeypatch):
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    model = _build_ready(TINY_FULL)
    g = torch.Generator().manual_seed(5)
    width = 6 * HIDDEN
    t_dim = TINY_FULL["time_embed_dim"]
    sd = {
        "diffusion_model.audio_patch_proj.lora_down.weight": torch.randn(2, TINY_FULL["audio_in_channels"], generator=g),
        "diffusion_model.audio_patch_proj.lora_up.weight": torch.randn(HIDDEN, 2, generator=g),
        "diffusion_model.blocks.0.adaln_proj.linear.lora_down.weight": torch.randn(2, t_dim, generator=g),
        "diffusion_model.blocks.0.adaln_proj.linear.lora_up.weight": torch.randn(3 * width, 2, generator=g),
    }
    audio_before = model.audio_patch_proj.weight.clone()
    adaln_before = model.blocks[0].adaln_proj.linear.weight.clone()
    apply_loras(model, [(sd, 1.0)], row_masked=[True])

    adaln_after = model.blocks[0].adaln_proj.linear.weight
    assert torch.equal(model.audio_patch_proj.weight, audio_before)
    assert torch.equal(adaln_after[2 * width:], adaln_before[2 * width:])
    assert not torch.allclose(adaln_after[: 2 * width], adaln_before[: 2 * width])
