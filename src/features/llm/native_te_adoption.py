"""Adopting a single-file diffusion text encoder as a native chat model.

A Gemma-3 / Qwen3 language model already on disk as a ComfyUI-style single-file
safetensors (under ``models/text_encoders/`` — this app's text-encoder depot, see
``src.features.models.catalog``) can, for the tied/bf16 cases, be reused as a
chat model instead of the user downloading a second ~24GB HF-layout copy.

These comfy repacks already use HF-native ``model.*`` keys
(``model.embed_tokens.weight`` / ``model.layers.N.self_attn.q_proj.weight`` /
``model.norm.weight``) — the SAME names ``transformers`` uses for
``Qwen3ForCausalLM`` / ``Gemma3ForCausalLM`` — so there is no key remap to
invert; the language-model weights map onto the HF class 1:1. What a single file
LACKS is a ``config.json`` (reconstructed here from the weight shapes plus a
curated per-family constant table) and a tokenizer (see the tokenizer notes
below).

Eligibility (``list_adopted_te_checkpoints``) is deliberately conservative; a
detected-but-ineligible checkpoint is still listed WITH a reason so the picker
can show why it doesn't qualify:

  * Only causal-LM families are candidates (qwen3 / gemma3). T5-XXL / CLIP-L /
    UMT5 TEs are not chat models and are skipped entirely.
  * ``gemma3``'s bundled native-TE asset is a bare SentencePiece model
    (``assets/gemma3_spiece/spiece.model``) — no chat template. That gap is
    lifted: a ``tokenizer.json`` + ``tokenizer_config.json`` (which carries the
    chat template) can be fetched on demand — see ``ensure_gemma3_chat_tokenizer``
    — and cached next to the TE depot at ``text_encoders/_chat_tokenizer/gemma3/``. Until
    fetched, gemma3 stays gated with an actionable reason naming what's missing.
  * fp8-scaled repacks are detected and dequantized to bf16 at adoption time
    (``_dequantize_fp8_state_dict``) — reusing the exact per-tensor
    ``weight * weight_scale`` formula (and ``cast_to`` helper) the native
    engine's own ``Fp8ScaledLinear.forward_comfy_cast_weights`` runs per-forward
    (``vendor/gpl/comfyui/ops.py``), just applied once in bulk instead of on
    every call. fp8 no longer gates adoption by itself; a fp8 file gates only
    for the same reasons a bf16 file of the same family would.
  * mixed bf16/nvfp4 repacks (e.g. "Sikaworld"-style Gemma-3 quantizations,
    BE-13x) are detected separately from fp8 — an nvfp4 layer's packed
    ``uint8`` weight and per-block fp8-typed scale are NOT the same format as
    a plain fp8-scaled weight (see ``_detect_quantization`` and
    ``_dequantize_nvfp4_state_dict``) and dequantizing them with the fp8
    formula corrupts shapes rather than raising cleanly at adoption time.
  * ``lm_head`` gate: read from the safetensors header (no weights loaded). When
    a checkpoint omits ``lm_head.weight`` it is only adoptable if its family ties
    the embeddings (Gemma-3 always; Qwen3 small variants ≤4B), where transformers
    reconstructs the head from ``embed_tokens``. Untied + no ``lm_head`` → not
    listed as chat-capable (its logits can't be reconstructed).

The actual load (``build_adopted_te``) covers qwen3 and gemma3, bf16 or
fp8-scaled. Any other family raises a clear error rather than half-loading
(non-causal families are already gated out of the eligible listing, so this is
defence in depth).
"""

from __future__ import annotations

import json
import logging
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from src.platform.settings.repository import SettingRepository

logger = logging.getLogger(__name__)

# This app indexes ComfyUI-style text encoders under models/text_encoders/
# (model_type "text_encoder"; see
# src.platform.filesystem.model_types.DIRECTORY_TO_MODEL_TYPE). The adoption
# scan reads that same depot.
NATIVE_TE_SUBDIR = "text_encoders"

# safetensors header dtype tokens that mean the weights are fp8 (scaled). Such a
# file is dequantized to bf16 at adoption time (see `_dequantize_fp8_state_dict`)
# rather than gated out — these are detected only to decide whether to run that
# dequant pass, not to reject the checkpoint.
_FP8_DTYPES = {"F8_E4M3", "F8_E5M2", "F8_E4M3FN", "F8_E5M2FN"}

