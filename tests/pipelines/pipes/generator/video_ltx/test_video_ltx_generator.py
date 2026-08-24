"""Tests for the generator/video_ltx pipe: packing round-trips, audio token
geometry, placement resolution, conditioned forward wrapping, audio gating,
and convenience i2v/FLF defaults — tiny fake bundles, no real weights."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest
import torch

from src.pipelines.contracts import IOType, PipeInput
from src.pipelines.pipes.generator.video_ltx.audio import audio_token_count, unpack_audio_tokens
from src.pipelines.pipes.generator.video_ltx.conditioning import (
    LTXMediaCondition,
    mix_initial_noise,
    prepare_ltx_conditions,
)
from src.pipelines.pipes._shared.generation.ltx_conditioned_forward import ConditionedAVForward
from src.pipelines.pipes.generator.video_ltx.main import (
    GeneratorLtxVideoPipe,
    _VideoLtxCtx,
    _resolve_latent_index,
)
from src.platform.runtime.native.sampling import conditioned_sigmas, denoise_prenoised


# -- audio geometry -------------------------------------------------------------

def test_audio_token_count_formula():
    assert audio_token_count(49, 25.0) == 49          # 1.96 s * 25 = 49
    assert audio_token_count(121, 24.0) == 126        # 5.0416 s * 25
    assert audio_token_count(1, 60.0) == 1            # floor at 1


def test_unpack_audio_tokens_layout():
    t = torch.arange(2 * 3 * 128, dtype=torch.float32).view(2, 3, 128)
    a = unpack_audio_tokens(t)
    assert a.shape == (2, 8, 3, 16)
    # b t (c f) inverse: token (t=0, c=0, f=:) must be the first 16 values.
    assert torch.equal(a[0, 0, 0], t[0, 0, :16])
    assert torch.equal(a[0, 1, 0], t[0, 0, 16:32])


# -- placement resolution ---------------------------------------------------------

@pytest.mark.parametrize("frame,expected", [
    ("first", 0), (0, 0), (None, 0),
    ("last", -1),
    (1, 1), (8, 1), (9, 2), (16, 2), (17, 3),
])
def test_resolve_latent_index(frame, expected):
    assert _resolve_latent_index(frame, frames=49) == expected


def test_resolve_latent_index_clamps_frame_at_clip_end():
    # frame == frames (one past the last valid pixel index) clamps to
    # frames-1 rather than raising -- placement `frame` is computed upstream
    # from an UNSNAPPED duration*fps while `frames` here is already snapped,
    # so an end-of-clip keyframe legitimately lands exactly on this boundary.
    # frames=49 -> last valid pixel index 48 -> latent (48-1)//8+1 == 6.
    assert _resolve_latent_index(49, frames=49) == 6
    assert _resolve_latent_index(48, frames=49) == 6


def test_resolve_latent_index_clamps_frame_over_due_to_snapping():
    # Reproduces the reported crash: at=4.9s of a 5s@25fps request -> unsnapped
    # duration*fps round()s to 123, but frames snaps 125 -> 121 (1+8k lattice).
    # 123 >= 121 previously raised; it must now clamp to the last pixel index
    # (120 -> latent (120-1)//8+1 == 15), matching an un-snapping-affected
    # placement pinned exactly at the (snapped) clip end.
    assert _resolve_latent_index(123, frames=121) == 15
    assert _resolve_latent_index(120, frames=121) == 15


def test_resolve_latent_index_negative_passes_through_unclamped():
    # A negative index is a deliberate "from the end" signal for the
    # conditioning builder's own modulo handling downstream, not a rounding
    # artifact -- the clamp must not touch it.
    assert _resolve_latent_index(-1, frames=49) == -1


# -- conditioned forward wrapper ---------------------------------------------------

FRAMES, H, W = 17, 64, 64
T_LAT, H_LAT, W_LAT = 3, 2, 2
S_BASE = T_LAT * H_LAT * W_LAT
C_LAT = 128


def _fake_encode(fill):
    def enc(pixels):
        _, _, t, h, w = pixels.shape
        return torch.full((1, C_LAT, (t - 1) // 8 + 1, h // 32, w // 32), float(fill))
    return enc


def _ctx(prepared, audio_tokens=0):
    return _VideoLtxCtx(
        bundle=None, sampling_settings={}, conditioning=[], prepared=prepared,
        steps=4, cfg=1.0, sampler="euler", width=W, height=H, frames=FRAMES,
        fps=25.0, device="cpu", dtype=torch.float32,
        audio_tokens=audio_tokens, t_lat=T_LAT, h_lat=H_LAT, w_lat=W_LAT,
    )


def _prepared(conditions=(), fill=5.0):
    conditions = list(conditions)
    if not conditions:
        return prepare_ltx_conditions(
            [], _fake_encode(fill), frames=FRAMES, height=H, width=W,
            device="cpu", dtype=torch.float32, latent_channels=C_LAT)
    return prepare_ltx_conditions(
        conditions, _fake_encode(fill), frames=FRAMES, height=H, width=W,
        device="cpu", dtype=torch.float32, latent_channels=C_LAT)


def test_unpack_base_repack_roundtrip():
    fw = ConditionedAVForward.__new__(ConditionedAVForward)
    fw.s_base, fw.t_lat, fw.h_lat, fw.w_lat = S_BASE, T_LAT, H_LAT, W_LAT
    v5d = torch.randn(1, C_LAT, T_LAT, H_LAT, W_LAT)
    packed = fw._repack(v5d)
    assert packed.shape == (1, S_BASE, C_LAT)
    assert torch.equal(fw.unpack_base(packed), v5d)


def test_conditioned_forward_zero_velocity_blend():
    """With a zero-velocity DiT, the blend must emit v = m*(x - clean)/sigma."""
    prepared = _prepared([LTXMediaCondition(frames=torch.rand(1, H, W, 3), latent_index=0, strength=1.0)])
    ctx = _ctx(prepared)

    def fake_dit(model_x, timestep, context, **kw):
        return torch.zeros_like(model_x[0])

    fw = ConditionedAVForward(fake_dit, ctx)
    x = torch.randn(1, S_BASE, C_LAT)
    sigma = torch.tensor([0.8])
    v = fw(x, sigma, {"context": None})
    m = prepared.mask.unsqueeze(-1)
    expected = m * (x - prepared.clean) / 0.8
    assert torch.allclose(v, expected, atol=1e-5)


def test_conditioned_forward_passes_extras_and_audio():
    prepared = _prepared([LTXMediaCondition(frames=torch.rand(1, H, W, 3), latent_index=1)])
    n_extra = prepared.n_extra
    t_audio = 5
    ctx = _ctx(prepared, audio_tokens=t_audio)
    seen = {}

    def fake_dit(model_x, timestep, context, **kw):
        seen["n_streams"] = len(model_x)
        seen["audio_shape"] = tuple(model_x[1].shape)
        seen["extra"] = kw["extra_video_tokens"] is not None
        seen["coords"] = tuple(kw["extra_video_pixel_coords"].shape)
        seen["sigma"] = kw["sigma"]
        v_ts, a_ts = timestep
        seen["v_ts_shape"] = tuple(v_ts.shape)
        return (torch.zeros_like(model_x[0]),
                torch.zeros_like(model_x[1]),
                torch.zeros(1, n_extra, C_LAT))

    fw = ConditionedAVForward(fake_dit, ctx)
    s_total = S_BASE + n_extra + t_audio
    x = torch.randn(1, s_total, C_LAT)
    v = fw(x, torch.tensor([0.5]), {"context": None})
    assert v.shape == (1, s_total, C_LAT)
    assert seen["n_streams"] == 2
    assert seen["audio_shape"] == (1, 8, t_audio, 16)
    assert seen["extra"] and seen["coords"] == (1, 3, n_extra, 2)
    assert seen["v_ts_shape"] == (1, S_BASE + n_extra)


# -- ancestral/CFG++ sampler interaction with conditioned forcing --
#
# The maintainer's actual workflow is video_ltx (i2v with a source image, often
# audio:true), not txt2vid_ltx -- so the distilled-refine recipe (sampler
# euler_ancestral_cfg_pp + manual sigmas) has to be safe against this pipe's
# per-token x0-space conditioning blend, not just plain unconditioned t2v.
# These tests establish WHY it's safe: the blend inside ConditionedAVForward
# is recomputed fresh from the CURRENT x/sigma on every single call, with no
# memory of prior steps -- so injecting ancestral (stochastic) noise into a
# conditioned/masked position doesn't accumulate drift; the very next call
# forces the velocity back to point exactly at `clean` from wherever x
# currently sits, and the terminal step (sigma_next==0) always collapses to
# that forced x0 exactly (see euler_ancestral_cfg_pp.py's own module
# docstring for why THAT collapse is exact regardless of sampler/eta).

def test_conditioned_forward_blend_recomputed_fresh_regardless_of_x_noise_level():
    """Blend-then-step ordering: the forcing happens INSIDE the model_forward
    wrapper, before any sampler ever sees the returned velocity -- so an `x`
    that already carries extra noise at a masked position (as if a prior
    ancestral step had injected some) still gets forced back to the exact
    same `v = m*(x-clean)/sigma` relationship, independent of how noisy `x`
    is and independent of the raw (non-zero) DiT prediction underneath."""
    prepared = _prepared([LTXMediaCondition(frames=torch.rand(1, H, W, 3), latent_index=0, strength=1.0)])
    ctx = _ctx(prepared)

    def fake_dit(model_x, timestep, context, **kw):
        # Non-trivial raw prediction -- if the forcing just passed this
        # through unmodified, the assertion below would fail.
        return torch.full_like(model_x[0], 3.7)

    fw = ConditionedAVForward(fake_dit, ctx)
    m = prepared.mask.unsqueeze(-1)
    sigma = torch.tensor([0.6])
    for extra_noise_scale in (0.0, 1.0, 5.0, 50.0):
        x = prepared.clean + extra_noise_scale * torch.randn_like(prepared.clean)
        v = fw(x, sigma, {"context": None})
        # Only the MASKED (conditioned) positions are forced; unmasked
        # positions pass the raw (here constant 3.7) DiT prediction through
        # unmodified -- comparing the full tensor to the forced-everywhere
        # formula would be wrong (see test_conditioned_forward_zero_velocity_
        # blend above, which follows the same masked-only comparison).
        expected_masked = m * (x - prepared.clean) / 0.6
        assert torch.allclose(v * m, expected_masked * m, atol=1e-4), \
            f"failed at noise scale {extra_noise_scale}"


def test_ancestral_cfg_pp_pins_conditioned_position_exactly_at_cfg_one():
    """Conditioned-frame invariance across a full sampling run: at cfg=1.0
    (the maintainer's actual distilled-recipe value -- TrueCFG's scale==1.0
    early return means no uncond branch, no CFG-Zero* rescale to interact
    with the forcing), running FULL ancestral noise injection (eta=1.0) at
    every intermediate step must still land EXACTLY on `clean` at the
    conditioned (masked) position once sampling reaches sigma=0."""
    torch.manual_seed(0)
    prepared = _prepared([LTXMediaCondition(frames=torch.rand(1, H, W, 3), latent_index=0, strength=1.0)])
    ctx = _ctx(prepared)

    def fake_dit(model_x, timestep, context, **kw):
        return torch.randn_like(model_x[0]) * 2.0

    fw = ConditionedAVForward(fake_dit, ctx)
    sigmas = conditioned_sigmas(8, {"guidance": "cfg", "shift": 1.0})
    noise = torch.randn(1, S_BASE, C_LAT)
    x_init = mix_initial_noise(prepared, noise, float(sigmas[0]))

    out = denoise_prenoised(
        fw, x_init, cond={"context": None}, uncond=None,
        steps=8, sampler_name="euler_ancestral_cfg_pp",
        sampling_settings={"guidance": "cfg"}, guidance_scale=1.0, sigmas=sigmas,
        sampler_options={"eta": 1.0, "generator": torch.Generator().manual_seed(3)},
    )
    m = prepared.mask.unsqueeze(-1)
    assert torch.allclose(out * m, prepared.clean * m, atol=1e-4)


def test_ancestral_cfg_pp_matches_plain_euler_at_conditioned_position():
    """Both euler and euler_ancestral_cfg_pp force the SAME clean value at
    masked positions via the SAME memoryless mechanism, so at cfg=1 they must
    agree there exactly -- while the unmasked (actually-generated) tokens are
    free to diverge (ancestral noise vs. fully deterministic)."""
    torch.manual_seed(1)
    prepared = _prepared([LTXMediaCondition(frames=torch.rand(1, H, W, 3), latent_index=0, strength=1.0)])
    ctx = _ctx(prepared)

    def fake_dit(model_x, timestep, context, **kw):
        return torch.randn_like(model_x[0]) * 2.0

    sigmas = conditioned_sigmas(8, {"guidance": "cfg", "shift": 1.0})
    noise = torch.randn(1, S_BASE, C_LAT)
    x_init = mix_initial_noise(prepared, noise, float(sigmas[0]))

    out_euler = denoise_prenoised(
        ConditionedAVForward(fake_dit, ctx), x_init.clone(), cond={"context": None}, uncond=None,
        steps=8, sampler_name="euler", sampling_settings={"guidance": "cfg"}, guidance_scale=1.0, sigmas=sigmas,
    )
    out_cfg_pp = denoise_prenoised(
        ConditionedAVForward(fake_dit, ctx), x_init.clone(), cond={"context": None}, uncond=None,
        steps=8, sampler_name="euler_ancestral_cfg_pp", sampling_settings={"guidance": "cfg"}, guidance_scale=1.0,
        sigmas=sigmas, sampler_options={"eta": 1.0, "generator": torch.Generator().manual_seed(9)},
    )
    m = prepared.mask.unsqueeze(-1)
    assert torch.allclose(out_euler * m, prepared.clean * m, atol=1e-4)
    assert torch.allclose(out_cfg_pp * m, prepared.clean * m, atol=1e-4)
    # And they genuinely diverge at unmasked positions -- proving the
    # ancestral noise actually did something rather than the test being
    # vacuously true (e.g. no unmasked tokens at all).
    unmasked = 1.0 - m
    assert torch.any(unmasked > 0)
    assert not torch.allclose(out_euler * unmasked, out_cfg_pp * unmasked, atol=1e-3)


def test_ancestral_cfg_pp_conditioned_position_bounded_at_cfg_above_one():
    """At cfg>1 the CFG-Zero* rescale (default on) multiplies the WHOLE
    uncond velocity by a single per-batch scalar alpha (see
    _cfg_zero_star_alpha in sampling/cfg.py), which is not exactly 1 in
    general -- so the masked-position forcing is only exact when alpha==1 or
    cfg==1 (measured empirically: ~0.75 max abs deviation at cfg=2.5,
    clean-value magnitude 5.0, for THIS synthetic fixture -- see the next
    test for the fix). This is a PRE-EXISTING property of the conditioning +
    CFG-Zero* interaction, unrelated to which sampler drives it (plain euler
    has it too) -- this test just confirms the ancestral/cfg_pp path doesn't
    make it materially worse (stays bounded, doesn't diverge/blow up) rather
    than asserting bit-exactness, which isn't guaranteed off the cfg=1 recipe
    or with cfg_zero_star left on."""
    torch.manual_seed(2)
    prepared = _prepared([LTXMediaCondition(frames=torch.rand(1, H, W, 3), latent_index=0, strength=1.0)])
    ctx = _ctx(prepared)

    def fake_dit(model_x, timestep, context, **kw):
        return torch.randn_like(model_x[0]) * 2.0

    sigmas = conditioned_sigmas(8, {"guidance": "cfg", "shift": 1.0})
    noise = torch.randn(1, S_BASE, C_LAT)
    x_init = mix_initial_noise(prepared, noise, float(sigmas[0]))
    # uncond embedding differs from cond so TrueCFG's uncond branch actually runs.
    out = denoise_prenoised(
        ConditionedAVForward(fake_dit, ctx), x_init, cond={"context": None}, uncond={"context": None},
        steps=8, sampler_name="euler_ancestral_cfg_pp", sampling_settings={"guidance": "cfg"}, guidance_scale=2.5,
        sigmas=sigmas, sampler_options={"eta": 1.0, "generator": torch.Generator().manual_seed(4)},
    )
    m = prepared.mask.unsqueeze(-1)
    assert torch.isfinite(out).all()
    assert torch.allclose(out * m, prepared.clean * m, atol=1.0)  # bounded, not necessarily exact


def test_ancestral_cfg_pp_conditioned_position_exact_with_cfg_zero_star_disabled():
    """The fix for the above: with cfg_zero_star=False, uncond_v is never
    rescaled, so cond_v == uncond_v exactly at masked positions regardless of
    cfg scale (both independently forced to the same `m*(x-clean)/sigma`
    formula) -- the masked position is pinned EXACTLY even at cfg=2.5 with
    full ancestral noise. Confirms the deviation above comes specifically
    from CFG-Zero*'s rescale, not from ancestral sampling or CFG itself."""
    torch.manual_seed(2)
    prepared = _prepared([LTXMediaCondition(frames=torch.rand(1, H, W, 3), latent_index=0, strength=1.0)])
    ctx = _ctx(prepared)

    def fake_dit(model_x, timestep, context, **kw):
        return torch.randn_like(model_x[0]) * 2.0

    sigmas = conditioned_sigmas(8, {"guidance": "cfg", "shift": 1.0})
    noise = torch.randn(1, S_BASE, C_LAT)
    x_init = mix_initial_noise(prepared, noise, float(sigmas[0]))
    out = denoise_prenoised(
        ConditionedAVForward(fake_dit, ctx), x_init, cond={"context": None}, uncond={"context": None},
        steps=8, sampler_name="euler_ancestral_cfg_pp", sampling_settings={"guidance": "cfg"}, guidance_scale=2.5,
        sigmas=sigmas, cfg_zero_star=False,
        sampler_options={"eta": 1.0, "generator": torch.Generator().manual_seed(4)},
    )
    m = prepared.mask.unsqueeze(-1)
    assert torch.allclose(out * m, prepared.clean * m, atol=1e-4)


# -- pipe-level -----------------------------------------------------------------

@dataclass
class _FakeSpec:
    family: str = "ltx"
    variant: str = "ltxav"
    sampling_settings: dict = field(default_factory=lambda: {"prediction": "const", "shift": 2.37, "guidance": "cfg"})


def _bundle(family="ltx", audio=False, te_cache_key=None):
    def encode(pixels):
        _, _, t, h, w = pixels.shape
        return torch.zeros(1, 128, (t - 1) // 8 + 1, h // 32, w // 32)

    vae = SimpleNamespace(
        compute_dtype=torch.float32, move_to=lambda d: None, offload=lambda: None,
        module=SimpleNamespace(
            encode=encode,
            decode=lambda z: torch.zeros(1, 3, z.shape[2], z.shape[3] * 32, z.shape[4] * 32)),
    )
    dit = SimpleNamespace(compute_dtype=torch.float32, move_to=lambda d: None, offload=lambda: None,
                          module=SimpleNamespace(config=SimpleNamespace(causal_temporal_positioning=True)))
    extras = {}
    if audio:
        extras = {"audio_vae": SimpleNamespace(compute_dtype=torch.float32, move_to=lambda d: None,
                                               offload=lambda: None, module=None),
                  "vocoder": SimpleNamespace(compute_dtype=torch.float32, move_to=lambda d: None,
                                             offload=lambda: None, module=None)}
    return SimpleNamespace(dit=dit, vae=vae, spec=_FakeSpec(family=family),
                           projections={}, audio_vae=extras.get("audio_vae"),
                           vocoder=extras.get("vocoder"),
                           te=SimpleNamespace(module=None), te_cache_key=te_cache_key)


class _FakeModelsService:
    """Records evict_dead_weight(key) calls; returns True (evicted) by default."""

    def __init__(self, evict_result=True, raise_on_evict=False):
        self.evict_calls: list[str] = []
        self._evict_result = evict_result
        self._raise = raise_on_evict

    def evict_dead_weight(self, key: str) -> bool:
        self.evict_calls.append(key)
        if self._raise:
            raise RuntimeError("boom")
        return self._evict_result


def _pipe(**over):
    cfg = GeneratorLtxVideoPipe.get_default_config()
    cfg["device"] = "cpu"
    cfg.update(over)
    return GeneratorLtxVideoPipe(config=cfg)


def _pil_image():
    Image = pytest.importorskip("PIL.Image")
    return Image.new("RGB", (64, 64), (255, 0, 0))


def _pipe_input(images=(), family="ltx", bundle=None, videos=(), models=None):
    cond = [SimpleNamespace(embeds={"context": torch.ones(1, 4, 8)}, n_embeds=None)]
    inp = {"model": bundle or _bundle(family=family), "conditioning": cond,
           "seed": [7], "image": list(images), "video": list(videos)}
    if models is not None:
        inp["MODELS"] = models
    return PipeInput(input=inp)


def _reference_clip(tmp_path, n_frames=9, size=64):
    """Synthesize a real playable clip: `source: "video"` placements go through
    `_load_video_frames`, which is cv2-backed and opens the file for real."""
    cv2 = pytest.importorskip(
        "cv2", reason="video-sourced conditioning is cv2-backed", exc_type=ImportError)
    path = tmp_path / "reference.mp4"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 8.0, (size, size))
    if not writer.isOpened():
        pytest.skip("no mp4v encoder available to synthesize a reference clip")
    try:
        for i in range(n_frames):
            writer.write(np.full((size, size, 3), i * 10, dtype=np.uint8))
    finally:
        writer.release()
    return path


def test_metadata():
    assert GeneratorLtxVideoPipe.name == "generator"
    inputs = {i.name: i.io_type for i in GeneratorLtxVideoPipe.inputs()}
    assert inputs["image"] == IOType.IMAGE
    assert inputs["video"] == IOType.VIDEO
    assert inputs["audio"] == IOType.AUDIO
    assert GeneratorLtxVideoPipe.outputs()[0].io_type == IOType.VIDEO


def test_metadata_includes_audio_output_and_passthrough_choice():
    # The pipe emits a per-seed `audio` output (mux
    # hand-off for a stage-2 refine) and offers `audio_source="passthrough"`.
    outputs = {o.name: o.io_type for o in GeneratorLtxVideoPipe.outputs()}
    assert outputs["audio"] == IOType.AUDIO
    spec = next(s for s in GeneratorLtxVideoPipe.configuration() if s.name == "audio_source")
    assert set(spec.choices) == {"generate", "file", "passthrough"}


def test_sampler_choices_euler_and_cfg_pp_only():
    # video_ltx's conditioned runs support only single-step, no-history
    # samplers (the x0-space conditioning blend is re-derived fresh per step;
    # multistep history over blended velocities, e.g. dpmpp_2m, is
    # unvalidated) -- unlike the other 4 video pipes, this one must NOT pick
    # up #32's full sampler set. euler_ancestral_cfg_pp, euler_cfg_pp
    # (Lightricks' own deterministic distilled-refine recipe, now the
    # Distilled speed profile's default -- see docs/models/ltx.md) and
    # euler_ancestral (LTX-2.5 stage-1) ARE offered: none carries cross-step
    # velocity history, same class as euler/euler_sde.
    spec = next(s for s in GeneratorLtxVideoPipe.configuration() if s.name == "sampler")
    assert set(spec.choices) == {"euler", "euler_ancestral", "euler_ancestral_cfg_pp", "euler_cfg_pp"}


def test_non_ltx_model_raises():
    with pytest.raises(ValueError, match="LTX"):
        _pipe().build_context(_pipe_input(family="wan"))


def test_no_media_yields_unconditioned_state():
    ctx = _pipe(resolution="768x512", frames=49).build_context(_pipe_input())
    p = ctx.extra.prepared
    assert p.n_extra == 0
    assert p.base_tokens == 7 * 16 * 24
    assert torch.all(p.mask == 0)


def test_convenience_i2v_and_flf_defaults():
    img = _pil_image()
    ctx1 = _pipe(resolution="64x64", frames=17).build_context(_pipe_input(images=[img]))
    p1 = ctx1.extra.prepared
    assert p1.n_extra == 0 and torch.any(p1.mask > 0)          # first-frame overwrite

    ctx2 = _pipe(resolution="64x64", frames=17).build_context(_pipe_input(images=[img, img]))
    p2 = ctx2.extra.prepared
    assert p2.n_extra > 0                                       # last frame appended
    assert torch.any(p2.mask[:, : p2.base_tokens] > 0)


def test_image_sourced_reference_placement_builds_single_frame_reference_tokens():
    """An IC-LoRA reference routed to `source: "image"` (the media
    type it actually is, instead of the old hardcoded "video") must produce
    sane reference-role conditioning from a single still -- shape-level check
    with a fake VAE, no real weights. 1 input frame -> 1 latent frame's worth
    of appended tokens (H_LAT * W_LAT), full strength in the mask, latent_index
    forced to 0 (unread for role="reference")."""
    cfg = _pipe(resolution="64x64", frames=17, media_placements=[
        {"source": "image", "index": 0, "frame": "first", "strength": 0.75, "role": "reference"},
    ])
    ctx = cfg.build_context(_pipe_input(images=[_pil_image()]))
    p = ctx.extra.prepared
    n_ref = 2 * 2  # H_LAT * W_LAT at 64x64 (LTX_SPATIAL=32) for a single latent frame
    assert p.n_extra == n_ref
    assert p.base_tokens == 3 * 2 * 2  # T_LAT * H_LAT * W_LAT unchanged (no overwrite)
    assert torch.all(p.tokens[:, : p.base_tokens] == 0)          # base untouched
    assert torch.allclose(p.mask[:, p.base_tokens:], torch.full((1, n_ref), 0.75))
    assert p.extra_coords is not None and p.extra_coords.shape == (1, 3, n_ref, 2)


def test_video_sourced_reference_placement_decodes_clip_into_reference_tokens(tmp_path):
    """A video-typed IC-LoRA reference (`source: "video"` indexing the `video`
    input, which the LTX-2 director wires from its `media_videos` loader) must
    decode through `_load_video_frames` and land as appended reference-role
    tokens -- the video counterpart of the image-sourced case above. 9 pixel
    frames -> 2 latent frames (LTX_TEMPORAL=8), so H_LAT * W_LAT * 2 tokens.
    """
    clip = _reference_clip(tmp_path, n_frames=9)
    cfg = _pipe(resolution="64x64", frames=17, media_placements=[
        {"source": "video", "index": 0, "frame": "first", "strength": 0.6, "role": "reference"},
    ])
    ctx = cfg.build_context(_pipe_input(videos=[str(clip)]))
    p = ctx.extra.prepared

    n_ref = 2 * 2 * 2
    assert p.n_extra == n_ref
    assert p.base_tokens == 3 * 2 * 2                              # base grid unchanged
    assert torch.all(p.tokens[:, : p.base_tokens] == 0)            # no first-frame overwrite
    assert torch.allclose(p.mask[:, p.base_tokens:], torch.full((1, n_ref), 0.6))
    assert p.extra_coords is not None and p.extra_coords.shape == (1, 3, n_ref, 2)


def test_video_sourced_placement_index_out_of_range_raises():
    cfg = _pipe(resolution="64x64", frames=17, media_placements=[
        {"source": "video", "index": 1, "frame": "first", "strength": 1.0, "role": "reference"},
    ])
    with pytest.raises(ValueError, match=r"video\[1\]"):
        cfg.build_context(_pipe_input(videos=["/only/one.mp4"]))


def test_audio_generate_without_components_raises():
    with pytest.raises(ValueError, match="audio"):
        _pipe(audio=True, audio_source="generate").build_context(_pipe_input())


def test_audio_generate_with_components_sets_token_count():
    bundle = _bundle(audio=True)
    ctx = _pipe(audio=True, audio_source="generate", resolution="64x64", frames=17) \
        .build_context(_pipe_input(bundle=bundle))
    # 17 frames @ 25 fps = 0.68 s -> round(17) = 17 audio tokens
    assert ctx.extra.audio_tokens == audio_token_count(17, 25.0)
    assert ctx.extra.audio_mode == "generate"


def test_audio_file_requires_audio_input():
    with pytest.raises(ValueError, match="audio input"):
        _pipe(audio=True, audio_source="file").build_context(_pipe_input())


# -- audio_source="passthrough" (stage-2 mux hand-off) --

def test_audio_passthrough_without_initial_latent_raises():
    """passthrough only makes sense on a stage-2 refine call -- using it on a
    plain generation (no initial_latent connected) is meaningless and must be
    rejected loudly rather than silently muxing nothing."""
    pipe_input = _pipe_input()
    pipe_input.input["audio"] = ["/fake/stage1_audio.wav"]
    with pytest.raises(ValueError, match="passthrough"):
        _pipe(audio=True, audio_source="passthrough").build_context(pipe_input)


def test_audio_passthrough_requires_audio_input():
    pipe_input = _pipe_input()
    pipe_input.input["initial_latent"] = [torch.zeros(1, 128, 3, 2, 2)]
    with pytest.raises(ValueError, match="audio input"):
        _pipe(resolution="64x64", frames=17, audio=True, audio_source="passthrough").build_context(pipe_input)


def test_audio_passthrough_accepts_stage1_track_alongside_initial_latent():
    from src.pipelines.pipes._shared.media.video_encode import AudioTrack
    track = AudioTrack(waveform=np.zeros((1, 10), dtype=np.float32), sample_rate=16000)
    pipe_input = _pipe_input()
    pipe_input.input["initial_latent"] = [torch.zeros(1, 128, 3, 2, 2)]
    pipe_input.input["audio"] = [track]
    ctx = _pipe(resolution="64x64", frames=17, audio=True, audio_source="passthrough").build_context(pipe_input)
    assert ctx.extra.audio_mode == "passthrough"
    assert ctx.extra.audio_file is track
    # No AV DiT audio tokens for a passthrough mux -- nothing is regenerated.
    assert ctx.extra.audio_tokens == 0


def test_placement_missing_image_raises():
    with pytest.raises(ValueError, match="image"):
        _pipe(media_placements=[{"source": "image", "index": 0, "frame": "first"}]) \
            .build_context(_pipe_input())


# -- APG settings threading ------------------------------------------------------

def test_apg_defaults_are_omitted_not_forced_onto_sampling_settings():
    # P5 fix: unset apg_* knobs must be OMITTED, not forced to a "default"
    # value, so they never clobber a non-default ModelSpec value.
    ctx = _pipe(resolution="768x512", frames=49).build_context(_pipe_input())
    ss = ctx.extra.sampling_settings
    for key in ("apg_eta", "apg_norm_threshold", "apg_momentum"):
        assert key not in ss
    assert ss["guidance"] == "cfg"  # base spec key survives the merge


def test_apg_config_overrides_thread_into_sampling_settings():
    ctx = _pipe(resolution="768x512", frames=49, apg_eta=0.6, apg_norm_threshold=0.4, apg_momentum=-0.2) \
        .build_context(_pipe_input())
    ss = ctx.extra.sampling_settings
    assert ss["apg_eta"] == 0.6
    assert ss["apg_norm_threshold"] == 0.4
    assert ss["apg_momentum"] == -0.2


def test_schedule_settings_config_overrides_thread_into_sampling_settings():
    ctx = _pipe(resolution="768x512", frames=49, schedule="beta",
                schedule_options={"alpha": 0.5, "beta": 0.7}, detail_strength=-0.2) \
        .build_context(_pipe_input())
    ss = ctx.extra.sampling_settings
    assert ss["schedule"] == "beta"
    assert ss["schedule_options"] == {"alpha": 0.5, "beta": 0.7}
    assert ss["detail_strength"] == -0.2


def test_manual_sigmas_config_threads_into_sampling_settings_as_manual_schedule():
    # the maintainer's validated distilled-refine recipe,
    # expressed through this pipe's config surface too (video/Director mode).
    recipe = "1.0, 0.99375, 0.9875, 0.98125, 0.975, 0.909375, 0.725, 0.421875, 0.0"
    ctx = _pipe(
        resolution="768x512", frames=49, sampler="euler_ancestral_cfg_pp", cfg=1.0,
        manual_sigmas=recipe, schedule="beta", schedule_options={"alpha": 0.4, "beta": 0.9},
    ).build_context(_pipe_input())
    ss = ctx.extra.sampling_settings
    assert ss["schedule"] == "manual"
    assert ss["schedule_options"] == {"sigmas": recipe}
    assert ctx.extra.sampler == "euler_ancestral_cfg_pp"
    assert ctx.extra.cfg == 1.0

    from src.platform.runtime.native.sampling.flow_schedule import build_sigmas
    sigmas = build_sigmas(24, schedule=ss["schedule"], schedule_options=ss["schedule_options"])
    assert sigmas.shape == (9,)
    assert sigmas[0].item() == 1.0
    assert sigmas[-1].item() == 0.0


def test_manual_sigmas_default_empty_leaves_schedule_unset():
    ctx = _pipe(resolution="768x512", frames=49).build_context(_pipe_input())
    ss = ctx.extra.sampling_settings
    assert "schedule" not in ss
    assert "schedule_options" not in ss


# -- sampler_options / step_cache ---------------------------------------------

@patch("src.pipelines.pipes.generator.video_ltx.main.encode_frames_to_mp4", lambda frames, path, fps, audio=None: path)
def test_sampler_options_step_cache_absent_reach_denoise_prenoised_as_none():
    captured = {}
    with patch("src.pipelines.pipes.generator.video_ltx.main.denoise_prenoised") as md:
        md.side_effect = lambda fwd, x, cond_, uncond, **k: captured.update(k) or x
        _pipe(resolution="768x512", frames=49).process(_pipe_input(), lambda o: None)
    assert captured["sampler_options"] is None
    assert captured["step_cache_options"] is None


@patch("src.pipelines.pipes.generator.video_ltx.main.encode_frames_to_mp4", lambda frames, path, fps, audio=None: path)
def test_denoise_prenoised_receives_the_managers_is_cancelled_probe():
    captured = {}
    probe = lambda: False
    with patch("src.pipelines.pipes.generator.video_ltx.main.denoise_prenoised") as md:
        md.side_effect = lambda fwd, x, cond_, uncond, **k: captured.update(k) or x
        _pipe(resolution="768x512", frames=49).process(_pipe_input(), lambda o: None, is_cancelled=probe)
    assert captured["is_cancelled"] is probe


@patch("src.pipelines.pipes.generator.video_ltx.main.encode_frames_to_mp4", lambda frames, path, fps, audio=None: path)
def test_sampler_options_step_cache_present_reach_denoise_prenoised():
    captured = {}
    sampler_opts = {"restart_count": 1}
    step_cache_opts = {"rel_threshold": 0.2}
    with patch("src.pipelines.pipes.generator.video_ltx.main.denoise_prenoised") as md:
        md.side_effect = lambda fwd, x, cond_, uncond, **k: captured.update(k) or x
        _pipe(resolution="768x512", frames=49, sampler_options=sampler_opts, step_cache=step_cache_opts).process(
            _pipe_input(), lambda o: None)
    assert captured["sampler_options"] == sampler_opts
    assert captured["step_cache_options"] == step_cache_opts


def test_conditioned_forward_step_cache_absent_no_extra_kwargs():
    prepared = _prepared()
    ctx = _ctx(prepared)
    seen = {}

    def fake_dit(model_x, timestep, context, **kw):
        seen["kwargs"] = {k: v for k, v in kw.items() if k in ("step_cache",)}
        return torch.zeros_like(model_x[0])

    fw = ConditionedAVForward(fake_dit, ctx)
    x = torch.randn(1, S_BASE, C_LAT)
    fw(x, torch.tensor([0.5]), {"context": None})
    assert seen["kwargs"] == {}


def test_conditioned_forward_step_cache_present_reaches_dit_kwargs():
    prepared = _prepared()
    ctx = _ctx(prepared)
    seen = {}
    sentinel_cache = object()

    def fake_dit(model_x, timestep, context, **kw):
        seen["step_cache"] = kw.get("step_cache")
        return torch.zeros_like(model_x[0])

    fw = ConditionedAVForward(fake_dit, ctx)
    x = torch.randn(1, S_BASE, C_LAT)
    fw(x, torch.tensor([0.5]), {"context": None, "step_cache": sentinel_cache})
    assert seen["step_cache"] is sentinel_cache


def test_conditioned_forward_none_step_cache_is_not_forwarded():
    prepared = _prepared()
    ctx = _ctx(prepared)
    seen = {}

    def fake_dit(model_x, timestep, context, **kw):
        seen["kwargs"] = {k: v for k, v in kw.items() if k in ("step_cache",)}
        return torch.zeros_like(model_x[0])

    fw = ConditionedAVForward(fake_dit, ctx)
    x = torch.randn(1, S_BASE, C_LAT)
    fw(x, torch.tensor([0.5]), {"context": None, "step_cache": None})
    assert seen["kwargs"] == {}


# -- model-input clamp (ancestral sampler i2v conditioning fix) ----

def test_conditioned_forward_clamps_model_input_to_clean_at_masked_positions():
    """The DiT must receive `clean` at masked positions in its input, not the
    sampler's potentially-noisy x — this is the fix for the ancestral-noise bug (ancestral
    samplers inject fresh noise into all tokens each step; without this clamp
    the DiT sees `clean + accumulated ancestral noise` at positions whose
    per-token timestep claims sigma=0, corrupting i2v identity)."""
    prepared = _prepared([LTXMediaCondition(frames=torch.rand(1, H, W, 3), latent_index=0, strength=1.0)])
    ctx = _ctx(prepared)
    seen = {}

    def recording_dit(model_x, timestep, context, **kw):
        # Record the 5D base video input the DiT received (before any repacking)
        seen["model_x_base"] = model_x[0].clone()
        return torch.randn_like(model_x[0]) * 2.0

    fw = ConditionedAVForward(recording_dit, ctx)
    m = prepared.mask.unsqueeze(-1)

    # Poison the sampler's x at masked positions with deliberate noise
    x_sampler = prepared.clean.clone()
    noise_poison = torch.randn_like(prepared.clean) * 10.0
    x_sampler = x_sampler * (1.0 - m) + (prepared.clean + noise_poison) * m

    fw(x_sampler, torch.tensor([0.8]), {"context": None})

    # The DiT must have seen clean at masked positions (unpacked to 5D)
    model_x_repacked = fw._repack(seen["model_x_base"])
    assert torch.allclose(model_x_repacked * m, prepared.clean * m, atol=1e-5), \
        "model input at masked positions must equal clean exactly"
    # Unmasked positions should match the sampler's x (no clamp applied there)
    unmasked = 1.0 - m
    assert torch.allclose(model_x_repacked * unmasked, x_sampler[:, :S_BASE] * unmasked, atol=1e-5), \
        "model input at unmasked positions must match sampler's x"


def test_conditioned_forward_x0_blend_uses_original_sampler_x():
    """The x0-blend must operate on the sampler's ORIGINAL x (with its noise),
    not the clamped model-input tensor — this defines the trajectory update."""
    prepared = _prepared([LTXMediaCondition(frames=torch.rand(1, H, W, 3), latent_index=0, strength=1.0)])
    ctx = _ctx(prepared)

    def fake_dit(model_x, timestep, context, **kw):
        # Return a nonzero velocity so the blend isn't vacuous
        return torch.full_like(model_x[0], 2.5)

    fw = ConditionedAVForward(fake_dit, ctx)
    m = prepared.mask.unsqueeze(-1)

    # Sampler's x with noise at masked positions
    x_sampler = prepared.clean + torch.randn_like(prepared.clean) * 5.0
    sigma = torch.tensor([0.7])
    v = fw(x_sampler, sigma, {"context": None})

    # At masked positions: x0 = x_sampler - sigma*v must equal clean
    # (the blend formula: x0 <- x0*(1-m) + clean*m forces it)
    x0_from_v = x_sampler - sigma.item() * v
    assert torch.allclose(x0_from_v * m, prepared.clean * m, atol=1e-4), \
        "x0 from returned velocity must match clean at masked positions"


def test_conditioned_forward_no_mask_skips_clamp():
    """Pure t2v (mask all-zeros) must not apply the model-input clamp — the
    DiT receives the sampler's x unmodified."""
    prepared = _prepared([])  # No conditions = mask all zeros
    assert not prepared.mask.any()
    ctx = _ctx(prepared)
    seen = {}

    def recording_dit(model_x, timestep, context, **kw):
        seen["model_x_base"] = model_x[0].clone()
        return torch.zeros_like(model_x[0])

    fw = ConditionedAVForward(recording_dit, ctx)
    x_sampler = torch.randn(1, S_BASE, C_LAT)
    fw(x_sampler, torch.tensor([0.5]), {"context": None})

    model_x_repacked = fw._repack(seen["model_x_base"])
    assert torch.equal(model_x_repacked, x_sampler), \
        "no-mask case: model input must be identical to sampler x (no clamp)"


def test_euler_ancestral_cfg_pp_conditioned_final_output_exact_after_model_input_fix():
    """End-to-end: with the model-input clamp in place, euler_ancestral_cfg_pp
    at cfg=1.0 must converge to `clean` at masked positions exactly (the
    combination of the clamp + the x0-blend + the sampler's sigma=0 collapse
    restores the diffusers invariant under full ancestral noise)."""
    torch.manual_seed(5)
    prepared = _prepared([LTXMediaCondition(frames=torch.rand(1, H, W, 3), latent_index=0, strength=1.0)])
    ctx = _ctx(prepared)

    def fake_dit(model_x, timestep, context, **kw):
        return torch.randn_like(model_x[0]) * 3.0

    fw = ConditionedAVForward(fake_dit, ctx)
    sigmas = conditioned_sigmas(8, {"guidance": "cfg", "shift": 1.0})
    noise = torch.randn(1, S_BASE, C_LAT)
    x_init = mix_initial_noise(prepared, noise, float(sigmas[0]))

    out = denoise_prenoised(
        fw, x_init, cond={"context": None}, uncond=None,
        steps=8, sampler_name="euler_ancestral_cfg_pp",
        sampling_settings={"guidance": "cfg"}, guidance_scale=1.0, sigmas=sigmas,
        sampler_options={"eta": 1.0, "generator": torch.Generator().manual_seed(7)},
    )
    m = prepared.mask.unsqueeze(-1)
    assert torch.allclose(out * m, prepared.clean * m, atol=1e-4), \
        "final output at masked positions must equal clean exactly"


# -- NAG-LTX: ConditionedAVForward kwarg threading ------------------------------

def test_conditioned_forward_nag_absent_no_extra_kwargs():
    prepared = _prepared()
    ctx = _ctx(prepared)
    seen = {}

    def fake_dit(model_x, timestep, context, **kw):
        seen["kwargs"] = {k: v for k, v in kw.items() if k in ("nag_context", "nag")}
        return torch.zeros_like(model_x[0])

    fw = ConditionedAVForward(fake_dit, ctx)
    x = torch.randn(1, S_BASE, C_LAT)
    fw(x, torch.tensor([0.5]), {"context": None})
    assert seen["kwargs"] == {}


def test_conditioned_forward_nag_present_reaches_dit_kwargs():
    prepared = _prepared()
    ctx = _ctx(prepared)
    seen = {}

    def fake_dit(model_x, timestep, context, **kw):
        seen["nag_context"] = kw.get("nag_context")
        seen["nag"] = kw.get("nag")
        return torch.zeros_like(model_x[0])

    fw = ConditionedAVForward(fake_dit, ctx)
    x = torch.randn(1, S_BASE, C_LAT)
    neg_context = torch.ones(1, 4, 8)
    conditioning = {"context": None, "nag_context": neg_context, "nag": {"scale": 1.4, "tau": 3.0, "alpha": 0.5}}
    fw(x, torch.tensor([0.5]), conditioning)
    assert torch.equal(seen["nag_context"], neg_context)
    assert seen["nag"] == {"scale": 1.4, "tau": 3.0, "alpha": 0.5}


# -- NAG-LTX: pipe-level (_attach_nag composition) -------------------------------

@patch("src.pipelines.pipes.generator.video_ltx.main.encode_frames_to_mp4", lambda frames, path, fps, audio=None: path)
def test_nag_default_off_does_not_touch_cond():
    captured = {}
    cond = [SimpleNamespace(embeds={"context": torch.ones(1, 4, 8)}, n_embeds={"context": torch.zeros(1, 4, 8)})]
    pipe_input = PipeInput(input={"model": _bundle(), "conditioning": cond, "seed": [7], "image": []})
    with patch("src.pipelines.pipes.generator.video_ltx.main.denoise_prenoised") as md:
        md.side_effect = lambda fwd, x, cond_, uncond, **k: captured.update(cond=cond_) or x
        _pipe(resolution="768x512", frames=49).process(pipe_input, lambda o: None)
    assert "nag_context" not in captured["cond"]


@patch("src.pipelines.pipes.generator.video_ltx.main.encode_frames_to_mp4", lambda frames, path, fps, audio=None: path)
def test_nag_scale_above_one_attaches_negative_context_equal_to_uncond_context():
    captured = {}
    cond = [SimpleNamespace(embeds={"context": torch.ones(1, 4, 8)}, n_embeds={"context": torch.zeros(1, 4, 8)})]
    pipe_input = PipeInput(input={"model": _bundle(), "conditioning": cond, "seed": [7], "image": []})
    with patch("src.pipelines.pipes.generator.video_ltx.main.denoise_prenoised") as md:
        md.side_effect = lambda fwd, x, cond_, uncond, **k: captured.update(cond=cond_, uncond=uncond) or x
        _pipe(resolution="768x512", frames=49, nag_scale=1.5, nag_tau=2.0, nag_alpha=0.25).process(
            pipe_input, lambda o: None)
    assert torch.equal(captured["cond"]["nag_context"], captured["uncond"]["context"])
    assert captured["cond"]["nag"] == {"scale": 1.5, "tau": 2.0, "alpha": 0.25}


# -- guidance_options: cfg_zero_star / zero_init_steps ---------------------------

@patch("src.pipelines.pipes.generator.video_ltx.main.encode_frames_to_mp4", lambda frames, path, fps, audio=None: path)
def test_cfg_zero_star_and_zero_init_steps_reach_denoise_prenoised():
    captured = {}
    with patch("src.pipelines.pipes.generator.video_ltx.main.denoise_prenoised") as md:
        md.side_effect = lambda fwd, x, cond_, uncond, **k: captured.update(k) or x
        _pipe(resolution="768x512", frames=49, cfg_zero_star=False, zero_init_steps=2).process(
            _pipe_input(), lambda o: None)
    assert captured["cfg_zero_star"] is False
    assert captured["zero_init_steps"] == 2


@patch("src.pipelines.pipes.generator.video_ltx.main.encode_frames_to_mp4", lambda frames, path, fps, audio=None: path)
def test_cfg_zero_star_defaults_true_zero_init_steps_defaults_zero():
    captured = {}
    with patch("src.pipelines.pipes.generator.video_ltx.main.denoise_prenoised") as md:
        md.side_effect = lambda fwd, x, cond_, uncond, **k: captured.update(k) or x
        _pipe(resolution="768x512", frames=49).process(_pipe_input(), lambda o: None)
    assert captured["cfg_zero_star"] is True
    assert captured["zero_init_steps"] == 0


# -- best-effort DiT-to-VRAM restore after decode -------------

@patch("src.pipelines.pipes.generator.video_ltx.main.encode_frames_to_mp4", lambda frames, path, fps, audio=None: path)
@patch("src.pipelines.pipes.generator.video_ltx.main.denoise_prenoised", lambda fwd, x, c, u, **k: x)
def test_emitted_videos_carry_live_resolution():
    """This generator used to emit VideoGenerationOutput with
    resolution=None -- the live workbench/gallery message had no dimensions
    until the file was later re-fetched from the DB. build_context() now
    stashes the (post-snap) resolution on self so emit_results() can stamp
    it onto every video it emits, matching what image pipes already do."""
    from src.pipelines.outputs import GalleryGenerationOutput

    bundle = _bundle()
    cond = [SimpleNamespace(embeds={"context": torch.ones(1, 4, 8)}, n_embeds=None) for _ in range(2)]
    pipe_input = PipeInput(input={"model": bundle, "conditioning": cond, "seed": [5, 6], "image": []})
    emitted = []
    # 1000x540 -> 32px grid (992x544), same snap this pipe's build_context applies.
    _pipe(resolution="1000x540", frames=49, quantity=2).process(pipe_input, lambda o: emitted.append(o))
    gallery = [o for o in emitted if isinstance(o, GalleryGenerationOutput)][0]
    assert [v.resolution for v in gallery.videos] == [(992, 544), (992, 544)]


@patch("src.pipelines.pipes.generator.video_ltx.main.encode_frames_to_mp4", lambda frames, path, fps, audio=None: path)
@patch("src.pipelines.pipes.generator.video_ltx.main.denoise_prenoised", lambda fwd, x, c, u, **k: x)
@patch("src.pipelines.pipes.generator.video_ltx.main.restore_dit_best_effort")
def test_restore_called_once_after_last_seed_of_quantity(mock_restore):
    bundle = _bundle()
    cond = [SimpleNamespace(embeds={"context": torch.ones(1, 4, 8)}, n_embeds=None) for _ in range(2)]
    pipe_input = PipeInput(input={"model": bundle, "conditioning": cond, "seed": [5, 6], "image": []})
    _pipe(resolution="768x512", frames=49, quantity=2).process(pipe_input, lambda o: None)
    mock_restore.assert_called_once_with(bundle.dit, "cpu")


@patch("src.pipelines.pipes.generator.video_ltx.main.encode_frames_to_mp4", lambda frames, path, fps, audio=None: path)
@patch("src.pipelines.pipes.generator.video_ltx.main.denoise_prenoised", lambda fwd, x, c, u, **k: x)
@patch("src.pipelines.pipes.generator.video_ltx.main.restore_dit_best_effort")
def test_restore_not_called_between_seeds_of_a_single_invocation(mock_restore):
    calls = {"n": 0}
    orig_generate_one = GeneratorLtxVideoPipe.generate_one

    def counting_generate_one(self, ctx, index, seed, progress):
        calls["n"] += 1
        assert mock_restore.call_count == 0, "restore fired before the final seed"
        return orig_generate_one(self, ctx, index, seed, progress)

    bundle = _bundle()
    cond = [SimpleNamespace(embeds={"context": torch.ones(1, 4, 8)}, n_embeds=None) for _ in range(3)]
    pipe_input = PipeInput(input={"model": bundle, "conditioning": cond, "seed": [1, 2, 3], "image": []})
    with patch.object(GeneratorLtxVideoPipe, "generate_one", counting_generate_one):
        _pipe(resolution="768x512", frames=49, quantity=3).process(pipe_input, lambda o: None)
    assert calls["n"] == 3
    mock_restore.assert_called_once()


# -- initial_latent / decode=false (upscale refine stage) -----

def test_metadata_includes_latent_output_and_initial_latent_input():
    outputs = {o.name: o.io_type for o in GeneratorLtxVideoPipe.outputs()}
    assert outputs["latent"] == IOType.LATENT
    inputs = {i.name: i for i in GeneratorLtxVideoPipe.inputs()}
    assert inputs["initial_latent"].io_type == IOType.LATENT
    assert inputs["initial_latent"].required is False


def test_initial_latent_with_media_placements_no_longer_raises():
    """A stage-2 refine may now be paired with image-sourced keyframe
    conditioning -- build_context builds `prepared` from the conditions
    (at THIS call's own resolution) instead of raising."""
    cfg = _pipe(resolution="64x64", frames=17, media_placements=[
        {"source": "image", "index": 0, "frame": "first", "strength": 1.0, "role": "keyframe"},
    ])
    pipe_input = _pipe_input(images=[_pil_image()])
    pipe_input.input["initial_latent"] = [torch.zeros(1, 128, 3, 2, 2)]
    ctx = cfg.build_context(pipe_input)
    assert ctx.extra.has_conditions
    assert ctx.extra.prepared.mask.any()


def test_initial_latent_with_image_input_no_longer_raises():
    """Same as above via the convenience i2v default (no explicit
    media_placements, image[0] -> first-frame keyframe)."""
    pipe_input = _pipe_input(images=[_pil_image()])
    pipe_input.input["initial_latent"] = [torch.zeros(1, 128, 3, 2, 2)]
    ctx = _pipe(resolution="64x64", frames=17).build_context(pipe_input)
    assert ctx.extra.has_conditions
    assert ctx.extra.prepared.mask.any()


def test_initial_latent_with_reference_role_conditioning_raises():
    """role='reference' (IC-LoRA) semantics are tied to the distilled
    first-pass pipeline -- out of scope for a stage-2 refine."""
    pipe_input = _pipe_input(images=[_pil_image()])
    pipe_input.input["initial_latent"] = [torch.zeros(1, 128, 3, 2, 2)]
    cfg = _pipe(resolution="64x64", frames=17, media_placements=[
        {"source": "image", "index": 0, "role": "reference"},
    ])
    with pytest.raises(ValueError, match="role='reference'"):
        cfg.build_context(pipe_input)


def test_initial_latent_with_video_sourced_conditioning_raises():
    """Video-sourced conditioning (keyframe clips / IC-LoRA references)
    combined with a stage-2 refine is unvalidated -- rejected outright
    rather than silently mis-applied at a refined resolution."""
    pipe_input = _pipe_input()
    pipe_input.input["video"] = ["/fake/reference.mp4"]
    pipe_input.input["initial_latent"] = [torch.zeros(1, 128, 3, 2, 2)]
    with pytest.raises(ValueError, match="video-sourced"):
        _pipe(resolution="64x64", frames=17).build_context(pipe_input)


@patch("src.pipelines.pipes.generator.video_ltx.main.encode_frames_to_mp4", lambda frames, path, fps, audio=None: path)
@patch("src.pipelines.pipes.generator.video_ltx.main.denoise_prenoised")
def test_initial_latent_no_conditions_wholesale_replaces_base_tokens_mask_stays_zero(mock_denoise):
    """No media conditioning present: byte-identical to prior behavior --
    `tokens`/`clean` are wholesale REPLACED by the seed latent, mask stays
    all-zero (nothing clamped clean)."""
    captured = {}

    def fake_denoise(fwd, x, cond, uncond, **kw):
        captured["prepared_clean"] = fwd.clean.clone()
        captured["mask"] = fwd.mask.clone()
        return x

    mock_denoise.side_effect = fake_denoise

    # 64x64/32=2x2, frames=17 -> t_lat=3 -> base_tokens = 3*2*2 = 12
    seed_latent = torch.full((1, 128, 3, 2, 2), 0.5)
    pipe_input = _pipe_input()
    pipe_input.input["initial_latent"] = [seed_latent]
    _pipe(resolution="64x64", frames=17, refine_sigmas="0.9, 0.0").process(pipe_input, lambda o: None)

    assert torch.all(captured["mask"] == 0)  # no position clamped clean
    from src.pipelines.pipes.generator.video_ltx.conditioning import _pack
    assert torch.equal(captured["prepared_clean"], _pack(seed_latent))


def _bundle_with_encode_fill(fill: float):
    """Like `_bundle()` but with a VAE encode that returns a distinguishable
    constant, so keyframe-encoded tokens can be told apart from the
    upsampled-prior latent's own fill value in the merge tests below."""
    def encode(pixels):
        _, _, t, h, w = pixels.shape
        return torch.full((1, 128, (t - 1) // 8 + 1, h // 32, w // 32), fill)

    vae = SimpleNamespace(
        compute_dtype=torch.float32, move_to=lambda d: None, offload=lambda: None,
        module=SimpleNamespace(
            encode=encode,
            decode=lambda z: torch.zeros(1, 3, z.shape[2], z.shape[3] * 32, z.shape[4] * 32)),
    )
    dit = SimpleNamespace(compute_dtype=torch.float32, move_to=lambda d: None, offload=lambda: None,
                          module=SimpleNamespace(config=SimpleNamespace(causal_temporal_positioning=True)))
    return SimpleNamespace(dit=dit, vae=vae, spec=_FakeSpec(family="ltx"),
                           projections={}, audio_vae=None, vocoder=None)


@patch("src.pipelines.pipes.generator.video_ltx.main.encode_frames_to_mp4", lambda frames, path, fps, audio=None: path)
@patch("src.pipelines.pipes.generator.video_ltx.main.denoise_prenoised")
def test_initial_latent_with_conditions_merges_masked_positions_keep_keyframe(mock_denoise):
    """With media conditioning present, the base slice is MERGED, not
    replaced: masked (keyframe-anchored) positions keep the re-encoded
    keyframe tokens, unmasked positions take the upsampled prior latent;
    `clean` stays `prepared.clean` (the keyframe anchor); appended (extra)
    tokens survive untouched."""
    captured = {}

    def fake_denoise(fwd, x, cond, uncond, **kw):
        captured["x"] = x.clone()
        captured["mask"] = fwd.mask.clone()
        captured["clean"] = fwd.clean.clone()
        captured["s_base"] = fwd.s_base
        captured["n_extra"] = fwd.n_extra
        return x

    mock_denoise.side_effect = fake_denoise

    keyframe_fill, prior_fill = 9.0, 0.3
    bundle = _bundle_with_encode_fill(keyframe_fill)
    img = _pil_image()
    # images=[img, img] -> first-frame overwrite (base, masked) + last-frame
    # appended keyframe (extra tokens) -- exercises both the base-slice merge
    # and "extra tokens preserved" in one pass.
    pipe_input = _pipe_input(images=[img, img], bundle=bundle)
    seed_latent = torch.full((1, 128, 3, 2, 2), prior_fill)  # matches the base grid (t_lat=3,h_lat=2,w_lat=2)
    pipe_input.input["initial_latent"] = [seed_latent]
    # sigma0=0.0 -> mix_initial_noise's `x = noise*scaled + tokens*(1-scaled)`
    # collapses to `x == tokens` exactly everywhere (scaled=0), so the
    # captured sampler input directly reflects the merged `tokens` tensor.
    _pipe(resolution="64x64", frames=17, refine_sigmas="0.0, 0.0").process(pipe_input, lambda o: None)

    s_base, n_extra = captured["s_base"], captured["n_extra"]
    assert n_extra > 0, "the second image (last-frame) should append extra tokens"
    mask_base = captured["mask"][:, :s_base]
    assert mask_base.any() and not mask_base.all(), "first-frame overwrite must partially mask the base"
    m = mask_base.unsqueeze(-1)

    from src.pipelines.pipes.generator.video_ltx.conditioning import _pack
    packed_prior = _pack(seed_latent)

    x_base = captured["x"][:, :s_base]
    # Masked (keyframe) positions: kept the re-encoded keyframe value.
    assert torch.allclose(x_base * m, torch.full_like(packed_prior, keyframe_fill) * m)
    assert torch.allclose(captured["clean"][:, :s_base] * m, torch.full_like(packed_prior, keyframe_fill) * m)
    # Unmasked positions: the upsampled prior latent, untouched.
    unmasked = 1.0 - m
    assert torch.allclose(x_base * unmasked, packed_prior * unmasked)

    # Extra (appended keyframe) tokens: preserved exactly, mask stays at
    # whatever the conditioning builder gave them (all-1 for a full-strength
    # keyframe), completely unrelated to the seed latent's fill value.
    extra_mask = captured["mask"][:, s_base:]
    assert torch.all(extra_mask == 1.0)
    assert torch.allclose(captured["clean"][:, s_base:], torch.full((1, n_extra, 128), keyframe_fill))


@patch("src.pipelines.pipes.generator.video_ltx.main.encode_frames_to_mp4", lambda frames, path, fps, audio=None: path)
@patch("src.pipelines.pipes.generator.video_ltx.main.denoise_prenoised")
def test_quantity_two_second_seeds_merge_uses_pristine_tokens_not_first_seeds_output(mock_denoise):
    """`ctx.extra` (the `_VideoLtxCtx`) is SHARED across every seed of one
    process() call -- seed 1's masked-position merge input must be built from
    the PRISTINE keyframe tokens `build_context` produced, never from seed
    0's own merged output. At a fractional mask/strength this is NOT the
    same thing (the blend is not idempotent), so the bug only shows up with
    strength < 1.0 -- a binary (strength=1.0) mask would pass either way."""
    captured = []

    def fake_denoise(fwd, x, cond, uncond, **kw):
        captured.append((x.clone(), fwd.mask.clone(), fwd.s_base))
        return x

    mock_denoise.side_effect = fake_denoise

    keyframe_fill = 9.0
    bundle = _bundle_with_encode_fill(keyframe_fill)
    img = _pil_image()
    pipe_input = _pipe_input(images=[img], bundle=bundle)
    pipe_input.input["conditioning"] = [
        SimpleNamespace(embeds={"context": torch.ones(1, 4, 8)}, n_embeds=None) for _ in range(2)
    ]
    pipe_input.input["seed"] = [7, 8]
    prior_fill_0, prior_fill_1 = 0.2, 0.7
    seed_latent_0 = torch.full((1, 128, 3, 2, 2), prior_fill_0)
    seed_latent_1 = torch.full((1, 128, 3, 2, 2), prior_fill_1)
    pipe_input.input["initial_latent"] = [seed_latent_0, seed_latent_1]

    # sigma0=0.0 -> mix_initial_noise collapses to `x == tokens` exactly (see
    # the sibling merge test above), so the captured sampler input directly
    # reflects the merged `tokens` tensor for each seed.
    _pipe(
        resolution="64x64", frames=17, quantity=2, refine_sigmas="0.0, 0.0",
        media_placements=[{"source": "image", "index": 0, "frame": "first", "strength": 0.5}],
    ).process(pipe_input, lambda o: None)

    assert len(captured) == 2
    from src.pipelines.pipes.generator.video_ltx.conditioning import _pack
    x1, mask1, s_base = captured[1]
    m = mask1[:, :s_base].unsqueeze(-1)
    assert m.max() == 0.5 and m.min() == 0, "expected a fractional (0.5) mask at some, but not all, base positions"
    expected_1 = _pack(seed_latent_1) * (1.0 - m) + keyframe_fill * m
    assert torch.allclose(x1[:, :s_base], expected_1, atol=1e-5), (
        "seed 1's merge must use the pristine keyframe tokens, not seed 0's own "
        "already-merged output"
    )


@patch("src.pipelines.pipes.generator.video_ltx.main.encode_frames_to_mp4", lambda frames, path, fps, audio=None: path)
@patch("src.pipelines.pipes.generator.video_ltx.main.denoise_prenoised", lambda fwd, x, c, u, **k: x)
def test_initial_latent_guard_tolerates_extra_tokens_but_rejects_wrong_base_count():
    """The token-count guard must tolerate `n_extra > 0` (the appended
    keyframe/reference tokens don't come from the upsampled latent at all)
    while still rejecting a base-token-count mismatch."""
    bundle = _bundle_with_encode_fill(9.0)
    img = _pil_image()

    # Correct base count (12 = 3*2*2) with extras present (n_extra > 0 from
    # the appended last-frame keyframe) must NOT raise.
    pipe_input_ok = _pipe_input(images=[img, img], bundle=bundle)
    pipe_input_ok.input["initial_latent"] = [torch.full((1, 128, 3, 2, 2), 0.3)]
    _pipe(resolution="64x64", frames=17, refine_sigmas="0.0, 0.0").process(pipe_input_ok, lambda o: None)

    # Wrong base count still raises, extras or not.
    pipe_input_bad = _pipe_input(images=[img, img], bundle=bundle)
    pipe_input_bad.input["initial_latent"] = [torch.zeros(1, 128, 1, 1, 1)]
    with pytest.raises(ValueError, match="initial_latent token count"):
        _pipe(resolution="64x64", frames=17, refine_sigmas="0.0, 0.0").process(pipe_input_bad, lambda o: None)


def test_conditioning_built_at_own_resolution_matches_upsampled_latent_grid():
    """Stage-2 keyframes are re-VAE-encoded at the CALL's own (stage-2)
    height/width -- not upsampled from a smaller prior -- so
    `prepared.base_tokens` must match the packed token count of an
    upsampled latent at THAT same resolution (the shape-level parity the
    generate_one merge relies on)."""
    img = _pil_image()
    ctx = _pipe(resolution="128x128", frames=17).build_context(_pipe_input(images=[img]))
    p = ctx.extra.prepared
    t_lat, h_lat, w_lat = 3, 4, 4  # (17-1)//8+1, 128//32, 128//32
    assert p.base_tokens == t_lat * h_lat * w_lat

    from src.pipelines.pipes.generator.video_ltx.conditioning import _pack
    upsampled_latent = torch.zeros(1, 128, t_lat, h_lat, w_lat)
    assert _pack(upsampled_latent).shape[1] == p.base_tokens


@patch("src.pipelines.pipes.generator.video_ltx.main.encode_frames_to_mp4", lambda frames, path, fps, audio=None: path)
@patch("src.pipelines.pipes.generator.video_ltx.main.denoise_prenoised", lambda fwd, x, c, u, **k: x)
def test_initial_latent_without_refine_sigmas_raises_clear_error():
    seed_latent = torch.full((1, 128, 3, 2, 2), 0.5)
    pipe_input = _pipe_input()
    pipe_input.input["initial_latent"] = [seed_latent]
    with pytest.raises(ValueError, match="refine_sigmas' is empty"):
        _pipe(resolution="64x64", frames=17).process(pipe_input, lambda o: None)


@patch("src.pipelines.pipes.generator.video_ltx.main.encode_frames_to_mp4", lambda frames, path, fps, audio=None: path)
@patch("src.pipelines.pipes.generator.video_ltx.main.denoise_prenoised")
def test_refine_sigmas_parsed_verbatim_and_passed_to_denoise_prenoised(mock_denoise):
    captured = {}

    def fake_denoise(fwd, x, cond, uncond, **kw):
        captured["sigmas"] = kw["sigmas"]
        return x

    mock_denoise.side_effect = fake_denoise
    seed_latent = torch.full((1, 128, 3, 2, 2), 0.5)
    pipe_input = _pipe_input()
    pipe_input.input["initial_latent"] = [seed_latent]
    recipe = "0.909375, 0.725, 0.421875, 0.0"
    _pipe(resolution="64x64", frames=17, refine_sigmas=recipe).process(pipe_input, lambda o: None)
    # NOT forced to 1.0 (unlike manual_sigmas) -- the whole point of this knob.
    assert torch.allclose(captured["sigmas"], torch.tensor([0.909375, 0.725, 0.421875, 0.0]))


@patch("src.pipelines.pipes.generator.video_ltx.main.encode_frames_to_mp4", lambda frames, path, fps, audio=None: path)
@patch("src.pipelines.pipes.generator.video_ltx.main.denoise_prenoised", lambda fwd, x, c, u, **k: x)
def test_initial_latent_wrong_token_count_raises_clear_error():
    bad_latent = torch.zeros(1, 128, 1, 1, 1)  # wrong resolution/frames
    pipe_input = _pipe_input()
    pipe_input.input["initial_latent"] = [bad_latent]
    with pytest.raises(ValueError, match="initial_latent token count"):
        _pipe(resolution="64x64", frames=17).process(pipe_input, lambda o: None)


# -- temporal geometry follows the latent, not a re-snapped config (live
# repro "initial_latent token count 7680 vs base 8160", a
# 17-vs-16 t_lat divergence between the config's own re-snap and the actual
# stage-1 latent) ------------------------------------------------------------

def test_t_lat_unchanged_without_initial_latent():
    """Non-refine path: byte-identical -- t_lat still comes from the snapped
    config frames, never from a latent (there isn't one)."""
    ctx = _pipe(resolution="64x64", frames=17).build_context(_pipe_input())
    assert ctx.extra.t_lat == 3          # (17-1)//8+1
    assert ctx.extra.frames == 17
    assert ctx.extra.prepared.base_tokens == 3 * 2 * 2


@patch("src.pipelines.pipes.generator.video_ltx.main.encode_frames_to_mp4", lambda frames, path, fps, audio=None: path)
@patch("src.pipelines.pipes.generator.video_ltx.main.denoise_prenoised", lambda fwd, x, c, u, **k: x)
def test_initial_latent_temporal_mismatch_follows_latent_plain_refine():
    """Reproduces the report at small scale: config frames=17 snaps to
    t_lat=3, but the actual seed latent is t_lat=2 -- must NOT raise; the
    geometry (and base_tokens) follow the latent."""
    seed_latent = torch.zeros(1, 128, 2, 2, 2)   # t_lat=2, not the config's t_lat=3
    pipe_input = _pipe_input()
    pipe_input.input["initial_latent"] = [seed_latent]
    ctx = _pipe(resolution="64x64", frames=17, refine_sigmas="0.9, 0.0").build_context(pipe_input)
    assert ctx.extra.t_lat == 2
    assert ctx.extra.frames == 9         # (2-1)*8+1, reconciled from the latent
    assert ctx.extra.prepared.base_tokens == 2 * 2 * 2
    # And the full per-seed path (packed-token guard) accepts it too.
    _pipe(resolution="64x64", frames=17, refine_sigmas="0.9, 0.0").process(pipe_input, lambda o: None)


@patch("src.pipelines.pipes.generator.video_ltx.main.encode_frames_to_mp4", lambda frames, path, fps, audio=None: path)
@patch("src.pipelines.pipes.generator.video_ltx.main.denoise_prenoised", lambda fwd, x, c, u, **k: x)
def test_initial_latent_temporal_mismatch_follows_latent_with_i2v_conditions():
    """Same mismatch, but with image-sourced (i2v) media conditioning
    attached -- the with-conditions path (`prepare_ltx_conditions`) must also
    build against the latent's own t_lat, not config's."""
    img = _pil_image()
    seed_latent = torch.zeros(1, 128, 2, 2, 2)
    pipe_input = _pipe_input(images=[img])
    pipe_input.input["initial_latent"] = [seed_latent]
    ctx = _pipe(resolution="64x64", frames=17, refine_sigmas="0.9, 0.0").build_context(pipe_input)
    assert ctx.extra.has_conditions
    assert ctx.extra.t_lat == 2
    assert ctx.extra.prepared.base_tokens == 2 * 2 * 2
    _pipe(resolution="64x64", frames=17, refine_sigmas="0.9, 0.0").process(pipe_input, lambda o: None)


def test_initial_latent_mismatched_seed_geometries_raises_clear_error():
    """Multiple seeds share one latent geometry (build_context runs once per
    generation, not per seed) -- disagreeing per-seed latent frame counts
    must be rejected loudly rather than silently building against seed 0's
    shape and crashing (or silently mis-conditioning) on the others."""
    pipe_input = _pipe_input()
    pipe_input.input["seed"] = [1, 2]
    pipe_input.input["initial_latent"] = [
        torch.zeros(1, 128, 2, 2, 2),
        torch.zeros(1, 128, 3, 2, 2),
    ]
    with pytest.raises(ValueError, match="same latent temporal geometry"):
        _pipe(resolution="64x64", frames=17, refine_sigmas="0.9, 0.0").build_context(pipe_input)


@patch("src.pipelines.pipes.generator.video_ltx.main.encode_frames_to_mp4", lambda frames, path, fps, audio=None: path)
@patch("src.pipelines.pipes.generator.video_ltx.main.denoise_prenoised", lambda fwd, x, c, u, **k: x)
def test_initial_latent_wrong_spatial_dims_still_raises():
    """Frame count is now derived from the latent itself, so a genuine
    mismatch must be spatial: h/w that doesn't match the configured
    'resolution'. The error message must no longer blame frames/config."""
    bad_latent = torch.zeros(1, 128, 3, 1, 1)   # t_lat=3 matches config; h/w=1x1 does not (config wants 2x2)
    pipe_input = _pipe_input()
    pipe_input.input["initial_latent"] = [bad_latent]
    with pytest.raises(ValueError, match="spatial resolution"):
        _pipe(resolution="64x64", frames=17, refine_sigmas="0.9, 0.0").process(pipe_input, lambda o: None)


def test_last_frame_keyframe_placement_lands_on_latent_derived_last_slot():
    """Config frames snap to t_lat=4, but the actual seed latent is t_lat=2 --
    a 'last'-frame keyframe placement must resolve against the LATENT's own
    t_lat (true last index 1, appended at pixel_frame_idx 1), not the stale
    config t_lat (index 3, pixel_frame_idx 17) -- see
    `_resolve_latent_index`/`prepare_ltx_conditions`'s `latent_idx % t_lat`."""
    img = _pil_image()
    seed_latent = torch.zeros(1, 128, 2, 2, 2)   # t_lat=2 (frames reconciles to 9)
    cfg = _pipe(resolution="64x64", frames=25, media_placements=[
        {"source": "image", "index": 0, "frame": "last", "strength": 1.0, "role": "keyframe"},
    ])
    pipe_input = _pipe_input(images=[img])
    pipe_input.input["initial_latent"] = [seed_latent]
    ctx = cfg.build_context(pipe_input)

    assert ctx.extra.t_lat == 2
    assert ctx.extra.prepared.n_extra > 0
    temporal_start = ctx.extra.prepared.extra_coords[0, 0, 0, 0].item()
    assert temporal_start == 1.0   # true last slot of t_lat=2, NOT 17.0 (t_lat=4's stale last slot)


@patch("src.pipelines.pipes.generator.video_ltx.main.encode_frames_to_mp4", lambda frames, path, fps, audio=None: path)
@patch("src.pipelines.pipes.generator.video_ltx.main.denoise_prenoised", lambda fwd, x, c, u, **k: x)
def test_initial_latent_falls_back_to_last_when_fewer_than_quantity():
    only_latent = torch.full((1, 128, 3, 2, 2), 0.3)
    cond = [SimpleNamespace(embeds={"context": torch.ones(1, 4, 8)}, n_embeds=None) for _ in range(2)]
    pipe_input = PipeInput(input={
        "model": _bundle(), "conditioning": cond, "seed": [1, 2], "image": [],
        "initial_latent": [only_latent],
    })
    result = _pipe(resolution="64x64", frames=17, quantity=2, refine_sigmas="0.9, 0.0").process(pipe_input, lambda o: None)
    assert len(result.output["video"]) == 2  # both seeds succeeded (no shape-mismatch raise)


@patch("src.pipelines.pipes.generator.video_ltx.main.denoise_prenoised", lambda fwd, x, c, u, **k: x)
@patch("src.pipelines.pipes.generator.video_ltx.main.encode_frames_to_mp4")
@patch("src.pipelines.pipes.generator.video_ltx.main._decode_video")
def test_decode_false_skips_decode_and_emits_latent_output(mock_decode, mock_encode):
    seed_latent = torch.full((1, 128, 3, 2, 2), 0.5)
    pipe_input = _pipe_input()
    pipe_input.input["initial_latent"] = [seed_latent]
    # sigma0=0.0 (fully clean, no noise mixed in) so the round trip is exact --
    # see mix_initial_noise: scaled = (1-mask)*sigma0 = 0 at mask=0, sigma0=0.
    # (refine_sigmas, NOT manual_sigmas -- the latter would be forced to 1.0.)
    result = _pipe(resolution="64x64", frames=17, decode=False,
                   refine_sigmas="0.0, 0.0").process(pipe_input, lambda o: None)
    mock_decode.assert_not_called()
    mock_encode.assert_not_called()
    assert result.output["video"] == []
    assert len(result.output["latent"]) == 1
    assert torch.equal(result.output["latent"][0], seed_latent)
    assert result.output["audio"] == [None]  # audio not requested -- nothing to hand off


# -- audio decode is independent of `decode` -----------

@patch("src.pipelines.pipes.generator.video_ltx.main.denoise_prenoised", lambda fwd, x, c, u, **k: x)
@patch("src.pipelines.pipes.generator.video_ltx.main.encode_frames_to_mp4")
@patch("src.pipelines.pipes.generator.video_ltx.main._decode_video")
@patch("src.pipelines.pipes.generator.video_ltx.main.decode_generated_audio")
def test_decode_false_still_decodes_generated_audio_for_stage2_handoff(mock_decode_audio, mock_decode_video, mock_encode):
    """A stage-1 call with decode=false (two-stage upscale) must still
    produce a finished `audio` output when audio generation was requested --
    the video VAE round trip is skipped, but the independent audio VAE +
    vocoder decode is not."""
    sentinel_track = object()
    mock_decode_audio.return_value = sentinel_track
    bundle = _bundle(audio=True)
    seed_latent = torch.full((1, 128, 3, 2, 2), 0.5)
    pipe_input = _pipe_input(bundle=bundle)
    pipe_input.input["initial_latent"] = [seed_latent]
    result = _pipe(resolution="64x64", frames=17, decode=False, audio=True, audio_source="generate",
                   refine_sigmas="0.0, 0.0").process(pipe_input, lambda o: None)
    mock_decode_audio.assert_called_once()
    mock_decode_video.assert_not_called()
    mock_encode.assert_not_called()
    assert result.output["video"] == []
    assert result.output["audio"] == [sentinel_track]


@patch("src.pipelines.pipes.generator.video_ltx.main.denoise_prenoised", lambda fwd, x, c, u, **k: x)
@patch("src.pipelines.pipes.generator.video_ltx.main.decode_generated_audio")
def test_audio_passthrough_muxes_verbatim_without_regenerating(mock_decode_audio):
    """A stage-2 refine call configured with audio_source='passthrough' must
    mux the upstream (already-decoded) track exactly as given, and must NOT
    call decode_generated_audio -- no re-generation, no re-noising."""
    captured = {}

    def fake_encode(frames, path, fps, audio=None):
        captured["audio"] = audio
        return path

    upstream_track = object()
    seed_latent = torch.full((1, 128, 3, 2, 2), 0.5)
    pipe_input = _pipe_input()
    pipe_input.input["initial_latent"] = [seed_latent]
    pipe_input.input["audio"] = [upstream_track]
    with patch("src.pipelines.pipes.generator.video_ltx.main.encode_frames_to_mp4", fake_encode):
        result = _pipe(resolution="64x64", frames=17, audio=True, audio_source="passthrough",
                       refine_sigmas="0.0, 0.0").process(pipe_input, lambda o: None)
    mock_decode_audio.assert_not_called()
    assert captured["audio"] is upstream_track
    assert result.output["audio"] == [upstream_track]


@patch("src.pipelines.pipes.generator.video_ltx.main.denoise_prenoised", lambda fwd, x, c, u, **k: x)
@patch("src.pipelines.pipes.generator.video_ltx.main.encode_frames_to_mp4", lambda frames, path, fps, audio=None: path)
def test_decode_true_default_audio_output_is_none_list_when_audio_off():
    result = _pipe(resolution="64x64", frames=17).process(_pipe_input(), lambda o: None)
    assert result.output["audio"] == [None]


@patch("src.pipelines.pipes.generator.video_ltx.main.denoise_prenoised", lambda fwd, x, c, u, **k: x)
@patch("src.pipelines.pipes.generator.video_ltx.main.encode_frames_to_mp4")
def test_decode_false_emits_no_gallery(mock_encode):
    from src.pipelines.outputs import GalleryGenerationOutput

    emitted = []
    _pipe(resolution="64x64", frames=17, decode=False).process(_pipe_input(), lambda o: emitted.append(o))
    assert not any(isinstance(o, GalleryGenerationOutput) for o in emitted)


@patch("src.pipelines.pipes.generator.video_ltx.main.encode_frames_to_mp4", lambda frames, path, fps, audio=None: path)
@patch("src.pipelines.pipes.generator.video_ltx.main.denoise_prenoised", lambda fwd, x, c, u, **k: x)
def test_decode_true_default_output_has_empty_latent_list():
    result = _pipe(resolution="64x64", frames=17).process(_pipe_input(), lambda o: None)
    assert result.output["latent"] == []
    assert len(result.output["video"]) == 1


# -- stage2_loras (LTX-2 two-stage refine, Enhance-tab LoRA picker) --------

def test_stage2_loras_default_empty_and_declared_in_configuration():
    assert GeneratorLtxVideoPipe.get_default_config()["stage2_loras"] == []
    spec = next(s for s in GeneratorLtxVideoPipe.configuration() if s.name == "stage2_loras")
    assert spec.default == []
    assert spec.required is False


def test_stage2_loras_without_initial_latent_ignored_with_warning(caplog):
    """The config only means something on a refine pass -- set without
    'initial_latent' connected, it's ignored (build_context never even
    touches the (fake) LoRA file path) rather than silently applied to a
    plain generation."""
    pipe_input = _pipe_input()
    with caplog.at_level("WARNING"):
        ctx = _pipe(
            resolution="64x64", frames=17,
            stage2_loras=[{"file_path": "/fake/distilled.safetensors", "weight": 1.0}],
        ).build_context(pipe_input)
    assert ctx.extra.stage2_lora_stack == []
    assert any("stage2_loras" in r.message for r in caplog.records)


def test_stage2_loras_empty_stays_empty_stack_no_warning(caplog):
    with caplog.at_level("WARNING"):
        ctx = _pipe(resolution="64x64", frames=17).build_context(_pipe_input())
    assert ctx.extra.stage2_lora_stack == []
    assert not any("stage2_loras" in r.message for r in caplog.records)


@patch("src.pipelines.pipes.generator.video_ltx.main._load_lora_stack")
def test_stage2_loras_loaded_once_in_build_context_on_a_refine_call(mock_load):
    sentinel_stack = [({"lora.weight": torch.zeros(1)}, 1.0)]
    mock_load.return_value = sentinel_stack
    pipe_input = _pipe_input()
    pipe_input.input["initial_latent"] = [torch.zeros(1, 128, 3, 2, 2)]
    ctx = _pipe(
        resolution="64x64", frames=17, refine_sigmas="0.9, 0.0",
        stage2_loras=[{"file_path": "/fake/distilled.safetensors", "weight": 1.0}],
    ).build_context(pipe_input)
    mock_load.assert_called_once_with([{"file_path": "/fake/distilled.safetensors", "weight": 1.0}])
    assert ctx.extra.stage2_lora_stack is sentinel_stack


@patch("src.pipelines.pipes.generator.video_ltx.main.encode_frames_to_mp4", lambda frames, path, fps, audio=None: path)
@patch("src.pipelines.pipes.generator.video_ltx.main._load_lora_stack")
def test_stage2_loras_wraps_only_the_sampling_call_on_a_refine_call(mock_load):
    """The context manager must wrap the denoise_prenoised call itself (the
    whole per-step sampling loop) -- entered before, exited after -- not the
    VAE decode or anything else in generate_one."""
    sentinel_stack = [({"lora.weight": torch.zeros(1)}, 1.0)]
    mock_load.return_value = sentinel_stack
    calls = []

    @contextmanager
    def fake_ctx(module, stack):
        calls.append(("enter", stack))
        yield
        calls.append(("exit", stack))

    def fake_denoise(fwd, x, cond, uncond, **kw):
        calls.append(("denoise",))
        return x

    seed_latent = torch.full((1, 128, 3, 2, 2), 0.5)
    pipe_input = _pipe_input()
    pipe_input.input["initial_latent"] = [seed_latent]
    with patch("src.pipelines.pipes.generator.video_ltx.main.temporarily_applied_loras", fake_ctx), \
         patch("src.pipelines.pipes.generator.video_ltx.main.denoise_prenoised", fake_denoise):
        _pipe(
            resolution="64x64", frames=17, refine_sigmas="0.9, 0.0",
            stage2_loras=[{"file_path": "/fake/distilled.safetensors", "weight": 1.0}],
        ).process(pipe_input, lambda o: None)

    assert calls == [("enter", sentinel_stack), ("denoise",), ("exit", sentinel_stack)]


@patch("src.pipelines.pipes.generator.video_ltx.main.encode_frames_to_mp4", lambda frames, path, fps, audio=None: path)
@patch("src.pipelines.pipes.generator.video_ltx.main.denoise_prenoised", lambda fwd, x, c, u, **k: x)
def test_stage2_loras_wraps_with_empty_stack_on_a_plain_generation(caplog):
    """No 'initial_latent' -> ignored-with-warning path -- the context
    manager is still entered (uniform code path) but with an empty stack, so
    it no-ops (see the platform-level empty-stack tests)."""
    calls = []

    @contextmanager
    def fake_ctx(module, stack):
        calls.append(stack)
        yield

    with patch("src.pipelines.pipes.generator.video_ltx.main.temporarily_applied_loras", fake_ctx):
        with caplog.at_level("WARNING"):
            _pipe(
                resolution="64x64", frames=17,
                stage2_loras=[{"file_path": "/fake/distilled.safetensors", "weight": 1.0}],
            ).process(_pipe_input(), lambda o: None)

    assert calls == [[]]


# -- TE eviction --------------------------------------------------------------
# This pipe never touches the TE itself (conditioning is already-encoded by
# `prompt_encoder`, upstream of every LTX preset pipeline) -- see
# `release_idle_te`'s docstring (generator/txt2vid_ltx/main.py). Mirrors
# generator/qwen's and generator/krea2's identical TE-eviction test suites.

def test_te_eviction_fires_on_build_context():
    models = _FakeModelsService()
    bundle = _bundle(te_cache_key="native/te/gemma3.safetensors")
    _pipe().build_context(_pipe_input(bundle=bundle, models=models))
    assert models.evict_calls == ["native/te/gemma3.safetensors"]


def test_te_eviction_noop_without_cache_key():
    models = _FakeModelsService()
    _pipe().build_context(_pipe_input(models=models))  # default bundle has te_cache_key=None
    assert models.evict_calls == []


def test_te_eviction_noop_without_models_service():
    bundle = _bundle(te_cache_key="native/te/gemma3.safetensors")
    # No "MODELS" key in pipe_input at all -- must not raise.
    ctx = _pipe().build_context(_pipe_input(bundle=bundle))
    assert ctx is not None


def test_te_eviction_failure_does_not_raise():
    models = _FakeModelsService(raise_on_evict=True)
    bundle = _bundle(te_cache_key="native/te/gemma3.safetensors")
    ctx = _pipe().build_context(_pipe_input(bundle=bundle, models=models))
    assert ctx is not None
    assert models.evict_calls == ["native/te/gemma3.safetensors"]  # eviction was attempted


def test_te_eviction_fires_once_per_build_context_call_not_per_seed():
    models = _FakeModelsService()
    bundle = _bundle(te_cache_key="native/te/gemma3.safetensors")
    cond = [SimpleNamespace(embeds={"context": torch.ones(1, 4, 8)}, n_embeds=None) for _ in range(3)]
    pipe_input = PipeInput(input={"model": bundle, "conditioning": cond, "seed": [1, 2, 3], "MODELS": models})
    _pipe().build_context(pipe_input)
    assert models.evict_calls == ["native/te/gemma3.safetensors"]  # not 3x


def test_te_eviction_also_wired_for_stage2_style_call_with_initial_latent():
    """stage2 (a second node instance reading the SAME bundle, with
    `initial_latent` connected) calls its own build_context too -- a second
    eviction attempt for the SAME key is a harmless no-op (`evict_dead_weight`
    reports False for an already-gone entry), so this must not raise either."""
    models = _FakeModelsService(evict_result=False)  # already evicted by stage 1
    bundle = _bundle(te_cache_key="native/te/gemma3.safetensors")
    pipe_input = _pipe_input(bundle=bundle, models=models)
    pipe_input.input["initial_latent"] = [torch.zeros(1, 128, 3, 2, 2)]
    _pipe(resolution="64x64", frames=17, refine_sigmas="0.9, 0.0").build_context(pipe_input)
    assert models.evict_calls == ["native/te/gemma3.safetensors"]


# -- _VideoLtxCtx.release_gpu -------------------------------------------------

class _FakeOffloadable:
    def __init__(self, raise_on_offload=False):
        self.offloaded = 0
        self._raise = raise_on_offload

    def offload(self):
        self.offloaded += 1
        if self._raise:
            raise RuntimeError("cuda error")


def _make_video_ltx_ctx(bundle):
    return _VideoLtxCtx(
        bundle=bundle, sampling_settings={}, conditioning=[], prepared=None,
        steps=1, cfg=1.0, sampler="euler", width=8, height=8, frames=1, fps=25.0,
        device="cuda", dtype=torch.bfloat16,
    )


class TestVideoLtxCtxReleaseGpu:
    """`_VideoLtxCtx.release_gpu()` is what makes `BaseGeneratorPipe`'s generic
    error-path cleanup fire for this pipe: `ctx.extra` is this dataclass
    directly, so it must define its own `release_gpu()` -- covers a mid-
    generation failure (e.g. a VAE decode failure) that would otherwise leave
    the DiT + video VAE + audio VAE + vocoder resident."""

    def test_offloads_dit_video_vae_audio_vae_and_vocoder(self):
        dit, vae, audio_vae, vocoder = (_FakeOffloadable() for _ in range(4))
        bundle = SimpleNamespace(dit=dit, vae=vae, audio_vae=audio_vae, vocoder=vocoder)
        _make_video_ltx_ctx(bundle).release_gpu()
        assert dit.offloaded == 1
        assert vae.offloaded == 1
        assert audio_vae.offloaded == 1
        assert vocoder.offloaded == 1

    def test_missing_audio_vae_and_vocoder_are_skipped(self):
        # A bundle built without audio: model_loader/ltx's `audio: false` --
        # audio_vae/vocoder are None, must not be offloaded.
        dit, vae = _FakeOffloadable(), _FakeOffloadable()
        bundle = SimpleNamespace(dit=dit, vae=vae, audio_vae=None, vocoder=None)
        _make_video_ltx_ctx(bundle).release_gpu()  # must not raise
        assert dit.offloaded == 1
        assert vae.offloaded == 1

    def test_never_raises_when_an_offload_fails(self):
        dit = _FakeOffloadable(raise_on_offload=True)
        vae, audio_vae, vocoder = (_FakeOffloadable() for _ in range(3))
        bundle = SimpleNamespace(dit=dit, vae=vae, audio_vae=audio_vae, vocoder=vocoder)
        _make_video_ltx_ctx(bundle).release_gpu()  # must not raise
        assert vae.offloaded == 1
        assert audio_vae.offloaded == 1
        assert vocoder.offloaded == 1
