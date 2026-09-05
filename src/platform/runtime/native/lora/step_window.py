"""Step-windowed LoRA: apply a LoRA only while sampling is inside a step range.

Some distilled LoRAs are trained to steer only the high-noise end of the
trajectory and actively *degrade* the result if left on for the whole run.
``F16/krea2-turbo-sda`` is the motivating case: "It must only be active for the
first 2 of the 8 denoise steps, then switched off" — its own reference snippet
is a ``callback_on_step_end`` that calls ``pipe.disable_lora()`` once step 2 has
finished. Leaving it on for all 8 collapses quality (2.2x baseline HF energy,
-10% HPS per the model card).

Window semantics (the config contract presets and the form UI speak):

  * ``step_start`` — 1-based, INCLUSIVE. The first step the LoRA is active on.
    Omitted/None = 1.
  * ``step_end``   — 1-based, INCLUSIVE. The last step the LoRA is active on.
    Omitted/None = the final step (i.e. "on from ``step_start`` onward").

1-based-inclusive on both ends is chosen so the model card's phrasing maps
literally: "the first 2 of the 8 denoise steps" is ``step_start: 1,
step_end: 2``, and "always on" is ``step_end: 8`` on an 8-step run — the same
reading as that card's own ``gate`` parameter. It also matches what the user
sees in the form, where ``steps: 8`` means steps 1..8.

An entry with NEITHER key is not windowed at all: it is baked into the model at
load time exactly as before this module existed, and never touched by the
sampler. Only entries that carry a window ride :class:`LoraStepWindowHook`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, List, Sequence, Tuple

import torch

from ..sampling.hooks import BaseStepHook
from .apply import AdapterApplication, apply_loras_with_report, restore_lora_state, snapshot_lora_state

if TYPE_CHECKING:  # a runtime import would close the engine -> lora -> engine loop
    from ..engine import NativeModel

logger = logging.getLogger(__name__)

# Config keys a LoRA entry may carry to request a window.
WINDOW_KEYS = ("step_start", "step_end")


@dataclass(frozen=True)
class LoraStepWindow:
    """A 1-based inclusive ``[start, end]`` step range; ``end=None`` = open-ended."""

    start: int = 1
    end: "int | None" = None

    def __post_init__(self) -> None:
        if self.start < 1:
            raise ValueError(f"step_start must be >= 1 (steps are 1-based), got {self.start}")
        if self.end is not None:
            if self.end < 1:
                raise ValueError(f"step_end must be >= 1 (steps are 1-based), got {self.end}")
            if self.end < self.start:
                raise ValueError(
                    f"step_end ({self.end}) must be >= step_start ({self.start}) — "
                    f"an empty window would leave the LoRA permanently off"
                )

    def contains(self, step_index: int) -> bool:
        """True iff the 0-based ``step_index`` falls inside this window."""
        if step_index < self.start - 1:
            return False
        return self.end is None or step_index <= self.end - 1

    def describe(self) -> str:
        return f"steps {self.start}-{self.end if self.end is not None else 'end'}"


def parse_lora_window(entry: Dict[str, Any]) -> "LoraStepWindow | None":
    """Read a :class:`LoraStepWindow` off a raw LoRA config entry.

    ``None`` when the entry carries neither key (or both are blank/None) — the
    unwindowed, bake-at-load path. Values arrive from YAML/form JSON, so a
    blank string counts as absent, but a non-numeric non-blank value is a
    misconfigured preset and raises rather than silently dropping the window.

    Idempotent: an entry that has already been through ``active_loras`` carries
    the parsed window under ``window`` rather than the raw keys, and some
    loaders (wan22's ``acquire``) re-filter their own output. Reading that back
    keeps the window instead of quietly losing it on the second pass.
    """
    parsed_already = entry.get("window")
    if isinstance(parsed_already, LoraStepWindow):
        return parsed_already
    raw = {key: entry.get(key) for key in WINDOW_KEYS}
    present = {key: value for key, value in raw.items()
               if value is not None and str(value).strip() != ""}
    if not present:
        return None
    parsed: Dict[str, int] = {}
    for key, value in present.items():
        try:
            parsed[key] = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"LoRA {key} must be an integer step number, got {value!r}") from exc
    return LoraStepWindow(start=parsed.get("step_start", 1), end=parsed.get("step_end"))


def has_lora_window(entry: Dict[str, Any]) -> bool:
    """True iff the raw entry asks for a window (without validating it)."""
    return any(
        entry.get(key) is not None and str(entry.get(key)).strip() != ""
        for key in WINDOW_KEYS
    )


# A windowed LoRA ready to toggle: its loaded state dict, its strength, and when.
WindowedLora = Tuple[Dict[str, torch.Tensor], float, LoraStepWindow]


class LoraStepWindowHook(BaseStepHook):
    """Toggles windowed LoRAs on ``module`` as the sampler crosses step edges.

    The sampler dispatches ``on_start(total_steps)`` before step 0 and
    ``on_step(i, ...)`` after step ``i`` completes, so every step boundary is
    observable: ``on_start`` sets the state entering step 0, and ``on_step(i)``
    sets the state entering step ``i+1``. That is the same seam the model
    card's ``callback_on_step_end`` gate uses.

    Rather than tracking per-window apply/remove (which breaks the moment two
    windows overlap — the second's snapshot would capture the first's deltas as
    pre-existing state), every transition restores the module to ONE base
    snapshot taken at construction and re-applies whichever windows are active
    now. Two patch ops per boundary, and boundaries are few (at most one per
    distinct window edge), so the cost is noise against a model forward.

    The base snapshot is what makes this cache-safe: it is taken AFTER the
    loader baked the unwindowed stack, so restoring to it leaves that stack
    intact and removes only what this hook added. :meth:`close` restores
    unconditionally and is idempotent — callers MUST invoke it from a
    ``finally`` rather than relying on ``on_end``, because the sampler isolates
    (swallows) hook exceptions and a swallowed removal would leave the shared,
    cached model permanently patched.

    Every boundary that actually changes the patches advances the DiT wrapper's
    ``effective_revision``, which is what run-scoped caches of weight-dependent
    work key on (``NativeGenerator.RunCache``). The bump PRECEDES the mutation on
    both halves — apply and restore alike — because either can fail partway
    through and the sampler isolates ordinary hook exceptions, so the next forward
    would otherwise read a cache entry keyed on weights the module no longer has.
    ``weight_revision`` is left alone on the success path — it is the cross-run
    identity, and moving it per step would change the trajectory warm-start key
    under an unchanged schedule. A FAILED apply or restore forces it forward too:
    a half-patched or half-restored module is no longer the base stack the loader
    stamped, so no trajectory keyed on that stack may be resumed again.
    """

    # Ahead of preview/progress so a step's weights are settled before anything
    # observes the step; ordering is not load-bearing for correctness (no other
    # hook reads model weights) but keeps the trace easy to read.
    priority = 900

    def __init__(self, dit: "NativeModel", loras: Sequence[WindowedLora]) -> None:
        self._dit = dit
        self._module = dit.module
        self._loras = tuple(loras)
        self._base = snapshot_lora_state(self._module)
        self._applied: Tuple[int, ...] = ()
        self._dirty = False
        self._closed = False
        self._total_steps = 0
        #: Whether a sampler ever dispatched to this hook. False after a
        #: completed generation means the sampling call never received the hook,
        #: i.e. the windows were silently ignored — see
        #: ``FlowMatchGeneratorPipe.generation_scope``.
        self.started = False
        #: Per-adapter application evidence (see ``AdapterApplication``) for
        #: whichever window is CURRENTLY applied — refreshed only on an actual
        #: transition (``_sync`` already no-ops when ``wanted`` is unchanged),
        #: so a run sitting inside one window for many steps computes this
        #: once, not per step. Cleared to ``()`` on a failed apply — a
        #: half-patched module has no evidence describing it as applied.
        self.last_application: Tuple[AdapterApplication, ...] = ()

    def on_start(self, total_steps: int) -> None:
        self.started = True
        self._total_steps = int(total_steps)
        self._warn_unreachable()
        self._sync(0)

    def on_step(self, step_index, total_steps, x, sigma, denoised_x0) -> None:
        self._total_steps = int(total_steps)
        nxt = int(step_index) + 1
        if nxt >= self._total_steps:
            return  # the run is over; close() owns the final removal
        self._sync(nxt)

    def on_end(self) -> None:
        self.close()

    def close(self, final: bool = False) -> None:
        """Restore the module to its pre-hook LoRA state.

        Idempotent on SUCCESS only: ``_closed`` is set after the restore returns,
        never before it is attempted. A restore that raises leaves the hook dirty
        and open so the next ``close`` retries it, and the exception propagates —
        a half-restored shared model must never be reported as cleanly closed.

        ``final`` is the caller declaring itself the LAST owner of this hook:
        nothing will retry after it, so a failure there leaves windowed patches
        sitting in weights the loader's stamp calls the bare base stack, on a DiT
        shared through the MODELS cache. That is where the wrapper is declared
        unusable (:meth:`NativeModel.mark_unusable`), so the next acquisition
        reloads the checkpoint instead of sampling it. Only the owning ``finally``
        passes it — ``on_end`` does not, because the sampler swallows its
        exception and the owner's close still gets a turn.

        Whether the sampler reached ``on_end`` at all is exactly what must not be
        assumed: a sampler that dispatches ``on_start`` before the ``try`` guarding
        its loop (``euler_cfg_pp`` allocates ``s_in`` between the two) can apply a
        window and then leave without an ``on_end``, so the owner's close is the
        only one there ever was. Counting attempts would read that as a first,
        retryable failure and leave the wrapper unmarked.
        """
        if self._closed:
            return
        if not self._dirty:
            self._closed = True
            return
        try:
            # A FRESH epoch, not the pre-apply value. Restoring from the base
            # snapshot is believed exact, but "believed" is not "verified" across
            # the quantised runtime-delta path, and a wrong reuse here is a
            # silently wrong image. A fresh epoch costs at most a recompute of
            # run-scoped work nothing is going to ask for again — the run is over.
            self._restore("windowed LoRA restored at run end")
        except Exception as exc:
            if final:
                self._dit.mark_unusable(f"a windowed LoRA could not be removed: {exc}")
            raise
        self._closed = True

    def _restore(self, reason: str) -> None:
        """Undo this hook's patches, invalidating BEFORE the first weight moves.

        ``restore_lora_state`` walks Linears one at a time, so a failure partway
        through leaves a module that is neither the patched nor the base state.
        The effective bump therefore precedes the attempt, not follows it: the
        sampler isolates ordinary hook exceptions, so the very next forward can
        read a run-scoped cache, and an entry keyed on the pre-restore revision
        would answer it from weights that no longer exist. A failure additionally
        forces the CROSS-run identity forward for the same reason the failed-apply
        path does — a half-restored module is not the base stack the loader
        stamped, so no trajectory keyed on it may ever be resumed. ``_dirty``
        survives a failure so the next attempt retries the whole restore.
        """
        self._dit.bump_effective_revision(reason)
        # Cleared BEFORE the attempt, same as the apply path: whether the
        # restore lands cleanly (nothing is applied any more) or dies partway
        # (the module is now ambiguous, not describable by the OLD evidence
        # either), the evidence for what used to be applied is stale either way.
        self.last_application = ()
        try:
            restore_lora_state(self._base)
        except Exception:
            self._dit.bump_weight_revision(f"{reason}: restore failed, module half-restored")
            raise
        self._applied = ()
        self._dirty = False

    def _warn_unreachable(self) -> None:
        for index, (_sd, _weight, window) in enumerate(self._loras):
            if window.start > self._total_steps:
                logger.warning(
                    "LoRA #%d window (%s) starts past the run's %d step(s) — it will never apply",
                    index, window.describe(), self._total_steps,
                )

    def _active_at(self, step_index: int) -> Tuple[int, ...]:
        return tuple(i for i, (_sd, _w, window) in enumerate(self._loras)
                     if window.contains(step_index))

    def _sync(self, step_index: int) -> None:
        """Make the module's windowed patches match the active set at ``step_index``."""
        wanted = self._active_at(step_index)
        if wanted == self._applied:
            return
        if self._dirty:
            self._restore(f"windowed LoRA cleared entering step {step_index}")
        if not wanted:
            self.last_application = ()
            return
        stack: List[Tuple[Dict[str, torch.Tensor], float]] = [
            (self._loras[i][0], self._loras[i][1]) for i in wanted
        ]
        names = [f"window-lora[{i}]" for i in wanted]
        # Marked dirty BEFORE the patch: a mid-apply failure still needs
        # close() to run the restore that undoes the partial work.
        self._dirty = True
        # Revised BEFORE the patch for the same reason: a partial apply must not
        # leave any cache able to answer from the pre-apply weights. A failure
        # additionally forces the CROSS-run identity forward, since a half-patched
        # module is no longer the base stack the loader stamped.
        self._dit.bump_effective_revision(f"windowed LoRA set {list(wanted)} entering step {step_index}")
        self.last_application = ()
        try:
            _patched, _unmatched, report = apply_loras_with_report(self._module, stack, names=names)
        except Exception:
            self._dit.bump_weight_revision("windowed LoRA apply failed, module half-patched")
            raise
        self._applied = wanted
        self.last_application = tuple(report)
        logger.debug("step %d: windowed LoRA set -> %s", step_index, list(wanted))
