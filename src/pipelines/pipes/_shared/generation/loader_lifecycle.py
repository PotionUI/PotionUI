"""Component lifetimes for the native model-loader pipes.

Every native loader pipe resolves the same shape: a bundle of heavy
components, each acquired through ``MODELS`` under its own cache key, each
announced to the frontend through :class:`ComponentProgress` before it loads,
and each falling back to a direct load when no lifecycle service is injected
(isolated pipe use, e.g. tests). :class:`Component` names one such piece and
:class:`ComponentLifecycle` owns the acquire itself; families keep their own
construction, encoder variants and precision/device decisions.

Cache keys follow ``native/<kind>/<path>`` and fingerprints ``<path>|<dtype>``
plus whatever else changes the built module (Qwen/Krea-2's ``vision`` flag,
Z-Image's TE variant, a DiT's LoRA stack where the family busts on it). Both
are the caller's, not this module's: a family that needs a different identity
scheme states it rather than reaching for a flag here.

``Component.estimated_vram_gb`` is the pre-load admission estimate, and it is
NOT always the component's file size:

* A component in its own standalone file estimates ``file_size_gb(path)``.
* A component sliced out of a checkpoint another component already estimated
  passes None. LTX's all-in-one file carries the video VAE, audio VAE and
  vocoder behind the DiT's footprint, so estimating each from the whole ~40GB
  file would 2-4x the admission budget. The lifecycle records the real
  per-component size after the load either way.
* Flux's composite text encoder is two files (T5-XXL + CLIP-L) under one key,
  so its estimate is their sum.

Deferred acquisition (:meth:`ComponentLifecycle.deferred`) hands a component's
acquire to a clip adapter as a thunk, run at most once and only when the
adapter actually needs the module — a generation whose prompt-embed cache hits
never resolves it, and never pays a from-disk text-encoder load the generator's
idle-TE release would evict moments later. A bundle built this way holds
``te=None`` and carries the TE's cache key as a plain string, which is what
eviction addresses; evicting a never-acquired key is a documented no-op.

Two family specifics stay out of here deliberately: MiniMax-H3's sparse
attention and vision-tower flags belong to its own ``loader.load`` call, and
Krea-2's step-window revision (``_sync_lora_windows``) has a single
implementation, so hoisting it would add indirection without removing a copy.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from src.platform.runtime.native.engine import NativeModel
from src.platform.runtime.native.lora import remove_loras
from src.pipelines.pipes._shared.generation.loader_helpers import ComponentProgress


@dataclass(frozen=True)
class Component:
    """One cacheable piece of a model bundle.

    ``label`` is the human-readable component name in the progress line
    ("text encoder", "high-noise DiT"); ``load`` is the from-disk fallback the
    lifecycle runs on a cache miss or with no lifecycle service at all.
    """

    label: str
    key: str
    fingerprint: str
    load: Callable[[], Any]
    estimated_vram_gb: Optional[float] = None


class ComponentLifecycle:
    """Acquires a loader pipe's components, announcing each one first.

    ``models`` is the ``ModelLifecycle``-shaped service injected as the
    ``MODELS`` pipe input, or None when there is none to cache through.
    """

    def __init__(self, models: Optional[Any], progress: ComponentProgress) -> None:
        self._models = models
        self._progress = progress

    @property
    def caching(self) -> bool:
        return self._models is not None

    def acquire(self, component: Component) -> Any:
        self._progress.advance(component.label, component.key)
        if self._models is None:
            return component.load()
        return self._models.acquire(
            key=component.key,
            fingerprint=component.fingerprint,
            loader=component.load,
            estimated_vram_gb=component.estimated_vram_gb,
        )

    def deferred_module(self, component: Component) -> Callable[[], Any]:
        """The acquire as a thunk yielding the loaded ``module``, for a
        component whose consumer decides whether it is ever needed. Nothing is
        announced, admitted or loaded until the thunk runs."""
        return lambda: self.acquire(component).module


def sync_loras(
    dit_model: NativeModel,
    loras: List[Dict[str, Any]],
    lora_fp: str,
    apply: Callable[[NativeModel, List[Dict[str, Any]]], None],
) -> None:
    """Reconcile a (possibly cache-HIT, already-patched) DiT's applied LoRA
    stack with the requested one, in place — never re-reads the checkpoint.

    For a family whose DiT fingerprint is LoRA-INDEPENDENT (path + dtype only),
    a LoRA-set change is a cache HIT reusing the resident weights instead of a
    fingerprint bust that re-reads a ~24GB checkpoint from disk.

    ``dit_model._active_lora_fp`` is the loader's stamp of what is currently
    patched into the weights (set here and by the family's ``load_dit`` on a
    fresh load). Equal to the requested ``lora_fp`` -> nothing to do, the common
    "same preset, same LoRAs, next generation" case is a pure no-op. A mismatch
    means a cache HIT with a different LoRA request; a cache MISS whose loader
    already applied and stamped the correct stack never reaches the branch.

    The revision bump precedes the mutation so an ``apply`` that raises midway
    (leaving a stripped or half-patched module, with the stamp deliberately
    left stale so the next call retries) still cannot serve a cache keyed on
    the pre-mutation weights.
    """
    if getattr(dit_model, "_active_lora_fp", None) == lora_fp:
        return
    dit_model.bump_weight_revision(f"lora stack -> {lora_fp}")
    remove_loras(dit_model.module)
    if loras:
        apply(dit_model, loras)
    dit_model._active_lora_fp = lora_fp  # noqa: SLF001 - the loader's stamp, not the wrapper's private state
