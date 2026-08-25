"""Tests for the generator/video_minimax_h3 pipe: config validation, the
packed-sequence forward wrapper (pack/unpack roundtrip + condition-row
freeze), audio-source resolution, and mux argument construction -- tiny fake
bundles, no real weights, CPU-only."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest
import torch

from src.pipelines.pipes._shared.generation.generator_base import GeneratorContext
from src.pipelines.pipes._shared.generation.progress import ProgressEmitter
from src.pipelines.pipes._shared.media.video_encode import AudioTrack
from src.pipelines.pipes.generator.video_minimax_h3.conditioning import (
    ReferenceConditioning,
    ReferenceMedia,
    normalize_reference_image,
    prepare_reference_conditioning,
)
from src.pipelines.pipes.generator.video_minimax_h3.layout import (
    TEXT_TAG,
    ReferenceBlock,
    build_packed_sequence,
    build_ref2va_packed_sequence,
    build_row_timesteps,
)
from src.pipelines.pipes.generator.video_minimax_h3.main import (
    GeneratorMinimaxH3Pipe,
    H3_INNER_DIM,
    _MiniMaxH3Ctx,
    _MiniMaxH3Forward,
    build_step_cache,
    validate_minimax_h3_config,
)
from src.platform.runtime.native.errors import SamplingCancelled
from src.platform.runtime.native.sampling.step_cache import FirstBlockCache
from src.pipelines.pipes.generator.video_minimax_h3.schedule import (
    AUDIO_SHIFT,
    VIDEO_SHIFT,
    build_sigma_schedule,
    euler_step,
)

PATCH = (1, 2, 2)


# -- config validation -----------------------------------------------------

def test_validate_config_accepts_defaults():
    validate_minimax_h3_config({}, pipe_id="t")


@pytest.mark.parametrize("anchors", [["middle"], ["first", "middle"]])
def test_validate_config_rejects_invalid_anchor(anchors):
    with pytest.raises(ValueError):
        validate_minimax_h3_config({"keyframe_anchors": anchors}, pipe_id="t")


def test_validate_config_rejects_too_many_anchors():
    with pytest.raises(ValueError):
        validate_minimax_h3_config({"keyframe_anchors": ["first", "last", "first"]}, pipe_id="t")


def test_validate_config_rejects_duplicate_anchor():
    with pytest.raises(ValueError):
        validate_minimax_h3_config({"keyframe_anchors": ["first", "first"]}, pipe_id="t")


def test_validate_config_accepts_valid_fl2va_anchors():
    validate_minimax_h3_config({"keyframe_anchors": ["first", "last"]}, pipe_id="t")
    validate_minimax_h3_config({"keyframe_anchors": ["last"]}, pipe_id="t")


@pytest.mark.parametrize("source", ["generate", "file", "passthrough"])
def test_validate_config_accepts_valid_audio_source(source):
    validate_minimax_h3_config({"audio_source": source}, pipe_id="t")


def test_validate_config_rejects_invalid_audio_source():
    with pytest.raises(ValueError):
        validate_minimax_h3_config({"audio_source": "bogus"}, pipe_id="t")


# -- build_context input validation (runtime, needs pipe_input) -------------

def test_build_context_rejects_non_minimax_h3_family():
    pipe = GeneratorMinimaxH3Pipe(GeneratorMinimaxH3Pipe.get_default_config())
    bundle = SimpleNamespace(spec=SimpleNamespace(family="ltx", variant="whatever"))
    with pytest.raises(ValueError, match="not a MiniMax-H3"):
        pipe.build_context(_pipe_input(model=bundle, conditioning=[]))


def test_build_context_rejects_file_audio_source_without_audio_input():
    pipe = GeneratorMinimaxH3Pipe({**GeneratorMinimaxH3Pipe.get_default_config(), "audio_source": "file"})
    bundle = _fake_bundle()
    with pytest.raises(ValueError, match="audio_source"):
        pipe.build_context(_pipe_input(model=bundle, conditioning=[_fake_conditioning(3)]))


def test_build_context_rejects_more_anchors_than_images():
    pipe = GeneratorMinimaxH3Pipe({**GeneratorMinimaxH3Pipe.get_default_config(), "keyframe_anchors": ["first", "last"]})
    bundle = _fake_bundle()
    with pytest.raises(ValueError, match="keyframe_anchors"):
        pipe.build_context(_pipe_input(model=bundle, conditioning=[_fake_conditioning(3)], image=["only_one"]))


# -- ref2va: reference_images mutual exclusion / limits ----------------------

def test_build_context_rejects_reference_images_with_fl2va_image():
    pipe = GeneratorMinimaxH3Pipe(GeneratorMinimaxH3Pipe.get_default_config())
    bundle = _fake_bundle()
    with pytest.raises(ValueError, match="mutually exclusive"):
        pipe.build_context(_pipe_input(
            model=bundle, conditioning=[_fake_conditioning(3)],
            image=["a_keyframe"], reference_images=["a_reference"],
        ))


def test_build_context_rejects_reference_images_with_configured_keyframe_anchors():
    pipe = GeneratorMinimaxH3Pipe({**GeneratorMinimaxH3Pipe.get_default_config(), "keyframe_anchors": ["first"]})
    bundle = _fake_bundle()
    with pytest.raises(ValueError, match="mutually exclusive"):
        pipe.build_context(_pipe_input(
            model=bundle, conditioning=[_fake_conditioning(3)], reference_images=["a_reference"],
        ))


def test_build_context_accepts_reference_images_alone():
    pipe = GeneratorMinimaxH3Pipe({
        **GeneratorMinimaxH3Pipe.get_default_config(),
        "references": [{"path": "ref1"}, {"path": "ref2"}],
    })
    bundle = _fake_bundle()
    ctx = pipe.build_context(_pipe_input(
        model=bundle, conditioning=[_fake_conditioning(3)],
        reference_images=[_reference_image(), _reference_image()],
    ))
    assert [reference.kind for reference in ctx.extra.references] == ["image", "image"]
    assert ctx.extra.keyframe_images == []
    assert ctx.extra.keyframe_anchors == ()


def test_build_context_rejects_too_many_reference_images():
    pipe = GeneratorMinimaxH3Pipe(GeneratorMinimaxH3Pipe.get_default_config())
    bundle = _fake_bundle()
    with pytest.raises(ValueError, match="at most"):
        pipe.build_context(_pipe_input(
            model=bundle, conditioning=[_fake_conditioning(3)], reference_images=["r"] * 10,
        ))


def test_build_context_default_has_no_references():
    pipe = GeneratorMinimaxH3Pipe(GeneratorMinimaxH3Pipe.get_default_config())
    bundle = _fake_bundle()
    ctx = pipe.build_context(_pipe_input(model=bundle, conditioning=[_fake_conditioning(3)]))
    assert ctx.extra.references == ()


# -- ref2va: 'references' config cross-validated against 'reference_images' --

def test_build_context_rejects_a_shorter_references_field_than_loaded_images():
    pipe = GeneratorMinimaxH3Pipe({
        **GeneratorMinimaxH3Pipe.get_default_config(), "references": [{"path": "ref1"}],
    })
    bundle = _fake_bundle()
    with pytest.raises(ValueError, match="drifted apart"):
        pipe.build_context(_pipe_input(
            model=bundle, conditioning=[_fake_conditioning(3)], reference_images=["ref1", "ref2"],
        ))


def test_build_context_rejects_a_longer_references_field_than_loaded_images():
    pipe = GeneratorMinimaxH3Pipe({
        **GeneratorMinimaxH3Pipe.get_default_config(),
        "references": [{"path": "ref1"}, {"path": "ref2"}],
    })
    bundle = _fake_bundle()
    with pytest.raises(ValueError, match="drifted apart"):
        pipe.build_context(_pipe_input(
            model=bundle, conditioning=[_fake_conditioning(3)], reference_images=["ref1"],
        ))


def test_build_context_rejects_references_declared_with_no_images_loaded():
    pipe = GeneratorMinimaxH3Pipe({
        **GeneratorMinimaxH3Pipe.get_default_config(), "references": [{"path": "ref1"}],
    })
    bundle = _fake_bundle()
    with pytest.raises(ValueError, match="drifted apart"):
        pipe.build_context(_pipe_input(model=bundle, conditioning=[_fake_conditioning(3)]))


def test_build_context_accepts_matching_references_and_reference_images():
    pipe = GeneratorMinimaxH3Pipe({
        **GeneratorMinimaxH3Pipe.get_default_config(),
        "references": [{"path": "ref1", "label": "subject"}, {"path": "ref2"}],
    })
    bundle = _fake_bundle()
    ctx = pipe.build_context(_pipe_input(
        model=bundle, conditioning=[_fake_conditioning(3)],
        reference_images=[_reference_image(), _reference_image()],
    ))
    assert len(ctx.extra.references) == 2


def test_build_context_rejects_references_with_a_continuing_director_segment():
    """`_director_document(2)` defaults segment 1 to sub_type 'chain' -- a
    refs-conditioned run refuses continuation outright (windows.py's module
    docstring, "ref2va Director runs are hard-cut-only"), naming the segment
    rather than blanket-refusing every Director document."""
    pipe = GeneratorMinimaxH3Pipe({
        **GeneratorMinimaxH3Pipe.get_default_config(), "document": _director_document(2),
        "references": [{"path": "a_reference"}],
    })
    bundle = _fake_bundle()
    with pytest.raises(ValueError, match="seg-1.*continues the previous window"):
        pipe.build_context(_pipe_input(
            model=bundle, conditioning=[_fake_conditioning(3)], reference_images=["a_reference"],
        ))


def test_build_context_accepts_references_with_a_hard_cut_director_document():
    """The companion of the test above: a document whose segments are all
    cuts (no 'chain', no keyframe) is a supported refs-conditioned Director
    run and must build a context carrying both the plan and the references."""
    document = _director_document(2, sub_types={0: "t2v", 1: "t2v"})
    pipe = GeneratorMinimaxH3Pipe({
        **GeneratorMinimaxH3Pipe.get_default_config(), "document": document,
        "references": [{"path": "a_reference"}],
    })
    bundle = _fake_bundle()
    ctx = pipe.build_context(_pipe_input(
        model=bundle, conditioning=[_fake_conditioning(3), _fake_conditioning(3)],
        reference_images=[_reference_image()],
    ))
    assert ctx.extra.plan is not None
    assert len(ctx.extra.plan.windows) == 2
    assert len(ctx.extra.references) == 1


# -- ref2va + Video Director: hard-cut-only validation -----------------------

def test_validate_refs_director_plan_names_the_continuing_segment():
    plan = build_director_plan(_director_document(2), default_seed=-1)
    with pytest.raises(DirectorPlanError, match="seg-1.*continues the previous window"):
        GeneratorMinimaxH3Pipe._validate_refs_director_plan(plan, num_references=1)


def test_validate_refs_director_plan_names_the_segment_with_its_own_keyframe():
    document = _director_document(1)
    document["media"] = [{"role": "keyframe", "at": 0.0, "segment_id": None,
                          "strength": 1.0, "media": {"path": "/k.png", "type": "image"}}]
    document["media_images"] = ["/k.png"]
    document["media_placements"] = [
        {"source": "image", "index": 0, "frame": 0, "strength": 1.0, "role": "keyframe"},
    ]
    plan = build_director_plan(document, default_seed=-1)
    assert plan.windows[0].keyframes
    with pytest.raises(DirectorPlanError, match="seg-0.*its own Director keyframe"):
        GeneratorMinimaxH3Pipe._validate_refs_director_plan(plan, num_references=1)


def test_validate_refs_director_plan_rejects_an_empty_reference_selection():
    from dataclasses import replace

    plan = build_director_plan(_director_document(1), default_seed=-1)
    plan = replace(plan, windows=(replace(plan.windows[0], reference_indices=()),))
    with pytest.raises(DirectorPlanError, match="empty"):
        GeneratorMinimaxH3Pipe._validate_refs_director_plan(plan, num_references=2)


def test_validate_refs_director_plan_rejects_an_out_of_range_reference_index():
    from dataclasses import replace

    plan = build_director_plan(_director_document(1), default_seed=-1)
    plan = replace(plan, windows=(replace(plan.windows[0], reference_indices=(0, 5)),))
    with pytest.raises(DirectorPlanError, match="out of range"):
        GeneratorMinimaxH3Pipe._validate_refs_director_plan(plan, num_references=2)


def test_validate_refs_director_plan_accepts_a_valid_hard_cut_plan():
    document = _director_document(2, sub_types={1: "t2v"})
    plan = build_director_plan(document, default_seed=-1)
    GeneratorMinimaxH3Pipe._validate_refs_director_plan(plan, num_references=1)  # must not raise


def test_bite_check_the_hard_cut_plan_would_fail_the_continuation_guard_unmodified():
    # BITE CHECK for the "accepts" test above: the SAME document shape
    # without forcing segment 1 to a cut defaults to 'chain' and must still
    # be refused -- confirms the "accepts" test isn't vacuously passing
    # because the guard never fires.
    document = _director_document(2)  # segment 1 defaults to 'chain'
    plan = build_director_plan(document, default_seed=-1)
    with pytest.raises(DirectorPlanError):
        GeneratorMinimaxH3Pipe._validate_refs_director_plan(plan, num_references=1)


def _pipe_input(**kwargs):
    from src.pipelines.contracts import PipeInput
    return PipeInput(input=kwargs)


def _reference_image(size=(64, 48)):
    """One loaded reference image, as `media_loader` would hand it over.

    `build_context` normalizes references now, so a placeholder string no
    longer stands in for a picture: the fit is real work on a real image.
    """
    from PIL import Image

    return Image.new("RGB", size, color=(10, 20, 30))


def _fake_conditioning(num_text_tokens: int, text_tag: int = TEXT_TAG):
    return SimpleNamespace(embeds={
        "context": torch.zeros(1, num_text_tokens, 8),
        "token_tags": torch.full((num_text_tokens,), text_tag, dtype=torch.long),
    })


def _fake_bundle():
    return SimpleNamespace(
        spec=SimpleNamespace(family="minimax_h3", variant="h3", sampling_settings={}, latent_format={}),
        dit=SimpleNamespace(compute_dtype=torch.float32, estimated_vram_gb=0.0, module=None,
                             move_to=lambda d: None, offload=lambda: None),
        te=None, te_cache_key=None,
        video_vae=SimpleNamespace(module=None, move_to=lambda d: None, offload=lambda: None),
        audio_vae=SimpleNamespace(module=None, move_to=lambda d: None, offload=lambda: None),
    )


# -- refine entry path: contract + build_context (initial_latent/denoise) ---

def test_inputs_contract_includes_the_refine_entry_path():
    names = {spec.name for spec in GeneratorMinimaxH3Pipe.inputs()}
    assert "initial_latent" in names
    assert "source_frame_count" in names


def test_configuration_contract_includes_denoise_and_video_sigma_shift():
    names = {spec.name for spec in GeneratorMinimaxH3Pipe.configuration()}
    assert "denoise" in names
    assert "video_sigma_shift" in names


def _refine_latent(t_lat=7, h_lat=4, w_lat=6):
    return torch.zeros(1, 24, t_lat, h_lat, w_lat)


def test_build_context_derives_geometry_from_initial_latent():
    pipe = GeneratorMinimaxH3Pipe(GeneratorMinimaxH3Pipe.get_default_config())
    bundle = _fake_bundle()
    ctx = pipe.build_context(_pipe_input(
        model=bundle, conditioning=[_fake_conditioning(3)], initial_latent=[_refine_latent()],
    ))
    # 7 latent frames = n=1 chunk -> 22 aligned pixel frames (17*1+5).
    assert ctx.extra.num_latent_frames == 7
    assert ctx.extra.latent_height == 4
    assert ctx.extra.latent_width == 6
    assert ctx.extra.height == 64   # 4 * 16
    assert ctx.extra.width == 96    # 6 * 16
    assert ctx.extra.frames == 22
    assert len(ctx.extra.initial_latents) == 1
    assert ctx.extra.denoise == pytest.approx(1.0)
    assert ctx.extra.video_sigma_shift == pytest.approx(VIDEO_SHIFT)


def test_build_context_accepts_a_bare_tensor_initial_latent_not_just_a_list():
    pipe = GeneratorMinimaxH3Pipe(GeneratorMinimaxH3Pipe.get_default_config())
    bundle = _fake_bundle()
    ctx = pipe.build_context(_pipe_input(
        model=bundle, conditioning=[_fake_conditioning(3)], initial_latent=_refine_latent(),
    ))
    assert len(ctx.extra.initial_latents) == 1


def test_build_context_carries_denoise_and_video_sigma_shift_off_config():
    pipe = GeneratorMinimaxH3Pipe({
        **GeneratorMinimaxH3Pipe.get_default_config(), "denoise": 0.45, "video_sigma_shift": 9.0,
    })
    bundle = _fake_bundle()
    ctx = pipe.build_context(_pipe_input(
        model=bundle, conditioning=[_fake_conditioning(3)], initial_latent=[_refine_latent()],
    ))
    assert ctx.extra.denoise == pytest.approx(0.45)
    assert ctx.extra.video_sigma_shift == pytest.approx(9.0)


def test_build_context_without_initial_latent_is_the_ordinary_from_noise_path():
    pipe = GeneratorMinimaxH3Pipe(GeneratorMinimaxH3Pipe.get_default_config())
    bundle = _fake_bundle()
    ctx = pipe.build_context(_pipe_input(model=bundle, conditioning=[_fake_conditioning(3)]))
    assert ctx.extra.initial_latents == []
    assert ctx.extra.denoise == pytest.approx(1.0)


def test_build_context_rejects_initial_latent_with_a_director_document():
    pipe = GeneratorMinimaxH3Pipe({
        **GeneratorMinimaxH3Pipe.get_default_config(), "document": _director_document(1),
    })
    bundle = _fake_bundle()
    with pytest.raises(ValueError, match="mutually exclusive"):
        pipe.build_context(_pipe_input(
            model=bundle, conditioning=[_fake_conditioning(3)], initial_latent=[_refine_latent()],
        ))


def test_build_context_rejects_initial_latent_with_an_fl2va_image():
    pipe = GeneratorMinimaxH3Pipe(GeneratorMinimaxH3Pipe.get_default_config())
    bundle = _fake_bundle()
    with pytest.raises(ValueError, match="mutually exclusive"):
        pipe.build_context(_pipe_input(
            model=bundle, conditioning=[_fake_conditioning(3)],
            initial_latent=[_refine_latent()], image=["a_keyframe"],
        ))


def test_build_context_rejects_initial_latent_with_reference_images():
    pipe = GeneratorMinimaxH3Pipe(GeneratorMinimaxH3Pipe.get_default_config())
    bundle = _fake_bundle()
    with pytest.raises(ValueError, match="mutually exclusive"):
        pipe.build_context(_pipe_input(
            model=bundle, conditioning=[_fake_conditioning(3)],
            initial_latent=[_refine_latent()], reference_images=[_reference_image()],
        ))


def test_build_context_rejects_denoise_below_1_without_initial_latent():
    pipe = GeneratorMinimaxH3Pipe({**GeneratorMinimaxH3Pipe.get_default_config(), "denoise": 0.45})
    bundle = _fake_bundle()
    with pytest.raises(ValueError, match="has no effect without"):
        pipe.build_context(_pipe_input(model=bundle, conditioning=[_fake_conditioning(3)]))


def test_build_context_rejects_an_initial_latent_with_the_wrong_channel_count():
    pipe = GeneratorMinimaxH3Pipe(GeneratorMinimaxH3Pipe.get_default_config())
    bundle = _fake_bundle()
    with pytest.raises(ValueError, match="24 channels"):
        pipe.build_context(_pipe_input(
            model=bundle, conditioning=[_fake_conditioning(3)],
            initial_latent=[torch.zeros(1, 16, 7, 3, 4)],
        ))


def test_build_context_rejects_odd_latent_height_or_width():
    pipe = GeneratorMinimaxH3Pipe(GeneratorMinimaxH3Pipe.get_default_config())
    bundle = _fake_bundle()
    for h_lat, w_lat in ((3, 6), (4, 5)):
        with pytest.raises(ValueError, match="even latent height and width"):
            pipe.build_context(_pipe_input(
                model=bundle, conditioning=[_fake_conditioning(3)],
                initial_latent=[_refine_latent(h_lat=h_lat, w_lat=w_lat)],
            ))


def test_build_context_rejects_an_initial_latent_outside_the_aspect_range():
    # latent 2x40 -> pixel 32x640, aspect 20:1, outside the 1:4..4:1 bound.
    pipe = GeneratorMinimaxH3Pipe(GeneratorMinimaxH3Pipe.get_default_config())
    bundle = _fake_bundle()
    with pytest.raises(ValueError, match="aspect range"):
        pipe.build_context(_pipe_input(
            model=bundle, conditioning=[_fake_conditioning(3)],
            initial_latent=[_refine_latent(h_lat=2, w_lat=40)],
        ))


def test_build_context_accepts_an_initial_latent_above_the_generation_canvas_cap():
    # 1920x1088 (~2.09 MP) is deliberately ABOVE the release canvas's own
    # CANVAS_MAX_PIXELS (768*1344 =~ 1.03 MP) -- a refine latent already
    # exists at whatever resolution its own upstream generation used, and the
    # release canvas's area cap must not reject it (module docstring, "Refine
    # entry path"). latent = pixel / 16: 1088/16=68, 1920/16=120.
    pipe = GeneratorMinimaxH3Pipe(GeneratorMinimaxH3Pipe.get_default_config())
    bundle = _fake_bundle()
    ctx = pipe.build_context(_pipe_input(
        model=bundle, conditioning=[_fake_conditioning(3)],
        initial_latent=[_refine_latent(h_lat=68, w_lat=120)],
    ))
    assert ctx.extra.height == 1088
    assert ctx.extra.width == 1920
    assert ctx.extra.height * ctx.extra.width > 768 * 1344  # above the release canvas cap, on purpose


# -- refine entry path: _normalized_initial_latent ---------------------------

def _bare_ctx(**overrides) -> _MiniMaxH3Ctx:
    kwargs = dict(
        bundle=None, conditioning=[], steps=1, height=48, width=64,
        frames=22, num_latent_frames=7, latent_height=3, latent_width=4,
        num_audio_latents=2, device="cpu", dtype=torch.float32, spec=None,
    )
    kwargs.update(overrides)
    return _MiniMaxH3Ctx(**kwargs)


class _NormalizingVae:
    latents_mean = torch.full((24,), 1.0)
    latents_std = torch.full((24,), 2.0)


def test_normalized_initial_latent_is_none_without_initial_latents():
    c = _bare_ctx()
    assert GeneratorMinimaxH3Pipe._normalized_initial_latent(c, 0, _NormalizingVae()) is None


def test_normalized_initial_latent_inverts_the_decode_denormalize():
    c = _bare_ctx(initial_latents=[torch.full((1, 24, 7, 3, 4), 5.0)])
    got = GeneratorMinimaxH3Pipe._normalized_initial_latent(c, 0, _NormalizingVae())
    # `_decode_video` reverses this with `* latents_std + latents_mean`; this
    # is the exact inverse: (5.0 - 1.0) / 2.0 = 2.0.
    torch.testing.assert_close(got, torch.full((1, 24, 7, 3, 4), 2.0), rtol=0, atol=1e-6)


def test_normalized_initial_latent_falls_back_to_the_last_entry_for_a_later_seed():
    latent0 = torch.full((1, 24, 7, 3, 4), 1.0)
    c = _bare_ctx(initial_latents=[latent0])
    got = GeneratorMinimaxH3Pipe._normalized_initial_latent(c, 3, _NormalizingVae())
    torch.testing.assert_close(got, (latent0 - 1.0) / 2.0, rtol=0, atol=1e-6)


def test_normalized_initial_latent_rejects_a_mismatched_shape():
    c = _bare_ctx(initial_latents=[torch.zeros(1, 24, 5, 3, 4)])  # wrong num_latent_frames
    with pytest.raises(ValueError, match="does not match"):
        GeneratorMinimaxH3Pipe._normalized_initial_latent(c, 0, _NormalizingVae())


# -- H3_INNER_DIM: the wider of attn-inner (7168) and hidden (5376) --------

def test_h3_inner_dim_is_the_wider_of_attn_inner_and_hidden():
    assert H3_INNER_DIM == 56 * 128  # 7168, wider than hidden_size 5376
    assert H3_INNER_DIM > 5376


# -- forward wrapper: pack/unpack roundtrip ----------------------------------

def _fake_dit_module(video_patch_dim: int, audio_channels: int = 32, seen_step_caches: list | None = None,
                     seen_sol_attn: list | None = None):
    """Deterministic, non-degenerate "DiT": scales each stream by a constant
    so the output is distinguishable from the input and from the other
    stream, and returns batch-1 tensors matching the real forward's shape
    contract. ``seen_step_caches`` collects the ``step_cache`` argument of
    every call, in step order; ``seen_sol_attn`` does the same for the
    Sol-Attn context."""
    seen_step_caches = [] if seen_step_caches is None else seen_step_caches

    def forward(*, hidden_states, audio_hidden_states, encoder_hidden_states, timestep,
                timestep_indices, token_tags, position_ids, video_indices, audio_indices, text_indices,
                step_cache=None, sparse_attn_ctx=None):
        seen_step_caches.append(step_cache)
        if seen_sol_attn is not None:
            # ONE context is reused across the window and its `dense` flag is
            # rewritten per step, so storing the object itself would give every
            # entry the last step's value. Snapshot the fields instead.
            seen_sol_attn.append(
                None if sparse_attn_ctx is None
                else (sparse_attn_ctx.tau, sparse_attn_ctx.sink_tokens, sparse_attn_ctx.dense)
            )
        assert hidden_states.shape[-1] == video_patch_dim
        assert audio_hidden_states.shape[-1] == audio_channels
        assert position_ids.shape == (hidden_states.shape[1] + audio_hidden_states.shape[1] + text_indices.numel(), 3)
        video_pred = hidden_states * 0.1
        audio_pred = audio_hidden_states * -0.2
        return video_pred, audio_pred
    return forward


def test_forward_wrapper_shapes_and_kwargs():
    num_text_tokens = 3
    text_tags = torch.full((num_text_tokens,), TEXT_TAG, dtype=torch.long)
    layout = build_packed_sequence(
        text_tags, num_latent_frames=2, latent_height=2, latent_width=2, num_audio_latents=2, patch_size=PATCH,
    )
    video_patch_dim = 24 * PATCH[0] * PATCH[1] * PATCH[2]
    dit = _fake_dit_module(video_patch_dim)
    forward = _MiniMaxH3Forward(dit, layout, torch.zeros(1, num_text_tokens, 8))

    num_video_rows = layout.video_indices.numel()
    num_audio_rows = layout.audio_indices.numel()
    video_rows = torch.randn(num_video_rows, video_patch_dim)
    audio_rows = torch.randn(num_audio_rows, 32)
    unique_ts, ts_idx = build_row_timesteps(
        layout.video_indices, layout.audio_indices,
        num_condition_video_rows=layout.num_condition_video_rows, num_condition_audio_rows=0,
        num_text_tokens=num_text_tokens, video_timestep=0.5, audio_timestep=0.5,
        condition_video_timestep=0.999, condition_audio_timestep=1.0,
    )
    video_pred, audio_pred = forward(video_rows, audio_rows, unique_ts, ts_idx)

    assert video_pred.shape == (num_video_rows, video_patch_dim)
    assert audio_pred.shape == (num_audio_rows, 32)
    torch.testing.assert_close(video_pred, video_rows * 0.1)
    torch.testing.assert_close(audio_pred, audio_rows * -0.2)


# -- condition-row freeze ----------------------------------------------------

def _step_all_rows(forward, layout, video_rows, audio_rows, *, freeze_condition: bool, steps: int = 3):
    """A minimal re-implementation of generate_one's inner loop, parameterized
    on whether the condition-row slice is respected -- lets the freeze test
    also serve as its own bite check."""
    video_schedule = build_sigma_schedule(steps, VIDEO_SHIFT)
    audio_schedule = build_sigma_schedule(steps, AUDIO_SHIFT)
    num_steps = video_schedule.timesteps.numel()
    n_cv = 0 if freeze_condition is False else layout.num_condition_video_rows
    n_ca = layout.num_condition_audio_rows
    for i in range(num_steps):
        video_t = float(video_schedule.timesteps[i])
        audio_t = float(audio_schedule.timesteps[i])
        unique_ts, ts_idx = build_row_timesteps(
            layout.video_indices, layout.audio_indices,
            num_condition_video_rows=layout.num_condition_video_rows, num_condition_audio_rows=n_ca,
            num_text_tokens=layout.text_indices.numel(), video_timestep=video_t, audio_timestep=audio_t,
            condition_video_timestep=max(video_t, 0.999), condition_audio_timestep=1.0,
        )
        video_pred, audio_pred = forward(video_rows, audio_rows, unique_ts, ts_idx)
        video_rows[n_cv:] = euler_step(
            video_pred[n_cv:], video_t, video_rows[n_cv:], video_schedule.sigmas[i], video_schedule.sigmas[i + 1],
        )
        audio_rows[n_ca:] = euler_step(
            audio_pred[n_ca:], audio_t, audio_rows[n_ca:], audio_schedule.sigmas[i], audio_schedule.sigmas[i + 1],
        )
    return video_rows, audio_rows


def _fl2va_layout_and_forward():
    num_text_tokens = 3
    text_tags = torch.full((num_text_tokens,), TEXT_TAG, dtype=torch.long)
    layout = build_packed_sequence(
        text_tags, num_latent_frames=2, latent_height=2, latent_width=2, num_audio_latents=2, patch_size=PATCH,
        keyframe_anchors=("first",),
    )
    video_patch_dim = 24 * PATCH[0] * PATCH[1] * PATCH[2]
    dit = _fake_dit_module(video_patch_dim)
    forward = _MiniMaxH3Forward(dit, layout, torch.zeros(1, num_text_tokens, 8))
    return layout, forward, video_patch_dim


def test_condition_rows_are_frozen_across_steps():
    layout, forward, video_patch_dim = _fl2va_layout_and_forward()
    n_cv = layout.num_condition_video_rows
    assert n_cv > 0
    video_rows = torch.randn(layout.video_indices.numel(), video_patch_dim)
    audio_rows = torch.randn(layout.audio_indices.numel(), 32)
    condition_before = video_rows[:n_cv].clone()

    video_after, _ = _step_all_rows(forward, layout, video_rows.clone(), audio_rows.clone(), freeze_condition=True)

    torch.testing.assert_close(video_after[:n_cv], condition_before, rtol=0, atol=0)
    # The generated rows, in contrast, must actually have moved.
    assert not torch.allclose(video_after[n_cv:], video_rows[n_cv:])


def test_bite_check_condition_row_freeze_detects_an_unfrozen_scheduler():
    # BITE CHECK: stepping the scheduler over ALL rows (never excluding the
    # condition slice) must change the condition rows -- confirms the freeze
    # assertion above is actually exercising real behavior, not a vacuous
    # no-op comparison.
    layout, forward, video_patch_dim = _fl2va_layout_and_forward()
    n_cv = layout.num_condition_video_rows
    video_rows = torch.randn(layout.video_indices.numel(), video_patch_dim)
    audio_rows = torch.randn(layout.audio_indices.numel(), 32)
    condition_before = video_rows[:n_cv].clone()

    video_after, _ = _step_all_rows(forward, layout, video_rows.clone(), audio_rows.clone(), freeze_condition=False)

    assert not torch.allclose(video_after[:n_cv], condition_before)


# -- audio source resolution --------------------------------------------------

def _fake_audio_vae_module():
    latents_mean = torch.zeros(32)
    latents_std = torch.ones(32)

    class _FakeAudioVae:
        def __init__(self):
            self.latents_mean = latents_mean
            self.latents_std = latents_std

        def decode(self, latents):
            return torch.zeros(2, 1, latents.shape[-1] * 4)

    return _FakeAudioVae()


def test_resolve_audio_generate_decodes_through_audio_vae():
    pipe = GeneratorMinimaxH3Pipe(GeneratorMinimaxH3Pipe.get_default_config())
    audio_vae_module = _fake_audio_vae_module()
    bundle = SimpleNamespace(audio_vae=SimpleNamespace(module=audio_vae_module, move_to=lambda d: None, offload=lambda: None))
    ctx = _MiniMaxH3Ctx(
        bundle=bundle, conditioning=[], steps=2, height=32, width=32, frames=5,
        num_latent_frames=2, latent_height=2, latent_width=2, num_audio_latents=3,
        device="cpu", dtype=torch.float32, spec=None, audio_source="generate",
    )
    audio_rows = torch.zeros(3 * 2, 32)
    track = pipe._resolve_audio(ctx, audio_rows)
    assert isinstance(track, AudioTrack)
    assert track.sample_rate == 32000
    assert track.waveform.shape[0] == 2


def test_resolve_audio_file_passes_through_verbatim():
    pipe = GeneratorMinimaxH3Pipe(GeneratorMinimaxH3Pipe.get_default_config())
    ctx = _MiniMaxH3Ctx(
        bundle=None, conditioning=[], steps=2, height=32, width=32, frames=5,
        num_latent_frames=2, latent_height=2, latent_width=2, num_audio_latents=3,
        device="cpu", dtype=torch.float32, spec=None, audio_source="file", audio_file="/tmp/some.wav",
    )
    assert pipe._resolve_audio(ctx, torch.zeros(1)) == "/tmp/some.wav"


# -- pixel conversion ---------------------------------------------------------

def test_pixels_5d_to_uint8_frames_endpoints():
    from src.pipelines.pipes._shared.media.pixel_convert import pixels_3thw_to_uint8_frames

    pixels = torch.zeros(1, 3, 1, 1, 2)
    pixels[0, :, 0, 0, 0] = 0.0
    pixels[0, :, 0, 0, 1] = 1.0
    frames = pixels_3thw_to_uint8_frames(pixels[0], value_range="unit")
    assert frames.shape == (1, 1, 2, 3)
    assert frames[0, 0, 0].tolist() == [0, 0, 0]
    assert frames[0, 0, 1].tolist() == [255, 255, 255]


def test_pixels_5d_to_uint8_frames_clamps_out_of_range():
    from src.pipelines.pipes._shared.media.pixel_convert import pixels_3thw_to_uint8_frames

    pixels = torch.full((1, 3, 1, 1, 1), 1.5)
    frames = pixels_3thw_to_uint8_frames(pixels[0], value_range="unit")
    assert frames[0, 0, 0].tolist() == [255, 255, 255]


# -- mux argument construction -------------------------------------------------

def test_generate_one_t2va_mux_args(tmp_path):
    """End-to-end generate_one on a fake bundle (t2va, no keyframes), CPU
    device, tiny geometry -- verifies the packed roundtrip runs to completion
    and that encode_frames_to_mp4 is called with fps=24 and the decoded
    AudioTrack."""
    video_patch_dim = 24 * PATCH[0] * PATCH[1] * PATCH[2]

    class _FakeVideoVae:
        latents_mean = torch.zeros(24)
        latents_std = torch.ones(24)

        def decode(self, z):
            b, c, f, h, w = z.shape
            return torch.rand(b, 3, f, h, w)

    class _FakeDitModel:
        module = None
        compute_dtype = torch.float32
        estimated_vram_gb = 0.0

        def move_to(self, device):
            pass

        def offload(self):
            pass

    dit_native = _FakeDitModel()
    dit_native.module = _fake_dit_module(video_patch_dim)

    bundle = SimpleNamespace(
        spec=SimpleNamespace(family="minimax_h3", variant="h3", sampling_settings={},
                              latent_format={"format": "minimax_h3", "latent_channels": 24}),
        dit=dit_native,
        video_vae=SimpleNamespace(module=_FakeVideoVae(), compute_dtype=torch.float32,
                                   move_to=lambda d: None, offload=lambda: None),
        audio_vae=SimpleNamespace(module=_fake_audio_vae_module(), move_to=lambda d: None, offload=lambda: None),
        te=None, te_cache_key=None,
    )

    ctx_extra = _MiniMaxH3Ctx(
        bundle=bundle, conditioning=[_fake_conditioning(3)], steps=3,
        height=32, width=32, frames=22, num_latent_frames=2, latent_height=2, latent_width=2,
        num_audio_latents=2, device="cpu", dtype=torch.float32, spec=bundle.spec,
        keyframe_images=[], keyframe_anchors=(), audio_source="generate", decode=True,
    )
    ctx = GeneratorContext(quantity=1, input_seeds=[7], extra=ctx_extra)

    pipe = GeneratorMinimaxH3Pipe({**GeneratorMinimaxH3Pipe.get_default_config(), "preview": False})
    pipe._audio_results = []  # normally set by build_context; bypassed here for a tighter unit test
    events = []
    progress = ProgressEmitter(events.append, title="test")

    with patch("src.pipelines.pipes.generator.video_minimax_h3.main.encode_frames_to_mp4") as mock_encode:
        mock_encode.return_value = tmp_path / "out.mp4"
        result = pipe.generate_one(ctx, 0, 7, progress)

    assert mock_encode.call_count == 1
    _, kwargs = mock_encode.call_args
    assert kwargs["fps"] == 24
    assert isinstance(kwargs["audio"], AudioTrack)
    assert isinstance(result, str)
    assert len(pipe._audio_results) == 1


def test_generate_one_ref2va_routes_through_the_reference_layout(tmp_path):
    """generate_one's ref2va branch: `prepare_reference_conditioning` and
    `build_ref2va_packed_sequence` are called (not the fl2va/t2va pair) and
    their outputs actually reach `_sample_window` as the pre-built layout --
    both real functions are faked here (not the geometry math itself, which
    layout.py's/conditioning.py's own tests already cover) purely to keep
    this CPU test's tensors tiny regardless of REFERENCE_IMAGE_SHORT_EDGE."""
    video_patch_dim = 24 * PATCH[0] * PATCH[1] * PATCH[2]

    class _FakeVideoVae:
        latents_mean = torch.zeros(24)
        latents_std = torch.ones(24)

        def decode(self, z):
            b, c, f, h, w = z.shape
            return torch.rand(b, 3, f, h, w)

    class _FakeDitModel:
        module = None
        compute_dtype = torch.float32
        estimated_vram_gb = 0.0

        def move_to(self, device):
            pass

        def offload(self):
            pass

    dit_native = _FakeDitModel()
    dit_native.module = _fake_dit_module(video_patch_dim)

    bundle = SimpleNamespace(
        spec=SimpleNamespace(family="minimax_h3", variant="h3", sampling_settings={},
                              latent_format={"format": "minimax_h3", "latent_channels": 24}),
        dit=dit_native,
        video_vae=SimpleNamespace(module=_FakeVideoVae(), compute_dtype=torch.float32,
                                   move_to=lambda d: None, offload=lambda: None),
        audio_vae=SimpleNamespace(module=_fake_audio_vae_module(), move_to=lambda d: None, offload=lambda: None),
        te=None, te_cache_key=None,
    )

    ctx_extra = _MiniMaxH3Ctx(
        bundle=bundle, conditioning=[_fake_conditioning(3)], steps=3,
        height=32, width=32, frames=22, num_latent_frames=2, latent_height=2, latent_width=2,
        num_audio_latents=2, device="cpu", dtype=torch.float32, spec=bundle.spec,
        keyframe_images=[], keyframe_anchors=(),
        references=(ReferenceMedia(kind="image", image=_reference_image((8, 8))),),
        audio_source="generate", decode=True,
    )
    ctx = GeneratorContext(quantity=1, input_seeds=[7], extra=ctx_extra)

    pipe = GeneratorMinimaxH3Pipe({**GeneratorMinimaxH3Pipe.get_default_config(), "preview": False})
    pipe._audio_results = []
    events = []
    progress = ProgressEmitter(events.append, title="test")

    fake_condition_latent = torch.zeros(1, 24, 1, 2, 2)
    # 1 row: matches the mocked layout below (1 keyframe_anchor * rows_per_frame=1
    # for a 2x2 latent under PATCH (1,2,2)) -- the shapes must agree because
    # `generate_one` concatenates `condition_rows` onto the target rows by
    # its own `.shape[0]`, independent of the (also mocked) layout's row count.
    fake_condition_rows = torch.zeros(1, video_patch_dim)

    with (
        patch("src.pipelines.pipes.generator.video_minimax_h3.main.encode_frames_to_mp4") as mock_encode,
        patch(
            "src.pipelines.pipes.generator.video_minimax_h3.main.prepare_reference_conditioning",
        ) as mock_prepare_refs,
        patch(
            "src.pipelines.pipes.generator.video_minimax_h3.main.prepare_keyframe_condition_rows",
        ) as mock_prepare_keyframes,
        patch(
            "src.pipelines.pipes.generator.video_minimax_h3.main.build_ref2va_packed_sequence",
        ) as mock_build_ref2va,
        patch(
            "src.pipelines.pipes.generator.video_minimax_h3.main.build_packed_sequence",
        ) as mock_build_t2va,
    ):
        mock_encode.return_value = tmp_path / "out.mp4"
        mock_prepare_refs.return_value = ReferenceConditioning(
            blocks=(ReferenceBlock(kind="image"),),
            condition_latents=(fake_condition_latent,),
            audio_condition_latents=(),
            condition_rows=fake_condition_rows,
            condition_audio_rows=None,
        )
        real_layout = build_packed_sequence(
            torch.full((3,), TEXT_TAG, dtype=torch.long),
            num_latent_frames=2, latent_height=2, latent_width=2, num_audio_latents=2, patch_size=PATCH,
            keyframe_anchors=(0,), device="cpu",
        )
        mock_build_ref2va.return_value = real_layout
        pipe.generate_one(ctx, 0, 7, progress)

    assert mock_prepare_refs.call_count == 1
    assert mock_prepare_keyframes.call_count == 0  # NOT the fl2va/t2va condition path
    assert mock_build_ref2va.call_count == 1
    assert mock_build_t2va.call_count == 0  # NOT the fl2va/t2va layout builder
    # The layout's blocks and both latent iterators are forwarded from the
    # ONE ReferenceConditioning, untouched and in its order.
    call_args = mock_build_ref2va.call_args
    references = call_args[0][1]
    assert [r.kind for r in references] == ["image"]
    assert call_args[0][2] == (fake_condition_latent,)  # condition_latents forwarded as-is
    assert call_args[0][3] == ()  # no audio_condition_latents (images never carry audio)


