# Derived from: OpenVDN/vdn-minimax-h3 (Apache-2.0, tree b8cb28f)
# `src/models/linear_attention/{branch,delta_rule,features,layers,scan}.py` and
# `src/models/attention_gates.py` — the gated-delta-rule recurrence, its feature chain,
# the two scans and the boundary gather, re-expressed against this engine's `operations`
# construction seam. Parameter names are the checkpoint's own tails, so a state-dict
# slice `{k.removeprefix("transformer_blocks.N.attn."): v}` loads 1:1. The fast paths
# (Triton temporal conv, compiled epilogue, preallocated-bank Ulysses body) are not
# ported; only the semantics they are equal to.

"""MiniMax-H3 VDN linear branch — everything the softmax window cannot see.

The VDN hybrid restricts the block's original attention to a local frame window and
adds this second branch alongside it: a bidirectional gated-delta-rule recurrence over
frames that summarises exactly the frames the window excludes. The two sum, and the
block's q/k/v projection is shared — the branch consumes the RAW pre-QK-norm, pre-RoPE
q/k/v and adds no projection cost, only its own post-processing, recurrence and output
projection.

Per query frame ``t`` with inclusive window ``[lo, hi]``::

    left  = prefix[lo - 1]      # frames 0..lo-1, decayed to t
    right = suffix[hi + 1]      # frames hi+1..F-1, decayed to t
    state = left + right        # the complement of the window, in t's frame of reference

Both scans start from the same initial state: half the prompt's single-chunk delta-rule
update when the layout carries text rows, zero otherwise.

Numerics are load-bearing here, not incidental. Four ``autocast(enabled=False)`` islands
(the frame statistics, the decay gate, the text chunk, the scans) exist because autocast
intercepts matmul at the op level, so an explicit ``.float()`` alone is not fp32 math;
``A`` is computed in fp32 and explicitly symmetrised because the Cholesky behind the
delta rule reads one triangle and a bf16 asymmetry pushes the smallest eigenvalue of
``I + A`` below the 1 the maths guarantees. Random inputs do not reproduce that — real
patches within a frame are correlated, so ``A``'s off-diagonals are large.
"""

from __future__ import annotations

from typing import Any

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch import Tensor

from .layout import VdnLayout

# The prompt is written into both scans at half weight: both directions carry the SAME
# text state and the gather adds them, so a half each keeps the sum at roughly one copy
# while the per-frame video injections (one direction each) stay at full weight. Baked
# into every trained checkpoint — changing it retrains, it does not reconfigure.
_TEXT_STATE_SCALE = 0.5

_CONV_KERNEL = 5
_L2_NORM_EPS = 1e-6
_BRANCH_NORM_EPS = 1e-6


def _cast_to(x: Tensor, linear: nn.Module) -> Tensor:
    """Align an activation with a mixed-precision boundary Linear's own dtype."""
    return x.to(linear.weight.dtype)


def _fp32_weight(linear: nn.Module, like: Tensor) -> Tensor:
    """A layer's weight promoted to fp32 on ``like``'s device.

    The autocast-off islands read weights directly instead of calling the layer, so
    nothing reconciles dtype or device for them any more: a checkpoint-dtype weight
    against an fp32 activation raises, and under weight streaming the weight may not be
    on the activation's device yet.
    """
    return linear.weight.to(device=like.device, dtype=torch.float32)


def _temporal_shift(x: Tensor, weight: Tensor, kernel: int) -> Tensor:
    """Depthwise ``kernel``-tap conv over frames, zero-padded and non-causal.

    ``x`` is ``[frames, tokens, channels]``, ``weight`` ``[channels, kernel]`` already in
    ``x``'s dtype. Written as shifted multiply-adds rather than ``F.conv1d`` because the
    spatial half hands over exactly this layout and a conv1d would need two transposes
    of the branch's largest tensor; this is also the spelling the checkpoints were
    trained under.
    """
    pad = kernel // 2
    padded = F.pad(x, (0, 0, 0, 0, pad, pad))
    out = None
    for tap in range(kernel):
        part = padded[tap : tap + x.shape[0]] * weight[:, tap].view(1, 1, -1)
        out = part if out is None else out + part
    return out


