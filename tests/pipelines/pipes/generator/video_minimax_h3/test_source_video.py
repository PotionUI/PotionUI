from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest
import torch
from PIL import Image

from src.pipelines.contracts import PipeInput
from src.pipelines.pipes._shared.generation.generator_base import GeneratorContext
from src.pipelines.pipes._shared.generation.progress import ProgressEmitter
from src.pipelines.pipes.generator.video_minimax_h3.conditioning import (
    ReferenceMedia,
    encode_source_video_condition,
    fit_source_video,
    resample_frames,
    source_canvas_size,
    source_tail_frames,
)
from src.pipelines.pipes.generator.video_minimax_h3.geometry import (
    FPS,
    audio_latent_num_frames,
    head_frames_for_latents,
    video_latent_num_frames,
)
from src.platform.runtime.native.arch.minimax_h3.packing import patchify_video_latents
from src.pipelines.pipes.generator.video_minimax_h3.main import (
    MAX_WINDOW_FRAMES,
    GeneratorMinimaxH3Pipe,
    _MiniMaxH3Ctx,
)
from src.pipelines.pipes.generator.video_minimax_h3.schedule import KEYFRAME_NOISE_AUG, scale_noise
from tests.pipelines.pipes.generator.video_minimax_h3.test_main import (
    _FakeKeyframeVae,
    _RecordingPipe,
    _fake_bundle,
    _fake_conditioning,
    _reference_image,
    _stub_prepare_text_context,
)

_MAIN = "src.pipelines.pipes.generator.video_minimax_h3.main"
PATCH = (1, 2, 2)
WINDOW_FRAMES = 124
WINDOW_LATENTS = video_latent_num_frames(WINDOW_FRAMES)
WINDOW_AUDIO_LATENTS = audio_latent_num_frames(WINDOW_FRAMES)


def _red(value):
    return (value / 255.0 - 0.485) / 0.229


def _marker_frames(count, height=32, width=32):
    markers = np.arange(count, dtype=np.uint8).reshape(count, 1, 1, 1)
    return markers.repeat(height, axis=1).repeat(width, axis=2).repeat(3, axis=3)


def test_resample_frames_drops_whole_frames_to_24_fps():
    frames = _marker_frames(30, 2, 2)
    out = resample_frames(frames, fps=30.0)
    assert out.shape[0] == 24
    assert set(out[:, 0, 0, 0].tolist()) <= set(range(30))


def test_resample_frames_rejects_a_zero_rate():
    with pytest.raises(ValueError):
        resample_frames(_marker_frames(3, 2, 2), fps=0.0)


def test_source_canvas_follows_the_clip_aspect():
    assert source_canvas_size(1920, 1080) == (768, 1344)
    assert source_canvas_size(1080, 1920) == (1344, 768)


def test_source_canvas_clamps_an_aspect_outside_one_to_four():
    assert source_canvas_size(100, 1000) == source_canvas_size(1, 4)
    assert source_canvas_size(1000, 10) == source_canvas_size(4, 1)


def test_fit_source_video_cover_crops_onto_the_canvas():
    frames = np.zeros((24, 20, 80, 3), dtype=np.uint8)
    frames[:, :, 30:50] = 255
    fitted = fit_source_video(frames, fps=24.0, height=64, width=64)
    assert fitted.shape == (24, 64, 64, 3)
    assert fitted[:, 32, 32].min() == 255


def test_source_tail_frames_takes_the_last_real_frames_then_pads():
    frames = _marker_frames(40, 2, 2)
    tail = source_tail_frames(frames, 5)
    markers = tail[:, 0, 0, 0].tolist()
    assert markers[:17] == list(range(23, 40))
    assert markers[17:] == [39] * (len(tail) - 17)


def test_source_tail_frames_refuses_a_clip_shorter_than_the_overlap():
    with pytest.raises(ValueError, match="needs 17 source frames"):
        source_tail_frames(_marker_frames(10, 2, 2), 5)


