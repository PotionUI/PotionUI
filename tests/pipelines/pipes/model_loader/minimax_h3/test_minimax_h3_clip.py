"""Tests for MiniMaxH3ClipTextEncoder: a thin adapter over the REAL
`MiniMaxH3TextEncoder.encode_request` (text_encoders/qwen3.py) -- the
presentation-building logic (labels, vision-block splicing, token_tags) now
lives ONLY there, so these are integration tests through the real
tokenizer + real `preprocess_qwen3_vl_image` + real `encode_request`/
`encode_presentation`/`_encode_with_vision` code paths. Only the actual
neural forward (attention layers) is faked -- no weights, CPU-only (every
adapter is constructed with `device="cpu"` explicitly so `run_text_encode_
batch`'s placement machinery takes its documented CPU/no-CUDA short-circuit
-- `encode_fn()` called directly, no residency/co-residency decision)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
import torch
import torch.nn as nn
from PIL import Image

from src.pipelines.pipes._shared.generation.clip_batch import SequentialWindowClipTextEncoder
from src.platform.runtime.native.errors import NativeEngineUnsupportedError
from src.platform.runtime.native.text_encoders.qwen3 import MiniMaxH3Reference, MiniMaxH3TextEncoder
from src.platform.runtime.native.text_encoders.qwen3_vl_vision import (
    H3_VISION_MAX_PIXELS,
    H3_VISION_MIN_PIXELS,
)
from src.platform.runtime.native.text_encoders.tokenization import MiniMaxH3Tokenizer
from src.pipelines.pipes.model_loader.minimax_h3.clip import MiniMaxH3ClipTextEncoder

_TEXT_HIDDEN = 5120


class _FakeVisual:
    """Stands in for `Qwen3VLVisionTower`: real shape-derivation attrs (so
    `preprocess_qwen3_vl_image`'s real math runs), a faked neural forward."""

    spatial_merge_size = 2
    patch_size = 16
    patch_embed = SimpleNamespace(temporal_patch_size=2)
    deepstack_indexes: list = []

    def __call__(self, patches: torch.Tensor, grid_thw: torch.Tensor):
        num_merged = int(grid_thw[0].prod().item()) // (self.spatial_merge_size ** 2)
        return torch.zeros(num_merged, _TEXT_HIDDEN), []


class _FakeQwen3Module(nn.Module):
    """Stands in for `Qwen3Model`: real `.model.embed_tokens`/`.visual`
    attachment shape (so `MiniMaxH3TextEncoder._encode_with_vision`'s real
    splicing/position-id code runs), a faked attention forward. A real
    `nn.Module` (not a plain object) so `MiniMaxH3TextEncoder.to(device)`
    (-> `_module_to` -> `module.to(device)`) -- now exercised on every
    `encode_prompt` call via `SequentialWindowClipTextEncoder`'s placement
    machinery -- has something to call."""

    def __init__(self, *, has_vision: bool = True):
        super().__init__()
        self.model = SimpleNamespace(embed_tokens=lambda ids: torch.zeros(1, ids.shape[1], _TEXT_HIDDEN))
        if has_vision:
            self.visual = _FakeVisual()
        self.calls = []

    def forward(self, input_ids=None, attention_mask=None, layers_to_extract=None, capture=None,
                inputs_embeds=None, position_ids=None, deepstack_embeds=None, visual_pos_mask=None):
        self.calls.append((input_ids, inputs_embeds))
        seq_len = input_ids.shape[1] if input_ids is not None else inputs_embeds.shape[1]
        return torch.zeros(1, len(layers_to_extract), seq_len, _TEXT_HIDDEN)


def _real_text_encoder(*, has_vision: bool = True) -> MiniMaxH3TextEncoder:
    return MiniMaxH3TextEncoder(_FakeQwen3Module(has_vision=has_vision), MiniMaxH3Tokenizer(), device="cpu")


def _tiny_image() -> Image.Image:
    return Image.new("RGB", (64, 48), color=(10, 20, 30))


# -- t2va: plain prompt, no vision -------------------------------------------

def test_t2va_encode_prompt_is_all_text_tagged():
    te = _real_text_encoder()
    encoder = MiniMaxH3ClipTextEncoder(lambda: te, device="cpu")
    result = encoder.encode_prompt("a red car", "")

    tokenizer = MiniMaxH3Tokenizer()
    expected_len = len(tokenizer("a red car"))
    assert result.embeds["context"].shape == (1, expected_len, _TEXT_HIDDEN)
    assert torch.equal(result.embeds["token_tags"], torch.ones(expected_len, dtype=torch.long))
    assert result.n_embeds == {}  # guidance-distilled: never a negative branch


# -- fl2va: label + vision block per keyframe, through the REAL encode_request --

def test_fl2va_single_keyframe_boundary_and_pad_run_length():
    te = _real_text_encoder()
    encoder = MiniMaxH3ClipTextEncoder(lambda: te, device="cpu")
    result = encoder.encode_prompt("a prompt", "", images=[_tiny_image()])

    tokenizer = MiniMaxH3Tokenizer()
    label_len = len(tokenizer("<Picture 1>: "))
    prompt_len = len(tokenizer("a prompt"))
    tags = result.embeds["token_tags"]

    # label -> vision block (>=3: start + >=1 pad + end) -> prompt, packed order.
    assert tags[:label_len].tolist() == [1] * label_len          # TEXT
    assert tags[-prompt_len:].tolist() == [1] * prompt_len       # TEXT
    vision_span = tags[label_len:-prompt_len]
    assert vision_span.numel() >= 3
    assert vision_span.tolist() == [0] * vision_span.numel()     # VIDEO, contiguous
    assert result.embeds["context"].shape == (1, tags.numel(), _TEXT_HIDDEN)


def test_fl2va_two_keyframes_labelled_first_and_last_in_order():
    te = _real_text_encoder()
    encoder = MiniMaxH3ClipTextEncoder(lambda: te, device="cpu")
    result = encoder.encode_prompt("prompt", "", images=[_tiny_image(), _tiny_image()])

    tokenizer = MiniMaxH3Tokenizer()
    label1 = tokenizer("<Picture 1>: ")
    label2 = tokenizer("<Picture 2>: ")
    tags = result.embeds["token_tags"].tolist()

    # Two TEXT-tagged label runs, each immediately followed by a VIDEO span.
    assert tags[:len(label1)] == [1] * len(label1)
    after_first_vision = tags[len(label1):]
    first_video_len = next(i for i, t in enumerate(after_first_vision) if t == 1)
    assert first_video_len >= 3
    second_label_start = len(label1) + first_video_len
    assert tags[second_label_start:second_label_start + len(label2)] == [1] * len(label2)


def test_fl2va_raises_without_a_vision_tower():
    te = _real_text_encoder(has_vision=False)
    encoder = MiniMaxH3ClipTextEncoder(lambda: te, device="cpu")
    with pytest.raises(NativeEngineUnsupportedError):
        encoder.encode_prompt("prompt", "", images=[_tiny_image()])


def test_fl2va_never_produces_negative_embeds():
    te = _real_text_encoder()
    encoder = MiniMaxH3ClipTextEncoder(lambda: te, device="cpu")
    result = encoder.encode_prompt("prompt", "a negative prompt", images=[_tiny_image()])
    assert result.n_embeds == {}


def test_image_tensors_reach_the_real_encoder_hwc_float01():
    # The adapter's ONLY remaining vision-side job: PIL -> [H, W, 3] float
    # [0, 1]. Verified by making preprocess_qwen3_vl_image (called inside the
    # real encode_request) actually run without shape/range errors on a real
    # PIL image, exercised implicitly by the tests above; this test pins the
    # conversion helper itself.
    from src.pipelines.pipes.model_loader.minimax_h3.clip import _to_hwc_float01

    tensor = _to_hwc_float01(_tiny_image())
    assert tensor.shape == (48, 64, 3)
    assert tensor.dtype == torch.float32
    assert 0.0 <= tensor.min() and tensor.max() <= 1.0


# -- keyframe pixel budget: prompt_encoder's `image_max_pixels` reaching the
# vision tower's smart-resize, so a large keyframe is not read at the
# checkpoint's full 16.7MP bound regardless of the canvas being rendered. ----


class _RecordingEncodeRequest(MiniMaxH3TextEncoder):
    """Real encoder, with the bounds actually handed to `encode_request`
    recorded -- the kwarg is what decides the vision grid, so asserting on it
    is the whole contract this adapter owns."""

    def __init__(self):
        super().__init__(_FakeQwen3Module(), MiniMaxH3Tokenizer(), device="cpu")
        self.bounds: list = []

    def encode_request(self, text, images=None, min_pixels=H3_VISION_MIN_PIXELS,
                       max_pixels=H3_VISION_MAX_PIXELS):
        self.bounds.append((min_pixels, max_pixels))
        return super().encode_request(text, images=images, min_pixels=min_pixels, max_pixels=max_pixels)


def _encode_with_budget(te, budget):
    encoder = MiniMaxH3ClipTextEncoder(lambda: te, device="cpu")
    request = {"prompt": "a prompt", "negative_prompt": "", "images": [_tiny_image()]}
    if budget is not None:
        request["image_max_pixels"] = budget
    return encoder, encoder.encode_prompts([request])


def test_absent_budget_leaves_the_checkpoint_bound_in_place():
    te = _RecordingEncodeRequest()
    _encode_with_budget(te, None)
    assert te.bounds == [(H3_VISION_MIN_PIXELS, H3_VISION_MAX_PIXELS)]


def test_budget_reaches_encode_request_as_max_pixels():
    te = _RecordingEncodeRequest()
    _encode_with_budget(te, 2 * 1344 * 768)
    assert te.bounds == [(H3_VISION_MIN_PIXELS, 2 * 1344 * 768)]


def test_budget_below_the_checkpoint_minimum_is_clamped_up():
    # `preprocess_qwen3_vl_image` applies max/min as an if/elif single pass, so
    # a max under the min would silently win and produce a below-spec grid.
    te = _RecordingEncodeRequest()
    _encode_with_budget(te, 1024)
    assert te.bounds == [(H3_VISION_MIN_PIXELS, H3_VISION_MIN_PIXELS)]


def test_a_smaller_budget_actually_shrinks_the_vision_grid():
    # The end-to-end point of the knob: fewer vision tokens in the encoded
    # presentation, which is what the vision forward is priced by.
    te = _real_text_encoder()
    encoder = MiniMaxH3ClipTextEncoder(lambda: te, device="cpu")
    big = Image.new("RGB", (1024, 1024), color=(10, 20, 30))
    base = {"prompt": "a prompt", "negative_prompt": "", "images": [big]}

    uncapped = encoder.encode_prompts([dict(base)])[0]
    capped = encoder.encode_prompts([dict(base, image_max_pixels=H3_VISION_MIN_PIXELS)])[0]

    assert capped.embeds["token_tags"].numel() < uncapped.embeds["token_tags"].numel()


def test_the_same_image_at_two_budgets_gets_two_cache_keys():
    te = _real_text_encoder()
    encoder = MiniMaxH3ClipTextEncoder(lambda: te, device="cpu", model_fingerprint="fp")
    base = {"prompt": "a prompt", "negative_prompt": "", "images": [_tiny_image()]}

    keys = [
        encoder._encode_fn_and_key(dict(base, image_max_pixels=budget))[1]
        for budget in (1 * 1024 * 1024, 4 * 1024 * 1024)
    ]
    assert keys[0] != keys[1]


def test_no_budget_keeps_the_cache_key_it_had_before():
    # An unconfigured knob must not invalidate conditioning cached by a
    # generation that predates it.
    te = _real_text_encoder()
    encoder = MiniMaxH3ClipTextEncoder(lambda: te, device="cpu", model_fingerprint="fp")
    base = {"prompt": "a prompt", "negative_prompt": "", "images": [_tiny_image()]}

    plain = encoder._encode_fn_and_key(dict(base))[1]
    zero = encoder._encode_fn_and_key(dict(base, image_max_pixels=0))[1]
    assert plain == zero


# -- lazy TE acquisition (the "TE reloaded even on a conditioning cache hit"
# fix): the loader hands `clip` a zero-arg te_factory, NOT a resolved
# encoder -- resolved only on the FIRST actual read of `self.encoder`. -----

def test_te_factory_is_not_called_at_construction():
    calls = []

    def factory():
        calls.append(1)
        return _real_text_encoder()

    MiniMaxH3ClipTextEncoder(factory, device="cpu")
    assert calls == [], "constructing the adapter must not touch the factory at all"


def test_te_factory_is_not_called_by_reading_the_model_fingerprint():
    # prompt_encoder's OWN conditioning-cache lookup keys off
    # `clip._model_fingerprint` alone -- must be readable with zero TE
    # resolution, or the cache lookup itself would defeat the whole point.
    calls = []

    def factory():
        calls.append(1)
        return _real_text_encoder()

    encoder = MiniMaxH3ClipTextEncoder(factory, device="cpu", model_fingerprint="fp-123")
    assert encoder._model_fingerprint == "fp-123"
    assert calls == []


def test_te_factory_is_called_exactly_once_on_first_real_encode():
    # `te` held in a LOCAL (not constructed inline inside `factory`):
    # MiniMaxH3ClipTextEncoder stores the RESOLVED encoder via WeakModelRef,
    # matching production (the MODELS cache is the one true strong owner) --
    # a factory that hands out a fresh, unretained object each call would
    # have it collected the instant the property returns, same footgun
    # documented elsewhere in this suite. A real `models.acquire(...)`-backed
    # factory always returns the SAME cache-held object, which is what this
    # local mimics.
    te = _real_text_encoder()
    calls = []

    def factory():
        calls.append(1)
        return te

    encoder = MiniMaxH3ClipTextEncoder(factory, device="cpu")
    assert calls == []
    encoder.encode_prompt("a prompt", "")
    assert calls == [1]


def test_te_factory_is_not_re_invoked_on_a_second_encode_same_instance():
    # Resolved once, reused for the rest of this clip instance's lifetime
    # (one generation) -- not re-acquired per request within the same batch.
    te = _real_text_encoder()
    calls = []

    def factory():
        calls.append(1)
        return te

    encoder = MiniMaxH3ClipTextEncoder(factory, device="cpu")
    encoder.encode_prompt("first", "")
    encoder.encode_prompt("second", "")
    assert calls == [1]


def test_a_conditioning_cache_hit_upstream_never_touches_the_te_factory():
    # The actual bug scenario: prompt_encoder's own MODELS.acquire(key=
    # "prompt_encoder.conditioning", ...) wraps clip.encode_prompts entirely
    # -- on a hit, the loader closure is never called. Simulated directly
    # here (no need for the real prompt_encoder pipe): if the caller simply
    # never invokes encode_prompt/encode_prompts on a cache hit, the factory
    # must never have run.
    calls = []

    def factory():
        calls.append(1)
        return _real_text_encoder()

    encoder = MiniMaxH3ClipTextEncoder(factory, device="cpu")
    # A "cache hit": the caller never calls encoder.encode_prompt(s) at all.
    assert calls == []


# -- prompt_encoder integration marker ---------------------------------------

def test_declares_full_image_batch_forwarding():
    # fl2va's first/last keyframes are a FIXED pair shared by every output of
    # one generation (every `quantity` variation denoises the same two
    # keyframes) -- prompt_encoder must forward the whole image list to
    # every request rather than selecting one image per output index. See
    # ClipTextEncoder.forwards_full_image_batch's docstring.
    te = _real_text_encoder()
    encoder = MiniMaxH3ClipTextEncoder(lambda: te, device="cpu")
    assert encoder.forwards_full_image_batch is True


# -- GPU placement (the "190s at 0 VRAM" fix) --------------------------------

def test_is_a_sequential_window_clip_text_encoder():
    # The base class whose encode_prompts() moves the TE to `self.device`
    # ONCE per batch and offloads in `finally` (run_text_encode_batch) --
    # without it the 32B TE never leaves the CPU it was loaded on.
    te = _real_text_encoder()
    encoder = MiniMaxH3ClipTextEncoder(lambda: te, device="cpu")
    assert isinstance(encoder, SequentialWindowClipTextEncoder)


def test_encode_prompts_batches_multiple_requests_through_one_window():
    te = _real_text_encoder()
    encoder = MiniMaxH3ClipTextEncoder(lambda: te, device="cpu")
    requests = [
        {"prompt": "a red car", "negative_prompt": ""},
        {"prompt": "a blue car", "negative_prompt": ""},
    ]
    results = encoder.encode_prompts(requests)
    assert len(results) == 2
    assert results[0].p_prompt == "a red car"
    assert results[1].p_prompt == "a blue car"
    # Different prompts (different token counts) -> genuinely different
    # context shapes, proving each request was actually encoded on its own
    # terms rather than one result reused for both.
    assert results[0].embeds["context"].shape[1] == len(MiniMaxH3Tokenizer()("a red car"))
    assert results[1].embeds["context"].shape[1] == len(MiniMaxH3Tokenizer()("a blue car"))


def test_cpu_device_takes_the_documented_no_placement_short_circuit():
    # run_text_encode's own documented contract: "On a CPU / no-CUDA device
    # it just calls encode_fn()" -- no .to() round trip at all, since the TE
    # is already resident where it was loaded. Pins that this adapter reaches
    # that exact code path (routes through SequentialWindowClipTextEncoder's
    # placement machinery) rather than, say, silently no-op'ing some other
    # way that would happen to look the same on CPU. See the GPU test below
    # for the actual device-move proof.
    fake_module = _FakeQwen3Module()
    calls = []
    real_to = fake_module.to

    def _spy_to(device):
        calls.append(str(device))
        return real_to(device)

    fake_module.to = _spy_to
    te = MiniMaxH3TextEncoder(fake_module, MiniMaxH3Tokenizer(), device="cpu")
    encoder = MiniMaxH3ClipTextEncoder(lambda: te, device="cpu")
    encoder.encode_prompt("a prompt", "")
    assert calls == [], f"expected no .to() calls on the CPU short-circuit path, got {calls}"


# -- ref2va: reference_labels routes to encode_reference_request ------------

class _RecordingEncodeReferenceRequest(MiniMaxH3TextEncoder):
    """Real encoder, with every call to `encode_request`/`encode_reference_
    request` recorded -- proves the adapter routes to the RIGHT one rather
    than always calling `encode_request` regardless of `references`."""

    def __init__(self):
        super().__init__(_FakeQwen3Module(), MiniMaxH3Tokenizer(), device="cpu")
        self.calls: list = []

    def encode_request(self, text, images=None, min_pixels=H3_VISION_MIN_PIXELS, max_pixels=H3_VISION_MAX_PIXELS):
        self.calls.append(("encode_request", text, images, None))
        return super().encode_request(text, images=images, min_pixels=min_pixels, max_pixels=max_pixels)

    def encode_reference_request(self, text, references, min_pixels=H3_VISION_MIN_PIXELS,
                                  max_pixels=H3_VISION_MAX_PIXELS, **kwargs):
        self.calls.append(("encode_reference_request", text, references, None))
        return super().encode_reference_request(
            text, references, min_pixels=min_pixels, max_pixels=max_pixels, **kwargs,
        )


def _image_references(*images):
    """`prompt_encoder`'s own `references` request shape for image entries."""
    return [{"kind": "image", "media": image} for image in images]


def test_references_absent_routes_to_encode_request():
    te = _RecordingEncodeReferenceRequest()
    encoder = MiniMaxH3ClipTextEncoder(lambda: te, device="cpu")
    request = {"prompt": "a prompt", "negative_prompt": "", "images": [_tiny_image()]}
    encoder.encode_prompts([request])
    assert [c[0] for c in te.calls] == ["encode_request"]


def test_references_present_routes_to_encode_reference_request():
    te = _RecordingEncodeReferenceRequest()
    encoder = MiniMaxH3ClipTextEncoder(lambda: te, device="cpu")
    request = {
        "prompt": "a prompt", "negative_prompt": "",
        "references": _image_references(_tiny_image()),
    }
    encoder.encode_prompts([request])
    assert [c[0] for c in te.calls] == ["encode_reference_request"]
    # The adapter hands over a typed reference list, not label strings -- the
    # TE derives "<Picture i>: " itself.
    references = te.calls[0][2]
    assert [r.kind for r in references] == ["image"]
    assert references[0].media.shape == (48, 64, 3)


def test_an_audio_reference_reaches_the_encoder_with_no_media():
    """A waveform never reaches the conditioner: an audio reference carries
    its kind and its `has_audio` flag and nothing else, which is what earns it
    an "<Audio j>: " label and no vision block."""
    te = _RecordingEncodeReferenceRequest()
    encoder = MiniMaxH3ClipTextEncoder(lambda: te, device="cpu")
    request = {
        "prompt": "a prompt", "negative_prompt": "",
        "references": _image_references(_tiny_image()) + [{"kind": "audio", "media": "/media/track.wav"}],
    }
    encoder.encode_prompts([request])
    references = te.calls[0][2]
    assert [r.kind for r in references] == ["image", "audio"]
    assert references[1].media is None
    assert references[1].has_audio is True
    # A video reference's own soundtrack is not read here, so `has_audio`
    # means exactly "this is an audio reference".
    assert references[0].has_audio is False


def test_an_unknown_reference_kind_raises():
    te = _real_text_encoder()
    encoder = MiniMaxH3ClipTextEncoder(lambda: te, device="cpu")
    request = {
        "prompt": "a prompt", "negative_prompt": "",
        "references": [{"kind": "mesh", "media": _tiny_image()}],
    }
    with pytest.raises(ValueError, match="'image', 'video' or 'audio'"):
        encoder.encode_prompts([request])


def test_a_reference_video_without_a_frame_count_raises():
    """Truncating the encoder's view and the generator's condition-encode at
    different frames conditions on two different videos, so a missing
    `reference_video_frames` is refused rather than defaulted."""
    te = _real_text_encoder()
    encoder = MiniMaxH3ClipTextEncoder(lambda: te, device="cpu")
    request = {
        "prompt": "a prompt", "negative_prompt": "",
        "references": [{"kind": "video", "media": "/media/clip.mp4"}],
    }
    with pytest.raises(ValueError, match="reference_video_frames"):
        encoder.encode_prompts([request])


def test_reference_images_without_a_vision_tower_raises():
    te = _real_text_encoder(has_vision=False)
    encoder = MiniMaxH3ClipTextEncoder(lambda: te, device="cpu")
    request = {
        "prompt": "a prompt", "negative_prompt": "",
        "references": _image_references(_tiny_image()),
    }
    with pytest.raises(NativeEngineUnsupportedError):
        encoder.encode_prompts([request])


def test_an_audio_only_reference_needs_no_vision_tower():
    """An audio reference contributes no vision block, so it must not trip
    the guard a request carrying pixels does."""
    te = _real_text_encoder(has_vision=False)
    encoder = MiniMaxH3ClipTextEncoder(lambda: te, device="cpu")
    request = {
        "prompt": "a prompt", "negative_prompt": "",
        "references": [{"kind": "audio", "media": "/media/track.wav"}],
    }
    encoder.encode_prompts([request])


def test_reference_request_label_is_derived_by_the_encoder_not_the_request():
    """The request carries reference KINDS, never label text: the encoder
    numbers "<Picture i>: "/"<Video k>: "/"<Audio j>: " per modality itself."""
    te = _real_text_encoder()
    encoder = MiniMaxH3ClipTextEncoder(lambda: te, device="cpu")
    request = {
        "prompt": "a prompt", "negative_prompt": "",
        "references": _image_references(_tiny_image()),
    }
    result = encoder.encode_prompts([request])[0]

    tokenizer = MiniMaxH3Tokenizer()
    derived_len = len(tokenizer("<Picture 1>: "))
    prompt_len = len(tokenizer("a prompt"))
    tags = result.embeds["token_tags"]
    assert tags[:derived_len].tolist() == [1] * derived_len
    assert tags[-prompt_len:].tolist() == [1] * prompt_len
    # The vision block sits right after the derived label.
    assert tags[derived_len].item() == 0


def test_references_and_fl2va_images_produce_different_cache_keys():
    # ref2va vs. fl2va with the SAME image must not alias to the same cached
    # conditioning: the presentation label set is part of what was encoded.
    te = _real_text_encoder()
    encoder = MiniMaxH3ClipTextEncoder(lambda: te, device="cpu", model_fingerprint="fp")
    image = _tiny_image()
    fl2va_key = encoder._encode_fn_and_key(
        {"prompt": "a prompt", "negative_prompt": "", "images": [image]}
    )[1]
    ref2va_key = encoder._encode_fn_and_key(
        {"prompt": "a prompt", "negative_prompt": "", "references": _image_references(image)}
    )[1]
    assert fl2va_key != ref2va_key


def test_two_different_reference_orders_get_different_cache_keys():
    # The packed sequence is order-sensitive, so the same references in a
    # different order are a different request and must not alias.
    te = _real_text_encoder()
    encoder = MiniMaxH3ClipTextEncoder(lambda: te, device="cpu", model_fingerprint="fp")
    first = Image.new("RGB", (64, 48), color=(10, 20, 30))
    second = Image.new("RGB", (64, 48), color=(200, 100, 50))
    forward_key = encoder._encode_fn_and_key(
        {"prompt": "p", "negative_prompt": "", "references": _image_references(first, second)}
    )[1]
    reversed_key = encoder._encode_fn_and_key(
        {"prompt": "p", "negative_prompt": "", "references": _image_references(second, first)}
    )[1]
    assert forward_key != reversed_key


def test_the_same_reference_video_at_two_clip_lengths_gets_different_keys(monkeypatch):
    """`reference_video_frames` is the truncation point, so it is part of the
    presentation: the same file cut to two lengths is two different `<t
    seconds>` block sequences and must not share a cached conditioning."""
    from src.pipelines.pipes.model_loader.minimax_h3 import clip as clip_module

    monkeypatch.setattr(clip_module, "_to_fhwc_float01", lambda path, frames: torch.zeros(4, 8, 8, 3))
    te = _real_text_encoder()
    encoder = MiniMaxH3ClipTextEncoder(lambda: te, device="cpu", model_fingerprint="fp")

    def _key(num_frames):
        return encoder._encode_fn_and_key({
            "prompt": "p", "negative_prompt": "",
            "references": [{"kind": "video", "media": "/media/clip.mp4"}],
            "reference_video_frames": num_frames,
        })[1]

    assert _key(124) != _key(226)


# -- the cache key must cover EVERY reference modality -----------------------
#
# `_encode_fn_and_key` builds the ref2va key from the reference LIST. These
# drive `_reference_fingerprint`/the key builder directly with mixed-modality
# lists: a key that silently aliases returns the WRONG conditioning with no
# error, so each modality's contribution to it is pinned individually.

def _ref_key(encoder, references, monkeypatch):
    """The cache key the REAL `_encode_fn_and_key` builds for `references`.

    Drives the production key builder rather than restating its format here --
    a helper that recomputed the key would pass no matter what `clip.py` does,
    which is the whole failure mode this guards. `_build_references` is
    stubbed only so a video reference can be handed over as an in-memory
    tensor instead of a file on disk; everything downstream of it is the real
    code path.
    """
    from src.pipelines.pipes.model_loader.minimax_h3 import clip as clip_module

    monkeypatch.setattr(clip_module, "_build_references", lambda request: list(references))
    return encoder._encode_fn_and_key({
        "prompt": "p", "negative_prompt": "",
        "references": [{"kind": "image", "media": _tiny_image()}],
    })[1]


def test_reference_sets_differing_only_in_a_video_member_get_different_keys(monkeypatch):
    te = _real_text_encoder()
    encoder = MiniMaxH3ClipTextEncoder(lambda: te, device="cpu", model_fingerprint="fp")
    _key = lambda refs: _ref_key(encoder, refs, monkeypatch)
    image = MiniMaxH3Reference(kind="image", media=torch.rand(8, 8, 3))
    one = MiniMaxH3Reference(kind="video", media=torch.zeros(4, 8, 8, 3))
    other = MiniMaxH3Reference(kind="video", media=torch.ones(4, 8, 8, 3))
    assert _key([image, one]) != _key([image, other])


def test_adding_an_audio_reference_changes_the_key(monkeypatch):
    # An audio reference's CONTENT never reaches the conditioner, but its
    # presence shifts every later label's number and every later block's place
    # on the packed clock -- so the key must move.
    te = _real_text_encoder()
    encoder = MiniMaxH3ClipTextEncoder(lambda: te, device="cpu", model_fingerprint="fp")
    _key = lambda refs: _ref_key(encoder, refs, monkeypatch)
    image = MiniMaxH3Reference(kind="image", media=torch.rand(8, 8, 3))
    audio = MiniMaxH3Reference(kind="audio", has_audio=True)
    assert _key([image]) != _key([image, audio])
    assert _key([image, audio]) != _key([audio, image])


def test_a_video_and_an_image_of_the_same_bytes_do_not_alias(monkeypatch):
    # `image_content_fingerprint` puts the tensor SHAPE in its hash header, and
    # the kind goes into the key too -- an [H, W, 3] image and an [F, H, W, 3]
    # video built from the same values are two different references.
    te = _real_text_encoder()
    encoder = MiniMaxH3ClipTextEncoder(lambda: te, device="cpu", model_fingerprint="fp")
    _key = lambda refs: _ref_key(encoder, refs, monkeypatch)
    anchor = MiniMaxH3Reference(kind="image", media=torch.rand(8, 8, 3))
    as_image = MiniMaxH3Reference(kind="image", media=torch.zeros(2, 8, 3))
    as_video = MiniMaxH3Reference(kind="video", media=torch.zeros(2, 8, 3))
    assert _key([anchor, as_image]) != _key([anchor, as_video])


def test_a_video_reference_bearing_audio_differs_from_the_same_video_silent(monkeypatch):
    te = _real_text_encoder()
    encoder = MiniMaxH3ClipTextEncoder(lambda: te, device="cpu", model_fingerprint="fp")
    _key = lambda refs: _ref_key(encoder, refs, monkeypatch)
    image = MiniMaxH3Reference(kind="image", media=torch.rand(8, 8, 3))
    frames = torch.rand(4, 8, 8, 3)
    silent = MiniMaxH3Reference(kind="video", media=frames)
    voiced = MiniMaxH3Reference(kind="video", media=frames, has_audio=True)
    assert _key([image, silent]) != _key([image, voiced])


@pytest.mark.requires_gpu
def test_gpu_encode_actually_moves_the_te_off_the_cpu_it_loaded_on():
    # Real-GPU regression for the reported bug: the 32B TE never left the CPU
    # it was loaded on (NativeEngineLoader._load_te always loads to
    # device="cpu"), so every encode ran a full-precision transformer forward
    # on the CPU -- ~190s and +11.4GB host RSS at ZERO VRAM for one prompt.
    # Tiny fake module (a single 1-element buffer, not real layers) per the
    # shared-GPU rule.
    module = _FakeQwen3Module()
    module.register_buffer("_probe", torch.zeros(1))
    devices_requested: list[str] = []
    real_to = module.to

    def _spy_to(device):
        devices_requested.append(str(torch.device(device)))
        return real_to(device)

    module.to = _spy_to
    te = MiniMaxH3TextEncoder(module, MiniMaxH3Tokenizer(), device="cpu")
    encoder = MiniMaxH3ClipTextEncoder(lambda: te, device="cuda")

    encoder.encode_prompt("a prompt", "")

    # Moved TO cuda for the encode, then back to cpu in the placement
    # machinery's own `finally` -- both legs, in order, not just "touched".
    cuda_moves = [i for i, d in enumerate(devices_requested) if d.startswith("cuda")]
    cpu_moves = [i for i, d in enumerate(devices_requested) if d == "cpu"]
    assert cuda_moves, f"expected a move to cuda, got {devices_requested}"
    assert cpu_moves and cpu_moves[-1] > cuda_moves[0], f"expected a move back to cpu after cuda, got {devices_requested}"
    assert module._probe.device.type == "cpu"  # left resting on CPU, not GPU-resident after the encode
