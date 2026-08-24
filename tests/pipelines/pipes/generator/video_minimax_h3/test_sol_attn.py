"""Tests for the MiniMax-H3 generator's sparse-attention wiring: the ONE
method knob (`off`/`sol`/`sla`) resolving to a `SolAttnContext`,
`SlaAttnContext` or `None`, the sink/prefix derived from a window's packed
layout, the trailing-dense-step rule, and the reserve dispatch by context
type.

The seam under test is everything the generator owns; the attention backends
themselves are `tests/platform/runtime/native/test_sol_attn.py` and
`tests/platform/runtime/native/test_sla_attn.py`'s subjects and are never
reached here.
"""

from __future__ import annotations

import dataclasses

import pytest
import torch

from src.pipelines.pipes.generator.video_minimax_h3.layout import build_packed_sequence
from src.pipelines.pipes.generator.video_minimax_h3.main import (
    GeneratorMinimaxH3Pipe,
    build_sparse_attn_ctx,
    is_dense_step,
    sparse_attn_dense_last_steps,
    sparse_attn_reserve_gb,
    video_target_start,
)
from src.platform.runtime.native.sla_attn import SlaAttnContext
from src.platform.runtime.native.sol_attn import SolAttnContext

PATCH_SIZE = (1, 2, 2)

# [ text(5) | condition audio(2) | keyframe conditions(4) | target audio(6) |
#   target video(8) ] -- the target video starts at row 17 of 25.
_TEXT = 5
_EXPECTED_SINK = 17
_EXPECTED_SEQ_LEN = 25


def _layout(**over):
    kwargs = dict(
        num_latent_frames=2, latent_height=4, latent_width=4, num_audio_latents=3,
        patch_size=PATCH_SIZE, keyframe_anchors=("first",), num_condition_audio_latents=1,
    )
    kwargs.update(over)
    return build_packed_sequence(torch.ones(_TEXT, dtype=torch.long), **kwargs)


def _config(**over):
    config = dict(GeneratorMinimaxH3Pipe.get_default_config())
    config.update(over)
    return config


# --- the sink -----------------------------------------------------------

def test_the_sink_is_the_first_target_video_row():
    layout = _layout()
    assert int(layout.position_ids.shape[0]) == _EXPECTED_SEQ_LEN
    assert video_target_start(layout) == _EXPECTED_SINK


def test_the_sink_covers_every_non_target_row():
    """Everything the model is conditioned on -- text, condition audio,
    keyframe rows and the target audio -- must fall inside the exact prefix."""
    layout = _layout()
    sink = video_target_start(layout)
    conditioning = torch.cat([
        layout.text_indices,
        layout.audio_indices,
        layout.video_indices[: layout.num_condition_video_rows],
    ])
    assert int(conditioning.max()) < sink
    assert int(layout.video_indices[layout.num_condition_video_rows:].min()) == sink


def test_the_sink_tracks_a_layout_without_conditioning_rows():
    layout = _layout(keyframe_anchors=(), num_condition_audio_latents=0)
    # [ text(5) | target audio(6) | target video(8) ]
    assert video_target_start(layout) == _TEXT + 6


def test_a_non_contiguous_video_tail_is_refused():
    """A sink derived against a different row order would silently approximate
    the conditioning instead of failing, so the tail assumption is checked."""
    layout = _layout()
    tail = layout.video_indices[layout.num_condition_video_rows:]
    shuffled = torch.cat([
        layout.video_indices[: layout.num_condition_video_rows], tail.flip(0),
    ])
    broken = dataclasses.replace(layout, video_indices=shuffled)
    with pytest.raises(ValueError, match="contiguous tail"):
        video_target_start(broken)


# --- method dispatch ------------------------------------------------------

def test_sparse_attn_is_off_by_default():
    assert build_sparse_attn_ctx(_config(), _layout()) is None


def test_a_layout_the_sink_cannot_be_derived_from_does_not_fail_the_default_path():
    """Off must mean the layout is never inspected: a knob nobody touched
    cannot be what fails a generation."""
    layout = _layout()
    broken = dataclasses.replace(layout, video_indices=layout.video_indices.flip(0))
    with pytest.raises(ValueError):
        build_sparse_attn_ctx(_config(sparse_attn="sol"), broken)
    assert build_sparse_attn_ctx(_config(), broken) is None


def test_the_pipe_spec_defaults_are_off():
    defaults = {s.name: s.default for s in GeneratorMinimaxH3Pipe.configuration()}
    assert defaults["sparse_attn"] == "off"
    assert defaults["sol_attn_tau"] == 1.0
    assert defaults["sla_sparsity"] == 0.90
    assert defaults["sla_block_size"] == 64
    assert defaults["sparse_attn_dense_last_steps"] == 2
    assert GeneratorMinimaxH3Pipe.get_default_config()["sparse_attn"] == "off"


def test_sol_builds_a_sol_context_with_the_layout_sink():
    ctx = build_sparse_attn_ctx(_config(sparse_attn="sol", sol_attn_tau=1.3), _layout())
    assert isinstance(ctx, SolAttnContext)
    assert ctx.sink_tokens == _EXPECTED_SINK
    assert ctx.tau == 1.3
    assert ctx.dense is False


def test_sla_builds_an_sla_context_with_the_layout_prefix():
    ctx = build_sparse_attn_ctx(
        _config(sparse_attn="sla", sla_sparsity=0.85, sla_block_size=128), _layout(),
    )
    assert isinstance(ctx, SlaAttnContext)
    assert ctx.prefix_tokens == _EXPECTED_SINK
    assert ctx.sparsity == 0.85
    assert ctx.block_size == 128
    assert ctx.dense is False