def test_encode_source_video_condition_anchors_from_the_chosen_latent():
    vae = _FakeKeyframeVae()
    generator = torch.Generator().manual_seed(3)
    anchors, rows = encode_source_video_condition(
        vae, source_tail_frames(_marker_frames(40), 5), num_latents=5, anchor_start=7, patch_size=PATCH,
        device="cpu", dtype=torch.float32, latents_mean=vae.latents_mean, latents_std=vae.latents_std,
        generator=generator,
    )
    assert anchors == (7, 8, 9, 10, 11)

    expected_generator = torch.Generator().manual_seed(3)
    frame = torch.full((1, 24, 1, 2, 2), 0.25)
    expected = torch.cat([
        patchify_video_latents(
            scale_noise(frame, KEYFRAME_NOISE_AUG, torch.randn(frame.shape, generator=expected_generator)), PATCH,
        )
        for _ in range(5)
    ])
    torch.testing.assert_close(rows, expected, rtol=0, atol=0)


@pytest.mark.parametrize("bad", [{"num_latents": 0}, {"anchor_start": -1}])
def test_encode_source_video_condition_rejects_bad_positions(bad):
    vae = _FakeKeyframeVae()
    kwargs = dict(num_latents=1, anchor_start=0, patch_size=PATCH, device="cpu", dtype=torch.float32,
                  latents_mean=vae.latents_mean, latents_std=vae.latents_std, generator=torch.Generator())
    kwargs.update(bad)
    with pytest.raises(ValueError):
        encode_source_video_condition(vae, _marker_frames(1), **kwargs)


def _run_source(role, *, source_frames, has_audio=False, waveform=None, vae=None):
    forwards = []

    def dit_forward(*, hidden_states, audio_hidden_states, **kwargs):
        forwards.append((hidden_states[0].clone(), audio_hidden_states[0].clone(), kwargs))
        return hidden_states * 0.1, audio_hidden_states * -0.2
    dit_forward.prepare_text_context = _stub_prepare_text_context

    bundle = SimpleNamespace(
        spec=SimpleNamespace(family="minimax_h3", variant="h3", sampling_settings={},
                             latent_format={"format": "minimax_h3", "latent_channels": 24}),
        dit=SimpleNamespace(compute_dtype=torch.float32, estimated_vram_gb=0.0, module=dit_forward,
                            move_to=lambda d: None, offload=lambda: None),
        video_vae=SimpleNamespace(module=vae or _FakeKeyframeVae(), compute_dtype=torch.float32,
                                  move_to=lambda d: None, offload=lambda: None),
        audio_vae=SimpleNamespace(module=object(), move_to=lambda d: None, offload=lambda: None),
        te=None, te_cache_key=None,
    )
    ctx = GeneratorContext(quantity=1, input_seeds=[7], extra=_MiniMaxH3Ctx(
        bundle=bundle, conditioning=[_fake_conditioning(3)], steps=3,
        height=32, width=32, frames=WINDOW_FRAMES, num_latent_frames=WINDOW_LATENTS,
        latent_height=2, latent_width=2, num_audio_latents=WINDOW_AUDIO_LATENTS,
        device="cpu", dtype=torch.float32, spec=bundle.spec, audio_source="generate", decode=True,
        source_role=role, source_frames=source_frames, source_path="source.mp4",
        source_has_audio=has_audio, source_overlap_latents=5 if role == "continue" else 0,
    ))
    pipe = _RecordingPipe({**GeneratorMinimaxH3Pipe.get_default_config(), "preview": False, "steps": 3})
    pipe._audio_results = []
    pipe._video_resolution = (32, 32)
    audio_encodes = []

    def fake_encode_audio(module, wave, *, sample_rate, num_condition_audio_latents, device, dtype):
        audio_encodes.append((wave.clone(), sample_rate, num_condition_audio_latents))
        return torch.full((2 * num_condition_audio_latents, 32), 0.5)

    with patch(f"{_MAIN}.encode_frames_to_mp4") as mock_mp4, \
            patch(f"{_MAIN}.encode_audio_condition", side_effect=fake_encode_audio), \
            patch(f"{_MAIN}._decode_waveform_via_ffmpeg", return_value=(waveform, 32000)):
        mock_mp4.side_effect = lambda frames, path, **kw: path
        pipe.generate_one(ctx, 0, 7, ProgressEmitter([].append, title="test"))
        written = mock_mp4.call_args_list[0].args[0]
    return pipe, forwards, written, audio_encodes


