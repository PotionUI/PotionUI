"""Unit tests for the AnimaClipTextEncoder ABC adapter.

Uses a fake NativeTextEncoder so no model weights are loaded -- the adapter's
job is purely to run the native encoder (through the shared GPU-residency
path) and pack its role-keyed dicts into a ConditioningModel. Anima uses TRUE
CFG, so the negative pass is always encoded when CFG is requested (mirrors
QwenClipTextEncoder / ZImageClipTextEncoder).

This adapter used to call ``self.encoder.encode_weighted(...)``
directly, so the Qwen3-0.6B forward always ran on the CPU in fp32 -- it never
joined the GPU residency path the other families use. These tests guard the
fix: the encode must route through ``run_text_encode_batch`` (moving the
encoder to ``self.device`` for the duration) and its call must carry a cache
key built from the encoder's identity.

The adapter's own ``encode_prompt``/``encode_prompts`` now live on the
shared ``SequentialWindowClipTextEncoder`` base
(``src.pipelines.pipes._shared.generation.clip_batch``), which calls
``run_text_encode_batch`` directly -- there is no more module-level
``run_text_encode`` name on ``anima_clip`` to monkeypatch, so the
regression guards below spy on ``clip_batch.run_text_encode_batch`` instead.
"""

from __future__ import annotations

import torch

from src.platform.runtime.primitives.clip import ClipTextEncoder, ConditioningModel
from src.platform.runtime.native.text_encoders import NativeTextEncoder
from src.platform.runtime.native.text_encoders.embed_cache import get_prompt_embed_cache
from src.pipelines.pipes._shared.generation import clip_batch as clip_batch_module
from src.pipelines.pipes.model_loader.anima.anima_clip import AnimaClipTextEncoder


class _FakeEncoder(NativeTextEncoder):
    """Records the texts it was asked to encode; returns canned role tensors.

    Mirrors the Anima output contract: a FOUR-key dict (``context``,
    ``attention_mask``, ``t5xxl_ids``, ``t5xxl_weights``) -- no pooled vector.
    """

    role = "qwen3_06b"

    def __init__(self):
        self.calls: list[str] = []

    def encode(self, texts):
        # encode_weighted's no-weight-syntax fast path calls encode([prompt]).
        prompt = texts[0]
        self.calls.append(prompt)
        seq = max(1, len(prompt))
        return {
            "context": torch.ones(1, seq, 4),
            "attention_mask": torch.ones(1, seq),
            "t5xxl_ids": torch.ones(1, seq, dtype=torch.long),
            "t5xxl_weights": torch.ones(1, seq),
        }


def _adapter(fingerprint=None, device="cpu"):
    # The prompt-embed cache is a process-global singleton -- clear it so one
    # test's cached entry can't leak into the next.
    get_prompt_embed_cache().clear()
    fake = _FakeEncoder()
    enc = AnimaClipTextEncoder(fake, device=device, model_fingerprint=fingerprint)
    return enc, fake


def test_is_a_clip_text_encoder():
    enc, _ = _adapter()
    assert isinstance(enc, ClipTextEncoder)


def test_packs_roles_into_conditioning_model():
    enc, fake = _adapter()
    cond = enc.encode_prompt("a cat", "blurry")
    assert isinstance(cond, ConditioningModel)
    assert cond.p_prompt == "a cat"
    assert cond.n_prompt == "blurry"
    assert set(cond.embeds) == {"context", "attention_mask", "t5xxl_ids", "t5xxl_weights"}
    assert set(cond.n_embeds) == {"context", "attention_mask", "t5xxl_ids", "t5xxl_weights"}
    # positive then negative encode (true CFG)
    assert fake.calls == ["a cat", "blurry"]


def test_no_pooled_vector():
    enc, _ = _adapter()
    cond = enc.encode_prompt("prompt", "neg")
    assert "pooled" not in cond.embeds


