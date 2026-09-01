"""Tests for the TRELLIS.2 DINOv3 image conditioner (``conditioner.py``).

Coverage:
  * config construction + token-count arithmetic
  * the ``layer.`` -> ``model.layer.`` checkpoint remap, and that it is what
    makes a load succeed
  * ``load_dino_v3`` refusing a file that leaves parameters unfilled
  * key+shape parity against the real depot checkpoint (skipped unless
    ``POTIONUI_MODEL_TESTS=1`` and the file is present on disk)
  * preprocessing: LANCZOS square resize, RGB, /255, ImageNet normalisation
  * a tiny-weights forward producing ``(B, (S/16)^2 + 1 + registers, hidden)``
"""

from __future__ import annotations

from pathlib import Path

import pytest
import torch
from PIL import Image

from src.platform.runtime.native.arch.trellis2.conditioner import (
    IMAGENET_MEAN,
    IMAGENET_STD,
    DinoV3ImageConditioner,
    build_dino_v3,
    load_dino_v3,
    remap_dino_checkpoint,
)
from src.platform.runtime.native.arch.trellis2.config import DINO_V3_VIT_L16, DinoV3Config

_REPO_ROOT = Path(__file__).resolve().parents[6]
# Comfy-Org ships this file under `clip_vision/`; this depot keeps every
# conditioning encoder in `text_encoders/` (see `filesystem/model_types.py`).
_DINO_PATH = _REPO_ROOT / "models" / "text_encoders" / "dino_v3_vit_l.safetensors"

TINY = DinoV3Config(
    hidden_size=64,
    num_hidden_layers=2,
    num_attention_heads=4,
    intermediate_size=128,
)


def _tiny_conditioner() -> DinoV3ImageConditioner:
    torch.manual_seed(0)
    return DinoV3ImageConditioner(build_dino_v3(TINY).eval(), TINY)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def test_production_config_is_vit_l16_with_four_register_tokens():
    assert DINO_V3_VIT_L16.hidden_size == 1024
    assert DINO_V3_VIT_L16.num_hidden_layers == 24
    assert DINO_V3_VIT_L16.num_attention_heads == 16
    assert DINO_V3_VIT_L16.intermediate_size == 4096
    assert DINO_V3_VIT_L16.patch_size == 16
    assert DINO_V3_VIT_L16.num_register_tokens == 4


@pytest.mark.parametrize("size,expected", [(512, 1024 + 5), (1024, 4096 + 5)])
def test_num_tokens_counts_patches_plus_cls_plus_registers(size, expected):
    assert DINO_V3_VIT_L16.num_tokens(size) == expected


def test_num_tokens_rejects_a_size_that_is_not_a_whole_number_of_patches():
    with pytest.raises(ValueError, match="multiple of patch_size"):
        DINO_V3_VIT_L16.num_tokens(500)


def test_config_rejects_a_head_count_that_does_not_divide_the_width():
    with pytest.raises(ValueError, match="not divisible"):
        DinoV3Config(hidden_size=1024, num_attention_heads=7)


def test_build_dino_v3_produces_the_configured_shapes():
    state = build_dino_v3(TINY).state_dict()
    assert state["embeddings.cls_token"].shape == (1, 1, TINY.hidden_size)
    assert state["embeddings.register_tokens"].shape == (
        1,
        TINY.num_register_tokens,
        TINY.hidden_size,
    )
    assert state["embeddings.patch_embeddings.weight"].shape == (
        TINY.hidden_size,
        3,
        TINY.patch_size,
        TINY.patch_size,
    )
    assert state["model.layer.0.mlp.up_proj.weight"].shape == (
        TINY.intermediate_size,
        TINY.hidden_size,
    )
    assert sum(1 for k in state if k.startswith("model.layer.")) > 0
    assert not any(k.startswith("layer.") for k in state)


# ---------------------------------------------------------------------------
# Checkpoint remap
# ---------------------------------------------------------------------------


def test_remap_moves_block_keys_and_leaves_the_rest_alone():
    remapped = remap_dino_checkpoint({
        "layer.0.norm1.weight": 1,
        "layer.23.mlp.up_proj.bias": 2,
        "embeddings.cls_token": 3,
        "norm.weight": 4,
    })
    assert remapped == {
        "model.layer.0.norm1.weight": 1,
        "model.layer.23.mlp.up_proj.bias": 2,
        "embeddings.cls_token": 3,
        "norm.weight": 4,
    }


def test_remap_of_a_checkpoint_shaped_state_dict_fills_every_parameter(tmp_path):
    """The remap is load-bearing: the same file without it leaves every block
    unfilled, which is exactly what ``load_dino_v3`` must refuse."""
    from safetensors.torch import save_file

    reference = build_dino_v3(TINY).state_dict()
    checkpoint = {
        (k[len("model."):] if k.startswith("model.layer.") else k): v.clone()
        for k, v in reference.items()
    }
    assert any(k.startswith("layer.") for k in checkpoint)

    path = tmp_path / "dino.safetensors"
    save_file(checkpoint, str(path))

    loaded = load_dino_v3(path, TINY).state_dict()
    assert set(loaded) == set(reference)
    for key, value in remap_dino_checkpoint(checkpoint).items():
        assert torch.equal(loaded[key], value), key


