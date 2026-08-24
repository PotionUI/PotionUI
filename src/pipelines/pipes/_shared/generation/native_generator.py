"""Family-keyed factory for building the right ``NativeGenerator`` subclass.

Most native families sample through the plain :class:`NativeGenerator` (its
generic ``model_forward`` threads ``context``/``y``/``guidance``/``attention_mask``).
A few need a subclass with a family-specific forward adapter -- Anima's
``AnimaNativeGenerator`` also passes the LLMAdapter's ``t5xxl_ids``/``t5xxl_weights``.

The generator *pipe* already knows its own subclass and builds it inline. The
problem this registry solves is the **second** consumer: the family-agnostic
``tiled_refiner`` pipe reuses the same model bundle + conditioning to run per-tile
img2img, so it must build the SAME generator class the family's generator would --
without hard-coding a family name. Each generator module registers its subclass at
import (``@register_native_generator("anima")``); since pipe discovery imports every
``main.py`` at startup, every family is registered before any pipeline runs. A
family with no registration falls back to the plain generator, which is correct for
every plain-forward family.
"""

from __future__ import annotations

from src.platform.runtime.native.engine import NativeGenerator
from src.platform.runtime.native.memory import make_device_plan

_GENERATOR_CLASSES: dict[str, type[NativeGenerator]] = {}


def register_native_generator(family: str):
    """Class decorator: register ``cls`` as the generator for ``family``."""

    def _register(cls: type[NativeGenerator]) -> type[NativeGenerator]:
        _GENERATOR_CLASSES[family] = cls
        return cls

    return _register


def native_generator_class(family: str) -> type[NativeGenerator]:
    """The registered generator subclass for ``family`` (plain ``NativeGenerator`` default)."""
    return _GENERATOR_CLASSES.get(family, NativeGenerator)


def build_native_generator(bundle, *, device: str = "cuda") -> NativeGenerator:
    """Build the family-correct generator for a loaded model ``bundle``.

    ``bundle`` is any family model bundle exposing ``.dit`` / ``.te_encoder`` /
    ``.vae`` / ``.spec`` (e.g. ``AnimaModelBundle``) -- the same shape the generator
    pipes consume. The device plan is sized from the DiT so placement/eviction work
    identically to the generator path.
    """
    device_plan = make_device_plan(preferred=device, dit_gb=bundle.dit.estimated_vram_gb)
    cls = native_generator_class(bundle.spec.family)
    return cls(bundle.dit, bundle.te_encoder, bundle.vae, device_plan)
