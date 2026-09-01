"""Model loader for native Krea-2 (txt2img).

Mirrors ``model_loader/flux``: acquires each heavy component (Qwen3-VL text
encoder, Qwen-Image causal-3D VAE, Krea-2 DiT) under its OWN ``MODELS`` cache key
so a shared TE/VAE is reused across presets. The Krea-2 DiT is a mixed-dtype
bf16/f32 checkpoint — the engine's loader selects ``manual_cast`` for it via the
mixed-precision rule (verified: ``NativeEngineLoader._ops_for`` handles Krea-2).

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

from pathlib import Path
from typing import Any, Dict, List, Optional

from src.pipelines.outputs import (
    ModelGenerationOutput,
    ModelsGenerationOutput,
)
from src.platform.runtime.model_lifecycle.lifecycle import file_size_gb
from src.platform.runtime.native.engine import NativeEngineLoader, NativeModel
from src.platform.runtime.native.lora import remove_loras as _remove_loras
from src.pipelines.contracts import (
    IOType,
    PipeInput,
    PipeInputSpec,
    PipeOutput,
    PipeOutputSpec,
    PipeConfigSpec,
)
from src.pipelines.pipes._shared.generation.loader_base import BaseModelLoaderPipe
from src.pipelines.pipes._shared.generation.loader_helpers import (
    ComponentProgress,
    active_loras as _active_loras,
    apply_loras_to as _apply_loras_to,
    partition_step_windows as _partition_step_windows,
    path_of as _path_of,
    vram_budget as _vram_budget_fn,
)
from src.pipelines.pipes.model_loader.krea2.bundle import Krea2ModelBundle
from src.pipelines.pipes.model_loader.krea2.krea2_clip import Krea2ClipTextEncoder

_LOG_TAG = "MODEL LOADER KREA2"


class ModelLoaderKrea2Pipe(BaseModelLoaderPipe):
    name = "model_loader"
    description = "Load a native Krea-2 checkpoint set (DiT + Qwen3-VL TE + Qwen-Image VAE)"

    @classmethod
    def get_default_config(cls) -> Dict[str, Any]:
        return {
            "diffusion_model": None,
            "text_encoder": None,
            "vae": None,
            "loras": [],
            "device": "cuda",
            "dtype": "bfloat16",
            "vision": False,
        }

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
            PipeConfigSpec("device", str, "cuda", "Compute device", required=False, choices=["cuda", "cpu"]),
            PipeConfigSpec("dtype", str, "bfloat16", "Compute dtype", required=False,
                           choices=["bfloat16", "float16", "float32"]),
            PipeConfigSpec("vram_limit_gb", float, None, "VRAM budget hint (backend-injected)", required=False),
            # Opt-in vision-grounded instruction encode (Krea-2 edit
            # mode only). Default False -- keeps the checkpoint's Qwen3-VL
            # vision tower stripped at load, exactly as before this flag
            # existed (a few extra GB resident once enabled, see
            # qwen3_vl_vision.py). Only the krea2-edit plugin's pipeline
            # should ever set this true; plain txt2img Krea-2 never does.
            PipeConfigSpec("vision", bool, False,
                           "Load the Qwen3-VL vision tower for image-grounded instruction encoding (Krea-2 edit mode)",
                           required=False),
        ]

    @classmethod
    def inputs(cls) -> List[PipeInputSpec]:
        return [
            PipeInputSpec("MODELS", IOType.SERVICE, False, "Model lifecycle service for per-component reuse", is_array=False),
            PipeInputSpec("GPU", IOType.SERVICE, False, "GPU manager for the VRAM budget", is_array=False),
        ]

    @classmethod
    def outputs(cls) -> List[PipeOutputSpec]:
        return [
            PipeOutputSpec("model", IOType.MODEL, "Krea-2 model bundle (DiT + TE + VAE)", is_array=False),
            PipeOutputSpec("text_encoder", IOType.TEXT_ENCODER, "Krea-2 text encoder (ClipTextEncoder ABC)", is_array=False),
        ]

    def progress_message(self) -> str:
        dit_path = _path_of(self.config.get("diffusion_model")) or "?"
        return f"Loading Krea-2 model <<MODEL:{Path(dit_path).stem}>>"

    def describe_models(self) -> List[ModelGenerationOutput]:
        out: List[ModelGenerationOutput] = []
        for key, mtype in (
            ("diffusion_model", "krea2_dit"),
            ("text_encoder", "krea2_text_encoder"),
            ("vae", "krea2_vae"),
        ):
            cfg = self.config.get(key)
            if _path_of(cfg):
                out.append(ModelGenerationOutput(name=cfg.get("name") or Path(_path_of(cfg)).stem, type=mtype))
        for lora in self._loras():
            out.append(ModelGenerationOutput(name=Path(lora["file_path"]).stem, type="lora", weight=lora["weight"]))
        return out

    def _loras(self) -> List[Dict[str, Any]]:
        """Active LoRA entries, step windows permitted (see ``process``)."""
        return _active_loras(self.config.get("loras"), step_windows=True, log_tag=_LOG_TAG)

    def process(self, pipe_input: PipeInput, generation_outputs: callable) -> PipeOutput:
        self.validate()
        generation_outputs(ModelsGenerationOutput(models=self.describe_models()))

        dit_path = _path_of(self.config.get("diffusion_model"))
        te_path = _path_of(self.config.get("text_encoder"))
        vae_path = _path_of(self.config.get("vae"))
        if not (dit_path and te_path and vae_path):
            raise ValueError("model_loader/krea2 requires diffusion_model, text_encoder and vae file paths")

        device = self.config.get("device", "cuda")
        dtype = self.config.get("dtype", "bfloat16")
        # Windowed entries are split off here and never reach the DiT: they are
        # handed to `generator/krea2` on the bundle and toggled by the sampler's
        # step hook. Baking one would patch the SHARED, MODELS-cached DiT for
        # good, leaking the LoRA into every later generation that hits the cache.
        loras, windowed_loras = _partition_step_windows(self._loras())
        vision = bool(self.config.get("vision", False))

        vram_gb = self._vram_budget(pipe_input)
        loader = NativeEngineLoader(device=device, vram_gb=vram_gb)

        # `vision` MUST be in the TE fingerprint: a text-only and a
        # vision-enabled load of the same checkpoint build DIFFERENT modules
        # (`self.model.visual` present or absent — see `load_text_encoder`'s
        # docstring) -- without this, switching a preset's edit mode on/off
        # could hand back a stale module from the MODELS cache.
        te_fp = f"{te_path}|{dtype}|vision={vision}"
        vae_fp = f"{vae_path}|{dtype}"
        # Baked entries only: a windowed LoRA is never patched into the cached
        # DiT, so including it here would stamp weights that aren't there.
        lora_fp = "+".join(f"{l['file_path']}@{l['weight']}" for l in loras) or "none"
        # LoRA-INDEPENDENT: the DiT cache identity is path+dtype only, so a
        # different LoRA stack is a cache HIT reusing the resident weights;
        # _sync_loras reconciles the applied stack in place rather than reloading.
        dit_fp = f"{dit_path}|{dtype}"

        def load_te() -> NativeModel:
            return loader.load(te_path, "text_encoder", vision=vision)

        def load_vae() -> NativeModel:
            return loader.load(vae_path, "vae")

        def load_dit() -> NativeModel:
            model = loader.load(dit_path, "diffusion_model")
            self._apply_loras(model, loras)
            model._active_lora_fp = lora_fp  # noqa: SLF001 - our own stamp, not the wrapper's private state
            return model

        models = pipe_input.input.get("MODELS", None)
        progress = ComponentProgress(generation_outputs, models, self.progress_message(), total=3)
        if models is not None:
            progress.advance("text encoder", f"native/te/{te_path}")
            te_model = models.acquire(key=f"native/te/{te_path}", fingerprint=te_fp, loader=load_te, estimated_vram_gb=file_size_gb(te_path))
            progress.advance("VAE", f"native/vae/{vae_path}")
            vae_model = models.acquire(key=f"native/vae/{vae_path}", fingerprint=vae_fp, loader=load_vae, estimated_vram_gb=file_size_gb(vae_path))
            progress.advance("DiT", f"native/dit/{dit_path}")
            dit_model = models.acquire(key=f"native/dit/{dit_path}", fingerprint=dit_fp, loader=load_dit, estimated_vram_gb=file_size_gb(dit_path))
            self._sync_loras(dit_model, loras, lora_fp)
        else:
            progress.advance("text encoder", f"native/te/{te_path}")
            progress.advance("VAE", f"native/vae/{vae_path}")
            progress.advance("DiT", f"native/dit/{dit_path}")
            te_model, vae_model, dit_model = load_te(), load_vae(), load_dit()

        bundle = Krea2ModelBundle(
            dit=dit_model, te=te_model, vae=vae_model, te_cache_key=f"native/te/{te_path}",
            windowed_loras=tuple(windowed_loras),
        )
        clip = Krea2ClipTextEncoder(te_model.module, device=device, model_fingerprint=f"{te_fp}|{dit_fp}")
        return PipeOutput(output={"model": bundle, "text_encoder": clip})

    def _vram_budget(self, pipe_input: PipeInput) -> Optional[float]:
        return _vram_budget_fn(pipe_input, self.config.get("vram_limit_gb", None), _LOG_TAG)

    @staticmethod
    def _apply_loras(dit_model: NativeModel, loras: List[Dict[str, Any]]) -> None:
        _apply_loras_to(dit_model, loras, _LOG_TAG)

    @staticmethod
    def _sync_loras(dit_model: NativeModel, loras: List[Dict[str, Any]], lora_fp: str) -> None:
        """Reconcile a (possibly cache-HIT, already-patched) DiT's applied
        LoRA stack with the requested one, in place — never re-reads the
        checkpoint (see this module's docstring).

        ``dit_model._active_lora_fp`` is our own stamp of what's currently
        patched into the weights (set here and by ``load_dit`` on a fresh
        load). Equal to the requested ``lora_fp`` -> nothing to do, the common
        "same preset, same LoRAs, next generation" case is a pure no-op. A
        mismatch means either a cache HIT with a different LoRA request (the
        add/remove-LoRA case this exists for) or a cache MISS whose loader
        already applied+stamped the correct stack, in which case the stamps
        already match and this function never reaches the branch below.
        """
        if getattr(dit_model, "_active_lora_fp", None) == lora_fp:
            return
        _remove_loras(dit_model.module)
        if loras:
            ModelLoaderKrea2Pipe._apply_loras(dit_model, loras)
        dit_model._active_lora_fp = lora_fp  # noqa: SLF001 - our own stamp, not the wrapper's private state
