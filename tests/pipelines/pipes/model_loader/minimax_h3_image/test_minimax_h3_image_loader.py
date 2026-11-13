from __future__ import annotations

from types import SimpleNamespace

import pytest
import torch
from PIL import Image

from src.pipelines.pipes._shared.generation import clip_batch as clip_batch_module
from src.pipelines.pipes.model_loader.minimax_h3.clip import MiniMaxH3ClipTextEncoder
from src.pipelines.pipes.model_loader.minimax_h3_image.clip import MiniMaxH3ImageClipTextEncoder
from src.pipelines.pipes.model_loader.minimax_h3_image.main import ModelLoaderMiniMaxH3ImagePipe
from src.platform.runtime.native.memory.residency import TextEncodeWorkingMemoryError


class _FakeEncoder:
    role = "qwen3vl_32b"

    def __init__(self):
        self.calls = []
        self.module = SimpleNamespace(visual=object())

    def encode_request(self, prompt, images=None, **kwargs):
        self.calls.append((prompt, images, kwargs))
        return {"context": torch.zeros(1, 2, 4), "token_tags": torch.ones(2, dtype=torch.long)}


def _request(*sizes):
    return {"prompt": "swap the jacket", "images": [Image.new("RGB", size, "red") for size in sizes]}


def test_pictures_reach_the_encoder_labelled_and_sized_like_the_reference_node():
    encoder = _FakeEncoder()
    clip = MiniMaxH3ImageClipTextEncoder(te_loader=lambda: encoder, model_fingerprint="fp")
    encode, _key = clip._encode_fn_and_key(_request((800, 600), (2000, 1000)))
    encode()
    prompt, images, kwargs = encoder.calls[0]
    assert prompt == "swap the jacket"
    assert [tuple(image.shape) for image in images] == [(1024, 1376, 3), (1024, 2048, 3)]
    assert kwargs == {"picture_label": "Picture {}: ", "max_prompt_tokens": 4096}


