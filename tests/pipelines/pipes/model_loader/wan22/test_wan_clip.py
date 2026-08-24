"""Unit tests for WanClipTextEncoder (ABC adapter over UMT5)."""

from __future__ import annotations

import torch

from src.platform.runtime.primitives.clip import ClipTextEncoder, ConditioningModel
from src.platform.runtime.native.text_encoders import NativeTextEncoder
from src.pipelines.pipes.model_loader.wan22.wan_clip import WanClipTextEncoder


class _FakeUMT5(NativeTextEncoder):
    role = "umt5_xxl"

    def __init__(self):
        self.calls = []

    def encode(self, texts):
        self.calls.append(list(texts))
        return {"context": torch.ones(1, 4, 8), "attention_mask": torch.ones(1, 4)}


def test_is_clip_text_encoder():
    assert isinstance(WanClipTextEncoder(_FakeUMT5()), ClipTextEncoder)


def test_packs_context_and_mask():
    fake = _FakeUMT5()
    cond = WanClipTextEncoder(fake, device="cpu").encode_prompt("a cat walking", "blurry")
    assert isinstance(cond, ConditioningModel)
    assert set(cond.embeds) == {"context", "attention_mask"}
    assert set(cond.n_embeds) == {"context", "attention_mask"}


def test_encodes_negative_for_true_cfg():
    # Unlike Flux (embedded guidance), Wan uses true CFG -> negative IS encoded.
    fake = _FakeUMT5()
    WanClipTextEncoder(fake, device="cpu").encode_prompt("p", "n", do_classifier_free_guidance=True)
    assert fake.calls == [["p"], ["n"]]


def test_no_cfg_skips_negative():
    fake = _FakeUMT5()
    cond = WanClipTextEncoder(fake, device="cpu").encode_prompt("p", "n", do_classifier_free_guidance=False)
    assert cond.n_embeds == {}
    assert fake.calls == [["p"]]


def test_fingerprint_surfaced():
    enc = WanClipTextEncoder(_FakeUMT5(), model_fingerprint="wan|te|high|low")
    assert enc._model_fingerprint == "wan|te|high|low"
