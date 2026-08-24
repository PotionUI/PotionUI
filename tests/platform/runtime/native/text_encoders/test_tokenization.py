"""Offline tokenizer correctness against golden token ids.

Golden ids were verified once against the bundled assets (the exact files ComfyUI
ships) and are hard-coded so a tokenizer regression is caught without a network.
The tokenizers load fully offline (local_files_only=True).
"""

from __future__ import annotations

import pytest

torch = pytest.importorskip("torch")
pytest.importorskip("transformers")

from src.platform.runtime.native.text_encoders.tokenization import (  # noqa: E402
    CLIP_PAD,
    CLIPLTokenizerWrap,
    QWEN3_MIN_LEN,
    QWEN3_PAD,
    Qwen3Tokenizer,
    T5_MIN_LEN,
    T5_PAD,
    T5XXLTokenizerWrap,
)

# Verified against the bundled assets (see task report). The Qwen row is the chat
# template "<|im_start|>user\na cat<|im_end|>\n<|im_start|>assistant\n<think>..." .
QWEN3_A_CAT = [151644, 872, 198, 64, 8251, 151645, 198, 151644, 77091, 198, 151667, 271, 151668, 271]
T5_A_CAT = [3, 9, 1712, 1]
CLIP_A_CAT = [49406, 320, 2368, 49407]


def test_qwen3_golden_ids_and_padding():
    tok = Qwen3Tokenizer()
    ids, mask = tok(["a cat"])
    assert ids.shape == (1, QWEN3_MIN_LEN)  # padded up to min length 512
    assert ids[0, : len(QWEN3_A_CAT)].tolist() == QWEN3_A_CAT
    assert (ids[0, len(QWEN3_A_CAT):] == QWEN3_PAD).all()
    assert int(mask[0].sum()) == len(QWEN3_A_CAT)


def test_qwen3_batch_pads_to_longest():
    tok = Qwen3Tokenizer()
    ids, mask = tok(["a cat", "a cat on a warm sunny beach with palm trees"])
    assert ids.shape[0] == 2
    assert ids.shape[1] >= QWEN3_MIN_LEN
    # Different real-token counts per row.
    assert int(mask[0].sum()) < int(mask[1].sum())


def test_t5_golden_ids_and_padding():
    tok = T5XXLTokenizerWrap()
    ids, mask = tok(["a cat"])
    assert ids.shape == (1, T5_MIN_LEN)
    assert ids[0, : len(T5_A_CAT)].tolist() == T5_A_CAT  # trailing EOS(1) kept
    assert (ids[0, len(T5_A_CAT):] == T5_PAD).all()
    assert int(mask[0].sum()) == len(T5_A_CAT)


def test_clip_golden_ids_and_eos_index():
    tok = CLIPLTokenizerWrap()
    ids, mask, eos = tok(["a cat"])
    assert ids.shape == (1, 77)
    assert ids[0, : len(CLIP_A_CAT)].tolist() == CLIP_A_CAT
    assert (ids[0, len(CLIP_A_CAT):] == CLIP_PAD).all()
    assert int(eos[0]) == len(CLIP_A_CAT) - 1  # position of first EOS
    assert int(mask[0].sum()) == len(CLIP_A_CAT)
