"""Unit tests for Krea2ClipTextEncoder (ABC adapter over the Qwen3-VL TE).

Exercises the CPU path of the encode: ``run_text_encode`` runs ``encode_fn`` in
place on a CPU device (no move), so these assert the adapter's packing /
negative-pass logic without a GPU. The GPU path is covered by
``tests/core/native/memory/test_residency.py``.
"""

from __future__ import annotations

import torch

from src.platform.runtime.primitives.clip import ClipTextEncoder, ConditioningModel
from src.platform.runtime.native.text_encoders import NativeTextEncoder
from src.pipelines.pipes.model_loader.krea2.krea2_clip import Krea2ClipTextEncoder


class _FakeQwen3VL(NativeTextEncoder):
    role = "qwen3vl_4b"

    def __init__(self):
        self.calls = []
        self.devices = []
        self.image_calls: list[list] = []
        self.grounding_px_calls: list[int] = []
        self.system_prompt_calls: list = []

    def encode(self, texts, images=None, grounding_px=768, system_prompt=None):
        self.calls.append(list(texts))
        self.image_calls.append(list(images) if images else [])
        self.grounding_px_calls.append(grounding_px)
        self.system_prompt_calls.append(system_prompt)
        # Krea-2 keeps the 12-layer axis separate (position 2).
        return {"context": torch.ones(1, 4, 12, 8), "attention_mask": torch.ones(1, 4)}

    def to(self, device):
        self.devices.append(str(device))
        return self


def test_is_clip_text_encoder():
    assert isinstance(Krea2ClipTextEncoder(_FakeQwen3VL()), ClipTextEncoder)


def test_packs_layered_context_and_mask():
    fake = _FakeQwen3VL()
    cond = Krea2ClipTextEncoder(fake, device="cpu").encode_prompt("a fox", "")
    assert isinstance(cond, ConditioningModel)
    assert set(cond.embeds) == {"context", "attention_mask"}
    assert cond.embeds["context"].shape == (1, 4, 12, 8)  # layer axis preserved


def test_noCfg_skips_negative_by_default():
    # Krea-2 turbo is NoCFG: an empty negative -> no negative pass.
    fake = _FakeQwen3VL()
    cond = Krea2ClipTextEncoder(fake, device="cpu").encode_prompt(
        "p", "", do_classifier_free_guidance=False
    )
    assert cond.n_embeds == {}
    assert fake.calls == [["p"]]


def test_encodes_negative_only_when_cfg_and_real_negative():
    fake = _FakeQwen3VL()
    Krea2ClipTextEncoder(fake, device="cpu").encode_prompt(
        "p", "blurry", do_classifier_free_guidance=True
    )
    assert fake.calls == [["p"], ["blurry"]]


def test_cfg_with_empty_negative_still_encodes():
    # Krea-2's registry guidance is "cfg" (not embedded like Flux): an empty
    # negative is still a valid uncond target and must be encoded so CFG
    # actually contrasts against something, matching qwen/z_image/anima.
    fake = _FakeQwen3VL()
    cond = Krea2ClipTextEncoder(fake, device="cpu").encode_prompt(
        "p", "   ", do_classifier_free_guidance=True
    )
    assert fake.calls == [["p"], ["   "]]
    assert cond.n_embeds != {}


def test_cpu_device_does_not_move_encoder():
    # run_text_encode runs in place on CPU -> the encoder is never .to()'d.
    fake = _FakeQwen3VL()
    Krea2ClipTextEncoder(fake, device="cpu").encode_prompt("p", "")
    assert fake.devices == []


def test_fingerprint_surfaced():
    enc = Krea2ClipTextEncoder(_FakeQwen3VL(), model_fingerprint="krea2|te|dit")
    assert enc._model_fingerprint == "krea2|te|dit"


# --- image-conditioned encode (Krea-2 edit mode) --------------------


def test_no_images_never_calls_encode_with_an_images_kwarg():
    """Regression guard: the plain (no-image) path must stay on
    encode_weighted -> encode([prompt]) with no images, never the
    image-conditioned encode([prompt], images=...) branch."""
    fake = _FakeQwen3VL()
    Krea2ClipTextEncoder(fake, device="cpu").encode_prompt("a fox", "blurry", do_classifier_free_guidance=True)
    assert fake.image_calls == [[], []]


def test_images_condition_both_positive_and_negative_pass():
    fake = _FakeQwen3VL()
    enc = Krea2ClipTextEncoder(fake, device="cpu")
    img = torch.rand(4, 4, 3)
    cond = enc.encode_prompt("make it night", "blurry", images=[img], do_classifier_free_guidance=True)
    assert isinstance(cond, ConditioningModel)
    assert fake.calls == [["make it night"], ["blurry"]]
    assert fake.image_calls == [[img], [img]]


