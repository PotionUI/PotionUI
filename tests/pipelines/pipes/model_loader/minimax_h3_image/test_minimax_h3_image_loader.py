from __future__ import annotations

from types import SimpleNamespace

import torch
from PIL import Image

from src.pipelines.pipes.model_loader.minimax_h3.clip import MiniMaxH3ClipTextEncoder
from src.pipelines.pipes.model_loader.minimax_h3_image.clip import MiniMaxH3ImageClipTextEncoder
from src.pipelines.pipes.model_loader.minimax_h3_image.main import ModelLoaderMiniMaxH3ImagePipe


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