def _activate(tokens: Tensor, l2norm: bool) -> Tensor:
    """SiLU, then L2-normalise over the head dim for q and k. ``v`` is not normalised.

    The cast back to the input dtype is load-bearing under autocast: ``F.normalize``'s
    norm is an autocast-fp32 op, so the division would promote the whole feature tensor
    and the fp32 k would then meet a bf16 v inside the deliberately autocast-off frame
    statistics.
    """
    x = F.silu(tokens)
    return F.normalize(x, dim=-1, eps=_L2_NORM_EPS).to(x.dtype) if l2norm else x


def frame_statistics(key: Tensor, value: Tensor, beta: Tensor) -> tuple[Tensor, Tensor]:
    """Per-frame delta-rule statistics from a frame's tokens.

        A[f,h,k,l] = sum_s key[f,h,s,k] beta[f,h,s] key[f,h,s,l]
        B[f,h,v,k] = sum_s value[f,h,s,v] beta[f,h,s] key[f,h,s,k]

    ``key``/``value`` are ``[F, H, S, d]``, ``beta`` is ``[F, H, S]``; both results come
    back ``[F, H, d, d]`` fp32.

    ``A`` is computed in fp32 and then explicitly symmetrised. It is symmetric by
    construction, but computed as ``(k*beta)^T k`` the ``(k,l)`` and ``(l,k)`` entries
    multiply differently-rounded operands, and on correlated real activations that
    asymmetry is large enough to push the smallest eigenvalue of ``I + A`` below 1;
    ``torch.linalg.cholesky`` reads the lower triangle only, so it then factorises an
    indefinite matrix and the delta rule dies. ``B`` is never inverted and its error
    enters the state linearly, so it stays in the input dtype until the promotion.

    Autocast must be off for the whole thing: it intercepts matmul at the op level, so
    the explicit promotions below would be re-downcast and the fp32 ``A`` this function
    exists to guarantee would silently be a bf16 one.
    """
    with torch.autocast(device_type=key.device.type, enabled=False):
        # The repacks are load-bearing, not defensive: key/value arrive from a permute,
        # so the contraction axis carries stride H*d and a batched GEMM handed those
        # strides drops to a fraction of its throughput.
        key = key.contiguous()
        key_fp32 = key.float()
        scaled = (key_fp32 * beta.unsqueeze(-1).float()).contiguous()
        weighted_value = (value * beta.unsqueeze(-1).to(value.dtype)).contiguous()

        a_stat = torch.matmul(scaled.transpose(-1, -2), key_fp32)
        a_stat = 0.5 * (a_stat + a_stat.transpose(-1, -2))
        b_stat = torch.matmul(weighted_value.transpose(-1, -2), key).float()
        return a_stat, b_stat


def vdn_solve(alpha: Tensor, a_stat: Tensor, b_stat: Tensor) -> tuple[Tensor, Tensor]:
    """The released delta rule: ``S_out = (S_in Diag(alpha) + B) (I + A)^-1``.

    Unscaled — ``tokens_per_frame`` plays no part, which is what distinguishes this rule
    from the scaled variants the checkpoints do not use. Returns the transition and the
    injection for every frame at once; the serial scan applies the transition
    repeatedly, so it is materialised rather than re-solved per step.

    ``(I + A)`` is SPD, so the inverse is one batched Cholesky, one triangular solve and
    a product — the second triangular solve ``cholesky_solve`` would do is traded for a
    GEMM, which is the same matrix to fp32 rounding on a different association.
    """
    eye = torch.eye(a_stat.shape[-1], device=a_stat.device, dtype=torch.float32).expand_as(a_stat)
    chol = torch.linalg.cholesky(a_stat + eye)
    lower_inv = torch.linalg.solve_triangular(chol, eye, upper=False, left=True)
    inverse = lower_inv.transpose(-1, -2) @ lower_inv
    return alpha.unsqueeze(-1) * inverse, b_stat @ inverse