# nvfp4 (4-bit) repacks (e.g. comfy-style "Sikaworld" mixed bf16/nvfp4 Gemma-3
# TEs) carry a `<layer>.weight_scale_2` sibling (the per-tensor global scale of
# the nvfp4 double-scale format — see vendor/gpl/comfyui/ops.py's NVFP4
# section) alongside a `<layer>.weight_scale` PER-BLOCK scale stored as
# F8_E4M3. That per-block scale tensor's dtype is what makes an nvfp4 file
# ALSO match `_FP8_DTYPES` above — it is not itself fp8-quantized data, just an
# fp8-typed scale table for a 4-bit weight. `weight_scale_2` is the only marker
# unique to nvfp4 and must be checked before falling back to the legacy
# scaled-fp8 dtype-sniff (see `_detect_quantization`).
_NVFP4_SCALE2_SUFFIX = ".weight_scale_2"

# Bundled tokenizer assets that can serve a chat turn, per family. gemma3 is
# absent on purpose: only a bare spiece.model ships in-repo, with no chat
# template — its chat tokenizer is fetched on demand instead (see below).
_TE_ASSETS = (
    Path(__file__).resolve().parents[2]
    / "platform" / "runtime" / "native" / "text_encoders" / "assets"
)
_CHAT_TOKENIZER_DIRS = {"qwen3": _TE_ASSETS / "qwen3_tokenizer"}

# Qwen3 chat template (Qwen2/Qwen3 ChatML). The bundled Klein tokenizer_config
# carries a diffusion-oriented template; chat uses the standard ChatML turns so
# system/user/assistant roles render correctly.
_QWEN3_CHAT_TEMPLATE = (
    "{% for message in messages %}"
    "{{ '<|im_start|>' + message['role'] + '\n' + message['content'] + '<|im_end|>' + '\n' }}"
    "{% endfor %}"
    "{% if add_generation_prompt %}{{ '<|im_start|>assistant\n' }}{% endif %}"
)

# gemma3's chat tokenizer (a full ``tokenizer.json`` + the ``tokenizer_config.json``
# that carries its chat_template) is not something this repo can bundle — it's a
# multi-MB vocab, unlike the tiny bundled qwen3/t5/clip tokenizer assets above.
# It is instead fetched ONCE on demand, the same way the image tagger / prompt
# embedding models lazily fetch their own weights: via
# ``DownloadQueue.ensure_local_hf_repo`` (src/features/downloads/manager.py),
# the core download queue's HF-repo fetch, so the request shows up in the admin
# download history like any other model fetch. Landed next to the TE depot
# (``text_encoders/_chat_tokenizer/gemma3/``), not in-repo — see ``ensure_gemma3_chat_tokenizer``.
#
# The canonical origin, ``google/gemma-3-12b-it``, is GATED (Gemma license +
# HF token required), which sent this on-demand fetch through the same
# 401/license wall as any other gated repo. ``unsloth/gemma-3-12b-it`` is an
# UNGATED, public mirror carrying the identical vocab: its ``tokenizer.json``
# is byte-for-byte identical to google's (same LFS blob id
# ``29401f984828a18bb09a6128d437c6766785eb66`` /
# sha256 ``4667f2089529e8e7657cfb6d1c19910ae71ff5f28aa7ab2ff2763330affad795``,
# verified 2026-07-23), and its ``tokenizer_config.json`` is a complete,
# functionally-equivalent GemmaTokenizer config (same special tokens/
# tokenizer_class, chat_template present) at the same top-level path — so most
# users need no HF token at all. ``google/gemma-3-12b-it`` remains the
# documented gated fallback for anyone who wants the literal upstream repo (set
# this constant back to it; the fetch/auth path handles gated repos too, see
# ``DownloadAuthenticationException`` in ``src/features/downloads/worker.py``).
GEMMA3_CHAT_TOKENIZER_REPO = "unsloth/gemma-3-12b-it"
GEMMA3_CHAT_TOKENIZER_GATED_FALLBACK_REPO = "google/gemma-3-12b-it"
# Required for gemma3_chat_tokenizer_ready(): the minimum AutoTokenizer.from_pretrained
# needs to build a tokenizer at all.
_GEMMA3_CHAT_TOKENIZER_FILES = ("tokenizer.json", "tokenizer_config.json")
# Fetched alongside the required files when present upstream, but not required
# for "ready": current `transformers` writes/reads a chat template from a
# standalone `chat_template.jinja` file rather than an embedded
# tokenizer_config.json key (verified against the installed transformers
# version's own `save_pretrained`); older repos still embed it in
# tokenizer_config.json instead, so this is fetched best-effort, not gated on.
_GEMMA3_CHAT_TOKENIZER_OPTIONAL_FILES = ("chat_template.jinja",)
_GEMMA3_CHAT_TOKENIZER_SUBDIR = "_chat_tokenizer/gemma3"


