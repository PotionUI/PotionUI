"""Full-load dry run for the MiniMax-H3 DiT through the REAL public loader path.

The key-parity tests in ``tests/platform/runtime/native/arch/test_minimax_h3_model.py``
prove the arch module's own construction matches the real checkpoint headers'
key sets. They do NOT prove the full pipeline the maintainer's real load
actually drives: ``NativeEngineLoader.load(path, "diffusion_model")`` ->
``load_torch_file`` -> ``detect_prefix``/``strip_prefix`` -> ``detect_unet_config``
-> ``match_model_spec`` -> ``detect_quant_format`` -> ``weight_dtype``/
``pick_dtypes`` -> ``_ops_for`` (ops namespace selection) -> meta construct ->
``load_into_module`` -> ``post_load``. A gap can live in any of the joints
between those calls even when each end (detection, construction) is separately
correct -- exactly the class of bug that hit the TE (a module built that the
real checkpoint has no keys for).

Both real checkpoint shapes are driven: pruned (fp8-scaled, per-layer
``comfy_quant``/``weight_scale``/``input_scale`` sidecars, NO file-level
``__metadata__`` at all -- verified directly against ``pruned_fp8_header.json``,
this is PORT_PLAN.md risk #2) and full (bf16 + f32 mixed precision, a
``__metadata__["config"]`` blob present but never consulted by this engine's
shape-only detection).

Memory: writing/loading REAL-size tensors for every one of the ~1080 real keys
would be ~19.5GB (pruned) / ~62GB (full) materialized on this shared box, which
the task explicitly rules out. Instead every hidden/inner/ffn/time-embed
dimension is shrunk (hidden_size 5376->192, num_attention_heads 56->2,
attention_head_dim kept at the REAL 128 -- it is the rotary floor,
2*3*rope_freq_dim(16)=96 must fit inside it -- ffn_dim 14336->384, time_embed_dim
2688->64), while the KEY SET (all 50 blocks, both refiner blocks, every
per-layer quant sidecar) and every key's DTYPE match the real header exactly.
This proves: every module the loader constructs has a real checkpoint key
covering it and vice versa (missing/unexpected-key integrity), quant-format
detection fires correctly from per-layer markers alone with no global
metadata, and post_load leaves no meta/garbage buffer. It CANNOT catch a bug
that only manifests at the real (5376/56/128/14336) scale (e.g. an OOM, or a
shape arithmetic bug that happens to divide evenly at both scales) -- the
meta-device REAL-size key-parity tests in test_minimax_h3_model.py cover that
axis instead. No forward pass is run (not needed to catch a load-integrity
gap, and the whole point of shrinking is to stay tiny).
"""

from __future__ import annotations

import json

import pytest
import torch
from safetensors.torch import save_file

from src.platform.runtime.native.arch.minimax_h3.model import MiniMaxH3Model
from src.platform.runtime.native.engine import NativeEngineLoader, NativeModel

# Real dims kept as-is: attention_head_dim (rotary floor), rope_freq_dim,
# in_channels/audio_in_channels/text_dim/patch_size (small already), block
# counts (the KEY SET must be exact). Shrunk: hidden_size, num_attention_heads,
# ffn_dim, time_embed_dim/freq_dim/time_embed_hidden_dim (full only).
HIDDEN = 192
HEADS = 2
HEAD_DIM = 128           # real value -- rotary floor (2*3*16=96 <= head_dim)
INNER = HEADS * HEAD_DIM
FFN = 384
IN_CHANNELS = 24
VIDEO_PATCH_DIM = IN_CHANNELS * 4
AUDIO_IN_CHANNELS = 32
TEXT_DIM = 5120
ROPE_FREQ_DIM = 16       # real value -- must match rope.inv_freq's real shape
NUM_LAYERS = 50          # real value -- key set must be exact
NUM_REFINER_LAYERS = 2   # real value
PRUNED_TIME_EMBED_DIM = 8       # real value (tiny already)
PRUNED_ADALN_GRID = 1025        # real value (tiny already)
FULL_TIME_EMBED_DIM = 64        # shrunk from 2688
FULL_FREQ_DIM = 64              # shrunk from 256
FULL_TIME_EMBED_HIDDEN = 64     # shrunk from 5376


