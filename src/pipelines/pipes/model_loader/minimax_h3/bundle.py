"""The `model` payload produced by `model_loader/minimax_h3`.

A MiniMax-H3 generation needs the DiT (the `t2va`/`fl2va` checkpoint --
`transformer_ref`/ref2va shares the SAME class and structural loader but is
a distinct file, not built by this pipe yet, per the port plan's staged
scope), the Qwen3-VL-32B text encoder, the video VAE and the audio VAE.
Unlike LTX's `upscale_model`/`audio`-are-optional idiom, the audio VAE is
ALWAYS loaded here: audio is inherent to H3 generation, not an opt-in
(dossier "No CFG" / port plan S6).

Every component is held via :class:`WeakModelRef` (see that module's
docstring): a bundle must never be able to keep its components alive on its
own once the MODELS cache has moved on to a new fingerprint -- the same
"lightweight VIEW, not an owner" contract every other native family's bundle
follows (Flux, Wan, LTX, Krea-2, ...).

There is no latent-upscaler slot on this bundle: the standalone "upscale"
mode and its 3D upsampler checkpoint moved to the ``minimax-h3-upscale``
plugin, which acquires and loads that checkpoint itself.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from src.pipelines.pipes._shared.generation.weak_model_ref import WeakModelRef
from src.platform.runtime.native.engine import NativeModel

logger = logging.getLogger(__name__)


@dataclass
class MiniMaxH3ModelBundle:
    """DiT + Qwen3-VL-32B TE + video VAE + audio VAE."""

    dit: NativeModel = field(default=WeakModelRef())
    te: NativeModel = field(default=WeakModelRef())
    video_vae: NativeModel = field(default=WeakModelRef())
    audio_vae: NativeModel = field(default=WeakModelRef())
    # The MODELS cache key `te` was acquired under -- lets a generator pipe
    # release the TE explicitly once prompt_encoder is done with it (same
    # `release_idle_te`-style idiom as LTX's `te_cache_key`). `None` for a
    # bundle built outside the MODELS cache (e.g. isolated pipe tests).
    te_cache_key: Optional[str] = None

    @property
    def spec(self):
        """The DiT's ModelSpec (sampling settings incl. shift/audio_shift,
        latent format)."""
        return self.dit.spec

    def unload(self) -> None:
        for component in (self.dit, self.te, self.video_vae, self.audio_vae):
            if component is None:
                continue
            try:
                component.unload()
            except Exception:  # pragma: no cover - best-effort eviction
                logger.debug("minimax_h3 bundle component eviction failed", exc_info=True)