def run_scans(
    alpha: Tensor, a_stat: Tensor, b_stat: Tensor, text_state: Tensor | None
) -> tuple[Tensor, Tensor]:
    """Forward and reverse state banks, both starting from the same initial state.

    ``prefix[t]`` summarises frames ``0..t`` and ``suffix[t]`` frames ``t..F-1``. The
    recurrence is ``state = state @ transition[t] + injection[t]`` in both directions,
    written into preallocated banks so one bank per direction is live instead of a list
    plus its stack. Autocast off throughout: cuSOLVER has no bf16 Cholesky, and the
    recurrence multiplies the decay across every frame.
    """
    with torch.autocast(device_type=a_stat.device.type, enabled=False):
        transitions, injections = vdn_solve(alpha, a_stat, b_stat)
        num_frames = transitions.shape[0]

        start = (
            torch.zeros_like(injections[0])
            if text_state is None
            else text_state.to(injections.dtype)
        )
        prefix = torch.empty(
            (num_frames, *start.shape), dtype=injections.dtype, device=injections.device
        )
        suffix = torch.empty_like(prefix)

        state = start
        for frame in range(num_frames):
            torch.baddbmm(injections[frame], state, transitions[frame], out=prefix[frame])
            state = prefix[frame]

        state = start
        for frame in range(num_frames - 1, -1, -1):
            torch.baddbmm(injections[frame], state, transitions[frame], out=suffix[frame])
            state = suffix[frame]

        return prefix, suffix


def gather_linear_state(
    prefix: Tensor,
    suffix: Tensor,
    alpha: Tensor,
    bounds: tuple[tuple[int, int], ...],
    text_state: Tensor | None,
) -> Tensor:
    """Everything outside the softmax window, in the query frame's frame of reference.

    For query frame ``t`` with window ``[lo, hi]`` the complement is exactly
    ``prefix[lo-1] + suffix[hi+1]`` — one frame outside on each side, nothing counted
    twice. Each side is then bridged to ``t`` by the product of the decay over the
    frames in between: the recurrence advances through the window while pretending those
    frames wrote nothing, because the softmax branch covers them and applying the full
    transition would double count. The product is a difference of log-prefix sums, so
    any span is one subtraction, and the decay is per KEY channel, broadcast over the
    value axis.

    The bridge indices are deliberately NOT the clamped gather indices at the ends. A
    boundary row gathers a state it then discards, but must decay the text state over
    the frames it really skipped — from the scans' virtual index -1 forward and F
    reverse. Clamping both the same way decays over one frame too few, which is a
    plausible-looking render rather than an error.
    """
    device = prefix.device
    num_frames = prefix.shape[0]
    last_before = torch.tensor([lo for lo, _ in bounds], device=device) - 1
    first_after = torch.tensor([hi for _, hi in bounds], device=device) + 1
    has_before = last_before >= 0
    has_after = first_after < num_frames
    frames = torch.arange(num_frames, device=device)

    state_before = prefix[last_before.clamp(min=0)]
    state_after = suffix[first_after.clamp(max=num_frames - 1)]
    if text_state is not None:
        # The out-of-range side reads the START of the scan, not a frame state.
        text_state = text_state.to(state_before.dtype)
        state_before = torch.where(has_before.view(-1, 1, 1, 1), state_before, text_state)
        state_after = torch.where(has_after.view(-1, 1, 1, 1), state_after, text_state)

    log_alpha = torch.log(alpha.clamp_min(1e-12))
    # The leading zero row makes the prefix EXCLUSIVE, so the empty product is 1.
    log_prefix = torch.cat([torch.zeros_like(log_alpha[:1]), log_alpha.cumsum(0)])
    bridge_before = (last_before + 1).clamp(min=0)
    bridge_after = first_after.clamp(max=num_frames)
    state_before = state_before * torch.exp(
        log_prefix[frames + 1] - log_prefix[bridge_before]
    ).unsqueeze(2)
    state_after = state_after * torch.exp(
        log_prefix[bridge_after] - log_prefix[frames]
    ).unsqueeze(2)

    if text_state is not None:
        return state_before + state_after
    return state_before * has_before.view(-1, 1, 1, 1) + state_after * has_after.view(
        -1, 1, 1, 1
    )


