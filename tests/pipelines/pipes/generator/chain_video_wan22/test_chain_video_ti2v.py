from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import List
from unittest.mock import patch

import numpy as np
import pytest
import torch

from src.pipelines.contracts import PipeInput
from src.pipelines.pipes._shared.generation.wan_ti2v import TI2VForward
from src.pipelines.pipes.generator.chain_video_wan22.main import GeneratorWanChainVideoPipe, _ChainForward
from src.platform.runtime.native.vae.causal_3d_v2 import LATENTS_MEAN as WAN22_LATENTS_MEAN, LATENTS_STD as WAN22_LATENTS_STD

_MAIN = "src.pipelines.pipes.generator.chain_video_wan22.main"


def _spec():
    return SimpleNamespace(
        variant="wan_ti2v_5b", sampling_settings={"guidance": "cfg"},
        latent_format={"latent_channels": 48, "format": "wan22", "spatial_downscale": 16},
    )


def _dit(spec):
    return SimpleNamespace(
        compute_dtype=torch.float32, spec=spec,
        module=SimpleNamespace(patch_size=(1, 2, 2), in_dim=48, img_emb=None),
        move_to=lambda d: None, offload=lambda: None,
    )


def _ti2v_bundle(encoded: List[torch.Tensor], low=False):
    spec = _spec()

    def encode(px):
        encoded.append(px.clone())
        return torch.full((1, 48, 1, px.shape[3] // 16, px.shape[4] // 16), float(len(encoded)))

    vae = SimpleNamespace(compute_dtype=torch.float32, move_to=lambda d: None, offload=lambda: None,
                          module=SimpleNamespace(encode=encode))
    return SimpleNamespace(high_dit=_dit(spec), low_dit=_dit(spec) if low else None, vae=vae,
                           is_dual_expert=low, spec=spec, loras_high=[], loras_low=[])


def _segment(seg_id, frames=13, **over):
    seg = {"id": seg_id, "prompt": "a", "negative_prompt": "", "start": None, "end": None,
           "frames": frames, "seed": None, "steps": None, "cfg": None, "loras": None}
    seg.update(over)
    return seg


def _media(seg_id, role):
    return {"id": f"m-{seg_id}-{role}", "role": role, "segment_id": seg_id, "at": None,
            "strength": 1.0, "media": {"path": f"/up/{seg_id}-{role}.png"}}


def _document(segments, media=(), continuation=None):
    return {
        "schema_version": 1, "mode": "director",
        "settings": {"fps": 16, "duration": None, "resolution": "", "seed": 7, "continuation": continuation},
        "segments": list(segments), "media": list(media), "audio": [], "ic_lora": [],
    }


def _pipe(document, **over):
    cfg = GeneratorWanChainVideoPipe.get_default_config()
    cfg.update({"resolution": "32x32", "device": "cpu", "document": document,
                "t2v_high_noise_model": {"file_path": "/m/ti2v_5b.safetensors"},
                "i2v_high_noise_model": {"file_path": "/m/ti2v_5b.safetensors"}})
    cfg.update(over)
    return GeneratorWanChainVideoPipe(config=cfg)


def _inputs(n, model=None, model_t2v=None, image=None, end_image=None):
    inp = {"conditioning": [SimpleNamespace(embeds={"context": torch.ones(1, 4, 8)}, n_embeds=None)
                            for _ in range(n)]}
    for key, value in (("model", model), ("model_t2v", model_t2v), ("image", image), ("end_image", end_image)):
        if value is not None:
            inp[key] = value
    return PipeInput(input=inp)


def _fake_decode(ctx, latent):
    frames = (latent.shape[2] - 1) * 4 + 1
    arr = np.zeros((frames, 32, 32, 3), dtype=np.uint8)
    for f in range(frames):
        arr[f] = 100 + f
    return arr


def _run(pipe, pipe_input):
    runs = []
    decoded = []

    def fake_denoise(model_forward, latents, cond, uncond, **kw):
        runs.append({"forward": model_forward, "latents": latents.clone(), "noise": kw["seed_noise"].clone()})
        return torch.randn_like(latents)

    def fake_decode(ctx, latent):
        decoded.append(latent.clone())
        return _fake_decode(ctx, latent)

    with patch(f"{_MAIN}.encode_frames_to_mp4", lambda frames, path, fps: Path(path).write_bytes(b"fake")), \
         patch(f"{_MAIN}.denoise", side_effect=fake_denoise), \
         patch(f"{_MAIN}.decode_wan_video", side_effect=fake_decode), \
         patch(f"{_MAIN}.stitch_segments", side_effect=lambda paths, overlaps, out, fps: out), \
         patch(f"{_MAIN}.build_i2v_concat") as mock_concat:
        pipe.process(pipe_input, lambda o: None)
    return runs, decoded, mock_concat


def _normalized(value):
    mean = torch.tensor(WAN22_LATENTS_MEAN).view(1, -1, 1, 1, 1)
    std = torch.tensor(WAN22_LATENTS_STD).view(1, -1, 1, 1, 1)
    return (torch.full((1, 48, 1, 2, 2), value) - mean) / std


def test_a_ti2v_5b_chain_opens_on_t2v_and_continues_from_the_last_frame_pinned_as_the_first_latent():
    encoded: List[torch.Tensor] = []
    bundle = _ti2v_bundle(encoded)
    document = _document([_segment("seg-0"), _segment("seg-1")])
    runs, decoded, mock_concat = _run(_pipe(document), _inputs(2, model=bundle, model_t2v=bundle))

    mock_concat.assert_not_called()
    assert isinstance(runs[0]["forward"], _ChainForward) and runs[0]["forward"].concat is None
    assert isinstance(runs[1]["forward"], TI2VForward)
    assert [tuple(px.shape) for px in encoded] == [(1, 3, 1, 32, 32)]
    assert torch.allclose(encoded[0], torch.full((1, 3, 1, 32, 32), 112 / 255.0 * 2.0 - 1.0))
    start = _normalized(1.0)
    assert tuple(runs[1]["latents"].shape) == (1, 48, 4, 2, 2)
    assert torch.allclose(runs[1]["latents"][:, :, :1], start)
    assert torch.allclose(runs[1]["noise"][:, :, :1], start)
    assert torch.allclose(decoded[1][:, :, :1], start)


def test_a_ti2v_5b_i2v_shot_encodes_its_own_start_image():
    encoded: List[torch.Tensor] = []
    bundle = _ti2v_bundle(encoded)
    document = _document([_segment("seg-0")], media=[_media("seg-0", "first")])
    runs, decoded, mock_concat = _run(_pipe(document), _inputs(1, model=bundle, image=[torch.full((32, 32, 3), 0.75)]))

    mock_concat.assert_not_called()
    assert isinstance(runs[0]["forward"], TI2VForward)
    assert torch.allclose(encoded[0], torch.full((1, 3, 1, 32, 32), 0.5))
    assert torch.allclose(runs[0]["latents"][:, :, :1], _normalized(1.0))


def test_a_ti2v_5b_flf_shot_raises_before_any_segment_work():
    encoded: List[torch.Tensor] = []
    bundle = _ti2v_bundle(encoded)
    document = _document([_segment("seg-0")], media=[_media("seg-0", "first"), _media("seg-0", "last")])
    with patch(f"{_MAIN}.denoise") as mock_denoise, pytest.raises(ValueError, match="first frame only"):
        _pipe(document).process(
            _inputs(1, model=bundle, image=[torch.rand(32, 32, 3)], end_image=[torch.rand(32, 32, 3)]), lambda o: None,
        )
    mock_denoise.assert_not_called()
    assert encoded == []


def test_a_ti2v_5b_chain_carrying_more_than_one_tail_frame_raises_before_any_segment_work():
    encoded: List[torch.Tensor] = []
    bundle = _ti2v_bundle(encoded)
    document = _document([_segment("seg-0"), _segment("seg-1")])
    with patch(f"{_MAIN}.denoise") as mock_denoise, pytest.raises(ValueError, match="motion latents carried to 1"):
        _pipe(document, motion_latent_count=2).process(_inputs(2, model=bundle, model_t2v=bundle), lambda o: None)
    mock_denoise.assert_not_called()


def test_a_ti2v_5b_set_with_a_low_noise_expert_raises():
    encoded: List[torch.Tensor] = []
    bundle = _ti2v_bundle(encoded, low=True)
    document = _document([_segment("seg-0")], media=[_media("seg-0", "first")])
    with pytest.raises(ValueError, match="low-noise picker empty"):
        _pipe(document).process(_inputs(1, model=bundle, image=[torch.rand(32, 32, 3)]), lambda o: None)


def test_a_ti2v_5b_i2v_shot_makes_room_on_the_card_before_it_encodes_its_start_image():
    encoded: List[torch.Tensor] = []
    bundle = _ti2v_bundle(encoded)
    order = []
    bundle.vae.move_to = lambda d: order.append("vae.move_to")
    encode = bundle.vae.module.encode
    bundle.vae.module.encode = lambda px: (order.append("encode"), encode(px))[1]

    def make_room(device, keep):
        order.append("make_room")
        kept = [id(m) for m in keep if m is not None]
        assert kept == [id(bundle.vae), id(bundle.high_dit)]

    document = _document([_segment("seg-0")], media=[_media("seg-0", "first")])
    with patch(f"{_MAIN}.make_room_for_wan_vae_encode", side_effect=make_room):
        _run(_pipe(document), _inputs(1, model=bundle, image=[torch.full((32, 32, 3), 0.75)]))

    assert order[:3] == ["make_room", "vae.move_to", "encode"]