def test_images_encode_negative_pass_even_when_negative_empty_with_cfg():
    # With an image AND explicit CFG, an empty negative is still encoded --
    # same "cfg" guidance rule as the no-image path above.
    fake = _FakeQwen3VL()
    enc = Krea2ClipTextEncoder(fake, device="cpu")
    img = torch.rand(4, 4, 3)
    cond = enc.encode_prompt("make it night", "", images=[img], do_classifier_free_guidance=True)
    assert cond.n_embeds != {}
    assert fake.calls == [["make it night"], [""]]
    assert fake.image_calls == [[img], [img]]


def test_images_pass_grounding_px_and_system_prompt_through():
    fake = _FakeQwen3VL()
    enc = Krea2ClipTextEncoder(fake, device="cpu")
    img = torch.rand(4, 4, 3)
    enc.encode_prompt(
        "edit it", "", images=[img], do_classifier_free_guidance=False,
        grounding_px=384, system_prompt="a custom system prompt",
    )
    assert fake.grounding_px_calls == [384]
    assert fake.system_prompt_calls == ["a custom system prompt"]


def test_images_default_grounding_px_is_768():
    fake = _FakeQwen3VL()
    enc = Krea2ClipTextEncoder(fake, device="cpu")
    img = torch.rand(4, 4, 3)
    enc.encode_prompt("edit it", "", images=[img], do_classifier_free_guidance=False)
    assert fake.grounding_px_calls == [768]
    assert fake.system_prompt_calls == [None]


def _spy_on_prompt_embed_key(monkeypatch):
    import src.pipelines.pipes.model_loader.krea2.krea2_clip as krea2_clip_mod

    calls = []
    real_key = krea2_clip_mod.prompt_embed_key

    def spy(*args, **kwargs):
        key = real_key(*args, **kwargs)
        calls.append(key)
        return key

    monkeypatch.setattr(krea2_clip_mod, "prompt_embed_key", spy)
    return calls


def test_image_cache_key_differs_for_different_images(monkeypatch):
    """The hazard `image_content_fingerprint` exists to prevent: same prompt,
    different source images, must not collide in the cache key."""
    calls = _spy_on_prompt_embed_key(monkeypatch)
    fake = _FakeQwen3VL()
    enc = Krea2ClipTextEncoder(fake, device="cpu", model_fingerprint="fp")

    enc.encode_prompt("edit it", "", images=[torch.rand(4, 4, 3)], do_classifier_free_guidance=False)
    enc.encode_prompt("edit it", "", images=[torch.rand(4, 4, 3)], do_classifier_free_guidance=False)

    assert calls[0] != calls[1]


def test_image_cache_key_same_for_same_image_content(monkeypatch):
    calls = _spy_on_prompt_embed_key(monkeypatch)
    fake = _FakeQwen3VL()
    enc = Krea2ClipTextEncoder(fake, device="cpu", model_fingerprint="fp")
    same_img = torch.rand(4, 4, 3)

    enc.encode_prompt("edit it", "", images=[same_img], do_classifier_free_guidance=False)
    enc.encode_prompt("edit it", "", images=[same_img.clone()], do_classifier_free_guidance=False)

    assert calls[0] == calls[1]


def test_image_cache_key_differs_for_different_grounding_px(monkeypatch):
    """A different grounding_px cap changes the vision tower's output for the
    SAME image -- must not collide in the cache key either."""
    calls = _spy_on_prompt_embed_key(monkeypatch)
    fake = _FakeQwen3VL()
    enc = Krea2ClipTextEncoder(fake, device="cpu", model_fingerprint="fp")
    same_img = torch.rand(4, 4, 3)

    enc.encode_prompt("edit it", "", images=[same_img], do_classifier_free_guidance=False, grounding_px=768)
    enc.encode_prompt("edit it", "", images=[same_img], do_classifier_free_guidance=False, grounding_px=384)

    assert calls[0] != calls[1]


def test_image_cache_key_none_without_model_fingerprint():
    fake = _FakeQwen3VL()
    enc = Krea2ClipTextEncoder(fake, device="cpu")  # no model_fingerprint
    # Must not raise despite the missing fingerprint -- caching is simply
    # disabled (prompt_embed_key returns None), matching the no-image path.
    cond = enc.encode_prompt("edit it", "", images=[torch.rand(4, 4, 3)], do_classifier_free_guidance=False)
    assert isinstance(cond, ConditioningModel)