# -- cancellation -------------------------------------------------------------

def _cancel_after(n: int):
    """`is_cancelled` that returns False for the first `n` calls, True after."""
    calls = {"n": 0}

    def is_cancelled() -> bool:
        calls["n"] += 1
        return calls["n"] > n

    return is_cancelled, calls


def _fake_bundle_for_generate_one(video_patch_dim: int, *, seen_step_caches: list):
    class _FakeVideoVae:
        latents_mean = torch.zeros(24)
        latents_std = torch.ones(24)

        def decode(self, z):
            b, c, f, h, w = z.shape
            return torch.rand(b, 3, f, h, w)

    dit_native = SimpleNamespace(
        compute_dtype=torch.float32, estimated_vram_gb=0.0,
        module=_fake_dit_module(video_patch_dim, seen_step_caches=seen_step_caches),
        move_to=lambda d: None, offload=lambda: None,
    )
    return SimpleNamespace(
        spec=SimpleNamespace(family="minimax_h3", variant="h3", sampling_settings={},
                              latent_format={"format": "minimax_h3", "latent_channels": 24}),
        dit=dit_native,
        video_vae=SimpleNamespace(module=_FakeVideoVae(), compute_dtype=torch.float32,
                                   move_to=lambda d: None, offload=lambda: None),
        audio_vae=SimpleNamespace(module=_fake_audio_vae_module(), move_to=lambda d: None, offload=lambda: None),
        te=None, te_cache_key=None,
    )


