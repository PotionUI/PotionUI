"""The VDN hybrid attention module and its wiring through the H3 arch.

The composition itself is pinned against a reference built in the test from the two
branches' own public functions, so a rewiring that still runs cannot pass. Everything
else here is about the seams: no layout must be the dense model byte for byte, a full
cover must be the dense path with the branch silent, the layout must never reach the
token refiner, and the step cache must keep skipping with a layout present.
"""

from __future__ import annotations

import pytest
import torch

from src.platform.runtime.native.arch.minimax_h3.model import (
    MiniMaxH3Attention,
    _apply_rotary_emb,
)
from src.platform.runtime.native.arch.minimax_h3.vdn import (
    MiniMaxH3LinearBranch,
    MiniMaxH3VdnAttention,
    VdnLayout,
    attach_vdn_branch,
    window_bounds,
    windowed_softmax,
)
from src.platform.runtime.native.arch.minimax_h3.vdn.window import clear_window_plan_cache
from src.platform.runtime.native.sampling.step_cache import FirstBlockCache
from src.platform.runtime.native.sol_attn import SolAttnContext

from ..test_minimax_h3_model import (
    TINY_FULL,
    _build_ready,
    _fbcache_forward,
    _fbcache_inputs,
    _fp32_ops,
    _tiny_layout,
)

FRAMES = 12
TOKENS_PER_FRAME = 2
TEXT_ROWS = 3
AUDIO_ROWS = 2


@pytest.fixture(autouse=True)
def _drop_window_plans():
    clear_window_plan_cache()
    yield
    clear_window_plan_cache()


def _branch_state_dict(blocks: int, config: dict = TINY_FULL) -> dict[str, torch.Tensor]:
    """A full branch checkpoint for ``blocks`` blocks, keyed as the real one is."""
    proto = MiniMaxH3LinearBranch(
        config["hidden_size"], config["num_attention_heads"], config["attention_head_dim"],
        _fp32_ops(),
    )
    template = proto.state_dict()
    state: dict[str, torch.Tensor] = {}
    for index in range(blocks):
        for tail, value in template.items():
            state[f"transformer_blocks.{index}.attn.{tail}"] = (
                torch.ones_like(value) if tail.endswith("norm.weight")
                else torch.randn_like(value) * 0.02
            )
    return state


def _vdn_layout(num_frames: int = FRAMES) -> VdnLayout:
    video_rows = num_frames * TOKENS_PER_FRAME
    return VdnLayout(
        seq_len=TEXT_ROWS + video_rows + AUDIO_ROWS,
        video_start=TEXT_ROWS,
        num_frames=num_frames,
        tokens_per_frame=TOKENS_PER_FRAME,
        frame_height=1,
        frame_width=TOKENS_PER_FRAME,
        window_bounds=tuple(window_bounds(num_frames)),
        text_start=0,
        text_len=TEXT_ROWS,
    ).with_anchor_mode("both")


def _packed_layout(num_frames: int = FRAMES) -> dict[str, torch.Tensor]:
    return _tiny_layout(TEXT_ROWS, num_frames * TOKENS_PER_FRAME, AUDIO_ROWS)


def _attached(seed: int = 7, config: dict = TINY_FULL):
    torch.manual_seed(seed)
    model = _build_ready(config)
    torch.manual_seed(seed + 1)
    report = attach_vdn_branch(model, _branch_state_dict(config["num_layers"], config))
    return model, report


def _rotary(rows: int) -> tuple[torch.Tensor, torch.Tensor]:
    freqs = torch.linspace(0.0, 3.0, rows * 6).view(rows, 6)
    return torch.cos(freqs), torch.sin(freqs)


def _spy(module) -> list:
    """Record every call to ``module.forward`` as its (args, kwargs) pair."""
    seen: list = []
    original = module.forward

    def recording(*args, **kwargs):
        seen.append((args, kwargs))
        return original(*args, **kwargs)

    module.forward = recording
    return seen


# --- no layout: the dense model, byte for byte -------------------------------

def test_without_a_layout_the_attached_model_is_the_dense_model():
    torch.manual_seed(7)
    dense = _build_ready(TINY_FULL)
    hybrid, _ = _attached()
    layout = _packed_layout()
    torch.manual_seed(21)
    inputs = _fbcache_inputs(TINY_FULL, layout)
    ts = torch.tensor([0.2, 0.9])

    reference = _fbcache_forward(dense, layout, inputs, ts)
    attached = _fbcache_forward(hybrid, layout, inputs, ts)

    for a, b in zip(reference, attached):
        assert torch.equal(a, b)


def test_attach_leaves_the_base_parameters_as_the_same_tensor_objects():
    torch.manual_seed(7)
    model = _build_ready(TINY_FULL)
    before = dict(model.named_parameters())
    torch.manual_seed(8)
    attach_vdn_branch(model, _branch_state_dict(TINY_FULL["num_layers"]))
    after = dict(model.named_parameters())

    assert set(before) <= set(after)
    for name, param in before.items():
        assert after[name] is param, name
    added = sorted(set(after) - set(before))
    assert added
    assert all(".attn.linear." in name for name in added), added
    assert "blocks.0.attn.qkv_proj.weight" in after


