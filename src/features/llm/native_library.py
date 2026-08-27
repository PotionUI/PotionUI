"""Listing chat-capable HF-layout checkpoints for the native LLM provider.

The models catalog (`src.features.models.indexer.ModelScanner`) now recognizes
`models/llm/<name>/` as an HF-layout checkpoint directory (`config.json` +
sharded safetensors) and indexes each one as one catalog row, keyed by
directory name, with a config+shard-list fingerprint standing in for a content
hash (see `ModelScanner.DIRECTORY_MODEL_TYPES`). That gives these
checkpoints a stable id, tags, description and admin metadata like any other
model.

This module's own scan is deliberately kept as-is rather than rebased onto
that catalog: the LLM config UI needs allowlist validation against
`model_type`, vision-vs-text classification, and per-checkpoint quant-mode
offers, none of which the generic model row carries - reading the catalog
here would still require re-opening every `config.json` to get them, so
nothing is saved by the indirection. The two stay independent, deliberately:
this scan is what `resolve_native_checkpoint_path` and `NativeLLMClient`
resolve a config's `model` value against; the catalog row is what the general
model browser, tags and provider-info fetch operate on. Both read the same
`models/llm/` directory, so they never disagree on what exists.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from src.platform.settings.repository import SettingRepository

logger = logging.getLogger(__name__)

# Keep in sync with the family gate in `src.features.llm.clients.native`.
TEXT_MODEL_TYPES = {"qwen3", "gemma3_text"}
VISION_MODEL_TYPES = {"qwen3_vl", "qwen2_vl", "gemma3"}
SUPPORTED_MODEL_TYPES = TEXT_MODEL_TYPES | VISION_MODEL_TYPES

NATIVE_LLM_SUBDIR = "llm"

# Quantized-load modes NativeLLMClient accepts. "none" is the
# bf16/auto default; "int8"/"nf4" go through transformers' BitsAndBytesConfig
# and require a CUDA GPU plus bitsandbytes installed.
NATIVE_LLM_QUANT_MODES = ("none", "int8", "nf4")

# Resident-footprint multipliers vs. the bf16 shard sum, for the size estimate
# fed to ModelLifecycle admission control: nf4 packs weights to 4 bits
# (~0.25x) plus the double-quant absmax overhead; int8 halves them.
NATIVE_LLM_QUANT_SIZE_FACTORS = {"int8": 0.5, "nf4": 0.28}


@dataclass
class NativeCheckpointEntry:
    name: str               # directory name — this is what LLMConfig.model stores
    path: str                # absolute path, for display only
    model_type: Optional[str]
    supported: bool
    vision: bool
    reason: Optional[str] = None  # why `supported` is False, when it is
    # Quantized-load modes the picker may offer for this checkpoint —
    # empty for unsupported entries, the full set for supported ones.
    quant_modes: list = None
    # True for a single-file diffusion text encoder adopted as a chat model —
    # shared with the native TE stack, not a dedicated HF-layout dir.
    shared_te: bool = False

    def __post_init__(self):
        if self.quant_modes is None:
            self.quant_modes = []


def _models_dir() -> Path:
    setting = SettingRepository().get_setting_by_key("models_dir")
    return Path(setting.get_typed_value() if setting else "models")


def native_llm_dir(models_dir: Optional[Path] = None) -> Path:
    return (models_dir or _models_dir()) / NATIVE_LLM_SUBDIR


# HF-layout checkpoints are directories, not files: `file_size_gb()`
# (src.platform.runtime.model_lifecycle.lifecycle) stats a single path and
# would return the DIRECTORY INODE's size (a few bytes) for one of these, not
# the checkpoint's actual footprint - silently feeding a near-zero VRAM
# estimate into ModelLifecycle's admission control. Shard weight files
# sit flat in the directory (standard HF layout, no nested shard dirs).
_SHARD_EXTENSIONS = (".safetensors", ".bin", ".pt", ".pth", ".gguf")


def checkpoint_size_gb(path: str) -> Optional[float]:
    """Sum of shard weight file sizes under an HF-layout checkpoint
    directory, in GB - the size estimate `NativeLLMClient` passes to
    `ModelLifecycle.acquire(estimated_vram_gb=...)`. None if the
    directory can't be read or holds no recognized shard files."""
    try:
        total = sum(
            f.stat().st_size
            for f in Path(path).iterdir()
            if f.is_file() and f.suffix in _SHARD_EXTENSIONS
        )
    except OSError as e:
        logger.debug(f"[NativeLLMLibrary] could not size checkpoint at {path}: {e}")
        return None
    return (total / (1024 ** 3)) if total else None


def list_native_checkpoints(models_dir: Optional[Path] = None) -> list[NativeCheckpointEntry]:
    """Scan `<models_dir>/llm/*/` for HF-layout checkpoint directories.

    A candidate is any immediate subdirectory with a `config.json`. Unreadable
    or unrecognized configs are still listed (so the admin can see *why* a
    directory doesn't qualify) with `supported=False` and a `reason`.
    """
    entries: list[NativeCheckpointEntry] = []
    base = native_llm_dir(models_dir)
    for child in sorted(base.iterdir()) if base.is_dir() else []:
        if not child.is_dir():
            continue
        config_path = child / "config.json"
        if not config_path.is_file():
            continue
        model_type: Optional[str] = None
        reason: Optional[str] = None
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                model_type = json.load(f).get("model_type")
        except (OSError, json.JSONDecodeError) as e:
            reason = f"could not read config.json: {e}"
            logger.debug(f"[NativeLLMLibrary] {child}: {reason}")

        vision = model_type in VISION_MODEL_TYPES
        supported = model_type in SUPPORTED_MODEL_TYPES
        if not supported and reason is None:
            reason = (
                f"model_type '{model_type}' is not one of the supported families "
                f"({', '.join(sorted(SUPPORTED_MODEL_TYPES))})"
            )
        entries.append(NativeCheckpointEntry(
            name=child.name,
            path=str(child),
            model_type=model_type,
            supported=supported,
            vision=vision,
            reason=reason,
            quant_modes=list(NATIVE_LLM_QUANT_MODES) if supported else [],
        ))

    # Single-file text encoders already on disk that can be shared with chat,
    # mapped into the same picker shape and flagged shared_te. An
    # adoptable one can also be bnb-quantized in place — same modes
    # as an HF-directory checkpoint, since NativeLLMClient routes both through
    # the same BitsAndBytesConfig.
    from src.features.llm.native_te_adoption import list_adopted_te_checkpoints

    for te in list_adopted_te_checkpoints(models_dir):
        entries.append(NativeCheckpointEntry(
            name=te.name,
            path=te.path,
            model_type=te.model_type,
            supported=te.adoptable,
            vision=False,
            reason=te.reason,
            quant_modes=list(NATIVE_LLM_QUANT_MODES) if te.adoptable else [],
            shared_te=True,
        ))
    return entries


def resolve_native_checkpoint_path(name: str, models_dir: Optional[Path] = None) -> str:
    """Resolve an `LLMConfig.model` value (a directory name under
    `models/llm/`) to its absolute path. Raises ValueError if it doesn't
    exist or escapes the native-llm directory (path traversal guard)."""
    base = native_llm_dir(models_dir).resolve()
    candidate = (base / name).resolve()
    if base not in candidate.parents and candidate != base:
        raise ValueError(f"Invalid native LLM checkpoint name: {name!r}")
    if not candidate.is_dir():
        raise ValueError(
            f"Native LLM checkpoint '{name}' not found under {base} — "
            f"place an HF-layout checkpoint directory there first"
        )
    return str(candidate)
