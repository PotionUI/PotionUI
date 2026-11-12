from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest
import torch

from src.platform.runtime.native.text_encoders.qwen3 import MiniMaxH3TextEncoder

from .test_minimax_h3 import _SMALL_PX, _spied_encoder


def test_image_edits_label_pictures_without_angle_brackets():
    pytest.importorskip("transformers")
    _module, tok, enc, seen = _spied_encoder()
    enc.encode_request("make it blue", images=[torch.rand(32, 32, 3)], picture_label="Picture {}: ")
    ids = seen["token_ids"]
    label = tok("Picture 1: ")
    assert ids[:len(label)] == label
    assert tok("<Picture 1>: ") != label
    assert ids[-len(tok("make it blue")):] == tok("make it blue")


@pytest.mark.parametrize("sizes", [(), ((32, 32),), ((32, 32), (64, 16), (40, 24))])
@pytest.mark.parametrize("words", [3, 40])
def test_the_token_count_is_the_length_of_the_presentation_the_encode_builds(sizes, words):
    pytest.importorskip("transformers")
    _module, tok, enc, seen = _spied_encoder()
    prompt = " ".join(["word"] * words)
    images = [torch.rand(height, width, 3) for width, height in sizes] or None

    enc.encode_request(prompt, images=images, picture_label="Picture {}: ", max_prompt_tokens=10)
    tokens, vision_tokens = enc.request_token_counts(
        prompt, list(sizes), picture_label="Picture {}: ", max_prompt_tokens=10, **_SMALL_PX,
    )

    assert tokens == len(seen["token_ids"])
    assert vision_tokens == seen["token_ids"].count(tok.convert_tokens_to_ids("<|image_pad|>"))


def _h3_32b_encoder() -> MiniMaxH3TextEncoder:
    cfg = SimpleNamespace(hidden_size=5120, intermediate_size=25600, num_attention_heads=64, head_dim=128)
    return MiniMaxH3TextEncoder(SimpleNamespace(cfg=cfg), None)


_ALLOCATED_ON_12_GB = [(2890, 2816, 1.585, 1.6924), (3594, 3520, 1.981, 2.106), (4330, 4256, 2.3998, 2.5603),
                       (10296, 10208, 5.942, 6.7604)]


def test_the_working_memory_of_an_encode_grows_with_its_tokens_faster_than_linearly():
    enc = _h3_32b_encoder()
    one, two = enc.encode_working_gb(4000, 3000), enc.encode_working_gb(8000, 6000)

    assert 2 * one < two < 2.2 * one


@pytest.mark.parametrize(("tokens", "vision_tokens", "allocated_gib", "reserved_gib"), _ALLOCATED_ON_12_GB)
def test_the_working_memory_covers_what_the_encode_allocated_on_a_12_gb_card(tokens, vision_tokens, allocated_gib,
                                                                          reserved_gib):
    estimate = _h3_32b_encoder().encode_working_gb(tokens, vision_tokens)

    assert allocated_gib <= estimate <= 1.05 * allocated_gib


@pytest.mark.parametrize(("tokens", "vision_tokens", "allocated_gib", "reserved_gib"), _ALLOCATED_ON_12_GB)
def test_the_placement_margin_covers_what_the_allocator_reserved_on_a_12_gb_card(tokens, vision_tokens,
                                                                              allocated_gib, reserved_gib):
    from src.platform.runtime.native.memory.residency import te_working_need_gb

    estimate = _h3_32b_encoder().encode_working_gb(tokens, vision_tokens)

    assert 1.05 * reserved_gib <= te_working_need_gb(estimate)


def test_the_attention_phase_is_priced_from_the_checkpoints_own_widths():
    narrow_mlp = MiniMaxH3TextEncoder(SimpleNamespace(cfg=SimpleNamespace(
        hidden_size=5120, intermediate_size=1024, num_attention_heads=64, head_dim=128)), None)
    wider_heads = MiniMaxH3TextEncoder(SimpleNamespace(cfg=SimpleNamespace(
        hidden_size=5120, intermediate_size=1024, num_attention_heads=96, head_dim=128)), None)

    assert wider_heads.encode_working_gb(4000) > narrow_mlp.encode_working_gb(4000)


def test_vision_tokens_cost_more_working_memory_than_text_tokens():
    enc = _h3_32b_encoder()

    assert enc.encode_working_gb(4000, 4000) > enc.encode_working_gb(4000, 0) > enc.encode_working_gb(3000, 0)


def test_the_video_label_stays_the_default():
    pytest.importorskip("transformers")
    _module, tok, enc, seen = _spied_encoder()
    enc.encode_request("make it blue", images=[torch.rand(32, 32, 3)])
    assert seen["token_ids"][:len(tok("<Picture 1>: "))] == tok("<Picture 1>: ")


@pytest.mark.parametrize("images", [None, "one"])
def test_a_long_prompt_keeps_only_its_first_tokens(images, caplog):
    pytest.importorskip("transformers")
    _module, tok, enc, seen = _spied_encoder()
    prompt = " ".join(["word"] * 40)
    full = tok(prompt)
    pictures = [torch.rand(32, 32, 3)] if images else None
    with caplog.at_level(logging.WARNING):
        out = enc.encode_request(prompt, images=pictures, picture_label="Picture {}: ", max_prompt_tokens=10)
    assert seen["token_ids"][-10:] == full[:10]
    assert out["token_tags"].shape[0] == len(seen["token_ids"])
    assert "keeping the first 10" in caplog.text