@dataclass
class AdoptedTEEntry:
    name: str                 # "text_encoders/<file>.safetensors" — what LLMConfig.model stores
    path: str                  # absolute path, display only
    model_type: Optional[str]  # "qwen3" | "gemma3"
    tied: bool                 # embeddings tied -> lm_head reconstructable when absent
    has_lm_head: bool
    fp8: bool
    adoptable: bool            # eligible to run as a chat model
    reason: Optional[str] = None  # why not, when adoptable is False
    nvfp4: bool = False        # mixed bf16/nvfp4 repack (see `_detect_quantization`)


def _models_dir() -> Path:
    setting = SettingRepository().get_setting_by_key("models_dir")
    return Path(setting.get_typed_value() if setting else "models")


def native_te_dir(models_dir: Optional[Path] = None) -> Path:
    return (models_dir or _models_dir()) / NATIVE_TE_SUBDIR


def read_safetensors_header(path: str | Path) -> dict[str, Any]:
    """Parse a safetensors file's JSON header (keys + per-tensor dtype/shape +
    ``__metadata__``) WITHOUT reading any tensor data — the first 8 bytes are the
    little-endian header length, followed by that many bytes of JSON."""
    with open(path, "rb") as f:
        (header_len,) = struct.unpack("<Q", f.read(8))
        return json.loads(f.read(header_len))


def _family_from_keys(keys: set[str]) -> Optional[str]:
    """Which causal-LM family a comfy TE state dict belongs to, from key shape
    alone — or None for a non-causal TE (T5 / CLIP / UMT5) we don't adopt."""
    if "model.embed_tokens.weight" not in keys:
        return None
    # Gemma-3's alternating pre/post-FFN norms are unique to it among these.
    if "model.layers.0.pre_feedforward_layernorm.weight" in keys:
        return "gemma3"
    # Qwen3 has per-head q/k RMSNorm; Gemma-3 (ruled out above) also does, so this
    # check must come second.
    if "model.layers.0.self_attn.q_norm.weight" in keys:
        return "qwen3"
    return None


def _detect_quantization(keys: set[str], dtypes: set[Optional[str]]) -> tuple[bool, bool]:
    """Returns ``(fp8, nvfp4)`` for a candidate state dict's header, mutually
    exclusive: an nvfp4 repack's per-block scale tensors are themselves stored
    as F8_E4M3 (see `_NVFP4_SCALE2_SUFFIX`'s comment), which would otherwise
    trip the legacy scaled-fp8 dtype-sniff below — so the nvfp4 marker
    (a `weight_scale_2` sibling key, unique to the double-scale nvfp4 format)
    is checked FIRST and wins."""
    nvfp4 = any(k.endswith(_NVFP4_SCALE2_SUFFIX) for k in keys)
    fp8 = (not nvfp4) and (("scaled_fp8" in keys) or bool(dtypes & _FP8_DTYPES))
    return fp8, nvfp4


def _qwen3_is_tied(hidden_size: int) -> bool:
    """Qwen3 ties word embeddings on the small variants (0.6B/1.7B/4B, hidden <
    4096) and not on 8B+ — the file itself doesn't carry the flag, so infer it
    from the embedding width (the same split HF's per-model config.json encodes)."""
    return hidden_size < 4096


def gemma3_chat_tokenizer_dir(models_dir: Optional[Path] = None) -> Path:
    """Where the on-demand gemma3 chat tokenizer lands — next to the TE depot
    (``text_encoders/_chat_tokenizer/gemma3/``), not in-repo (see the module docstring
    and ``ensure_gemma3_chat_tokenizer``)."""
    return native_te_dir(models_dir) / _GEMMA3_CHAT_TOKENIZER_SUBDIR


def gemma3_chat_tokenizer_ready(models_dir: Optional[Path] = None) -> bool:
    """Whether the gemma3 chat tokenizer assets have already been fetched."""
    d = gemma3_chat_tokenizer_dir(models_dir)
    return all((d / f).is_file() for f in _GEMMA3_CHAT_TOKENIZER_FILES)