def test_an_unknown_method_warns_once_and_is_treated_as_off(caplog):
    import logging

    with caplog.at_level(logging.WARNING, logger="src.pipelines.contracts"):
        assert build_sparse_attn_ctx(_config(sparse_attn="nonsense"), _layout()) is None
    assert any("nonsense" in r.message for r in caplog.records)


def test_a_longer_video_does_not_grow_the_sink():
    """The exact prefix is the conditioning, so it must not scale with the
    target -- that is what makes the feature pay off on long clips."""
    short = build_sparse_attn_ctx(_config(sparse_attn="sol"), _layout())
    long = build_sparse_attn_ctx(_config(sparse_attn="sol"), _layout(num_latent_frames=8))
    assert short.sink_tokens == long.sink_tokens == _EXPECTED_SINK


def test_a_longer_prefix_moves_the_sink():
    ctx = build_sparse_attn_ctx(_config(sparse_attn="sol"), _layout(num_audio_latents=5))
    assert ctx.sink_tokens == _EXPECTED_SINK + 4  # two extra latents, stereo


@pytest.mark.parametrize("value, expected", [(0, 0), (3, 3), ("2", 2), (None, 2), ("many", 2), (-4, 0)])
def test_dense_last_steps_coercion(value, expected):
    assert sparse_attn_dense_last_steps(_config(sparse_attn_dense_last_steps=value)) == expected


# --- the trailing-dense rule ------------------------------------------------

def test_only_the_trailing_steps_are_dense():
    dense = [is_dense_step(i, 10, 2) for i in range(10)]
    assert dense == [False] * 8 + [True] * 2


def test_zero_dense_last_steps_leaves_every_step_sparse():
    assert not any(is_dense_step(i, 6, 0) for i in range(6))


@pytest.mark.parametrize("dense_last", [6, 7, 99])
def test_a_dense_window_at_or_above_the_step_count_is_the_feature_off(dense_last):
    assert all(is_dense_step(i, 6, dense_last) for i in range(6))


# --- the placement reserve --------------------------------------------------

def test_no_reserve_when_the_feature_is_off():
    """Off must leave the DiT placement exactly as it was: 0.0 is the value
    `place_dit_for_sequence` already defaults to."""
    assert sparse_attn_reserve_gb(None, _layout()) == 0.0


def test_the_sol_reserve_is_positive_once_enabled():
    """Small layouts are below the routing threshold, so use a realistic one."""
    layout = _layout(num_latent_frames=64, latent_height=16, latent_width=16)
    ctx = build_sparse_attn_ctx(_config(sparse_attn="sol"), layout)
    assert sparse_attn_reserve_gb(ctx, layout) > 0.0


def test_the_sol_reserve_grows_with_the_packed_row_count():
    small = _layout(num_latent_frames=64, latent_height=16, latent_width=16)
    large = _layout(num_latent_frames=128, latent_height=16, latent_width=16)
    assert int(large.position_ids.shape[0]) > int(small.position_ids.shape[0])
    ctx = build_sparse_attn_ctx(_config(sparse_attn="sol"), small)
    assert sparse_attn_reserve_gb(ctx, large) > sparse_attn_reserve_gb(ctx, small)


def test_the_sol_reserve_is_sized_off_the_whole_packed_sequence():
    """Not off the video rows alone -- Sol-Attn copies the full packed q/k/v,
    conditioning prefix included."""
    from src.platform.runtime.native.sol_attn import estimate_transient_gb

    from src.pipelines.pipes.generator.video_minimax_h3.main import H3_HEAD_DIM, H3_NUM_HEADS

    layout = _layout(num_latent_frames=64, latent_height=16, latent_width=16)
    ctx = build_sparse_attn_ctx(_config(sparse_attn="sol"), layout)
    assert sparse_attn_reserve_gb(ctx, layout) == estimate_transient_gb(
        int(layout.position_ids.shape[0]), H3_NUM_HEADS, H3_HEAD_DIM,
    )


def test_the_sla_reserve_is_dispatched_to_the_sla_estimator():
    from src.platform.runtime.native.sla_attn import estimate_transient_gb as sla_estimate

    from src.pipelines.pipes.generator.video_minimax_h3.main import H3_HEAD_DIM, H3_NUM_HEADS

    layout = _layout(num_latent_frames=64, latent_height=16, latent_width=16)
    ctx = build_sparse_attn_ctx(_config(sparse_attn="sla"), layout)
    assert sparse_attn_reserve_gb(ctx, layout) == sla_estimate(
        int(layout.position_ids.shape[0]), H3_NUM_HEADS, H3_HEAD_DIM,
    )


def test_sla_reserves_far_less_than_sol_at_the_documented_scale():
    """The point of the feature: at the 768x1344/141-frame scale (43047 rows,
    56 heads, head_dim 128) SLA's transient reserve is a fraction of Sol-
    Attn's -- SLA skips blocks instead of materialising the full padded q/k/v
    routing tensors Sol-Attn's flex backend does."""
    from src.platform.runtime.native.sla_attn import estimate_transient_gb as sla_estimate
    from src.platform.runtime.native.sol_attn import estimate_transient_gb as sol_estimate

    seq_len, heads, head_dim = 43047, 56, 128
    sol_gb = sol_estimate(seq_len, heads, head_dim)
    sla_gb = sla_estimate(seq_len, heads, head_dim)
    assert sol_gb > 4.0
    assert sla_gb < 1.5
    assert sla_gb < sol_gb
