"""Model loader for the native MiniMax-H3 video+audio family.

Unlike LTX's all-in-one checkpoint, every MiniMax-H3 component ships as its
own STANDALONE file (Comfy-Org single-file repacks -- port plan S6): the DiT
(`t2va`/`fl2va` checkpoint), the Qwen3-VL-32B text encoder, the video VAE and
the audio VAE are four independent `MODELS.acquire()` calls, each estimating
its VRAM footprint straight from its own file size (no all-in-one
slice-before-estimate special-casing needed, unlike `model_loader/ltx`).

Load order matters for two reasons this pipe follows LTX's own convention
for: the DiT is acquired FIRST (its fingerprint is the one a LoRA change
busts), and the audio VAE is ALWAYS loaded -- H3 audio is inherent to the
checkpoint's own packed-sequence design (dossier "No CFG" / port plan S6),
not an opt-in `audio: true` flag the way LTX's is.

The `clip` output wraps the text encoder for `prompt_encoder` to call, over
`MiniMaxH3TextEncoder.encode_request` (`clip.py`'s module docstring has the
full contract). Unlike the other three components, the TE is NOT acquired
here at load time -- `clip` is handed a lazy `te_factory` closure instead
(see `clip.py`'s "Lazy TE acquisition"), so a `prompt_encoder`-level
conditioning-cache hit (same prompt as a prior generation) never touches the
TE, never mind reloads it from disk.

There is no latent-upscaler slot here: the standalone "upscale" mode and its
3D upsampler checkpoint moved to the ``minimax-h3-upscale`` plugin, which
acquires and loads that checkpoint itself rather than through this bundle.
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
from src.platform.runtime.native.base import NativeArchModule
from src.platform.runtime.native.arch.minimax_h3.model import MiniMaxH3Model
from src.platform.runtime.native.vae.minimax_h3_audio import MiniMaxH3AudioVAE
from src.platform.runtime.native.vae.minimax_h3_video import MiniMaxH3VideoVAE
from src.pipelines.pipes.model_loader.minimax_h3.bundle import MiniMaxH3ModelBundle
from src.pipelines.pipes.model_loader.minimax_h3.clip import MiniMaxH3ClipTextEncoder


def _assert_h3_component(label: str, model: Any, expected_cls: type, path: str) -> None:
    """Refuse a component whose file loaded as a DIFFERENT family's arch.

    The generic kinds ("vae", "latent_upscaler", "diffusion_model") route by
    state-dict detection, and the model pickers filter by model_type only --
    so e.g. an LTX VAE file selected in this preset's Video VAE picker loads
    cleanly as the LTX class and then fails far downstream (a real 5090 run
    OOM'd inside the LTX whole-clip encoder before any shape check could
    fire). Only a constructed ``NativeArchModule`` of the wrong class is
    rejected: test fakes and duck-typed stand-ins pass through untouched.
    """
    module = getattr(model, "module", None)
    if isinstance(module, NativeArchModule) and not isinstance(module, expected_cls):
        raise ValueError(
            f"model_loader/minimax_h3: the '{label}' file {Path(path).name!r} loaded as "
            f"{type(module).__name__} -- not a MiniMax-H3 {label}. The picker lists every model of the "
            f"matching type, including other families' files; select the minimax_h3 file for this slot "
            f"(or set the preset's model-tag filters to hide foreign files)."
        )


class ModelLoaderMinimaxH3Pipe(BaseModelLoaderPipe):
    name = "model_loader"
    description = "Load a native MiniMax-H3 checkpoint set (DiT + Qwen3-VL-32B TE + video VAE + audio VAE)"

    @classmethod
    def get_default_config(cls) -> Dict[str, Any]:
        return {
            "model": None,
            "text_encoder": None,
            "video_vae": None,
            "audio_vae": None,
            "loras": [],
            "device": "cuda",
            "dtype": "bfloat16",
        }

    @classmethod
    def configuration(cls) -> List[PipeConfigSpec]:
        return [
            PipeConfigSpec("model", dict, None, "MiniMax-H3 DiT checkpoint (t2va/fl2va)", required=True),
            PipeConfigSpec("text_encoder", dict, None, "Qwen3-VL-32B text encoder", required=True),
            PipeConfigSpec("video_vae", dict, None, "MiniMax-H3 video VAE", required=True),
            PipeConfigSpec("audio_vae", dict, None, "MiniMax-H3 audio VAE (always loaded -- audio is inherent)", required=True),
            PipeConfigSpec("loras", list, [], "DiT LoRAs", required=False),
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
            PipeOutputSpec("model", IOType.MODEL, "MiniMax-H3 model bundle (DiT + TE + video VAE + audio VAE)", is_array=False),
            PipeOutputSpec("text_encoder", IOType.TEXT_ENCODER, "MiniMax-H3 Qwen3-VL-32B text encoder (ClipTextEncoder ABC)", is_array=False),
        ]

    def progress_message(self) -> str:
        model_path = _path_of(self.config.get("model")) or "?"
        return f"Loading MiniMax-H3 model <<MODEL:{Path(model_path).stem}>>"

    def describe_models(self) -> List[ModelGenerationOutput]:
        out: List[ModelGenerationOutput] = []
        for key, mtype in (
            ("model", "minimax_h3_dit"),
            ("text_encoder", "minimax_h3_qwen3vl_32b"),
            ("video_vae", "minimax_h3_video_vae"),
            ("audio_vae", "minimax_h3_audio_vae"),
        ):
            cfg = self.config.get(key)
            if _path_of(cfg):
                out.append(ModelGenerationOutput(name=cfg.get("name") or Path(_path_of(cfg)).stem, type=mtype))
        for lora in _active_loras(self.config.get("loras")):
            out.append(ModelGenerationOutput(name=Path(lora["file_path"]).stem, type="lora", weight=lora["weight"]))
        return out

    def process(self, pipe_input: PipeInput, generation_outputs: callable) -> PipeOutput:
        self.validate()
        generation_outputs(ModelsGenerationOutput(models=self.describe_models()))

        model_path = _path_of(self.config.get("model"))
        te_path = _path_of(self.config.get("text_encoder"))
        video_vae_path = _path_of(self.config.get("video_vae"))
        audio_vae_path = _path_of(self.config.get("audio_vae"))
        if not (model_path and te_path and video_vae_path and audio_vae_path):
            raise ValueError(
                "model_loader/minimax_h3 requires model, text_encoder, video_vae and audio_vae file paths"
            )

        device = self.config.get("device", "cuda")
        dtype = self.config.get("dtype", "bfloat16")
        loras = _active_loras(self.config.get("loras"))
        vram_gb = self._vram_budget(pipe_input)
        loader = NativeEngineLoader(device=device, vram_gb=vram_gb)

        models = pipe_input.input.get("MODELS", None)

        def acquire(key: str, fp: str, kind: str, path: str, **kwargs: Any) -> NativeModel:
            # Every H3 component lives in its OWN standalone file (unlike
            # LTX's all-in-one checkpoint), so every acquire estimates from
            # its own file size -- no slice-before-estimate special-casing.
            estimated_vram_gb = file_size_gb(path)
            if models is not None:
                return models.acquire(
                    key=key, fingerprint=fp, loader=lambda: loader.load(path, kind, **kwargs),
                    estimated_vram_gb=estimated_vram_gb,
                )
            return loader.load(path, kind, **kwargs)

        def acquire_dit() -> NativeModel:
            lora_fp = "+".join(f"{l['file_path']}@{l['weight']}" for l in loras) or "none"
            def load():
                model = loader.load(model_path, "diffusion_model")
                self._apply_loras(model, loras)
                return model
            if models is not None:
                return models.acquire(
                    key=f"native/dit/{model_path}", fingerprint=f"{model_path}|{dtype}|{lora_fp}", loader=load,
                    estimated_vram_gb=file_size_gb(model_path),
                )
            return load()

        progress = ComponentProgress(generation_outputs, models, self.progress_message(), total=3)
        progress.advance("DiT", f"native/dit/{model_path}")
        dit_model = acquire_dit()
        progress.advance("video VAE", f"native/vae/{video_vae_path}")
        video_vae_model = acquire(f"native/vae/{video_vae_path}", f"{video_vae_path}|{dtype}", "vae", video_vae_path)
        progress.advance("audio VAE", f"native/audio_vae/{audio_vae_path}")
        audio_vae_model = acquire(
            f"native/audio_vae/{audio_vae_path}", f"{audio_vae_path}|{dtype}", "audio_vae", audio_vae_path,
        )

        # The TE is NOT acquired here -- deferred into `clip`'s own lazy
        # `te_factory` (see clip.py's module docstring "Lazy TE acquisition"):
        # eagerly acquiring it every generation, regardless of whether
        # `prompt_encoder`'s conditioning cache will even need it, is what
        # caused a real warm-run trace to pay a ~21s disk reload on a
        # SAME-prompt (cache-hit) generation that never touched the TE at
        # all. Vision-enabled load -- H3 always taps the checkpoint's vision
        # tower (fl2va keyframes go through it at encode time), so the flag
        # is folded into the fingerprint unconditionally (documented hazard,
        # text_encoders/loader.py:435-449: a text-only and a vision-enabled
        # load of the SAME path build DIFFERENT modules).
        def _acquire_te() -> Any:
            return acquire(
                f"native/te/{te_path}", f"{te_path}|{dtype}|vision=True", "text_encoder", te_path, vision=True,
            ).module

        _assert_h3_component("model", dit_model, MiniMaxH3Model, model_path)
        _assert_h3_component("video_vae", video_vae_model, MiniMaxH3VideoVAE, video_vae_path)
        _assert_h3_component("audio_vae", audio_vae_model, MiniMaxH3AudioVAE, audio_vae_path)

        bundle = MiniMaxH3ModelBundle(
            dit=dit_model, te=None, video_vae=video_vae_model, audio_vae=audio_vae_model,
            te_cache_key=f"native/te/{te_path}",
        )
        clip = MiniMaxH3ClipTextEncoder(
            _acquire_te, device=device, model_fingerprint=f"{te_path}|vision=True",
        )
        return PipeOutput(output={"model": bundle, "text_encoder": clip})

    def _vram_budget(self, pipe_input: PipeInput) -> Optional[float]:
        return _vram_budget_fn(pipe_input, self.config.get("vram_limit_gb", None), "MODEL LOADER MINIMAX-H3")

    @staticmethod
    def _apply_loras(dit_model: NativeModel, loras: List[Dict[str, Any]]) -> None:
        _apply_loras_to(dit_model, loras, "MODEL LOADER MINIMAX-H3")