def ensure_gemma3_chat_tokenizer(
    download_queue: Any, models_dir: Optional[Path] = None, timeout: Optional[float] = None,
) -> Path:
    """Fetch ``tokenizer.json`` + ``tokenizer_config.json`` (required) plus
    ``chat_template.jinja`` (fetched if the repo has it — see the module-level
    comment on ``_GEMMA3_CHAT_TOKENIZER_OPTIONAL_FILES``) for gemma3 adoption,
    via the SAME on-demand HF-repo fetch the image tagger / prompt-embedding
    models already use to lazily fetch their own weights
    (``DownloadQueue.ensure_local_hf_repo``, src/features/downloads/manager.py)
    — the request lands in the admin download history like any other model
    fetch. Blocking; call off the event loop, matching
    ``ensure_local_hf_repo``'s own contract. ``timeout`` bounds only that
    call's own wait-for-completion poll (see its docstring); it does not
    bound HF repo-metadata enumeration, which happens before the download is
    even queued."""
    target = gemma3_chat_tokenizer_dir(models_dir)
    target.mkdir(parents=True, exist_ok=True)
    download_queue.ensure_local_hf_repo(
        GEMMA3_CHAT_TOKENIZER_REPO, str(target),
        allow_patterns=list(_GEMMA3_CHAT_TOKENIZER_FILES) + list(_GEMMA3_CHAT_TOKENIZER_OPTIONAL_FILES),
        timeout=timeout,
    )
    return target


def _inspect(path: str | Path, models_dir: Optional[Path] = None) -> Optional[AdoptedTEEntry]:
    """Header-only inspection of one candidate file -> an entry, or None when the
    file isn't a causal-LM TE (skipped, not listed)."""
    try:
        header = read_safetensors_header(path)
    except (OSError, ValueError, struct.error) as e:
        logger.debug("[NativeTEAdoption] could not read header of %s: %s", path, e)
        return None

    keys = {k for k in header if k != "__metadata__"}
    family = _family_from_keys(keys)
    if family is None:
        return None

    embed = header.get("model.embed_tokens.weight", {})
    shape = embed.get("shape") or [0, 0]
    hidden_size = int(shape[1]) if len(shape) == 2 else 0

    dtypes = {v.get("dtype") for k, v in header.items() if k != "__metadata__" and isinstance(v, dict)}
    fp8, nvfp4 = _detect_quantization(keys, dtypes)
    has_lm_head = "lm_head.weight" in keys
    tied = True if family == "gemma3" else _qwen3_is_tied(hidden_size)

    gemma3_tokenizer_ready = family == "gemma3" and gemma3_chat_tokenizer_ready(models_dir)
    adoptable, reason = _eligibility(family, has_lm_head, tied, gemma3_tokenizer_ready)
    name = f"{NATIVE_TE_SUBDIR}/{Path(path).name}"
    return AdoptedTEEntry(
        name=name, path=str(path), model_type=family, tied=tied,
        has_lm_head=has_lm_head, fp8=fp8, adoptable=adoptable, reason=reason, nvfp4=nvfp4,
    )


def _eligibility(
    family: str, has_lm_head: bool, tied: bool, gemma3_tokenizer_ready: bool = False,
) -> tuple[bool, Optional[str]]:
    """fp8 is no longer a gate here: a fp8-scaled checkpoint dequantizes
    to bf16 at adoption time (``_dequantize_fp8_state_dict``), so it is eligible
    on exactly the same terms as a bf16 file of the same family."""
    if family == "gemma3":
        if not gemma3_tokenizer_ready:
            return False, (
                "gemma3 chat tokenizer assets not downloaded yet — fetch "
                f"{' + '.join(_GEMMA3_CHAT_TOKENIZER_FILES)} from "
                f"{GEMMA3_CHAT_TOKENIZER_REPO} (via the downloads queue: "
                "POST /api/llm/native/checkpoints/gemma3-tokenizer/fetch) into "
                f"{NATIVE_TE_SUBDIR}/{_GEMMA3_CHAT_TOKENIZER_SUBDIR}/, then this "
                "checkpoint becomes chat-capable"
            )
    elif family not in _CHAT_TOKENIZER_DIRS:
        return False, f"no bundled chat tokenizer for '{family}' (a chat template is needed)"
    if not has_lm_head and not tied:
        return False, "checkpoint has no lm_head and its config is untied — output logits can't be reconstructed"
    return True, None


