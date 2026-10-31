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
from src.pipelines.pipes.generator.video_minimax_h3.control import (
    available_frames,
    control_active,
    control_reserve_gb,
    encode_control_rows,
    fit_control_frames,
    snap_control_frames,
    validate_control_settings,
)
from src.pipelines.pipes.generator.video_minimax_h3.conditioning import ReferenceConditioning, ReferenceMedia
from src.pipelines.pipes.generator.video_minimax_h3.layout import ReferenceBlock
from src.pipelines.pipes.generator.video_minimax_h3.geometry import audio_latent_num_frames, video_latent_num_frames
from src.pipelines.pipes.generator.video_minimax_h3.main import (
    MAX_WINDOW_FRAMES,
    GeneratorMinimaxH3Pipe,
    _MiniMaxH3Ctx,
)
from src.platform.runtime.native.arch.minimax_h3.control import ControlContext
from tests.pipelines.pipes.generator.video_minimax_h3.test_main import (
    _FakeKeyframeVae,
    _RecordingPipe,
    _fake_bundle,
    _fake_conditioning,
    _stub_prepare_text_context,
)

_MAIN = "src.pipelines.pipes.generator.video_minimax_h3.main"
PATCH = (1, 2, 2)
FRAMES = 124
LATENTS = video_latent_num_frames(FRAMES)
AUDIO_LATENTS = audio_latent_num_frames(FRAMES)
COLUMNS = 196


def _frames(count, height=32, width=32):
    markers = np.arange(count, dtype=np.uint8).reshape(count, 1, 1, 1)
    return markers.repeat(height, axis=1).repeat(width, axis=2).repeat(3, axis=3)


def test_available_frames_converts_to_the_24_fps_timeline():
    assert available_frames(30, 30.0) == 24
    assert available_frames(48, 24.0) == 48


@pytest.mark.parametrize("count,fps", [(0, 24.0), (10, 0.0)])
def test_available_frames_rejects_unusable_clips(count, fps):
    with pytest.raises(ValueError):
        available_frames(count, fps)


@pytest.mark.parametrize("available,expected", [
    (3, 5), (5, 5), (6, 22), (21, 22), (22, 22), (23, 39), (120, 124), (123, 124), (124, 124), (125, 141),
    (5000, MAX_WINDOW_FRAMES),
])
def test_snap_control_frames_rounds_up_to_the_vae_lattice(available, expected):
    assert snap_control_frames(available, MAX_WINDOW_FRAMES) == expected


@pytest.mark.parametrize("seconds", [5.0, 4.0])
def test_a_whole_driver_clip_is_never_truncated_by_the_snapped_length(seconds):
    available = available_frames(round(seconds * 30), 30.0)
    assert snap_control_frames(available, MAX_WINDOW_FRAMES) >= available


def test_fit_control_frames_repeats_the_tail_of_a_short_clip_and_truncates_a_long_one():
    short = fit_control_frames(_frames(10, 8, 8), fps=24.0, height=8, width=8, num_frames=14)
    assert short[:, 0, 0, 0].tolist() == list(range(10)) + [9] * 4
    long = fit_control_frames(_frames(30, 8, 8), fps=24.0, height=8, width=8, num_frames=22)
    assert long.shape[0] == 22


def test_fit_control_frames_cover_crops_onto_the_canvas():
    wide = np.zeros((24, 20, 80, 3), dtype=np.uint8)
    wide[:, :, 30:50] = 255
    fitted = fit_control_frames(wide, fps=24.0, height=64, width=64, num_frames=24)
    assert fitted.shape == (24, 64, 64, 3)
    assert fitted[:, 32, 32].min() == 255


def test_encode_control_rows_normalises_patchifies_and_zero_pads_the_inpaint_columns():
    seen = {}

    def encoder(pixels):
        seen["pixels"] = pixels
        return torch.full((1, 24, 2, 2, 2), 3.0)

    rows = encode_control_rows(
        _frames(5, 32, 32), encoder, patch_size=PATCH, device="cpu",
        latents_mean=torch.full((24,), 1.0), latents_std=torch.full((24,), 2.0), columns=COLUMNS,
    )
    assert rows.shape == (1, 2, COLUMNS)
    assert torch.all(rows[0, :, :96] == 1.0)
    assert torch.all(rows[0, :, 96:] == 0.0)
    assert seen["pixels"].shape == (1, 3, 5, 32, 32)
    assert seen["pixels"][0, 0, 3, 0, 0].item() == pytest.approx((3 / 255.0 - 0.485) / 0.229)


def test_encode_control_rows_refuses_a_branch_narrower_than_the_rows():
    with pytest.raises(ValueError, match="columns"):
        encode_control_rows(
            _frames(5, 32, 32), lambda pixels: torch.zeros(1, 24, 2, 2, 2), patch_size=PATCH, device="cpu",
            latents_mean=torch.zeros(24), latents_std=torch.ones(24), columns=50,
        )