def test_continue_encodes_the_source_tail_as_the_head_condition():
    vae = _FakeKeyframeVae()
    _, forwards, _, _ = _run_source("continue", source_frames=_marker_frames(60), vae=vae)
    assert vae.encode_frame_counts == [22]
    assert vae.encoded_frame_markers[0][:17] == pytest.approx([_red(m) for m in range(43, 60)])

    generator = torch.Generator().manual_seed(7)
    frame = torch.full((1, 24, 1, 2, 2), 0.25)
    expected = torch.cat([
        patchify_video_latents(scale_noise(frame, KEYFRAME_NOISE_AUG, torch.randn(frame.shape, generator=generator)), PATCH)
        for _ in range(5)
    ])
    first_video_rows = forwards[0][0]
    torch.testing.assert_close(first_video_rows[:expected.shape[0]], expected, rtol=0, atol=0)
    assert forwards[0][2]["num_condition_video_rows"] == expected.shape[0]


def test_continue_emits_only_the_new_footage():
    pipe, _, written, _ = _run_source("continue", source_frames=_marker_frames(60))
    decoded = head_frames_for_latents(WINDOW_LATENTS)
    assert written.shape[0] == decoded - 17
    assert written[0, 0, 0, 0] == 17
    total_samples = WINDOW_AUDIO_LATENTS * 800
    assert pipe._audio_results[0].waveform.shape[1] == total_samples - round(17 / FPS * 32000)


def test_continue_carries_the_source_audio_ending_where_the_new_footage_starts():
    waveform = torch.arange(32000 * 3, dtype=torch.float32).reshape(1, -1).repeat(2, 1)
    _, forwards, _, audio_encodes = _run_source(
        "continue", source_frames=_marker_frames(72), has_audio=True, waveform=waveform,
    )
    wave, sample_rate, num_latents = audio_encodes[0]
    assert num_latents == audio_latent_num_frames(17)
    assert wave.shape[1] == round((72 - 17) / FPS * sample_rate)
    assert forwards[0][2]["num_condition_audio_rows"] == 2 * num_latents


def test_continue_without_source_audio_carries_none():
    _, forwards, _, audio_encodes = _run_source("continue", source_frames=_marker_frames(60))
    assert audio_encodes == []
    assert forwards[0][2]["num_condition_audio_rows"] == 0


def test_precede_anchors_the_source_first_frame_as_the_last_keyframe():
    vae = _FakeKeyframeVae()
    source = _marker_frames(60) + 100
    _, forwards, written, _ = _run_source("precede", source_frames=source, vae=vae)
    assert vae.encode_frame_counts == [1]
    assert vae.encoded_frame_markers[0] == pytest.approx([_red(100)])
    assert forwards[0][2]["num_condition_video_rows"] == 1
    decoded = head_frames_for_latents(WINDOW_LATENTS)
    assert written.shape[0] == decoded - 1


def _source_pipe(**config):
    return GeneratorMinimaxH3Pipe({**GeneratorMinimaxH3Pipe.get_default_config(), **config})


def _source_frames_list(count=48, size=(64, 36)):
    return [Image.new("RGB", size, color=(i, 0, 0)) for i in range(count)]


@pytest.fixture
def fake_source(monkeypatch):
    monkeypatch.setattr(f"{_MAIN}.read_video_frames", lambda path: (_source_frames_list(), 24.0))
    monkeypatch.setattr(f"{_MAIN}.has_audio_stream", lambda path: False)


def test_build_context_fits_an_auto_canvas_to_the_source(fake_source):
    pipe = _source_pipe(source_video_role="continue", resolution="auto", frames=48)
    ctx = pipe.build_context(PipeInput(input={
        "model": _fake_bundle(), "conditioning": [_fake_conditioning(3)], "source_video": ["clip.mp4"],
    }))
    c = ctx.extra
    assert (c.height, c.width) == (768, 1344)
    assert c.source_frames.shape == (48, 768, 1344, 3)
    assert c.frames >= 48 + 17
    assert c.source_overlap_latents == 5


def test_build_context_caps_an_extension_at_one_window(fake_source):
    pipe = _source_pipe(source_video_role="precede", resolution="auto", frames=600)
    ctx = pipe.build_context(PipeInput(input={
        "model": _fake_bundle(), "conditioning": [_fake_conditioning(3)], "source_video": ["clip.mp4"],
    }))
    assert ctx.extra.frames == MAX_WINDOW_FRAMES