def list_adopted_te_checkpoints(models_dir: Optional[Path] = None) -> list[AdoptedTEEntry]:
    """Scan ``<models_dir>/text_encoders/*.safetensors`` for causal-LM text encoders that
    can be shared with the native chat provider. Non-causal TEs are
    skipped; detected-but-ineligible ones are listed with a ``reason``. The
    ``_chat_tokenizer/`` cache subdirectory (see ``gemma3_chat_tokenizer_dir``)
    holds no ``.safetensors`` files and is never itself listed as a candidate."""
    base = native_te_dir(models_dir)
    if not base.is_dir():
        return []
    entries: list[AdoptedTEEntry] = []
    for child in sorted(base.iterdir()):
        if not (child.is_file() and child.suffix == ".safetensors"):
            continue
        entry = _inspect(child, models_dir)
        if entry is not None:
            entries.append(entry)
    return entries


def resolve_adopted_te_path(name: str, models_dir: Optional[Path] = None) -> str:
    """Resolve an ``LLMConfig.model`` value of the form ``text_encoders/<file>.safetensors``
    to its absolute path, guarding against traversal outside the TE depot."""
    prefix = f"{NATIVE_TE_SUBDIR}/"
    if not name.startswith(prefix):
        raise ValueError(f"Not an adopted text-encoder reference: {name!r}")
    base = native_te_dir(models_dir).resolve()
    candidate = (base / name[len(prefix):]).resolve()
    if base not in candidate.parents:
        raise ValueError(f"Invalid adopted text-encoder name: {name!r}")
    if not candidate.is_file():
        raise ValueError(
            f"Adopted text-encoder '{name}' not found under {base} — "
            f"place a single-file safetensors checkpoint there first"
        )
    return str(candidate)


def is_adopted_te_reference(name: str) -> bool:
    return name.startswith(f"{NATIVE_TE_SUBDIR}/")


def _count_layers(sd: dict) -> int:
    n = -1
    for k in sd:
        if k.startswith("model.layers."):
            try:
                n = max(n, int(k.split(".")[2]))
            except (IndexError, ValueError):
                continue
    return n + 1


def _qwen3_config_from_state_dict(sd: dict, tied: bool):
    """Reconstruct a ``Qwen3Config`` from the weight shapes plus the curated
    non-shape constants a single file can't carry (rope theta, norm eps, max
    positions). Shapes recovered the same way the native TE loader's
    ``_build_config`` does."""
    from transformers import Qwen3Config

    embed = sd["model.embed_tokens.weight"]
    vocab_size, hidden_size = int(embed.shape[0]), int(embed.shape[1])
    head_dim = int(sd["model.layers.0.self_attn.q_norm.weight"].shape[0])
    q = sd["model.layers.0.self_attn.q_proj.weight"]
    k = sd["model.layers.0.self_attn.k_proj.weight"]
    gate = sd["model.layers.0.mlp.gate_proj.weight"]
    return Qwen3Config(
        vocab_size=vocab_size,
        hidden_size=hidden_size,
        intermediate_size=int(gate.shape[0]),
        num_hidden_layers=_count_layers(sd),
        num_attention_heads=int(q.shape[0]) // head_dim,
        num_key_value_heads=int(k.shape[0]) // head_dim,
        head_dim=head_dim,
        rope_theta=1_000_000.0,
        rms_norm_eps=1e-6,
        max_position_embeddings=40960,
        tie_word_embeddings=tied,
    )


# gemma3 head_dim is a family constant (never recovered from shapes — see
# native/text_encoders/gemma3.py's own Gemma3Config default), used both to
# recover head counts from projection shapes and as query_pre_attn_scalar
# (Gemma-3's convention: the attention pre-softmax scalar equals head_dim).
_GEMMA3_HEAD_DIM = 256

# HF's Gemma3TextConfig default rope_parameters already match the native
# module's theta pair (global/full-attention 1e6, local/sliding 1e4) — but NOT
# its long-context linear scale on the global rope (native/text_encoders/
# gemma3.py's `rope_scale_global = 8.0`, dividing inv_freq by 8 on the every-
# 6th full-attention layer). Passed explicitly so the reconstructed config
# matches the vendored, GPU-validated constants exactly rather than silently
# dropping the scale.
_GEMMA3_ROPE_PARAMETERS = {
    "full_attention": {"rope_type": "linear", "rope_theta": 1_000_000.0, "factor": 8.0},
    "sliding_attention": {"rope_type": "default", "rope_theta": 10_000.0},
}


