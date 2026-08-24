"""Tests for the ONE packed order a ref2va request's references are walked in.

`pack_references` is a four-line function, but it is the seam two independent
pipes agree on -- so the tests that matter are not about the function, they are
about the two consumers deriving their traversal FROM it. A divergence between
them is silent: the request runs, every shape agrees, and each reference
conditions the generation from another reference's position. CPU-only, no
weights.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
import torch
from PIL import Image

from src.pipelines.contracts import PipeInput
from src.pipelines.pipes._shared.generation.reference_order import PACKED_KINDS, pack_references
from src.pipelines.pipes.generator.video_minimax_h3.conditioning import ReferenceMedia
from src.pipelines.pipes.generator.video_minimax_h3.main import GeneratorMinimaxH3Pipe
from src.pipelines.pipes.prompt_encoder.main import PromptEncoderPipe


def test_the_packed_kinds_are_image_video_audio_in_that_order():
    assert PACKED_KINDS == ("image", "video", "audio")


def test_pack_references_concatenates_the_groups_in_kind_order():
    assert pack_references(["i1", "i2"], ["v1"], ["a1"]) == [
        ("image", "i1"), ("image", "i2"), ("video", "v1"), ("audio", "a1"),
    ]


def test_pack_references_preserves_each_group_s_own_order():
    """Item position inside a picker is what the labels are numbered by, so a
    reordered picker must produce a reordered packing rather than a
    canonicalized one."""
    assert [media for _kind, media in pack_references(["b", "a"], [], [])] == ["b", "a"]


def test_pack_references_treats_missing_and_empty_groups_alike():
    assert pack_references() == []
    assert pack_references(None, [], None) == []
    assert pack_references(images=["i"]) == [("image", "i")]


# -- the invariant: both consumers walk that same order ----------------------


class _RecordingClip:
    """Captures the `references` list `prompt_encoder` builds. Declares
    `forwards_full_image_batch` the way the H3 adapter does."""

    forwards_full_image_batch = True

    def __init__(self):
        self.requests: list = []

    def encode_prompts(self, requests):
        from src.platform.runtime.primitives.clip import ConditioningModel

        self.requests.extend(requests)
        return [
            ConditioningModel(p_prompt=r["prompt"], n_prompt=r["negative_prompt"], embeds={}, n_embeds={})
            for r in requests
        ]


def _prompt_encoder_kinds(images, videos, audios):
    clip = _RecordingClip()
    pipe = PromptEncoderPipe({
        **PromptEncoderPipe.get_default_config(),
        "p_prompt": {"input": "a cat", "output": "a cat"},
        "n_prompt": {"input": "", "output": ""},
        "reference_video_frames": 124,
    })
    pipe.process(PipeInput(input={
        "clip": clip, "reference_image": images, "reference_video": videos, "reference_audio": audios,
    }), lambda output: None)
    return [entry["kind"] for entry in clip.requests[0]["references"]]


def _generator_kinds(images, videos, audios):
    from types import SimpleNamespace

    import src.pipelines.pipes.generator.video_minimax_h3.main as main_module

    bundle = SimpleNamespace(
        spec=SimpleNamespace(family="minimax_h3", variant="h3", sampling_settings={}, latent_format={}),
        dit=SimpleNamespace(compute_dtype=torch.float32, estimated_vram_gb=0.0, module=None,
                            move_to=lambda d: None, offload=lambda: None),
        te=None, te_cache_key=None,
        video_vae=SimpleNamespace(module=None, move_to=lambda d: None, offload=lambda: None),
        audio_vae=SimpleNamespace(module=None, move_to=lambda d: None, offload=lambda: None),
    )
    conditioning = SimpleNamespace(embeds={
        "context": torch.zeros(1, 3, 8), "token_tags": torch.zeros(3, dtype=torch.long),
    })
    pipe = GeneratorMinimaxH3Pipe({
        **GeneratorMinimaxH3Pipe.get_default_config(), "mode": "references",
        "references": [{"path": f"i{i}"} for i in range(len(images))],
        "reference_videos": [{"path": f"v{i}"} for i in range(len(videos))],
        "reference_audios": [{"path": f"a{i}"} for i in range(len(audios))],
    })
    with (
        patch.object(main_module, "_load_reference_video",
                     lambda path: ReferenceMedia(kind="video", frames=_frames(), fps=24.0)),
        patch.object(main_module, "_load_reference_audio",
                     lambda path: ReferenceMedia(kind="audio", audio=torch.zeros(2, 32000), sample_rate=32000)),
    ):
        ctx = pipe.build_context(PipeInput(input={
            "model": bundle, "conditioning": [conditioning],
            "reference_images": images, "reference_videos": videos, "reference_audios": audios,
        }))
    return [reference.kind for reference in ctx.extra.references]


def _frames(count: int = 30, size: int = 64):
    import numpy as np

    return np.zeros((count, size, size, 3), dtype=np.uint8)


def _image():
    return Image.new("RGB", (64, 48), color=(10, 20, 30))


@pytest.mark.parametrize(
    "counts,expected",
    [
        ((1, 0, 0), ["image"]),
        ((0, 1, 0), ["video"]),
        ((2, 1, 1), ["image", "image", "video", "audio"]),
        ((1, 2, 2), ["image", "video", "video", "audio", "audio"]),
    ],
)
def test_both_consumers_derive_the_same_packed_order(counts, expected):
    """THE cross-pipe invariant. The text encoder's presentation order and the
    generator's reference-block order are two traversals of one contract; if
    either one is re-sorted, filtered or built from a different list, the two
    stop agreeing here -- and nowhere else, because downstream every shape
    still lines up.
    """
    num_images, num_videos, num_audios = counts
    images = [_image() for _ in range(num_images)]
    videos = [f"/media/v{i}.mp4" for i in range(num_videos)]
    audios = [f"/media/a{i}.wav" for i in range(num_audios)]

    presentation = _prompt_encoder_kinds(images, videos, audios)
    blocks = _generator_kinds(images, videos, audios)

    assert presentation == blocks == expected
    # And both are the shared contract's own answer, not a coincidence.
    assert presentation == [kind for kind, _media in pack_references(images, videos, audios)]
