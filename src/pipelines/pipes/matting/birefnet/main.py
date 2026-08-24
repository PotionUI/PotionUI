"""Background removal via the vendored BiRefNet matting model, producing an
RGBA cutout.

The model (`BackgroundMattingModel`, `src/platform/runtime/native/matting.py`)
returns a RAW sigmoid probability alpha with no threshold of its own - left
alone, a pixel the model is unsure about lands at a wishy-washy mid-gray
value instead of committing to transparent or opaque, which reads as
background residue (or, in the worst case, "removed nothing": a mostly-opaque
alpha over the whole frame, since nothing here is a hard cut). `matte_strength`
is the levels/smoothstep (`_shared.imaging.alpha.apply_matte_strength`) that
commits those pixels one way or the other.

Loading goes through the `MODELS` built-in service (`ModelLifecycleManager`)
when the pipeline wires it in, exactly like every other native loader pipe -
`acquire()` caches the loaded checkpoint by (key, fingerprint) and the caller
only ever moves the ALREADY-loaded model on/off the compute device. Without a
`MODELS` service (e.g. a bare unit test) the pipe loads a fresh instance
per-call instead of caching - correct but slow, never used in a real
pipeline. Either way the model is moved back to CPU after use so this
lightweight utility step never pins VRAM between generations.

Config `model.file_path` is used AS-IS, exactly like `interpolator/rife`'s
`_resolve_model_path` - never joined onto a models directory. A model-picker
row's `file_path` is already relative to the process's own working directory
and already carries the models dir's own name as its first path component
(see `content/plugins/marketplace/spritesheet/backend/imaging/matting.py::resolve_checkpoint`);
joining it onto another base double-prefixes and 404s on a real checkpoint.
"""

from typing import Any, Dict, List

import numpy as np
from PIL import Image

from src.pipelines.contracts import (
    BasePipe,
    IOType,
    PipeConfigSpec,
    PipeInput,
    PipeInputSpec,
    PipeOutput,
    PipeOutputSpec,
)
from src.pipelines.outputs import Icon, ImageGenerationOutput, ProgressGenerationOutput
from src.pipelines.pipes._shared.imaging.alpha import apply_matte_strength, feather_alpha
from src.pipelines.pipes._shared.imaging.io import as_image_list
from src.platform.runtime.native.matting import BackgroundMattingModel


class MattingBirefnetPipe(BasePipe):
    name = "matting/birefnet"
    description = "Background removal via BiRefNet, producing an RGBA cutout"

    @classmethod
    def get_default_config(cls) -> Dict[str, Any]:
        return {
            "model": None,
            "matte_strength": 50,
            "feather": 0.0,
        }

    @classmethod
    def configuration(cls) -> List[PipeConfigSpec]:
        return [
            PipeConfigSpec("model", dict, None,
                           "BiRefNet matting checkpoint ({file_path, name}) from the model picker",
                           required=True),
            PipeConfigSpec("matte_strength", int, 50,
                           "Smoothstep tightening of the raw sigmoid alpha "
                           "(0 = identity, 100 = hard threshold at 128)",
                           required=False, min_value=0, max_value=100),
            PipeConfigSpec("feather", float, 0.0,
                           "Gaussian-blur radius (px) applied to the alpha edge",
                           required=False, min_value=0.0, max_value=16.0),
        ]

    @classmethod
    def inputs(cls) -> List[PipeInputSpec]:
        return [
            PipeInputSpec("image", IOType.IMAGE, True, "Source image(s) to cut the subject out of", is_array=True),
            PipeInputSpec("MODELS", IOType.SERVICE, False, "Model lifecycle service for checkpoint caching", is_array=False),
        ]

    @classmethod
    def outputs(cls) -> List[PipeOutputSpec]:
        return [
            PipeOutputSpec("image", IOType.IMAGE, "RGBA cutout(s), alpha = matted subject", is_array=True),
        ]

    def _resolve_model_path(self) -> str:
        model_cfg = self.config.get("model")
        if isinstance(model_cfg, dict):
            path = model_cfg.get("file_path") or model_cfg.get("name")
        else:
            path = model_cfg
        if not path:
            raise ValueError("matting/birefnet requires a 'model' checkpoint in config")
        return str(path)

    def _acquire_model(self, pipe_input: PipeInput, model_path: str) -> BackgroundMattingModel:
        def load() -> BackgroundMattingModel:
            return BackgroundMattingModel.from_checkpoint(model_path)

        models = pipe_input.input.get("MODELS", None)
        if models is None:
            return load()
        return models.acquire(
            key=f"native/matting/{model_path}",
            fingerprint=model_path,
            loader=load,
        )

    def process(self, pipe_input: PipeInput, generation_outputs: callable) -> PipeOutput:
        images = as_image_list(pipe_input.input.get("image"), "matting/birefnet")

        matte_strength = int(self.config.get("matte_strength", 50))
        feather = float(self.config.get("feather", 0.0))
        model_path = self._resolve_model_path()

        generation_outputs(ProgressGenerationOutput(
            state=f"Removing background <<EFFECT:matting:image>> (<<NUMBER:{len(images)}>>)",
            icon=Icon(name="scissors"),
        ))

        model = self._acquire_model(pipe_input, model_path)

        import torch
        device = "cuda" if torch.cuda.is_available() else "cpu"
        results = []
        try:
            model.to(device)
            for image in images:
                matted = model(image.convert("RGB"))

                rgba = np.array(matted.convert("RGBA"))
                alpha = apply_matte_strength(rgba[..., 3], matte_strength)
                alpha = feather_alpha(alpha, feather)

                out = np.dstack([rgba[..., :3], alpha]).astype(np.uint8)
                result = Image.fromarray(out, mode="RGBA")
                results.append(result)
                generation_outputs(ImageGenerationOutput(image=result, temporary=True))
        finally:
            model.cpu()

        generation_outputs(ProgressGenerationOutput(
            state=f"Background removed (<<NUMBER:{len(results)}>>)",
            icon=Icon(name="check-circle"),
        ))
        return PipeOutput(output={"image": results})