def _gemma3_config_from_state_dict(sd: dict, tied: bool):
    """Reconstruct a ``Gemma3TextConfig`` (the LM-only half of Gemma-3, model_type
    ``gemma3_text`` — the checkpoint's SigLIP vision tower and multimodal
    projector are never present in a diffusion TE repack) from the weight shapes
    plus the curated constants ``native/text_encoders/gemma3.py`` hardcodes for
    this family: head_dim 256, rms_norm_eps 1e-6, the 5-sliding/1-global
    attention pattern on a 1024-token window, and the dual rope (global theta
    1e6 with an 8x long-context linear scale / local theta 1e4 unscaled) —
    see ``_GEMMA3_ROPE_PARAMETERS``."""
    from transformers import Gemma3TextConfig

    embed = sd["model.embed_tokens.weight"]
    vocab_size, hidden_size = int(embed.shape[0]), int(embed.shape[1])
    q = sd["model.layers.0.self_attn.q_proj.weight"]
    k = sd["model.layers.0.self_attn.k_proj.weight"]
    gate = sd["model.layers.0.mlp.gate_proj.weight"]
    return Gemma3TextConfig(
        vocab_size=vocab_size,
        hidden_size=hidden_size,
        intermediate_size=int(gate.shape[0]),
        num_hidden_layers=_count_layers(sd),
        num_attention_heads=int(q.shape[0]) // _GEMMA3_HEAD_DIM,
        num_key_value_heads=int(k.shape[0]) // _GEMMA3_HEAD_DIM,
        head_dim=_GEMMA3_HEAD_DIM,
        query_pre_attn_scalar=_GEMMA3_HEAD_DIM,
        rms_norm_eps=1e-6,
        sliding_window=1024,
        rope_parameters=dict(_GEMMA3_ROPE_PARAMETERS),
        tie_word_embeddings=tied,
    )


_FAMILY_CONFIG_BUILDERS = {
    "qwen3": _qwen3_config_from_state_dict,
    "gemma3": _gemma3_config_from_state_dict,
}

# torch dtypes an fp8-scaled repack stores its quantized weights as — the same
# two dtypes vendor/gpl/comfyui/ops.py's `_FP8_DTYPES` gates its own dequant
# path on (kept as a separate literal here since that name is private to that
# module; the *formula* below is reused, not the constant).
_FP8_TORCH_DTYPES = {"float8_e4m3fn", "float8_e5m2"}
# The two scale-key spellings a comfy fp8-scaled repack uses, matching
# Fp8ScaledLinear._load_from_state_dict's own two spellings exactly (modern
# `<layer>.weight_scale`, legacy `<layer>.scale_weight` — single scale, no input).
_FP8_SCALE_SUFFIXES = (".weight_scale", ".scale_weight")
# Sidecar keys/suffixes a scaled-fp8 repack carries that carry no weight data of
# their own once dequantized: `input_scale`/`scale_input` (captured by the
# native loader only for its own future fp8-matmul fast path, unused by the
# dequant formula), `weight_scale_2` (fp4 double-scale, not applicable to the
# e4m3/e5m2 dtypes this adoption path detects), and the bare `scaled_fp8`
# format marker.
_FP8_SIDECAR_SUFFIXES = (".input_scale", ".scale_input", ".weight_scale_2")
_FP8_SIDECAR_KEYS = {"scaled_fp8", "comfy_quant"}


def _dequantize_fp8_state_dict(sd: dict) -> dict:
    """Bulk bf16 dequant of a fp8-scaled comfy TE state dict.

    Reuses the exact per-tensor ``weight.to(compute) * weight_scale`` formula
    (and the ``cast_to`` cast helper) that the native engine's own
    ``Fp8ScaledLinear.forward_comfy_cast_weights`` (vendor/gpl/comfyui/ops.py)
    runs on every forward pass for a live fp8-scaled module — applied once here
    instead, since adoption needs a plain bf16 state dict to load into a
    ``transformers`` model, not a live cast-on-forward module. Not a
    reimplementation of that dequant math: same formula, same helper, called
    once in bulk rather than once per forward.
    """
    import torch
    from vendor.gpl.comfyui.ops import cast_to

    scales: dict[str, Any] = {}
    for key in list(sd):
        for suffix in _FP8_SCALE_SUFFIXES:
            if key.endswith(suffix):
                scales.setdefault(key[: -len(suffix)], sd.pop(key))
                break

    out: dict[str, Any] = {}
    for key, tensor in sd.items():
        if key in _FP8_SIDECAR_KEYS or any(key.endswith(s) for s in _FP8_SIDECAR_SUFFIXES):
            continue
        base = key[: -len(".weight")] if key.endswith(".weight") else None
        scale = scales.get(base) if base is not None else None
        if scale is not None:
            dequantized = cast_to(tensor, torch.bfloat16, tensor.device)
            out[key] = dequantized * cast_to(scale, torch.bfloat16, tensor.device)
        elif str(tensor.dtype).rsplit(".", 1)[-1] in _FP8_TORCH_DTYPES:
            raise ValueError(
                f"Native LLM provider: fp8 tensor '{key}' has no matching "
                f"weight_scale/scale_weight sibling — cannot dequantize"
            )
        else:
            out[key] = tensor
    return out