def test_load_dino_v3_refuses_a_checkpoint_that_leaves_parameters_unfilled(tmp_path):
    from safetensors.torch import save_file

    state = build_dino_v3(TINY).state_dict()
    save_file({"embeddings.cls_token": state["embeddings.cls_token"].clone()}, str(tmp_path / "x.safetensors"))

    with pytest.raises(ValueError, match="not a DINOv3 ViT-L/16 image encoder"):
        load_dino_v3(tmp_path / "x.safetensors", TINY)


def test_without_the_remap_every_block_weight_is_left_unfilled():
    """What the remap buys: fed the checkpoint's own key space, the model fills
    only the seven non-block tensors and silently keeps random blocks."""
    model = build_dino_v3(TINY)
    checkpoint = {
        (k[len("model."):] if k.startswith("model.layer.") else k): v
        for k, v in model.state_dict().items()
    }

    unfilled = set(model.load_state_dict(checkpoint, strict=False).missing_keys)
    assert unfilled == {k for k in model.state_dict() if k.startswith("model.layer.")}
    assert unfilled


# ---------------------------------------------------------------------------
# Real-checkpoint parity
# ---------------------------------------------------------------------------


@pytest.mark.requires_models
@pytest.mark.skipif(not _DINO_PATH.exists(), reason="needs the real DINOv3 ViT-L/16 checkpoint on disk")
def test_production_config_matches_the_real_checkpoint():
    from safetensors import safe_open

    ours = {k: tuple(v.shape) for k, v in build_dino_v3().state_dict().items()}
    with safe_open(str(_DINO_PATH), framework="pt") as f:
        checkpoint = remap_dino_checkpoint({k: tuple(f.get_slice(k).get_shape()) for k in f.keys()})

    assert checkpoint == ours


# ---------------------------------------------------------------------------
# Preprocessing
# ---------------------------------------------------------------------------


def test_preprocess_squares_the_image_and_applies_imagenet_normalisation():
    image = Image.new("RGB", (37, 53), (255, 0, 0))
    out = DinoV3ImageConditioner.preprocess(image, 64)

    assert out.shape == (1, 3, 64, 64)
    expected = [(c - m) / s for c, m, s in zip((1.0, 0.0, 0.0), IMAGENET_MEAN, IMAGENET_STD)]
    for channel, value in enumerate(expected):
        assert out[0, channel].allclose(torch.full((64, 64), value), atol=1e-5)


def test_preprocess_converts_non_rgb_modes_and_batches_a_sequence():
    grey = Image.new("L", (32, 32), 255)
    out = DinoV3ImageConditioner.preprocess([grey, grey, grey], 32)
    assert out.shape == (3, 3, 32, 32)
    assert torch.equal(out[0], out[2])


# ---------------------------------------------------------------------------
# Forward
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("size", [64, 128])
def test_encode_emits_one_token_per_patch_plus_cls_and_registers(size):
    conditioner = _tiny_conditioner()
    out = conditioner.encode(Image.new("RGB", (size, size), (10, 200, 30)), size)
    assert out.shape == (1, TINY.num_tokens(size), TINY.hidden_size)


def test_encode_accepts_an_already_preprocessed_batch_and_keeps_its_batch_size():
    conditioner = _tiny_conditioner()
    pixels = DinoV3ImageConditioner.preprocess([Image.new("RGB", (64, 64))] * 2, 64)
    assert conditioner.encode(pixels).shape == (2, TINY.num_tokens(64), TINY.hidden_size)


def test_encode_rejects_an_unbatched_tensor():
    with pytest.raises(ValueError, match=r"batched \(B, C, H, W\)"):
        _tiny_conditioner().encode(torch.randn(3, 64, 64))


def test_encode_output_is_layer_normalised_over_the_feature_dim():
    out = _tiny_conditioner().encode(Image.new("RGB", (64, 64), (90, 40, 160)), 64)
    assert out.mean(dim=-1).abs().max() < 1e-5
    assert out.std(dim=-1, unbiased=False).allclose(torch.ones(out.shape[:-1]), atol=5e-3)


def test_encode_follows_the_conditioners_dtype_and_negative_matches_the_shape():
    conditioner = _tiny_conditioner().to(dtype=torch.float64)
    out = conditioner.encode(Image.new("RGB", (64, 64), (5, 5, 5)), 64)

    assert out.dtype == torch.float64
    negative = DinoV3ImageConditioner.negative(out)
    assert negative.shape == out.shape
    assert negative.dtype == out.dtype
    assert not negative.any()
