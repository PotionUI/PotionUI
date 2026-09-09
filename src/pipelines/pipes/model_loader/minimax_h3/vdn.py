"""VDN branch attach and dense-AdaLN adapter translation for the H3 loader.

Two things happen here that the plain DiT load does not do.

**Attach.** The Video-DeltaNet branch ships as its own safetensors alongside the
DiT (``linear_branch/model.safetensors``, keyed
``transformer_blocks.N.attn.<tail>``). ``attach_vdn_branch`` swaps every main
block's attention for the hybrid one; the branch weights are assign-loaded, so
the DiT's own parameters are untouched and the only new resident bytes are the
branch's own. A VDN DiT is a DIFFERENT model, not a mode of the dense one -- the
loader gives it its own cache key and folds the branch path into the DiT
fingerprint.

The branch is built with the DiT's OWN ``operations`` namespace even when that
is the quantised one: ``fp8_ops.Linear`` (``Nvfp4Linear``) registers every scale
as a NON-persistent buffer, so its ``state_dict()`` is just ``weight``/``bias``,
an unquantised bf16 tensor assign-loads into it unchanged, and its forward falls
through to the plain ``cast_bias_weight`` branch (``weight_scale is None``).
Building the branch under a second namespace would only split the module's dtype
handling in two for no gain.

**Translation.** The turbo adapter's AdaLN half is trained against the FULL
checkpoint's ``time_embedder`` output (input width 2688). A pruned checkpoint
replaces that MLP with an ``adaln_t_table`` lookup a few columns wide, so those
LoRA rows address a basis the pruned projection does not have and are dropped by
the key map. Dropping them is not a small loss: an 8-step distilled adapter
without its AdaLN half is a different model. ``lora.adaln_translate`` re-expresses
them against the table's basis, which needs the dense ``time_embedder`` weights
that the pruned checkpoint does not carry -- hence the ``dense_time_embedder``
sidecar (``scripts/h3_extract_time_embedder.py`` writes one).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import torch

from src.pipelines.contracts import logger
from src.platform.runtime.native.arch.minimax_h3.model import MiniMaxH3TimeEmbedder
from src.platform.runtime.native.arch.minimax_h3.vdn import AttachReport, attach_vdn_branch
from src.platform.runtime.native.engine import NativeModel
from src.platform.runtime.native.io.safetensors_loader import load_torch_file
from src.platform.runtime.native.lora import AdapterApplication, apply_loras_with_report
from src.pipelines.pipes._shared.generation.loader_helpers import (
    read_lora_state_dict as _read_lora_state_dict,
)
from src.platform.runtime.native.lora.adaln_translate import (
    AffineFit,
    adaln_curve_timesteps,
    apply_bias_deltas,
    dense_silu_grid,
    fit_affine_grid,
    translate_adaln_lora,
    translate_adaln_lora_state_dict,
)
# The one place that knows every low-rank pair spelling in the wild (kohya,
# diffusers, PEFT with an adapter-name infix). Re-deriving it here would drift
# from the key map that actually resolves these stems.
from src.platform.runtime.native.lora.key_mapping import _iter_stems
from vendor.gpl.comfyui.ops import pick_operations

__all__ = [
    "VdnAttachment",
    "apply_loras_with_adaln_translation",
    "attach_branch",
    "dit_component_key",
    "dit_fingerprint",
    "load_dense_time_embedder",
]

_BLOCK_ADALN_STEM = re.compile(r"^(?:transformer\.)?transformer_blocks\.(\d+)\.adaln_proj\.linear$")
_FINAL_ADALN_STEM = re.compile(r"^(?:transformer\.)?norm_out\.linear$")
_FINAL_ADALN_NATIVE = "final_layer.adaln_proj.linear"

_TIME_EMBEDDER_TENSORS = ("proj_in.weight", "proj_in.bias", "proj_out.weight", "proj_out.bias")


@dataclass(frozen=True)
class VdnAttachment:
    """What the VDN wiring did to one freshly loaded DiT.

    Either half can happen alone: a branch with no adapter needing translation,
    or a dense-dialect adapter translated onto a pruned DiT with no branch.
    ``adaln_residual`` is the relative error of the dense-curve fit that
    translation rode on -- the quality number for it.
    """

    report: Optional[AttachReport] = None
    adaln_residual: Optional[float] = None

    def describe(self) -> str:
        parts = []
        if self.report is not None:
            parts.append(
                f"VDN branch attached to {self.report.blocks} block(s): "
                f"{self.report.tensors} tensors, {self.report.bytes / (1024 ** 3):.2f} GB"
            )
        if self.adaln_residual is not None:
            parts.append(
                f"dense AdaLN adapter rows translated onto the pruned projection "
                f"(fit residual {self.adaln_residual:.4f})"
            )
        return "; ".join(parts)


def dit_component_key(model_path: str, vdn_path: Optional[str]) -> str:
    """``MODELS`` cache key for the DiT component.

    A VDN DiT carries extra modules and extra weights, so it must never share a
    cache entry with the plain load of the same file.
    """
    return f"native/dit/{model_path}+vdn" if vdn_path else f"native/dit/{model_path}"


def dit_fingerprint(
    model_path: str, dtype: str, lora_fp: str, vdn_path: Optional[str], sidecar_path: Optional[str],
) -> str:
    """Identity of the built DiT: swapping either sidecar builds a different model."""
    return (
        f"{model_path}|{dtype}|{lora_fp}"
        f"|vdn={vdn_path or 'none'}|te={sidecar_path or 'none'}"
    )


def attach_branch(dit_model: NativeModel, vdn_path: str) -> AttachReport:
    """Load the branch checkpoint and attach it, charging its bytes to the
    DiT's VRAM estimate (placement admits on that number, and the branch is
    resident alongside the weights it wraps)."""
    branch_sd, _ = load_torch_file(vdn_path, device="cpu")
    report = attach_vdn_branch(dit_model.module, branch_sd)
    dit_model.estimated_vram_gb = (dit_model.estimated_vram_gb or 0.0) + report.bytes / (1024 ** 3)
    logger.info(
        "[MODEL LOADER MINIMAX-H3] VDN branch %s: %d block(s), %d tensors, %.2f GB",
        Path(vdn_path).name, report.blocks, report.tensors, report.bytes / (1024 ** 3),
    )
    return report


def load_dense_time_embedder(path: str) -> MiniMaxH3TimeEmbedder:
    """Build the FULL checkpoint's ``time_embedder`` from a four-tensor sidecar.

    Every dimension comes from the tensors themselves rather than from the
    loaded model's config: on a pruned checkpoint that config describes the
    lookup table, not the MLP this file holds.
    """
    raw, _ = load_torch_file(path, device="cpu")
    sd = {key.removeprefix("time_embedder."): value for key, value in raw.items()}
    missing = [name for name in _TIME_EMBEDDER_TENSORS if name not in sd]
    if missing:
        raise ValueError(
            f"'{Path(path).name}' is not a MiniMax-H3 dense time-embedder sidecar: it is missing "
            f"time_embedder.{missing[0]} ({len(missing)} of {len(_TIME_EMBEDDER_TENSORS)} tensors "
            "missing). Build one with scripts/h3_extract_time_embedder.py against a FULL H3 checkpoint."
        )
    hidden_dim, freq_dim = sd["proj_in.weight"].shape
    out_dim = sd["proj_out.weight"].shape[0]
    embedder = MiniMaxH3TimeEmbedder(
        int(freq_dim), int(hidden_dim), int(out_dim),
        pick_operations(torch.float32, torch.float32), dtype=torch.float32,
    )
    embedder.load_state_dict(
        {name: sd[name].to(torch.float32) for name in _TIME_EMBEDDER_TENSORS}, strict=True,
    )
    embedder.eval()
    return embedder


def _native_adaln_stem(stem: str) -> Optional[str]:
    block = _BLOCK_ADALN_STEM.match(stem)
    if block is not None:
        return f"blocks.{block.group(1)}.adaln_proj.linear"
    return _FINAL_ADALN_NATIVE if _FINAL_ADALN_STEM.match(stem) else None


def _resolve(module: torch.nn.Module, path: str) -> Optional[torch.nn.Module]:
    current: Any = module
    for part in path.split("."):
        current = getattr(current, part, None)
        if current is None:
            return None
    return current


def _dense_adaln_stems(lora_sd: Dict[str, torch.Tensor], module: torch.nn.Module) -> List[str]:
    """AdaLN stems in ``lora_sd`` whose ``down`` width disagrees with the width
    this model's own AdaLN projection takes -- i.e. rows trained against a full
    checkpoint that would otherwise be dropped by the key map."""
    stems: List[str] = []
    for stem, _up_key, down_key in _iter_stems(lora_sd):
        native = _native_adaln_stem(stem)
        if native is None:
            continue
        linear = _resolve(module, native)
        in_features = getattr(linear, "in_features", None)
        if in_features is None:
            continue
        if int(lora_sd[down_key].shape[1]) != int(in_features):
            stems.append(stem)
    return stems


def _build_fit(module: torch.nn.Module, sidecar_path: str) -> AffineFit:
    table = getattr(module, "adaln_t_table", None)
    if table is None:
        raise ValueError(
            "the dense AdaLN translation needs a pruned DiT's adaln_t_table, and this "
            "checkpoint carries none"
        )
    grid = adaln_curve_timesteps(int(table.shape[0]))
    dense = dense_silu_grid(load_dense_time_embedder(sidecar_path), grid)
    return fit_affine_grid(dense, table.detach().to(device="cpu", dtype=torch.float32))


def _translate(
    lora_sd: Dict[str, torch.Tensor], module: torch.nn.Module, fit: AffineFit,
) -> Tuple[Dict[str, torch.Tensor], Dict[str, torch.Tensor], set]:
    """``(translated_lora_sd, bias_deltas, consumed_source_keys)``.

    ``translate_adaln_lora_state_dict`` covers the 50 block projections; the
    final layer's ``norm_out.linear`` is the same math against a different
    native name and is done here.
    """
    block_set = translate_adaln_lora_state_dict(lora_sd, module, fit)
    translated = dict(block_set.lora_sd)
    bias_deltas = dict(block_set.bias_deltas)
    consumed = set(block_set.consumed)

    if _resolve(module, _FINAL_ADALN_NATIVE) is None:
        return translated, bias_deltas, consumed

    for stem, up_key, down_key in _iter_stems(lora_sd):
        if up_key in consumed or _FINAL_ADALN_STEM.match(stem) is None:
            continue
        alpha_key = f"{stem}.alpha"
        rank = int(lora_sd[down_key].shape[0])
        alpha = float(lora_sd[alpha_key].item()) if alpha_key in lora_sd else float(rank)
        one = translate_adaln_lora(lora_sd[down_key], lora_sd[up_key], alpha, 1.0, fit)
        translated[f"{_FINAL_ADALN_NATIVE}.lora_down.weight"] = one.delta.down
        translated[f"{_FINAL_ADALN_NATIVE}.lora_up.weight"] = one.delta.up
        translated[f"{_FINAL_ADALN_NATIVE}.alpha"] = torch.tensor(alpha)
        bias_deltas[_FINAL_ADALN_NATIVE] = one.bias_delta
        consumed.update({up_key, down_key})
        if alpha_key in lora_sd:
            consumed.add(alpha_key)
    return translated, bias_deltas, consumed


def apply_loras_with_adaln_translation(
    dit_model: NativeModel,
    loras: List[Dict[str, Any]],
    log_tag: str,
    *,
    dense_time_embedder_path: Optional[str],
) -> Tuple[List[AdapterApplication], Optional[float]]:
    """Apply ``loras``, first re-expressing any dense-dialect AdaLN rows against
    this DiT's own AdaLN basis.

    Returns the per-file application evidence and the fit residual, or ``None``
    for a stack that needed no translation. A file carrying dense AdaLN rows
    with no ``dense_time_embedder`` configured raises rather than dropping them.
    """
    if not loras:
        return [], None

    module = dit_model.module
    stack: List[Tuple[Dict[str, torch.Tensor], float]] = []
    bias_deltas: List[Tuple[Dict[str, torch.Tensor], float]] = []
    fit: Optional[AffineFit] = None

    for lora in loras:
        path = lora["file_path"]
        lora_sd = _read_lora_state_dict(path)
        stems = _dense_adaln_stems(lora_sd, module)
        if stems:
            if not dense_time_embedder_path:
                raise ValueError(
                    f"[{log_tag}] {Path(path).name} carries AdaLN rows trained against the FULL "
                    f"MiniMax-H3 checkpoint ({len(stems)} target(s), e.g. {stems[0]}), but this DiT "
                    "is the pruned repack whose AdaLN projection reads a lookup table instead. "
                    "Those rows cannot be applied as they stand, and an 8-step adapter without its "
                    "AdaLN half is a different model. Set the loader's 'dense_time_embedder' to a "
                    "sidecar holding the full checkpoint's time_embedder tensors (build one with "
                    "scripts/h3_extract_time_embedder.py), or remove this adapter."
                )
            if fit is None:
                fit = _build_fit(module, dense_time_embedder_path)
            translated, biases, consumed = _translate(lora_sd, module, fit)
            lora_sd = {k: v for k, v in lora_sd.items() if k not in consumed}
            lora_sd.update(translated)
            bias_deltas.append((biases, lora["weight"]))
            logger.info(
                "[%s] %s: translated %d dense AdaLN target(s) onto the pruned projection "
                "(fit residual %.4f)", log_tag, Path(path).name, len(stems), fit.residual,
            )
        stack.append((lora_sd, lora["weight"]))

    file_paths = [lora["file_path"] for lora in loras]
    patched, unmatched, reports = apply_loras_with_report(module, stack, names=file_paths)
    for biases, strength in bias_deltas:
        apply_bias_deltas(module, biases, strength)

    if patched == 0:
        names = ", ".join(Path(path).name for path in file_paths)
        logger.warning(
            "[%s] LoRA(s) had NO effect (%s): 0 params patched, %d unmatched keys. "
            "This architecture did not recognise the LoRA's key naming; first unmatched: %s",
            log_tag, names, len(unmatched), unmatched[:5],
        )
    else:
        logger.info("[%s] applied %d LoRA(s): %d params patched, %d unmatched keys",
                    log_tag, len(loras), patched, len(unmatched))
        if unmatched:
            logger.info("[%s] first unmatched LoRA keys: %s", log_tag, unmatched[:6])
    return reports, (fit.residual if fit is not None else None)
