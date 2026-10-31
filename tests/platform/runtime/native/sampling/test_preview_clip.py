from __future__ import annotations

import io
import threading
from types import SimpleNamespace

import numpy as np
import torch
from PIL import Image

from src.platform.runtime.native.sampling import preview as preview_module
from src.platform.runtime.native.sampling import preview_clip as pc
from src.platform.runtime.native.sampling.preview import make_preview_hook

WAN_SPEC = SimpleNamespace(latent_format={"latent_channels": 16, "format": "wan21"}, variant="wan_14b")


def noise_frames(count, height=72, width=128, seed=0):
    rng = np.random.default_rng(seed)
    return [rng.integers(0, 256, (height, width, 3), dtype=np.uint8) for _ in range(count)]


def flat_frames(count, height=72, width=128):
    return [np.full((height, width, 3), 40 + i, dtype=np.uint8) for i in range(count)]


def test_cadence_is_every_three_steps_for_short_runs_and_an_eighth_for_long_ones():
    assert pc.clip_cadence(10) == 3
    assert pc.clip_cadence(24) == 3
    assert pc.clip_cadence(40) == 5
    assert pc.clip_cadence(80) == 10


def test_schedule_fires_on_the_cadence_and_never_more_than_eight_times():
    schedule = pc.ClipSchedule()
    schedule.start(200)
    fired = []
    for step in range(200):
        if schedule.due(step):
            schedule.mark_emitted()
            fired.append(step)
    assert len(fired) == pc.CLIP_MAX_UPDATES
    assert fired[:2] == [24, 49]


def test_schedule_on_a_short_run_stays_under_the_cap():
    schedule = pc.ClipSchedule()
    schedule.start(20)
    fired = [step for step in range(20) if schedule.due(step) and not schedule.mark_emitted()]
    assert fired == [2, 5, 8, 11, 14, 17]


def test_schedule_cap_spans_restarts_of_the_sampler():
    schedule = pc.ClipSchedule(max_updates=3)
    for total in (6, 6):
        schedule.start(total)
        for step in range(total):
            if schedule.due(step):
                schedule.mark_emitted()
    assert schedule.emitted == 3


def test_sprite_layout_and_metadata():
    frames = noise_frames(25)
    clip = pc.build_sprite_clip(frames, list(range(0, 100, 4)), step=15, total_steps=40)
    assert (clip.frames, clip.columns) == (25, 5)
    assert (clip.frame_width, clip.frame_height) == (128, 72)
    assert clip.sprite.size == (128 * 5, 72 * 5)
    assert clip.fps == 8 and clip.step == 15 and clip.total_steps == 40
    assert clip.source_indices[:3] == [0, 4, 8]
    decoded = Image.open(io.BytesIO(clip.encoded))
    assert decoded.format == "WEBP" and decoded.size == clip.sprite.size


def test_frames_beyond_the_cap_are_dropped():
    clip = pc.build_sprite_clip(noise_frames(30), list(range(30)), step=1, total_steps=10)
    assert clip.frames == 25 and len(clip.source_indices) == 25


def test_a_clip_under_the_byte_cap_keeps_the_first_tier():
    clip = pc.build_sprite_clip(flat_frames(25, 216, 384), list(range(25)), step=1, total_steps=10)
    assert clip.long_edge == 384
    assert clip.frame_width == 384


def test_a_clip_over_the_byte_cap_drops_a_resolution_tier():
    frames = noise_frames(25, 216, 384)
    full = pc.build_sprite_clip(frames, list(range(25)), step=1, total_steps=10, byte_cap=10**9)
    cap = len(full.encoded) - 1
    clip = pc.build_sprite_clip(frames, list(range(25)), step=1, total_steps=10, byte_cap=cap)
    assert full.long_edge == 384
    assert clip.long_edge < 384
    assert clip.frame_width < 384
    assert len(clip.encoded) <= cap or clip.long_edge == pc.CLIP_TIERS[-1]


def test_the_default_cap_is_300_kb_even_for_incompressible_frames():
    clip = pc.build_sprite_clip(noise_frames(25, 216, 384), list(range(25)), step=1, total_steps=10)
    assert len(clip.encoded) <= 300 * 1024
    assert clip.long_edge < 384


def test_a_newer_clip_replaces_a_queued_one_while_one_is_in_flight():
    gate = threading.Event()
    started = threading.Event()
    emitted = []

    def emit(clip):
        started.set()
        gate.wait(5)
        emitted.append(clip.step)

    relay = pc.ClipRelay(emit)
    kwargs = dict(total_steps=30, update_index=1, every=3)
    relay.submit(flat_frames(3), [0, 1, 2], step=3, **kwargs)
    assert started.wait(5)
    relay.submit(flat_frames(3), [0, 1, 2], step=6, **kwargs)
    relay.submit(flat_frames(3), [0, 1, 2], step=9, **kwargs)
    gate.set()
    relay.drain()
    assert emitted == [3, 9]
    assert relay.dropped == 1


