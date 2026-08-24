"""A1111-style prompt attention weighting for the native text encoders.

Two primitives shared by every native ``ClipTextEncoder`` adapter (Flux / Klein /
Krea-2 / Qwen-Image / Wan):

  * :func:`parse_a1111` — parse ``(word:1.3)`` / ``[word]`` / nesting / escapes /
    ``BREAK`` into ``[(segment, weight), ...]``. Adapted verbatim from this repo's
    SDXL parser (``src/pipelines/pipes/checkpoint_loader/sdxl/sdxl_clip.py``
    ``SDXLClipTextEncoder.parse_prompt_attention``) — duplicated rather than moved
    because the SDXL path is guarded by tests that require ``diffusers`` (not
    importable in this environment), so a shared move can't be regression-checked
    here. Keep the two in sync if the SDXL grammar changes.

  * :func:`apply_token_weights` — ComfyUI's weight application for non-CLIP
    encoders (``comfy/sd1_clip.py`` ``SDClipModel.encode_token_weights``): scale
    each OUTPUT token embedding relative to the empty-prompt baseline,
    ``z[j] = (z[j] - z_empty[j]) * w[j] + z_empty[j]``. This is the method the T5 /
    llama / qwen-family encoders inherit unchanged — output-embedding scaling, NOT
    input-embedding scaling.

``has_weights`` lets callers short-circuit to a plain encode (bit-identical) when
a prompt carries no weight syntax — the weighting is then a strict no-op.
"""

from __future__ import annotations

import math
import re

import torch

_RE_ATTENTION = re.compile(
    r"""
    \\\(|\\\)|\\\[|\\\]|\\\\|\\||\(|\[|:([+-]?[.\d]+)\)|
    \)|]|[^\\()\[\]:]+|:
    """,
    re.X,
)
_RE_BREAK = re.compile(r"\s*\bBREAK\b\s*", re.S)

_ROUND_MULT = 1.1
_SQUARE_MULT = 1.0 / 1.1


def parse_a1111(text: str) -> list[list]:
    """Parse A1111 attention syntax into ``[[segment, weight], ...]``.

    ``(word)`` -> x1.1, ``[word]`` -> /1.1, ``(word:1.3)`` -> explicit, nesting
    multiplies, ``\\(`` escapes a literal paren, ``BREAK`` becomes a ``["BREAK",
    -1]`` marker. Consecutive equal-weight segments are merged.
    """
    round_brackets: list[int] = []
    square_brackets: list[int] = []
    res: list[list] = []

    def multiply_range(start: int, multiplier: float) -> None:
        for p in range(start, len(res)):
            res[p][1] *= multiplier

    for m in _RE_ATTENTION.finditer(text):
        chunk = m.group(0)
        weight = m.group(1)
        if chunk.startswith("\\"):
            res.append([chunk[1:], 1.0])
        elif chunk == "(":
            round_brackets.append(len(res))
        elif chunk == "[":
            square_brackets.append(len(res))
        elif weight is not None and round_brackets:
            multiply_range(round_brackets.pop(), float(weight))
        elif chunk == ")" and round_brackets:
            multiply_range(round_brackets.pop(), _ROUND_MULT)
        elif chunk == "]" and square_brackets:
            multiply_range(square_brackets.pop(), _SQUARE_MULT)
        else:
            parts = re.split(_RE_BREAK, chunk)
            for i, part in enumerate(parts):
                if i > 0:
                    res.append(["BREAK", -1])
                res.append([part, 1.0])

    for pos in round_brackets:
        multiply_range(pos, _ROUND_MULT)
    for pos in square_brackets:
        multiply_range(pos, _SQUARE_MULT)

    if not res:
        res = [["", 1.0]]

    i = 0
    while i + 1 < len(res):
        if res[i][1] == res[i + 1][1]:
            res[i][0] += res[i + 1][0]
            res.pop(i + 1)
        else:
            i += 1
    return res


def transform_weight(weight: float) -> float:
    """A1111 non-linear weight transform (matches the SDXL adapter): negatives are
    clamped to 1.0 (no negative attention), then ``w**1.2`` above 1 / ``w**0.8``
    below (softens the effect), 1.0 stays 1.0."""
    if weight < 0:
        return 1.0
    if weight == 1.0:
        return 1.0
    return math.pow(weight, 1.2) if weight > 1 else math.pow(weight, 0.8)


def has_weights(segments: list[list]) -> bool:
    """True when any parsed segment carries a non-1.0 (real) weight."""
    return any(w != 1.0 and w >= 0 for _seg, w in segments)


def strip_syntax(segments: list[list]) -> str:
    """Reconstruct the plain prompt text (A1111 syntax removed) from segments."""
    return "".join(seg for seg, w in segments if w != -1)  # drop BREAK markers


def weighted_token_ids(raw_tok, prompt: str, prefix: str = "", suffix: str = "") -> tuple[list[int], list[float]]:
    """Tokenize ``prefix + <weighted prompt> + suffix`` -> ``(ids, per-token weights)``.

    Template ``prefix``/``suffix`` tokens get weight 1.0; each A1111 prompt segment
    contributes its transformed weight for all of its tokens. Segments are
    tokenized separately (``add_special_tokens=False``) so a weight boundary can
    fall mid-string — the standard ComfyUI/A1111 behaviour (BPE merges across the
    boundary are accepted). ``raw_tok`` is a HuggingFace tokenizer.

    Callers should only use this when :func:`has_weights` is True; the unweighted
    path stays on the plain single-string tokenize so it is bit-identical.
    """
    ids: list[int] = []
    weights: list[float] = []
    if prefix:
        pid = raw_tok(prefix, add_special_tokens=False)["input_ids"]
        ids += pid
        weights += [1.0] * len(pid)
    for seg, w in parse_a1111(prompt):
        if w == -1 or seg == "":  # BREAK marker / empty run
            continue
        sid = raw_tok(seg, add_special_tokens=False)["input_ids"]
        ids += sid
        weights += [transform_weight(w)] * len(sid)
    if suffix:
        sid = raw_tok(suffix, add_special_tokens=False)["input_ids"]
        ids += sid
        weights += [1.0] * len(sid)
    return ids, weights


def apply_token_weights(
    embeds: torch.Tensor,
    weights: torch.Tensor,
    baseline: torch.Tensor,
) -> torch.Tensor:
    """ComfyUI output-embedding weighting: ``(z - z_empty) * w + z_empty``.

    ``embeds``   : ``(B, S, ...)`` encoder output for the real prompt.
    ``baseline`` : ``(B, S, ...)`` encoder output for the empty prompt (same S).
    ``weights``  : ``(B, S)`` per-token weight (1.0 = unchanged).

    Broadcasts the per-token weight over the trailing feature dims (so it works
    for both flat ``(B,S,D)`` and Krea-2's layered ``(B,S,L,D)`` contexts). Tokens
    with weight exactly 1.0 are returned unchanged (numerically exact).
    """
    if embeds.shape[:2] != baseline.shape[:2]:
        raise ValueError(f"embeds {tuple(embeds.shape)} and baseline {tuple(baseline.shape)} disagree on (B,S)")
    w = weights.to(dtype=embeds.dtype, device=embeds.device)
    # add trailing singleton dims to broadcast over the feature axes.
    while w.ndim < embeds.ndim:
        w = w.unsqueeze(-1)
    scaled = (embeds - baseline) * w + baseline
    # weight-1.0 tokens must be left EXACTLY unchanged (ComfyUI only touches != 1.0;
    # (e - b) * 1 + b is not bit-exact in float), so restore them via a mask.
    return torch.where(w == 1.0, embeds, scaled)
