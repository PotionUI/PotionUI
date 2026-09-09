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
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from src.platform.observability.profiling import get_profiler
from src.platform.runtime.native.engine import NativeModel
from src.platform.runtime.native.lora import AdapterApplication, remove_loras
from src.pipelines.contracts import logger
from src.pipelines.pipes._shared.generation.loader_helpers import (
    ComponentProgress,
    describe_lora_stack,
    emit_lora_application_diagnostics,
)


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

    def discard(self, component: Component) -> None:
        """Drop ``component``'s cache entry so the next acquire loads it from
        disk again, for a caller that has found the cached value unusable."""
        evict = getattr(self._models, "evict_dead_weight", None)
        if callable(evict):
            evict(component.key)

    def deferred_module(self, component: Component) -> Callable[[], Any]:
        """The acquire as a thunk yielding the loaded ``module``, for a
        component whose consumer decides whether it is ever needed. Nothing is
        announced, admitted or loaded until the thunk runs."""
        return lambda: self.acquire(component).module


#: The ``lora_fp`` stamp of a DiT carrying no adapters at all — what a family
#: builds for an empty stack, and what :func:`sync_loras` re-stamps after it has
#: rolled a failed reconciliation back to the base checkpoint weights.
NO_LORAS = "none"


def sync_loras(
    dit_model: NativeModel,
    loras: List[Dict[str, Any]],
    lora_fp: str,
    apply: Callable[[NativeModel, List[Dict[str, Any]]], Optional[Sequence[AdapterApplication]]],
    *,
    lifecycle: Optional[ComponentLifecycle] = None,
    component: Optional[Component] = None,
    generation_outputs: Optional[Callable] = None,
    log_tag: str = "MODEL LOADER",
) -> Tuple[AdapterApplication, ...]:
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

    The stamp is CLEARED before the first mutation and only written once the
    requested stack is fully applied, so a reconciliation that dies midway can
    never be mistaken for a completed one. That is the whole failure the stamp
    exists to prevent: stamps are compared, not verified, so a module left
    stripped or half-patched while still labelled ``A`` makes the next request
    for ``A`` a silent no-op that samples weights nobody asked for.

    A failure is then rolled back to the base checkpoint weights (``remove_loras``
    undoes exactly the deltas that were recorded, partial applications included)
    and re-stamped :data:`NO_LORAS`, so the next request for any stack — the one
    that just failed, or the one that was resident before it — reapplies from a
    known state. If the rollback itself fails there is no known state left to
    name: the wrapper is marked unusable (refused at the top of this function)
    and its cache entry dropped, forcing a fresh read from disk rather than a
    generation on weights that match no request.

    Every path that changes the weights bumps the revision first, so a cache
    keyed on the pre-mutation weights is invalidated even when the mutation
    raises (see :meth:`NativeModel.bump_weight_revision`).

    ``apply``'s per-file :class:`AdapterApplication` evidence (its return —
    ``None``/empty tolerated, since not every caller returns it yet) is
    stashed on ``dit_model`` next to the stamp and returned. The no-op branch
    (unchanged ``lora_fp``) re-emits that STORED evidence through
    ``generation_outputs`` rather than recomputing it — the whole point of the
    cache-HIT path is to skip re-running ``map_lora_keys``/``apply_loras`` on
    an unchanged stack, so the diagnostics for it must not force that work
    back on. A failed reconciliation leaves no evidence behind (cleared before
    the mutation, alongside the stamp, and never re-set on the raising path).
    """
    if dit_model.unusable_reason is not None:
        raise RuntimeError(
            f"native DiT is unusable and must be reloaded: {dit_model.unusable_reason}"
        )
    if getattr(dit_model, "_active_lora_fp", None) == lora_fp:
        cached = getattr(dit_model, "_active_lora_application", ())
        get_profiler().mark("lora.sync", branch="noop", fingerprint=lora_fp)
        if cached:
            # The stack is still patched into the resident weights, so the
            # generation runs with exactly the cost this describes even though
            # nothing was read or applied on this pass.
            logger.info("[%s] %s (reused)", log_tag, describe_lora_stack(cached))
        emit_lora_application_diagnostics(generation_outputs, cached, log_tag)
        return cached
    get_profiler().mark("lora.sync", branch="reapply", fingerprint=lora_fp)
    dit_model.bump_weight_revision(f"lora stack -> {lora_fp}")
    dit_model._active_lora_fp = None  # noqa: SLF001 - the loader's stamp, not the wrapper's private state
    dit_model._active_lora_application = ()  # noqa: SLF001 - cleared before the mutation, same as the stamp
    try:
        remove_loras(dit_model.module)
        report = apply(dit_model, loras) if loras else ()
    except Exception:
        _roll_back_to_base_weights(dit_model, lifecycle, component)
        raise
    dit_model._active_lora_fp = lora_fp  # noqa: SLF001 - the loader's stamp, not the wrapper's private state
    dit_model._active_lora_application = tuple(report) if report else ()  # noqa: SLF001
    emit_lora_application_diagnostics(generation_outputs, dit_model._active_lora_application, log_tag)
    return dit_model._active_lora_application


def _roll_back_to_base_weights(
    dit_model: NativeModel,
    lifecycle: Optional[ComponentLifecycle],
    component: Optional[Component],
) -> None:
    """Strip whatever a failed :func:`sync_loras` left behind, or give up on the
    wrapper entirely. Never raises — the reconciliation's own error is the one
    the caller must see."""
    dit_model.bump_weight_revision("lora reconciliation failed -> unpatching to base weights")
    try:
        remove_loras(dit_model.module)
    except Exception as exc:
        dit_model.mark_unusable(f"a failed LoRA reconciliation could not be rolled back: {exc}")
        if lifecycle is not None and component is not None:
            lifecycle.discard(component)
        return
    dit_model._active_lora_fp = NO_LORAS  # noqa: SLF001 - the loader's stamp, not the wrapper's private state