def test_build_context_refuses_a_source_too_short_to_continue(monkeypatch):
    monkeypatch.setattr(f"{_MAIN}.read_video_frames", lambda path: (_source_frames_list(8), 24.0))
    monkeypatch.setattr(f"{_MAIN}.has_audio_stream", lambda path: False)
    with pytest.raises(ValueError, match="at least 17 frames"):
        _source_pipe(source_video_role="continue", resolution="auto").build_context(PipeInput(input={
            "model": _fake_bundle(), "conditioning": [_fake_conditioning(3)], "source_video": ["clip.mp4"],
        }))


def test_build_context_needs_a_role_for_a_connected_source(fake_source):
    with pytest.raises(ValueError, match="source_video_role"):
        _source_pipe().build_context(PipeInput(input={
            "model": _fake_bundle(), "conditioning": [_fake_conditioning(3)], "source_video": ["clip.mp4"],
        }))


def test_build_context_needs_a_source_for_a_role():
    with pytest.raises(ValueError, match="no 'source_video'"):
        _source_pipe(source_video_role="continue").build_context(PipeInput(input={
            "model": _fake_bundle(), "conditioning": [_fake_conditioning(3)],
        }))


def test_build_context_refuses_a_source_beside_references(fake_source):
    with pytest.raises(ValueError, match="mutually exclusive"):
        _source_pipe(source_video_role="continue", mode="references", references=[{"path": "a"}]).build_context(
            PipeInput(input={
                "model": _fake_bundle(), "conditioning": [_fake_conditioning(3)], "source_video": ["clip.mp4"],
                "reference_images": [_reference_image()],
            })
        )


def test_length_from_reference_video_shortens_to_the_clip():
    pipe = _source_pipe(length_from="reference_video")
    video = ReferenceMedia(kind="video", frames=_source_frames_list(100), fps=30.0)
    assert pipe._length_from_reference(343, [video]) == 73


def test_length_from_reference_audio_shortens_to_the_track():
    pipe = _source_pipe(length_from="reference_audio")
    audio = ReferenceMedia(kind="audio", audio=torch.zeros(1, 16000 * 4), sample_rate=16000)
    assert pipe._length_from_reference(343, [audio]) == 90


def test_length_from_keeps_the_configured_bound():
    pipe = _source_pipe(length_from="reference_audio")
    audio = ReferenceMedia(kind="audio", audio=torch.zeros(1, 16000 * 14), sample_rate=16000)
    assert pipe._length_from_reference(124, [audio]) == 124


def test_length_from_none_is_a_no_op():
    video = ReferenceMedia(kind="video", frames=_source_frames_list(30), fps=24.0)
    assert _source_pipe()._length_from_reference(124, [video]) == 124


def test_auto_canvas_takes_the_first_reference_video_then_image():
    video = ReferenceMedia(kind="video", frames=_source_frames_list(30, size=(36, 64)), fps=24.0)
    image = ReferenceMedia(kind="image", image=_reference_image((64, 36)))
    assert GeneratorMinimaxH3Pipe._auto_canvas(None, [image, video]) == source_canvas_size(36, 64)
    assert GeneratorMinimaxH3Pipe._auto_canvas(None, [video, image]) == source_canvas_size(36, 64)
    assert GeneratorMinimaxH3Pipe._auto_canvas(None, [image]) == source_canvas_size(64, 36)


def test_audio_source_reference_video_muxes_the_first_reference_video(monkeypatch):
    monkeypatch.setattr(f"{_MAIN}.read_video_frames", lambda path, **kw: (_source_frames_list(30), 24.0))
    pipe = _source_pipe(mode="references", audio_source="reference_video", reference_videos=[{"path": "a.mp4"}],
                        frames=22)
    ctx = pipe.build_context(PipeInput(input={
        "model": _fake_bundle(), "conditioning": [_fake_conditioning(3)], "reference_videos": ["a.mp4"],
    }))
    assert ctx.extra.audio_file == "a.mp4"


def test_audio_source_reference_video_needs_a_reference_video():
    pipe = _source_pipe(mode="references", audio_source="reference_video", references=[{"path": "a"}])
    with pytest.raises(ValueError, match="no 'reference_videos'"):
        pipe.build_context(PipeInput(input={
            "model": _fake_bundle(), "conditioning": [_fake_conditioning(3)],
            "reference_images": [_reference_image()],
        }))