def test_generate_one_raises_sampling_cancelled_mid_step_and_stops_the_dit():
    """`ctx.is_cancelled` flipping True partway through the step loop must
    stop the per-step DiT forward from running any further -- this is the
    whole point of the fix: sampling itself observes cancellation instead of
    running to completion regardless."""
    steps = 6
    video_patch_dim = 24 * PATCH[0] * PATCH[1] * PATCH[2]
    seen: list = []
    bundle = _fake_bundle_for_generate_one(video_patch_dim, seen_step_caches=seen)

    ctx_extra = _MiniMaxH3Ctx(
        bundle=bundle, conditioning=[_fake_conditioning(3)], steps=steps,
        height=32, width=32, frames=22, num_latent_frames=2, latent_height=2, latent_width=2,
        num_audio_latents=2, device="cpu", dtype=torch.float32, spec=bundle.spec,
        keyframe_images=[], keyframe_anchors=(), audio_source="generate", decode=True,
    )
    is_cancelled, calls = _cancel_after(3)  # cancel partway through the 6-step loop
    ctx = GeneratorContext(quantity=1, input_seeds=[7], extra=ctx_extra, is_cancelled=is_cancelled)

    pipe = GeneratorMinimaxH3Pipe({**GeneratorMinimaxH3Pipe.get_default_config(), "preview": False, "steps": steps})
    pipe._audio_results = []
    progress = ProgressEmitter([].append, title="test")

    with patch("src.pipelines.pipes.generator.video_minimax_h3.main.encode_frames_to_mp4") as mock_encode:
        with pytest.raises(SamplingCancelled):
            pipe.generate_one(ctx, 0, 7, progress)

    mock_encode.assert_not_called()
    assert len(seen) < steps  # the DiT forward never reached the final step
    assert calls["n"] == len(seen) + 1  # one is_cancelled() check per step, raised on the (n+1)th


