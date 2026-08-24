"""Map LoRA checkpoint keys onto native Flux module parameter names.

Flux LoRAs ship in several key dialects. This module normalises all of them to
the native module's own parameter names (``double_blocks.0.img_attn.qkv.weight``
etc.) so :mod:`apply` can patch weights uniformly.

Supported dialects (the two families the task calls out, plus their common
spellings):

  * **comfy / kohya underscore** — stem ``lora_unet_double_blocks_0_img_attn_qkv``
    with ``.lora_up.weight`` / ``.lora_down.weight`` (+ ``.alpha``). The
    underscore mangling is ambiguous to invert directly (param names contain
    underscores), so we build the reverse map from the *actual* module param
    names, exactly like ComfyUI's ``model_lora_keys_unet``.
  * **comfy generic** — stem ``double_blocks.0.img_attn.qkv`` or
    ``diffusion_model.double_blocks.0.img_attn.qkv`` (dotted, no mangling).
  * **diffusers / PEFT** — stem ``transformer.transformer_blocks.0.attn.to_q``
    with ``.lora_A.weight`` / ``.lora_B.weight``. Diffusers keeps attention
    projections *split* (``to_q``/``to_k``/``to_v``) while the native module
    fuses them into one ``qkv`` weight, so these map to a **row-slice** of the
    fused target (``target_slice``). Also handles the ``lora_transformer_*`` /
    ``lycoris_*`` (OneTrainer / SimpleTuner) and bare-dotted (DiffSynth) prefixes.

``LoraDelta.up @ LoraDelta.down`` is the low-rank update; the effective patch is
``(scale * alpha / rank) * up @ down`` (see :func:`vendor.gpl.comfyui.ops.apply_lora_deltas`).

API note: ``map_lora_keys`` returns ``dict[param_name, list[LoraDelta]]`` (not a
single delta) because a fused ``qkv`` weight legitimately receives three deltas
(q, k, v) from a diffusers LoRA — each into a different ``target_slice``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import torch
import torch.nn as nn

logger = logging.getLogger(__name__)


@dataclass
class LoraDelta:
    """One weight patch for a (possibly sliced) target weight.

    Two kinds share this container:

    * plain LoRA (``kron=False``): ``delta = (scale * alpha / rank) * up @ down``
      with ``down (rank, in)`` / ``up (out|slice, rank)``.
    * LoKr / LyCORIS Kronecker (``kron=True``): ``up``/``down`` hold the two
      Kronecker factors ``w1``/``w2`` (factorized sides already combined at map
      time) and ``delta = (scale * alpha) * kron(w1, w2).reshape(W.shape)`` —
      ``alpha`` here is the PRE-DIVIDED LyCORIS factor (``alpha/dim`` when a
      side was factorized, else 1.0, matching ComfyUI's LoKr adapter). Never
      sliced (LoKr targets whole weights).
    """

    down: torch.Tensor          # (rank, in_features) | LoKr w2
    up: torch.Tensor            # (out_features | slice_len, rank) | LoKr w1
    alpha: float
    scale: float = 1.0          # user strength multiplier
    # (dim, start, length) into the target weight; None = whole weight.
    target_slice: tuple[int, int, int] | None = None
    kron: bool = False


# Native-param -> (dim, start_fn, length_fn) slice specs for the fused
# attention weights, mirroring comfy.utils.flux_to_diffusers offsets.
def _double_block_diffusers_map(index: int, hidden: int) -> dict[str, tuple[str, tuple | None]]:
    """diffusers double-block sub-key -> (native_stem, target_slice)."""
    pf = f"transformer_blocks.{index}"
    to = f"double_blocks.{index}"
    m: dict[str, tuple[str, tuple | None]] = {
        f"{pf}.attn.to_q": (f"{to}.img_attn.qkv", (0, 0, hidden)),
        f"{pf}.attn.to_k": (f"{to}.img_attn.qkv", (0, hidden, hidden)),
        f"{pf}.attn.to_v": (f"{to}.img_attn.qkv", (0, hidden * 2, hidden)),
        f"{pf}.attn.add_q_proj": (f"{to}.txt_attn.qkv", (0, 0, hidden)),
        f"{pf}.attn.add_k_proj": (f"{to}.txt_attn.qkv", (0, hidden, hidden)),
        f"{pf}.attn.add_v_proj": (f"{to}.txt_attn.qkv", (0, hidden * 2, hidden)),
        f"{pf}.attn.to_out.0": (f"{to}.img_attn.proj", None),
        f"{pf}.attn.to_add_out": (f"{to}.txt_attn.proj", None),
        f"{pf}.norm1.linear": (f"{to}.img_mod.lin", None),
        f"{pf}.norm1_context.linear": (f"{to}.txt_mod.lin", None),
        f"{pf}.ff.net.0.proj": (f"{to}.img_mlp.0", None),
        f"{pf}.ff.net.2": (f"{to}.img_mlp.2", None),
        f"{pf}.ff_context.net.0.proj": (f"{to}.txt_mlp.0", None),
        f"{pf}.ff_context.net.2": (f"{to}.txt_mlp.2", None),
    }
    return m


def _single_block_diffusers_map(index: int, hidden: int) -> dict[str, tuple[str, tuple | None]]:
    """diffusers single-block sub-key -> (native_stem, target_slice)."""
    pf = f"single_transformer_blocks.{index}"
    to = f"single_blocks.{index}"
    return {
        f"{pf}.attn.to_q": (f"{to}.linear1", (0, 0, hidden)),
        f"{pf}.attn.to_k": (f"{to}.linear1", (0, hidden, hidden)),
        f"{pf}.attn.to_v": (f"{to}.linear1", (0, hidden * 2, hidden)),
        f"{pf}.proj_mlp": (f"{to}.linear1", (0, hidden * 3, hidden * 4)),
        f"{pf}.proj_out": (f"{to}.linear2", None),
        f"{pf}.norm.linear": (f"{to}.modulation.lin", None),
        # Flux2 fused single-block spellings:
        f"{pf}.attn.to_qkv_mlp_proj": (f"{to}.linear1", None),
        f"{pf}.attn.to_out": (f"{to}.linear2", None),
    }


_BASIC_DIFFUSERS_MAP = {
    "final_layer.linear": "proj_out",
    "img_in": "x_embedder",
    "time_in.in_layer": "time_text_embed.timestep_embedder.linear_1",
    "time_in.out_layer": "time_text_embed.timestep_embedder.linear_2",
    "txt_in": "context_embedder",
    "vector_in.in_layer": "time_text_embed.text_embedder.linear_1",
    "vector_in.out_layer": "time_text_embed.text_embedder.linear_2",
    "guidance_in.in_layer": "time_text_embed.guidance_embedder.linear_1",
    "guidance_in.out_layer": "time_text_embed.guidance_embedder.linear_2",
}


def _linear_param_stems(module: nn.Module) -> list[str]:
    """Native parameter stems (no ``.weight``) for every Linear in the module."""
    stems: list[str] = []
    for name, sub in module.named_modules():
        if isinstance(sub, nn.Linear) and name:
            stems.append(name)
    return stems


def build_flux_lora_key_map(module: nn.Module) -> dict[str, tuple[str, tuple | None]]:
    """Reverse map: LoRA key stem -> (native param name, target_slice).

    Covers comfy underscore, comfy generic, and diffusers dialects. Diffusers
    keys are only registered for the attention/mlp/basic weights that a fused
    native layout requires translating; every native Linear also gets the two
    comfy spellings for free.
    """
    key_map: dict[str, tuple[str, tuple | None]] = {}

    # comfy-style (works for every native Linear weight, no config needed).
    for stem in _linear_param_stems(module):
        param = f"{stem}.weight"
        underscored = stem.replace(".", "_")
        key_map[f"lora_unet_{underscored}"] = (param, None)   # kohya / comfy
        key_map[f"diffusion_model.{stem}"] = (param, None)    # comfy generic (prefixed)
        key_map[stem] = (param, None)                          # comfy generic (bare)

    # diffusers-style: needs depth + hidden_size from the module config.
    params = getattr(module, "params", None)
    if params is not None:
        hidden = params.hidden_size
        # diff_map: diffusers stem -> (native param, target_slice).
        diff_map: dict[str, tuple[str, tuple | None]] = {}
        for i in range(params.depth):
            diff_map.update(_double_block_diffusers_map(i, hidden))
        for i in range(params.depth_single_blocks):
            diff_map.update(_single_block_diffusers_map(i, hidden))
        for native_stem, diff_stem in _BASIC_DIFFUSERS_MAP.items():
            diff_map[diff_stem] = (f"{native_stem}", None)

        native_params = _module_param_names(module)
        for diff_stem, (native_stem, sl) in diff_map.items():
            native_param = native_stem if native_stem.endswith(".weight") else f"{native_stem}.weight"
            # Skip diffusers entries whose native target does not exist on this
            # variant (e.g. img_mod on Flux2, which uses shared modulation).
            if native_param not in native_params:
                continue
            underscored = diff_stem.replace(".", "_")
            key_map[f"transformer.{diff_stem}"] = (native_param, sl)   # diffusers / DiffSynth(prefixed)
            key_map[diff_stem] = (native_param, sl)                     # DiffSynth (bare)
            key_map[f"lora_transformer_{underscored}"] = (native_param, sl)  # OneTrainer
            key_map[f"lycoris_{underscored}"] = (native_param, sl)          # SimpleTuner lycoris

    return key_map


# Native -> diffusers module-name translation for the Krea-2 arch. Exact stems
# first, then pattern substitutions applied in order. Derived from real files
# (krea/Krea-2-LoRA-* official diffusers-trained LoRAs), every pair verified by
# tensor shape (e.g. to_k out=1536 GQA <-> wk; time_mod_proj out=36864 <-> tproj.1).
_KREA2_DIFFUSERS_EXACT = {
    "first": "img_in",
    "last.linear": "final_layer.linear",
    "tmlp.0": "time_embed.linear_1",
    "tmlp.2": "time_embed.linear_2",
    "tproj.1": "time_mod_proj",
    "txtmlp.1": "txt_in.linear_1",
    "txtmlp.3": "txt_in.linear_2",
}
_KREA2_DIFFUSERS_SUBS = [
    ("blocks.", "transformer_blocks."),   # applied only at stem start (see below)
    ("txtfusion.", "text_fusion."),
    (".attn.wq", ".attn.to_q"),
    (".attn.wk", ".attn.to_k"),
    (".attn.wv", ".attn.to_v"),
    (".attn.wo", ".attn.to_out.0"),
    (".attn.gate", ".attn.to_gate"),
    (".mlp.", ".ff."),
]


def _krea2_diffusers_stem(stem: str) -> str | None:
    """Translate a native Krea-2 Linear stem to its diffusers spelling."""
    if stem in _KREA2_DIFFUSERS_EXACT:
        return _KREA2_DIFFUSERS_EXACT[stem]
    out = stem
    for old, new in _KREA2_DIFFUSERS_SUBS:
        if old == "blocks.":
            if out.startswith("blocks."):
                out = "transformer_blocks." + out[len("blocks."):]
        else:
            out = out.replace(old, new)
    return out if out != stem else None


def build_krea2_lora_key_map(module: nn.Module) -> dict[str, tuple[str, tuple | None]]:
    """Reverse LoRA key map for the Krea-2 arch.

    Krea-2's attention is already SPLIT (``blocks.N.attn.{wq,wk,wv,wo,gate}``,
    ``mlp.{gate,up,down}``, plus the trainable ``txtfusion``/``txtmlp``/``tmlp``/
    ``tproj``/``first``/``last`` Linears), so — unlike Flux's fused ``qkv`` — there
    is no diffusers slice table: every trainable Linear maps 1:1 by name.

    Registered spellings, all verified against real files (2026-07-10):
    - kohya underscore ``lora_unet_...`` and comfy generic (bare +
      ``diffusion_model.``-prefixed) over the native names;
    - PEFT ``base_model.model.{native_stem}`` (community trainers, e.g.
      gokaygokay/Krea-2-Realism-LoRA — native names behind the PEFT wrapper);
    - diffusers ``transformer.{renamed_stem}`` and bare ``{renamed_stem}``
      (official krea/Krea-2-LoRA-* files: ``to_q/to_k/to_v/to_out.0/to_gate``,
      ``ff.``, ``transformer_blocks.``, ``text_fusion.``, ``img_in``,
      ``final_layer.linear``, ``time_embed.linear_{1,2}``, ``time_mod_proj``,
      ``txt_in.linear_{1,2}`` — see ``_KREA2_DIFFUSERS_EXACT``/``_SUBS``).
    """
    key_map: dict[str, tuple[str, tuple | None]] = {}
    for stem in _linear_param_stems(module):
        param = f"{stem}.weight"
        underscored = stem.replace(".", "_")
        key_map[f"lora_unet_{underscored}"] = (param, None)   # kohya / comfy
        key_map[f"diffusion_model.{stem}"] = (param, None)    # comfy generic (prefixed)
        key_map[stem] = (param, None)                          # comfy generic (bare)
        key_map[f"base_model.model.{stem}"] = (param, None)    # PEFT wrapper
        diffusers_stem = _krea2_diffusers_stem(stem)
        if diffusers_stem is not None:
            key_map[f"transformer.{diffusers_stem}"] = (param, None)  # diffusers (prefixed)
            key_map.setdefault(diffusers_stem, (param, None))         # diffusers (bare)
        # Non-Krea-2 archs that fall back to this map (e.g. native LTX) already
        # use diffusers-shaped param names, so `_krea2_diffusers_stem` has
        # nothing to translate and returns None for them above. Lightricks'
        # published LTX LoRAs (including IC-LoRAs) key against exactly that
        # diffusers spelling under a `transformer.` prefix, so register it
        # unconditionally. `setdefault` so a Krea-2 diffusers-renamed alias
        # for this same string (i.e. `diffusers_stem == stem`, which
        # `_krea2_diffusers_stem` never actually returns, but keep the map
        # first-write-wins regardless) is never clobbered.
        key_map.setdefault(f"transformer.{stem}", (param, None))
    return key_map


# MiniMax-H3's checkpoint fuses q/k/v into one `blocks.{i}.attn.qkv_proj`
# [3*inner, hidden] (like Flux's `qkv`), but under this module's OWN naming
# (`blocks`/`attn.qkv_proj`/`attn.out_proj`/`mlp.fc1`/`mlp.fc2`/
# `token_refiner.blocks`), not Flux's. The one published LoRA (lightx2v/
# Minimax-h3-Turbo, 624 keys, verified against the real header) ships in the
# diffusers/PEFT dialect diffusers' own transformer_minimax_h3.py trains
# against: split `attn.to_q/to_k/to_v/to_out.0`, `ff.net.0.proj`/`ff.net.2`,
# `token_refiner.refiner_blocks.{i}.*` (not `token_refiner.blocks.{i}.*`).

# Sentinel `target_slice` value (in place of a real `(dim, start, length)`
# tuple): the fc1 output ROWS must be half-swapped, not sliced. Resolved in
# `map_lora_keys` — see `_swap_swiglu_halves`'s docstring for why.
_SWIGLU_HALF_SWAP = "swiglu_half_swap"


def _minimax_h3_diffusers_map(
    index: int, inner: int, *, refiner: bool,
) -> dict[str, tuple[str, tuple | str | None]]:
    """diffusers/PEFT MiniMax-H3 LoRA sub-key -> (native_stem, target_slice).

    ``to_q``/``to_k``/``to_v`` map to ROW SLICES of the fused ``qkv_proj``
    (out axis), in q|k|v order — the order verified against ComfyUI's actual
    ``.split(dim=-1)`` consumption of these exact repack files (see
    ``arch/minimax_h3/model.py``'s module docstring). ``ff.net.2`` needs no
    slicing: diffusers' own SwiGLU down-projection is the plain
    ``[hidden, ffn]`` matrix matching this module's ``mlp.fc2`` 1:1 (the
    SwiGLU product it consumes is a single ``ffn``-wide vector indexed by
    ffn-channel, and that indexing is the same in both conventions — see
    ``_swap_swiglu_halves``'s docstring for why only ``fc1`` needs a swap).
    ``ff.net.0.proj`` -> ``mlp.fc1`` DOES need the ``_SWIGLU_HALF_SWAP``
    sentinel: diffusers' fused up-projection lays out ``[value | gate]``
    (``models/activations.py``'s ``SwiGLU.forward``: ``hidden_states, gate =
    proj(x).chunk(2, -1); return hidden_states * silu(gate)`` — value first),
    but this module's ``mlp.fc1`` (matching ComfyUI's own ``_swiglu_eager``,
    the real consumer of the checkpoint this arch loads — see
    ``arch/minimax_h3/model.py``'s ``MiniMaxH3MLP``) lays out ``[gate |
    value]`` instead. Applying a diffusers LoRA's ``up`` rows to ``fc1``
    unswapped would patch gate deltas onto value rows and vice versa.
    """
    if refiner:
        pf = f"token_refiner.refiner_blocks.{index}"
        to = f"token_refiner.blocks.{index}"
    else:
        pf = f"transformer_blocks.{index}"
        to = f"blocks.{index}"
    return {
        f"{pf}.attn.to_q": (f"{to}.attn.qkv_proj", (0, 0, inner)),
        f"{pf}.attn.to_k": (f"{to}.attn.qkv_proj", (0, inner, inner)),
        f"{pf}.attn.to_v": (f"{to}.attn.qkv_proj", (0, 2 * inner, inner)),
        f"{pf}.attn.to_out.0": (f"{to}.attn.out_proj", None),
        f"{pf}.ff.net.0.proj": (f"{to}.mlp.fc1", _SWIGLU_HALF_SWAP),
        f"{pf}.ff.net.2": (f"{to}.mlp.fc2", None),
    }


def _swap_swiglu_halves(up: torch.Tensor) -> torch.Tensor:
    """Row-swap a diffusers-dialect fc1 LoRA's ``up`` (lora_B) between its
    ``[value | gate]`` halves, producing the ``[gate | value]`` row order the
    native (Comfy-convention) ``mlp.fc1`` actually needs — see
    ``_minimax_h3_diffusers_map``'s docstring. ``down`` (lora_A) needs no
    change: it addresses fc1's INPUT axis (``hidden_size``), which the swap
    does not touch."""
    half = up.shape[0] // 2
    return torch.cat([up[half:], up[:half]], dim=0)


def build_minimax_h3_lora_key_map(module: nn.Module) -> dict[str, tuple[str, tuple | None]]:
    """Reverse LoRA key map for the MiniMax-H3 arch.

    Every native Linear gets the comfy/kohya/PEFT-wrapper spellings for free
    (no config needed, same as the other dialects); the diffusers/PEFT dialect
    (the only published H3 LoRA) additionally needs the block/refiner-block
    counts and the attention inner dim (``heads * head_dim`` — NOT
    ``hidden_size``, H3's attention inner is wider than the residual stream)
    to build the fused-qkv slice table. No AdaLN/time_embedder targets are
    registered — the real turbo LoRA carries no such keys (guidance-distilled,
    the modulation tables are frozen), and an absent target is simply never
    looked up by :func:`map_lora_keys` (which is driven by the LoRA file's own
    keys, not by any required set on the module side) — nothing to ignore
    explicitly.
    """
    key_map: dict[str, tuple[str, tuple | None]] = {}
    for stem in _linear_param_stems(module):
        param = f"{stem}.weight"
        underscored = stem.replace(".", "_")
        key_map[f"lora_unet_{underscored}"] = (param, None)   # kohya / comfy
        key_map[f"diffusion_model.{stem}"] = (param, None)    # comfy generic (prefixed)
        key_map[stem] = (param, None)                          # comfy generic (bare)
        key_map[f"base_model.model.{stem}"] = (param, None)    # PEFT wrapper

    config = getattr(module, "config", None)
    if config is not None:
        inner = config.num_attention_heads * config.attention_head_dim
        diff_map: dict[str, tuple[str, tuple | None]] = {}
        for i in range(config.num_layers):
            diff_map.update(_minimax_h3_diffusers_map(i, inner, refiner=False))
        for i in range(config.num_refiner_layers):
            diff_map.update(_minimax_h3_diffusers_map(i, inner, refiner=True))

        native_params = _module_param_names(module)
        for diff_stem, (native_stem, sl) in diff_map.items():
            native_param = f"{native_stem}.weight"
            if native_param not in native_params:
                continue
            key_map[f"transformer.{diff_stem}"] = (native_param, sl)   # diffusers (prefixed)
            key_map.setdefault(diff_stem, (native_param, sl))           # diffusers (bare)

    return key_map


def _select_key_map(module: nn.Module) -> dict[str, tuple[str, tuple | None]]:
    """Pick the per-family LoRA key map from the module's arch shape.

    Flux modules carry ``.params`` (FluxParams, with fused qkv needing a diffusers
    slice table); MiniMax-H3 carries ``.config`` with ``.video_patch_dim`` and
    ``.pruned`` (also fused qkv, own naming + refiner-block split); Krea-2
    carries ``.config`` with ``.features`` (split attention, name-1:1).
    Anything else falls back to Krea-2's plain comfy/kohya scheme.
    """
    params = getattr(module, "params", None)
    if params is not None and hasattr(params, "hidden_size"):
        return build_flux_lora_key_map(module)
    config = getattr(module, "config", None)
    if config is not None and hasattr(config, "video_patch_dim") and hasattr(config, "pruned"):
        return build_minimax_h3_lora_key_map(module)
    return build_krea2_lora_key_map(module)


def _module_param_names(module: nn.Module) -> set[str]:
    if not hasattr(module, "_lora_param_name_cache"):
        module._lora_param_name_cache = set(dict(module.named_parameters()).keys())
    return module._lora_param_name_cache


# LoRA up/down spellings: (up_suffix, down_suffix). First match wins.
_LORA_PAIRS = [
    (".lora_up.weight", ".lora_down.weight"),   # kohya / comfy
    (".lora_B.weight", ".lora_A.weight"),       # diffusers / PEFT
    (".lora.up.weight", ".lora.down.weight"),   # some diffusers exports
    (".lora_B.default.weight", ".lora_A.default.weight"),  # qwen/peft default
]


def _iter_stems(lora_sd: dict[str, torch.Tensor]):
    """Yield (stem, up_key, down_key) for every low-rank pair in ``lora_sd``."""
    for up_suf, down_suf in _LORA_PAIRS:
        for key in lora_sd:
            if key.endswith(up_suf):
                stem = key[: -len(up_suf)]
                down_key = stem + down_suf
                if down_key in lora_sd:
                    yield stem, key, down_key


# LoKr (LyCORIS Kronecker) tensor names per stem. Either side may ship direct
# (``lokr_w1``) or factorized (``lokr_w1_a`` @ ``lokr_w1_b``); ``lokr_t2`` is
# the conv-only Tucker core (unsupported here — Linear-only arches).
_LOKR_SUFFIXES = (".lokr_w1", ".lokr_w1_a", ".lokr_w1_b",
                  ".lokr_w2", ".lokr_w2_a", ".lokr_w2_b", ".lokr_t2")


def _iter_lokr_stems(lora_sd: dict[str, torch.Tensor]):
    """Yield (stem, {suffix: key}) for every LoKr group in ``lora_sd``."""
    stems: dict[str, dict[str, str]] = {}
    for key in lora_sd:
        for suf in _LOKR_SUFFIXES:
            if key.endswith(suf):
                stems.setdefault(key[: -len(suf)], {})[suf] = key
                break
    yield from stems.items()


def _combine_lokr_side(lora_sd, parts, direct_suf: str, a_suf: str, b_suf: str):
    """Return ``(factor_tensor, factor_rank | None)`` for one Kronecker side.

    Direct tensors pass through (rank None); factorized sides are combined
    ``a @ b`` in fp32 at map time (they're tiny) with rank ``b.shape[0]`` —
    mirroring ComfyUI's LoKr adapter, where ``dim`` comes from the factorized
    side and scales ``alpha/dim``.
    """
    if direct_suf in parts:
        return lora_sd[parts[direct_suf]], None
    if a_suf in parts and b_suf in parts:
        a = lora_sd[parts[a_suf]].to(torch.float32)
        b = lora_sd[parts[b_suf]].to(torch.float32)
        return a @ b, b.shape[0]
    return None, None


def map_lora_keys(
    lora_sd: dict[str, torch.Tensor],
    module: nn.Module,
) -> tuple[dict[str, list[LoraDelta]], list[str]]:
    """Map a LoRA state dict onto native param names.

    Returns ``(mapped, unmatched)`` where ``mapped`` is
    ``{native_param_name: [LoraDelta, ...]}`` and ``unmatched`` lists LoRA key
    stems whose target could not be resolved (the caller decides warn vs error).
    ``.alpha`` / ``.dora_scale`` sidecar keys are consumed silently.
    """
    key_map = _select_key_map(module)
    mapped: dict[str, list[LoraDelta]] = {}
    consumed: set[str] = set()
    unmatched: list[str] = []

    for stem, up_key, down_key in _iter_stems(lora_sd):
        consumed.add(up_key)
        consumed.add(down_key)
        target = key_map.get(stem)
        if target is None:
            unmatched.append(stem)
            continue
        param_name, target_slice = target
        up = lora_sd[up_key]
        down = lora_sd[down_key]
        if target_slice == _SWIGLU_HALF_SWAP:
            up = _swap_swiglu_halves(up)
            target_slice = None
        rank = down.shape[0]
        alpha_key = f"{stem}.alpha"
        alpha = float(lora_sd[alpha_key].item()) if alpha_key in lora_sd else float(rank)
        if alpha_key in lora_sd:
            consumed.add(alpha_key)
        mapped.setdefault(param_name, []).append(
            LoraDelta(down=down, up=up, alpha=alpha, scale=1.0, target_slice=target_slice)
        )

    # LoKr (LyCORIS Kronecker) groups: delta = (alpha/dim) * kron(w1, w2).
    for stem, parts in _iter_lokr_stems(lora_sd):
        target = key_map.get(stem)
        if target is None:
            unmatched.append(stem)
            consumed.update(parts.values())
            continue
        param_name, target_slice = target
        if target_slice is not None or ".lokr_t2" in parts:
            # LoKr into a fused-slice target, or a conv Tucker core, has no
            # reference semantics for these Linear-only arches — report it
            # rather than half-apply.
            unmatched.append(stem)
            consumed.update(parts.values())
            continue
        w1, rank1 = _combine_lokr_side(lora_sd, parts, ".lokr_w1", ".lokr_w1_a", ".lokr_w1_b")
        w2, rank2 = _combine_lokr_side(lora_sd, parts, ".lokr_w2", ".lokr_w2_a", ".lokr_w2_b")
        if w1 is None or w2 is None:
            unmatched.append(stem)
            consumed.update(parts.values())
            continue
        consumed.update(parts.values())
        alpha_key = f"{stem}.alpha"
        dim = rank1 if rank1 is not None else rank2
        if alpha_key in lora_sd and dim is not None:
            alpha_scale = float(lora_sd[alpha_key].item()) / dim
        else:
            alpha_scale = 1.0
        if alpha_key in lora_sd:
            consumed.add(alpha_key)
        mapped.setdefault(param_name, []).append(
            LoraDelta(down=w2, up=w1, alpha=alpha_scale, scale=1.0,
                      target_slice=None, kron=True)
        )

    # anything left (that looks like a lora tensor) is reported unmatched.
    for key in lora_sd:
        if key in consumed:
            continue
        if key.endswith(".alpha") or key.endswith(".dora_scale"):
            continue
        unmatched.append(key)

    logger.debug("map_lora_keys: %d params patched, %d unmatched", len(mapped), len(unmatched))
    return mapped, unmatched
