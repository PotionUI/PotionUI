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

Loading goes through the `MODELS` built-in service (`ModelLifecycle`)
when the pipeline wires it in, exactly like every other native loader pipe -
`acquire()` caches the loaded checkpoint by (key, fingerprint) and the caller
only ever moves the ALREADY-loaded model on/off the compute device.
`fingerprint` is the checkpoint's on-disk revision (mtime + size, see
`_model_fingerprint`) - the same convention `interpolator/rife`'s
`_acquire_base_model` uses - so a checkpoint replaced in place at the same
path busts the cache instead of being served stale until an unrelated
eviction/restart. Without a `MODELS` service (e.g. a bare unit test) the pipe
loads a fresh instance per-call instead of caching - correct but slow, never
used in a real pipeline. Either way the model is moved back to CPU after use
so this lightweight utility step never pins VRAM between generations.

`_RAW_MATTE_CACHE` is a second, orthogonal cache: BiRefNet's per-image
transform (`BackgroundMattingModel.INPUT_SIZE`, its normalization) is a fixed
constant with no config knob, so the raw alpha it produces is a pure function
of (checkpoint identity + revision, RGB pixel bytes, dimensions) - it never
depends on `matte_strength`/`feather`, which are applied downstream on every
hit and miss alike. Caching it lets a re-run with only `matte_strength`/
`feather` changed (a common "tune the cutout" loop) skip the model
altogether, on CPU only and under a small byte budget.

Config `model.file_path` is used AS-IS, exactly like `interpolator/rife`'s
`_resolve_model_path` - never joined onto a models directory. A model-picker
row's `file_path` is already relative to the process's own working directory
and already carries the models dir's own name as its first path component
(see `content/plugins/marketplace/spritesheet/backend/imaging/matting.py::resolve_checkpoint`);
joining it onto another base double-prefixes and 404s on a real checkpoint.
"""

import hashlib
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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

#: A few tens of MB of raw single-channel (uint8) alpha maps - enough to
#: carry a "tweak matte_strength/feather" loop over a handful of images
#: without ever approaching model-checkpoint scale.
_RAW_MATTE_CACHE_MAX_BYTES = 32 * 1024 * 1024


def _model_fingerprint(model_path: str) -> str:
    """Fingerprint on the checkpoint's on-disk revision (mtime + size) - see
    `interpolator/rife`'s `_model_fingerprint` - so a checkpoint replaced in
    place at the same path busts the `MODELS` lifecycle cache (and this
    pipe's own raw-matte cache) instead of being served stale."""
    stat = Path(model_path).stat()
    return f"{stat.st_mtime}|{stat.st_size}"


class _RawMatteCache:
    """Bounded CPU-only LRU for {cache key -> raw single-channel alpha}.

    Entries are immutable: `put` stores a defensive contiguous copy and `get`
    hands back another copy, so neither a caller mutating what it got back
    nor a caller mutating the array it just handed to `put` can corrupt what
    is cached. An entry larger than the whole budget is never stored (the
    caller still gets its result - it's just never cached), rather than
    evicting every other entry to make room for one that can't fit anyway.
    """

    def __init__(self, max_bytes: int):
        self._max_bytes = max_bytes
        self._entries: "OrderedDict[tuple, np.ndarray]" = OrderedDict()
        self._bytes = 0
        self._lock = threading.Lock()

    def get(self, key: tuple) -> Optional[np.ndarray]:
        with self._lock:
            value = self._entries.get(key)
            if value is None:
                return None
            self._entries.move_to_end(key)
            return value.copy()

    def put(self, key: tuple, value: np.ndarray) -> None:
        entry = np.ascontiguousarray(value, dtype=np.uint8).copy()
        size = entry.nbytes
        if size > self._max_bytes:
            return
        with self._lock:
            existing = self._entries.pop(key, None)
            if existing is not None:
                self._bytes -= existing.nbytes
            self._entries[key] = entry
            self._bytes += size
            while self._bytes > self._max_bytes:
                _, evicted = self._entries.popitem(last=False)
                self._bytes -= evicted.nbytes


_RAW_MATTE_CACHE = _RawMatteCache(_RAW_MATTE_CACHE_MAX_BYTES)


def _raw_matte_cache_key(model_path: str, fingerprint: str, rgb: Image.Image) -> Tuple:
    digest = hashlib.sha1(rgb.tobytes()).hexdigest()
    width, height = rgb.size
    return (model_path, fingerprint, digest, width, height)


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

    def _acquire_model(self, pipe_input: PipeInput, model_path: str, fingerprint: str) -> BackgroundMattingModel:
        def load() -> BackgroundMattingModel:
            return BackgroundMattingModel.from_checkpoint(model_path)

        models = pipe_input.input.get("MODELS", None)
        if models is None:
            return load()
        return models.acquire(
            key=f"native/matting/{model_path}",
            fingerprint=fingerprint,
            loader=load,
        )

    def _finalize(
        self,
        rgb: Image.Image,
        raw_alpha: np.ndarray,
        matte_strength: int,
        feather: float,
        generation_outputs: callable,
    ) -> Image.Image:
        alpha = apply_matte_strength(raw_alpha, matte_strength)
        alpha = feather_alpha(alpha, feather)
        out = np.dstack([np.array(rgb), alpha]).astype(np.uint8)
        result = Image.fromarray(out, mode="RGBA")
        generation_outputs(ImageGenerationOutput(image=result, temporary=True))
        return result

    def process(self, pipe_input: PipeInput, generation_outputs: callable) -> PipeOutput:
        images = as_image_list(pipe_input.input.get("image"), "matting/birefnet")

        matte_strength = int(self.config.get("matte_strength", 50))
        feather = float(self.config.get("feather", 0.0))
        model_path = self._resolve_model_path()
        fingerprint = _model_fingerprint(model_path)

        generation_outputs(ProgressGenerationOutput(
            state=f"Removing background <<EFFECT:matting:image>> (<<NUMBER:{len(images)}>>)",
            icon=Icon(name="scissors"),
        ))

        rgb_images = [image.convert("RGB") for image in images]
        cache_keys = [_raw_matte_cache_key(model_path, fingerprint, rgb) for rgb in rgb_images]
        cached_alphas = [_RAW_MATTE_CACHE.get(key) for key in cache_keys]

        results: List[Image.Image] = []
        if all(alpha is not None for alpha in cached_alphas):
            # Every raw matte for this batch is already cached, keyed on the
            # checkpoint's identity/revision established above from the
            # on-disk stat alone - never needs to acquire the model or move
            # anything onto the compute device.
            for rgb, alpha in zip(rgb_images, cached_alphas):
                results.append(self._finalize(rgb, alpha, matte_strength, feather, generation_outputs))
        else:
            model = self._acquire_model(pipe_input, model_path, fingerprint)
            import torch
            device = "cuda" if torch.cuda.is_available() else "cpu"
            try:
                model.to(device)
                for rgb, key, cached in zip(rgb_images, cache_keys, cached_alphas):
                    if cached is None:
                        matted = model(rgb)
                        raw_alpha = np.array(matted.convert("RGBA"))[..., 3]
                        _RAW_MATTE_CACHE.put(key, raw_alpha)
                    else:
                        raw_alpha = cached
                    results.append(self._finalize(rgb, raw_alpha, matte_strength, feather, generation_outputs))
            finally:
                model.cpu()

        generation_outputs(ProgressGenerationOutput(
            state=f"Background removed (<<NUMBER:{len(results)}>>)",
            icon=Icon(name="check-circle"),
        ))
        return PipeOutput(output={"image": results})
