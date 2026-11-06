# Derived from: https://github.com/aigc-apps/VideoX-Fun/blob/main/videox_fun/models/minimax_h3_transformer3d_control.py (Apache-2.0)
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass

import torch
from torch import Tensor, nn

from ...errors import NativeEngineUnsupportedError
from ...kernels import fp8_ops
from ...kernels.operations import detect_quant_format
from .config import MiniMaxH3Config
from .model import MiniMaxH3Block

__all__ = [
    "CONTROL_V1_PLACES",
    "CONTROL_V2_PLACES",
    "HINT_ROWS_NON_AUDIO",
    "HINT_ROWS_TRAINED",
    "ControlContext",
    "ControlPatchInfo",
    "MiniMaxH3ControlBlock",
    "MiniMaxH3ControlBranch",
    "attach_control_branch",
    "control_attached",
    "detect_control_patch",
]

CONTROL_V1_PLACES = (0, 10, 20, 30, 40)
CONTROL_V2_PLACES = (0, 5, 10, 15, 20, 25, 30, 35, 40, 45)
HINT_ROWS_TRAINED = "trained"
HINT_ROWS_NON_AUDIO = "non_audio"
HINT_ROW_MODES = (HINT_ROWS_TRAINED, HINT_ROWS_NON_AUDIO)
PRE_NORM = "pre_norm"
POST_NORM = "post_norm"
PRUNED_PATCH_NAME = "minimax_h3_fun_controlnet_union_2.0_pruned_*.safetensors from Comfy-Org/MiniMax-H3 (model_patches/)"

_BLOCK_KEY = re.compile(r"^control_blocks\.(\d+)\.")


@dataclass(frozen=True)
class ControlPatchInfo:
    kind: str
    places: tuple[int, ...]
    masked_pixel_mode: str
    hidden_size: int
    time_embed_dim: int
    inner_dim: int
    control_in_columns: int
    quantized: bool

    @property
    def num_blocks(self) -> int:
        return len(self.places)

    @property
    def inpaint(self) -> bool:
        return self.control_in_columns > 0


@dataclass(frozen=True)
class ControlContext:
    rows: Tensor
    scale: float
    hint_rows: str = HINT_ROWS_TRAINED

    def __post_init__(self) -> None:
        if self.hint_rows not in HINT_ROW_MODES:
            raise ValueError(f"hint_rows must be one of {HINT_ROW_MODES}, got {self.hint_rows!r}")
        if self.rows.ndim != 3 or self.rows.shape[0] != 1:
            raise ValueError(f"control rows must be a (1, rows, columns) tensor, got {list(self.rows.shape)}")


def _parse_places(raw: str) -> tuple[int, ...]:
    try:
        values = json.loads(raw)
        return tuple(int(v) for v in values)
    except (TypeError, ValueError) as exc:
        raise NativeEngineUnsupportedError(f"control_blocks_places metadata {raw!r} is not a list of integers") from exc


