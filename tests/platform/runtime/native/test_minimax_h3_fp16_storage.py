from __future__ import annotations

import logging

import pytest
import torch
import torch.nn.functional as F
from safetensors.torch import save_file

from src.platform.runtime.native.engine import NativeEngineLoader

from ._quant_layouts import (
    convrot_descriptor,
    descriptor_blob,
    reference_hadamard,
    reference_quantize_convrot,
)

HIDDEN = 256
HEADS = 2
HEAD_DIM = 128
INNER = HEADS * HEAD_DIM
FFN = 256
IN_CHANNELS = 24
VIDEO_PATCH_DIM = IN_CHANNELS * 4
AUDIO_IN_CHANNELS = 32
TEXT_DIM = 64
ROPE_FREQ_DIM = 16
NUM_LAYERS = 2
NUM_REFINER_LAYERS = 1
TIME_EMBED_DIM = 8
ADALN_GRID = 9
GROUP = 256
METADATA = {"format": "pt", "pruned": "curve_rank8", "quantized": "int8_tensorwise_convrot"}


def _int8_convrot(sd: dict, prefix: str, out_f: int, in_f: int, originals: dict) -> None:
    w = torch.randn(out_f, in_f) * 0.05
    codes, scale = reference_quantize_convrot(w, GROUP)
    sd[f"{prefix}.weight"] = codes
    sd[f"{prefix}.weight_scale"] = scale
    sd[f"{prefix}.comfy_quant"] = descriptor_blob(convrot_descriptor(GROUP))
    originals[prefix] = (codes, scale)


def _block(sd: dict, prefix: str, half: torch.dtype, originals: dict) -> None:
    _int8_convrot(sd, f"{prefix}attn.qkv_proj", 3 * INNER, HIDDEN, originals)
    _int8_convrot(sd, f"{prefix}attn.out_proj", HIDDEN, INNER, originals)
    _int8_convrot(sd, f"{prefix}mlp.fc1", 2 * FFN, HIDDEN, originals)
    _int8_convrot(sd, f"{prefix}mlp.fc2", HIDDEN, FFN, originals)
    sd[f"{prefix}attn.q_norm.weight"] = torch.ones(HEAD_DIM, dtype=half)
    sd[f"{prefix}attn.k_norm.weight"] = torch.ones(HEAD_DIM, dtype=half)
    sd[f"{prefix}norm1.weight"] = torch.ones(HIDDEN, dtype=half)
    sd[f"{prefix}norm2.weight"] = torch.ones(HIDDEN, dtype=half)


def singularity_layout(half: torch.dtype = torch.float16) -> tuple[dict, dict]:
    torch.manual_seed(3)
    originals: dict = {}
    sd: dict[str, torch.Tensor] = {
        "adaln_t_table": torch.randn(ADALN_GRID, TIME_EMBED_DIM) * 0.1,
        "video_patch_proj.weight": (torch.randn(HIDDEN, VIDEO_PATCH_DIM) * 0.05).to(torch.float16),
        "video_patch_proj.bias": torch.zeros(HIDDEN, dtype=torch.float16),
        "audio_patch_proj.weight": (torch.randn(HIDDEN, AUDIO_IN_CHANNELS) * 0.05).to(torch.float16),
        "audio_patch_proj.bias": torch.zeros(HIDDEN, dtype=torch.float16),
        "condition_proj.weight": (torch.randn(HIDDEN, TEXT_DIM) * 0.05).to(half),
        "condition_proj.bias": torch.zeros(HIDDEN, dtype=half),
        "final_layer.norm.weight": torch.ones(HIDDEN, dtype=half),
        "final_layer.adaln_proj.linear.weight": torch.randn(2 * HIDDEN, TIME_EMBED_DIM) * 0.01,
        "final_layer.adaln_proj.linear.bias": torch.zeros(2 * HIDDEN),
        "final_layer.video_out.weight": (torch.randn(VIDEO_PATCH_DIM, HIDDEN) * 0.05).to(torch.float16),
        "final_layer.video_out.bias": torch.zeros(VIDEO_PATCH_DIM, dtype=torch.float16),
        "final_layer.audio_out.weight": (torch.randn(AUDIO_IN_CHANNELS, HIDDEN) * 0.05).to(torch.float16),
        "final_layer.audio_out.bias": torch.zeros(AUDIO_IN_CHANNELS, dtype=torch.float16),
        "rope.inv_freq": torch.ones(ROPE_FREQ_DIM, dtype=torch.float16),
        "token_refiner.final_norm.weight": torch.ones(HIDDEN, dtype=half),
    }
    for i in range(NUM_LAYERS):
        _block(sd, f"blocks.{i}.", half, originals)
        sd[f"blocks.{i}.adaln_proj.linear.weight"] = torch.randn(6 * HIDDEN * 3, TIME_EMBED_DIM) * 0.01
        sd[f"blocks.{i}.adaln_proj.linear.bias"] = torch.zeros(6 * HIDDEN * 3)
    for i in range(NUM_REFINER_LAYERS):
        _block(sd, f"token_refiner.blocks.{i}.", half, originals)
    return sd, originals


@pytest.fixture(scope="module")
def checkpoint(tmp_path_factory):
    sd, originals = singularity_layout()
    path = tmp_path_factory.mktemp("h3_fp16") / "h3_int8_convrot_fp16_norms.safetensors"
    save_file(sd, str(path), metadata=METADATA)
    return path, originals


