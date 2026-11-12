from __future__ import annotations

import pytest
import torch

from src.pipelines.pipes._shared.video.frames import hold_frames, to_frames_tensor

from .test_video_ltx_generator import _pil_image, _pipe, _pipe_input

REFERENCE = {"source": "image", "index": 0, "frame": "first", "strength": 1.0, "role": "reference"}


def test_hold_frames_repeats_the_first_frame_without_copying():
    frames = torch.rand(1, 8, 8, 3)
    held = hold_frames(frames, 121)

    assert held.shape == (121, 8, 8, 3)
    assert torch.equal(held[0], held[120])
    assert torch.equal(held[60], frames[0])
    assert held.data_ptr() == frames.data_ptr()


def test_hold_frames_keeps_only_the_first_of_several():
    frames = torch.stack([torch.zeros(4, 4, 3), torch.ones(4, 4, 3)])

    assert hold_frames(frames, 3).sum().item() == 0


def build(placements, frames=17):
    pipe = _pipe(resolution="64x64", frames=frames, media_placements=placements)
    return pipe.build_context(_pipe_input(images=[_pil_image()])).extra


def test_a_held_reference_spans_every_latent_frame_of_the_clip():
    c = build([{**REFERENCE, "hold": True}], frames=17)
    still = build([REFERENCE], frames=17)

    assert still.prepared.n_extra == 2 * 2
    assert c.prepared.n_extra == 3 * 2 * 2
    assert c.prepared.base_tokens == still.prepared.base_tokens
    assert c.prepared.extra_coords.shape == (1, 3, 12, 2)


def test_the_held_reference_covers_the_clip_in_time():
    c = build([{**REFERENCE, "hold": True}], frames=17)
    times = c.prepared.extra_coords[0, 0, :, 0]

    assert times.min().item() == 0
    assert torch.unique(times).numel() == 3


def test_the_held_reference_is_all_at_full_strength():
    c = build([{**REFERENCE, "hold": True, "strength": 0.5}], frames=17)

    assert torch.allclose(c.prepared.mask[:, c.prepared.base_tokens:], torch.full((1, 12), 0.5))


@pytest.mark.parametrize("frames", [9, 17, 25, 121])
def test_the_hold_follows_the_clip_length(frames):
    c = build([{**REFERENCE, "hold": True}], frames=frames)

    assert c.prepared.n_extra == ((frames - 1) // 8 + 1) * 2 * 2


def test_hold_is_refused_on_a_keyframe():
    with pytest.raises(ValueError, match="'hold' media placement must have role 'reference'"):
        build([{"source": "image", "index": 0, "frame": "first", "strength": 1.0, "role": "keyframe", "hold": True}])


@pytest.mark.parametrize("placement", [
    REFERENCE,
    {**REFERENCE, "hold": False},
    {**REFERENCE, "hold": "false"},
    {"source": "image", "index": 0, "frame": "first", "strength": 1.0},
    {"source": "image", "index": 0, "frame": "last", "strength": 1.0, "role": "keyframe"},
])
def test_placements_without_hold_build_exactly_the_conditions_they_did_before(placement):
    pipe = _pipe(resolution="64x64", frames=17)
    image = _pil_image()
    conditions = pipe._build_conditions([placement], [image], [], 17)

    assert len(conditions) == 1
    assert conditions[0].frames.shape[0] == 1
    assert torch.equal(conditions[0].frames, to_frames_tensor(image))
    assert conditions[0].role == placement.get("role", "keyframe")


def test_an_unheld_reference_still_encodes_one_latent_frame():
    c = build([REFERENCE], frames=17)

    assert c.prepared.n_extra == 4
    assert c.prepared.extra_coords.shape == (1, 3, 4, 2)