def detect_control_patch(sd: dict[str, Tensor], metadata: dict[str, str] | None = None) -> ControlPatchInfo | None:
    metadata = metadata or {}
    if not any(k.startswith("control_blocks.") for k in sd) and "control_proj_in.weight" not in sd:
        return None
    if "control_blocks.0.attn.to_q.weight" in sd or "control_blocks.0.ff.net.0.proj.weight" in sd:
        raise NativeEngineUnsupportedError(
            "this is the original full-AdaLN MiniMax-H3 Fun ControlNet (needs the 33B full DiT's time_embedder); "
            f"use the pruned Comfy-Org patch instead: {PRUNED_PATCH_NAME}"
        )
    needed = (
        "control_proj_in.weight", "control_proj_in.bias",
        "control_blocks.0.before_proj.weight", "control_blocks.0.after_proj.weight",
        "control_blocks.0.attn.qkv_proj.weight", "control_blocks.0.adaln_proj.linear.weight",
    )
    absent = [k for k in needed if k not in sd]
    if absent:
        raise NativeEngineUnsupportedError(f"not a pruned MiniMax-H3 Fun ControlNet patch: missing {absent[0]}")
    indices = {int(m.group(1)) for k in sd for m in [_BLOCK_KEY.match(k)] if m}
    count = len(indices)
    if indices != set(range(count)) or count not in (len(CONTROL_V1_PLACES), len(CONTROL_V2_PLACES)):
        raise NativeEngineUnsupportedError(
            f"control patch has blocks {sorted(indices)}; expected 5 (v1) or 10 (2.0) consecutive blocks"
        )
    kind = "v1" if count == len(CONTROL_V1_PLACES) else "2.0"
    default_places = CONTROL_V1_PLACES if kind == "v1" else CONTROL_V2_PLACES
    default_mode = PRE_NORM if kind == "v1" else POST_NORM
    places = _parse_places(metadata["control_blocks_places"]) if "control_blocks_places" in metadata else default_places
    mode = metadata.get("inpaint_masked_pixel_mode", default_mode)
    if len(places) != count:
        raise NativeEngineUnsupportedError(
            f"control_blocks_places lists {len(places)} places but the patch holds {count} control blocks "
            f"({kind} config on a mismatched state dict)"
        )
    if list(places) != sorted(set(places)) or places[0] != 0:
        raise NativeEngineUnsupportedError(f"control_blocks_places must be strictly increasing from 0, got {list(places)}")
    if places != default_places:
        raise NativeEngineUnsupportedError(
            f"control_blocks_places {list(places)} does not match the {kind} layout {list(default_places)}"
        )
    if mode not in (PRE_NORM, POST_NORM) or mode != default_mode:
        raise NativeEngineUnsupportedError(
            f"inpaint_masked_pixel_mode {mode!r} does not match the {kind} patch (expected {default_mode!r})"
        )
    hidden = int(sd["control_blocks.0.after_proj.weight"].shape[0])
    inner = int(sd["control_blocks.0.attn.qkv_proj.weight"].shape[0]) // 3
    time_dim = int(sd["control_blocks.0.adaln_proj.linear.weight"].shape[1])
    columns = int(sd["control_proj_in.weight"].shape[1])
    quantized = detect_quant_format(metadata, sd) is not None
    return ControlPatchInfo(kind, tuple(places), mode, hidden, time_dim, inner, columns, quantized)


class MiniMaxH3ControlBlock(MiniMaxH3Block):
    def __init__(self, hidden_size: int, heads: int, head_dim: int, ffn_dim: int, t_dim: int,
                 apply_silu: bool, norm_eps: float, qk_norm_eps: float, operations, first: bool,
                 dtype=None, device=None) -> None:
        super().__init__(hidden_size, heads, head_dim, ffn_dim, t_dim, apply_silu, norm_eps, qk_norm_eps,
                         operations, dtype=dtype, device=device)
        if first:
            self.before_proj = operations.Linear(hidden_size, hidden_size, bias=True, dtype=dtype, device=device)
        self.after_proj = operations.Linear(hidden_size, hidden_size, bias=True, dtype=dtype, device=device)

    def step(self, c: Tensor, packed: Tensor | None, temb: Tensor, adaln_indices: Tensor,
             rotary_emb: tuple[Tensor, Tensor], seq_chunk_rows: int, ffn_chunk_rows: int) -> tuple[Tensor, Tensor]:
        if hasattr(self, "before_proj"):
            c = self.before_proj(c.to(self.before_proj.weight.dtype)).to(c.dtype) + packed
        c = self(c, temb, adaln_indices, rotary_emb, None, seq_chunk_rows, None, ffn_chunk_rows)
        return c, self.after_proj(c.to(self.after_proj.weight.dtype)).to(c.dtype)


class MiniMaxH3ControlBranch(nn.Module):
    def __init__(self, config: MiniMaxH3Config, places: tuple[int, ...], columns: int, operations,
                 dtype=None, device=None) -> None:
        super().__init__()
        self.places = tuple(places)
        self.control_proj_in = operations.Linear(columns, config.hidden_size, bias=True,
                                                 dtype=torch.float32, device=device)
        self.control_blocks = nn.ModuleList([
            MiniMaxH3ControlBlock(
                config.hidden_size, config.num_attention_heads, config.attention_head_dim, config.ffn_dim,
                config.time_embed_dim, not config.pruned, config.norm_eps, config.qk_norm_eps, operations,
                first=n == 0, dtype=dtype, device=device,
            )
            for n in range(len(places))
        ])

    def stream(self, context: ControlContext, packed: Tensor, video_indices: Tensor, audio_indices: Tensor,
               num_condition_video_rows: int) -> "ControlStream":
        return ControlStream(self, context, packed, video_indices, audio_indices, num_condition_video_rows)


