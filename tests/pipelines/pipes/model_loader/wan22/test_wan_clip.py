"""Unit tests for WanClipTextEncoder (ABC adapter over UMT5)."""

from __future__ import annotations

import torch

from src.platform.runtime.primitives.clip import ClipTextEncoder, ConditioningModel
from src.platform.runtime.native.text_encoders import NativeTextEncoder
from src.platform.runtime.native.text_encoders.embed_cache import get_prompt_embed_cache
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


# --- deferred TE acquisition (te_loader) ---------------------------------


class _CountingLoader:
    def __init__(self, encoder):
        self._encoder = encoder
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return self._encoder


def test_lazy_encoder_never_resolved_on_a_full_embed_cache_hit():
    get_prompt_embed_cache().clear()
    fake = _FakeUMT5()
    warm_loader = _CountingLoader(fake)
    WanClipTextEncoder(device="cpu", model_fingerprint="fp-hit", te_loader=warm_loader).encode_prompt(
        "a fox", "", do_classifier_free_guidance=False
    )
    assert warm_loader.calls == 1

    cold_loader = _CountingLoader(fake)
    cond = WanClipTextEncoder(device="cpu", model_fingerprint="fp-hit", te_loader=cold_loader).encode_prompt(
        "a fox", "", do_classifier_free_guidance=False
    )
    assert cold_loader.calls == 0
    assert isinstance(cond, ConditioningModel)


def test_lazy_encoder_resolved_once_for_a_batch_of_misses():
    get_prompt_embed_cache().clear()
    fake = _FakeUMT5()
    loader = _CountingLoader(fake)
    enc = WanClipTextEncoder(device="cpu", model_fingerprint="fp-batch", te_loader=loader)

    enc.encode_prompts([
        {"prompt": "a", "negative_prompt": "", "do_classifier_free_guidance": False},
        {"prompt": "b", "negative_prompt": "", "do_classifier_free_guidance": False},
        {"prompt": "c", "negative_prompt": "", "do_classifier_free_guidance": False},
    ])
    assert loader.calls == 1


def test_lazy_encoder_property_caches_after_first_resolve():
    fake = _FakeUMT5()
    loader = _CountingLoader(fake)
    enc = WanClipTextEncoder(device="cpu", te_loader=loader)
    assert enc.encoder is fake
    assert enc.encoder is fake
    assert loader.calls == 1


def test_eager_encoder_bypasses_te_loader_entirely():
    fake = _FakeUMT5()

    def _must_not_run():
        raise AssertionError("te_loader must not run when encoder is already resolved")

    enc = WanClipTextEncoder(fake, device="cpu", te_loader=_must_not_run)
    assert enc.encoder is fake