class _CountingEncoder(_FakeEncoder):
    def __init__(self):
        super().__init__()
        self.counted = []

    def request_token_counts(self, text, image_sizes=None, **kwargs):
        self.counted.append((text, list(image_sizes or ()), kwargs))
        vision = sum(width * height // 1024 for width, height in image_sizes or ())
        return vision + 10, vision

    def encode_working_gb(self, num_tokens, num_vision_tokens=0):
        return num_tokens / 1000


def _bilevel_request(*sizes):
    return {"prompt": "swap the jacket", "images": [Image.new("1", size) for size in sizes]}


def test_the_working_memory_follows_the_shape_of_each_picture_not_its_file_size():
    encoder = _CountingEncoder()
    clip = MiniMaxH3ImageClipTextEncoder(te_loader=lambda: encoder, model_fingerprint="fp")
    requests = [_bilevel_request((5504, 8256), (1000, 1250)), _bilevel_request((2831, 8256), (1000, 1250))]

    working_gb = clip._encode_working_gb_for(requests)

    assert working_gb(encoder, [0]) < working_gb(encoder, [1]) == working_gb(encoder, [0, 1])
    assert encoder.counted[:2] == [
        ("swap the jacket", [(1024, 1536), (1024, 1280)], {"picture_label": "Picture {}: ", "max_prompt_tokens": 4096}),
        ("swap the jacket", [(1024, 2976), (1024, 1280)], {"picture_label": "Picture {}: ", "max_prompt_tokens": 4096}),
    ]


def test_pictures_without_a_vision_tower_leave_the_refusal_to_the_encode():
    encoder = _CountingEncoder()
    encoder.module = SimpleNamespace(visual=None)
    clip = MiniMaxH3ImageClipTextEncoder(te_loader=lambda: encoder, model_fingerprint="fp")

    assert clip._encode_working_gb_for([_bilevel_request((64, 64))])(encoder, [0]) == 0.0
    assert encoder.counted == []


@pytest.mark.usefixtures("fresh_embed_cache")
def test_only_the_prompts_the_embed_cache_misses_size_the_working_memory():
    encoder = _CountingEncoder()
    clip = MiniMaxH3ImageClipTextEncoder(te_loader=lambda: encoder, device="cpu", model_fingerprint="fp")
    clip.encode_prompts([_request((64, 64))])
    encoder.counted.clear()

    clip.encode_prompts([_request((64, 64)), _request((64, 128))])

    assert [sizes for _text, sizes, _kwargs in encoder.counted] == [[(1024, 2048)]]


def _refused(monkeypatch):
    def refuse(*_args, **_kwargs):
        raise TextEncodeWorkingMemoryError("generic", working_gb=48.6, room_gb=9.1)

    monkeypatch.setattr(clip_batch_module, "run_text_encode_batch", refuse)


def test_a_refused_edit_names_its_pictures_and_the_narrow_ones(monkeypatch):
    _refused(monkeypatch)
    clip = MiniMaxH3ImageClipTextEncoder(te_loader=_CountingEncoder, model_fingerprint="fp")

    with pytest.raises(TextEncodeWorkingMemoryError) as refused:
        clip.encode_prompts([_bilevel_request((2831, 8256), (1000, 1250), (4096, 1024))])

    message = str(refused.value)
    assert "these 3 pictures" in message
    assert "48.6 GB" in message and "9.1 GB of RAM" in message
    assert "(picture 1 is 1:2.9, picture 3 is 1:4)" in message
    assert (refused.value.working_gb, refused.value.room_gb) == (48.6, 9.1)


def test_a_refused_edit_with_many_narrow_pictures_counts_them_instead_of_listing_them(monkeypatch):
    _refused(monkeypatch)
    clip = MiniMaxH3ImageClipTextEncoder(te_loader=_CountingEncoder, model_fingerprint="fp")

    with pytest.raises(TextEncodeWorkingMemoryError) as refused:
        clip.encode_prompts([_bilevel_request(*([(1024, 4096)] * 15 + [(2831, 8256)]))])

    message = str(refused.value)
    assert "these 16 pictures" in message
    assert "(16 of them are long and narrow, up to 1:4)" in message
    assert "picture 2 is" not in message


def test_a_refused_single_picture_is_named_alone_and_a_square_one_is_not_called_narrow(monkeypatch):
    _refused(monkeypatch)
    clip = MiniMaxH3ImageClipTextEncoder(te_loader=_CountingEncoder, model_fingerprint="fp")

    with pytest.raises(TextEncodeWorkingMemoryError) as refused:
        clip.encode_prompts([_bilevel_request((4000, 4000))])

    message = str(refused.value)
    assert "cannot read this picture:" in message and "about 48.6 GB of working memory for it" in message
    assert "is 1:" not in message


def test_a_refused_prompt_without_pictures_asks_for_a_shorter_prompt(monkeypatch):
    _refused(monkeypatch)
    clip = MiniMaxH3ImageClipTextEncoder(te_loader=_CountingEncoder, model_fingerprint="fp")

    with pytest.raises(TextEncodeWorkingMemoryError) as refused:
        clip.encode_prompts([{"prompt": "a lighthouse"}])

    message = str(refused.value)
    assert "cannot encode this prompt" in message and "Send a shorter prompt." in message
    assert "pictures" not in message


def test_text_only_prompts_send_no_pictures():
    encoder = _FakeEncoder()
    clip = MiniMaxH3ImageClipTextEncoder(encoder, model_fingerprint="fp")
    encode, _key = clip._encode_fn_and_key({"prompt": "a lighthouse"})
    encode()
    assert encoder.calls[0][1] is None


def test_the_cached_prompt_depends_on_the_pictures_and_differs_from_the_video_presets():
    image = MiniMaxH3ImageClipTextEncoder(_FakeEncoder(), model_fingerprint="fp")
    video = MiniMaxH3ClipTextEncoder(lambda: _FakeEncoder(), model_fingerprint="fp")
    _, red = image._encode_fn_and_key(_request((64, 64)))
    _, blue = image._encode_fn_and_key({"prompt": "swap the jacket", "images": [Image.new("RGB", (64, 64), "blue")]})
    _, plain = image._encode_fn_and_key({"prompt": "swap the jacket"})
    _, video_plain = video._encode_fn_and_key({"prompt": "swap the jacket"})
    assert len({red, blue, plain, video_plain}) == 4


def test_the_text_encoder_is_cached_under_the_same_key_as_minimax_h3():
    pipe = ModelLoaderMiniMaxH3ImagePipe(config=ModelLoaderMiniMaxH3ImagePipe.get_default_config())
    ctx = SimpleNamespace(te_path="/models/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors", dtype="bfloat16")
    assert pipe._te_key(ctx) == "native/te//models/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"
    assert pipe._te_fingerprint(ctx) == "/models/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors|bfloat16|vision=True"
