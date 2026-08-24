"""The ``model`` payload produced by ``model_loader/minimax_music3``.

A MiniMax-Music3 generation needs the flow-matching DiT (``dit``, the fused
condition encoder is part of the same file -- see ``arch/minimax_music3/
model.py``), the fused AR text encoder (``lm``: global LLM + depth decoder +
the checkpoint's own tokenizer, ``lm.tokenizer``), and the DAV vocoder
(``dav``). Unlike every image/video family's bundle, ``dit`` and ``lm`` are
never meant to be resident together on a 24GB card: the AR stage's ~17GB
bf16 LM unit must leave before the DiT places (port plan S5 "VRAM strategy /
stage handoff", the same failure shape as the H3 mode-switch RAM OOM). The
generator releases ``lm``'s MODELS cache entry via ``lm_cache_key`` -- the
same proactive-eviction idiom LTX's/H3's ``te_cache_key`` established for
their own text encoders, just triggered from inside the generator instead of
before it (Music3 has no separate ``prompt_encoder`` stage: the AR core is
consumed directly by this family's own generator pipe).

Every component is held via :class:`WeakModelRef` (see that module's
docstring): a bundle must never be able to keep its components alive on its
own once the MODELS cache has moved on to a new fingerprint -- the same
"lightweight VIEW, not an owner" contract every other native family's bundle
follows (Flux, Wan, LTX, Krea-2, MiniMax-H3, ...).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from src.pipelines.pipes._shared.generation.weak_model_ref import WeakModelRef
from src.platform.runtime.native.engine import NativeModel

logger = logging.getLogger(__name__)


@dataclass
class MiniMaxMusic3ModelBundle:
    """Flow-matching DiT + fused AR text encoder + DAV vocoder."""

    dit: NativeModel = field(default=WeakModelRef())
    lm: NativeModel = field(default=WeakModelRef())
    dav: NativeModel = field(default=WeakModelRef())
    # The MODELS cache key `lm` was acquired under -- lets the generator
    # release the AR core explicitly once the AR loop is done with it,
    # before the DiT places (see module docstring). `None` for a bundle
    # built outside the MODELS cache (e.g. isolated pipe tests).
    lm_cache_key: Optional[str] = None

    @property
    def spec(self):
        """The DiT's ModelSpec (sampling settings incl. steps/cfg/ar_cfg/
        top_k, latent format)."""
        return self.dit.spec

    @property
    def tokenizer(self) -> Any:
        """The checkpoint's own tokenizer, built alongside `lm` at load time
        (`model_loader/minimax_music3/te_loader.py`). A caller must read
        this BEFORE releasing `lm` (see module docstring) -- once evicted,
        `lm` (and therefore this) may resolve to `None`."""
        lm = self.lm
        return getattr(lm, "tokenizer", None) if lm is not None else None

    def unload(self) -> None:
        for component in (self.dit, self.lm, self.dav):
            if component is None:
                continue
            try:
                component.unload()
            except Exception:  # pragma: no cover - best-effort eviction
                logger.debug("minimax_music3 bundle component eviction failed", exc_info=True)
