"""Loader for MiniMax-Music3's fused text-encoder file.

The checkpoint fuses three things this engine keeps separate everywhere
else: the global Qwen3-8B LLM, the RVQ depth decoder, and the song's own
tokenizer (embedded as a ``tokenizer_json`` U8 tensor). None of that fits
``text_encoders/loader.py``'s contract -- that loader's job ends at
producing a ``NativeTextEncoder`` wrapping one encode-per-prompt call, and
MiniMax-Music3's AR core is consumed as a raw KV-cached decoder across
thousands of incremental steps, never as a ``.encode(prompt)`` call (port
plan S5: "text_encoders/loader.py is deliberately NOT the path"). This
module is ``model_loader/minimax_music3``'s own equivalent of
``text_encoders/loader.py``'s ``_load_one``, scoped to the one te_type it
needs to build.

Tokenizer capture follows the Gemma4 idiom verbatim (``text_encoders/
loader.py``'s ``_load_one``, ``te_type == "gemma4"`` branch): the
``tokenizer_json`` tensor's raw bytes are read BEFORE the tensor is
stripped from the state dict, because nothing else in the checkpoint (or
bundled with this engine) carries the song's own tokenizer.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from pathlib import Path

import torch

from src.platform.runtime.native.arch.minimax_music3.lm import MiniMaxMusic3AudioLM
from src.platform.runtime.native.arch.minimax_music3.prompt import MiniMaxMusic3Tokenizer
from src.platform.runtime.native.base import load_into_module
from src.platform.runtime.native.detect.te_detect import detect_te_config
from src.platform.runtime.native.engine import NativeModel
from src.platform.runtime.native.errors import NativeEngineUnsupportedError
from src.platform.runtime.native.io.safetensors_loader import load_torch_file
from src.platform.runtime.native.io.state_dict_utils import weight_dtype
from src.platform.runtime.native.ops.dtype import pick_dtypes
from vendor.gpl.comfyui.ops import detect_quant_format, pick_operations

# Sidecar tensors tolerated but never consumed in phase 1 (bf16/fp16 only --
# see the port plan's "Phase 1 vs Phase 2"); allowlisted now so turning on
# int8_convrot later is a config change here, not a load-integrity failure.
_QUANT_SIDECAR = {
    "*.weight_scale", "*.input_scale", "*.scale_weight", "*.scale_input",
    "*.weight_scale_2", "*.pre_quant_scale", "*.comfy_quant", "scaled_fp8",
}


@dataclass(frozen=True)
class _LoadAllowlist:
    """Duck-types ``ModelSpec`` for ``load_into_module``'s key-allowlist check.

    ``family``/``variant`` are read only by ``load_into_module``'s failure
    messages (meta-leftover / NaN / unexpected-key errors) -- present so a
    real load-integrity failure here still produces a readable message
    instead of an ``AttributeError`` masking it.
    """

    family: str = "minimax_music3"
    variant: str = "minimax_music3"
    expected_missing_keys: set = field(default_factory=set)
    expected_unexpected_keys: set = field(default_factory=lambda: set(_QUANT_SIDECAR))

    def key_is_expected_missing(self, key: str) -> bool:
        return any(fnmatch.fnmatch(key, pat) for pat in self.expected_missing_keys)

    def key_is_expected_unexpected(self, key: str) -> bool:
        return any(fnmatch.fnmatch(key, pat) for pat in self.expected_unexpected_keys)


def load_minimax_music3_te(path: str | Path, *, device: str | torch.device = "cuda") -> NativeModel:
    """Build + integrity-load the fused TE file.

    Returns a ``NativeModel`` whose ``.module`` is a ``MiniMaxMusic3AudioLM``
    and whose ``.tokenizer`` (a plain extra attribute, not part of the
    ``nn.Module``) is the checkpoint's own ``MiniMaxMusic3Tokenizer`` --
    constructed from the SAME load, so a fingerprint-bust reload always
    rebuilds both together. ``device`` is the eventual placement target (for
    compute-dtype selection only, matching ``NativeEngineLoader``'s own
    convention) -- the returned model itself stays on CPU, exactly like
    every other native loader here, until a caller's ``.move_to(device)``.
    """
    sd, metadata = load_torch_file(path, device="cpu")
    te_config = detect_te_config(sd)
    if te_config is None or te_config.get("te_type") != "minimax_music3":
        raise NativeEngineUnsupportedError(
            f"'{Path(path).name}' is not a recognised MiniMax-Music3 text encoder"
        )

    tok_tensor = sd.get("tokenizer_json")
    if tok_tensor is None:
        raise NativeEngineUnsupportedError(
            f"MiniMax-Music3 text encoder missing embedded 'tokenizer_json' tensor "
            f"(required -- no bundled fallback exists): {Path(path).name}"
        )
    tokenizer_json_bytes = bytes(tok_tensor.numpy().tobytes())
    tokenizer = MiniMaxMusic3Tokenizer(tokenizer_json_bytes)
    sd = {k: v for k, v in sd.items() if k != "tokenizer_json"}

    sd_dtype = weight_dtype(sd)
    quant_format = detect_quant_format(metadata, sd)
    _, compute_dtype = pick_dtypes(sd_dtype, device)
    storage_dtype = sd_dtype or compute_dtype
    ops = pick_operations(storage_dtype, compute_dtype, quant_format)

    # Empty on the meta device, same as every other multi-GB native
    # component here: load_into_module assign-loads the real tensors and
    # post_load (MiniMaxMusic3AudioLM.post_load -> recompute_inv_freq)
    # rebuilds the one computed buffer.
    with torch.device("meta"):
        module = MiniMaxMusic3AudioLM.from_config(te_config, ops)
    load_into_module(module, sd, _LoadAllowlist())

    model = NativeModel(
        "text_encoder", module,
        estimated_vram_gb=None, compute_dtype=compute_dtype, quant_format=quant_format,
    )
    model.tokenizer = tokenizer
    return model
