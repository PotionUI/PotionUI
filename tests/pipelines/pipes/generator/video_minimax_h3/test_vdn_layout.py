"""The MiniMax-H3 generator's VDN wiring: the per-window ``VdnLayout`` derived
from that window's packed layout, the placement reserve it adds, its mutual
exclusion with the sparse-attention and sequence-chunking knobs, and the
untouched plain path.

The branch math itself belongs to
``tests/platform/runtime/native/arch/vdn/`` and is never reached here -- the DiT
is a fake that records what it was handed.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
import torch

from src.pipelines.pipes._shared.generation.progress import ProgressEmitter
from src.pipelines.pipes.generator.video_minimax_h3.layout import build_packed_sequence
from src.pipelines.pipes.generator.video_minimax_h3.main import (
    H3_HEAD_DIM,
    H3_HIDDEN_SIZE,
    H3_NUM_HEADS,
    GeneratorMinimaxH3Pipe,
    _MiniMaxH3Ctx,
    build_vdn_layout,
    vdn_reserve_gb,
)
from src.pipelines.pipes._shared.generation.generator_base import GeneratorContext
from src.platform.runtime.native.arch.minimax_h3.vdn import (
    estimate_vdn_transient_gb,
    window_bounds,
)

from tests.pipelines.pipes.generator.video_minimax_h3.test_main import (
    PATCH,
    _fake_audio_vae_module,
    _fake_conditioning,
    _fake_dit_module,
)

_TEXT_TOKENS = 5


def _packed(**over):
    kwargs = dict(
        num_latent_frames=4, latent_height=4, latent_width=6, num_audio_latents=3,
        patch_size=PATCH, keyframe_anchors=("first",), num_condition_audio_latents=1,
    )
    kwargs.update(over)
    return build_packed_sequence(torch.ones(_TEXT_TOKENS, dtype=torch.long), **kwargs), kwargs


# --- the layout -------------------------------------------------------------

def test_the_layout_starts_at_the_target_video_rows_not_the_condition_ones():
    layout, kwargs = _packed()
    built = build_vdn_layout(
        layout, num_latent_frames=kwargs["num_latent_frames"],
        latent_height=kwargs["latent_height"], latent_width=kwargs["latent_width"],
    )
    condition_rows = layout.video_indices[: layout.num_condition_video_rows]
    assert condition_rows.numel() > 0
    assert built.video_start == int(layout.video_indices[layout.num_condition_video_rows])
    assert built.video_start > int(condition_rows.max())
    # The target block is the sequence's tail, so the layout closes on it exactly.
    assert built.video_end == built.seq_len == int(layout.position_ids.shape[0])


def test_the_frame_grid_comes_from_the_patched_latent_geometry():
    layout, kwargs = _packed()
    built = build_vdn_layout(
        layout, num_latent_frames=kwargs["num_latent_frames"],
        latent_height=kwargs["latent_height"], latent_width=kwargs["latent_width"],
    )
    _, patch_h, patch_w = PATCH
    assert built.frame_height == kwargs["latent_height"] // patch_h
    assert built.frame_width == kwargs["latent_width"] // patch_w
    assert built.tokens_per_frame == built.frame_height * built.frame_width
    assert built.num_frames == kwargs["num_latent_frames"]


def test_the_text_range_is_the_prompt_rows_alone():
    layout, kwargs = _packed()
    built = build_vdn_layout(
        layout, num_latent_frames=kwargs["num_latent_frames"],
        latent_height=kwargs["latent_height"], latent_width=kwargs["latent_width"],
    )
    assert built.text_range == (0, _TEXT_TOKENS)
    # The condition-audio rows sit immediately after the prompt and must stay
    # outside it: they are dense globals, never a scan seed.
    assert int(layout.audio_indices[0]) == _TEXT_TOKENS


def test_the_window_bounds_and_anchor_mode_are_the_trained_ones():
    layout, kwargs = _packed()
    built = build_vdn_layout(
        layout, num_latent_frames=kwargs["num_latent_frames"],
        latent_height=kwargs["latent_height"], latent_width=kwargs["latent_width"],
    )
    assert built.window_bounds == tuple(window_bounds(kwargs["num_latent_frames"]))
    assert built.skip_ends is True


def test_a_geometry_that_does_not_factor_the_target_rows_is_refused():
    layout, kwargs = _packed()
    with pytest.raises(ValueError, match="do not factor"):
        build_vdn_layout(
            layout, num_latent_frames=kwargs["num_latent_frames"] + 1,
            latent_height=kwargs["latent_height"], latent_width=kwargs["latent_width"],
        )


# --- the reserve ------------------------------------------------------------

def test_no_layout_reserves_nothing():
    assert vdn_reserve_gb(None) == 0.0


def test_the_reserve_is_the_branch_estimate_for_this_layout():
    layout, kwargs = _packed(num_latent_frames=32, latent_height=32, latent_width=32)
    built = build_vdn_layout(
        layout, num_latent_frames=32, latent_height=32, latent_width=32,
    )
    assert vdn_reserve_gb(built) == estimate_vdn_transient_gb(
        built, H3_NUM_HEADS, H3_HEAD_DIM, H3_HIDDEN_SIZE, anchor_frames="both",
    )
    assert vdn_reserve_gb(built) > 0.0


# --- the sampling loop ------------------------------------------------------

class _AttachedDit:
    """A DiT fake that ``vdn_attached`` says carries a branch, recording the
    ``vdn_layout`` of every forward it is given."""

    def __init__(self, video_patch_dim: int, attached: bool = True):
        self._forward = _fake_dit_module(video_patch_dim)
        self.blocks = [SimpleNamespace(attn=SimpleNamespace(vdn_branch_attached=attached))]
        self.seen: list = []

    def __call__(self, **kwargs):
        self.seen.append(kwargs.get("vdn_layout", "ABSENT"))
        kwargs.pop("vdn_layout", None)
        return self._forward(**kwargs)

    def prepare_text_context(self, encoder_hidden_states, weight_revision=None):
        return self._forward.prepare_text_context(encoder_hidden_states, weight_revision)


def _run(config_overrides: dict, *, attached: bool = True, steps: int = 3):
    video_patch_dim = 24 * PATCH[0] * PATCH[1] * PATCH[2]
    dit_module = _AttachedDit(video_patch_dim, attached=attached)

    class _FakeVideoVae:
        latents_mean = torch.zeros(24)
        latents_std = torch.ones(24)

        def decode(self, z):
            b, c, f, h, w = z.shape
            return torch.rand(b, 3, f, h, w)

    bundle = SimpleNamespace(
        spec=SimpleNamespace(family="minimax_h3", variant="h3", sampling_settings={},
                             latent_format={"format": "minimax_h3", "latent_channels": 24}),
        dit=SimpleNamespace(compute_dtype=torch.float32, estimated_vram_gb=0.0, module=dit_module,
                            move_to=lambda d: None, offload=lambda: None),
        video_vae=SimpleNamespace(module=_FakeVideoVae(), compute_dtype=torch.float32,
                                  move_to=lambda d: None, offload=lambda: None),
        audio_vae=SimpleNamespace(module=_fake_audio_vae_module(), move_to=lambda d: None, offload=lambda: None),
        te=None, te_cache_key=None,
    )
    ctx = GeneratorContext(quantity=1, input_seeds=[7], extra=_MiniMaxH3Ctx(
        bundle=bundle, conditioning=[_fake_conditioning(3)], steps=steps,
        height=32, width=32, frames=22, num_latent_frames=2, latent_height=2, latent_width=2,
        num_audio_latents=2, device="cpu", dtype=torch.float32, spec=bundle.spec,
        keyframe_images=[], keyframe_anchors=(), audio_source="generate", decode=False,
    ))
    pipe = GeneratorMinimaxH3Pipe({
        **GeneratorMinimaxH3Pipe.get_default_config(), "preview": False, "steps": steps,
        **config_overrides,
    })
    pipe._audio_results = []
    with patch("src.pipelines.pipes.generator.video_minimax_h3.main.place_dit_for_sequence") as placement:
        pipe.generate_one(ctx, 0, 7, ProgressEmitter([].append, title="test"))
    return dit_module, [call.kwargs for call in placement.call_args_list]


def test_every_step_of_an_attached_dit_gets_the_same_layout():
    dit_module, _ = _run({}, steps=3)
    assert len(dit_module.seen) == 3
    assert all(seen is not None and seen != "ABSENT" for seen in dit_module.seen)
    assert len({id(seen) for seen in dit_module.seen}) == 1


def test_the_layout_the_forward_gets_describes_this_window():
    dit_module, _ = _run({}, steps=2)
    layout = dit_module.seen[0]
    # ctx geometry: 2 latent frames, 2x2 latents under patch (1,2,2) -> 1 token/frame.
    assert layout.num_frames == 2
    assert layout.tokens_per_frame == 1
    assert layout.skip_ends is True


def test_a_plain_dit_gets_no_layout_and_no_kwarg():
    dit_module, calls = _run({}, attached=False, steps=2)
    assert dit_module.seen == ["ABSENT", "ABSENT"]
    assert all(kwargs["reserve_gb"] == 0.0 for kwargs in calls)


def test_the_placement_reserves_the_vdn_transient():
    _, calls = _run({}, steps=1)
    assert calls
    assert all(kwargs["reserve_gb"] > 0.0 for kwargs in calls)


def test_sparse_attention_alongside_a_vdn_dit_is_refused():
    with pytest.raises(ValueError, match="Video-DeltaNet"):
        _run({"sparse_attn": "sol"})


def test_sequence_chunking_alongside_a_vdn_dit_is_refused():
    with pytest.raises(ValueError, match="seq_chunk_rows"):
        _run({"seq_chunk_rows": 128})


def test_the_same_knobs_still_work_on_a_plain_dit():
    """The refusal is the VDN DiT's, not a new global rule."""
    dit_module, _ = _run({"seq_chunk_rows": 128}, attached=False, steps=1)
    assert dit_module.seen == ["ABSENT"]