class ControlStream:
    def __init__(self, branch: MiniMaxH3ControlBranch, context: ControlContext, packed: Tensor,
                 video_indices: Tensor, audio_indices: Tensor, num_condition_video_rows: int) -> None:
        target = video_indices[num_condition_video_rows:]
        if context.rows.shape[1] != target.shape[0]:
            raise ValueError(
                f"control carries {context.rows.shape[1]} rows but the packed sequence has {target.shape[0]} "
                "target video rows; one control row per target row is required"
            )
        rows = context.rows.to(packed.device)
        proj = branch.control_proj_in
        if rows.shape[2] != proj.weight.shape[1]:
            raise ValueError(
                f"control rows are {rows.shape[2]} columns wide but control_proj_in expects {proj.weight.shape[1]}"
            )
        embeds = proj(rows.to(proj.weight.dtype)).to(packed.dtype)
        self.branch = branch
        self.packed = packed
        self.scale = float(context.scale)
        self.c = packed.index_copy(1, target, embeds)
        keep = torch.ones(packed.shape[1], dtype=packed.dtype, device=packed.device)
        keep[audio_indices] = 0
        if context.hint_rows == HINT_ROWS_TRAINED and num_condition_video_rows:
            keep[video_indices[:num_condition_video_rows]] = 0
        self.keep = keep.view(1, -1, 1)
        self.slot = {place: n for n, place in enumerate(branch.places)}
        self.hint: Tensor | None = None

    def before(self, layer: int, temb: Tensor, adaln_indices: Tensor, rotary_emb: tuple[Tensor, Tensor],
               seq_chunk_rows: int, ffn_chunk_rows: int) -> None:
        n = self.slot.get(layer)
        if n is None:
            return
        block = self.branch.control_blocks[n]
        self.c, self.hint = block.step(
            self.c, self.packed if n == 0 else None, temb, adaln_indices, rotary_emb, seq_chunk_rows, ffn_chunk_rows,
        )

    def after(self, layer: int, hidden: Tensor) -> Tensor:
        if layer not in self.slot or self.hint is None:
            return hidden
        out = hidden + (self.hint * self.keep).to(hidden.dtype) * self.scale
        self.hint = None
        if layer == self.branch.places[-1]:
            self.c = None
        return out


def control_attached(model: nn.Module) -> bool:
    return getattr(model, "control_branch", None) is not None


def _validate_against_model(model: nn.Module, info: ControlPatchInfo) -> None:
    config: MiniMaxH3Config = model.config
    if not config.pruned:
        raise NativeEngineUnsupportedError(
            "the pruned Fun ControlNet patch needs a pruned MiniMax-H3 DiT (adaln_t_table); this DiT is the full one"
        )
    checks = (
        ("hidden size", info.hidden_size, config.hidden_size),
        ("AdaLN width", info.time_embed_dim, config.time_embed_dim),
        ("attention width", info.inner_dim, config.inner_dim),
    )
    for label, got, want in checks:
        if got != want:
            raise NativeEngineUnsupportedError(f"the control patch {label} is {got}, the DiT's is {want}")
    if info.places[-1] >= config.num_layers:
        raise NativeEngineUnsupportedError(
            f"control place {info.places[-1]} is outside a {config.num_layers}-block DiT"
        )
    video = config.video_patch_dim
    inpaint = (1 + config.in_channels) * math.prod(config.patch_size)
    if info.control_in_columns not in (video, video + inpaint):
        raise NativeEngineUnsupportedError(
            f"control_proj_in takes {info.control_in_columns} columns; expected {video} or {video + inpaint}"
        )


def attach_control_branch(model: nn.Module, patch_sd: dict[str, Tensor], info: ControlPatchInfo) -> int:
    if control_attached(model):
        raise ValueError("this model already carries a control branch; attach once per model")
    _validate_against_model(model, info)
    operations = fp8_ops if info.quantized else model.operations
    placeholder = next((t.dtype for k, t in patch_sd.items() if k.endswith("after_proj.weight")), torch.bfloat16)
    branch = MiniMaxH3ControlBranch(model.config, info.places, info.control_in_columns, operations,
                                    dtype=placeholder, device="meta")
    branch.requires_grad_(False)
    result = branch.load_state_dict(patch_sd, strict=False, assign=True)
    if result.missing_keys:
        raise ValueError(
            f"control patch is missing '{result.missing_keys[0]}' ({len(result.missing_keys)} tensors missing)"
        )
    if result.unexpected_keys:
        raise ValueError(
            f"control patch has an unexpected key '{result.unexpected_keys[0]}' "
            f"({len(result.unexpected_keys)} unexpected)"
        )
    model.control_branch = branch
    model.control_info = info
    return sum(t.numel() * t.element_size() for t in patch_sd.values())