class MiniMaxH3ShortConv(nn.Module):
    """Depthwise separable short conv on the branch's k and v features.

    Per projection: a depthwise 5x5 spatial conv over each frame's ``(gh, gw)`` grid,
    then a depthwise 5-tap temporal conv across frames. The effective 3D stencil is the
    rank-1 outer product of the two — 30 parameters per channel, not 125. Zero-padded
    and non-causal on both axes; the token layout ``[F*S, C]`` read as ``[T, gh, gw, C]``
    is already the channels-last memory format the spatial half wants, so the permute
    costs nothing. Text rows skip the conv entirely: the prompt has no spatial grid.
    """

    KERNEL = _CONV_KERNEL

    def __init__(self, channels: int, operations: Any, dtype=None, device=None) -> None:
        super().__init__()
        for proj in ("k", "v"):
            setattr(self, f"{proj}_sp", operations.Conv2d(
                channels, channels, self.KERNEL, padding=self.KERNEL // 2,
                groups=channels, bias=False, dtype=dtype, device=device,
            ))
            setattr(self, f"{proj}_tm", operations.Conv1d(
                channels, channels, self.KERNEL, padding=self.KERNEL // 2,
                groups=channels, bias=False, dtype=dtype, device=device,
            ))

    def forward(self, proj: str, tokens: Tensor, num_frames: int,
                frame_size: tuple[int, int]) -> Tensor:
        heads, head_dim = tokens.shape[-2], tokens.shape[-1]
        grid_h, grid_w = frame_size
        channels = heads * head_dim
        volume = tokens.reshape(num_frames, grid_h, grid_w, channels).permute(0, 3, 1, 2)
        volume = getattr(self, f"{proj}_sp")(volume)
        x = volume.permute(0, 2, 3, 1).reshape(num_frames, grid_h * grid_w, channels)
        weight = getattr(self, f"{proj}_tm").weight.squeeze(1).to(
            device=x.device, dtype=x.dtype
        )
        return _temporal_shift(x, weight, self.KERNEL).reshape(-1, heads, head_dim)


class MiniMaxH3FrameAlpha(nn.Module):
    """The KDA decay gate, one value per (frame, head, key channel).

        delta = up(down(frame_mean)) + dt_bias
        alpha = exp(-exp(A_log)[h] * softplus(delta))

    ``A_log`` is per head and ``dt_bias`` per channel, the official ``fla`` layout. The
    input is the frame MEAN of the block's hidden states, so the gate is per frame, not
    per token.
    """

    def __init__(self, hidden_size: int, heads: int, head_dim: int, operations: Any,
                 dtype=None, device=None) -> None:
        super().__init__()
        self.heads = heads
        self.head_dim = head_dim
        self.down = operations.Linear(hidden_size, head_dim, bias=False, dtype=dtype, device=device)
        self.up = operations.Linear(head_dim, heads * head_dim, bias=False, dtype=dtype, device=device)
        self.A_log = nn.Parameter(torch.empty(heads, dtype=dtype, device=device))
        self.dt_bias = nn.Parameter(torch.empty(heads * head_dim, dtype=dtype, device=device))

    def forward(self, frame_mean: Tensor) -> Tensor:
        """``[F, hidden]`` fp32 -> ``[F, H, d]`` fp32."""
        # The WEIGHTS are promoted too, not just the input: with autocast off nothing
        # reconciles dtypes any more, and casting the input alone would have been an
        # fp32 activation against an 8-mantissa-bit weight. It matters because the scan
        # multiplies alpha across every frame, so a per-element error compounds.
        with torch.autocast(device_type=frame_mean.device.type, enabled=False):
            delta = F.linear(frame_mean.float(), _fp32_weight(self.down, frame_mean))
            delta = F.linear(delta, _fp32_weight(self.up, frame_mean))
            delta = delta + self.dt_bias.to(device=frame_mean.device, dtype=torch.float32)
            scale = torch.exp(self.A_log.to(device=frame_mean.device, dtype=torch.float32))
            delta = delta.view(-1, self.heads, self.head_dim)
            return torch.exp(-scale[:, None] * F.softplus(delta))


