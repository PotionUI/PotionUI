from __future__ import annotations

import contextlib
from unittest.mock import patch

import numpy as np
import pytest
import torch
from PIL import Image

from tests.pipelines.pipes.generator.chain_video_wan22 import test_chain_video_generator as chain
from tests.pipelines.pipes.generator.img2vid_wan22 import test_wan_i2v_generator as i2v
from tests.pipelines.pipes.generator.qwen import test_qwen_generator as qwen
from tests.pipelines.pipes.generator.txt2vid_ltx import test_ltx_txt2vid_generator as ltx_t2v
from tests.pipelines.pipes.generator.txt2vid_wan22 import test_wan_txt2vid_generator as wan_t2v
from tests.pipelines.pipes.generator.video_ltx import test_video_ltx_generator as ltx_video

DECODER = "/models/vae_approx/taew2_1.safetensors"
SHARED_HOOK = "src.platform.runtime.native.sampling.make_preview_hook"


def _wan_t2v(config):
    patches = [
        patch("src.pipelines.pipes.generator.txt2vid_wan22.main.encode_frames_to_mp4", lambda frames, path, fps: path),
        patch("src.pipelines.pipes.generator.txt2vid_wan22.main.denoise", lambda mf, latents, c, u, **k: latents),
    ]
    with contextlib.ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        wan_t2v._pipe(device="cpu", resolution="64x64", frames=5, **config).process(wan_t2v._pipe_input(), lambda o: None)


def _wan_i2v(config):
    patches = [
        patch("src.pipelines.pipes.generator.img2vid_wan22.main.encode_frames_to_mp4", lambda frames, path, fps: path),
        patch("src.pipelines.pipes.generator.img2vid_wan22.main.denoise", lambda mf, latents, c, u, **k: latents),
    ]
    with contextlib.ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        i2v._pipe(device="cpu", resolution="512x256", frames=9, **config).process(i2v._pipe_input(), lambda o: None)


def _wan_chain(config):
    pipe = chain._pipe(document=chain._document(n_segments=1, start_on_seg0=False), **config)
    inputs = chain._inputs(
        model=chain._bundle(in_dim=36, variant="wan_i2v_14b"),
        model_t2v=chain._bundle(in_dim=16, dual=False, variant="wan22_t2v"),
        conditioning=chain._cond(1),
    )
    p1, p2, p3, p4 = chain._patches()
    with p1, p2, p3, p4, chain._no_stitch():
        pipe.process(inputs, lambda o: None)


def _ltx_t2v(config):
    patches = [
        patch("src.pipelines.pipes.generator.txt2vid_ltx.main.encode_frames_to_mp4", lambda frames, path, fps, audio=None: path),
        patch("src.pipelines.pipes.generator.txt2vid_ltx.main.denoise", lambda mf, latents, c, u, **k: latents),
    ]
    with contextlib.ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        ltx_t2v._pipe(device="cpu", **config).process(ltx_t2v._pipe_input(), lambda o: None)


def _ltx_video(config):
    main = "src.pipelines.pipes.generator.video_ltx.main"
    patches = [
        patch(f"{main}.denoise_prenoised", lambda fwd, x, c, u, **k: x),
        patch(f"{main}.decode_ltx_video", lambda *a, **k: np.zeros((9, 4, 4, 3), dtype=np.uint8)),
        patch(f"{main}.encode_frames_to_mp4", lambda frames, path, fps, audio=None: None),
        patch(f"{main}.restore_dit_best_effort", lambda *a: None),
    ]
    with contextlib.ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        ltx_video._pipe(**config).process(ltx_video._pipe_input(), lambda o: None)


def _qwen(mode):
    def run(config):
        patches = [
            patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None),
            patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.NativeGenerator", qwen._FakeGenerator),
        ]
        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            pipe_input = qwen._pipe_input()
            if mode != "txt2img":
                pipe_input.input["image"] = [Image.new("RGB", (64, 64), color=(10, 20, 30))]
            qwen._make_pipe(mode=mode, **config).process(pipe_input, lambda o: None)

    return run


CASES = [
    pytest.param(_wan_t2v, "src.pipelines.pipes.generator.txt2vid_wan22.main.make_preview_hook", id="txt2vid_wan22"),
    pytest.param(_wan_i2v, "src.pipelines.pipes.generator.img2vid_wan22.main.make_preview_hook", id="img2vid_wan22"),
    pytest.param(_wan_chain, "src.pipelines.pipes.generator.chain_video_wan22.main.make_preview_hook", id="chain_video_wan22"),
    pytest.param(_ltx_t2v, "src.pipelines.pipes.generator.txt2vid_ltx.main.make_preview_hook", id="txt2vid_ltx"),
    pytest.param(_ltx_video, "src.pipelines.pipes.generator.video_ltx.main.make_preview_hook", id="video_ltx"),
    pytest.param(_qwen("txt2img"), SHARED_HOOK, id="qwen_flow_generator"),
    pytest.param(_qwen("img2img"), SHARED_HOOK, id="qwen_img2img"),
    pytest.param(_qwen("edit"), SHARED_HOOK, id="qwen_edit"),
]


@pytest.fixture(autouse=True)
def _reset_fake_generators():
    qwen._FakeGenerator.instances.clear()
    yield
    qwen._FakeGenerator.instances.clear()


@pytest.mark.parametrize("run,target", CASES)
def test_the_configured_preview_decoder_reaches_the_preview_hook(run, target):
    seen = []
    with patch(target, lambda spec, emit, **kwargs: seen.append(kwargs)):
        run({"preview_decoder": DECODER})
    assert seen
    assert all(kwargs["decoder_path"] == DECODER for kwargs in seen)


@pytest.mark.parametrize("run,target", CASES)
@pytest.mark.parametrize("value", [{}, {"preview_decoder": ""}])
def test_an_empty_preview_decoder_reaches_the_preview_hook_as_none(run, target, value):
    seen = []
    with patch(target, lambda spec, emit, **kwargs: seen.append(kwargs)):
        run(value)
    assert seen
    assert all(kwargs["decoder_path"] is None for kwargs in seen)