def test_generate_one_skips_decode_when_cancelled_right_after_sampling():
    """The step loop completes (every DiT forward ran), but `is_cancelled`
    flips True the instant it's done -- decode/mp4-encode (~27s of GPU work)
    must still be skipped, not just the sampling."""
    steps = 4
    video_patch_dim = 24 * PATCH[0] * PATCH[1] * PATCH[2]
    seen: list = []
    bundle = _fake_bundle_for_generate_one(video_patch_dim, seen_step_caches=seen)

    ctx_extra = _MiniMaxH3Ctx(
        bundle=bundle, conditioning=[_fake_conditioning(3)], steps=steps,
        height=32, width=32, frames=22, num_latent_frames=2, latent_height=2, latent_width=2,
        num_audio_latents=2, device="cpu", dtype=torch.float32, spec=bundle.spec,
        keyframe_images=[], keyframe_anchors=(), audio_source="generate", decode=True,
    )
    # False for every one of the `steps` per-step checks (the whole loop
    # completes), True from the post-sampling check onward.
    is_cancelled, calls = _cancel_after(steps)
    ctx = GeneratorContext(quantity=1, input_seeds=[7], extra=ctx_extra, is_cancelled=is_cancelled)

    pipe = GeneratorMinimaxH3Pipe({**GeneratorMinimaxH3Pipe.get_default_config(), "preview": False, "steps": steps})
    pipe._audio_results = []
    progress = ProgressEmitter([].append, title="test")

    with patch("src.pipelines.pipes.generator.video_minimax_h3.main.encode_frames_to_mp4") as mock_encode:
        with pytest.raises(SamplingCancelled):
            pipe.generate_one(ctx, 0, 7, progress)

    mock_encode.assert_not_called()
    assert len(seen) == steps  # sampling ran to completion...
    assert pipe._audio_results == []  # ...but nothing past it did


# -- FBCache step-cache wiring ------------------------------------------------

def _run_generate_one(config_overrides: dict, *, steps: int = 4, tmp_path=None, ctx_overrides: dict | None = None,
                      seen_sol_attn: list | None = None):
    """Run `generate_one` on fake modules and return (pipe, step_cache args
    the DiT saw, one per step). The pipe carries `_last_result` -- the raw
    video latent when `ctx_overrides` turns `decode` off."""
    video_patch_dim = 24 * PATCH[0] * PATCH[1] * PATCH[2]
    seen: list = []

    class _FakeVideoVae:
        latents_mean = torch.zeros(24)
        latents_std = torch.ones(24)

        def decode(self, z):
            b, c, f, h, w = z.shape
            return torch.rand(b, 3, f, h, w)

    dit_native = SimpleNamespace(
        compute_dtype=torch.float32, estimated_vram_gb=0.0,
        module=_fake_dit_module(video_patch_dim, seen_step_caches=seen, seen_sol_attn=seen_sol_attn),
        move_to=lambda d: None, offload=lambda: None,
    )
    bundle = SimpleNamespace(
        spec=SimpleNamespace(family="minimax_h3", variant="h3", sampling_settings={},
                              latent_format={"format": "minimax_h3", "latent_channels": 24}),
        dit=dit_native,
        video_vae=SimpleNamespace(module=_FakeVideoVae(), compute_dtype=torch.float32,
                                   move_to=lambda d: None, offload=lambda: None),
        audio_vae=SimpleNamespace(module=_fake_audio_vae_module(), move_to=lambda d: None, offload=lambda: None),
        te=None, te_cache_key=None,
    )
    ctx_kwargs = dict(
        bundle=bundle, conditioning=[_fake_conditioning(3)], steps=steps,
        height=32, width=32, frames=22, num_latent_frames=2, latent_height=2, latent_width=2,
        num_audio_latents=2, device="cpu", dtype=torch.float32, spec=bundle.spec,
        keyframe_images=[], keyframe_anchors=(), audio_source="generate", decode=True,
    )
    ctx_kwargs.update(ctx_overrides or {})
    ctx = GeneratorContext(quantity=1, input_seeds=[7], extra=_MiniMaxH3Ctx(**ctx_kwargs))

    pipe = GeneratorMinimaxH3Pipe({
        **GeneratorMinimaxH3Pipe.get_default_config(), "preview": False, "steps": steps, **config_overrides,
    })
    pipe._audio_results = []
    progress = ProgressEmitter([].append, title="test")
    with patch("src.pipelines.pipes.generator.video_minimax_h3.main.encode_frames_to_mp4") as mock_encode:
        mock_encode.return_value = "/tmp/out.mp4"
        pipe._last_result = pipe.generate_one(ctx, 0, 7, progress)
    return pipe, seen


def test_step_cache_is_off_by_default():
    assert GeneratorMinimaxH3Pipe.get_default_config()["step_cache_threshold"] == 0.0
    assert build_step_cache(GeneratorMinimaxH3Pipe.get_default_config()) is None

    _, seen = _run_generate_one({})
    assert seen and all(cache is None for cache in seen)


def test_build_step_cache_carries_all_three_knobs():
    cache = build_step_cache({
        "step_cache_threshold": 0.12, "step_cache_warmup_steps": 6, "step_cache_max_skips": 2,
    })
    assert isinstance(cache, FirstBlockCache)
    assert cache.rel_threshold == pytest.approx(0.12)
    assert cache.warmup_steps == 6
    assert cache.max_consecutive_skips == 2
    assert cache.enabled


def test_build_step_cache_accepts_rendered_string_knobs():
    """A preset renders every configuration value through Jinja, so the knobs
    arrive as strings."""
    cache = build_step_cache({
        "step_cache_threshold": "0.12", "step_cache_warmup_steps": "6", "step_cache_max_skips": "2",
    })
    assert cache is not None
    assert cache.rel_threshold == pytest.approx(0.12)
    assert cache.warmup_steps == 6
    assert cache.max_consecutive_skips == 2


def test_build_step_cache_falls_back_on_garbage_knobs():
    assert build_step_cache({"step_cache_threshold": "not-a-number"}) is None
    cache = build_step_cache({"step_cache_threshold": 0.1, "step_cache_warmup_steps": "nope"})
    assert cache is not None and cache.warmup_steps == 4


@pytest.mark.parametrize("threshold", [0.0, -1.0])
def test_build_step_cache_none_when_not_positive(threshold):
    assert build_step_cache({"step_cache_threshold": threshold}) is None


def test_enabled_step_cache_reaches_every_step_but_the_last():
    steps = 5
    _, seen = _run_generate_one({"step_cache_threshold": 0.12}, steps=steps)
    # generate_one's loop length comes from the schedule, not the raw knob.
    assert len(seen) == int(build_sigma_schedule(steps, VIDEO_SHIFT).timesteps.numel())
    assert len(seen) > 1
    # The final step must never consult the cache: its velocity lands directly
    # on the returned latent.
    assert seen[-1] is None
    caches = seen[:-1]
    assert all(isinstance(cache, FirstBlockCache) for cache in caches)
    # ONE cache instance for the whole generation (H3 is guidance-distilled --
    # a single trajectory, so a per-branch set would be pointless).
    assert len({id(cache) for cache in caches}) == 1
    assert caches[0].rel_threshold == pytest.approx(0.12)


# -- manual sigma schedules ---------------------------------------------------

_MANUAL_VIDEO = "1.0, 0.92, 0.7, 0.4, 0.15, 0.0"  # 6 grid values -> 5 steps


def test_manual_sigmas_default_to_empty():
    defaults = GeneratorMinimaxH3Pipe.get_default_config()
    assert defaults["manual_sigmas"] == ""
    assert defaults["manual_audio_sigmas"] == ""
    spec_defaults = {s.name: s.default for s in GeneratorMinimaxH3Pipe.configuration()}
    assert spec_defaults["manual_sigmas"] == ""
    assert spec_defaults["manual_audio_sigmas"] == ""


def test_validate_config_rejects_a_schedule_that_does_not_end_at_zero():
    with pytest.raises(ValueError, match="manual_sigmas"):
        validate_minimax_h3_config({"manual_sigmas": "1.0, 0.5, 0.1"}, pipe_id="t")


def test_validate_config_rejects_mismatched_manual_lengths():
    with pytest.raises(ValueError, match="same number of values"):
        validate_minimax_h3_config(
            {"manual_sigmas": "1.0, 0.5, 0.0", "manual_audio_sigmas": "1.0, 0.7, 0.3, 0.0"}, pipe_id="t",
        )


def test_validate_config_accepts_matching_manual_lengths_and_blank_defaults():
    validate_minimax_h3_config({"manual_sigmas": "1.0, 0.5, 0.0", "manual_audio_sigmas": "0.9, 0.4, 0.0"}, pipe_id="t")
    validate_minimax_h3_config({"manual_sigmas": "", "manual_audio_sigmas": ""}, pipe_id="t")


def test_manual_sigmas_drive_the_loop_length_not_steps():
    # `steps=4` would give 4 model evaluations; the 6-value manual grid gives 5.
    _, seen = _run_generate_one({}, steps=4, ctx_overrides={"manual_sigmas": _MANUAL_VIDEO})
    assert len(seen) == len(_MANUAL_VIDEO.split(",")) - 1 == 5


def test_manual_audio_sigmas_alone_also_drive_the_loop_length():
    _, seen = _run_generate_one({}, steps=4, ctx_overrides={"manual_audio_sigmas": _MANUAL_VIDEO})
    assert len(seen) == 5


def test_mismatched_manual_schedules_fail_the_generation():
    with pytest.raises(ValueError, match="same number of steps"):
        _run_generate_one({}, steps=4, ctx_overrides={
            "manual_sigmas": "1.0, 0.5, 0.0", "manual_audio_sigmas": "1.0, 0.7, 0.3, 0.0",
        })


def test_final_step_cache_guard_lands_on_the_last_manual_step():
    _, seen = _run_generate_one(
        {"step_cache_threshold": 0.12}, steps=4, ctx_overrides={"manual_sigmas": _MANUAL_VIDEO},
    )
    assert len(seen) == 5
    assert seen[-1] is None
    assert all(isinstance(cache, FirstBlockCache) for cache in seen[:-1])


def test_manual_schedule_spelling_out_the_computed_one_is_bit_identical():
    """The equivalence that makes the default path safe: a manual string
    holding the computed schedule's own values must produce the SAME latent,
    bit for bit -- i.e. the override changes nothing but the numbers."""
    steps = 6
    computed = build_sigma_schedule(steps, VIDEO_SHIFT)
    spelled_out = ", ".join(repr(float(v)) for v in computed.sigmas)

    baseline, _ = _run_generate_one({}, steps=steps, ctx_overrides={"decode": False})
    overridden, _ = _run_generate_one(
        {}, steps=steps, ctx_overrides={"decode": False, "manual_sigmas": spelled_out},
    )

    torch.testing.assert_close(overridden._last_result, baseline._last_result, rtol=0, atol=0)


def test_bite_check_a_different_manual_schedule_moves_the_latent():
    # BITE CHECK: the equivalence above must not be vacuous -- a genuinely
    # different manual grid of the SAME length has to change the output.
    steps = 6
    baseline, _ = _run_generate_one({}, steps=steps, ctx_overrides={"decode": False})
    overridden, _ = _run_generate_one(
        {}, steps=steps, ctx_overrides={"decode": False, "manual_sigmas": _MANUAL_VIDEO},
    )
    assert not torch.allclose(overridden._last_result, baseline._last_result)


# -- refine entry path: denoise=1.0 no-op, denoise<1.0 actually refines -------

def test_denoise_1_with_initial_latent_reproduces_the_plain_noise_path():
    """The load-bearing no-op property (schedule.py's `scale_noise`, `t=0` at
    denoise=1.0's first kept sigma): connecting ANY `initial_latent` at
    `denoise=1.0` must reproduce the SAME seed's ordinary from-noise
    generation bit for bit -- the latent's own content is fully discarded,
    the same as an img2img `denoise=1.0` request ignores its source image."""
    plain, _ = _run_generate_one({}, ctx_overrides={"decode": False})
    refine, _ = _run_generate_one({}, ctx_overrides={
        "decode": False, "denoise": 1.0,
        "initial_latents": [torch.rand(1, 24, 2, 2, 2)],  # arbitrary -- must be ignored
    })
    torch.testing.assert_close(refine._last_result, plain._last_result, rtol=0, atol=0)


def test_bite_check_denoise_below_1_moves_the_latent_off_the_plain_noise_path():
    # BITE CHECK for the no-op test above: a genuinely partial denoise must
    # not be vacuously identical to the from-noise path.
    plain, _ = _run_generate_one({}, ctx_overrides={"decode": False})
    refine, _ = _run_generate_one({}, ctx_overrides={
        "decode": False, "denoise": 0.45,
        "initial_latents": [torch.rand(1, 24, 2, 2, 2)],
    })
    assert not torch.allclose(refine._last_result, plain._last_result)


def test_bite_check_the_initial_latent_content_reaches_the_refine_trajectory():
    # BITE CHECK isolating the noising wire (not just the schedule
    # truncation): same seed, same denoise/steps, only the INITIAL LATENT's
    # own content differs -- the two outputs must diverge, or the latent
    # never actually reached `scale_noise`.
    run_a, _ = _run_generate_one({}, ctx_overrides={
        "decode": False, "denoise": 0.5,
        "initial_latents": [torch.full((1, 24, 2, 2, 2), 3.0)],
    })
    run_b, _ = _run_generate_one({}, ctx_overrides={
        "decode": False, "denoise": 0.5,
        "initial_latents": [torch.full((1, 24, 2, 2, 2), -3.0)],
    })
    assert not torch.allclose(run_a._last_result, run_b._last_result)


