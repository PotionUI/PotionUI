from __future__ import annotations

import pytest
import torch

from src.platform.runtime.native.text_encoders.qwen3 import Qwen3VLTextEncoder
from src.platform.runtime.native.text_encoders.qwen3_vl_vision import preprocess_qwen3_vl_image
from src.platform.runtime.native.text_encoders.tokenization import (
    KREA2VL_DEFAULT_SYSTEM_PROMPT,
    QWEN3VL_VISION_MARKUP,
    Qwen3VLTokenizer,
    krea2vl_picture_prefix,
)

from .test_qwen3vl import _tiny_vl_module


def test_picture_prefix_numbers_each_image_without_separators():
    opening, prefix = krea2vl_picture_prefix("sys", 2)
    assert opening == "<|im_start|>system\nsys<|im_end|>\n<|im_start|>user\n"
    assert prefix == opening + f"Picture 1: {QWEN3VL_VISION_MARKUP}Picture 2: {QWEN3VL_VISION_MARKUP}"


def test_picture_labelled_tokens_strip_only_the_system_and_user_opening():
    pytest.importorskip("transformers")
    tok = Qwen3VLTokenizer()
    ids, _mask, prefix_len = tok.tokenize_with_images(
        "make it real", num_images=2, device="cpu", picture_labels=True,
    )
    text = tok._tok.decode(ids[0].tolist())
    assert f"Picture 1: {QWEN3VL_VISION_MARKUP}Picture 2: {QWEN3VL_VISION_MARKUP}make it real" in text
    opening, _ = krea2vl_picture_prefix(KREA2VL_DEFAULT_SYSTEM_PROMPT, 2)
    assert prefix_len == len(tok._tok(opening)["input_ids"])
    assert prefix_len == tok._prefix_len


def test_unlabelled_tokens_are_unchanged_by_the_new_option():
    pytest.importorskip("transformers")
    tok = Qwen3VLTokenizer()
    plain = tok.tokenize_with_images("x", num_images=1, device="cpu")
    explicit = tok.tokenize_with_images("x", num_images=1, device="cpu", picture_labels=False)
    assert torch.equal(plain[0], explicit[0]) and plain[2] == explicit[2]


def test_grounding_area_caps_the_vision_grid_by_area_not_longest_side():
    image = torch.rand(256, 512, 3)
    _, uncapped = preprocess_qwen3_vl_image(image, grounding_px=0)
    _, capped = preprocess_qwen3_vl_image(image, grounding_px=0, grounding_area=128 * 128)
    assert int(capped[0, 1] * capped[0, 2]) < int(uncapped[0, 1] * uncapped[0, 2])
    assert int(capped[0, 1] * capped[0, 2]) * 16 * 16 * 4 <= 2 * 128 * 128 * 4


def test_grounding_area_zero_leaves_the_grid_alone():
    image = torch.rand(256, 512, 3)
    _, a = preprocess_qwen3_vl_image(image, grounding_px=0)
    _, b = preprocess_qwen3_vl_image(image, grounding_px=0, grounding_area=0)
    assert torch.equal(a, b)


def test_picture_labelled_encode_runs_end_to_end_with_two_images():
    pytest.importorskip("transformers")
    m = _tiny_vl_module()
    tok = Qwen3VLTokenizer()
    enc = Qwen3VLTextEncoder(m, tok, device="cpu")
    images = [torch.rand(32, 32, 3), torch.rand(32, 32, 3)]

    ids, _mask, prefix_len = tok.tokenize_with_images("make it real", num_images=2, device="cpu", picture_labels=True)
    visual = m.model.visual
    merged = 0
    for image in images:
        _p, grid = preprocess_qwen3_vl_image(
            image, patch_size=visual.patch_size, temporal_patch_size=visual.patch_embed.temporal_patch_size,
            merge_size=visual.spatial_merge_size,
        )
        merged += int((grid[0, 1] * grid[0, 2]) // (visual.spatial_merge_size ** 2))
    expected = ids.shape[1] - 2 + merged - prefix_len

    out = enc.encode(["make it real"], images=images, picture_labels=True)
    assert out["context"].shape[1] == expected
