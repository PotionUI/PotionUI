"""Unit tests for the FluxClipTextEncoder ABC adapter.

Uses a fake NativeTextEncoder so no model weights are loaded — the adapter's job
is purely to run the native encoder and pack its role-keyed dicts into a
ConditioningModel, so a stub encoder fully exercises it.
"""

from __future__ import annotations

import torch

from src.platform.runtime.primitives.clip import ClipTextEncoder, ConditioningModel
from src.platform.runtime.native.text_encoders import NativeTextEncoder
from src.pipelines.pipes.model_loader.flux.flux_clip import FluxClipTextEncoder


class _FakeEncoder(NativeTextEncoder):
    """Records the texts it was asked to encode; returns canned role tensors."""

    role = "fake"

    def __init__(self, roles):
        self._roles = roles
        self.calls: list[list[str]] = []

    def encode(self, texts):
        self.calls.append(list(texts))
        # Shape the "context" by prompt length so different prompts are distinguishable.
        seq = max(1, len(texts[0]))
        out = {"context": torch.ones(1, seq, 4)}
        if "pooled" in self._roles:
            out["pooled"] = torch.ones(1, 8)
        return out


def test_is_a_clip_text_encoder():
    enc = FluxClipTextEncoder(_FakeEncoder({"context", "pooled"}))
    assert isinstance(enc, ClipTextEncoder)


def test_packs_roles_into_conditioning_model():
    fake = _FakeEncoder({"context", "pooled"})
    enc = FluxClipTextEncoder(fake, device="cpu")
    cond = enc.encode_prompt("a cat", "blurry")
    assert isinstance(cond, ConditioningModel)
    assert cond.p_prompt == "a cat"
    assert cond.n_prompt == "blurry"
    assert set(cond.embeds) == {"context", "pooled"}
    assert set(cond.n_embeds) == {"context", "pooled"}
    # positive then negative encode
    assert fake.calls == [["a cat"], ["blurry"]]


def test_klein_style_encoder_has_no_pooled():
    # Qwen3/Klein encoders emit only "context" (Flux2 DiT has no vector input).
    enc = FluxClipTextEncoder(_FakeEncoder({"context"}), device="cpu")
    cond = enc.encode_prompt("prompt", "neg")
    assert set(cond.embeds) == {"context"}
    assert "pooled" not in cond.embeds


def test_empty_negative_skips_negative_pass():
    fake = _FakeEncoder({"context"})
    enc = FluxClipTextEncoder(fake, device="cpu")
    cond = enc.encode_prompt("only positive", "")
    assert cond.n_embeds == {}
    assert fake.calls == [["only positive"]]  # negative not encoded


def test_no_cfg_skips_negative_pass():
    fake = _FakeEncoder({"context"})
    enc = FluxClipTextEncoder(fake, device="cpu")
    cond = enc.encode_prompt("p", "some negative", do_classifier_free_guidance=False)
    assert cond.n_embeds == {}
    assert fake.calls == [["p"]]


def test_model_fingerprint_surfaced_for_prompt_encoder_cache():
    enc = FluxClipTextEncoder(_FakeEncoder({"context"}), model_fingerprint="flux|abc|lora=none")
    assert enc._model_fingerprint == "flux|abc|lora=none"


def test_embedding_files_ignored_not_fatal():
    fake = _FakeEncoder({"context"})
    enc = FluxClipTextEncoder(fake, device="cpu")
    cond = enc.encode_prompt("p", "", embedding_files={"tok": "/path/to.pt"})
    assert isinstance(cond, ConditioningModel)  # ignored, no crash
