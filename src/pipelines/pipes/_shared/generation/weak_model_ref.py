"""Weak-reference field for native model-loader bundles.

Every family's ``model_loader/<family>/bundle.py`` (Flux, Qwen, Z-Image, Anima,
Wan, LTX, SeedVR2, Krea-2) is documented as "a lightweight VIEW over
independently MODELS-cached ``NativeModel`` components - not an owner": the
cache is supposed to be the only strong-reference root, so a fingerprint-bust
eviction (a LoRA swap, a preset switch) can actually free a component once
nothing else needs it. Every one of those bundles is a plain ``@dataclass``,
though, and a dataclass field IS a strong reference - the documented intent
didn't match the implementation.

Production's referrer diagnostic (``ModelLifecycleManager.
_evict_entry``'s ``gc.get_referrers`` dump) named a live ``Krea2ModelBundle``
instance as what kept an evicted, orphaned Krea-2 DiT VRAM/RAM-resident
forever after a LoRA swap (+25GB stuck host RAM). Who ends up holding the
bundle itself turned out not to matter: a bundle must never be able to keep
its components alive on its own, regardless of who holds it or for how long -
a generator pipe legitimately keeps a bundle reference for the length of one
(possibly long, chunked-video) generation (see ``generator/txt2vid_ltx``,
which stores ``ctx.bundle`` and re-reads ``bundle.dit`` repeatedly across a
whole video render) and that must keep working; what must NOT keep working is
that same bundle preventing its components from being reclaimed once the
CACHE has moved on to a new fingerprint.

``WeakModelRef`` fixes this once for every family: use it as a dataclass
field's default and the field is stored as a ``weakref.ref``, dereferenced
back to the live object (or ``None`` if it's been collected) on every read.
Holding the bundle - by anyone, for any reason, for any length of time - then
never keeps a multi-GB component resident a moment longer than the cache's
own strong reference does.

Contract for callers: dereference a bundle's component fields into your OWN
strong-held variable/attribute up front (as ``NativeGenerator.__init__``
already does: ``self.dit = dit`` from ``bundle.dit``) rather than repeatedly
reading ``bundle.<field>`` deep inside a long-running loop on the assumption
it's a stable strong reference - it's a live view, valid for as long as the
cache entry it mirrors is (the entire duration of one generation), but a view
nonetheless.
"""

from __future__ import annotations

import logging
import weakref
from typing import Any, Optional

logger = logging.getLogger(__name__)


class WeakModelRef:
    """Dataclass field descriptor: stores an assigned value as a
    ``weakref.ref``, returns the live object (or ``None``) on read.

    Use as ``dit: NativeModel = field(default=WeakModelRef())`` (works
    identically for an ``Optional[NativeModel] = field(default=WeakModelRef())``
    field like Wan's ``low_dit`` or LTX's ``audio_vae``/``vocoder`` - assigning
    ``None`` is a no-op, not a dead-weakref case). ``field(default=...)``
    is required, not a bare class-level assignment, so ``@dataclass`` treats
    each family's existing REQUIRED constructor args
    (``Krea2ModelBundle(dit=dit_model, te=te_model, vae=vae_model)``) as
    unchanged call sites - this descriptor only changes how the value is
    STORED internally, never the public field name or the constructor shape.
    """

    def __set_name__(self, owner: type, name: str) -> None:
        self._attr = f"_weak_{name}"
        self._field_name = name

    def __get__(self, obj: Any, objtype: Optional[type] = None) -> Any:
        if obj is None:
            return self
        stored = obj.__dict__.get(self._attr)
        return stored() if isinstance(stored, weakref.ReferenceType) else stored

    def __set__(self, obj: Any, value: Any) -> None:
        if value is None or value is self:
            # `value is self`: an OPTIONAL field (e.g. Wan's `low_dit`,
            # LTX's `audio_vae`/`vocoder`) used as
            # `field(default=WeakModelRef())` so it can ALSO hold a weak
            # reference when it IS provided - but when the caller omits it,
            # dataclass's generated __init__ resolves the parameter default to
            # this descriptor INSTANCE itself (the literal `default=` value)
            # and calls `self.<field> = <that instance>` - without this check
            # the omitted-field case would store (and later return) the
            # descriptor object instead of ``None``.
            obj.__dict__[self._attr] = None
            return
        try:
            obj.__dict__[self._attr] = weakref.ref(value)
        except TypeError:
            # Not every stand-in used in tests supports weakref (notably
            # ``types.SimpleNamespace``, which CPython does not allow to be
            # weakly referenced) - real production values are always
            # ``NativeModel`` instances, which do. Degrade to a strong
            # reference rather than making every isolated pipe unit test that
            # uses a lightweight fake responsible for knowing this detail;
            # the fix this field exists for only matters for the real
            # NativeModel-holding path.
            logger.debug(
                "WeakModelRef: %r is not weakly referenceable; storing %s "
                "as a strong reference instead", type(value).__name__, self._field_name,
            )
            obj.__dict__[self._attr] = value
