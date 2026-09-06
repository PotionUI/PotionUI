"""Tests for the MiniMax-H3 VDN linear branch.

Coverage: state-dict key parity against the checkpoint's own 16 tensor names, golden
parity against OpenVDN's reference implementation (fixture generated on CPU at a tiny
shape with correlated inputs — see ``fixtures/linear_branch_golden.json`` for the
provenance and ``fixtures/generate_linear_branch_golden.py`` for the command), the
numerical traps that fail silently rather than loudly, and the memory model.

The reference implementation is never imported here; only the tensors it produced.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import torch

from src.platform.runtime.native.arch.minimax_h3.vdn import (
    MiniMaxH3LinearBranch,
    VdnLayout,
    estimate_linear_branch_transient_gb,
    frame_statistics,
    gather_linear_state,
    linear_branch_transient_breakdown,
    vdn_solve,
)
from vendor.gpl.comfyui.ops import pick_operations

_FIXTURES = Path(__file__).parent / "fixtures"

# The checkpoint's own tensor names and shapes under `transformer_blocks.N.attn.`,
# transcribed from the safetensors header (50 blocks x these 16, hidden 5376, H 56,
# d 128). A slice of the checkpoint with that prefix removed must load into this module
# with nothing missing and nothing left over.
CHECKPOINT_TENSORS = {
    "linear_attention.alpha.A_log": (56,),
    "linear_attention.alpha.down.weight": (128, 5376),
    "linear_attention.alpha.up.weight": (7168, 128),
    "linear_attention.alpha.dt_bias": (7168,),
    "linear_attention.beta_proj.weight": (56, 5376),
    "linear_attention.norm.weight": (128,),
    "linear_attention.output_gate.down.weight": (128, 5376),
    "linear_attention.output_gate.up.weight": (7168, 128),
    "linear_attention.output_gate.up.bias": (7168,),
    "linear_attention.short_conv.k_sp.weight": (7168, 1, 5, 5),
    "linear_attention.short_conv.k_tm.weight": (7168, 1, 5),
    "linear_attention.short_conv.v_sp.weight": (7168, 1, 5, 5),
    "linear_attention.short_conv.v_tm.weight": (7168, 1, 5),
    "softmax_gate.up.weight": (56, 5376),
    "softmax_gate.up.bias": (56,),
    "to_out_linear.weight": (5376, 7168),
}


@pytest.fixture(scope="module")
def golden():
    return torch.load(_FIXTURES / "linear_branch_golden.pt", weights_only=False)


@pytest.fixture(scope="module")
def golden_config():
    with open(_FIXTURES / "linear_branch_golden.json") as handle:
        return json.load(handle)["config"]


def _ops():
    return pick_operations(torch.float32, torch.float32)


def _layout(config, *, skip_ends: bool, with_text: bool, bounds=None) -> VdnLayout:
    return VdnLayout(
        seq_len=config["seq_len"],
        video_start=config["video_start"],
        num_frames=config["num_frames"],
        tokens_per_frame=config["tokens_per_frame"],
        frame_height=config["frame_height"],
        frame_width=config["frame_width"],
        window_bounds=bounds if bounds is not None else _bounds(config),
        text_start=0,
        text_len=config["text_len"] if with_text else 0,
        skip_ends=skip_ends,
    )


def _bounds(config):
    chunk, radius = config["chunk"], config["radius"]
    return tuple(
        (((t // chunk) - radius) * chunk, ((t // chunk) + radius + 1) * chunk - 1)
        for t in range(config["num_frames"])
    )


def _loaded_branch(golden, config) -> MiniMaxH3LinearBranch:
    branch = MiniMaxH3LinearBranch(
        config["hidden"], config["heads"], config["head_dim"], _ops()
    )
    branch.load_state_dict(golden["state_dict"], assign=True)
    branch.eval()
    return branch


def _run(branch, golden, layout):
    with torch.no_grad():
        return branch(golden["x"], golden["q_raw"], golden["k_raw"], golden["v_raw"], layout)


def test_state_dict_is_the_checkpoint_tail_set():
    """Built on meta, at the released hidden/head geometry."""
    branch = MiniMaxH3LinearBranch(5376, 56, 128, _ops(), device="meta")
    built = {name: tuple(tensor.shape) for name, tensor in branch.state_dict().items()}
    assert built == CHECKPOINT_TENSORS
    assert all(tensor.device.type == "meta" for tensor in branch.state_dict().values())


def test_checkpoint_slice_loads_with_no_missing_or_unexpected_keys(golden, golden_config):
    branch = MiniMaxH3LinearBranch(
        golden_config["hidden"], golden_config["heads"], golden_config["head_dim"], _ops()
    )
    result = branch.load_state_dict(golden["state_dict"], strict=False, assign=True)
    assert list(result.missing_keys) == []
    assert list(result.unexpected_keys) == []


@pytest.mark.parametrize(
    ("case", "skip_ends", "with_text"),
    [("anchors", True, True), ("no_anchors", False, True), ("no_text", True, False)],
)
def test_matches_the_reference_implementation(golden, golden_config, case, skip_ends, with_text):
    branch = _loaded_branch(golden, golden_config)
    out = _run(branch, golden, _layout(golden_config, skip_ends=skip_ends, with_text=with_text))
    assert out.shape == golden[f"projected_{case}"].shape
    assert torch.allclose(out, golden[f"projected_{case}"], atol=1e-5, rtol=1e-5)


def test_softmax_gate_matches_the_reference(golden, golden_config):
    branch = _loaded_branch(golden, golden_config)
    with torch.no_grad():
        gate = branch.softmax_gate(golden["x"])
    assert torch.allclose(gate, golden["softmax_gate"], atol=1e-5, rtol=1e-5)


def test_anchor_frames_are_zero_and_the_bounds_rebase(golden, golden_config):
    """The anchor rows are exactly zero, and the interior is computed on REBASED bounds.

    A port that implements the anchor mask but keeps the original bounds gets a silently
    wrong far state, so the second half feeds the same module the un-rebased bounds and
    asserts the interior moves.
    """
    branch = _loaded_branch(golden, golden_config)
    tokens = golden_config["tokens_per_frame"]
    layout = _layout(golden_config, skip_ends=True, with_text=True)
    out = _run(branch, golden, layout)

    assert torch.count_nonzero(out[:tokens]) == 0
    assert torch.count_nonzero(out[-tokens:]) == 0
    assert torch.count_nonzero(out[tokens:-tokens]) > 0

    bounds = _bounds(golden_config)
    assert layout.branch_bounds == tuple((lo - 1, hi - 1) for lo, hi in bounds[1:-1])

    # Shift the interior bounds up by one so `branch_bounds` hands the branch exactly
    # what a forgotten rebase would have handed it.
    un_rebased = (bounds[0], *((lo + 1, hi + 1) for lo, hi in bounds[1:-1]), bounds[-1])
    wrong = _run(branch, golden, _layout(golden_config, skip_ends=True, with_text=True,
                                         bounds=un_rebased))
    assert not torch.allclose(wrong[tokens:-tokens], out[tokens:-tokens], atol=1e-4)


def test_after_side_bridge_spans_the_frame_the_gather_clamps_away():
    """The bridge index is not the gather index at the sequence ends.

    A boundary row gathers a state it discards, but must decay the initial state over
    the frames it really skipped — from the reverse scan's virtual index F, which is the
    prefix bank's F+1'th row. Clamping the bridge like the gather index (to F-1) decays
    over one frame too few, so the last frame's own decay stops mattering.
    """
    frames, heads, dim = 4, 2, 3
    torch.manual_seed(0)
    prefix = torch.randn(frames, heads, dim, dim)
    suffix = torch.randn(frames, heads, dim, dim)
    alpha = torch.rand(frames, heads, dim) * 0.5 + 0.25
    text_state = torch.randn(heads, dim, dim)
    bounds = ((0, 3), (0, 3), (0, 3), (0, 3))       # every row's window touches both ends
    # The row one short of the end: its after side reaches past F-1, and its before side
    # spans frames 0..t, so only the after side can carry the last frame's decay.
    row = frames - 2

    nudged = alpha.clone()
    nudged[frames - 1] *= 0.5
    out = gather_linear_state(prefix, suffix, alpha, bounds, text_state)
    moved = gather_linear_state(prefix, suffix, nudged, bounds, text_state)
    assert not torch.allclose(out[row], moved[row], atol=1e-6)

    def after_span(decay, bridge_index):
        log_prefix = torch.cat([
            torch.zeros_like(decay[:1]), torch.log(decay.clamp_min(1e-12)).cumsum(0)
        ])
        return torch.exp(log_prefix[bridge_index] - log_prefix[row])

    assert not torch.allclose(after_span(alpha, frames), after_span(nudged, frames))
    # Clamped like the gather index instead: the last frame drops out of the span, and
    # its decay stops reaching the row that skipped it.
    assert torch.allclose(after_span(alpha, frames - 1), after_span(nudged, frames - 1))


def test_q_and_k_are_l2_normalised_and_v_is_not(golden_config):
    """Scaling one frame's q or k leaves the readout alone; scaling its v does not.

    The conv weights are set to a delta stencil and the inputs kept in SiLU's linear
    tail, so a per-row scale survives the feature chain untouched — which the L2 norm
    then divides out for q and k, and leaves standing for v.
    """
    heads, head_dim, hidden = 2, 8, 16
    frames, grid_h, grid_w = 5, 2, 3
    tokens = grid_h * grid_w
    rows = frames * tokens
    torch.manual_seed(3)

    branch = MiniMaxH3LinearBranch(hidden, heads, head_dim, _ops())
    state = {k: torch.randn_like(v) * 0.05 for k, v in branch.state_dict().items()}
    for proj in ("k", "v"):
        for axis in ("sp", "tm"):
            weight = torch.zeros_like(state[f"linear_attention.short_conv.{proj}_{axis}.weight"])
            centre = (2, 2) if axis == "sp" else (2,)
            weight[(slice(None), 0, *centre)] = 1.0
            state[f"linear_attention.short_conv.{proj}_{axis}.weight"] = weight
    state["linear_attention.norm.weight"] = torch.ones(head_dim)
    branch.load_state_dict(state, assign=True)
    branch.eval()

    x = torch.randn(rows, hidden) * 0.1
    qkv = [torch.rand(rows, heads, head_dim) * 5.0 + 20.0 for _ in range(3)]
    layout = VdnLayout(
        seq_len=rows, video_start=0, num_frames=frames, tokens_per_frame=tokens,
        frame_height=grid_h, frame_width=grid_w,
        window_bounds=tuple((t - 1, t + 1) for t in range(frames)),
    )
    with torch.no_grad():
        base = branch(x, *qkv, layout)
        moved = {}
        for index, name in enumerate(("q", "k", "v")):
            scaled = [t.clone() for t in qkv]
            scaled[index][tokens : 2 * tokens] *= 3.0
            moved[name] = branch(x, *scaled, layout)

    assert torch.allclose(moved["q"], base, atol=1e-4)
    assert torch.allclose(moved["k"], base, atol=1e-4)
    assert not torch.allclose(moved["v"], base, atol=1e-3)


def test_text_state_is_half_the_single_chunk_update(golden, golden_config):
    """Both scans start from HALF the prompt's chunk update, not the whole of it."""
    branch = _loaded_branch(golden, golden_config)
    attention = branch.linear_attention
    text = slice(0, golden_config["text_len"])
    text_x = golden["x"][text]
    text_qkv = (golden["q_raw"][text], golden["k_raw"][text], golden["v_raw"][text])

    with torch.no_grad():
        state = attention._text_state(text_x, text_qkv)

        length = text_x.shape[0]
        heads, head_dim = golden_config["heads"], golden_config["head_dim"]
        key = attention._features(text_qkv[1], "k", 0, None)
        value = attention._features(text_qkv[2], "v", 0, None)
        key = key.view(1, length, heads, head_dim).permute(0, 2, 1, 3)
        value = value.view(1, length, heads, head_dim).permute(0, 2, 1, 3)
        beta = torch.sigmoid(attention.beta_proj(text_x))
        beta = beta.view(1, length, heads).permute(0, 2, 1)
        a_stat, b_stat = frame_statistics(key, value, beta)
        ones = torch.ones(1, heads, head_dim, dtype=a_stat.dtype)
        _, injection = vdn_solve(ones, a_stat, b_stat)

    assert torch.allclose(state, 0.5 * injection[0], atol=1e-6)
    assert not torch.allclose(state, injection[0], atol=1e-3)


