"""Shared DiT-acquire helper for the Wan family: cache-key/fingerprint scheme
+ on-cache-miss LoRA application, extracted out of ``model_loader/wan22`` so
``generator/chain_video_wan22`` can re-acquire a DiT with a different LoRA
stack mid-chain (segment-level LoRA swap) without duplicating the logic.

Cache key is ``native/dit/{path}`` (one slot per DiT file, shared with the
model_loader pipe so a chain segment whose LoRA stack matches what's already
resident is a cache HIT, not a reload); fingerprint is
``{path}|{dtype}|{lora_fp}`` where ``lora_fp`` is the ``file@weight`` stack
joined with ``+`` (or ``"none"``) -- unchanged from the original inline
closure in ``model_loader/wan22/main.py``, so warm entries from the model
loader are reused verbatim when a chain segment's LoRA stack matches.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.platform.runtime.model_lifecycle.lifecycle import file_size_gb
from src.platform.runtime.native.engine import NativeEngineLoader, NativeModel
from src.pipelines.pipes._shared.generation.loader_helpers import (
    active_loras as _active_loras,
    apply_loras_to as _apply_loras_to,
    lora_stack_fingerprint as _lora_stack_fingerprint,
    reemit_lora_application_diagnostics as _emit_lora_diagnostics,
)


def acquire_wan_dit(
    models: Optional[Any],
    loader: NativeEngineLoader,
    path: str,
    dtype: str,
    loras: List[Dict[str, Any]],
    *,
    log_tag: str = "MODEL LOADER WAN",
    generation_outputs: Optional[Any] = None,
) -> NativeModel:
    """Acquire (load-or-reuse) a Wan DiT at ``path`` with ``loras`` applied.

    ``loras`` may be raw picker entries (``file_path``/``model`` +
    ``weight``/``strength``) or already-filtered ``active_loras()`` output --
    filtering is idempotent, so either is safe to pass. Each expert's LoRA set
    is in ITS OWN fingerprint (busts only that DiT, not TE/VAE/the other
    expert) and applied only on a cache miss. ``models`` is the
    ``ModelLifecycle``-shaped service (``.acquire(key, fingerprint,
    loader)``); pass ``None`` to always load fresh (no caching).

    ``generation_outputs``, when given, gets the per-adapter application
    evidence re-surfaced once per call (see
    ``reemit_lora_application_diagnostics``) — on a fresh load AND on a cache
    hit reusing an expert already patched with this exact stack, since this
    fingerprint folds the LoRA stack in and a cache hit never re-runs
    ``load()`` at all.
    """
    active = _active_loras(loras)
    lora_fp = _lora_stack_fingerprint(active) or "none"

    def load() -> NativeModel:
        model = loader.load(path, "diffusion_model")
        model._active_lora_application = _apply_loras_to(model, active, log_tag)  # noqa: SLF001
        return model

    if models is not None:
        dit_model = models.acquire(
            key=f"native/dit/{path}",
            fingerprint=f"{path}|{dtype}|{lora_fp}",
            loader=load,
            estimated_vram_gb=file_size_gb(path),
        )
    else:
        dit_model = load()
    _emit_lora_diagnostics(dit_model, generation_outputs, log_tag)
    return dit_model
