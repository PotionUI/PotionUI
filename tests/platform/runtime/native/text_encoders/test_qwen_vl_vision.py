"""Qwen2.5-VL vision-tower tests (CPU only, synthetic + real-header key parity).

Covers: image preprocessing geometry (resize/patch counts), the vision tower's
forward shape/finiteness on a tiny synthetic config, its state-dict key
structure against the REAL local checkpoint header (no tensor loads), and the
m-RoPE position-id construction against hand-computed small cases. No GPU, no
real-weight forward (that needs maintainer validation).
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

import pytest

torch = pytest.importorskip("torch")

from vendor.gpl.comfyui.ops import disable_weight_init as ops  # noqa: E402

from src.platform.runtime.native.text_encoders.qwen_vl_vision import (  # noqa: E402
    IMAGE_PAD_TOKEN,
    Qwen2VLVisionTower,
    preprocess_qwen_vl_image,
    qwen25vl_mrope_position_ids,
)

_REPO_ROOT = Path(__file__).resolve().parents[5]
_REAL = _REPO_ROOT / "models/clip/qwen_2.5_vl_7b_fp8_scaled.safetensors"


# --- preprocessing geometry -------------------------------------------------


def test_preprocess_exact_multiple_of_factor_is_unchanged():
    # factor = patch_size(14) * merge_size(2) = 28; 224 = 28*8 needs no resize.
    img = torch.rand(224, 224, 3)
    patches, grid_thw = preprocess_qwen_vl_image(img)
    assert grid_thw.tolist() == [[1, 16, 16]]  # 224 / patch_size(14) = 16
    assert patches.shape == (16 * 16, 3 * 2 * 14 * 14)
    assert torch.isfinite(patches).all()


def test_preprocess_rounds_to_the_nearest_factor_multiple():
    # 230 rounds to the nearest multiple of 28 -> round(230/28)*28 = 224.
    img = torch.rand(230, 230, 3)
    _patches, grid_thw = preprocess_qwen_vl_image(img)
    assert grid_thw[0, 1].item() == 224 // 14
    assert grid_thw[0, 2].item() == 224 // 14


def test_preprocess_upsizes_below_min_pixels():
    # A tiny image (below min_pixels) is upsized, never left smaller.
    img = torch.rand(8, 8, 3)
    _patches, grid_thw = preprocess_qwen_vl_image(img, min_pixels=3136, max_pixels=12845056)
    h, w = grid_thw[0, 1].item(), grid_thw[0, 2].item()
    assert h * w * 14 * 14 >= 3136


def test_preprocess_downsizes_above_max_pixels():
    img = torch.rand(4000, 4000, 3)
    max_pixels = 12845056
    _patches, grid_thw = preprocess_qwen_vl_image(img, min_pixels=3136, max_pixels=max_pixels)
    h_bar = grid_thw[0, 1].item() * 14   # patch units -> resized pixel dims
    w_bar = grid_thw[0, 2].item() * 14
    # A little slack: h_bar/w_bar are rounded to the nearest patch*merge factor
    # (28px), so the actual pixel count can exceed max_pixels by up to ~2 factors.
    assert h_bar * w_bar <= max_pixels * 1.05


def test_preprocess_rejects_wrong_shape():
    with pytest.raises(ValueError):
        preprocess_qwen_vl_image(torch.rand(4, 4))


# --- tiny synthetic tower: forward shape/finiteness -------------------------

_TINY = dict(
    hidden_size=8, output_hidden_size=12, intermediate_size=16, num_heads=2, num_layers=2,
    patch_size=2, temporal_patch_size=2, spatial_merge_size=2, window_size=8,
)


def _build_tiny_tower() -> Qwen2VLVisionTower:
    tower = Qwen2VLVisionTower(operations=ops, **_TINY)
    for p in tower.parameters():
        torch.nn.init.normal_(p, std=0.02)
    return tower


def test_tiny_tower_forward_shape_and_finite():
    tower = _build_tiny_tower()
    # 4x4 patch grid (post-merge 2x2 = 4 merged tokens), matching an image
    # pre-patchified by preprocess_qwen_vl_image with patch_size=2.
    grid_thw = torch.tensor([[1, 4, 4]])
    num_patches = 4 * 4
    patch_dim = 3 * _TINY["temporal_patch_size"] * _TINY["patch_size"] * _TINY["patch_size"]
    pixel_values = torch.randn(num_patches, patch_dim)

    out = tower(pixel_values, grid_thw)
    assert out.shape == (16 // 4, _TINY["output_hidden_size"])  # merge_unit=4
    assert torch.isfinite(out).all()


def test_tiny_tower_fullatt_block_indexes_are_every_quarter():
    tower = _build_tiny_tower()
    # num_layers=2 -> (2//4)*(i+1)-1 = -1 for every i (2//4==0 in integer division):
    # a degenerate tiny case, so check the general formula on a bigger tower instead.
    big = Qwen2VLVisionTower(operations=ops, hidden_size=8, output_hidden_size=8,
                              intermediate_size=8, num_heads=2, num_layers=32)
    assert big.fullatt_block_indexes == [7, 15, 23, 31]


# --- m-RoPE position ids: hand-computed small cases -------------------------


def test_mrope_no_images_is_plain_sequential_on_all_axes():
    pos = qwen25vl_mrope_position_ids([], seq_len=5, device="cpu")
    assert pos.shape == (3, 5)
    for axis in range(3):
        assert pos[axis].tolist() == [0, 1, 2, 3, 4]


def test_mrope_hand_computed_single_image_span():
    # 3 text tokens, then a 2x2 (post-merge) image (4 vision tokens), then 2 more
    # text tokens. grid_thw is in PATCH units (pre-merge): h=w=4 -> post-merge 2x2.
    grid = torch.tensor([[1, 4, 4]])
    spans = [(3, 4, grid)]
    pos = qwen25vl_mrope_position_ids(spans, seq_len=9, device="cpu")

    # Leading text: sequential 0,1,2 on all 3 axes.
    assert pos[:, :3].tolist() == [[0, 1, 2]] * 3

    # Image span [3:7): T constant at start(=3); H tiles rows (0,0,1,1)+3; W tiles
    # cols (0,1,0,1)+3 (post-merge grid = 4//2 = 2 in both height and width).
    assert pos[0, 3:7].tolist() == [3, 3, 3, 3]
    assert pos[1, 3:7].tolist() == [3, 3, 4, 4]
    assert pos[2, 3:7].tolist() == [3, 4, 3, 4]

    # Trailing text resumes at start + max(grid)//2 = 3 + 4//2 = 5, sequential.
    assert pos[:, 7:].tolist() == [[5, 6]] * 3


def test_mrope_offset_threads_across_two_images():
    grid = torch.tensor([[1, 2, 2]])  # post-merge 1x1 -> 1 vision token each
    # image A at [1,2), image B at [3,4): 1 text token, image A, 1 text token, image B.
    spans = [(1, 1, grid), (3, 1, grid)]
    pos = qwen25vl_mrope_position_ids(spans, seq_len=5, device="cpu")

    assert pos[:, 0].tolist() == [0, 0, 0]           # leading text
    assert pos[:, 1].tolist() == [1, 1, 1]           # image A (T=H=W=start=1)
    # after image A: start_next = 1 + 2//2 = 2; offset becomes (1 - 1) = 0
    # (len_max=1, span size=1, so offset contribution is 0 here).
    assert pos[:, 2].tolist() == [2, 2, 2]           # text between images
    assert pos[:, 3].tolist() == [3, 3, 3]           # image B (start=3, offset=0)
    assert pos[:, 4].tolist() == [4, 4, 4]           # trailing text


def test_mrope_rejects_non_positive_seq_len():
    with pytest.raises(ValueError):
        qwen25vl_mrope_position_ids([], seq_len=0, device="cpu")


def test_mrope_rejects_non_positive_span_size():
    with pytest.raises(ValueError):
        qwen25vl_mrope_position_ids([(0, 0, torch.tensor([[1, 2, 2]]))], seq_len=3, device="cpu")


def test_image_pad_token_matches_qwen2_vocab_constant():
    assert IMAGE_PAD_TOKEN == 151655


# --- real-header key parity (no tensor loads) -------------------------------


@pytest.mark.requires_models
@pytest.mark.skipif(not _REAL.is_file(), reason="real qwen2.5-vl checkpoint absent")
def test_real_header_vision_key_parity():
    with open(_REAL, "rb") as f:
        n = struct.unpack("<Q", f.read(8))[0]
        header = json.loads(f.read(n))

    visual_keys = {k for k in header if k.startswith("visual.")}
    quant_sidecar_suffixes = (".weight_scale", ".input_scale", ".scale_weight", ".scale_input", ".comfy_quant")
    real_keys = {k[len("visual."):] for k in visual_keys if not k.endswith(quant_sidecar_suffixes)}

    tower = Qwen2VLVisionTower(
        hidden_size=1280, output_hidden_size=3584, intermediate_size=3420,
        num_heads=16, num_layers=32, operations=ops,
    )
    module_keys = set(tower.state_dict())

    assert not (real_keys - module_keys), sorted(real_keys - module_keys)[:10]
    assert not (module_keys - real_keys), sorted(module_keys - real_keys)[:10]