def test_frame_statistics_stay_cholesky_factorisable_on_correlated_keys():
    """The bf16 spelling of A breaks the property the delta rule's Cholesky needs.

    A is symmetric by construction, but computed as (k*beta)^T k in bf16 the (k,l) and
    (l,k) entries multiply differently-rounded operands. On correlated keys — a low-rank
    basis, as real patches within a frame are — that asymmetry pushes the smallest
    eigenvalue of I + A below the 1 the maths guarantees, and cholesky reads one
    triangle only. Random keys do not reproduce this.
    """
    tokens, dim = 4096, 128
    generator = torch.Generator().manual_seed(11)
    basis = torch.randn(1, dim, generator=generator)
    mix = torch.rand(tokens, 1, generator=generator) + 0.5
    key = torch.nn.functional.normalize(
        torch.nn.functional.silu(mix @ basis + 1e-3 * torch.randn(tokens, dim, generator=generator)),
        dim=-1,
    ).to(torch.bfloat16)
    value = torch.randn(tokens, dim, generator=generator).to(torch.bfloat16)
    beta = torch.full((1, 1, tokens), 0.95)
    eye = torch.eye(dim)

    a_stat, b_stat = frame_statistics(key.view(1, 1, tokens, dim), value.view(1, 1, tokens, dim), beta)
    assert a_stat.dtype is torch.float32
    assert torch.equal(a_stat, a_stat.transpose(-1, -2))
    torch.linalg.cholesky(a_stat[0, 0] + eye)                      # must not raise
    vdn_solve(torch.ones(1, 1, dim), a_stat, b_stat)

    unsymmetrised = ((key * beta[0, 0, :, None].to(key.dtype)).transpose(0, 1) @ key).float()
    assert not torch.equal(unsymmetrised, unsymmetrised.transpose(0, 1))
    with pytest.raises(RuntimeError):
        torch.linalg.cholesky(unsymmetrised + eye)