def _fp8_linear(sd: dict, prefix: str, out_f: int, in_f: int, *,
                has_input_scale: bool, full_precision_matrix_mult: bool) -> None:
    """A quantised Linear's weight + its real per-layer sidecar trio, byte-exact
    to what ComfyUI's repack actually writes (verified against the real
    header's exact ``comfy_quant`` blob lengths: 27 bytes with just
    ``{"format": ...}``, 63 bytes when ``full_precision_matrix_mult`` is added
    -- ``fc2`` in the real file, the only quantised Linear with no
    ``input_scale``)."""
    sd[f"{prefix}.weight"] = torch.zeros(out_f, in_f, dtype=torch.float8_e4m3fn)
    sd[f"{prefix}.weight_scale"] = torch.tensor(1.0, dtype=torch.float32)
    if has_input_scale:
        sd[f"{prefix}.input_scale"] = torch.tensor(1.0, dtype=torch.float32)
    blob = {"format": "float8_e4m3fn"}
    if full_precision_matrix_mult:
        blob["full_precision_matrix_mult"] = True
    payload = json.dumps(blob).encode("utf-8")
    sd[f"{prefix}.comfy_quant"] = torch.tensor(list(payload), dtype=torch.uint8)


def _bf16_attn_mlp_block(sd: dict, prefix: str) -> None:
    """token_refiner blocks: REAL header keeps these bf16 even in the pruned/
    fp8 checkpoint -- no quant sidecars at all for token_refiner.*, verified
    directly against pruned_fp8_header.json."""
    sd[f"{prefix}attn.qkv_proj.weight"] = torch.zeros(3 * INNER, HIDDEN, dtype=torch.bfloat16)
    sd[f"{prefix}attn.q_norm.weight"] = torch.zeros(HEAD_DIM, dtype=torch.bfloat16)
    sd[f"{prefix}attn.k_norm.weight"] = torch.zeros(HEAD_DIM, dtype=torch.bfloat16)
    sd[f"{prefix}attn.out_proj.weight"] = torch.zeros(HIDDEN, INNER, dtype=torch.bfloat16)
    sd[f"{prefix}mlp.fc1.weight"] = torch.zeros(2 * FFN, HIDDEN, dtype=torch.bfloat16)
    sd[f"{prefix}mlp.fc2.weight"] = torch.zeros(HIDDEN, FFN, dtype=torch.bfloat16)
    sd[f"{prefix}norm1.weight"] = torch.zeros(HIDDEN, dtype=torch.bfloat16)
    sd[f"{prefix}norm2.weight"] = torch.zeros(HIDDEN, dtype=torch.bfloat16)


def _common_sd() -> dict[str, torch.Tensor]:
    sd: dict[str, torch.Tensor] = {
        "video_patch_proj.weight": torch.zeros(HIDDEN, VIDEO_PATCH_DIM, dtype=torch.float32),
        "video_patch_proj.bias": torch.zeros(HIDDEN, dtype=torch.float32),
        "audio_patch_proj.weight": torch.zeros(HIDDEN, AUDIO_IN_CHANNELS, dtype=torch.float32),
        "audio_patch_proj.bias": torch.zeros(HIDDEN, dtype=torch.float32),
        "condition_proj.weight": torch.zeros(HIDDEN, TEXT_DIM, dtype=torch.bfloat16),
        "condition_proj.bias": torch.zeros(HIDDEN, dtype=torch.bfloat16),
        "rope.inv_freq": torch.zeros(ROPE_FREQ_DIM, dtype=torch.float32),
        "final_layer.norm.weight": torch.zeros(HIDDEN, dtype=torch.bfloat16),
        "final_layer.video_out.weight": torch.zeros(VIDEO_PATCH_DIM, HIDDEN, dtype=torch.float32),
        "final_layer.video_out.bias": torch.zeros(VIDEO_PATCH_DIM, dtype=torch.float32),
        "final_layer.audio_out.weight": torch.zeros(AUDIO_IN_CHANNELS, HIDDEN, dtype=torch.float32),
        "final_layer.audio_out.bias": torch.zeros(AUDIO_IN_CHANNELS, dtype=torch.float32),
        "token_refiner.final_norm.weight": torch.zeros(HIDDEN, dtype=torch.bfloat16),
    }
    for i in range(NUM_REFINER_LAYERS):
        _bf16_attn_mlp_block(sd, f"token_refiner.blocks.{i}.")
    return sd


