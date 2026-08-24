"""Model loader for the native LTX-2 / 2.3 / 2.5 video family.

LTX-2/2.3 ship their DiT, VAE(s), vocoder and ``text_embedding_projection`` in
ONE all-in-one checkpoint (unlike every other native family, which ships
separate files per component) — see
``src/platform/runtime/native/detect/registry.py``'s LTX note. LTX-2.5
switched to a SPLIT layout instead: a transformer-only DiT file, a standalone
video-VAE file, a standalone audio-VAE(+vocoder) file, and a TE file that also
carries the ``text_embedding_projection`` (relocated off the DiT — see
``projection.py``). This pipe's `vae`/`audio_model` config slots cover both
shapes: unset, they default to slicing the component out of the all-in-one
`model` checkpoint (2.0/2.3); set, they point at the split file (2.5). This
pipe still splits acquisition by component key (DiT / TE / VAE / audio-VAE /
vocoder / the projection tensors) so the shared MODELS cache can reuse a TE or
a VAE across LTX presets, and reload only the DiT on a LoRA change.

Audio (``audio=True``): the audio VAE + vocoder are acquired from the
``audio_model`` file when configured, else from the same file as ``vae``
(``audio_vae.*`` / ``vocoder.*`` prefixes), for presets that generate sound.
Off by default (video-only), at zero extra cost when unset.

Spatial upscaler (``upscale_model``): an optional, standalone
LTX-2.3/2.5 latent-upsampler checkpoint (e.g.
``ltx-2.3-spatial-upscaler-x1.5-1.0.safetensors``), acquired only when the
preset's `upscale: off | 1.5x | 2.0x` field is not "off". ``None`` (and
uncached) otherwise, at zero extra VRAM/RAM cost -- same idiom as
``audio``/``audio_vae`` above.

Temporal upscaler (``temporal_upscale_model``): a SECOND slot of the same
shape, for the LTX-2.5 temporal x2 latent upsampler
(``ltx-2.5-latent-temporal-upscaler-x2-bf16-1.0.safetensors``). It is a
separate slot rather than a mode of ``upscale_model`` because a pipeline can
need both files at once -- one spatial pass and one temporal pass over the
same latent -- and one slot cannot hold two checkpoints.
``latent_upscaler/ltx``'s ``mode`` config picks which of the two it reads.

Duration head (``duration_head``): the optional LTX-2.5
``ltx-2.5-duration-head-bf16.safetensors``, which predicts a shot's natural
length from the prompt connector outputs. Loaded on demand like the
upscalers; nothing consults it yet (see ``bundle.py``).

Split-checkpoint guard: when ``vae``/``audio_model`` are left unset, this pipe
checks the `model` file's own header for the ``vae.``/``audio_vae.`` prefix it
would otherwise fall back to before ever handing off to the engine loader --
an LTX-2.5 transformer-only file has neither, and reading tens of GB just to
fail deep inside VAE construction is both slow and confusing. See
``_require_embedded_component``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from safetensors import safe_open

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
from src.pipelines.pipes.model_loader.ltx.bundle import LTXModelBundle
from src.pipelines.pipes.model_loader.ltx.ltx_clip import LTXClipTextEncoder
from src.pipelines.pipes.model_loader.ltx.projection import load_projection


def _has_prefixed_keys(path: str, prefix: str) -> bool:
    """Cheap header-only check: does ``path`` carry any ``prefix``-matching key?

    Reads only the safetensors header (key names, no tensor data) -- safe to
    call before deciding whether a multi-GB fallback read is even warranted.
    """
    with safe_open(path, framework="pt", device="cpu") as f:
        return any(k.startswith(prefix) for k in f.keys())


def _require_embedded_component(model_path: str, prefix: str, config_key: str, component: str) -> None:
    """Raise a crisp, actionable error when ``model_path`` carries neither the
    ``prefix``-keyed component nor a configured override.

    Only fires against a real, on-disk file (a nonexistent/test-stub path is
    left to the normal load path's own "checkpoint not found" error) -- this
    is a pre-flight guard against LTX-2.5's split-checkpoint layout, where a
    transformer-only `model` file legitimately has no embedded VAE/audio-VAE.
    """
    if not Path(model_path).is_file():
        return
    if _has_prefixed_keys(model_path, prefix):
        return
    raise ValueError(
        f"model_loader/ltx: '{Path(model_path).name}' carries no embedded {component} "
        f"(LTX-2.5 ships the DiT and {component} as separate files) -- set this pipe's "
        f"`{config_key}` config to the standalone {component} checkpoint."
    )


class ModelLoaderLtxPipe(BaseModelLoaderPipe):
    name = "model_loader"
    description = "Load a native LTX-2/2.3/2.5 checkpoint set (all-in-one or split DiT + TE + video VAE)"

    @classmethod
    def get_default_config(cls) -> Dict[str, Any]:
        return {
            "model": None,
            "text_encoder": None,
            "vae": None,
            "audio": False,
            "audio_model": None,
            "upscale_model": None,
            "temporal_upscale_model": None,
            "duration_head": None,
            "loras": [],
            "device": "cuda",
            "dtype": "bfloat16",
        }

    @classmethod
    def configuration(cls) -> List[PipeConfigSpec]:
        return [
            PipeConfigSpec("model", dict, None, "LTX DiT checkpoint (all-in-one for 2.0/2.3, transformer-only for 2.5's split layout)", required=True),
            PipeConfigSpec("text_encoder", dict, None, "Text encoder (Gemma3-12B for 2.0/2.3, Gemma4-12B-with-proj for 2.5)", required=True),
            PipeConfigSpec("vae", dict, None, "Optional VAE override; defaults to the video VAE embedded in the all-in-one checkpoint (required for LTX-2.5 split checkpoints)", required=False),
            PipeConfigSpec("audio", bool, False, "Load the audio VAE + vocoder for audio-video generation", required=False),
            PipeConfigSpec("audio_model", dict, None, "Optional separate audio-VAE + vocoder checkpoint (LTX-2.5 split layout); defaults to the audio VAE/vocoder embedded in the all-in-one `model` checkpoint", required=False),
            PipeConfigSpec("upscale_model", dict, None, "Optional LTX-2.3/2.5 spatial latent-upscaler checkpoint", required=False),
            PipeConfigSpec("temporal_upscale_model", dict, None, "Optional LTX-2.5 temporal x2 latent-upscaler checkpoint", required=False),
            PipeConfigSpec("duration_head", dict, None, "Optional LTX-2.5 duration head (predicts a shot's natural length from the prompt)", required=False),
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
            PipeOutputSpec("model", IOType.MODEL, "LTX model bundle (DiT + TE + VAE + projections)", is_array=False),
            PipeOutputSpec("text_encoder", IOType.TEXT_ENCODER, "LTX Gemma3 text encoder (ClipTextEncoder ABC)", is_array=False),
        ]

    def progress_message(self) -> str:
        model_path = _path_of(self.config.get("model")) or "?"
        return f"Loading LTX model <<MODEL:{Path(model_path).stem}>>"

    def describe_models(self) -> List[ModelGenerationOutput]:
        out: List[ModelGenerationOutput] = []
        for key, mtype in (
            ("model", "ltx_dit"),
            ("text_encoder", "ltx_gemma3"),
            ("vae", "ltx_vae"),
            ("audio_model", "ltx_audio_vae"),
            ("upscale_model", "ltx_latent_upscaler"),
            ("temporal_upscale_model", "ltx_temporal_latent_upscaler"),
            ("duration_head", "ltx_duration_head"),
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
        if not (model_path and te_path):
            raise ValueError("model_loader/ltx requires model and text_encoder file paths")

        # No separate VAE file configured -> the video VAE embedded in the
        # all-in-one checkpoint (engine._load_vae slices its `vae.*` keys).
        # An LTX-2.5 transformer-only `model` file carries neither -- catch
        # that up front with a crisp error instead of a confusing failure
        # deep inside VAE construction.
        vae_cfg_path = _path_of(self.config.get("vae"))
        vae_path = vae_cfg_path or model_path
        if vae_cfg_path is None:
            _require_embedded_component(model_path, "vae.", "vae", "video VAE")

        # Same split/all-in-one duality for the audio VAE + vocoder, gated on
        # `audio` -- an unconfigured `audio_model` falls back to slicing
        # `audio_vae.*`/`vocoder.*` out of the `model` file.
        audio_cfg_path = _path_of(self.config.get("audio_model"))
        audio_path = audio_cfg_path or model_path
        if self.config.get("audio", False) and audio_cfg_path is None:
            _require_embedded_component(model_path, "audio_vae.", "audio_model", "audio VAE")

        device = self.config.get("device", "cuda")
        dtype = self.config.get("dtype", "bfloat16")
        loras = _active_loras(self.config.get("loras"))
        vram_gb = self._vram_budget(pipe_input)
        loader = NativeEngineLoader(device=device, vram_gb=vram_gb)

        models = pipe_input.input.get("MODELS", None)
        upscale_model_path = _path_of(self.config.get("upscale_model"))
        temporal_upscale_path = _path_of(self.config.get("temporal_upscale_model"))
        duration_head_path = _path_of(self.config.get("duration_head"))
        total_components = (
            3  # dit + te + vae
            + (2 if self.config.get("audio", False) else 0)  # audio_vae + vocoder
            + (1 if upscale_model_path else 0)
            + (1 if temporal_upscale_path else 0)
            + (1 if duration_head_path else 0)
            + 1  # text_embedding_projection
        )
        progress = ComponentProgress(generation_outputs, models, self.progress_message(), total=total_components)

        def acquire(key: str, fp: str, kind: str, path: str) -> NativeModel:
            # The DiT's file-size estimate already covers the all-in-one
            # checkpoint's footprint; a VAE/audio_vae/vocoder acquired from
            # that SAME file must not re-count the whole ~40GB file on top of
            # it (admission math would 2-4x the real budget). Only estimate
            # from file size when this component lives in its own standalone
            # file; the manager records the real (much smaller) per-component
            # size after load either way.
            estimated_vram_gb = None if path == model_path else file_size_gb(path)
            if models is not None:
                return models.acquire(
                    key=key, fingerprint=fp, loader=lambda: loader.load(path, kind),
                    estimated_vram_gb=estimated_vram_gb,
                )
            return loader.load(path, kind)

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

        progress.advance("DiT", f"native/dit/{model_path}")
        dit_model = acquire_dit()
        progress.advance("text encoder", f"native/te/{te_path}")
        te_model = acquire(f"native/te/{te_path}", f"{te_path}|{dtype}", "text_encoder", te_path)
        progress.advance("VAE", f"native/vae/{vae_path}")
        vae_model = acquire(f"native/vae/{vae_path}", f"{vae_path}|{dtype}", "vae", vae_path)

        audio_vae_model: Optional[NativeModel] = None
        vocoder_model: Optional[NativeModel] = None
        if self.config.get("audio", False):
            # `audio_path` is `model_path` unless `audio_model` overrides it
            # (LTX-2.5 split layout: one standalone file carrying both
            # `audio_vae.*` and `vocoder.*` prefixes) -- see
            # engine._load_audio_vae / _load_vocoder for the slice-before-
            # estimate treatment either way.
            progress.advance("audio VAE", f"native/audio_vae/{audio_path}")
            audio_vae_model = acquire(f"native/audio_vae/{audio_path}", f"{audio_path}|{dtype}", "audio_vae", audio_path)
            progress.advance("vocoder", f"native/vocoder/{audio_path}")
            vocoder_model = acquire(f"native/vocoder/{audio_path}", f"{audio_path}|{dtype}", "vocoder", audio_path)

        upsampler_model: Optional[NativeModel] = None
        if upscale_model_path:
            # A small standalone checkpoint (not sliced from the all-in-one
            # file) -- the plain `acquire` path's file-size estimate applies
            # directly, unlike the DiT/audio_vae/vocoder special-casing above.
            progress.advance("spatial upsampler", f"native/ltx_upsampler/{upscale_model_path}")
            upsampler_model = acquire(
                f"native/ltx_upsampler/{upscale_model_path}", f"{upscale_model_path}|{dtype}",
                "latent_upscaler", upscale_model_path,
            )

        # Same shape, second slot: a pipeline may need BOTH a spatial and a
        # temporal upsampler resident (see the module docstring).
        temporal_upsampler_model: Optional[NativeModel] = None
        if temporal_upscale_path:
            progress.advance("temporal upsampler", f"native/ltx_upsampler/{temporal_upscale_path}")
            temporal_upsampler_model = acquire(
                f"native/ltx_upsampler/{temporal_upscale_path}", f"{temporal_upscale_path}|{dtype}",
                "latent_upscaler", temporal_upscale_path,
            )

        duration_head_model: Optional[NativeModel] = None
        if duration_head_path:
            progress.advance("duration head", f"native/ltx_duration_head/{duration_head_path}")
            duration_head_model = acquire(
                f"native/ltx_duration_head/{duration_head_path}", f"{duration_head_path}|{dtype}",
                "duration_head", duration_head_path,
            )

        # The text_embedding_projection tensors are small (a few MB) but still
        # cheap to cache, keyed off the DiT path + its resolved compute dtype
        # (matches the projection's own to-dtype cast) so a LoRA-only DiT
        # reload doesn't reread them. `load_projection` tries `model_path`
        # first (2.0/2.3: embedded in the all-in-one checkpoint) and falls
        # back to `te_path` (2.5: relocated onto the Gemma4 TE file) -- the
        # fingerprint includes `te_path` too so a TE swap on a 2.5 preset
        # correctly invalidates the cached projection.
        proj_dtype = dit_model.compute_dtype

        def load_proj():
            return load_projection(model_path, "cpu", proj_dtype, te_path=te_path)

        progress.advance("text embedding projection", f"native/ltx_proj/{model_path}")
        if models is not None:
            projections = models.acquire(
                key=f"native/ltx_proj/{model_path}", fingerprint=f"{model_path}|{te_path}|{proj_dtype}", loader=load_proj,
            )
        else:
            projections = load_proj()

        bundle = LTXModelBundle(
            dit=dit_model, te=te_model, vae=vae_model, projections=projections,
            audio_vae=audio_vae_model, vocoder=vocoder_model, upsampler=upsampler_model,
            temporal_upsampler=temporal_upsampler_model, duration_head=duration_head_model,
            te_cache_key=f"native/te/{te_path}",
        )
        clip = LTXClipTextEncoder(
            te_model.module, dit_model.module, projections, device=device,
            model_fingerprint=f"{te_path}|{model_path}",
        )
        return PipeOutput(output={"model": bundle, "text_encoder": clip})

    def _vram_budget(self, pipe_input: PipeInput) -> Optional[float]:
        return _vram_budget_fn(pipe_input, self.config.get("vram_limit_gb", None), "MODEL LOADER LTX")

    @staticmethod
    def _apply_loras(dit_model: NativeModel, loras: List[Dict[str, Any]]) -> None:
        _apply_loras_to(dit_model, loras, "MODEL LOADER LTX")
