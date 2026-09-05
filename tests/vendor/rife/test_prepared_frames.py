"""Tests for reusing RIFE's per-frame preparation across timesteps and pairs.

`prepare_frame` hoists the zero-padding and (on rife47-49) the feature encode out
of `interpolate`, so a caller can prepare each source frame once instead of once
per synthesised frame. The reference these tests compare against is `_reference`
below: upstream's own path, padding both frames and letting `IFNet.forward`
encode them itself. Generating the expected values from `prepare_frame` would
only prove it agrees with itself -- the bug it has to rule out is preparation
that is subtly not what forward would have computed (encoding the unpadded frame
rather than the padded one, say).

CPU, tiny random-weight models: outputs are asserted bit-identical to the
reference and the encoder's call count is asserted, never visual quality.
"""

import pytest

torch = pytest.importorskip("torch")

import torch.nn.functional as F

from tests.vendor.rife.layouts import NARROW_ENCODER_BLOCKS, NARROW_NO_ENCODER_BLOCKS
from vendor.rife.ifnet import IFNet
from vendor.rife.inference import (
    PreparedFrame,
    interpolate,
    interpolate_prepared,
    pad_dims,
    prepare_frame,
)

TIMESTEPS = (0.25, 0.5, 0.75)


def _model(with_encoder, seed=0):
    torch.manual_seed(seed)
    specs = NARROW_ENCODER_BLOCKS if with_encoder else NARROW_NO_ENCODER_BLOCKS
    return IFNet(specs, (16, 4) if with_encoder else None).eval()


def _clip(n=3, h=64, w=64, seed=1):
    generator = torch.Generator().manual_seed(seed)
    return [torch.rand(1, 3, h, w, generator=generator) for _ in range(n)]


def _reference(model, img0, img1, timestep, flow_scale=1.0):
    """Upstream's per-call path, transcribed from Practical-RIFE's
    inference_video.py rather than from the module under test: pad both frames,
    hand them to forward, let forward run the encoder itself."""
    h, w = int(img0.shape[-2]), int(img0.shape[-1])
    ph, pw = pad_dims(h, w, flow_scale)
    pad = (0, pw - w, 0, ph - h)
    scales = [8.0 / flow_scale, 4.0 / flow_scale, 2.0 / flow_scale, 1.0 / flow_scale]
    with torch.no_grad():
        _, _, merged = model(
            torch.cat((F.pad(img0, pad), F.pad(img1, pad)), 1), timestep, scales
        )
    return merged[..., :h, :w]


def _count_encodes(model):
    calls = {"n": 0}
    if model.encode is not None:
        model.encode.register_forward_hook(
            lambda *_: calls.__setitem__("n", calls["n"] + 1)
        )
    return calls


def _stream_prepared(model, frames, timesteps, flow_scale=1.0):
    """The streaming shape the pipe uses: prepare each source frame once and carry
    the right frame of one pair over as the left frame of the next."""
    out = []
    carried = None
    for img0, img1 in zip(frames, frames[1:]):
        f0 = carried if (carried is not None and carried.matches(model, img0, flow_scale)) \
            else prepare_frame(model, img0, flow_scale)
        f1 = prepare_frame(model, img1, flow_scale)
        out.extend(interpolate_prepared(model, f0, f1, t, flow_scale) for t in timesteps)
        carried = f1
    return out


# -- reuse is exact -----------------------------------------------------------

@pytest.mark.parametrize("with_encoder", [False, True])
@pytest.mark.parametrize("t", TIMESTEPS)
def test_prepared_frame_matches_upstream_path(with_encoder, t):
    model = _model(with_encoder)
    img0, img1 = _clip(n=2)

    prepared = interpolate_prepared(
        model, prepare_frame(model, img0), prepare_frame(model, img1), t
    )

    assert torch.equal(prepared, _reference(model, img0, img1, t))


@pytest.mark.parametrize("with_encoder", [False, True])
def test_reuse_across_timesteps_and_adjacent_pairs_is_identical(with_encoder):
    # The middle frame of a 3-frame clip is prepared once and used as the right
    # half of pair 1 and the left half of pair 2; every timestep of both pairs
    # must still land on the value the uncached path produces.
    model = _model(with_encoder)
    frames = _clip(n=3)

    cached = _stream_prepared(model, frames, TIMESTEPS)
    uncached = [
        _reference(model, img0, img1, t)
        for img0, img1 in zip(frames, frames[1:])
        for t in TIMESTEPS
    ]

    assert len(cached) == len(uncached) == 6
    for got, want in zip(cached, uncached):
        assert torch.equal(got, want)


