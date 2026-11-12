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
from src.pipelines.pipes.generator.video_minimax_h3.conditioning import fit_keyframe_to_canvas
from src.pipelines.pipes.generator.video_minimax_h3.geometry import (
    FPS,
    FRAMES_PER_CHUNK,
    LATENTS_PER_CHUNK,
    align_num_frames,
    audio_latent_num_frames,
    video_latent_num_frames,
)
from src.pipelines.pipes.generator.video_minimax_h3.loop import (
    AUDIO_CONTEXT_LATENTS,
    PARTIAL_LATENTS,
    LoopRequestError,
    assemble_loop_latent,
    bridge_head,
    bridge_tail,
    cyclic_audio_latents,
    cyclic_decode_segments,
    cyclic_segment_frames,
    cyclic_segment_latent,
    max_loop_seconds,
    plan_loop,
)
from src.pipelines.pipes.generator.video_minimax_h3.main import (
    MAX_WINDOW_FRAMES,
    GeneratorMinimaxH3Pipe,
    _MiniMaxH3Ctx,
)
from src.pipelines.pipes.generator.video_minimax_h3.pins import (
    audio_pin,
    merge_pins,
    pinned_velocity,
    restore_pinned,
    video_pin,
)
from src.pipelines.pipes.generator.video_minimax_h3.layout import ROPE_FRAME_RESCALE, _pairwise_span_sum
from src.pipelines.pipes.generator.video_minimax_h3.schedule import data_estimate
from src.platform.runtime.native.vae.minimax_h3_video import MiniMaxH3VideoVAE
from tests.pipelines.pipes.generator.video_minimax_h3.test_main import (
    _FakeKeyframeVae,
    _RecordingPipe,
    _fake_bundle,
    _fake_conditioning,
    _stub_prepare_text_context,
)

_MAIN = "src.pipelines.pipes.generator.video_minimax_h3.main"
PATCH = (1, 2, 2)


OFFERED_SECONDS = (2, 3, 4, 5, 6, 8, 10)


@pytest.mark.parametrize("seconds, sound, loop_frames, anchor_frames, bridge_frames", [
    (0.5, False, 51, 56, 56),
    (2.0, False, 51, 56, 56),
    (3.0, False, 68, 73, 56),
    (4.0, False, 102, 107, 73),
    (5.0, False, 119, 124, 73),
    (6.0, False, 136, 141, 90),
    (8.0, False, 187, 192, 107),
    (10.0, False, 238, 243, 141),
    (14.0, False, 340, 345, 192),
    (0.5, True, 51, 56, 56),
    (3.0, True, 51, 56, 56),
    (4.0, True, 102, 107, 73),
    (8.0, True, 204, 209, 124),
    (12.75, True, 306, 311, 175),
])
def test_loop_frame_arithmetic(seconds, sound, loop_frames, anchor_frames, bridge_frames):
    plan = plan_loop(round(seconds * FPS), with_sound=sound)

    assert (plan.loop_frames, plan.anchor_frames, plan.bridge_frames) == (loop_frames, anchor_frames, bridge_frames)
    assert plan.loop_frames % FRAMES_PER_CHUNK == 0
    for frames in (plan.anchor_frames, plan.bridge_frames):
        assert align_num_frames(frames) == frames
        assert frames <= MAX_WINDOW_FRAMES
    assert plan.anchor_latents == video_latent_num_frames(plan.anchor_frames)
    assert plan.bridge_latents == video_latent_num_frames(plan.bridge_frames)
    assert plan.head_start + plan.tail_start == LATENTS_PER_CHUNK * plan.loop_chunks
    assert plan.tail_start + plan.tail_latents == plan.bridge_latents
    assert plan.bridge_chunks >= 3
    assert plan.head_chunk >= 1
    if sound:
        assert plan.loop_audio_latents * FPS == plan.loop_frames * 40
        for start, chunk in ((plan.head_audio_start, plan.head_chunk), (plan.tail_audio_start, plan.bridge_chunks - 1)):
            assert abs(start * FPS - chunk * FRAMES_PER_CHUNK * 40) * 3 <= FPS


