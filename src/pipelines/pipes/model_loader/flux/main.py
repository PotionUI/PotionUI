"""Model loader for the native Flux family (Flux1 / Flux2 / Klein / Krea-2).

One pipe handles every Flux variant — the native engine detects the variant from
the checkpoint, so Flux1 (T5-XXL + CLIP-L) and Klein/Flux2 (Qwen3, no CLIP-L)
differ only by which text-encoder file(s) the form supplies.

Unlike the single-``acquire`` ``BaseModelLoaderPipe`` flow, this pipe acquires
each heavy component (text encoder, VAE, DiT) under its OWN ``MODELS`` cache key
(per the plan): a shared T5-XXL is reused across Flux presets.

The DiT's ``MODELS`` fingerprint is LoRA-INDEPENDENT (path + dtype only),
mirroring ``model_loader/krea2``: a LoRA-set change is a cache HIT on the
already-resident DiT, never a fingerprint bust that re-reads the checkpoint
(~24GB for Flux1) from disk. ``_sync_loras`` below reconciles an already-cached
DiT's applied LoRA stack with the requested one on every acquire, in place, via
``lora/apply.py``'s ``apply_loras``/``remove_loras`` -- which already dispatch
per-Linear between an in-place weight patch (fp32/fp16/bf16 storage) and a
runtime delta (fp8/nvfp4 storage, applied fresh each forward without ever
touching the quantized weight), so Flux's fp8-scaled DiT variants patch
correctly with no family-specific handling here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from src.pipelines.outputs import (
    ModelGenerationOutput,
    ModelsGenerationOutput,
)
from src.platform.runtime.model_lifecycle.manager import file_size_gb
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
    path_of as _path_of,
    vram_budget as _vram_budget_fn,
)
from src.pipelines.pipes.model_loader.flux.bundle import FluxModelBundle
from src.pipelines.pipes.model_loader.flux.flux_clip import FluxClipTextEncoder


class ModelLoaderFluxPipe(BaseModelLoaderPipe):
    name = "model_loader"
    description = "Load a native Flux-family checkpoint set (DiT + text encoder + VAE)"

    # -- declaration -------------------------------------------------------

    @classmethod
    def get_default_config(cls) -> Dict[str, Any]:
        return {
            "diffusion_model": None,
            "text_encoder": None,
            "clip_l": None,
            "vae": None,
            "loras": [],
            "device": "cuda",
            "dtype": "bfloat16",
        }

    @classmethod
    def configuration(cls) -> List[PipeConfigSpec]:
        return [
            PipeConfigSpec("diffusion_model", dict, None, "Flux DiT checkpoint", required=True),
            PipeConfigSpec("text_encoder", dict, None, "Text encoder (T5-XXL for Flux1, Qwen3 for Klein)", required=True),
            PipeConfigSpec("clip_l", dict, None, "CLIP-L (Flux1 only; omit for Klein/Flux2)", required=False),
            PipeConfigSpec("vae", dict, None, "Flux VAE", required=True),
            PipeConfigSpec("loras", list, [], "LoRA adapters (patched in place on an already-cached DiT)", required=False),
            PipeConfigSpec("device", str, "cuda", "Compute device", required=False, choices=["cuda", "cpu"]),
            PipeConfigSpec("dtype", str, "bfloat16", "Compute dtype", required=False,
                           choices=["bfloat16", "float16", "float32"]),
            PipeConfigSpec("vram_limit_gb", float, None, "VRAM budget hint (backend-injected)", required=False),
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
            PipeOutputSpec("model", IOType.MODEL, "Flux model bundle (DiT + TE + VAE)", is_array=False),
            PipeOutputSpec("clip", IOType.CLIP, "Flux text encoder (ClipTextEncoder ABC)", is_array=False),
        ]

    # -- BaseModelLoaderPipe hooks reused by our process() -----------------

    def progress_message(self) -> str:
        dit_path = _path_of(self.config.get("diffusion_model")) or "?"
        return f"Loading Flux model <<MODEL:{Path(dit_path).stem}>>"

    def describe_models(self) -> List[ModelGenerationOutput]:
        out: List[ModelGenerationOutput] = []
        for key, mtype in (
            ("diffusion_model", "flux_dit"),
            ("text_encoder", "flux_text_encoder"),
            ("clip_l", "flux_clip_l"),
            ("vae", "flux_vae"),
        ):
            cfg = self.config.get(key)
            if _path_of(cfg):
                out.append(ModelGenerationOutput(name=cfg.get("name") or Path(_path_of(cfg)).stem, type=mtype))
        for lora in _active_loras(self.config.get("loras")):
            out.append(ModelGenerationOutput(name=Path(lora["file_path"]).stem, type="lora", weight=lora["weight"]))
        return out

    # -- multi-component load ----------------------------------------------

    def process(self, pipe_input: PipeInput, generation_outputs: callable) -> PipeOutput:
        """Emit progress/models, then acquire TE / VAE / DiT independently.

        Overrides the single-acquire base flow because Flux caches each heavy
        component under its own key (shared TE reuse; LoRA busts only the DiT).
        """
        self.validate()
        generation_outputs(ModelsGenerationOutput(models=self.describe_models()))

        dit_path = _path_of(self.config.get("diffusion_model"))
        te_path = _path_of(self.config.get("text_encoder"))
        clip_l_path = _path_of(self.config.get("clip_l"))
        vae_path = _path_of(self.config.get("vae"))
        if not (dit_path and te_path and vae_path):
            raise ValueError("model_loader/flux requires diffusion_model, text_encoder and vae file paths")

        device = self.config.get("device", "cuda")
        dtype = self.config.get("dtype", "bfloat16")
        loras = _active_loras(self.config.get("loras"))

        vram_gb = self._vram_budget(pipe_input)
        loader = NativeEngineLoader(device=device, vram_gb=vram_gb)

        # Flux1 = composite T5-XXL + CLIP-L; Klein/Flux2 = single Qwen3.
        te_arg: Any = [te_path, clip_l_path] if clip_l_path else te_path
        te_key = f"native/te/{te_path}|{clip_l_path or ''}"
        te_fp = f"{te_key}|{dtype}"
        vae_fp = f"{vae_path}|{dtype}"
        lora_fp = "+".join(f"{l['file_path']}@{l['weight']}" for l in loras) or "none"
        # LoRA-INDEPENDENT: the DiT cache identity is path+dtype only, so a
        # different LoRA stack is a cache HIT reusing the resident weights;
        # _sync_loras reconciles the applied stack in place rather than reloading.
        dit_fp = f"{dit_path}|{dtype}"

        def load_te() -> NativeModel:
            return loader.load(te_arg, "text_encoder")

        def load_vae() -> NativeModel:
            return loader.load(vae_path, "vae")

        def load_dit() -> NativeModel:
            model = loader.load(dit_path, "diffusion_model")
            self._apply_loras(model, loras)
            model._active_lora_fp = lora_fp  # noqa: SLF001 - our own stamp, not the wrapper's private state
            return model

        te_estimate_gb = file_size_gb(te_path)
        if clip_l_path:
            clip_l_gb = file_size_gb(clip_l_path)
            if clip_l_gb is not None:
                te_estimate_gb = (te_estimate_gb or 0.0) + clip_l_gb

        models = pipe_input.input.get("MODELS", None)
        progress = ComponentProgress(generation_outputs, models, self.progress_message(), total=3)
        if models is not None:
            progress.advance("text encoder", te_key)
            te_model = models.acquire(key=te_key, fingerprint=te_fp, loader=load_te, estimated_vram_gb=te_estimate_gb)
            progress.advance("VAE", f"native/vae/{vae_path}")
            vae_model = models.acquire(key=f"native/vae/{vae_path}", fingerprint=vae_fp, loader=load_vae, estimated_vram_gb=file_size_gb(vae_path))
            progress.advance("DiT", f"native/dit/{dit_path}")
            dit_model = models.acquire(key=f"native/dit/{dit_path}", fingerprint=dit_fp, loader=load_dit, estimated_vram_gb=file_size_gb(dit_path))
            self._sync_loras(dit_model, loras, lora_fp)
        else:
            progress.advance("text encoder", te_key)
            progress.advance("VAE", f"native/vae/{vae_path}")
            progress.advance("DiT", f"native/dit/{dit_path}")
            te_model, vae_model, dit_model = load_te(), load_vae(), load_dit()

        bundle = FluxModelBundle(dit=dit_model, te=te_model, vae=vae_model, te_cache_key=te_key)
        clip = FluxClipTextEncoder(
            te_model.module, device=device, model_fingerprint=f"{te_fp}|{dit_fp}"
        )
        return PipeOutput(output={"model": bundle, "clip": clip})

    # -- helpers -----------------------------------------------------------

    def _vram_budget(self, pipe_input: PipeInput) -> Optional[float]:
        return _vram_budget_fn(pipe_input, self.config.get("vram_limit_gb", None), "MODEL LOADER FLUX")

    @staticmethod
    def _apply_loras(dit_model: NativeModel, loras: List[Dict[str, Any]]) -> None:
        _apply_loras_to(dit_model, loras, "MODEL LOADER FLUX")

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
            ModelLoaderFluxPipe._apply_loras(dit_model, loras)
        dit_model._active_lora_fp = lora_fp  # noqa: SLF001 - our own stamp, not the wrapper's private state