def _pruned_sd() -> dict[str, torch.Tensor]:
    sd = _common_sd()
    sd["adaln_t_table"] = torch.zeros(PRUNED_ADALN_GRID, PRUNED_TIME_EMBED_DIM, dtype=torch.float32)
    sd["final_layer.adaln_proj.linear.weight"] = torch.zeros(
        2 * HIDDEN, PRUNED_TIME_EMBED_DIM, dtype=torch.float16)
    sd["final_layer.adaln_proj.linear.bias"] = torch.zeros(2 * HIDDEN, dtype=torch.float16)
    for i in range(NUM_LAYERS):
        p = f"blocks.{i}."
        _fp8_linear(sd, f"{p}attn.qkv_proj", 3 * INNER, HIDDEN, has_input_scale=True, full_precision_matrix_mult=False)
        sd[f"{p}attn.q_norm.weight"] = torch.zeros(HEAD_DIM, dtype=torch.bfloat16)
        sd[f"{p}attn.k_norm.weight"] = torch.zeros(HEAD_DIM, dtype=torch.bfloat16)
        _fp8_linear(sd, f"{p}attn.out_proj", HIDDEN, INNER, has_input_scale=True, full_precision_matrix_mult=False)
        _fp8_linear(sd, f"{p}mlp.fc1", 2 * FFN, HIDDEN, has_input_scale=True, full_precision_matrix_mult=False)
        _fp8_linear(sd, f"{p}mlp.fc2", HIDDEN, FFN, has_input_scale=False, full_precision_matrix_mult=True)
        sd[f"{p}norm1.weight"] = torch.zeros(HIDDEN, dtype=torch.bfloat16)
        sd[f"{p}norm2.weight"] = torch.zeros(HIDDEN, dtype=torch.bfloat16)
        sd[f"{p}adaln_proj.linear.weight"] = torch.zeros(6 * HIDDEN * 3, PRUNED_TIME_EMBED_DIM, dtype=torch.float16)
        sd[f"{p}adaln_proj.linear.bias"] = torch.zeros(6 * HIDDEN * 3, dtype=torch.float16)
    return sd


def _full_sd() -> dict[str, torch.Tensor]:
    sd = _common_sd()
    sd["time_embedder.proj_in.weight"] = torch.zeros(FULL_TIME_EMBED_HIDDEN, FULL_FREQ_DIM, dtype=torch.float32)
    sd["time_embedder.proj_in.bias"] = torch.zeros(FULL_TIME_EMBED_HIDDEN, dtype=torch.float32)
    sd["time_embedder.proj_out.weight"] = torch.zeros(FULL_TIME_EMBED_DIM, FULL_TIME_EMBED_HIDDEN, dtype=torch.float32)
    sd["time_embedder.proj_out.bias"] = torch.zeros(FULL_TIME_EMBED_DIM, dtype=torch.float32)
    sd["final_layer.adaln_proj.linear.weight"] = torch.zeros(2 * HIDDEN, FULL_TIME_EMBED_DIM, dtype=torch.bfloat16)
    sd["final_layer.adaln_proj.linear.bias"] = torch.zeros(2 * HIDDEN, dtype=torch.bfloat16)
    for i in range(NUM_LAYERS):
        p = f"blocks.{i}."
        _bf16_attn_mlp_block(sd, p)
        sd[f"{p}adaln_proj.linear.weight"] = torch.zeros(6 * HIDDEN * 3, FULL_TIME_EMBED_DIM, dtype=torch.bfloat16)
        sd[f"{p}adaln_proj.linear.bias"] = torch.zeros(6 * HIDDEN * 3, dtype=torch.bfloat16)
    return sd