def test_the_relay_survives_an_emit_failure():
    calls = []

    def emit(clip):
        calls.append(clip.step)
        if clip.step == 3:
            raise RuntimeError("socket gone")

    relay = pc.ClipRelay(emit)
    relay.submit(flat_frames(2), [0, 1], step=3, total_steps=9, update_index=1, every=3)
    relay.drain()
    relay.submit(flat_frames(2), [0, 1], step=6, total_steps=9, update_index=2, every=3)
    relay.drain()
    assert calls == [3, 6]


class FakeDecoder:
    latent_channels = 16


def video_latent(tokens=4):
    return torch.zeros(1, 16, tokens, 4, 4)


def install_fake_decoder(monkeypatch, frame_count=25):
    monkeypatch.setattr(preview_module, "_resolve_decoder", lambda spec, path: FakeDecoder())
    monkeypatch.setattr(
        preview_module,
        "decode_preview_clip",
        lambda decoder, x0, **kw: (flat_frames(frame_count), list(range(frame_count))),
    )


def run_hook(hook, total, latent):
    hook.on_start(total)
    for step in range(total):
        hook.on_step(step, total, None, None, latent)
        if hasattr(hook, "_relay"):
            hook._relay.drain()
    hook.on_end()


def test_hook_emits_clips_on_the_schedule_when_a_decoder_and_clip_sink_are_set(monkeypatch):
    install_fake_decoder(monkeypatch)
    singles, clips = [], []
    monkeypatch.setattr(
        preview_module, "decode_middle_frame", lambda decoder, x0, max_size: torch.zeros(3, 8, 8)
    )
    hook = make_preview_hook(
        WAN_SPEC, singles.append, decoder_path="d.safetensors", emit_clip=clips.append,
    )
    run_hook(hook, 20, video_latent())
    assert len(singles) == 1
    assert [c.step for c in clips] == [3, 6, 9, 12, 15, 18]
    assert [c.update_index for c in clips] == [1, 2, 3, 4, 5, 6]
    assert all(c.frames == 25 and c.every == 3 and c.updates_max == 8 for c in clips)


def test_hook_never_exceeds_eight_updates(monkeypatch):
    install_fake_decoder(monkeypatch)
    clips = []
    hook = make_preview_hook(WAN_SPEC, lambda image: None, decoder_path="d", emit_clip=clips.append)
    run_hook(hook, 120, video_latent())
    assert len(clips) == 8


def test_without_a_decoder_file_the_single_frame_preview_stays(monkeypatch):
    singles, clips = [], []
    hook = make_preview_hook(WAN_SPEC, singles.append, emit_clip=clips.append)
    run_hook(hook, 10, video_latent())
    assert clips == []
    assert singles


def test_without_a_clip_sink_the_decoder_keeps_the_single_frame_path(monkeypatch):
    monkeypatch.setattr(preview_module, "_resolve_decoder", lambda spec, path: FakeDecoder())
    monkeypatch.setattr(
        preview_module, "decode_middle_frame", lambda decoder, x0, max_size: torch.zeros(3, 8, 8)
    )
    singles = []
    hook = make_preview_hook(WAN_SPEC, singles.append, decoder_path="d")
    run_hook(hook, 10, video_latent())
    assert singles


def test_an_image_latent_with_a_decoder_stays_on_the_single_frame_path(monkeypatch):
    monkeypatch.setattr(preview_module, "_resolve_decoder", lambda spec, path: FakeDecoder())
    monkeypatch.setattr(
        preview_module, "decode_middle_frame", lambda decoder, x0, max_size: torch.zeros(3, 8, 8)
    )
    singles, clips = [], []
    hook = make_preview_hook(WAN_SPEC, singles.append, decoder_path="d", emit_clip=clips.append)
    run_hook(hook, 10, torch.zeros(1, 16, 1, 4, 4))
    assert clips == []
    assert singles


def test_a_one_frame_decode_emits_no_clip(monkeypatch):
    install_fake_decoder(monkeypatch, frame_count=1)
    clips = []
    hook = make_preview_hook(WAN_SPEC, lambda image: None, decoder_path="d", emit_clip=clips.append)
    run_hook(hook, 10, video_latent())
    assert clips == []


def test_single_frames_fill_the_wait_for_the_first_clip_and_stop_after_it(monkeypatch):
    install_fake_decoder(monkeypatch)
    monkeypatch.setattr(
        preview_module, "decode_middle_frame", lambda decoder, x0, max_size: torch.zeros(3, 8, 8)
    )
    order = []
    hook = make_preview_hook(
        WAN_SPEC, lambda image: order.append("single"), decoder_path="d", emit_clip=lambda c: order.append("clip"),
    )
    run_hook(hook, 40, video_latent())
    assert order[0] == "single"
    first_clip = order.index("clip")
    assert "single" not in order[first_clip:]
    assert order.count("clip") == 8
