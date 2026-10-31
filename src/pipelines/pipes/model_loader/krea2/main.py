"""Model loader for native Krea-2 (txt2img).

Mirrors ``model_loader/flux``: acquires each heavy component (Qwen3-VL text
encoder, Qwen-Image causal-3D VAE, Krea-2 DiT) under its OWN ``MODELS`` cache key
so a shared TE/VAE is reused across presets. The Krea-2 DiT is a mixed-dtype
bf16/f32 checkpoint — the engine's loader selects ``manual_cast`` for it via the
mixed-precision rule (verified: ``NativeEngineLoader._ops_for`` handles Krea-2).

The TE is the one exception to "acquire here": VAE and DiT are acquired
eagerly in ``process()`` as before, but the TE's ``MODELS.acquire()`` is
handed to ``Krea2ClipTextEncoder`` as a deferred ``te_loader`` thunk (see that
module) — it only runs if ``prompt_encoder`` actually needs to encode
something the prompt-embed cache doesn't already have. Acquiring it
unconditionally here meant every generation loaded/cache-hit a multi-GB TE
even when `prompt_encoder` never touched it that generation.

LoRA uses the Krea-2 dialect map (``lora/key_mapping.build_krea2_lora_key_map``,
selected by ``map_lora_keys`` from the arch class): kohya-underscore + bare-dotted
over the native split-attention names.

The DiT's ``MODELS`` fingerprint is deliberately LoRA-INDEPENDENT (path + dtype
only) — UNLIKE model_loader/flux, which busts its DiT cache key on every LoRA
change and so re-reads the whole ~24.5GB checkpoint from disk. ``_sync_loras``
below reconciles an already-cached DiT's applied LoRA stack with the requested
one on every acquire: a cache HIT with unchanged LoRAs is a no-op; a HIT with a
different stack unpatches the old and patches the new in place (via
``lora/apply.py``'s ``remove_loras``/``apply_loras``, not a disk reload); a MISS
applies once and stamps the fingerprint so the next acquire's comparison is a
no-op.

Step-windowed LoRAs (``step_start``/``step_end`` on an entry) take a different
route entirely: they are split out by ``partition_step_windows`` and passed to
``generator/krea2`` on the bundle, unapplied and absent from ``lora_fp``. The
generator's sampling loop patches them in at the window's first step and out
after its last. Baking one here would be a correctness bug, not an
optimisation — the DiT is shared through the MODELS cache, so a patch that
outlives its window silently contaminates every later generation on that entry.
"""

from __future__ import annotations

from typing import Any, Dict, List

from src.platform.runtime.native.engine import NativeModel
from src.pipelines.contracts import (
    IOType,
    PipeOutputSpec,
    PipeConfigSpec,
)
from src.pipelines.pipes._shared.generation.three_component_loader import ThreeComponentLoaderPipe
from src.pipelines.pipes._shared.generation.loader_helpers import (
    LORA_OPTION_STEP_WINDOW as _LORA_OPTION_STEP_WINDOW,
    active_loras as _active_loras,
)
from src.pipelines.pipes._shared.generation.loader_helpers import (
    partition_step_windows as _partition_step_windows,
    windowed_stack_fingerprint as _window_fingerprint,
)
from src.pipelines.pipes._shared.generation.three_component_loader import LoaderContext
from src.pipelines.pipes.model_loader.krea2.krea2_clip import Krea2ClipTextEncoder

_LOG_TAG = "MODEL LOADER KREA2"


class ModelLoaderKrea2Pipe(ThreeComponentLoaderPipe):
    name = "model_loader"
    description = "Load a native Krea-2 checkpoint set (DiT + Qwen3-VL TE + Qwen-Image VAE)"
    log_tag = _LOG_TAG
    family = "krea2"
    display_name = "Krea-2"
    component_labels = (
        ("diffusion_model", "krea2_dit"),
        ("text_encoder", "krea2_text_encoder"),
        ("vae", "krea2_vae"),
    )
    clip_cls = Krea2ClipTextEncoder
    te_vision = True
    sync_loras = True

    @classmethod
    def configuration(cls) -> List[PipeConfigSpec]:
        return [
            PipeConfigSpec("diffusion_model", dict, None, "Krea-2 DiT checkpoint (bf16)", required=True),
            PipeConfigSpec("text_encoder", dict, None, "Qwen3-VL-4B text encoder", required=True),
            PipeConfigSpec("vae", dict, None, "Qwen-Image causal-3D VAE", required=True),
            PipeConfigSpec("loras", list, [],
                           "LoRA adapters (patched in place on an already-cached DiT). An entry may add "
                           "'step_start'/'step_end' (1-based, inclusive) to be active only inside that step "
                           "range — such an entry is NOT baked into the model; the generator's sampling loop "
                           "switches it on at the window's first step and off after its last. Omit both keys "
                           "for the ordinary always-on behaviour.",
                           required=False),
            *cls.runtime_config_specs(),
            PipeConfigSpec("vision", bool, False,
                           "Load the Qwen3-VL vision tower for image-grounded instruction encoding (Krea-2 edit mode)",
                           required=False),
        ]

    @classmethod
    def outputs(cls) -> List[PipeOutputSpec]:
        return [
            PipeOutputSpec("model", IOType.MODEL, "Krea-2 model bundle (DiT + TE + VAE)", is_array=False),
            PipeOutputSpec("text_encoder", IOType.TEXT_ENCODER, "Krea-2 text encoder (ClipTextEncoder ABC)", is_array=False),
        ]

    def _loras(self) -> List[Dict[str, Any]]:
        """Active LoRA entries, step windows permitted (see ``process``)."""
        return _active_loras(self.config.get("loras"), supported_options={_LORA_OPTION_STEP_WINDOW}, log_tag=_LOG_TAG)

    @staticmethod
    def _sync_lora_windows(dit_model: NativeModel, window_fp: str) -> None:
        """Revise the shared DiT's weight identity when the step-windowed stack changes.

        Windowed entries are never patched into the cached module (see ``process``),
        so ``_sync_loras`` cannot see them — but the sampler switches them on at
        their window's first step and off after its last, which changes the weights
        the trajectory is actually computed under. A cache-HIT DiT whose windowed
        request differs from the stamped one therefore needs a fresh revision even
        though nothing about the resting module changed.
        """
        if getattr(dit_model, "_active_lora_window_fp", None) == window_fp:
            return
        dit_model.bump_weight_revision(f"lora windows -> {window_fp}")
        dit_model._active_lora_window_fp = window_fp

    def _partition_loras(self, loras):
        return _partition_step_windows(loras)

    def _stamp_dit(self, model: NativeModel, ctx: LoaderContext) -> None:
        model._active_lora_window_fp = _window_fingerprint(ctx.windowed_loras)

    def _sync_dit_extras(self, dit_model: NativeModel, ctx: LoaderContext) -> None:
        self._sync_lora_windows(dit_model, _window_fingerprint(ctx.windowed_loras))