@pytest.fixture(scope="module")
def pruned_dit_path(tmp_path_factory):
    path = tmp_path_factory.mktemp("minimax_h3") / "minimax_h3_pruned.safetensors"
    # Real file has NO __metadata__ at all -- verified against pruned_fp8_header.json,
    # this is exactly PORT_PLAN.md risk #2 (detect_quant_format must fire from the
    # per-layer comfy_quant/weight_scale markers alone, with zero global-metadata help).
    save_file(_pruned_sd(), str(path))
    return path


@pytest.fixture(scope="module")
def full_dit_path(tmp_path_factory):
    path = tmp_path_factory.mktemp("minimax_h3") / "minimax_h3_full.safetensors"
    save_file(_full_sd(), str(path), metadata={
        "config": json.dumps({"transformer": {"image_model": "minimax_h3"}}),
    })
    return path


def test_pruned_checkpoint_loads_through_the_real_public_path(pruned_dit_path):
    loader = NativeEngineLoader(device="cpu")
    model = loader.load(pruned_dit_path, "diffusion_model")

    assert isinstance(model, NativeModel)
    assert model.kind == "diffusion_model"
    assert model.spec.family == "minimax_h3"
    assert model.spec.variant == "h3"
    assert model.quant_format == "fp8_scaled"

    module = model.module
    assert isinstance(module, MiniMaxH3Model)
    assert module.config.pruned is True
    assert torch.isfinite(module.rope.inv_freq).all()
    assert module.rope.inv_freq.dtype == torch.float32
    assert module.rope.inv_freq.shape == (ROPE_FREQ_DIM,)

    # The quantised block Linears actually built as Fp8ScaledLinear (weight_scale
    # populated from the real sidecar, not left None as if unquantised).
    assert module.blocks[0].attn.qkv_proj.weight_scale is not None
    assert module.blocks[0].attn.qkv_proj.weight.dtype == torch.float8_e4m3fn
    # fc2 has no input_scale in the real file -- must stay None, not silently
    # defaulted to something that would misrepresent the checkpoint.
    assert module.blocks[0].mlp.fc2.input_scale is None
    assert module.blocks[0].mlp.fc1.input_scale is not None

    # token_refiner is bf16 in the REAL pruned file (no quant sidecars there) --
    # must load as a plain (non-quantised) layer despite living inside a module
    # built entirely under the fp8 ops namespace (Embedding-analog trap class).
    assert module.token_refiner.blocks[0].attn.qkv_proj.weight_scale is None
    assert module.token_refiner.blocks[0].attn.qkv_proj.weight.dtype == torch.bfloat16


def test_full_checkpoint_loads_through_the_real_public_path(full_dit_path):
    loader = NativeEngineLoader(device="cpu")
    model = loader.load(full_dit_path, "diffusion_model")

    assert isinstance(model, NativeModel)
    assert model.spec.family == "minimax_h3"
    assert model.spec.variant == "h3"
    assert model.quant_format is None  # unquantised bf16+f32 mixed checkpoint

    module = model.module
    assert isinstance(module, MiniMaxH3Model)
    assert module.config.pruned is False
    assert torch.isfinite(module.rope.inv_freq).all()
    assert module.rope.inv_freq.dtype == torch.float32
    assert not hasattr(module, "adaln_t_table")
    assert module.time_embedder.proj_in.weight.dtype == torch.float32