def test_denoise_below_1_runs_the_same_number_of_model_evaluations():
    # The truncated schedule always returns `steps + 1` knots (module
    # docstring "Refine entry path") -- denoise changes WHERE the trajectory
    # starts, never how many steps the loop takes.
    steps = 4
    _, seen_plain = _run_generate_one({}, steps=steps, ctx_overrides={"decode": False})
    _, seen_refine = _run_generate_one({}, steps=steps, ctx_overrides={
        "decode": False, "denoise": 0.45,
        "initial_latents": [torch.rand(1, 24, 2, 2, 2)],
    })
    assert len(seen_plain) == len(seen_refine) == steps


def test_video_sigma_shift_reaches_the_loop_and_changes_the_refine_latent():
    fixed_latent = torch.rand(1, 24, 2, 2, 2)
    default_shift, _ = _run_generate_one({}, ctx_overrides={
        "decode": False, "denoise": 0.6, "video_sigma_shift": VIDEO_SHIFT,
        "initial_latents": [fixed_latent],
    })
    alt_shift, _ = _run_generate_one({}, ctx_overrides={
        "decode": False, "denoise": 0.6, "video_sigma_shift": 9.0,
        "initial_latents": [fixed_latent],
    })
    assert not torch.allclose(default_shift._last_result, alt_shift._last_result)


# -- Video Director windowed generation -----------------------------------------
#
# The plan itself is covered in test_windows.py; what these exercise is the
# EXECUTION -- that the plan's overlap actually reaches the condition rows, and
# that exactly the frames it accounts for are trimmed back off again.

from src.pipelines.pipes.generator.video_minimax_h3.audio import pack_audio_rows, unpack_audio_rows
from src.pipelines.pipes.generator.video_minimax_h3.geometry import (
    audio_latent_num_frames,
    head_frames_for_latents,
    video_latent_num_frames,
)
from src.pipelines.pipes.generator.video_minimax_h3.layout import patchify_video_latents
from src.pipelines.pipes.generator.video_minimax_h3.schedule import KEYFRAME_NOISE_AUG, scale_noise
from src.pipelines.pipes.generator.video_minimax_h3.windows import DirectorPlanError, build_director_plan

DIRECTOR_FRAMES = 124            # 5.17s, the shortest window MiniMax-H3 allows
DIRECTOR_LATENTS = video_latent_num_frames(DIRECTOR_FRAMES)   # 37
DIRECTOR_AUDIO_LATENTS = audio_latent_num_frames(DIRECTOR_FRAMES)


class _RecordingPipe(GeneratorMinimaxH3Pipe):
    """Stands in for the two decoders so the window loop's own arithmetic --
    what it splices in, what it trims off -- is what the assertions see.

    `_decode_video` returns one identifiable value per pixel frame (the frame's
    own index within the window), so a trim is visible as the index the
    surviving footage starts at rather than only as a count.
    """

    def __init__(self, config):
        super().__init__(config)
        self.decoded_latents = []
        self.audio_calls = []

    def _decode_video(self, c, latent):
        self.decoded_latents.append(latent.clone())
        frames = head_frames_for_latents(latent.shape[2])
        return np.arange(frames, dtype=np.int32).reshape(frames, 1, 1).repeat(3, axis=2)

    def _resolve_audio(self, c, audio_rows, *, num_audio_latents=None, num_condition_audio_rows=0):
        latents = num_audio_latents if num_audio_latents is not None else c.num_audio_latents
        self.audio_calls.append((audio_rows.clone(), latents, num_condition_audio_rows))
        samples = latents * 800  # 40 latents/s at 32 kHz
        return AudioTrack(
            waveform=np.arange(samples, dtype=np.float32).reshape(1, -1).repeat(2, axis=0),
            sample_rate=32000,
        )


def _director_document(count=2, *, overlap=17, stitch=True, sub_types=None, seed=1000, frames=DIRECTOR_FRAMES):
    segments = []
    for index in range(count):
        sub_type = (sub_types or {}).get(index, "t2v" if index == 0 else "chain")
        segments.append({
            "id": f"seg-{index}", "prompt": f"shot {index}", "negative_prompt": "", "frames": frames,
            "seed": None, "steps": None, "cfg": None, "loras": None, "start": None, "end": None,
            "sub_type": sub_type,
        })
    return {
        "schema_version": 1, "mode": "director",
        "settings": {"fps": 24, "seed": seed, "resolution": "", "duration": None,
                     "continuation": {"source": "tail_frames", "overlap_frames": overlap, "stitch": stitch}},
        "segments": segments, "media": [], "audio": [], "ic_lora": [],
        "media_images": [], "media_videos": [], "media_placements": [],
    }


def _run_director(document, *, steps=3, config_overrides=None, director_images=()):
    """Run a whole Director document on fake modules.

    Returns `(pipe, forward_inputs, encode_calls, plan)` where `forward_inputs`
    holds the `(video_rows, audio_rows)` every transformer call saw, in order.
    """
    video_patch_dim = 24 * PATCH[0] * PATCH[1] * PATCH[2]
    forwards: list = []

    def dit_forward(*, hidden_states, audio_hidden_states, **kwargs):
        forwards.append((hidden_states[0].clone(), audio_hidden_states[0].clone()))
        return hidden_states * 0.1, audio_hidden_states * -0.2

    bundle = SimpleNamespace(
        spec=SimpleNamespace(family="minimax_h3", variant="h3", sampling_settings={},
                             latent_format={"format": "minimax_h3", "latent_channels": 24}),
        dit=SimpleNamespace(compute_dtype=torch.float32, estimated_vram_gb=0.0, module=dit_forward,
                            move_to=lambda d: None, offload=lambda: None),
        video_vae=SimpleNamespace(module=None, compute_dtype=torch.float32,
                                  move_to=lambda d: None, offload=lambda: None),
        audio_vae=SimpleNamespace(module=None, move_to=lambda d: None, offload=lambda: None),
        te=None, te_cache_key=None,
    )
    plan = build_director_plan(document, default_seed=-1)
    ctx = GeneratorContext(quantity=1, input_seeds=[7], extra=_MiniMaxH3Ctx(
        bundle=bundle, conditioning=[_fake_conditioning(3) for _ in plan.windows], steps=steps,
        height=32, width=32, frames=DIRECTOR_FRAMES, num_latent_frames=DIRECTOR_LATENTS,
        latent_height=2, latent_width=2, num_audio_latents=DIRECTOR_AUDIO_LATENTS,
        device="cpu", dtype=torch.float32, spec=bundle.spec, keyframe_images=[], keyframe_anchors=(),
        audio_source="generate", decode=True, plan=plan, director_images=list(director_images),
    ))

    pipe = _RecordingPipe({
        **GeneratorMinimaxH3Pipe.get_default_config(), "preview": False, "steps": steps,
        **(config_overrides or {}),
    })
    pipe._audio_results = []
    pipe._video_resolution = (32, 32)
    progress = ProgressEmitter([].append, title="test")
    with patch("src.pipelines.pipes.generator.video_minimax_h3.main.encode_frames_to_mp4") as mock_encode:
        mock_encode.side_effect = lambda frames, path, **kw: path
        pipe._last_result = pipe.generate_one(ctx, 0, 7, progress)
        encodes = list(mock_encode.call_args_list)
    return pipe, forwards, encodes, plan


def test_director_runs_one_window_per_segment():
    steps = 3
    pipe, forwards, encodes, plan = _run_director(_director_document(3), steps=steps)
    per_window = int(build_sigma_schedule(steps, VIDEO_SHIFT).timesteps.numel())
    assert len(forwards) == per_window * 3
    assert len(pipe.decoded_latents) == 3
    # Three per-window clips, then the stitched one.
    assert len(encodes) == 4
    assert isinstance(pipe._last_result, list) and len(pipe._last_result) == 1


def test_the_documents_overlap_reaches_the_condition_rows_bit_for_bit():
    """The load-bearing splice: window 1's leading condition rows must BE the
    tail of window 0's sampled latent, noise-augmented under window 1's own
    first noise draw -- not its head, and not a re-encode."""
    steps = 3
    pipe, forwards, _, plan = _run_director(_director_document(2), steps=steps)
    per_window = int(build_sigma_schedule(steps, VIDEO_SHIFT).timesteps.numel())
    window = plan.windows[1]
    assert window.overlap_latents == 5

    first_latent = pipe.decoded_latents[0]
    generator = torch.Generator(device="cpu").manual_seed(window.seed)
    expected = []
    for offset in range(window.overlap_latents):
        index = first_latent.shape[2] - window.overlap_latents + offset
        frame = first_latent[:, :, index:index + 1].to(torch.float32)
        noise = torch.randn(frame.shape, generator=generator, device="cpu", dtype=torch.float32)
        expected.append(patchify_video_latents(
            scale_noise(frame, KEYFRAME_NOISE_AUG, noise).to(torch.float32), PATCH,
        ))
    expected_rows = torch.cat(expected, dim=0)

    video_rows_seen = forwards[per_window][0]  # window 1, first step
    torch.testing.assert_close(video_rows_seen[:expected_rows.shape[0]], expected_rows, rtol=0, atol=0)


def test_bite_check_the_splice_assertion_rejects_the_head_of_the_previous_window():
    """BITE CHECK for the test above: building the same expectation from the
    FIRST latents of the previous window instead of its last -- the classic
    "the next shot restarts from the opening frame" bug -- must not match."""
    steps = 3
    pipe, forwards, _, plan = _run_director(_director_document(2), steps=steps)
    per_window = int(build_sigma_schedule(steps, VIDEO_SHIFT).timesteps.numel())
    window = plan.windows[1]

    first_latent = pipe.decoded_latents[0]
    generator = torch.Generator(device="cpu").manual_seed(window.seed)
    head = []
    for offset in range(window.overlap_latents):
        frame = first_latent[:, :, offset:offset + 1].to(torch.float32)
        noise = torch.randn(frame.shape, generator=generator, device="cpu", dtype=torch.float32)
        head.append(patchify_video_latents(
            scale_noise(frame, KEYFRAME_NOISE_AUG, noise).to(torch.float32), PATCH,
        ))
    head_rows = torch.cat(head, dim=0)

    video_rows_seen = forwards[per_window][0]
    assert not torch.allclose(video_rows_seen[:head_rows.shape[0]], head_rows)


def test_the_audio_carry_is_a_row_exact_slice_of_the_previous_window():
    steps = 3
    pipe, forwards, _, plan = _run_director(_director_document(2), steps=steps)
    per_window = int(build_sigma_schedule(steps, VIDEO_SHIFT).timesteps.numel())
    window = plan.windows[1]

    previous_rows, previous_latents, _ = pipe.audio_calls[0]
    overlap_latents = audio_latent_num_frames(window.overlap_frames)
    end = previous_latents - overlap_latents
    expected = pack_audio_rows(
        unpack_audio_rows(previous_rows, num_audio_latents=previous_latents)[..., end - overlap_latents:end]
    )

    audio_rows_seen = forwards[per_window][1]
    torch.testing.assert_close(audio_rows_seen[:expected.shape[0]], expected, rtol=0, atol=0)
    # ... and the prefix is exactly the rows the decoder is told to drop.
    assert pipe.audio_calls[1][2] == expected.shape[0] == overlap_latents * 2


def test_the_condition_audio_prefix_is_never_decoded_into_the_output():
    _, _, _, plan = _run_director(_director_document(2))
    pipe, _, _, _ = _run_director(_director_document(2))
    assert pipe.audio_calls[0][2] == 0
    assert pipe.audio_calls[1][2] > 0


def test_every_window_after_the_first_is_trimmed_by_exactly_its_overlap():
    pipe, _, encodes, plan = _run_director(_director_document(3))
    window_clips = encodes[:-1]
    for window, call in zip(plan.windows, window_clips):
        frames = call.args[0]
        assert len(frames) == window.emitted_frames
        # The surviving footage starts where the replayed context ended.
        assert int(frames[0][0][0]) == window.overlap_frames


def test_the_stitched_clip_is_every_windows_contribution_end_to_end():
    pipe, _, encodes, plan = _run_director(_director_document(3))
    stitched = encodes[-1].args[0]
    assert len(stitched) == plan.total_frames == DIRECTOR_FRAMES * 3 - 34
    assert int(stitched[0][0][0]) == 0                       # window 0 is not trimmed
    assert int(stitched[DIRECTOR_FRAMES][0][0]) == 17        # window 1 opens past its overlap


def test_bite_check_the_trim_assertion_fails_on_an_untrimmed_stitch():
    """BITE CHECK: the stitched length must not merely be "the sum of
    something" -- emitting each window whole would be 34 frames longer."""
    _, _, encodes, plan = _run_director(_director_document(3))
    assert len(encodes[-1].args[0]) != sum(window.frames for window in plan.windows)


def test_the_stitched_audio_loses_the_same_duration_as_the_video():
    pipe, _, encodes, plan = _run_director(_director_document(2))
    track = encodes[-1].kwargs["audio"]
    trimmed = int(round(17 / 24 * 32000))
    assert track.waveform.shape[1] == 2 * DIRECTOR_AUDIO_LATENTS * 800 - trimmed
    assert len(pipe._audio_results) == 1


# -- ref2va + Video Director: routing, per-shot selection, equivalence -------