def _layout() -> dict[str, torch.Tensor]:
    text_n, video_n, audio_n = 2, 3, 2
    seq_len = text_n + video_n + audio_n
    text_indices = torch.arange(0, text_n)
    video_indices = torch.arange(text_n, text_n + video_n)
    audio_indices = torch.arange(text_n + video_n, seq_len)
    token_tags = torch.zeros(seq_len, dtype=torch.long)
    token_tags[text_indices] = 1
    token_tags[audio_indices] = 2
    return dict(
        timestep_indices=torch.zeros(seq_len, dtype=torch.long), token_tags=token_tags,
        position_ids=torch.rand(seq_len, 3, dtype=torch.float64),
        video_indices=video_indices, audio_indices=audio_indices, text_indices=text_indices,
    )


def _stream_dtype(model) -> torch.dtype:
    module = model.module
    seen: list[torch.dtype] = []
    handle = module.blocks[0].register_forward_pre_hook(lambda _m, args: seen.append(args[0].dtype))
    layout = _layout()
    dtype = model.compute_dtype
    try:
        with torch.inference_mode():
            module(
                torch.randn(1, 3, VIDEO_PATCH_DIM, dtype=dtype), torch.randn(1, 2, AUDIO_IN_CHANNELS, dtype=dtype),
                torch.randn(1, 2, TEXT_DIM, dtype=dtype), torch.tensor([0.5]),
                layout["timestep_indices"], layout["token_tags"], layout["position_ids"],
                layout["video_indices"], layout["audio_indices"], layout["text_indices"],
            )
    finally:
        handle.remove()
    return seen[0]


def test_fp16_norm_checkpoint_runs_the_block_stack_in_bf16(checkpoint):
    path, _ = checkpoint
    model = NativeEngineLoader(device="cpu").load(path, "diffusion_model")

    assert model.compute_dtype == torch.bfloat16
    assert _stream_dtype(model) == torch.bfloat16


def test_fp16_storage_tensors_are_cast_to_the_compute_dtype_at_load(checkpoint):
    path, _ = checkpoint
    module = NativeEngineLoader(device="cpu").load(path, "diffusion_model").module

    assert module.condition_proj.weight.dtype == torch.bfloat16
    assert module.blocks[0].norm1.weight.dtype == torch.bfloat16
    assert module.token_refiner.final_norm.weight.dtype == torch.bfloat16
    assert module.adaln_t_table.dtype == torch.float32
    assert module.blocks[0].adaln_proj.linear.weight.dtype == torch.float32
    assert module.blocks[0].attn.qkv_proj.weight.dtype == torch.int8
    assert module.blocks[0].attn.qkv_proj.weight_scale.dtype == torch.float32


def test_bf16_half_storage_keeps_its_fp16_tensors(tmp_path):
    sd, _ = singularity_layout(half=torch.bfloat16)
    path = tmp_path / "h3_int8_convrot_bf16_norms.safetensors"
    save_file(sd, str(path), metadata=METADATA)
    module = NativeEngineLoader(device="cpu").load(path, "diffusion_model").module

    assert module.condition_proj.weight.dtype == torch.bfloat16
    assert module.video_patch_proj.weight.dtype == torch.float16
    assert module.final_layer.video_out.weight.dtype == torch.float16


def test_requested_compute_dtype_is_honoured_and_fp16_below_reference_warns(checkpoint, caplog):
    path, _ = checkpoint
    with caplog.at_level(logging.WARNING):
        model = NativeEngineLoader(device="cpu").load(path, "diffusion_model", compute_dtype="float16")

    assert model.compute_dtype == torch.float16
    assert _stream_dtype(model) == torch.float16
    assert any("reference dtype is torch.bfloat16" in r.getMessage() for r in caplog.records)


def test_block_stream_follows_the_compute_dtype_not_condition_proj_storage(checkpoint):
    path, _ = checkpoint
    model = NativeEngineLoader(device="cpu").load(path, "diffusion_model", compute_dtype="float32")

    assert model.module.condition_proj.weight.dtype == torch.float16
    assert _stream_dtype(model) == torch.float32


def test_bf16_request_does_not_warn(checkpoint, caplog):
    path, _ = checkpoint
    with caplog.at_level(logging.WARNING):
        NativeEngineLoader(device="cpu").load(path, "diffusion_model", compute_dtype="bfloat16")

    assert not any("reference dtype" in r.getMessage() for r in caplog.records)


def test_int8_convrot_linears_take_the_int8_convrot_dequant(checkpoint):
    path, originals = checkpoint
    model = NativeEngineLoader(device="cpu").load(path, "diffusion_model")
    linear = model.module.blocks[1].mlp.fc2
    codes, scale = originals["blocks.1.mlp.fc2"]
    rotated = codes.to(torch.float32) * scale
    hadamard = reference_hadamard(GROUP)
    reference = torch.matmul(rotated.reshape(HIDDEN, FFN // GROUP, GROUP), hadamard).reshape(HIDDEN, FFN)
    x = torch.randn(4, FFN)

    with torch.inference_mode():
        out = linear(x)

    assert linear.convrot_groupsize == GROUP
    torch.testing.assert_close(out, F.linear(x, reference), rtol=1e-5, atol=1e-5)
    assert not torch.allclose(out, F.linear(x, rotated), rtol=1e-2, atol=1e-2)


def test_profiler_label_names_the_per_layer_int8_convrot_format(checkpoint, monkeypatch):
    path, _ = checkpoint
    marks: list[tuple[str, dict]] = []

    class _Profiler:
        def mark(self, name, **fields):
            marks.append((name, fields))

    monkeypatch.setattr("src.platform.runtime.native.engine.get_profiler", lambda: _Profiler())
    NativeEngineLoader(device="cpu").load(path, "diffusion_model")

    built = dict(marks)["load.dit.built"]
    assert built["quant_layers"] == "int8_tensorwise+convrot256"
    assert built["compute_dtype"] == "bfloat16"