def test_the_frame_mean_reaches_the_decay_gate_in_fp32(golden_config):
    """The mean is taken WITH dtype=fp32, not rounded to bf16 and promoted afterwards."""
    heads, head_dim, hidden = 2, 8, 16
    frames, grid_h, grid_w = 4, 2, 3
    tokens = grid_h * grid_w
    rows = frames * tokens
    torch.manual_seed(5)

    branch = MiniMaxH3LinearBranch(hidden, heads, head_dim, _ops())
    branch.load_state_dict(
        {k: (torch.randn_like(v) * 0.05).to(torch.bfloat16) for k, v in branch.state_dict().items()},
        assign=True,
    )
    branch.eval()

    # A per-frame mean that bf16's 8 mantissa bits cannot hold, so rounding it back to
    # the input dtype and promoting afterwards is a different number.
    x = torch.rand(rows, hidden).to(torch.bfloat16)
    qkv = [torch.randn(rows, heads, head_dim).to(torch.bfloat16) for _ in range(3)]
    layout = VdnLayout(
        seq_len=rows, video_start=0, num_frames=frames, tokens_per_frame=tokens,
        frame_height=grid_h, frame_width=grid_w,
        window_bounds=tuple((t - 1, t + 1) for t in range(frames)),
    )

    seen = []
    original = type(branch.linear_attention.alpha).forward

    def spy(module, frame_mean):
        seen.append(frame_mean)
        return original(module, frame_mean)

    branch.linear_attention.alpha.forward = spy.__get__(branch.linear_attention.alpha)
    with torch.no_grad():
        branch(x, *qkv, layout)

    assert len(seen) == 1
    assert seen[0].dtype is torch.float32
    grouped = x.view(frames, tokens, hidden)
    assert torch.equal(seen[0], grouped.mean(dim=1, dtype=torch.float32))
    assert not torch.equal(seen[0], grouped.mean(dim=1).float())