@pytest.mark.parametrize("seconds, sound", [(s, sound) for s in OFFERED_SECONDS for sound in (False, True)])
def test_the_bridge_re_renders_the_anchor_closing_chunks_instead_of_adding_time_after_them(seconds, sound):
    plan = plan_loop(round(seconds * FPS), with_sound=sound)
    free_latents = plan.bridge_latents - plan.head_latents - plan.tail_latents

    assert plan.anchor_latents - PARTIAL_LATENTS == LATENTS_PER_CHUNK * plan.loop_chunks
    assert plan.head_start + plan.head_latents + free_latents == LATENTS_PER_CHUNK * plan.loop_chunks


def test_the_profiled_four_second_run_at_544x960_feeds_these_token_counts():
    plan = plan_loop(4 * FPS, with_sound=False)
    rows_per_frame = (960 // 16 // PATCH[1]) * (544 // 16 // PATCH[2])
    text_tokens = 19_717 - (22 + 12) * rows_per_frame

    assert rows_per_frame == 510 and text_tokens == 2_377
    assert (plan.anchor_latents + 1) * rows_per_frame + text_tokens == 19_207
    assert (plan.bridge_latents + len(plan.bridge_anchors)) * rows_per_frame + text_tokens == 19_717
    segment = cyclic_segment_latent(_marker_latent(plan.loop_chunks), 0, plan.loop_chunks)
    assert segment.shape[2] == video_latent_num_frames(124) == 37
    assert cyclic_segment_frames(0, plan.loop_chunks) == slice(17, 119)


@pytest.mark.parametrize("seconds, sound", [(15.0, False), (40.0, False), (14.0, True)])
def test_a_loop_longer_than_one_window_is_refused_in_plain_words(seconds, sound):
    with pytest.raises(LoopRequestError, match=r"at most 14\.2 seconds long \(12\.8 with sound\)"):
        plan_loop(round(seconds * FPS), with_sound=sound)
    assert max_loop_seconds(with_sound=False) == pytest.approx(340 / 24)


def test_a_loop_snaps_up_to_the_smallest_whole_loop():
    assert plan_loop(1, with_sound=False).loop_frames == 51
    assert plan_loop(1, with_sound=True).loop_frames == 51


def test_the_bridge_pins_one_chunk_at_its_head_and_a_chunk_and_a_half_at_its_tail():
    plan = plan_loop(96, with_sound=False)

    assert plan.bridge_anchors == (0, 1, 2, 3, 4, 15, 16, 17, 18, 19, 20, 21)
    assert (plan.head_start, plan.tail_start, plan.bridge_latents) == (15, 15, 22)

    sound = plan_loop(192, with_sound=True)
    assert sound.bridge_anchors[5:] == tuple(range(30, 37))
    assert (sound.head_audio_latents, sound.tail_audio_start, sound.bridge_audio_latents) == (28, 170, 207)
    assert (sound.head_audio_start, sound.tail_audio_latents, sound.loop_audio_latents) == (170, 37, 340)


def test_pinned_velocity_makes_the_clean_estimate_the_source_and_leaves_free_rows_alone():
    sample = torch.randn(6, 4)
    velocity = torch.randn(6, 4)
    values = torch.randn(2, 4)
    index = torch.tensor([1, 4])

    pinned = pinned_velocity(velocity, sample, 0.3, index, values)

    torch.testing.assert_close(data_estimate(pinned, 0.3, sample)[index], values)
    keep = torch.tensor([0, 2, 3, 5])
    assert torch.equal(pinned[keep], velocity[keep])
    assert pinned_velocity(velocity, sample, 0.3, torch.zeros(0, dtype=torch.long), values[:0]) is velocity


def test_pins_address_frame_rows_and_channel_major_audio_rows():
    latent = torch.arange(1 * 24 * 2 * 2 * 4, dtype=torch.float32).reshape(1, 24, 2, 2, 4)
    index, values = video_pin(latent, start=3, rows_per_frame=2, patch_size=PATCH)
    assert index.tolist() == [6, 7, 8, 9]
    assert values.shape == (4, 96)

    audio = torch.arange(2 * 32 * 3, dtype=torch.float32).reshape(2, 32, 3)
    audio_index, audio_values = audio_pin(audio, start=5, num_audio_latents=10)
    assert audio_index.tolist() == [5, 6, 7, 15, 16, 17]
    assert torch.equal(audio_values[3], audio[1, :, 0])
    with pytest.raises(ValueError, match="does not fit"):
        audio_pin(audio, start=8, num_audio_latents=10)

    with pytest.raises(ValueError, match="overlap"):
        merge_pins([(index, values), (index, values)], [], video_width=96, audio_width=32, device="cpu")

    rows = torch.zeros(12, 96, dtype=torch.bfloat16)
    restore_pinned(rows, 1, index, values)
    assert torch.equal(rows[7:11], values.to(torch.bfloat16))


def _marker_latent(chunks):
    frames = torch.arange(chunks * LATENTS_PER_CHUNK, dtype=torch.float32)
    return frames.view(1, 1, -1, 1, 1).expand(1, 2, -1, 1, 1).clone()


def test_cyclic_segments_carry_the_wrapped_chunk_before_and_two_latents_after():
    latent = _marker_latent(5)

    assert cyclic_decode_segments(45, segment_chunks=19) == [(0, 19), (19, 38), (38, 45)]
    first = cyclic_segment_latent(latent, 0, 2)[0, 0, :, 0, 0].tolist()
    assert first == [20, 21, 22, 23, 24, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]
    last = cyclic_segment_latent(latent, 3, 5)[0, 0, :, 0, 0].tolist()
    assert last == [10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 0, 1]
    assert cyclic_segment_frames(3, 5) == slice(17, 51)


def _chunk_local_decoder():
    def decode_clip(z):
        frames = z.repeat_interleave(4, dim=2)[:, :3]
        return frames + 0.1 * torch.cumsum(frames, dim=2) + 0.01 * frames ** 2

    vae = SimpleNamespace(
        tokens_chunk_size=5, token_drop=3, temporal_compression_ratio=4, frame_pre_padding=3, token_overlap=2,
        frame_overlap=5, clip_length=17, decode_chunk_observer=None, decode_tile_grid=lambda h, w: (1, 1),
        _decode_clip=decode_clip, _blend=MiniMaxH3VideoVAE._blend,
    )
    vae._decode_chunking = lambda frames: MiniMaxH3VideoVAE._decode_chunking(vae, frames)
    return vae


def _decode(vae, z):
    return MiniMaxH3VideoVAE._decode(vae, z)


@pytest.mark.parametrize("chunks, segment_chunks", [(2, 19), (5, 2), (7, 3)])
def test_the_delivered_loop_is_exactly_one_period_of_the_endlessly_repeated_decode(chunks, segment_chunks):
    vae = _chunk_local_decoder()
    loop = torch.randn(1, 3, chunks * LATENTS_PER_CHUNK, 2, 2, generator=torch.Generator().manual_seed(3))

    delivered = torch.cat([
        _decode(vae, cyclic_segment_latent(loop, start, end))[:, :, cyclic_segment_frames(start, end)]
        for start, end in cyclic_decode_segments(chunks, segment_chunks=segment_chunks)
    ], dim=2)
    repeated = _decode(vae, torch.cat([loop, loop, loop[:, :, :2]], dim=2))
    period = FRAMES_PER_CHUNK * chunks

    assert delivered.shape[2] == period
    assert torch.equal(delivered, repeated[:, :, period: 2 * period])


def test_cyclic_audio_pads_with_the_loop_itself_on_both_sides():
    audio = torch.arange(10, dtype=torch.float32).view(1, 1, 10)
    padded, context = cyclic_audio_latents(audio, context=3)
    assert context == 3
    assert padded[0, 0].tolist() == [7, 8, 9, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 0, 1, 2]
    assert cyclic_audio_latents(audio, context=50)[1] == 10


def _loop_bundle(calls):
    def dit_forward(**kwargs):
        calls.append({key: (value.clone() if isinstance(value, torch.Tensor) else value)
                      for key, value in kwargs.items()})
        return kwargs["hidden_states"] * 0.1, kwargs["audio_hidden_states"] * -0.2
    dit_forward.prepare_text_context = _stub_prepare_text_context
    return SimpleNamespace(
        spec=SimpleNamespace(family="minimax_h3", variant="h3", sampling_settings={},
                             latent_format={"format": "minimax_h3", "latent_channels": 24}),
        dit=SimpleNamespace(compute_dtype=torch.bfloat16, estimated_vram_gb=0.0, module=dit_forward,
                            move_to=lambda d: None, offload=lambda: None),
        video_vae=SimpleNamespace(module=_FakeKeyframeVae(), compute_dtype=torch.float32,
                                  move_to=lambda d: None, offload=lambda: None),
        audio_vae=SimpleNamespace(module=None, move_to=lambda d: None, offload=lambda: None),
        te=None, te_cache_key=None,
    )


def _run_loop(seconds=4.0, *, audio_source="none", picture=True, sampler="euler", steps=3, picture_image=None):
    plan = plan_loop(round(seconds * FPS), with_sound=audio_source == "generate")
    calls: list = []
    bundle = _loop_bundle(calls)
    ctx = GeneratorContext(quantity=1, input_seeds=[7], extra=_MiniMaxH3Ctx(
        bundle=bundle, conditioning=[_fake_conditioning(3)], steps=steps, height=32, width=32,
        frames=plan.anchor_frames, num_latent_frames=plan.anchor_latents, latent_height=2, latent_width=2,
        num_audio_latents=plan.anchor_audio_latents, device="cpu", dtype=torch.bfloat16, spec=bundle.spec,
        keyframe_images=[picture_image or Image.new("RGB", (32, 32), (10, 20, 30))] if picture else [],
        keyframe_anchors=("first",) if picture else (), audio_source=audio_source, decode=True,
        sampler=sampler, loop=plan,
    ))
    pipe = _RecordingPipe({**GeneratorMinimaxH3Pipe.get_default_config(), "preview": False, "steps": steps})
    pipe._audio_results = []
    pipe._video_resolution = (32, 32)
    windows: list = []
    original = GeneratorMinimaxH3Pipe._loop_window_latents

    def record(*args, **kwargs):
        windows.append(original(*args, **kwargs))
        return windows[-1]

    with patch.object(GeneratorMinimaxH3Pipe, "_loop_window_latents", side_effect=record), \
            patch(f"{_MAIN}.encode_frames_to_mp4") as mock_mp4:
        mock_mp4.side_effect = lambda frames, path, **kw: path
        pipe.generate_one(ctx, 0, 7, ProgressEmitter([].append, title="test"))
        written = mock_mp4.call_args_list[0]
    return SimpleNamespace(plan=plan, calls=calls, windows=windows[-2:], pipe=pipe, frames=written.args[0],
                           audio=written.kwargs.get("audio"), steps=steps, anchor_calls=calls[-2 * steps: -steps],
                           bridge_calls=calls[-steps:], opening_calls=calls[: -2 * steps])


def test_the_loop_runs_an_anchor_pass_then_a_bridge_pass_with_both_ends_conditioned():
    run = _run_loop(4.0)
    plan = run.plan

    assert len(run.calls) == 2 * run.steps
    bridge = run.bridge_calls[0]
    assert bridge["num_condition_video_rows"] == len(plan.bridge_anchors)
    position = bridge["position_ids"][:, 0]
    video = bridge["video_indices"]
    n_cv = bridge["num_condition_video_rows"]
    for row, anchor in enumerate(plan.bridge_anchors):
        assert position[video[row]] == position[video[n_cv + anchor]]
    anchor_latent = run.windows[0][0]
    _, head = video_pin(bridge_head(anchor_latent, plan), start=0, rows_per_frame=1, patch_size=PATCH)
    _, tail = video_pin(bridge_tail(anchor_latent, plan), start=0, rows_per_frame=1, patch_size=PATCH)
    condition = bridge["hidden_states"][0, :n_cv].float()
    torch.testing.assert_close(condition, torch.cat([head, tail]), atol=0.01, rtol=0.02)


def test_the_bridge_ends_are_bit_identical_to_the_anchor_and_its_middle_is_new():
    run = _run_loop(4.0, audio_source="generate")
    plan = run.plan
    (anchor, anchor_audio), (bridge, bridge_audio) = run.windows

    assert bridge.dtype == torch.bfloat16
    assert torch.equal(bridge[:, :, : plan.head_latents], anchor[:, :, plan.head_start: plan.head_start + 5])
    assert torch.equal(bridge[:, :, plan.tail_start:], anchor[:, :, : plan.tail_latents])
    assert not torch.equal(bridge[:, :, 5:10], anchor[:, :, plan.head_start: plan.head_start + 5])
    assert torch.equal(bridge_audio[..., : plan.head_audio_latents],
                       anchor_audio[..., plan.head_audio_start: plan.head_audio_start + plan.head_audio_latents])
    assert torch.equal(bridge_audio[..., plan.tail_audio_start:], anchor_audio[..., : plan.tail_audio_latents])


def _implied_noise(run, rows, source):
    implied = []
    for call in run.bridge_calls:
        n_cv = call["num_condition_video_rows"]
        target = call["video_indices"][n_cv]
        timestep = float(call["timestep"][call["timestep_indices"][target]])
        sample = call["hidden_states"][0, n_cv:][rows].float()
        implied.append((sample - timestep * source) / (1.0 - timestep))
    return implied


def test_the_pinned_rows_walk_the_source_own_noised_path_at_every_step():
    run = _run_loop(4.0, steps=4)
    plan = run.plan
    _, head = video_pin(bridge_head(run.windows[0][0], plan), start=0, rows_per_frame=1, patch_size=PATCH)

    pinned = _implied_noise(run, slice(0, 5), head)
    for noise in pinned[1:]:
        torch.testing.assert_close(noise, pinned[0], atol=0.05, rtol=0.05)
    free = _implied_noise(run, slice(5, 10), head)
    assert not torch.allclose(free[-1], free[0], atol=0.05, rtol=0.05)


def test_the_pinned_audio_rows_walk_the_source_own_noised_path_at_every_step():
    run = _run_loop(4.0, steps=4, audio_source="generate")
    plan = run.plan
    head = run.windows[0][1][0, :, plan.head_audio_start: plan.head_audio_start + plan.head_audio_latents].T.float()

    def implied(rows):
        noises = []
        for call in run.bridge_calls:
            target = call["audio_indices"][call["num_condition_audio_rows"]]
            timestep = float(call["timestep"][call["timestep_indices"][target]])
            sample = call["audio_hidden_states"][0][rows].float()
            noises.append((sample - timestep * head) / (1.0 - timestep))
        return noises

    pinned = implied(slice(0, plan.head_audio_latents))
    for noise in pinned[1:]:
        torch.testing.assert_close(noise, pinned[0], atol=0.05, rtol=0.05)
    free = implied(slice(40, 40 + plan.head_audio_latents))
    assert not torch.allclose(free[-1], free[0], atol=0.05, rtol=0.05)


def test_the_ends_are_written_back_exactly_even_when_a_step_misses_them():
    with patch(f"{_MAIN}.pinned_velocity", side_effect=lambda velocity, *args: velocity):
        run = _run_loop(4.0, audio_source="generate")
    plan = run.plan
    (anchor, anchor_audio), (bridge, bridge_audio) = run.windows

    assert torch.equal(bridge[:, :, plan.tail_start:], anchor[:, :, : plan.tail_latents])
    assert torch.equal(bridge_audio[..., plan.tail_audio_start:], anchor_audio[..., : plan.tail_audio_latents])


def test_the_join_is_by_construction_the_loop_opening():
    run = _run_loop(4.0)
    plan = run.plan
    (anchor, _), (bridge, _) = run.windows
    loop = assemble_loop_latent(anchor, bridge, plan)

    assert loop.shape[2] == LATENTS_PER_CHUNK * plan.loop_chunks
    assert torch.equal(bridge[:, :, plan.tail_start: plan.tail_start + 5], loop[:, :, :5])
    assert torch.equal(bridge[:, :, plan.tail_start + 5:], loop[:, :, 5:7])
    assert torch.equal(bridge[:, :, :5], anchor[:, :, plan.head_start: plan.head_start + 5])
    decoded = run.pipe.decoded_latents
    assert len(decoded) == 1
    assert torch.equal(decoded[-1], torch.cat([loop[:, :, -5:], loop, loop[:, :, :2]], dim=2))
    assert run.frames.shape[0] == plan.loop_frames


@pytest.mark.parametrize("seconds, sound", [(s, sound) for s in OFFERED_SECONDS for sound in ("none", "generate")])
def test_every_offered_length_runs_the_planned_windows_and_keeps_no_repeated_latent(seconds, sound):
    run = _run_loop(float(seconds), audio_source=sound, steps=2)
    plan = run.plan
    (anchor, _), (bridge, _) = run.windows
    loop = assemble_loop_latent(anchor, bridge, plan)
    kept = plan.head_start + plan.head_latents

    assert run.anchor_calls[0]["hidden_states"].shape[1] == plan.anchor_latents + 1
    assert run.bridge_calls[0]["hidden_states"].shape[1] == plan.bridge_latents + len(plan.bridge_anchors)
    assert anchor.shape[2] - PARTIAL_LATENTS == loop.shape[2] == LATENTS_PER_CHUNK * plan.loop_chunks
    assert torch.equal(loop[:, :, :kept], anchor[:, :, :kept])
    assert torch.equal(loop[:, :, kept:], bridge[:, :, plan.head_latents: plan.tail_start])
    frames = loop[0].transpose(0, 1).flatten(1).float()
    distances = torch.cdist(frames, frames) + torch.eye(frames.shape[0]) * 1e9
    assert distances.min() > 0
    assert torch.equal(run.pipe.decoded_latents[-1], torch.cat([loop[:, :, -5:], loop, loop[:, :, :2]], dim=2))
    assert run.frames.shape[0] == plan.loop_frames


def test_sound_off_writes_no_soundtrack_and_sound_on_wraps_an_exact_loop_of_audio():
    silent = _run_loop(4.0, audio_source="none")
    assert silent.audio is None
    assert silent.pipe.audio_calls == []

    loud = _run_loop(4.0, audio_source="generate")
    samples = loud.audio.waveform.shape[-1]
    assert samples == round(loud.plan.loop_frames / FPS * 32000)
    rows, latents, _ = loud.pipe.audio_calls[0]
    assert latents == loud.plan.loop_audio_latents + 2 * min(AUDIO_CONTEXT_LATENTS, loud.plan.loop_audio_latents)


def _anchor_keyframe_times(call, plan):
    position = call["position_ids"][:, 0]
    video = call["video_indices"]
    n_cv = call["num_condition_video_rows"]
    first = float(position[video[n_cv]])
    last = first + _pairwise_span_sum(plan.anchor_latents) - ROPE_FRAME_RESCALE
    return [float(position[video[row]]) for row in range(n_cv)], first, last


def test_the_anchor_pass_holds_the_start_picture_at_its_first_frame_only():
    run = _run_loop(4.0)
    plan = run.plan

    assert run.opening_calls == []
    times, first, _ = _anchor_keyframe_times(run.anchor_calls[0], plan)
    assert times == [first]
    assert run.bridge_calls[0]["num_condition_video_rows"] == len(plan.bridge_anchors)


def test_the_start_picture_is_fitted_once_keeping_its_shape():
    from src.pipelines.pipes.generator.video_minimax_h3 import main as h3_main

    original = h3_main.prepare_keyframe_condition_rows
    seen: list = []

    def record(images, anchors, **kwargs):
        seen.append([
            np.asarray(fit_keyframe_to_canvas(image, kwargs["height"], kwargs["width"], is_geometry_anchor=index == 0))
            for index, image in enumerate(images)
        ])
        return original(images, anchors, **kwargs)

    gradient = np.tile(np.arange(48, dtype=np.uint8)[:, None, None] * 5, (1, 32, 3))
    with patch(f"{_MAIN}.prepare_keyframe_condition_rows", side_effect=record):
        _run_loop(4.0, picture_image=Image.fromarray(gradient))

    (first,) = seen[-1]
    cropped = np.asarray(fit_keyframe_to_canvas(Image.fromarray(gradient), 32, 32, is_geometry_anchor=False))
    assert np.array_equal(first, cropped)


def test_a_loop_without_a_picture_runs_two_passes_from_the_prompt_alone():
    run = _run_loop(4.0, picture=False)
    plan = run.plan

    assert len(run.calls) == 2 * run.steps
    assert run.anchor_calls[0]["num_condition_video_rows"] == 0
    (anchor, _), (bridge, _) = run.windows
    assert torch.equal(bridge[:, :, plan.tail_start:], anchor[:, :, : plan.tail_latents])


def test_the_loop_runs_with_the_flex_sampler():
    run = _run_loop(2.0, sampler="lcm")
    plan = run.plan

    assert run.anchor_calls[0]["num_condition_video_rows"] == 1
    (anchor, _), (bridge, _) = run.windows
    assert torch.equal(bridge[:, :, plan.tail_start:], anchor[:, :, : plan.tail_latents])
    assert run.bridge_calls[0]["num_condition_video_rows"] == len(plan.bridge_anchors)


def _loop_pipe(**config):
    return GeneratorMinimaxH3Pipe({**GeneratorMinimaxH3Pipe.get_default_config(), "loop": True, **config})


def _context(pipe, **inputs):
    return pipe.build_context(PipeInput(input={
        "model": _fake_bundle(), "conditioning": [_fake_conditioning(3)], **inputs,
    }))


def test_build_context_plans_the_loop_and_sizes_the_anchor_window():
    ctx = _context(_loop_pipe(frames=96, resolution="1024x576", audio_source="none"))
    c = ctx.extra

    assert c.loop == plan_loop(96, with_sound=False)
    assert (c.frames, c.num_latent_frames) == (c.loop.anchor_frames, c.loop.anchor_latents)
    assert (c.height, c.width) == (576, 1024)


def test_build_context_keeps_the_picked_resolution_whatever_the_start_picture_shape():
    ctx = _context(_loop_pipe(frames=96, resolution="1344x768"), image=[Image.new("RGB", (1080, 1920))])

    assert (ctx.extra.height, ctx.extra.width) == (768, 1344)
    assert ctx.extra.keyframe_anchors == ("first",)


def test_build_context_refuses_a_loop_longer_than_one_window():
    with pytest.raises(LoopRequestError, match="at most 14.2 seconds"):
        _context(_loop_pipe(frames=30 * FPS, audio_source="none"))


@pytest.mark.parametrize("config, inputs, match", [
    ({"decode": False}, {}, "has to decode"),
    ({"audio_source": "file"}, {"audio": ["a.wav"]}, "its own sound or none"),
    ({"keyframe_anchors": ["last"]}, {"image": [Image.new("RGB", (32, 32))]}, "first frame"),
    ({}, {"image": [Image.new("RGB", (32, 32)), Image.new("RGB", (32, 32))]}, "at most one start picture"),
    ({"document": {"mode": "director"}}, {}, "prompt and an optional start picture"),
])
def test_build_context_refuses_what_a_loop_cannot_combine_with(config, inputs, match):
    with pytest.raises(ValueError, match=match):
        _context(_loop_pipe(frames=96, **config), **inputs)


def test_audio_source_none_is_a_valid_choice_for_every_generation():
    GeneratorMinimaxH3Pipe.validate_config({**GeneratorMinimaxH3Pipe.get_default_config(), "audio_source": "none"})
    assert audio_latent_num_frames(FRAMES_PER_CHUNK * 3) * FPS == FRAMES_PER_CHUNK * 3 * 40
