"""Unit tests for the QwenClipTextEncoder ABC adapter.

Uses a fake NativeTextEncoder so no model weights are loaded — the adapter's job
is purely to run the native encoder and pack its role-keyed dicts into a
ConditioningModel, so a stub encoder fully exercises it. Qwen-Image uses TRUE
CFG, so the negative pass is always encoded when CFG is requested (unlike the
embedded-guidance Flux adapter, which skips empty negatives).
"""

from __future__ import annotations

import torch

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


def test_forwards_full_image_batch():
    """Qwen-Image-Edit's reference set (1-3 images) is shared, unindexed,
    across every output of a batch -- prompt_encoder (main.py's `if
    getattr(clip, "forwards_full_image_batch", False)`) reads this to decide
    whether to forward the whole `images` list to every request instead of
    picking one image per batch index (same contract MiniMax-H3's fl2va
    keyframe pair relies on)."""
    enc = QwenClipTextEncoder(_FakeEncoder(), device="cpu")
    assert enc.forwards_full_image_batch is True


# --- image-conditioned encode (Qwen-Image-Edit) -----------


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


class _OptionRecordingEncoder(_FakeEncoder):
    def __init__(self):
        super().__init__()
        self.options: list[dict] = []

    def encode(self, texts, images=None, **options):
        self.options.append(options)
        return super().encode(texts, images=images)


def test_edit_plus_layout_reaches_the_positive_and_negative_pass():
    fake = _OptionRecordingEncoder()
    enc = QwenClipTextEncoder(fake, device="cpu")
    enc.encode_prompt("turn it", "", images=[torch.rand(4, 4, 3)], picture_labels=True, grounding_area=147456)
    assert fake.options == [{"picture_labels": True, "grounding_area": 147456}] * 2


def test_without_the_edit_plus_options_the_encoder_gets_none():
    fake = _OptionRecordingEncoder()
    enc = QwenClipTextEncoder(fake, device="cpu")
    enc.encode_prompt("turn it", "", images=[torch.rand(4, 4, 3)], picture_labels=False, grounding_area=0)
    assert fake.options == [{}, {}]


def test_the_edit_plus_options_are_part_of_the_cache_key():
    enc = QwenClipTextEncoder(_FakeEncoder(), device="cpu", model_fingerprint="qwen-te")
    request = {"prompt": "turn it", "negative_prompt": "", "images": [torch.rand(4, 4, 3)]}
    keys = {
        enc._encode_fn_and_key(dict(request, **extra))[1]
        for extra in ({}, {"picture_labels": True}, {"grounding_area": 147456})
    }
    assert len(keys) == 3


PLUS = {"picture_labels": True, "grounding_area": 147456}


def test_the_model_layout_applies_when_the_mode_sets_nothing():
    fake = _OptionRecordingEncoder()
    enc = QwenClipTextEncoder(fake, device="cpu", vision_layout=PLUS)
    enc.encode_prompt("turn it", "", images=[torch.rand(4, 4, 3)])
    assert fake.options == [PLUS, PLUS]


def test_a_mode_can_switch_the_model_layout_off():
    fake = _OptionRecordingEncoder()
    enc = QwenClipTextEncoder(fake, device="cpu", vision_layout=PLUS)
    enc.encode_prompt("turn it", "", images=[torch.rand(4, 4, 3)], picture_labels=False, grounding_area=0)
    assert fake.options == [{}, {}]


def test_a_mode_can_switch_one_part_of_the_model_layout_off():
    fake = _OptionRecordingEncoder()
    enc = QwenClipTextEncoder(fake, device="cpu", vision_layout=PLUS)
    enc.encode_prompt("turn it", "", images=[torch.rand(4, 4, 3)], picture_labels=False)
    assert fake.options == [{"grounding_area": 147456}] * 2


def test_the_model_layout_is_part_of_the_cache_key():
    request = {"prompt": "turn it", "negative_prompt": "", "images": [torch.rand(4, 4, 3)]}
    plain = QwenClipTextEncoder(_FakeEncoder(), device="cpu", model_fingerprint="qwen-te")
    plus = QwenClipTextEncoder(_FakeEncoder(), device="cpu", model_fingerprint="qwen-te", vision_layout=PLUS)
    assert plain._encode_fn_and_key(request)[1] != plus._encode_fn_and_key(request)[1]
