"""Unit tests for the QwenClipTextEncoder ABC adapter.

Uses a fake NativeTextEncoder so no model weights are loaded — the adapter's job
is purely to run the native encoder and pack its role-keyed dicts into a
ConditioningModel, so a stub encoder fully exercises it. Qwen-Image uses TRUE
CFG, so the negative pass is always encoded when CFG is requested (unlike the
embedded-guidance Flux adapter, which skips empty negatives).
"""

from __future__ import annotations

import torch

from src.platform.runtime.primitives.clip import ClipTextEncoder, ConditioningModel
from src.platform.runtime.native.text_encoders import NativeTextEncoder
from src.pipelines.pipes.model_loader.qwen.qwen_clip import QwenClipTextEncoder


class _FakeEncoder(NativeTextEncoder):
    """Records the texts (and any ``images``) it was asked to encode; returns
    canned role tensors.

    Mirrors the Qwen2.5-VL output contract: ``{"context", "attention_mask"}``
    and NO pooled vector.
    """

    role = "fake"

    def __init__(self):
        self.calls: list[list[str]] = []
        self.image_calls: list[list] = []

    def encode(self, texts, images=None):
        self.calls.append(list(texts))
        self.image_calls.append(list(images) if images else [])
        seq = max(1, len(texts[0]))
        return {
            "context": torch.ones(1, seq, 4),
            "attention_mask": torch.ones(1, seq),
        }


def test_is_a_clip_text_encoder():
    enc = QwenClipTextEncoder(_FakeEncoder())
    assert isinstance(enc, ClipTextEncoder)


def test_packs_roles_into_conditioning_model():
    fake = _FakeEncoder()
    enc = QwenClipTextEncoder(fake, device="cpu")
    cond = enc.encode_prompt("a cat", "blurry")
    assert isinstance(cond, ConditioningModel)
    assert cond.p_prompt == "a cat"
    assert cond.n_prompt == "blurry"
    assert set(cond.embeds) == {"context", "attention_mask"}
    assert set(cond.n_embeds) == {"context", "attention_mask"}
    # positive then negative encode (true CFG)
    assert fake.calls == [["a cat"], ["blurry"]]


def test_no_pooled_vector():
    enc = QwenClipTextEncoder(_FakeEncoder(), device="cpu")
    cond = enc.encode_prompt("prompt", "neg")
    assert "pooled" not in cond.embeds


def test_forwards_full_image_batch():
    """Qwen-Image-Edit's reference set (1-3 images) is shared, unindexed,
    across every output of a batch -- prompt_encoder (main.py's `if
    getattr(clip, "forwards_full_image_batch", False)`) reads this to decide
    whether to forward the whole `images` list to every request instead of
    picking one image per batch index (same contract MiniMax-H3's fl2va
    keyframe pair relies on)."""
    enc = QwenClipTextEncoder(_FakeEncoder(), device="cpu")
    assert enc.forwards_full_image_batch is True


def test_empty_negative_still_encoded_for_true_cfg():
    # Unlike Flux, Qwen's true-CFG sampler needs the uncond pass even when the
    # negative text is empty.
    fake = _FakeEncoder()
    enc = QwenClipTextEncoder(fake, device="cpu")
    cond = enc.encode_prompt("only positive", "")
    assert set(cond.n_embeds) == {"context", "attention_mask"}
    assert fake.calls == [["only positive"], [""]]


def test_no_cfg_skips_negative_pass():
    fake = _FakeEncoder()
    enc = QwenClipTextEncoder(fake, device="cpu")
    cond = enc.encode_prompt("p", "some negative", do_classifier_free_guidance=False)
    assert cond.n_embeds == {}
    assert fake.calls == [["p"]]


def test_model_fingerprint_surfaced_for_prompt_encoder_cache():
    enc = QwenClipTextEncoder(_FakeEncoder(), model_fingerprint="qwen|te|dit|lora=none")
    assert enc._model_fingerprint == "qwen|te|dit|lora=none"


def test_embedding_files_ignored_not_fatal():
    fake = _FakeEncoder()
    enc = QwenClipTextEncoder(fake, device="cpu")
    cond = enc.encode_prompt("p", "", embedding_files={"tok": "/path/to.pt"})
    assert isinstance(cond, ConditioningModel)  # ignored, no crash


# --- image-conditioned encode (Qwen-Image-Edit) -----------


def test_no_images_never_calls_encode_with_an_images_kwarg():
    """Regression guard: the plain (no-image) path must stay on
    encode_weighted -> encode([prompt]), never encode([prompt], images=...)."""
    fake = _FakeEncoder()
    enc = QwenClipTextEncoder(fake, device="cpu")
    enc.encode_prompt("a cat", "blurry")
    assert fake.image_calls == [[], []]


def test_images_condition_both_positive_and_negative_pass():
    fake = _FakeEncoder()
    enc = QwenClipTextEncoder(fake, device="cpu")
    img = torch.rand(4, 4, 3)
    cond = enc.encode_prompt("edit it", "blurry", images=[img])
    assert isinstance(cond, ConditioningModel)
    assert fake.calls == [["edit it"], ["blurry"]]
    assert fake.image_calls == [[img], [img]]