def test_interpolate_still_matches_the_upstream_path():
    # `interpolate` keeps its signature and prepares internally; it must not have
    # drifted from what forward computes on its own.
    model = _model(with_encoder=True)
    img0, img1 = _clip(n=2)

    assert torch.equal(
        interpolate(model, img0, img1, 0.5, 1.0), _reference(model, img0, img1, 0.5)
    )


@pytest.mark.parametrize("flow_scale", [1.0, 0.5])
def test_flow_scale_survives_preparation(flow_scale):
    model = _model(with_encoder=True)
    img0, img1 = _clip(n=2, h=128, w=128)

    prepared = interpolate_prepared(
        model,
        prepare_frame(model, img0, flow_scale),
        prepare_frame(model, img1, flow_scale),
        0.5,
        flow_scale,
    )

    assert torch.equal(prepared, _reference(model, img0, img1, 0.5, flow_scale))


def test_non_multiple_geometry_is_cropped_back():
    model = _model(with_encoder=True)
    img0, img1 = _clip(n=2, h=70, w=100)

    mid = interpolate_prepared(
        model, prepare_frame(model, img0), prepare_frame(model, img1), 0.5
    )

    assert mid.shape == (1, 3, 70, 100)


# -- the encoder runs once per source frame -----------------------------------

def test_encoder_runs_once_per_source_frame_when_prepared():
    # 3 frames at factor 4 = 2 pairs x 3 timesteps. Uncached, forward encodes
    # both frames of every call: 2 x 3 x 2 = 12. Prepared, each of the 3 source
    # frames is encoded once.
    frames = _clip(n=3)

    uncached_model = _model(with_encoder=True)
    uncached = _count_encodes(uncached_model)
    for img0, img1 in zip(frames, frames[1:]):
        for t in TIMESTEPS:
            _reference(uncached_model, img0, img1, t)

    cached_model = _model(with_encoder=True)
    cached = _count_encodes(cached_model)
    _stream_prepared(cached_model, frames, TIMESTEPS)

    assert uncached["n"] == 12
    assert cached["n"] == 3


def test_rife46_never_encodes_in_either_path():
    # rife46 has no feature encoder at all; preparation must not invent one.
    frames = _clip(n=3)
    model = _model(with_encoder=False)
    calls = _count_encodes(model)

    _stream_prepared(model, frames, TIMESTEPS)
    for img0, img1 in zip(frames, frames[1:]):
        _reference(model, img0, img1, 0.5)

    assert model.encode is None
    assert calls["n"] == 0
    assert prepare_frame(model, frames[0]).features is None


# -- staleness ----------------------------------------------------------------

def test_prepared_frame_records_its_context():
    model = _model(with_encoder=True)
    img = _clip(n=1, h=70, w=100)[0]

    prepared = prepare_frame(model, img, 0.5)

    assert isinstance(prepared, PreparedFrame)
    assert prepared.size == (70, 100)
    assert prepared.flow_scale == 0.5
    assert prepared.model_id == id(model)
    assert prepared.device == img.device and prepared.dtype == img.dtype
    assert prepared.padded.shape[-2:] == pad_dims(70, 100, 0.5)


def test_matches_rejects_a_changed_geometry():
    model = _model(with_encoder=True)
    small = _clip(n=1, h=64, w=64)[0]
    large = _clip(n=1, h=96, w=96)[0]
    prepared = prepare_frame(model, small)

    assert prepared.matches(model, small, 1.0)
    assert not prepared.matches(model, large, 1.0)


def test_matches_rejects_a_different_model_object():
    frames = _clip(n=1)
    prepared = prepare_frame(_model(with_encoder=True), frames[0])

    # A second model with the same shape and seed is still a different object:
    # its encoder weights are not the ones these features were computed with.
    assert not prepared.matches(_model(with_encoder=True), frames[0], 1.0)


def test_matches_rejects_a_changed_flow_scale_or_dtype():
    model = _model(with_encoder=True)
    img = _clip(n=1)[0]
    prepared = prepare_frame(model, img, 1.0)

    assert not prepared.matches(model, img, 0.5)
    assert not prepared.matches(model, img.double(), 1.0)


def test_interpolate_prepared_refuses_mismatched_frames():
    model = _model(with_encoder=True)
    small = prepare_frame(model, _clip(n=1, h=64, w=64)[0])
    large = prepare_frame(model, _clip(n=1, h=96, w=96)[0])

    with pytest.raises(ValueError, match="geometry"):
        interpolate_prepared(model, small, large, 0.5)

    with pytest.raises(ValueError, match="flow_scale"):
        interpolate_prepared(model, small, small, 0.5, 0.5)