def test_transient_estimate_matches_the_released_geometry():
    """F=102, H=56, d=128 at 768p: the scan banks are the ~1.5-1.9 GB the branch's own
    memory note quotes; the bf16 epilogue is the larger peak and the function returns it."""
    breakdown = linear_branch_transient_breakdown(102, 56, 128, 1008, 5376)
    assert 1.5 <= breakdown.scan_gb <= 1.9
    assert breakdown.readout_gb > breakdown.scan_gb
    assert estimate_linear_branch_transient_gb(102, 56, 128, 1008, 5376) == breakdown.peak_gb


def test_transient_scan_cost_is_resolution_independent():
    """F * H * d^2: halving the clip halves it, halving the canvas does nothing."""
    full = linear_branch_transient_breakdown(102, 56, 128, 1008, 5376).scan_gb
    assert linear_branch_transient_breakdown(102, 56, 128, 504, 5376).scan_gb == full
    assert linear_branch_transient_breakdown(51, 56, 128, 1008, 5376).scan_gb == pytest.approx(full / 2)


def test_skip_ends_is_derived_from_the_softmax_anchor_mode(golden, golden_config):
    """Only "both" may drop the anchor frames — the two settings are one decision."""
    layout = _layout(golden_config, skip_ends=False, with_text=True)
    assert layout.with_anchor_mode("both").skip_ends is True
    for mode in ("none", "columns", "rows"):
        assert layout.with_anchor_mode(mode).skip_ends is False

    branch = _loaded_branch(golden, golden_config)
    anchored = _run(branch, golden, layout.with_anchor_mode("both"))
    unanchored = _run(branch, golden, layout.with_anchor_mode("rows"))
    tokens = golden_config["tokens_per_frame"]
    assert torch.count_nonzero(anchored[:tokens]) == 0
    assert torch.count_nonzero(unanchored[:tokens]) > 0


def test_layout_rejects_a_grid_that_does_not_factor_the_frame():
    with pytest.raises(ValueError, match="tokens/frame"):
        VdnLayout(seq_len=24, video_start=0, num_frames=2, tokens_per_frame=12,
                  frame_height=3, frame_width=3, window_bounds=((0, 1), (0, 1)))


def test_layout_rejects_bounds_that_do_not_cover_every_frame():
    with pytest.raises(ValueError, match="window bounds"):
        VdnLayout(seq_len=24, video_start=0, num_frames=3, tokens_per_frame=4,
                  frame_height=2, frame_width=2, window_bounds=((0, 1), (0, 1)))