# --- with a layout: the composition ------------------------------------------

def test_hybrid_output_equals_the_two_branches_composed_by_hand():
    hybrid, _ = _attached()
    attn = hybrid.blocks[0].attn
    layout = _vdn_layout()
    torch.manual_seed(31)
    x = torch.randn(1, layout.seq_len, TINY_FULL["hidden_size"])
    rotary = _rotary(layout.seq_len)

    with torch.no_grad():
        produced = attn(x, rotary, vdn_layout=layout)

        heads, head_dim = attn.heads, attn.head_dim
        q_raw, k_raw, v_raw = attn.qkv_proj(x).chunk(3, dim=-1)
        q_raw = q_raw.view(1, layout.seq_len, heads, head_dim)
        k_raw = k_raw.view(1, layout.seq_len, heads, head_dim)
        v_raw = v_raw.view(1, layout.seq_len, heads, head_dim)
        query = _apply_rotary_emb(attn.q_norm(q_raw), *rotary).squeeze(0)
        key = _apply_rotary_emb(attn.k_norm(k_raw), *rotary).squeeze(0)
        q_raw, k_raw, v_raw = q_raw.squeeze(0), k_raw.squeeze(0), v_raw.squeeze(0)

        window = windowed_softmax(query, key, v_raw, layout, heads=heads, anchor_frames="both")
        assert window is not None
        rows = x[0]
        expected = attn.out_proj(
            (attn.linear.softmax_gate(rows) * window).reshape(1, layout.seq_len, -1)
        )
        expected[0, layout.video_start:layout.video_end] += attn.linear(
            rows, q_raw, k_raw, v_raw, layout
        )

    assert torch.equal(produced, expected)


def test_a_layout_changes_the_output():
    hybrid, _ = _attached()
    attn = hybrid.blocks[0].attn
    layout = _vdn_layout()
    torch.manual_seed(32)
    x = torch.randn(1, layout.seq_len, TINY_FULL["hidden_size"])
    rotary = _rotary(layout.seq_len)

    with torch.no_grad():
        dense = attn(x, rotary)
        hybrid_out = attn(x, rotary, vdn_layout=layout)

    assert dense.shape == hybrid_out.shape
    assert not torch.equal(dense, hybrid_out)


def test_full_cover_runs_the_dense_path_and_never_the_branch():
    hybrid, _ = _attached()
    attn = hybrid.blocks[0].attn
    layout = _vdn_layout(num_frames=3)
    torch.manual_seed(33)
    x = torch.randn(1, layout.seq_len, TINY_FULL["hidden_size"])
    rotary = _rotary(layout.seq_len)

    with torch.no_grad():
        dense = attn(x, rotary)
        branch_calls = _spy(attn.linear)
        covered = attn(x, rotary, vdn_layout=layout)

    assert branch_calls == []
    assert torch.equal(dense, covered)


def test_a_layout_with_chunking_or_sparse_attention_is_refused():
    hybrid, _ = _attached()
    attn = hybrid.blocks[0].attn
    layout = _vdn_layout()
    x = torch.randn(1, layout.seq_len, TINY_FULL["hidden_size"])
    rotary = _rotary(layout.seq_len)

    with pytest.raises(ValueError, match="mutually exclusive"):
        attn(x, rotary, seq_chunk_rows=4, vdn_layout=layout)
    with pytest.raises(ValueError, match="mutually exclusive"):
        attn(x, rotary, SolAttnContext(), 0, layout)


def test_a_layout_that_disagrees_with_the_sequence_is_refused():
    hybrid, _ = _attached()
    attn = hybrid.blocks[0].attn
    layout = _vdn_layout()
    rotary = _rotary(layout.seq_len)

    with pytest.raises(ValueError, match="rows but the layout describes"):
        attn(torch.randn(1, layout.seq_len - 1, TINY_FULL["hidden_size"]),
             _rotary(layout.seq_len - 1), vdn_layout=layout)
    with pytest.raises(ValueError, match="batch must be 1"):
        attn(torch.randn(2, layout.seq_len, TINY_FULL["hidden_size"]), rotary, vdn_layout=layout)


# --- model wiring -------------------------------------------------------------

def test_model_forward_threads_the_layout_to_every_main_block():
    hybrid, _ = _attached()
    packed = _packed_layout()
    vdn = _vdn_layout()
    torch.manual_seed(34)
    inputs = _fbcache_inputs(TINY_FULL, packed)
    seen = [_spy(block.attn) for block in hybrid.blocks]

    _fbcache_forward(hybrid, packed, inputs, torch.tensor([0.2, 0.9]), vdn_layout=vdn)

    for calls in seen:
        assert [args[-1] for args, _ in calls] == [vdn]