def _dequantize_nvfp4_state_dict(sd: dict) -> dict:
    """Bulk bf16 dequant of an nvfp4-packed comfy TE state dict (e.g. the
    "Sikaworld" mixed bf16/nvfp4 Gemma-3 repacks: outer/early and outer/late
    decoder layers stay bf16, a middle band is nvfp4-quantized).

    Reuses the vendored, GPU-validated nvfp4 dequant reference
    (``vendor.gpl.comfyui.ops.dequantize_nvfp4`` — the same LUT-gather /
    block-unswizzle math ``Nvfp4Linear.forward_comfy_cast_weights`` runs per
    forward for a live quantized module) rather than reimplementing the
    format. A layer that is NOT nvfp4-quantized in this repack has no
    ``weight_scale_2`` sibling and passes through unchanged.

    This is NOT interchangeable with ``_dequantize_fp8_state_dict``: an nvfp4
    ``.weight`` tensor is ``uint8``, packed two 4-bit codes per byte (half the
    true in-features width), with a per-16-element block scale (``.weight_scale``,
    itself stored fp8) PLUS a per-tensor global scale (``.weight_scale_2``) —
    naively doing ``weight.to(bf16) * weight_scale`` (the fp8-scaled formula)
    multiplies mismatched shapes (packed in-features vs. num-blocks, an 8x
    ratio at block size 16) and raises a broadcasting error deep in whatever
    caller triggered the lazy load — this was misdiagnosed once as a runtime
    rope/attention bug in ``generate()`` before the traceback was traced back
    to this dequant step; see the regression test naming this bug.
    """
    import torch
    from vendor.gpl.comfyui.ops import dequantize_nvfp4

    global_scales: dict[str, Any] = {}
    block_scales: dict[str, Any] = {}
    for key, tensor in sd.items():
        if key.endswith(_NVFP4_SCALE2_SUFFIX):
            global_scales[key[: -len(_NVFP4_SCALE2_SUFFIX)]] = tensor
        elif key.endswith(".weight_scale"):
            block_scales[key[: -len(".weight_scale")]] = tensor

    out: dict[str, Any] = {}
    for key, tensor in sd.items():
        if key.endswith(_NVFP4_SCALE2_SUFFIX) or key.endswith(".weight_scale") or key == "comfy_quant" or key.endswith(".comfy_quant"):
            continue
        base = key[: -len(".weight")] if key.endswith(".weight") else None
        global_scale = global_scales.get(base) if base is not None else None
        if global_scale is not None:
            block_scale = block_scales.get(base)
            if block_scale is None:
                raise ValueError(
                    f"Native LLM provider: nvfp4 tensor '{key}' has a weight_scale_2 "
                    f"(global scale) but no matching weight_scale (block scale) sibling "
                    f"— cannot dequantize"
                )
            out_features = tensor.shape[0]
            in_features = tensor.shape[1] * 2  # nvfp4 packs 2 codes per uint8 byte
            packed = tensor if tensor.dtype == torch.uint8 else tensor.view(torch.uint8)
            dequantized = dequantize_nvfp4(
                packed, block_scale, global_scale.reshape(()), out_features, in_features,
            )
            out[key] = dequantized.to(torch.bfloat16)
        else:
            out[key] = tensor
    return out


# A real LTX-2 Gemma3 single-file TE repack carries a SigLIP vision tower +
# multimodal projector (and sometimes an embedded spiece_model blob) alongside
# the LM weights this adoption path actually loads — the SAME extras the
# native TE loader strips before building its own Gemma3Model (see
# `src/platform/runtime/native/text_encoders/loader.py`'s gemma3 `TESpec`
# and its `_load_one` strip branch). `_family_from_keys` already ignores these
# (it only checks for specific LM keys' presence), so such a file is listed as
# adoptable; without this same strip here, `load_state_dict` rejects it as
# unexpected keys and adoption fails on a file the picker just said was fine.
_GEMMA3_MULTIMODAL_STRIP_PREFIXES = ("vision_model.", "multi_modal_projector.")
_GEMMA3_MULTIMODAL_STRIP_KEYS = {"spiece_model"}


