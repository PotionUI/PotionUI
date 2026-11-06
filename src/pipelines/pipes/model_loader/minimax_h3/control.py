from __future__ import annotations

from pathlib import Path
from typing import Optional

from src.pipelines.contracts import logger
from src.platform.runtime.native.arch.minimax_h3.control import (
    ControlPatchInfo,
    attach_control_branch,
    detect_control_patch,
)
from src.platform.runtime.native.engine import NativeModel
from src.platform.runtime.native.errors import NativeEngineUnsupportedError
from src.platform.runtime.native.io.safetensors_loader import load_torch_file


def dit_component_key(base_key: str, control_path: Optional[str]) -> str:
    return f"{base_key}+control={control_path}" if control_path else base_key


def dit_fingerprint(base_fingerprint: str, control_path: Optional[str]) -> str:
    return f"{base_fingerprint}|control={control_path}" if control_path else base_fingerprint


def attach_patch(dit_model: NativeModel, control_path: str) -> ControlPatchInfo:
    patch_sd, metadata = load_torch_file(control_path, device="cpu")
    info = detect_control_patch(patch_sd, metadata)
    if info is None:
        raise NativeEngineUnsupportedError(
            f"'{Path(control_path).name}' is not a MiniMax-H3 Fun ControlNet patch"
        )
    nbytes = attach_control_branch(dit_model.module, patch_sd, info)
    dit_model.estimated_vram_gb = (dit_model.estimated_vram_gb or 0.0) + nbytes / (1024 ** 3)
    logger.info(
        "[MODEL LOADER MINIMAX-H3] Fun ControlNet %s %s: %d block(s), %.2f GB",
        Path(control_path).name, info.kind, info.num_blocks, nbytes / (1024 ** 3),
    )
    return info