class MiniMaxH3LinearOutputGate(nn.Module):
    """The linear branch's sigmoid gate: one value per (token, head, channel).

    Low rank (the bottleneck is the head dim), matching ``fla``'s ``g_proj``. Everything
    feeding the scan reaches the loss only through this gate, which is why it is a live
    input-dependent path rather than a constant.
    """

    def __init__(self, hidden_size: int, heads: int, head_dim: int, operations: Any,
                 dtype=None, device=None) -> None:
        super().__init__()
        self.heads = heads
        self.head_dim = head_dim
        self.down = operations.Linear(hidden_size, head_dim, bias=False, dtype=dtype, device=device)
        self.up = operations.Linear(head_dim, heads * head_dim, bias=True, dtype=dtype, device=device)

    def forward(self, x: Tensor) -> Tensor:
        gate = torch.sigmoid(self.up(self.down(_cast_to(x, self.down))))
        return gate.view(-1, self.heads, self.head_dim)


class MiniMaxH3SoftmaxGate(nn.Module):
    """The windowed softmax branch's mixing gate: one scalar per (token, head).

    It belongs to the softmax side, but the checkpoint carries it under the same
    ``transformer_blocks.N.attn.`` prefix as the linear branch, so it is defined here and
    read by whoever composes the two. The windowed softmax renormalises to 1 no matter
    how little mass it saw; this rescales it toward the share it actually captured.
    """

    def __init__(self, hidden_size: int, heads: int, operations: Any,
                 dtype=None, device=None) -> None:
        super().__init__()
        self.heads = heads
        self.up = operations.Linear(hidden_size, heads, bias=True, dtype=dtype, device=device)

    def forward(self, x: Tensor) -> Tensor:
        return torch.sigmoid(self.up(_cast_to(x, self.up))).view(-1, self.heads, 1)


