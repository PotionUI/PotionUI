from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple, Type

from src.pipelines.contracts import IOType, PipeInput, PipeInputSpec, PipeOutput
from src.pipelines.outputs import ModelGenerationOutput, ModelsGenerationOutput
from src.pipelines.pipes._shared.generation.bundle import DitTeVaeBundle
from src.pipelines.pipes._shared.generation.clip_batch import SequentialWindowClipTextEncoder
from src.pipelines.pipes._shared.generation.loader_base import BaseModelLoaderPipe
from src.pipelines.pipes._shared.generation.loader_helpers import (
    ComponentProgress,
    active_loras as _active_loras,
    apply_loras_to as _apply_loras_to,
    lora_stack_fingerprint as _lora_stack_fingerprint,
    path_of as _path_of,
    reemit_lora_application_diagnostics as _emit_lora_diagnostics,
    vram_budget as _vram_budget_fn,
)
from src.pipelines.pipes._shared.generation.loader_lifecycle import (
    NO_LORAS,
    Component,
    ComponentLifecycle,
    sync_loras as _sync_loras,
)
from src.platform.runtime.model_lifecycle.lifecycle import file_size_gb
from src.platform.runtime.native.engine import NativeEngineLoader, NativeModel


@dataclass
class LoaderContext:
    loader: NativeEngineLoader
    device: str
    dtype: str
    dit_path: str
    te_path: str
    vae_path: str
    loras: List[Dict[str, Any]]
    lora_fp: str
    windowed_loras: List[Dict[str, Any]] = field(default_factory=list)


