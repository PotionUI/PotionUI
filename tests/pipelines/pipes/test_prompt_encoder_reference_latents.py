from __future__ import annotations

from unittest.mock import patch

import numpy as np
import pytest
import torch

from src.pipelines.contracts import IOType, PipeInput
from src.pipelines.pipes.prompt_encoder.main import PromptEncoderPipe, _decode_latent_reference
from src.plugin_api.pipes import PreEncodedReference
from src.platform.runtime.native.kernels import disable_weight_init
from src.platform.runtime.native.vae.minimax_h3_video import MiniMaxH3VideoVAE

TINY_VAE = dict(
    latent_channels=24,
    block_out_channels=(8, 16),
    layers_per_block=1,
    spatial_downsample_factors=(2, 1),
    temporal_downsample_factors=(1, 2),
    norm_num_groups=2,
    decoder_num_layers=1,
    decoder_num_attention_heads=2,
    decoder_attention_head_dim=8,
    decoder_num_register_tokens=2,
    decoder_ffn_mult=2,
    clip_length=17,
    token_drop=3,
    tile_sample_min_height=64,
    tile_sample_min_width=64,
    tile_sample_min_overlap_height=16,
    tile_sample_min_overlap_width=16,
)


class TinyVae:
    def __init__(self):
        self.module = MiniMaxH3VideoVAE.from_config(TINY_VAE, disable_weight_init)
        with torch.no_grad():
            for parameter in self.module.parameters():
                if parameter.is_floating_point():
                    parameter.normal_(std=0.02)
        self.compute_dtype = torch.float32
        self.moves = []
        self.offloaded = False

    def move_to(self, device):
        self.moves.append(device)
        self.module.to(device)

    def offload(self):
        self.offloaded = True


def _mod(kind, frames):
    latent = torch.randn(1, 24, frames, 4, 6, generator=torch.Generator().manual_seed(frames))
    return PreEncodedReference(kind=kind, latent=latent, name="ref", fingerprint="ref.safetensors|image|ref|1.0")


def test_an_image_reference_latent_decodes_to_one_picture_for_the_text_encoder():
    vae = TinyVae()
    picture = _decode_latent_reference(_mod("image", 1), vae=vae)
    assert isinstance(picture, np.ndarray)
    assert picture.shape == (4 * 2, 6 * 2, 3) and picture.dtype == np.uint8
    assert vae.offloaded


def test_a_two_frame_video_reference_latent_decodes_to_frames():
    frames = _decode_latent_reference(_mod("video", 2), vae=TinyVae())
    assert frames.ndim == 4 and frames.shape[0] >= 1 and frames.shape[1:] == (8, 12, 3)


class _RecordingClip:
    forwards_full_image_batch = True
    _model_fingerprint = "clip"

    def __init__(self):
        self.requests = []

    def encode_prompts(self, requests):
        from src.platform.runtime.primitives.clip import ConditioningModel

        self.requests.extend(requests)
        return [
            ConditioningModel(p_prompt=r["prompt"], n_prompt=r["negative_prompt"], embeds={}, n_embeds={})
            for r in requests
        ]


class _RecordingModels:
    def __init__(self):
        self.fingerprints = []

    def acquire(self, *, key, fingerprint, loader):
        self.fingerprints.append(fingerprint)
        return loader()


def _reference(kind, name, fingerprint=None, seed=0):
    latent = torch.randn(1, 24, 1, 2, 2, generator=torch.Generator().manual_seed(seed))
    return PreEncodedReference(kind=kind, latent=latent, name=name, fingerprint=fingerprint or f"fp:{name}")


def _encode(reference_latents, models=None, **inputs):
    clip = _RecordingClip()
    pipe = PromptEncoderPipe({
        **PromptEncoderPipe.get_default_config(),
        "p_prompt": {"input": "a cat", "output": "a cat"},
        "n_prompt": {"input": "", "output": ""},
        **inputs.pop("config", {}),
    })
    with patch(
        "src.pipelines.pipes.prompt_encoder.main._decode_latent_reference",
        lambda reference, vae: f"decoded:{reference.name}",
    ):
        pipe.process(PipeInput(input={
            "text_encoder": clip, "reference_latents": reference_latents, "vae": object(), "MODELS": models,
            **inputs,
        }), lambda output: None)
    return clip.requests


def _labels(entries):
    names = {"image": "Picture", "video": "Video", "audio": "Audio"}
    counts = {kind: 0 for kind in names}
    labels = []
    for entry in entries:
        counts[entry["kind"]] += 1
        labels.append(f"<{names[entry['kind']]} {counts[entry['kind']]}>")
    return labels


def test_the_prompt_encoder_declares_reference_latents_as_a_latent_array_input():
    spec = next(s for s in PromptEncoderPipe.inputs() if s.name == "reference_latents")
    assert (spec.io_type, spec.is_array, spec.required) == (IOType.LATENT, True, False)


def test_reference_latents_are_presented_after_the_native_references_of_their_kind():
    (request,) = _encode(
        [_reference("audio", "voice"), _reference("image", "face")],
        reference_image=["A", "B"], reference_audio=["/media/a.wav"],
    )
    entries = request["references"]
    assert [(e["kind"], e["media"]) for e in entries] == [
        ("image", "A"), ("image", "B"), ("image", "decoded:face"), ("audio", "/media/a.wav"), ("audio", None),
    ]
    assert _labels(entries) == ["<Picture 1>", "<Picture 2>", "<Picture 3>", "<Audio 1>", "<Audio 2>"]


def test_a_reference_selection_indexes_reference_latents_in_the_packed_order():
    (request,) = _encode(
        [_reference("image", "face")], reference_image=["A"], config={"reference_selections": [[1]]},
    )
    assert [e["media"] for e in request["references"]] == ["decoded:face"]


def test_a_pre_encoded_image_without_a_vae_is_refused_by_name():
    pipe = PromptEncoderPipe({
        **PromptEncoderPipe.get_default_config(),
        "p_prompt": {"input": "a cat", "output": "a cat"}, "n_prompt": {"input": "", "output": ""},
    })
    with pytest.raises(ValueError, match="'face' needs a 'vae'"):
        pipe.process(PipeInput(input={
            "text_encoder": _RecordingClip(), "reference_latents": [_reference("image", "face")],
        }), lambda output: None)


def test_the_reference_fingerprint_keys_the_conditioning_cache():
    def key(reference):
        models = _RecordingModels()
        _encode([reference], models=models)
        return models.fingerprints[0]

    first = key(_reference("image", "face", fingerprint="bundle|1|2|image|face|1.0", seed=1))
    assert first == key(_reference("image", "face", fingerprint="bundle|1|2|image|face|1.0", seed=9))
    assert first != key(_reference("image", "face", fingerprint="bundle|1|2|image|face|0.5", seed=1))
    assert "bundle|1|2|image|face|1.0" in first