class MiniMaxH3BidirectionalLinearAttention(nn.Module):
    """The recurrence itself: video tokens in, the gated and normalised readout out."""

    def __init__(self, hidden_size: int, heads: int, head_dim: int, operations: Any,
                 dtype=None, device=None) -> None:
        super().__init__()
        self.heads = heads
        self.head_dim = head_dim
        self.short_conv = MiniMaxH3ShortConv(heads * head_dim, operations, dtype=dtype, device=device)
        self.alpha = MiniMaxH3FrameAlpha(hidden_size, heads, head_dim, operations, dtype=dtype, device=device)
        self.beta_proj = operations.Linear(hidden_size, heads, bias=False, dtype=dtype, device=device)
        self.output_gate = MiniMaxH3LinearOutputGate(hidden_size, heads, head_dim, operations, dtype=dtype, device=device)
        self.norm = operations.RMSNorm(head_dim, eps=_BRANCH_NORM_EPS, dtype=dtype, device=device)

    def _features(self, tokens: Tensor, proj: str, num_frames: int,
                  frame_size: tuple[int, int] | None) -> Tensor:
        convolved = (
            self.short_conv(proj, tokens, num_frames, frame_size)
            if frame_size is not None and proj in ("k", "v")
            else tokens
        )
        return _activate(convolved, l2norm=proj != "v")

    def _text_state(self, text_x: Tensor, text_qkv: tuple[Tensor, Tensor, Tensor]) -> Tensor:
        """Half the prompt written into a zero state as ONE delta-rule chunk.

        No causal scan inside the text: the text encoder and token refiner have already
        written word order into every text hidden state, so the whole prompt is one
        chunk update, exactly what a video frame gets. The decay plays no part (the old
        state is zero, so the transition multiplies nothing) — only the injection.
        Returns ``[H, d, d]`` fp32.
        """
        length = text_qkv[1].shape[0]
        key = self._features(text_qkv[1], "k", 0, None)
        value = self._features(text_qkv[2], "v", 0, None)
        key = key.view(1, length, self.heads, self.head_dim).permute(0, 2, 1, 3)
        value = value.view(1, length, self.heads, self.head_dim).permute(0, 2, 1, 3)
        beta = torch.sigmoid(self.beta_proj(_cast_to(text_x, self.beta_proj)))
        beta = beta.view(1, length, self.heads).permute(0, 2, 1)

        a_stat, b_stat = frame_statistics(key, value, beta)
        with torch.autocast(device_type=a_stat.device.type, enabled=False):
            ones = torch.ones(1, self.heads, self.head_dim, device=a_stat.device, dtype=a_stat.dtype)
            _, injection = vdn_solve(ones, a_stat, b_stat)
        return _TEXT_STATE_SCALE * injection[0]

    def forward(self, video_x: Tensor, video_qkv: tuple[Tensor, Tensor, Tensor],
                layout: VdnLayout, text_x: Tensor | None,
                text_qkv: tuple[Tensor, Tensor, Tensor] | None) -> Tensor:
        """``[F*S, hidden]`` -> ``[F*S, H*d]``, the readout before ``to_out_linear``.

        Under ``skip_ends`` the two anchor frames are dropped from the INPUT rather than
        masked out of it, and their readout rows are exactly zero, so the softmax and
        linear sides stay an exact partition.
        """
        num_frames = layout.num_frames
        tokens_per_frame = layout.tokens_per_frame
        if not layout.skip_ends:
            return self._readout(video_x, video_qkv, layout.branch_bounds, num_frames,
                                 tokens_per_frame, layout.frame_size, text_x, text_qkv)

        rows = num_frames * tokens_per_frame
        if num_frames <= 2:                      # the anchors ARE the clip
            return video_x.new_zeros(rows, self.heads * self.head_dim)

        interior = slice(tokens_per_frame, (num_frames - 1) * tokens_per_frame)
        readout = self._readout(
            video_x[interior], tuple(t[interior] for t in video_qkv), layout.branch_bounds,
            num_frames - 2, tokens_per_frame, layout.frame_size, text_x, text_qkv,
        )
        out = readout.new_empty(rows, readout.shape[-1])
        out[:tokens_per_frame].zero_()
        out[(num_frames - 1) * tokens_per_frame:].zero_()
        out[interior] = readout
        return out

    def _readout(self, video_x: Tensor, video_qkv: tuple[Tensor, Tensor, Tensor],
                 bounds: tuple[tuple[int, int], ...], num_frames: int,
                 tokens_per_frame: int, frame_size: tuple[int, int],
                 text_x: Tensor | None,
                 text_qkv: tuple[Tensor, Tensor, Tensor] | None) -> Tensor:
        """The branch algorithm over exactly the frames it owns — no anchor notion."""
        heads, head_dim = self.heads, self.head_dim
        rows = num_frames * tokens_per_frame
        per_frame = (num_frames, tokens_per_frame, heads, head_dim)

        query, key, value = (
            self._features(tokens, proj, num_frames, frame_size)
            for proj, tokens in zip(("q", "k", "v"), video_qkv)
        )
        query_by_frame = query.view(per_frame)
        key_by_frame = key.view(per_frame).permute(0, 2, 1, 3)
        value_by_frame = value.view(per_frame).permute(0, 2, 1, 3)
        beta = torch.sigmoid(self.beta_proj(_cast_to(video_x, self.beta_proj)))
        beta = beta.view(num_frames, tokens_per_frame, heads).permute(0, 2, 1)

        a_stat, b_stat = frame_statistics(key_by_frame, value_by_frame, beta)
        del key, value, key_by_frame, value_by_frame

        # dtype=fp32 on the mean itself, not just inside the gate: the hidden states are
        # bf16, and a mean taken at their precision is already rounded before the fp32
        # island can help it.
        alpha = self.alpha(
            video_x.view(num_frames, tokens_per_frame, -1).mean(dim=1, dtype=torch.float32)
        )

        text_state = (
            self._text_state(text_x, text_qkv) if text_x is not None and text_qkv is not None
            else None
        )
        prefix, suffix = run_scans(alpha, a_stat, b_stat, text_state)
        linear_state = gather_linear_state(prefix, suffix, alpha, bounds, text_state)
        linear_state = linear_state.to(video_x.dtype)
        del prefix, suffix, a_stat, b_stat

        readout = torch.einsum("fhvk,fshk->fshv", linear_state, query_by_frame)
        del query, query_by_frame, linear_state
        readout = self.norm(readout.reshape(rows, heads, head_dim))
        return (readout * self.output_gate(video_x)).reshape(rows, heads * head_dim)