class ThreeComponentLoaderPipe(BaseModelLoaderPipe):
    log_tag: str
    family: str
    display_name: str
    component_labels: Tuple[Tuple[str, str], ...]
    clip_cls: Type[SequentialWindowClipTextEncoder]
    bundle_cls: Type[DitTeVaeBundle] = DitTeVaeBundle
    te_suffix: str = ""
    te_vision: bool = False
    te_load_kwargs: Mapping[str, Any] = {}
    sync_loras: bool = False

    @classmethod
    def inputs(cls) -> List[PipeInputSpec]:
        return [
            PipeInputSpec("MODELS", IOType.SERVICE, False, "Model lifecycle service for per-component reuse", is_array=False),
            PipeInputSpec("GPU", IOType.SERVICE, False, "GPU manager for the VRAM budget", is_array=False),
        ]

    def progress_message(self) -> str:
        dit_path = _path_of(self.config.get("diffusion_model")) or "?"
        return f"Loading {self.display_name} model <<MODEL:{Path(dit_path).stem}>>"

    def describe_models(self) -> List[ModelGenerationOutput]:
        out: List[ModelGenerationOutput] = []
        for key, mtype in self.component_labels:
            cfg = self.config.get(key)
            if _path_of(cfg):
                out.append(ModelGenerationOutput(name=cfg.get("name") or Path(_path_of(cfg)).stem, type=mtype))
        for lora in self._loras():
            out.append(ModelGenerationOutput(name=Path(lora["file_path"]).stem, type="lora", weight=lora["weight"]))
        return out

    def _loras(self) -> List[Dict[str, Any]]:
        return _active_loras(self.config.get("loras"), log_tag=self.log_tag)

    def _partition_loras(self, loras: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        return loras, []

    def _check_paths(self, dit_path: str) -> None:
        return None

    def _te_key(self, ctx: LoaderContext) -> str:
        return f"native/te/{ctx.te_path}{self.te_suffix}"

    def _te_fingerprint(self, ctx: LoaderContext) -> str:
        fp = f"{ctx.te_path}|{ctx.dtype}{self.te_suffix}"
        if self.te_vision:
            fp = f"{fp}|vision={self._vision()}"
        return fp

    def _te_size_gb(self, ctx: LoaderContext) -> Optional[float]:
        return file_size_gb(ctx.te_path)

    def _load_te(self, ctx: LoaderContext) -> NativeModel:
        kwargs = dict(self.te_load_kwargs)
        if self.te_vision:
            kwargs["vision"] = self._vision()
        return ctx.loader.load(ctx.te_path, "text_encoder", **kwargs)

    def _load_vae(self, ctx: LoaderContext) -> NativeModel:
        return ctx.loader.load(ctx.vae_path, "vae")

    def _dit_key(self, ctx: LoaderContext) -> str:
        return f"native/dit/{ctx.dit_path}"

    def _dit_fingerprint(self, ctx: LoaderContext) -> str:
        if self.sync_loras:
            return f"{ctx.dit_path}|{ctx.dtype}"
        return f"{ctx.dit_path}|{ctx.dtype}|{ctx.lora_fp}"

    def _dit_size_gb(self, ctx: LoaderContext) -> Optional[float]:
        return file_size_gb(ctx.dit_path)

    def _dit_load_kwargs(self, ctx: LoaderContext) -> Dict[str, Any]:
        return {}

    def _load_dit(self, ctx: LoaderContext) -> NativeModel:
        model = ctx.loader.load(ctx.dit_path, "diffusion_model", **self._dit_load_kwargs(ctx))
        model._active_lora_application = self._apply_loras(model, ctx.loras)
        if self.sync_loras:
            model._active_lora_fp = ctx.lora_fp
        self._stamp_dit(model, ctx)
        return model

    def _stamp_dit(self, model: NativeModel, ctx: LoaderContext) -> None:
        return None

    def _sync_dit_extras(self, dit_model: NativeModel, ctx: LoaderContext) -> None:
        return None

    def _vision(self) -> bool:
        return bool(self.config.get("vision", False))

    def _clip_kwargs(self, dit_model: NativeModel) -> Dict[str, Any]:
        return {}

    def process(self, pipe_input: PipeInput, generation_outputs: callable) -> PipeOutput:
        self.validate()
        generation_outputs(ModelsGenerationOutput(models=self.describe_models()))

        dit_path = _path_of(self.config.get("diffusion_model"))
        te_path = _path_of(self.config.get("text_encoder"))
        vae_path = _path_of(self.config.get("vae"))
        if not (dit_path and te_path and vae_path):
            raise ValueError(f"model_loader/{self.family} requires diffusion_model, text_encoder and vae file paths")
        self._check_paths(dit_path)

        device = self.config.get("device", "cuda")
        dtype = self.config.get("dtype", "bfloat16")
        loras, windowed_loras = self._partition_loras(self._loras())
        loader = NativeEngineLoader(device=device, vram_gb=self._vram_budget(pipe_input))
        ctx = LoaderContext(
            loader=loader, device=device, dtype=dtype,
            dit_path=dit_path, te_path=te_path, vae_path=vae_path,
            loras=loras, lora_fp=_lora_stack_fingerprint(loras) or NO_LORAS,
            windowed_loras=windowed_loras,
        )

        te_key = self._te_key(ctx)
        te_fp = self._te_fingerprint(ctx)
        dit_fp = self._dit_fingerprint(ctx)

        models = pipe_input.input.get("MODELS", None)
        progress = ComponentProgress(generation_outputs, models, self.progress_message(), total=3)
        lifecycle = ComponentLifecycle(models, progress)

        te = Component("text encoder", te_key, te_fp, lambda: self._load_te(ctx), self._te_size_gb(ctx))
        vae = Component("VAE", f"native/vae/{vae_path}", f"{vae_path}|{dtype}", lambda: self._load_vae(ctx), file_size_gb(vae_path))
        dit = Component("DiT", self._dit_key(ctx), dit_fp, lambda: self._load_dit(ctx), self._dit_size_gb(ctx))

        clip_fp = f"{te_fp}|{dit_fp}"
        windowed = tuple(windowed_loras)

        if not lifecycle.caching:
            te_model = lifecycle.acquire(te)
            vae_model = lifecycle.acquire(vae)
            dit_model = lifecycle.acquire(dit)
            _emit_lora_diagnostics(dit_model, generation_outputs, self.log_tag)
            return PipeOutput(output={
                "model": self.bundle_cls(
                    dit=dit_model, te=te_model, vae=vae_model, te_cache_key=te_key, windowed_loras=windowed,
                ),
                "text_encoder": self.clip_cls(
                    te_model.module, device=device, model_fingerprint=clip_fp, **self._clip_kwargs(dit_model),
                ),
            })

        vae_model = lifecycle.acquire(vae)
        dit_model = lifecycle.acquire(dit)
        if self.sync_loras:
            self._sync_loras(dit_model, loras, ctx.lora_fp, lifecycle, dit, generation_outputs)
            self._sync_dit_extras(dit_model, ctx)
        else:
            _emit_lora_diagnostics(dit_model, generation_outputs, self.log_tag)
        bundle = self.bundle_cls(
            dit=dit_model, te=None, vae=vae_model, te_cache_key=te_key, windowed_loras=windowed,
        )
        clip = self.clip_cls(
            device=device, model_fingerprint=clip_fp,
            te_loader=lifecycle.deferred_module(te), **self._clip_kwargs(dit_model),
        )
        return PipeOutput(output={"model": bundle, "text_encoder": clip})

    def _vram_budget(self, pipe_input: PipeInput) -> Optional[float]:
        return _vram_budget_fn(pipe_input, self.config.get("vram_limit_gb", None), self.log_tag)

    @classmethod
    def _apply_loras(cls, dit_model: NativeModel, loras: List[Dict[str, Any]]):
        return _apply_loras_to(dit_model, loras, cls.log_tag)

    @classmethod
    def _sync_loras(
        cls,
        dit_model: NativeModel,
        loras: List[Dict[str, Any]],
        lora_fp: str,
        lifecycle: Optional[ComponentLifecycle] = None,
        component: Optional[Component] = None,
        generation_outputs: Optional[callable] = None,
    ) -> None:
        _sync_loras(dit_model, loras, lora_fp, cls._apply_loras,
                    lifecycle=lifecycle, component=component,
                    generation_outputs=generation_outputs, log_tag=cls.log_tag)