def test_images_skips_the_negative_pass_when_cfg_off():
    fake = _FakeEncoder()
    enc = QwenClipTextEncoder(fake, device="cpu")
    img = torch.rand(4, 4, 3)
    cond = enc.encode_prompt("edit it", "neg", images=[img], do_classifier_free_guidance=False)
    assert cond.n_embeds == {}
    assert fake.calls == [["edit it"]]
    assert fake.image_calls == [[img]]


def test_images_converted_from_pil_to_tensor():
    from PIL import Image as PILImage

    fake = _FakeEncoder()
    enc = QwenClipTextEncoder(fake, device="cpu")
    pil_img = PILImage.new("RGB", (4, 4), color=(255, 0, 0))
    enc.encode_prompt("edit it", "", images=[pil_img], do_classifier_free_guidance=False)
    (seen,) = fake.image_calls[0]
    assert isinstance(seen, torch.Tensor)
    assert seen.shape == (4, 4, 3)
    assert seen.max().item() <= 1.0 and seen.min().item() >= 0.0


def _spy_on_prompt_embed_key(monkeypatch):
    import src.pipelines.pipes.model_loader.qwen.qwen_clip as qwen_clip_mod

    calls = []
    real_key = qwen_clip_mod.prompt_embed_key

    def spy(*args, **kwargs):
        key = real_key(*args, **kwargs)
        calls.append(key)
        return key

    monkeypatch.setattr(qwen_clip_mod, "prompt_embed_key", spy)
    return calls


def test_image_cache_key_differs_for_different_images(monkeypatch):
    """The hazard `image_content_fingerprint` exists to prevent: same prompt,
    different source images, must not collide in the cache key."""
    calls = _spy_on_prompt_embed_key(monkeypatch)
    fake = _FakeEncoder()
    enc = QwenClipTextEncoder(fake, device="cpu", model_fingerprint="fp")

    enc.encode_prompt("edit it", "", images=[torch.rand(4, 4, 3)], do_classifier_free_guidance=False)
    enc.encode_prompt("edit it", "", images=[torch.rand(4, 4, 3)], do_classifier_free_guidance=False)

    assert calls[0] != calls[1]


def test_image_cache_key_same_for_same_image_content(monkeypatch):
    calls = _spy_on_prompt_embed_key(monkeypatch)
    fake = _FakeEncoder()
    enc = QwenClipTextEncoder(fake, device="cpu", model_fingerprint="fp")
    same_img = torch.rand(4, 4, 3)

    enc.encode_prompt("edit it", "", images=[same_img], do_classifier_free_guidance=False)
    enc.encode_prompt("edit it", "", images=[same_img.clone()], do_classifier_free_guidance=False)

    assert calls[0] == calls[1]


# --- deferred TE acquisition (te_loader) ---------------------------------


def _fresh_embed_cache():
    from src.platform.runtime.native.text_encoders.embed_cache import get_prompt_embed_cache

    cache = get_prompt_embed_cache()
    cache.clear()
    return cache


class _CountingLoader:
    def __init__(self, encoder):
        self._encoder = encoder
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return self._encoder


def test_lazy_encoder_never_resolved_on_a_full_embed_cache_hit():
    _fresh_embed_cache()
    fake = _FakeEncoder()
    warm_loader = _CountingLoader(fake)
    QwenClipTextEncoder(device="cpu", model_fingerprint="fp-hit", te_loader=warm_loader).encode_prompt(
        "a fox", "", do_classifier_free_guidance=False
    )
    assert warm_loader.calls == 1  # cold: the first encode still needs the TE

    cold_loader = _CountingLoader(fake)
    cond = QwenClipTextEncoder(device="cpu", model_fingerprint="fp-hit", te_loader=cold_loader).encode_prompt(
        "a fox", "", do_classifier_free_guidance=False
    )
    assert cold_loader.calls == 0
    assert isinstance(cond, ConditioningModel)


def test_lazy_encoder_resolved_once_for_a_batch_of_misses():
    _fresh_embed_cache()
    fake = _FakeEncoder()
    loader = _CountingLoader(fake)
    enc = QwenClipTextEncoder(device="cpu", model_fingerprint="fp-batch", te_loader=loader)

    enc.encode_prompts([
        {"prompt": "a", "negative_prompt": "", "do_classifier_free_guidance": False},
        {"prompt": "b", "negative_prompt": "", "do_classifier_free_guidance": False},
        {"prompt": "c", "negative_prompt": "", "do_classifier_free_guidance": False},
    ])
    assert loader.calls == 1


def test_lazy_encoder_property_caches_after_first_resolve():
    fake = _FakeEncoder()
    loader = _CountingLoader(fake)
    enc = QwenClipTextEncoder(device="cpu", te_loader=loader)
    assert enc.encoder is fake
    assert enc.encoder is fake
    assert loader.calls == 1


def test_eager_encoder_bypasses_te_loader_entirely():
    fake = _FakeEncoder()

    def _must_not_run():
        raise AssertionError("te_loader must not run when encoder is already resolved")

    enc = QwenClipTextEncoder(fake, device="cpu", te_loader=_must_not_run)
    assert enc.encoder is fake