def test_control_window_is_a_fraction_of_the_steps():
    flags = [control_active(i, 4, 0.5, 1.0) for i in range(4)]
    assert flags == [False, False, True, True]
    assert all(control_active(i, 4, 0.0, 1.0) for i in range(4))
    assert [control_active(i, 4, 0.0, 0.5) for i in range(4)] == [True, True, False, False]


@pytest.mark.parametrize("args", [(-0.1, 0.0, 1.0, "trained"), (1.0, 0.5, 0.5, "trained"), (1.0, 0.0, 1.5, "trained"),
                                  (1.0, 0.0, 1.0, "video")])
def test_validate_control_settings_rejects_bad_values(args):
    with pytest.raises(ValueError):
        validate_control_settings(*args)


def test_the_reserve_grows_with_the_sequence():
    assert control_reserve_gb(1000, 5376) < control_reserve_gb(2000, 5376)
    assert control_reserve_gb(30000, 5376) == pytest.approx(0.9, abs=0.1)


def _bundle(*, attached=True, dit_forward=None):
    bundle = _fake_bundle()
    module = dit_forward if dit_forward is not None else SimpleNamespace()
    module.control_branch = object() if attached else None
    module.control_info = SimpleNamespace(control_in_columns=COLUMNS)
    bundle.dit.module = module
    return bundle


def _pipe(**config):
    return GeneratorMinimaxH3Pipe({**GeneratorMinimaxH3Pipe.get_default_config(), **config})


def _clip(count=48, size=(64, 36)):
    return [Image.new("RGB", size, color=(i, 0, 0)) for i in range(count)]


@pytest.fixture
def fake_clip(monkeypatch):
    monkeypatch.setattr(f"{_MAIN}.read_video_frames", lambda path: (_clip(), 24.0))


def _context(pipe, **inputs):
    return pipe.build_context(PipeInput(input={
        "model": _bundle(), "conditioning": [_fake_conditioning(3)], "control_video": ["pose.mp4"], **inputs,
    }))


def test_build_context_takes_canvas_and_length_from_the_control_video(fake_clip):
    c = _context(_pipe(resolution="auto", frames=124)).extra
    assert (c.height, c.width) == (768, 1344)
    assert c.frames == 56
    assert c.control_frames.shape == (56, 768, 1344, 3)
    assert c.control_columns == COLUMNS
    assert (c.control_scale, c.control_start, c.control_end, c.control_hint_rows) == (1.0, 0.0, 1.0, "trained")


def test_build_context_keeps_the_configured_length_when_asked(fake_clip):
    c = _context(_pipe(resolution="auto", frames=124, control_length_from_video=False)).extra
    assert c.frames == 124
    assert c.control_frames.shape[0] == 124


def test_build_context_without_a_control_video_leaves_everything_off():
    ctx = _pipe().build_context(PipeInput(input={"model": _bundle(), "conditioning": [_fake_conditioning(3)]}))
    assert ctx.extra.control_frames is None


def test_a_control_video_needs_a_control_branch_on_the_dit(fake_clip):
    with pytest.raises(ValueError, match="control_module"):
        _pipe().build_context(PipeInput(input={
            "model": _bundle(attached=False), "conditioning": [_fake_conditioning(3)], "control_video": ["pose.mp4"],
        }))


def test_a_control_video_is_refused_with_a_refine_latent(fake_clip):
    with pytest.raises(ValueError, match="cannot be combined"):
        _context(_pipe(), initial_latent=[torch.zeros(1, 24, 7, 4, 6)])


def test_a_control_video_is_refused_with_a_source_video(fake_clip):
    with pytest.raises(ValueError, match="cannot be combined"):
        _context(_pipe(source_video_role="continue"), source_video=["clip.mp4"])


@pytest.mark.parametrize("config", [{"control_context_scale": -1.0}, {"control_start": 0.8, "control_end": 0.2},
                                    {"control_hint_rows": "audio"}])
def test_build_context_validates_the_control_settings(fake_clip, config):
    with pytest.raises(ValueError):
        _context(_pipe(**config))


def test_a_control_video_works_with_a_first_frame_keyframe(fake_clip):
    c = _context(_pipe(resolution="auto"), image=[Image.new("RGB", (64, 36))]).extra
    assert c.keyframe_anchors == ("first",)
    assert c.control_frames is not None


