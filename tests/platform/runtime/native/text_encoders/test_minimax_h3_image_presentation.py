from __future__ import annotations

import logging

import pytest
import torch

from .test_minimax_h3 import _spied_encoder


def test_image_edits_label_pictures_without_angle_brackets():
    pytest.importorskip("transformers")
    _module, tok, enc, seen = _spied_encoder()
    enc.encode_request("make it blue", images=[torch.rand(32, 32, 3)], picture_label="Picture {}: ")
    ids = seen["token_ids"]
    label = tok("Picture 1: ")
    assert ids[:len(label)] == label
    assert tok("<Picture 1>: ") != label
    assert ids[-len(tok("make it blue")):] == tok("make it blue")


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
