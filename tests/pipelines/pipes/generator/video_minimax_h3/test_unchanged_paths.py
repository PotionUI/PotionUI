from __future__ import annotations

import hashlib
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest
import torch
from PIL import Image

from src.pipelines.pipes._shared.generation.generator_base import GeneratorContext
from src.pipelines.pipes._shared.generation.progress import ProgressEmitter
from src.pipelines.pipes.generator.video_minimax_h3.geometry import audio_latent_num_frames, video_latent_num_frames
from src.pipelines.pipes.generator.video_minimax_h3.main import GeneratorMinimaxH3Pipe, _MiniMaxH3Ctx
from src.pipelines.pipes.generator.video_minimax_h3.windows import build_director_plan
from tests.pipelines.pipes.generator.video_minimax_h3.test_main import (
    _FakeKeyframeVae,
    _RecordingPipe,
    _director_document,
    _fake_conditioning,
    _stub_prepare_text_context,
)

_MAIN = "src.pipelines.pipes.generator.video_minimax_h3.main"
FRAMES = 39
LATENTS = video_latent_num_frames(FRAMES)
AUDIO_LATENTS = audio_latent_num_frames(FRAMES)
_SCALARS = (torch.Tensor, int, float, str, bool, type(None))

EXPECTED = {
    "t2va": "cb107c3c5c5e27b1272ead30050f066374a0bf65a5ae0ad80a293c22ada743f0",
    "t2va_lcm": "7bfd7105339382215e3e53e8fe152158a8b781ef88cc4aa384ab4568285a5213",
    "fl2va_first": "8aacc0665fad7f179bd380cd33ada84c10130f6bc90a9a1a9f85c4e2e43d7e60",
    "continue": "938d17f32a224f740b1530928d9fd54e094cad531cec9b21a1bcc486dc86348b",
    "director_chain": "60460c76dcdc923ec64abf726225567e27f0eae075488eca4860e56989af4939",
}


def _update(digest, value):
    if isinstance(value, torch.Tensor):
        digest.update(f"{value.dtype}{tuple(value.shape)}".encode())
        digest.update(value.detach().cpu().contiguous().numpy().tobytes())
    elif isinstance(value, np.ndarray):
        digest.update(f"{value.dtype}{value.shape}".encode())
        digest.update(np.ascontiguousarray(value).tobytes())
    elif isinstance(value, (list, tuple)):
        for item in value:
            _update(digest, item)
    else:
        digest.update(repr(value).encode())


def _digest(*parts):
    digest = hashlib.sha256()
    for part in parts:
        _update(digest, part)
    return digest.hexdigest()


def _recording_bundle(calls):
    def dit_forward(**kwargs):
        calls.append([
            (key, value.clone() if isinstance(value, torch.Tensor) else value)
            for key, value in sorted(kwargs.items()) if isinstance(value, _SCALARS)
        ])
        return kwargs["hidden_states"] * 0.1, kwargs["audio_hidden_states"] * -0.2
    dit_forward.prepare_text_context = _stub_prepare_text_context
    return SimpleNamespace(
        spec=SimpleNamespace(family="minimax_h3", variant="h3", sampling_settings={},
                             latent_format={"format": "minimax_h3", "latent_channels": 24}),
        dit=SimpleNamespace(compute_dtype=torch.float32, estimated_vram_gb=0.0, module=dit_forward,
                            move_to=lambda d: None, offload=lambda: None),
        video_vae=SimpleNamespace(module=_FakeKeyframeVae(), compute_dtype=torch.float32,
                                  move_to=lambda d: None, offload=lambda: None),
        audio_vae=SimpleNamespace(module=None, move_to=lambda d: None, offload=lambda: None),
        te=None, te_cache_key=None,
    )


def _marker_frames(count):
    markers = np.arange(count, dtype=np.uint8).reshape(count, 1, 1, 1)
    return markers.repeat(32, axis=1).repeat(32, axis=2).repeat(3, axis=3)


def _run(*, plan=None, sampler="euler", decode=False, **extra):
    calls: list = []
    bundle = _recording_bundle(calls)
    windows = len(plan.windows) if plan is not None else 1
    ctx = GeneratorContext(quantity=1, input_seeds=[7], extra=_MiniMaxH3Ctx(
        bundle=bundle, conditioning=[_fake_conditioning(3) for _ in range(windows)], steps=3,
        height=32, width=32, frames=FRAMES, num_latent_frames=LATENTS, latent_height=2, latent_width=2,
        num_audio_latents=AUDIO_LATENTS, device="cpu", dtype=torch.float32, spec=bundle.spec,
        audio_source="generate", decode=decode, sampler=sampler, plan=plan, **extra,
    ))
    pipe = _RecordingPipe({**GeneratorMinimaxH3Pipe.get_default_config(), "preview": False, "steps": 3})
    pipe._audio_results = []
    pipe._video_resolution = (32, 32)
    with patch(f"{_MAIN}.encode_frames_to_mp4") as mock_mp4:
        mock_mp4.side_effect = lambda frames, path, **kw: path
        result = pipe.generate_one(ctx, 0, 7, ProgressEmitter([].append, title="test"))
        written = [call.args[0] for call in mock_mp4.call_args_list]
    outputs = [result] if isinstance(result, torch.Tensor) else []
    return _digest(calls, outputs, pipe.decoded_latents, [call[0] for call in pipe.audio_calls], written)


def _cases():
    return {
        "t2va": lambda: _run(),
        "t2va_lcm": lambda: _run(sampler="lcm"),
        "fl2va_first": lambda: _run(
            keyframe_images=[Image.new("RGB", (32, 32), (10, 20, 30))], keyframe_anchors=("first",),
        ),
        "continue": lambda: _run(
            decode=True, source_role="continue", source_frames=_marker_frames(60), source_path="source.mp4",
            source_overlap_latents=5,
        ),
        "director_chain": lambda: _run(
            decode=True, plan=build_director_plan(_director_document(2, frames=FRAMES), default_seed=-1),
        ),
    }


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_existing_paths_feed_the_dit_and_emit_exactly_what_they_did_before_loops(name):
    assert _cases()[name]() == EXPECTED[name]