def _run(*, steps=4, quantity=1, keyframe=False, references=False, extra_latents=0, decode=True, **config):
    forwards = []

    def dit_forward(*, hidden_states, audio_hidden_states, **kwargs):
        forwards.append(kwargs)
        return hidden_states * 0.1, audio_hidden_states * -0.2
    dit_forward.prepare_text_context = _stub_prepare_text_context

    vae = _FakeKeyframeVae()
    bundle = _bundle(dit_forward=dit_forward)
    bundle.video_vae = SimpleNamespace(
        module=vae, compute_dtype=torch.float32, move_to=lambda d: None, offload=lambda: None,
    )
    bundle.audio_vae = SimpleNamespace(module=object(), move_to=lambda d: None, offload=lambda: None)
    bundle.spec = SimpleNamespace(family="minimax_h3", variant="h3", sampling_settings={},
                                  latent_format={"format": "minimax_h3", "latent_channels": 24})
    extra = dict(
        keyframe_images=[Image.new("RGB", (32, 32))], keyframe_anchors=("first",),
    ) if keyframe else {}
    if references:
        extra["references"] = (ReferenceMedia(kind="image", image=Image.new("RGB", (8, 8))),)
    ctx = GeneratorContext(quantity=quantity, input_seeds=[7], extra=_MiniMaxH3Ctx(
        bundle=bundle, conditioning=[_fake_conditioning(3)], steps=steps,
        height=32, width=32, frames=FRAMES, num_latent_frames=LATENTS,
        latent_height=2, latent_width=2, num_audio_latents=AUDIO_LATENTS,
        device="cpu", dtype=torch.float32, spec=bundle.spec, audio_source="generate", decode=decode,
        control_frames=_frames(FRAMES), control_columns=COLUMNS,
        **{k: v for k, v in config.items() if k.startswith("control_")}, **extra,
    ))
    pipe = _RecordingPipe({**GeneratorMinimaxH3Pipe.get_default_config(), "preview": False, "steps": steps})
    pipe._audio_results = []
    pipe._video_resolution = (32, 32)

    def fake_encode(module, pixels, device, **kwargs):
        latent = module.encode(pixels)
        if extra_latents:
            latent = torch.cat([latent, latent[:, :, :extra_latents]], dim=2)
        return latent

    prepared = ReferenceConditioning(
        blocks=(ReferenceBlock(kind="image"),), condition_latents=(torch.zeros(1, 24, 1, 2, 2),),
        audio_condition_latents=(), condition_rows=torch.zeros(1, 96), condition_audio_rows=None,
    )
    with patch(f"{_MAIN}.encode_frames_to_mp4") as mock_mp4, \
            patch(f"{_MAIN}.encode_video", side_effect=fake_encode), \
            patch(f"{_MAIN}.encode_compute_dtype", return_value=torch.float32), \
            patch(f"{_MAIN}.prepare_reference_conditioning", return_value=prepared):
        mock_mp4.side_effect = lambda frames, path, **kw: path
        for index in range(quantity):
            pipe.generate_one(ctx, index, 7 + index, ProgressEmitter([].append, title="test"))
    return forwards, vae, ctx


def test_every_step_carries_the_control_rows_of_the_encoded_video():
    forwards, vae, _ = _run()
    assert len(forwards) == 4
    for kwargs in forwards:
        control = kwargs["control"]
        assert isinstance(control, ControlContext)
        assert control.rows.shape == (1, LATENTS, COLUMNS)
        assert control.scale == 1.0 and control.hint_rows == "trained"
        assert torch.all(control.rows[0, :, :96] == 0.25)
        assert torch.all(control.rows[0, :, 96:] == 0.0)
    assert vae.encode_frame_counts == [FRAMES]


def test_the_control_window_gates_which_steps_carry_hints():
    forwards, _, _ = _run(control_start=0.5, control_end=1.0)
    assert ["control" in kwargs for kwargs in forwards] == [False, False, True, True]


def test_scale_and_hint_rows_reach_the_model():
    forwards, _, _ = _run(control_scale=0.4, control_hint_rows="non_audio")
    assert all(k["control"].scale == 0.4 and k["control"].hint_rows == "non_audio" for k in forwards)


def test_the_control_video_is_encoded_once_across_a_quantity():
    forwards, vae, _ = _run(steps=2, quantity=2)
    assert len(forwards) == 4
    assert all("control" in kwargs for kwargs in forwards)
    assert vae.encode_frame_counts == [FRAMES]


def test_a_keyframe_adds_condition_rows_but_the_control_rows_stay_target_only():
    forwards, vae, _ = _run(keyframe=True)
    for kwargs in forwards:
        assert kwargs["num_condition_video_rows"] == 1
        assert kwargs["control"].rows.shape[1] == LATENTS
    assert FRAMES in vae.encode_frame_counts


def test_a_reference_adds_condition_rows_but_the_control_rows_stay_target_only():
    forwards, _, _ = _run(references=True)
    assert len(forwards) == 4
    for kwargs in forwards:
        assert kwargs["num_condition_video_rows"] == 1
        assert kwargs["control"].rows.shape[1] == LATENTS
        assert kwargs["video_indices"].numel() == LATENTS + 1


def test_the_control_cache_is_empty_after_a_successful_run():
    _, _, ctx = _run(steps=2, quantity=2)
    assert ctx.extra.control_cache == {}


def test_control_rows_that_do_not_match_the_generated_rows_are_refused():
    with pytest.raises(ValueError, match="encoded to"):
        _run(extra_latents=1)


def test_a_control_video_is_refused_with_a_director_document(fake_clip):
    from tests.pipelines.pipes.generator.video_minimax_h3.test_main import _director_document

    with pytest.raises(ValueError, match="cannot be combined"):
        _context(_pipe(document=_director_document(2)))


def test_the_control_cache_is_empty_after_a_latent_only_run():
    _, _, ctx = _run(steps=2, decode=False)
    assert ctx.extra.control_cache == {}