def test_empty_negative_still_encoded_for_true_cfg():
    # Anima's true-CFG sampler needs the uncond pass even when the negative
    # text is empty (unlike the embedded-guidance Flux adapter).
    enc, fake = _adapter()
    cond = enc.encode_prompt("only positive", "")
    assert set(cond.n_embeds) == {"context", "attention_mask", "t5xxl_ids", "t5xxl_weights"}
    assert fake.calls == ["only positive", ""]


def test_no_cfg_skips_negative_pass():
    enc, fake = _adapter()
    cond = enc.encode_prompt("p", "some negative", do_classifier_free_guidance=False)
    assert cond.n_embeds == {}
    assert fake.calls == ["p"]


def test_model_fingerprint_surfaced_for_prompt_encoder_cache():
    enc, _ = _adapter(fingerprint="anima|te|dit|lora=none")
    assert enc._model_fingerprint == "anima|te|dit|lora=none"


def test_embedding_files_ignored_not_fatal():
    enc, _ = _adapter()
    cond = enc.encode_prompt("p", "", embedding_files={"tok": "/path/to.pt"})
    assert isinstance(cond, ConditioningModel)  # ignored, no crash


# --- routes through run_text_encode_batch (GPU residency + --
# --- prompt cache, one shared window per encode_prompts call) --------------


def test_routes_through_run_text_encode(monkeypatch):
    """Regression guard: the encode call must go through
    ``run_text_encode_batch`` (the shared residency wrapper), not straight to
    ``self.encoder.encode_weighted`` -- otherwise the Qwen3-0.6B forward
    never leaves the CPU."""
    enc, fake = _adapter(fingerprint="anima|te|dit")

    calls = []
    real_run_text_encode_batch = clip_batch_module.run_text_encode_batch

    def spy(encoder, device, encode_fns, *, reserve_gb=None, cache_keys=None):
        calls.append({"encoder": encoder, "device": device, "cache_keys": cache_keys})
        return real_run_text_encode_batch(encoder, device, encode_fns, reserve_gb=reserve_gb, cache_keys=cache_keys)

    monkeypatch.setattr(clip_batch_module, "run_text_encode_batch", spy)

    enc.encode_prompt("a cat", "blurry")

    assert len(calls) == 1
    assert calls[0]["encoder"] is fake
    assert calls[0]["device"] == "cpu"
    assert calls[0]["cache_keys"][0] is not None  # fingerprint was supplied


def test_no_fingerprint_means_no_cache_key(monkeypatch):
    """Without a model_fingerprint, prompt_embed_key returns None -- the
    encode must still route through run_text_encode_batch (device residency
    still applies), just uncached."""
    enc, fake = _adapter(fingerprint=None)

    calls = []
    real_run_text_encode_batch = clip_batch_module.run_text_encode_batch

    def spy(encoder, device, encode_fns, *, reserve_gb=None, cache_keys=None):
        calls.append(cache_keys)
        return real_run_text_encode_batch(encoder, device, encode_fns, reserve_gb=reserve_gb, cache_keys=cache_keys)

    monkeypatch.setattr(clip_batch_module, "run_text_encode_batch", spy)

    enc.encode_prompt("a cat", "blurry")
    assert calls == [[None]]


def test_repeated_identical_prompt_hits_the_embed_cache():
    """Second identical encode_prompt call must NOT re-invoke the encoder --
    it should be served from the process-global PromptEmbedCache."""
    enc, fake = _adapter(fingerprint="anima|te|dit")

    enc.encode_prompt("a cat", "blurry")
    assert len(fake.calls) == 2  # positive + negative, first (real) pass

    enc.encode_prompt("a cat", "blurry")
    assert len(fake.calls) == 2  # unchanged -- second call was a cache hit


def test_different_prompt_does_not_hit_the_cache():
    enc, fake = _adapter(fingerprint="anima|te|dit")

    enc.encode_prompt("a cat", "blurry")
    enc.encode_prompt("a dog", "blurry")

    assert len(fake.calls) == 4  # both prompts actually encoded
