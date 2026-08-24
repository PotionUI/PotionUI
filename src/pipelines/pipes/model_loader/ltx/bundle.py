"""The `model` payload produced by `model_loader/ltx`.

An LTX generation needs the DiT + Gemma3-12B TE + causal video VAE, plus the
``text_embedding_projection`` tensors the all-in-one checkpoint carries at the
top level (outside the DiT's own ``model.diffusion_model.*`` slice — the DiT
loader drops them, see ``projection.py``). Unlike Wan, LTX is single-DiT: no
high/low expert pair.

``audio_vae``/``vocoder`` are populated only when the pipe's ``audio`` config
is on (see ``main.py``) — both ``None`` otherwise, at zero load cost. When
present, a ``generator/video_ltx``-style pipe decodes audio via
``decode_audio_waveform(bundle.audio_vae.module, bundle.vocoder.module, latents)``
(``src/platform/runtime/native/vae/ltx_audio.py``), which expects the raw
``LTXAudioAutoencoder``/``LTXVocoder``-or-``LTXVocoderAMP`` module handles, not
the ``NativeModel`` wrapper.

``upsampler`` is the same shape: populated only when the
pipe's ``upscale_model`` config points at a checkpoint (the preset's
`upscale: off | 1.5x | 2.0x` field), ``None`` otherwise, at zero VRAM/RAM cost
when unused. ``latent_upscaler/ltx`` (``src/pipelines/pipes/latent_upscaler/
ltx/main.py``) reads ``bundle.upsampler.module`` (an ``LTXLatentUpsampler``,
see ``src/platform/runtime/native/vae/ltx_latent_upsampler.py``) plus
``bundle.vae.module.per_channel_statistics`` for the un-normalize/normalize
step around the upsample call (verbatim ComfyUI/Lightricks recipe).

``temporal_upsampler`` is a second slot of that same shape, holding the
LTX-2.5 temporal x2 upsampler; one pipeline can need both files at once, so
they cannot share a slot. ``latent_upscaler/ltx``'s ``mode`` config selects
which one it reads.

``duration_head`` (an ``LTXDurationHead``, see
``src/platform/runtime/native/arch/ltx/duration_head.py``) is populated only
when the pipe's ``duration_head`` config points at the LTX-2.5 head file.
Nothing in the engine consults it yet -- no generator pipe calls
``LTXDurationHead.predict_num_frames`` through this bundle.

The ``NativeModel`` components (``dit``/``te``/``vae``/``audio_vae``/``vocoder``)
are held via ``WeakModelRef`` (see that module's docstring):
holding onto a bundle instance can never keep a stale, evicted component
resident. ``projections`` stays a plain strong field - it's tensors carried
directly by this bundle, not independently MODELS-cached elsewhere, so there is
no cache-owned strong reference for a weak view to defer to.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from src.pipelines.pipes._shared.generation.weak_model_ref import WeakModelRef
from src.platform.runtime.native.engine import NativeModel

logger = logging.getLogger(__name__)


@dataclass
class LTXModelBundle:
    """DiT + Gemma3 TE + causal video VAE + text-embedding projection tensors
    (+ optional audio VAE / vocoder, see module docstring)."""

    dit: NativeModel = field(default=WeakModelRef())
    te: NativeModel = field(default=WeakModelRef())
    vae: NativeModel = field(default=WeakModelRef())
    projections: Dict[str, Any] = field(default_factory=dict)
    audio_vae: Optional[NativeModel] = field(default=WeakModelRef())
    vocoder: Optional[NativeModel] = field(default=WeakModelRef())
    upsampler: Optional[NativeModel] = field(default=WeakModelRef())
    temporal_upsampler: Optional[NativeModel] = field(default=WeakModelRef())
    duration_head: Optional[NativeModel] = field(default=WeakModelRef())
    # The MODELS cache key `te` was acquired under (``f"native/te/{te_path}"``,
    # see model_loader/ltx/main.py) -- a plain str field, not a weak view: it's
    # not a MODELS-cached OBJECT itself, just the lookup key a later pipe needs
    # to release the TE explicitly once it's dead weight (see
    # `latent_upscaler/ltx/main.py`'s `_unload_idle_te`). `None` for any bundle
    # built without going through the MODELS cache (e.g. isolated pipe tests).
    te_cache_key: Optional[str] = None

    @property
    def spec(self):
        """The DiT's ModelSpec (sampling settings incl. shift, latent format)."""
        return self.dit.spec

    @property
    def model_version(self):
        """The checkpoint's own ``model_version`` (e.g. "2.5"), or ``None`` for
        checkpoints that predate the field. Threaded through
        ``detect_unet_config`` -> ``LTXAVConfig`` -> ``LTXAVModel.config`` at
        load time (see ``detect/unet_detect.py``'s ``_parse_ltx_model_version``);
        no bundle-level storage needed, this just names the existing path for
        callers that need to branch on it (e.g. sampling)."""
        return getattr(self.dit.module.config, "model_version", None)

    @property
    def te_encoder(self):
        te = self.te
        return te.module if te is not None else None

    def unload(self) -> None:
        for component in (self.dit, self.te, self.vae, self.audio_vae, self.vocoder, self.upsampler,
                          self.temporal_upsampler, self.duration_head):
            if component is None:
                continue
            try:
                component.unload()
            except Exception:  # pragma: no cover - best-effort eviction
                logger.debug("ltx bundle component eviction failed", exc_info=True)