def test_the_token_refiner_never_receives_the_layout():
    hybrid, _ = _attached()
    packed = _packed_layout()
    torch.manual_seed(35)
    inputs = _fbcache_inputs(TINY_FULL, packed)
    refiner_attn = hybrid.token_refiner.blocks[0].attn
    seen = _spy(refiner_attn)

    _fbcache_forward(hybrid, packed, inputs, torch.tensor([0.2, 0.9]), vdn_layout=_vdn_layout())

    assert seen
    assert type(refiner_attn) is MiniMaxH3Attention
    for args, kwargs in seen:
        assert len(args) <= 2, args
        assert "vdn_layout" not in kwargs


def test_model_refuses_a_layout_without_an_attached_branch():
    dense = _build_ready(TINY_FULL)
    packed = _packed_layout()
    inputs = _fbcache_inputs(TINY_FULL, packed)

    with pytest.raises(ValueError, match="carries no VDN branch"):
        _fbcache_forward(dense, packed, inputs, torch.tensor([0.2, 0.9]), vdn_layout=_vdn_layout())


def test_model_refuses_a_layout_alongside_chunking_or_sparse_attention():
    hybrid, _ = _attached()
    packed = _packed_layout()
    inputs = _fbcache_inputs(TINY_FULL, packed)
    ts = torch.tensor([0.2, 0.9])
    vdn = _vdn_layout()

    with pytest.raises(ValueError, match="mutually exclusive"):
        _fbcache_forward(hybrid, packed, inputs, ts, vdn_layout=vdn, seq_chunk_rows=4)
    with pytest.raises(ValueError, match="mutually exclusive"):
        _fbcache_forward(hybrid, packed, inputs, ts, vdn_layout=vdn,
                         sparse_attn_ctx=SolAttnContext())


def test_fbcache_identical_inputs_skip_still_replays_both_streams_with_a_layout():
    hybrid, _ = _attached()
    packed = _packed_layout()
    vdn = _vdn_layout()
    torch.manual_seed(36)
    inputs = _fbcache_inputs(TINY_FULL, packed)
    ts = torch.tensor([0.2, 0.9])
    cache = FirstBlockCache(rel_threshold=0.5, warmup_steps=0)

    first = _fbcache_forward(hybrid, packed, inputs, ts, step_cache=cache, vdn_layout=vdn)
    assert cache.stats() == {"computed": 1, "skipped": 0}
    second = _fbcache_forward(hybrid, packed, inputs, ts, step_cache=cache, vdn_layout=vdn)
    assert cache.stats() == {"computed": 1, "skipped": 1}

    for a, b in zip(first, second):
        assert torch.equal(a, b)


def test_bf16_model_forward_with_a_layout_is_finite():
    from src.platform.runtime.native.arch.minimax_h3.config import MiniMaxH3Config
    from src.platform.runtime.native.arch.minimax_h3.model import MiniMaxH3Model
    from src.platform.runtime.native.base import load_into_module
    from src.platform.runtime.native.detect.registry import match_model_spec
    from vendor.gpl.comfyui.ops import pick_operations

    torch.manual_seed(37)
    model = MiniMaxH3Model(
        MiniMaxH3Config.from_detect_config(TINY_FULL),
        pick_operations(torch.bfloat16, torch.bfloat16),
        dtype=torch.bfloat16,
    )
    state = {}
    for key, value in model.state_dict().items():
        if not value.is_floating_point():
            state[key] = value.clone()
        elif ".norm" in key:
            state[key] = torch.ones_like(value)
        else:
            state[key] = torch.randn_like(value) * 0.02
    load_into_module(model, state, match_model_spec(TINY_FULL))
    model.eval()
    branch = {k: v.to(torch.bfloat16) for k, v in _branch_state_dict(TINY_FULL["num_layers"]).items()}
    attach_vdn_branch(model, branch)

    packed = _packed_layout()
    inputs = {k: v.to(torch.bfloat16) for k, v in _fbcache_inputs(TINY_FULL, packed).items()}
    video, audio = _fbcache_forward(model, packed, inputs, torch.tensor([0.2, 0.9]),
                                    vdn_layout=_vdn_layout())

    assert video.shape == (1, packed["video_indices"].numel(), TINY_FULL["in_channels"] * 4)
    assert audio.shape == (1, packed["audio_indices"].numel(), TINY_FULL["audio_in_channels"])
    assert torch.isfinite(video).all()
    assert torch.isfinite(audio).all()


def test_the_wrapper_is_still_a_minimax_h3_attention():
    hybrid, _ = _attached()
    attn = hybrid.blocks[0].attn
    assert isinstance(attn, MiniMaxH3VdnAttention)
    assert isinstance(attn, MiniMaxH3Attention)
    assert not hasattr(attn, "orig")


def test_an_anchor_mode_that_disagrees_with_the_layout_is_refused():
    hybrid, _ = _attached()
    attn = hybrid.blocks[0].attn
    unpaired = _vdn_layout().with_anchor_mode("none")
    x = torch.randn(1, unpaired.seq_len, TINY_FULL["hidden_size"])

    with pytest.raises(ValueError, match="does not pair with"):
        attn(x, _rotary(unpaired.seq_len), vdn_layout=unpaired)