def _run_refs_director(document, references, *, steps=2, config_overrides=None):
    """Run a refs-conditioned Director document with `prepare_reference_
    conditioning` faked (its own geometry is test_conditioning.py's/
    test_layout.py's job; the property under test here is ROUTING and
    SELECTION) but `build_ref2va_packed_sequence` REAL, so `_sample_window`
    still gets a genuine layout to sample against.

    Returns `(pipe, calls, plan, mock_prepare_keyframes, mock_build_t2va)`
    where `calls` is the `references` tuple every `prepare_reference_
    conditioning` invocation actually received, one entry per window, in
    window order.
    """
    def dit_forward(*, hidden_states, audio_hidden_states, **kwargs):
        return hidden_states * 0.1, audio_hidden_states * -0.2

    bundle = SimpleNamespace(
        spec=SimpleNamespace(family="minimax_h3", variant="h3", sampling_settings={},
                             latent_format={"format": "minimax_h3", "latent_channels": 24}),
        dit=SimpleNamespace(compute_dtype=torch.float32, estimated_vram_gb=0.0, module=dit_forward,
                            move_to=lambda d: None, offload=lambda: None),
        video_vae=SimpleNamespace(module=SimpleNamespace(latents_mean=torch.zeros(24), latents_std=torch.ones(24)),
                                  compute_dtype=torch.float32, move_to=lambda d: None, offload=lambda: None),
        audio_vae=SimpleNamespace(module=None, move_to=lambda d: None, offload=lambda: None),
        te=None, te_cache_key=None,
    )
    plan = build_director_plan(document, default_seed=-1)
    ctx = GeneratorContext(quantity=1, input_seeds=[7], extra=_MiniMaxH3Ctx(
        bundle=bundle, conditioning=[_fake_conditioning(3) for _ in plan.windows], steps=steps,
        height=32, width=32, frames=DIRECTOR_FRAMES, num_latent_frames=DIRECTOR_LATENTS,
        latent_height=2, latent_width=2, num_audio_latents=DIRECTOR_AUDIO_LATENTS,
        device="cpu", dtype=torch.float32, spec=bundle.spec, keyframe_images=[], keyframe_anchors=(),
        references=tuple(references), audio_source="generate", decode=True, plan=plan,
    ))

    pipe = _RecordingPipe({
        **GeneratorMinimaxH3Pipe.get_default_config(), "mode": "references", "preview": False, "steps": steps,
        **(config_overrides or {}),
    })
    pipe._audio_results = []
    pipe._video_resolution = (32, 32)
    progress = ProgressEmitter([].append, title="test")

    condition_dim = 24 * PATCH[0] * PATCH[1] * PATCH[2]
    fake_latent = torch.zeros(1, 24, 1, 2, 2)
    calls: list = []

    def _fake_prepare(refs, **kwargs):
        calls.append(tuple(refs))
        return ReferenceConditioning(
            blocks=tuple(ReferenceBlock(kind="image") for _ in refs),
            condition_latents=tuple(fake_latent for _ in refs),
            audio_condition_latents=(),
            condition_rows=torch.zeros(len(refs), condition_dim),
            condition_audio_rows=None,
        )

    with (
        patch("src.pipelines.pipes.generator.video_minimax_h3.main.encode_frames_to_mp4") as mock_encode,
        patch(
            "src.pipelines.pipes.generator.video_minimax_h3.main.prepare_reference_conditioning", _fake_prepare,
        ),
        patch(
            "src.pipelines.pipes.generator.video_minimax_h3.main.prepare_keyframe_condition_rows",
        ) as mock_prepare_keyframes,
        patch(
            "src.pipelines.pipes.generator.video_minimax_h3.main.build_packed_sequence",
        ) as mock_build_t2va,
    ):
        mock_encode.side_effect = lambda frames, path, **kw: path
        pipe.generate_one(ctx, 0, 7, progress)

    return pipe, calls, plan, mock_prepare_keyframes, mock_build_t2va


def test_whole_film_references_reach_every_window_in_full():
    references = [_ref_image_media((4, 4)), _ref_image_media((4, 4))]
    document = _director_document(2, sub_types={1: "t2v"})
    pipe, calls, plan, mock_keyframes, mock_t2va = _run_refs_director(document, references)

    assert len(calls) == 2  # prepare_reference_conditioning ran once per window
    assert list(calls[0]) == references
    assert list(calls[1]) == references
    # And NEVER through the fl2va/t2va keyframe-overlay path.
    assert mock_keyframes.call_count == 0
    assert mock_t2va.call_count == 0


def test_a_per_shot_selection_narrows_that_windows_reference_set():
    references = [_ref_image_media((4, 4)), _ref_image_media((4, 4))]
    document = _director_document(2, sub_types={1: "t2v"})
    document["segments"][1]["reference_indices"] = [1]
    pipe, calls, plan, *_ = _run_refs_director(document, references)

    assert plan.windows[1].reference_indices == (1,)
    assert list(calls[0]) == references           # window 0: no selection -> every reference
    assert list(calls[1]) == [references[1]]       # window 1: 2-of-2 subset -> index 1 alone


def test_bite_check_no_selection_would_also_pass_a_vacuous_subset_assertion():
    # BITE CHECK for the selection test above: without `reference_indices`
    # set at all, window 1 must get the FULL set too -- so the subset
    # assertion above is verifying an actual narrowing, not just "not empty".
    references = [_ref_image_media((4, 4)), _ref_image_media((4, 4))]
    document = _director_document(2, sub_types={1: "t2v"})
    pipe, calls, plan, *_ = _run_refs_director(document, references)
    assert list(calls[1]) == references


def test_single_window_refs_and_a_1_shot_refs_director_plan_are_bit_identical():
    """The degenerate-equivalence check: a hard-cut, 1-segment Director run
    conditioned on the same references, seed and geometry as an ordinary
    single-window ref2va request must drive the DiT with the EXACT same
    rows, in the exact same order -- `_generate_director`'s refs branch and
    `generate_one`'s own ref2va branch share `_build_ref2va_layout`, and this
    is what proves the sharing actually keeps them in lockstep rather than
    two implementations that happen to look similar."""
    def _bundle(forwards):
        def dit_forward(*, hidden_states, audio_hidden_states, **kwargs):
            forwards.append((hidden_states[0].clone(), audio_hidden_states[0].clone()))
            return hidden_states * 0.1, audio_hidden_states * -0.2

        return SimpleNamespace(
            spec=SimpleNamespace(family="minimax_h3", variant="h3", sampling_settings={},
                                 latent_format={"format": "minimax_h3", "latent_channels": 24}),
            dit=SimpleNamespace(compute_dtype=torch.float32, estimated_vram_gb=0.0, module=dit_forward,
                                move_to=lambda d: None, offload=lambda: None),
            video_vae=SimpleNamespace(module=_RefVideoVae(), compute_dtype=torch.float32,
                                      move_to=lambda d: None, offload=lambda: None),
            audio_vae=SimpleNamespace(module=_RefAudioVae(), move_to=lambda d: None, offload=lambda: None),
            te=None, te_cache_key=None,
        )

    references = (_ref_image_media((4, 4)),)

    single_forwards: list = []
    single_bundle = _bundle(single_forwards)
    single_ctx = GeneratorContext(quantity=1, input_seeds=[7], extra=_MiniMaxH3Ctx(
        bundle=single_bundle, conditioning=[_fake_conditioning(3)], steps=2,
        height=32, width=32, frames=DIRECTOR_FRAMES, num_latent_frames=DIRECTOR_LATENTS,
        latent_height=2, latent_width=2, num_audio_latents=DIRECTOR_AUDIO_LATENTS,
        device="cpu", dtype=torch.float32, spec=single_bundle.spec,
        keyframe_images=[], keyframe_anchors=(), references=references,
        audio_source="generate", decode=True,
    ))
    single_pipe = GeneratorMinimaxH3Pipe({**GeneratorMinimaxH3Pipe.get_default_config(),
                                          "mode": "references", "preview": False, "steps": 2})
    single_pipe._audio_results = []
    with patch("src.pipelines.pipes.generator.video_minimax_h3.main.encode_frames_to_mp4") as mock_encode:
        mock_encode.side_effect = lambda frames, path, **kw: path
        single_pipe.generate_one(single_ctx, 0, 7, ProgressEmitter([].append, title="single"))

    director_document = _director_document(1, seed=7, frames=DIRECTOR_FRAMES)
    director_forwards: list = []
    director_bundle = _bundle(director_forwards)
    plan = build_director_plan(director_document, default_seed=-1)
    assert plan.windows[0].seed == 7 and not plan.windows[0].continues_previous
    director_ctx = GeneratorContext(quantity=1, input_seeds=[7], extra=_MiniMaxH3Ctx(
        bundle=director_bundle, conditioning=[_fake_conditioning(3)], steps=2,
        height=32, width=32, frames=DIRECTOR_FRAMES, num_latent_frames=DIRECTOR_LATENTS,
        latent_height=2, latent_width=2, num_audio_latents=DIRECTOR_AUDIO_LATENTS,
        device="cpu", dtype=torch.float32, spec=director_bundle.spec,
        keyframe_images=[], keyframe_anchors=(), references=references,
        audio_source="generate", decode=True, plan=plan,
    ))
    director_pipe = GeneratorMinimaxH3Pipe({**GeneratorMinimaxH3Pipe.get_default_config(),
                                            "mode": "references", "preview": False, "steps": 2})
    director_pipe._audio_results = []
    director_pipe._video_resolution = (32, 32)
    with patch("src.pipelines.pipes.generator.video_minimax_h3.main.encode_frames_to_mp4") as mock_encode:
        mock_encode.side_effect = lambda frames, path, **kw: path
        director_pipe.generate_one(director_ctx, 0, 7, ProgressEmitter([].append, title="director"))

    assert len(single_forwards) == len(director_forwards) > 0
    for (single_video, single_audio), (director_video, director_audio) in zip(single_forwards, director_forwards):
        torch.testing.assert_close(single_video, director_video, rtol=0, atol=0)
        torch.testing.assert_close(single_audio, director_audio, rtol=0, atol=0)


def test_stitch_false_emits_every_window_and_stitches_nothing():
    pipe, _, encodes, plan = _run_director(_director_document(3, stitch=False))
    assert len(encodes) == 3
    assert isinstance(pipe._last_result, list) and len(pipe._last_result) == 3
    assert len(pipe._audio_results) == 3
    assert pipe.build_output([pipe._last_result])["video"] == pipe._last_result


def test_a_cut_segment_takes_no_overlap_and_is_emitted_whole():
    pipe, _, encodes, plan = _run_director(_director_document(2, sub_types={1: "i2v"}))
    assert plan.windows[1].overlap_latents == 0
    assert len(encodes[1].args[0]) == DIRECTOR_FRAMES
    assert pipe.audio_calls[1][2] == 0


def test_each_window_seeds_its_own_generator():
    """Windows must not share one generator: the same segment has to render
    the same way wherever it sits in the document."""
    steps = 3
    _, forwards, _, plan = _run_director(_director_document(2, sub_types={1: "t2v"}), steps=steps)
    per_window = int(build_sigma_schedule(steps, VIDEO_SHIFT).timesteps.numel())
    assert plan.windows[0].seed != plan.windows[1].seed
    assert not torch.allclose(forwards[0][0], forwards[per_window][0])
    assert not torch.allclose(forwards[0][1], forwards[per_window][1])


def test_a_pinned_segment_seed_reproduces_that_window_exactly():
    document = _director_document(2, sub_types={1: "t2v"})
    document["segments"][1]["seed"] = document["segments"][0]["seed"] = 4242
    steps = 3
    _, forwards, _, _ = _run_director(document, steps=steps)
    per_window = int(build_sigma_schedule(steps, VIDEO_SHIFT).timesteps.numel())
    # Two independent t2v windows of identical geometry on identical seeds are
    # the same generation, which is what "one generator per window" buys.
    torch.testing.assert_close(forwards[0][0], forwards[per_window][0], rtol=0, atol=0)


def test_per_segment_steps_drive_that_windows_loop_alone():
    document = _director_document(2)
    document["segments"][0]["steps"] = 6
    _, forwards, _, _ = _run_director(document, steps=3)
    first = int(build_sigma_schedule(6, VIDEO_SHIFT).timesteps.numel())
    second = int(build_sigma_schedule(3, VIDEO_SHIFT).timesteps.numel())
    assert len(forwards) == first + second


def test_a_director_run_forces_one_composed_clip_regardless_of_quantity():
    pipe = GeneratorMinimaxH3Pipe({
        **GeneratorMinimaxH3Pipe.get_default_config(), "quantity": 4,
        "document": _director_document(2),
    })
    ctx = pipe.build_context(_pipe_input(model=_fake_bundle(), conditioning=[_fake_conditioning(3)]))
    assert ctx.quantity == 1
    assert ctx.extra.plan is not None and len(ctx.extra.plan.windows) == 2


def test_a_document_placing_an_image_the_loader_never_produced_fails_loudly():
    document = _director_document(1)
    document["media"] = [{"role": "keyframe", "at": 1.0, "segment_id": None, "strength": 1.0,
                          "media": {"path": "/k.png", "type": "image"}}]
    document["media_images"] = ["/k.png"]
    document["media_placements"] = [
        {"source": "image", "index": 0, "frame": 24, "strength": 1.0, "role": "keyframe"},
    ]
    pipe = GeneratorMinimaxH3Pipe({**GeneratorMinimaxH3Pipe.get_default_config(), "document": document})
    with pytest.raises(DirectorPlanError, match="director image"):
        pipe.build_context(_pipe_input(model=_fake_bundle(), conditioning=[_fake_conditioning(3)]))


def test_a_director_run_cannot_hand_off_a_raw_latent():
    pipe = GeneratorMinimaxH3Pipe({
        **GeneratorMinimaxH3Pipe.get_default_config(), "decode": False, "document": _director_document(2),
    })
    with pytest.raises(DirectorPlanError, match="has to decode"):
        pipe.build_context(_pipe_input(model=_fake_bundle(), conditioning=[_fake_conditioning(3)]))


# -- the non-director path is untouched -----------------------------------------

@pytest.mark.parametrize("steps,digest", [
    (4, "65e137c8624f7b4a97839f246d4f13f3a294701d6567d4c8db799b93669b3945"),
    (6, "88f30fee8a07d7969c92da95456e0ad7f608daea0d1e7750802d33f41ebdc938"),
])
def test_the_single_window_latent_is_unchanged_by_the_windowing_work(steps, digest):
    """Pinned against a run captured BEFORE the Director windowing landed. The
    window loop shares `_sample_window` with the ordinary path, so this is what
    proves the extraction moved code without changing arithmetic.

    RE-CAPTURED 2026-08-11 with the steps=evaluations fix: `steps` now drives
    N forwards on the ModelTC knots instead of N-1 on the wrong ones, so the
    latent moved for a reason that has nothing to do with windowing. The two
    digests below are the only ones in this suite that were regenerated
    rather than reasoned about -- a mismatch here still means the sampling
    path changed and wants explaining, not re-pinning.
    """
    import hashlib

    pipe, _ = _run_generate_one({}, steps=steps, ctx_overrides={"decode": False})
    assert hashlib.sha256(pipe._last_result.detach().cpu().numpy().tobytes()).hexdigest() == digest


def test_no_document_means_no_plan():
    pipe = GeneratorMinimaxH3Pipe(GeneratorMinimaxH3Pipe.get_default_config())
    ctx = pipe.build_context(_pipe_input(model=_fake_bundle(), conditioning=[_fake_conditioning(3)]))
    assert ctx.extra.plan is None
    assert GeneratorMinimaxH3Pipe.get_default_config()["document"] is None


# -- Director images inside a window ---------------------------------------------

