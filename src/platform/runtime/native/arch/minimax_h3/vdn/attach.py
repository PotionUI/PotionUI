"""Attaching the VDN branch to a loaded MiniMax-H3 model.

The branch checkpoint is a second file alongside the DiT weights, keyed
``transformer_blocks.N.attn.<tail>`` where the tails are exactly
:class:`~.linear_branch.MiniMaxH3LinearBranch`'s own submodule names. Attaching
replaces every MAIN block's attention with :class:`~.hybrid_attention.MiniMaxH3VdnAttention`,
which adopts that attention's projections unchanged and holds the branch under
``linear``. The token refiner's blocks are left alone: they attend over text only,
where there is no frame axis to window.

Nothing detaches. A VDN model is a different model — a separate cache entry — not a
mode of the dense one.
"""

from __future__ import annotations

from dataclasses import dataclass

from torch import Tensor, nn

from .hybrid_attention import MiniMaxH3VdnAttention

__all__ = ["AttachReport", "attach_vdn_branch", "vdn_attached"]

_BLOCK_PREFIX = "transformer_blocks."
_ATTN_INFIX = ".attn."


@dataclass(frozen=True)
class AttachReport:
    """What one :func:`attach_vdn_branch` call put on the model."""

    blocks: int
    tensors: int
    bytes: int


def vdn_attached(model: nn.Module) -> bool:
    """Whether every main block of ``model`` carries a VDN branch."""
    blocks = getattr(model, "blocks", None)
    if not blocks:
        return False
    return all(getattr(block.attn, "vdn_branch_attached", False) for block in blocks)


def _split_key(key: str) -> tuple[int, str]:
    if not key.startswith(_BLOCK_PREFIX) or _ATTN_INFIX not in key:
        raise ValueError(
            f"branch key {key!r} is not a '{_BLOCK_PREFIX}N{_ATTN_INFIX}<tail>' key"
        )
    index, _, tail = key[len(_BLOCK_PREFIX):].partition(_ATTN_INFIX)
    if not index.isdigit():
        raise ValueError(f"branch key {key!r} has no block index")
    return int(index), tail


def attach_vdn_branch(
    model: nn.Module,
    branch_state_dict: dict[str, Tensor],
    *,
    operations=None,
    anchor_frames: str = "both",
    backend: str | None = None,
) -> AttachReport:
    """Replace every main block's attention with the hybrid one and load the branch.

    ``operations`` defaults to the model's own. The branch tensors are assign-loaded
    onto a meta-device module, so they are installed as-is: no copy, and their device
    and dtype are whatever the caller read them at. The base attention's parameters are
    the same tensor objects afterwards — attaching moves no weights.

    Strict in both directions and validated for the whole checkpoint before anything is
    replaced, so a key typo cannot leave the model half-attached.
    """
    if vdn_attached(model):
        raise ValueError("this model already carries a VDN branch; attach once per model")
    blocks = model.blocks
    if operations is None:
        operations = model.operations
    config = model.config

    by_block: dict[int, dict[str, Tensor]] = {}
    for key, value in branch_state_dict.items():
        index, tail = _split_key(key)
        if not 0 <= index < len(blocks):
            raise ValueError(
                f"branch key {key!r} names block {index}, but the model has "
                f"{len(blocks)} blocks"
            )
        by_block.setdefault(index, {})[tail] = value

    wrappers: list[MiniMaxH3VdnAttention] = []
    tensors = 0
    total_bytes = 0
    for index, block in enumerate(blocks):
        wrapper = MiniMaxH3VdnAttention(
            block.attn, config.hidden_size, operations,
            anchor_frames=anchor_frames, backend=backend,
            dtype=block.attn.qkv_proj.weight.dtype, device="meta",
        )
        provided = by_block.get(index, {})
        expected = set(wrapper.linear.state_dict())
        missing = sorted(expected - set(provided))
        if missing:
            raise ValueError(
                f"branch checkpoint is missing '{_BLOCK_PREFIX}{index}{_ATTN_INFIX}"
                f"{missing[0]}' ({len(missing)} tensors missing for block {index})"
            )
        unexpected = sorted(set(provided) - expected)
        if unexpected:
            raise ValueError(
                f"branch checkpoint has an unexpected key '{_BLOCK_PREFIX}{index}"
                f"{_ATTN_INFIX}{unexpected[0]}' ({len(unexpected)} unexpected for "
                f"block {index})"
            )
        wrapper.linear.load_state_dict(provided, strict=True, assign=True)
        wrappers.append(wrapper)
        tensors += len(provided)
        total_bytes += sum(t.numel() * t.element_size() for t in provided.values())

    for block, wrapper in zip(blocks, wrappers):
        block.attn = wrapper
    return AttachReport(blocks=len(wrappers), tensors=tensors, bytes=total_bytes)