class MiniMaxH3LinearBranch(nn.Module):
    """The VDN linear branch of one DiT block, checkpoint-shaped.

    Submodule names are the checkpoint's own tails under
    ``transformer_blocks.N.attn.``, so a sliced state dict loads 1:1:
    ``linear_attention.*``, ``softmax_gate.up.*`` and ``to_out_linear.weight``. The
    softmax gate belongs to the windowed-softmax side but shares that prefix, so it is
    built here and read by the composer.

    ``forward`` takes the block's pre-attention hidden states and the RAW pre-QK-norm,
    pre-RoPE q/k/v for the whole packed sequence, and returns the addition for the video
    rows only — the caller adds it to those rows of the original attention's output.
    """

    def __init__(self, hidden_size: int, heads: int, head_dim: int, operations: Any,
                 dtype=None, device=None) -> None:
        super().__init__()
        self.hidden_size = hidden_size
        self.heads = heads
        self.head_dim = head_dim
        self.linear_attention = MiniMaxH3BidirectionalLinearAttention(
            hidden_size, heads, head_dim, operations, dtype=dtype, device=device
        )
        self.softmax_gate = MiniMaxH3SoftmaxGate(hidden_size, heads, operations, dtype=dtype, device=device)
        self.to_out_linear = operations.Linear(
            heads * head_dim, hidden_size, bias=False, dtype=dtype, device=device
        )

    def forward(self, x: Tensor, q_raw: Tensor, k_raw: Tensor, v_raw: Tensor,
                layout: VdnLayout) -> Tensor:
        """``[T, hidden]`` and three ``[T, H, d]`` -> ``[T_video, hidden]``.

        The prompt seeds both scans whenever the layout carries text rows; with none it
        is the plain complement of the softmax window, both scans starting from zero.
        """
        video = slice(layout.video_start, layout.video_end)
        video_qkv = (q_raw[video], k_raw[video], v_raw[video])
        text_x = text_qkv = None
        if layout.text_len:
            text_start, text_end = layout.text_range
            text = slice(text_start, text_end)
            text_x = x[text]
            text_qkv = (q_raw[text], k_raw[text], v_raw[text])

        readout = self.linear_attention(x[video], video_qkv, layout, text_x, text_qkv)
        return self.to_out_linear(_cast_to(readout, self.to_out_linear))