class _FakeKeyframeVae:
    """Enough of the video VAE for `encode_keyframe_condition`: a float
    parameter to pick an encode dtype from, and a deterministic encode."""

    latents_mean = torch.zeros(24)
    latents_std = torch.ones(24)

    def parameters(self):
        return iter([torch.zeros(1, dtype=torch.float32)])

    def encode(self, pixels, sample_posterior=False, generator=None):
        b, _, _, h, w = pixels.shape
        return torch.full((b, 24, 1, h // 16, w // 16), 0.25)


def _document_with_keyframe(at_frame, *, segment_count=2):
    document = _director_document(segment_count)
    document["media"] = [{"role": "keyframe", "at": at_frame / 24, "segment_id": None, "strength": 1.0,
                          "media": {"path": "/k.png", "type": "image"}}]
    document["media_images"] = ["/k.png"]
    document["media_placements"] = [
        {"source": "image", "index": 0, "frame": at_frame, "strength": 1.0, "role": "keyframe"},
    ]
    return document


def _run_director_with_keyframe(document, *, steps=3):
    """`_run_director` with a video VAE that can actually encode a keyframe."""
    from PIL import Image

    images = [Image.new("RGB", (32, 32), (10, 20, 30))]
    forwards: list = []

    def dit_forward(*, hidden_states, audio_hidden_states, **kwargs):
        forwards.append((hidden_states[0].clone(), audio_hidden_states[0].clone()))
        return hidden_states * 0.1, audio_hidden_states * -0.2

    vae_module = _FakeKeyframeVae()
    bundle = SimpleNamespace(
        spec=SimpleNamespace(family="minimax_h3", variant="h3", sampling_settings={},
                             latent_format={"format": "minimax_h3", "latent_channels": 24}),
        dit=SimpleNamespace(compute_dtype=torch.float32, estimated_vram_gb=0.0, module=dit_forward,
                            move_to=lambda d: None, offload=lambda: None),
        video_vae=SimpleNamespace(module=vae_module, compute_dtype=torch.float32,
                                  move_to=lambda d: None, offload=lambda: None),
        audio_vae=SimpleNamespace(module=None, move_to=lambda d: None, offload=lambda: None),
        te=None, te_cache_key=None,
    )
    plan = build_director_plan(document, default_seed=-1)
    ctx = GeneratorContext(quantity=1, input_seeds=[7], extra=_MiniMaxH3Ctx(
        bundle=bundle, conditioning=[_fake_conditioning(3) for _ in plan.windows], steps=steps,
        height=32, width=32, frames=DIRECTOR_FRAMES, num_latent_frames=DIRECTOR_LATENTS,
        latent_height=2, latent_width=2, num_audio_latents=DIRECTOR_AUDIO_LATENTS,
        device="cpu", dtype=torch.float32, spec=bundle.spec, keyframe_images=[], keyframe_anchors=(),
        audio_source="generate", decode=True, plan=plan, director_images=list(images),
    ))
    pipe = _RecordingPipe({**GeneratorMinimaxH3Pipe.get_default_config(), "preview": False, "steps": steps})
    pipe._audio_results = []
    pipe._video_resolution = (32, 32)
    with patch("src.pipelines.pipes.generator.video_minimax_h3.main.encode_frames_to_mp4") as mock_encode:
        mock_encode.side_effect = lambda frames, path, **kw: path
        pipe.generate_one(ctx, 0, 7, ProgressEmitter([].append, title="test"))
        encodes = list(mock_encode.call_args_list)
    return pipe, forwards, encodes, plan


def test_a_director_keyframe_adds_a_condition_row_after_the_continuation_tail():
    """Order is the contract: `build_packed_sequence` lays condition blocks out
    positionally against `keyframe_anchors`, so the tail latents must occupy
    the leading rows and the document's images follow."""
    steps = 3
    at_frame = DIRECTOR_FRAMES + 6   # inside window 1
    pipe, forwards, _, plan = _run_director_with_keyframe(_document_with_keyframe(at_frame), steps=steps)
    per_window = int(build_sigma_schedule(steps, VIDEO_SHIFT).timesteps.numel())
    window = plan.windows[1]
    assert len(window.keyframes) == 1

    rows_per_frame = 1  # a 2x2 latent canvas at patch (1,2,2)
    condition_rows = (window.overlap_latents + 1) * rows_per_frame
    video_rows_seen = forwards[per_window][0]
    assert video_rows_seen.shape[0] == condition_rows + DIRECTOR_LATENTS * rows_per_frame

    # The image's row is the LAST condition row, and it is the (k+1)-th draw
    # off this window's generator -- after every tail-latent draw.
    generator = torch.Generator(device="cpu").manual_seed(window.seed)
    for _ in range(window.overlap_latents):
        torch.randn((1, 24, 1, 2, 2), generator=generator, device="cpu", dtype=torch.float32)
    encoded = torch.full((1, 24, 1, 2, 2), 0.25).to(torch.float16).float()
    noise = torch.randn(encoded.shape, generator=generator, device="cpu", dtype=torch.float32)
    expected = patchify_video_latents(scale_noise(encoded, KEYFRAME_NOISE_AUG, noise), PATCH)
    torch.testing.assert_close(
        video_rows_seen[condition_rows - rows_per_frame:condition_rows], expected, rtol=0, atol=0,
    )


def test_a_keyframe_in_the_first_window_needs_no_continuation_tail():
    steps = 3
    pipe, forwards, _, plan = _run_director_with_keyframe(_document_with_keyframe(40), steps=steps)
    assert len(plan.windows[0].keyframes) == 1 and plan.windows[1].keyframes == ()
    assert forwards[0][0].shape[0] == 1 + DIRECTOR_LATENTS


# -- sparse-attention wiring (Sol-Attn method) --------------------------------

def test_sparse_attn_is_off_by_default_and_no_context_reaches_the_dit():
    assert GeneratorMinimaxH3Pipe.get_default_config()["sparse_attn"] == "off"
    seen_sol_attn: list = []
    _run_generate_one({}, seen_sol_attn=seen_sol_attn)
    assert seen_sol_attn and all(entry is None for entry in seen_sol_attn)


# `steps` counts model EVALUATIONS, so a run of N steps makes exactly N
# forwards on an N+1 value sigma grid -- every expectation below is N long.

def test_enabled_sol_attn_reaches_every_step_with_the_layout_sink():
    """The sink must be this window's own target-video start, not a constant:
    here [text(3) | audio(4) | video(2)] puts it at row 7 of 9."""
    seen_sol_attn: list = []
    _run_generate_one(
        {"sparse_attn": "sol", "sol_attn_tau": 1.4, "sparse_attn_dense_last_steps": 0},
        steps=4, seen_sol_attn=seen_sol_attn,
    )
    assert len(seen_sol_attn) == 4
    assert all(entry == (1.4, 7, False) for entry in seen_sol_attn)


def test_sol_attn_runs_dense_on_the_trailing_steps():
    seen_sol_attn: list = []
    _run_generate_one(
        {"sparse_attn": "sol", "sparse_attn_dense_last_steps": 2}, steps=5, seen_sol_attn=seen_sol_attn,
    )
    assert [entry[2] for entry in seen_sol_attn] == [False, False, False, True, True]


def test_sol_attn_dense_window_larger_than_the_run_is_dense_throughout():
    """The documented "effectively off" case: nothing is approximated, and the
    generation still completes."""
    seen_sol_attn: list = []
    _run_generate_one(
        {"sparse_attn": "sol", "sparse_attn_dense_last_steps": 9}, steps=3, seen_sol_attn=seen_sol_attn,
    )
    assert [entry[2] for entry in seen_sol_attn] == [True, True, True]


def test_sol_attn_composes_with_the_step_cache():
    """Both knobs on at once: the cache still sees every step but the last, and
    the Sol-Attn context still reaches every step."""
    seen_sol_attn: list = []
    _, seen_caches = _run_generate_one(
        {"sparse_attn": "sol", "step_cache_threshold": 0.12}, steps=4, seen_sol_attn=seen_sol_attn,
    )
    assert [cache is not None for cache in seen_caches] == [True, True, True, False]
    assert all(entry is not None for entry in seen_sol_attn)


def test_an_unknown_sparse_attn_method_reaches_no_context():
    """A preset typo must not fail a generation -- it degrades to off."""
    seen_sol_attn: list = []
    _run_generate_one({"sparse_attn": "bogus"}, seen_sol_attn=seen_sol_attn)
    assert seen_sol_attn and all(entry is None for entry in seen_sol_attn)


# -- sparse-attention placement reserve ---------------------------------------

# Big enough that the packed sequence clears the routing threshold, so the
# estimate is non-zero and the assertions below mean something.
_BIG_LAYOUT = {"num_latent_frames": 8, "latent_height": 16, "latent_width": 16}


def _placement_calls(config_overrides: dict, ctx_overrides: dict | None = None):
    """Run a generation with the DiT placement stubbed, and return its kwargs."""
    with patch("src.pipelines.pipes.generator.video_minimax_h3.main.place_dit_for_sequence") as placement:
        _run_generate_one(config_overrides, ctx_overrides=ctx_overrides)
    return [call.kwargs for call in placement.call_args_list]


def test_disabled_sparse_attn_reserves_nothing_from_the_dit_placement():
    """Pins the pre-sparse-attention behaviour: the placement sees the 0.0 it
    has always defaulted to, so an untouched form places the DiT exactly as
    before."""
    calls = _placement_calls({}, ctx_overrides=_BIG_LAYOUT)
    assert calls
    assert all(kwargs["reserve_gb"] == 0.0 for kwargs in calls)


def test_enabled_sol_attn_reserves_its_transients_from_the_dit_placement():
    """The fix for the field OOM: the placement must know Sol-Attn's copies
    exist BEFORE it decides how much of the DiT to keep resident."""
    calls = _placement_calls({"sparse_attn": "sol"}, ctx_overrides=_BIG_LAYOUT)
    assert calls
    assert all(kwargs["reserve_gb"] > 0.0 for kwargs in calls)


def test_the_sol_reserved_amount_is_the_estimate_for_this_window():
    from src.platform.runtime.native.sol_attn import estimate_transient_gb

    from src.pipelines.pipes.generator.video_minimax_h3.main import H3_HEAD_DIM, H3_NUM_HEADS

    calls = _placement_calls({"sparse_attn": "sol"}, ctx_overrides=_BIG_LAYOUT)
    kwargs = calls[0]
    seq_len = kwargs["video_tokens"] + kwargs["audio_tokens"]
    assert kwargs["reserve_gb"] == estimate_transient_gb(seq_len, H3_NUM_HEADS, H3_HEAD_DIM)


def test_a_larger_window_reserves_more():
    small = _placement_calls({"sparse_attn": "sol"}, ctx_overrides=_BIG_LAYOUT)[0]
    large = _placement_calls(
        {"sparse_attn": "sol"}, ctx_overrides=dict(_BIG_LAYOUT, num_latent_frames=16),
    )[0]
    assert large["reserve_gb"] > small["reserve_gb"]


def test_enabled_sla_reserves_its_transients_from_the_dit_placement():
    """The other method reaches the same placement seam. SLA's own routing
    threshold (8192 tokens) is far above Sol-Attn's, so this needs a bigger
    window than `_BIG_LAYOUT`."""
    sla_layout = {"num_latent_frames": 32, "latent_height": 32, "latent_width": 32}
    calls = _placement_calls({"sparse_attn": "sla"}, ctx_overrides=sla_layout)
    assert calls
    assert all(kwargs["reserve_gb"] > 0.0 for kwargs in calls)


# -- sampler / scheduler wiring into the loop ---------------------------------

def _recording_steppers(monkeypatch):
    """Wrap `make_stepper` so every stepper handed to the loop is recorded
    along with the `(model_output, sigma, sigma_next)` of every step it took."""
    from src.pipelines.pipes.generator.video_minimax_h3 import main as h3_main
    from src.pipelines.pipes.generator.video_minimax_h3 import samplers as h3_samplers

    built: list = []

    def recording_make_stepper(sampler, *, generator=None):
        inner = h3_samplers.make_stepper(sampler, generator=generator)
        record: list = []
        built.append((sampler, generator, record))

        class _Spy:
            def step(self, model_output, timestep, sample, sigma, sigma_next):
                record.append((model_output.clone(), float(sigma), float(sigma_next)))
                return inner.step(model_output, timestep, sample, sigma, sigma_next)

        return _Spy()

    monkeypatch.setattr(h3_main, "make_stepper", recording_make_stepper)
    return built


@pytest.mark.parametrize("sampler", ("res_multistep", "dpmpp_2m", "sa_solver", "er_sde"))
def test_the_sampler_choice_reaches_the_loop_and_changes_the_latent(sampler, monkeypatch):
    """Same seed, same schedule, different solver -- if the knob were dropped
    on the way to the loop these would be bit-identical to the euler run."""
    built = _recording_steppers(monkeypatch)
    pipe, _ = _run_generate_one({}, steps=5, ctx_overrides={"decode": False, "sampler": sampler})
    assert {name for name, _, _ in built} == {sampler}

    euler_pipe, _ = _run_generate_one({}, steps=5, ctx_overrides={"decode": False})
    assert not torch.allclose(pipe._last_result, euler_pipe._last_result)


def test_er_sde_gets_the_windows_own_seeded_generator(monkeypatch):
    """`er_sde` draws noise every step -- it must get the SAME seeded
    `torch.Generator` the window's video/audio/conditioning noise already
    comes from (`_sample_window`'s own `generator` parameter), not an
    unseeded global RNG, or two identically-seeded requests would not
    reproduce (see the test below)."""
    built = _recording_steppers(monkeypatch)
    _run_generate_one({}, steps=4, ctx_overrides={"sampler": "er_sde"})
    assert len(built) == 2  # one stepper per stream
    for _, generator, _ in built:
        assert isinstance(generator, torch.Generator)


def test_er_sde_is_reproducible_across_two_identical_generations():
    pipe_a, _ = _run_generate_one({}, steps=4, ctx_overrides={"decode": False, "sampler": "er_sde"})
    pipe_b, _ = _run_generate_one({}, steps=4, ctx_overrides={"decode": False, "sampler": "er_sde"})
    torch.testing.assert_close(pipe_a._last_result, pipe_b._last_result, rtol=0, atol=0)


def test_the_default_sampler_is_euler(monkeypatch):
    built = _recording_steppers(monkeypatch)
    _run_generate_one({}, steps=3)
    assert {name for name, _, _ in built} == {"euler"}


@pytest.mark.parametrize("key,value", [
    ("sampler", "res_multistep"), ("sampler", "dpmpp_2m"), ("sampler", "sa_solver"),
    ("sampler", "er_sde"), ("scheduler", "beta"),
])
def test_build_context_carries_the_solver_knobs_off_the_pipe_config(key, value):
    """The half of the chain `generate_one` cannot see: the pipe config is what
    the preset renders into, and `build_context` is where it becomes the ctx
    the sampling loop actually reads."""
    pipe = GeneratorMinimaxH3Pipe({**GeneratorMinimaxH3Pipe.get_default_config(), key: value})
    ctx = pipe.build_context(_pipe_input(model=_fake_bundle(), conditioning=[_fake_conditioning(3)]))
    assert getattr(ctx.extra, key) == value


def test_build_context_defaults_the_solver_knobs_to_the_reference_pair():
    pipe = GeneratorMinimaxH3Pipe(GeneratorMinimaxH3Pipe.get_default_config())
    ctx = pipe.build_context(_pipe_input(model=_fake_bundle(), conditioning=[_fake_conditioning(3)]))
    assert (ctx.extra.sampler, ctx.extra.scheduler) == ("euler", "simple")


def test_each_stream_gets_its_own_stepper_walking_its_own_sigmas(monkeypatch):
    """Video and audio advance on differently shifted grids inside ONE forward,
    so a shared stepper would mix two trajectories' history."""
    built = _recording_steppers(monkeypatch)
    _run_generate_one({}, steps=4, ctx_overrides={"sampler": "res_multistep"})

    assert len(built) == 2
    walked = [[sigma for _, sigma, _ in record] for _, _, record in built]
    assert walked[0] == pytest.approx(build_sigma_schedule(4, VIDEO_SHIFT).sigmas.tolist()[:-1])
    assert walked[1] == pytest.approx(build_sigma_schedule(4, AUDIO_SHIFT).sigmas.tolist()[:-1])
    assert walked[0] != walked[1]


def test_every_step_reaches_the_stepper_even_with_the_cache_on(monkeypatch):
    """A cached skip replays the previous velocity, and that replayed value has
    to enter the multistep history like any other: the loop must not skip the
    scheduler update along with the transformer call."""
    built = _recording_steppers(monkeypatch)
    _run_generate_one(
        {"step_cache_threshold": 0.12}, steps=6, ctx_overrides={"sampler": "res_multistep"},
    )
    assert [len(record) for _, _, record in built] == [6, 6]


def test_a_multistep_history_never_crosses_a_window_boundary(monkeypatch):
    """Each Director window is an independent trajectory restarting from pure
    noise, so it must get FRESH steppers -- carrying `D_prev` across would
    extrapolate the previous shot into this one's first step."""
    built = _recording_steppers(monkeypatch)
    _run_director(_director_document(3), steps=3)

    # Three windows x (video, audio), never reused.
    assert len(built) == 6
    for _, _, record in built:
        assert len(record) == 3


# -- ref2va: all three reference modalities through the real chain -----------
#
# `generate_one`'s reference branch with the REAL `prepare_reference_
# conditioning` and the REAL `build_ref2va_packed_sequence` (only the VAEs and
# the DiT are fakes), because the property under test is that ONE traversal of
# `ctx.extra.references` feeds the blocks, both latent iterators and the row
# prefixes. Faking either of those two functions would prove nothing about it.

_REF_AUDIO_HOP = 800


class _RefVideoVae:
    """The video VAE's shape contract: `17n + 5` frames encode to `5n + 2`
    latent frames, one frame stays one, spatial size passes through (so a
    reference's latent geometry is the caller's own and each reference is
    identifiable by it)."""

    latents_mean = torch.zeros(24)
    latents_std = torch.ones(24)

    def encode(self, x, *, sample_posterior=False, generator=None):
        b, _c, f, h, w = x.shape
        latent_frames = 1 if f == 1 else (f - 5) // 17 * 5 + 2
        shape = (b, 24, latent_frames, h, w)
        mean = torch.full(shape, 0.5)
        if not sample_posterior:
            return mean
        return mean + torch.exp(torch.tensor(1.0)) * torch.randn(shape, generator=generator)

    def decode(self, z):
        b, _c, f, h, w = z.shape
        return torch.rand(b, 3, f, h, w)

    def parameters(self):
        return iter([torch.zeros(1)])


class _RefAudioVae:
    """`(B, 1, samples)` -> `(B, 32, ceil(samples / hop))`, deterministic --
    the real encode returns the posterior mean and draws no noise."""

    def __init__(self):
        self.latents_mean = torch.zeros(32)
        self.latents_std = torch.ones(32)

    def encode(self, sample):
        num = -(-sample.shape[-1] // _REF_AUDIO_HOP)
        return torch.full((sample.shape[0], 32, num), 0.25)

    def decode(self, latents):
        return torch.zeros(2, 1, latents.shape[-1] * 4)


def _ref_image(size=(4, 4)):
    from PIL import Image

    return Image.new("RGB", size, color=(30, 60, 90))


def _ref_image_media(size=(4, 4)):
    return ReferenceMedia(kind="image", image=_ref_image(size))


def _ref_video(num_frames=22, size=4):
    """An ALREADY-NORMALIZED reference video: `build_context` is what fits and
    resamples, and this bypasses it to keep the fake VAE's tensors tiny."""
    rng = np.random.default_rng(0)
    frames = rng.integers(0, 256, (num_frames, size, size, 3), dtype=np.uint8)
    return ReferenceMedia(kind="video", frames=frames, fps=24.0)


def _ref_audio(num_latents=3):
    return ReferenceMedia(
        kind="audio", audio=torch.zeros(2, num_latents * _REF_AUDIO_HOP), sample_rate=32000,
    )


def _run_ref2va_generate_one(references, tmp_path):
    """Drive `generate_one` over `references` and return the PackedLayout the
    real `build_ref2va_packed_sequence` produced, plus its call args."""
    video_patch_dim = 24 * PATCH[0] * PATCH[1] * PATCH[2]
    dit_native = SimpleNamespace(
        compute_dtype=torch.float32, estimated_vram_gb=0.0, module=_fake_dit_module(video_patch_dim),
        move_to=lambda d: None, offload=lambda: None,
    )
    bundle = SimpleNamespace(
        spec=SimpleNamespace(family="minimax_h3", variant="h3", sampling_settings={},
                             latent_format={"format": "minimax_h3", "latent_channels": 24}),
        dit=dit_native,
        video_vae=SimpleNamespace(module=_RefVideoVae(), compute_dtype=torch.float32,
                                  move_to=lambda d: None, offload=lambda: None),
        audio_vae=SimpleNamespace(module=_RefAudioVae(), move_to=lambda d: None, offload=lambda: None),
        te=None, te_cache_key=None,
    )
    ctx = GeneratorContext(quantity=1, input_seeds=[7], extra=_MiniMaxH3Ctx(
        bundle=bundle, conditioning=[_fake_conditioning(3)], steps=2,
        height=32, width=32, frames=22, num_latent_frames=2, latent_height=2, latent_width=2,
        num_audio_latents=2, device="cpu", dtype=torch.float32, spec=bundle.spec,
        keyframe_images=[], keyframe_anchors=(), references=tuple(references),
        audio_source="generate", decode=True,
    ))

    pipe = GeneratorMinimaxH3Pipe({**GeneratorMinimaxH3Pipe.get_default_config(),
                                   "mode": "references", "preview": False})
    pipe._audio_results = []
    progress = ProgressEmitter([].append, title="test")

    real_build = build_ref2va_packed_sequence
    captured: dict = {}

    def _spy(*args, **kwargs):
        layout = real_build(*args, **kwargs)
        captured["args"] = args
        captured["layout"] = layout
        return layout

    with (
        patch("src.pipelines.pipes.generator.video_minimax_h3.main.encode_frames_to_mp4") as mock_encode,
        patch("src.pipelines.pipes.generator.video_minimax_h3.main.build_ref2va_packed_sequence", _spy),
    ):
        mock_encode.return_value = tmp_path / "out.mp4"
        pipe.generate_one(ctx, 0, 7, progress)
    return captured


def test_a_video_reference_reaches_the_generator_as_a_frame_stack(tmp_path):
    """A 22-frame reference is one VIDEO block whose latent carries `5n + 2`
    frames, not 1 -- the whole difference between a video reference and an
    image one at the layout level, and what its row count is derived from."""
    captured = _run_ref2va_generate_one([_ref_video(num_frames=22, size=4)], tmp_path)

    blocks, condition_latents, audio_condition_latents = captured["args"][1:4]
    assert [b.kind for b in blocks] == ["video"]
    assert tuple(condition_latents[0].shape[2:5]) == (7, 4, 4)  # (22 - 5)//17 * 5 + 2 = 7
    assert audio_condition_latents == ()
    # 7 latent frames x (4//2 * 4//2) rows per frame.
    assert captured["layout"].num_condition_video_rows == 28
    assert captured["layout"].num_condition_audio_rows == 0


def test_an_audio_reference_populates_the_audio_condition_latents(tmp_path):
    """An audio reference contributes CLEAN audio rows and no visual latent at
    all, so it reaches the layout through the second iterator only."""
    captured = _run_ref2va_generate_one([_ref_image_media(), _ref_audio(num_latents=3)], tmp_path)

    blocks, condition_latents, audio_condition_latents = captured["args"][1:4]
    assert [b.kind for b in blocks] == ["image", "audio"]
    assert [b.has_audio for b in blocks] == [False, True]
    # One visual latent (the image only) and one audio row block, packed
    # channel-major: 2 channels x 3 latents.
    assert len(condition_latents) == 1
    assert len(audio_condition_latents) == 1
    assert tuple(audio_condition_latents[0].shape) == (6, 32)
    assert captured["layout"].num_condition_audio_rows == 6


def test_an_audio_only_reference_set_is_refused(monkeypatch):
    """A soundtrack conditions a video some visual reference has to anchor --
    the released checkpoint's own rule. `build_context` normalizes the
    references, so it fires there: before the DiT is placed, not after."""
    import src.pipelines.pipes.generator.video_minimax_h3.main as main_module

    monkeypatch.setattr(main_module, "_load_reference_audio", lambda path: _ref_audio())
    pipe = GeneratorMinimaxH3Pipe({
        **GeneratorMinimaxH3Pipe.get_default_config(), "mode": "references",
        "reference_audios": [{"path": "/media/track.wav"}],
    })
    with pytest.raises(ValueError, match="cannot be used"):
        pipe.build_context(_pipe_input(
            model=_fake_bundle(), conditioning=[_fake_conditioning(3)],
            reference_audios=["/media/track.wav"],
        ))


def test_build_context_packs_the_three_modality_inputs_in_one_order(monkeypatch):
    """The generator's own half of the ordering contract: three separate
    inputs collapse to images, then videos, then audio -- never the order the
    edges happen to be declared in."""
    import src.pipelines.pipes.generator.video_minimax_h3.main as main_module

    monkeypatch.setattr(main_module, "_load_reference_video", lambda path: _ref_video())
    monkeypatch.setattr(main_module, "_load_reference_audio", lambda path: _ref_audio())
    pipe = GeneratorMinimaxH3Pipe({
        **GeneratorMinimaxH3Pipe.get_default_config(), "mode": "references",
        "references": [{"path": "a.png"}, {"path": "b.png"}],
        "reference_videos": [{"path": "c.mp4"}],
        "reference_audios": [{"path": "d.wav"}],
    })
    ctx = pipe.build_context(_pipe_input(
        model=_fake_bundle(), conditioning=[_fake_conditioning(3)],
        reference_images=[_ref_image(), _ref_image()],
        reference_videos=["c.mp4"], reference_audios=["d.wav"],
    ))
    assert [reference.kind for reference in ctx.extra.references] == ["image", "image", "video", "audio"]


def test_build_context_refuses_a_references_mode_request_with_no_references():
    pipe = GeneratorMinimaxH3Pipe({**GeneratorMinimaxH3Pipe.get_default_config(), "mode": "references"})
    with pytest.raises(ValueError, match="no reference image, video or audio"):
        pipe.build_context(_pipe_input(model=_fake_bundle(), conditioning=[_fake_conditioning(3)]))


def test_build_context_still_allows_an_empty_request_in_video_mode():
    """t2va is a valid `video`-mode request -- the empty-set refusal must be
    scoped to ref2va, not applied to every H3 generation."""
    pipe = GeneratorMinimaxH3Pipe(GeneratorMinimaxH3Pipe.get_default_config())
    ctx = pipe.build_context(_pipe_input(model=_fake_bundle(), conditioning=[_fake_conditioning(3)]))
    assert ctx.extra.references == ()


def test_mixed_references_walk_ONE_order_through_blocks_and_both_latents(tmp_path):
    """THE ordering invariant: the reference blocks, the visual-latent
    iterator and the audio-latent iterator are three views of one traversal.

    Each reference is given a geometry no other one shares, so the latents are
    individually identifiable: if the blocks were built from one traversal and
    the latents from another, the block at index i would describe a different
    reference than the latent at index i -- with no exception, no shape
    mismatch and a plausible video out the other end.
    """
    captured = _run_ref2va_generate_one(
        [_ref_image_media((4, 4)), _ref_video(num_frames=22, size=8), _ref_audio(num_latents=3)],
        tmp_path,
    )
    blocks, condition_latents, audio_condition_latents = captured["args"][1:4]

    assert [b.kind for b in blocks] == ["image", "video", "audio"]
    # Visual latents skip the audio reference and stay in packed order: the
    # 1-frame 4x4 image first, the 7-frame 8x8 video second.
    assert [tuple(latent.shape[2:5]) for latent in condition_latents] == [(1, 4, 4), (7, 8, 8)]
    # The audio iterator carries the one audio-bearing reference.
    assert [tuple(rows.shape) for rows in audio_condition_latents] == [(6, 32)]
    # And the row counts the layout derived agree with both.
    assert captured["layout"].num_condition_video_rows == 1 * 2 * 2 + 7 * 4 * 4
    assert captured["layout"].num_condition_audio_rows == 6


def test_reordering_the_reference_list_reorders_every_traversal(tmp_path):
    """BITE CHECK for the invariant above: the packed order is not a
    canonical per-modality sort. Feeding the same references video-first has
    to move the blocks AND the latents together."""
    captured = _run_ref2va_generate_one(
        [_ref_video(num_frames=22, size=8), _ref_image_media((4, 4))], tmp_path,
    )
    blocks, condition_latents, _ = captured["args"][1:4]
    assert [b.kind for b in blocks] == ["video", "image"]
    assert [tuple(latent.shape[2:5]) for latent in condition_latents] == [(7, 8, 8), (1, 4, 4)]


def test_the_image_only_reference_path_is_unchanged(tmp_path):
    """The image-only request now runs `prepare_reference_conditioning`
    instead of `prepare_reference_condition_rows`. The two must produce
    byte-identical condition rows off the same generator, or every existing
    ref2va seed silently changes what it renders."""
    from src.pipelines.pipes.generator.video_minimax_h3.conditioning import (
        normalize_references,
        prepare_reference_condition_rows,
    )
    from src.pipelines.pipes.generator.video_minimax_h3.geometry import CANVAS_MULTIPLE

    images = [_ref_image((64, 48)), _ref_image((48, 64))]
    kwargs = dict(patch_size=PATCH, device="cpu", dtype=torch.float32,
                  latents_mean=[0.0] * 24, latents_std=[1.0] * 24)

    legacy_latents, legacy_rows = prepare_reference_condition_rows(
        [normalize_reference_image(image, canvas_multiple=CANVAS_MULTIPLE) for image in images],
        vae_module=_RefVideoVae(), canvas_multiple=CANVAS_MULTIPLE,
        generator=torch.Generator().manual_seed(11), **kwargs,
    )
    current = prepare_reference_conditioning(
        normalize_references([ReferenceMedia(kind="image", image=image) for image in images], num_frames=22),
        vae_module=_RefVideoVae(), generator=torch.Generator().manual_seed(11), **kwargs,
    )

    assert torch.equal(current.condition_rows, legacy_rows)
    assert [tuple(l.shape) for l in current.condition_latents] == [tuple(l.shape) for l in legacy_latents]
    assert all(torch.equal(a, b) for a, b in zip(current.condition_latents, legacy_latents))
    assert current.condition_audio_rows is None