def _strip_gemma3_multimodal_extras(sd: dict) -> dict:
    return {
        k: v for k, v in sd.items()
        if not k.startswith(_GEMMA3_MULTIMODAL_STRIP_PREFIXES) and k not in _GEMMA3_MULTIMODAL_STRIP_KEYS
    }


def _load_chat_tokenizer(family: str, models_dir: Optional[Path] = None):
    from transformers import AutoTokenizer

    if family == "gemma3":
        d = gemma3_chat_tokenizer_dir(models_dir)
        if not gemma3_chat_tokenizer_ready(models_dir):
            raise ValueError(
                f"Native LLM provider: gemma3 chat tokenizer assets are not present under "
                f"{d} — fetch {' + '.join(_GEMMA3_CHAT_TOKENIZER_FILES)} from "
                f"{GEMMA3_CHAT_TOKENIZER_REPO} first (see ensure_gemma3_chat_tokenizer)"
            )
        return AutoTokenizer.from_pretrained(str(d), local_files_only=True)

    tok = AutoTokenizer.from_pretrained(str(_CHAT_TOKENIZER_DIRS[family]), local_files_only=True)
    if family == "qwen3":
        tok.chat_template = _QWEN3_CHAT_TEMPLATE
    return tok


def build_adopted_te(path: str) -> tuple[Any, Any, str]:
    """Load a single-file causal-LM TE (qwen3 or gemma3; bf16, fp8-scaled, or a
    mixed bf16/nvfp4 repack) as a ``transformers`` chat model.

    Returns ``(model, tokenizer, model_type)``. Builds the HF architecture from a
    reconstructed config, dequantizes fp8-scaled or nvfp4-packed weights first
    when present (config reconstruction reads shapes AFTER this dequant, so a
    layer quantized in the source file still yields its true in-features count
    — see ``_dequantize_nvfp4_state_dict``'s docstring for why nvfp4 needs its
    own dequant path rather than reusing the fp8 one), copies the
    (identity-keyed) weights in, ties the head when the checkpoint omitted it,
    and attaches the chat tokenizer (bundled for qwen3; fetched on demand for
    gemma3 — see ``ensure_gemma3_chat_tokenizer``). Raises a clear
    ``ValueError`` for any other family — already gated out of the eligible
    listing, so this is defence in depth."""
    from safetensors.torch import load_file
    from transformers import AutoModelForCausalLM

    header = read_safetensors_header(path)
    keys = {k for k in header if k != "__metadata__"}
    family = _family_from_keys(keys)
    config_builder = _FAMILY_CONFIG_BUILDERS.get(family)
    if config_builder is None:
        raise ValueError(
            f"Native LLM provider: text-encoder adoption supports the qwen3/gemma3 "
            f"families only, not {family!r} at '{path}'"
        )
    dtypes = {v.get("dtype") for k, v in header.items() if k != "__metadata__" and isinstance(v, dict)}
    fp8, nvfp4 = _detect_quantization(keys, dtypes)

    sd = load_file(path)
    if family == "gemma3":
        sd = _strip_gemma3_multimodal_extras(sd)
    if nvfp4:
        sd = _dequantize_nvfp4_state_dict(sd)
    elif fp8:
        sd = _dequantize_fp8_state_dict(sd)

    has_lm_head = "lm_head.weight" in sd
    embed = sd["model.embed_tokens.weight"]
    tied = True if family == "gemma3" else _qwen3_is_tied(int(embed.shape[1]))
    config = config_builder(sd, tied=tied)

    model = AutoModelForCausalLM.from_config(config)
    missing, unexpected = model.load_state_dict(sd, strict=False)
    # A tied checkpoint that shipped no lm_head has exactly that one missing key,
    # which tie_weights() fills from embed_tokens; anything else is a real mismatch.
    missing = [m for m in missing if not (m == "lm_head.weight" and tied and not has_lm_head)]
    if missing or unexpected:
        raise ValueError(
            f"Native LLM provider: adopted checkpoint '{path}' did not map cleanly onto "
            f"{type(model).__name__} (missing={missing[:8]}, unexpected={list(unexpected)[:8]})"
        )
    if tied and not has_lm_head:
        model.tie_weights()
    model.eval()

    # The gemma3 chat tokenizer lives next to the TE depot for the checkpoint's
    # OWN models_dir, not a bundled in-repo asset — derive it from the
    # checkpoint's own path (`<models_dir>/text_encoders/<file>`) rather than threading a
    # models_dir parameter through every caller of build_adopted_te.
    models_dir = Path(path).resolve().parents[1] if family == "gemma3" else None
    tokenizer = _load_chat_tokenizer(family, models_dir)
    return model, tokenizer, family
