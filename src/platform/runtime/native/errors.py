"""Exceptions for the native engine.

These are intentionally small and dependency-free so every native module can
import them without pulling the rest of the engine in.
"""

from __future__ import annotations

from collections.abc import Iterable

_TRUNCATE = 20


def _fmt_keys(keys: Iterable[str]) -> str:
    keys = list(keys)
    shown = keys[:_TRUNCATE]
    suffix = "" if len(keys) <= _TRUNCATE else f" (+{len(keys) - _TRUNCATE} more)"
    return "[" + ", ".join(shown) + "]" + suffix


class NativeEngineError(Exception):
    """Base class for every native-engine error."""


class NativeEngineUnsupportedError(NativeEngineError):
    """A checkpoint / file type / architecture the native engine cannot handle."""


class HostMemoryExhaustedError(NativeEngineError):
    """Refusing to place a component because host RAM cannot survive it.

    Raised when partial-residency streaming would pin a streamed set too large
    for the free host RAM to absorb (pin + eventual teardown transient). Failing
    the generation with this beats degrading into a state whose teardown gets the
    whole process OOM-killed by the OS.
    """


class SamplingCancelled(NativeEngineError):
    """Raised when a sampling loop observes its cancellation signal.

    Carries the step index at which cancellation was noticed so callers can
    report how far a generation got before being aborted.
    """

    def __init__(self, step_index: int | None = None) -> None:
        self.step_index = step_index
        if step_index is None:
            super().__init__("sampling cancelled")
        else:
            super().__init__(f"sampling cancelled at step {step_index}")


class SamplingNumericsError(NativeEngineError):
    """Raised when the sampling watchdog detects NaN/Inf in the running latent.

    Corrupt numerics (e.g. an attention backend that produces NaNs on a given
    model — the sage2-on-Qwen bug this guards against) otherwise decode to a
    silent black image. Surfacing them as a normal generation failure lets the
    user see *why*. Carries the step index, sampler name, and — when resolvable —
    the active attention backend, so the message reads like "Numerical
    instability at step 4 (sampler=euler, attention=sage2)".

    ``tensor_name`` ("x" | "x0") pins down which of the two tensors the
    watchdog checks went non-finite first — ``x0`` (the model's own denoised
    estimate) points at the model forward itself; ``x`` alone (``x0`` still
    finite) points at the sampler's own predictor/corrector math instead.

    ``switch_step`` (when the run crosses a multi-expert boundary, e.g. Wan's
    ``_ExpertRouter``) is the step index the model switched networks at; the
    active expert at ``step_index`` is then derivable (``"low"`` iff
    ``step_index >= switch_step``, else ``"high"``) and folded into the message.

    ``segment_index``/``segment_label`` (settable after construction — a
    chain-video pipe sequencing multiple independent denoise() calls has no
    way to know its own segment index from inside this generic sampling
    loop) attribute which segment of a multi-segment generation failed, so a
    watchdog trip several steps into segment N+1 is not misread as segment
    N's problem.
    """

    def __init__(
        self,
        step_index: int,
        sampler: str | None = None,
        attention_backend: str | None = None,
        *,
        tensor_name: str | None = None,
        switch_step: int | None = None,
    ) -> None:
        self.step_index = step_index
        self.sampler = sampler
        self.attention_backend = attention_backend
        self.tensor_name = tensor_name
        self.switch_step = switch_step
        self.segment_index: int | None = None
        self.segment_label: str | None = None
        detail = []
        if sampler:
            detail.append(f"sampler={sampler}")
        if attention_backend:
            detail.append(f"attention={attention_backend}")
        if tensor_name:
            detail.append(f"tensor={tensor_name}")
        if switch_step is not None:
            active_expert = "low" if step_index >= switch_step else "high"
            detail.append(f"expert={active_expert}, switch_step={switch_step}")
        suffix = f" ({', '.join(detail)})" if detail else ""
        super().__init__(f"numerical instability (NaN/Inf) at step {step_index}{suffix}")

    def annotate_segment(self, segment_index: int, segment_label: str | None = None) -> "SamplingNumericsError":
        """Attach which segment of a multi-segment (chain-video) generation
        this failure belongs to, and fold it into the message. Called by the
        sequencing pipe, which is the only place that knows its own segment
        index -- this generic sampling loop never sees it."""
        self.segment_index = segment_index
        self.segment_label = segment_label
        suffix = f"segment {segment_index}" + (f" ({segment_label})" if segment_label else "")
        self.args = (f"{self.args[0]}, {suffix}",)
        return self


class PoisonedConditioningError(NativeEngineError):
    """Raised when ``denoise()``'s one-time entry check finds non-finite
    conditioning (NaN/Inf already present in ``cond``/``uncond`` before the
    first sampling step runs).

    Distinguishes "the INPUT was already bad" from "the sampler diverged
    in-loop" (SamplingNumericsError): the same NaN/Inf watchdog symptom a few
    steps in reads very differently depending on which of the two it is —
    e.g. a chain-video continuation whose start-image conditioning came from
    a corrupted previous segment's decode would trip this at step 0 of the
    NEXT segment, not partway through it.
    """

    def __init__(self, which: str, key: str) -> None:
        self.which = which
        self.key = key
        super().__init__(f"non-finite (NaN/Inf) {which} conditioning at denoise() entry (key={key!r})")


class DecodeNumericsError(NativeEngineError):
    """Raised when a VAE decode produces non-finite (NaN/Inf) pixels.

    Mirrors SamplingNumericsError one stage later, at decode instead of at a
    sampling step: `pixels_3thw_to_uint8_frames`'s clamp leaves NaN unchanged
    (`torch.clamp` never touches NaN) and the subsequent `.to(torch.uint8)`
    cast turns that NaN into a silent 0 (black) with no error -- a single
    generation's decode failure would otherwise complete as a bad-looking but
    "successful" video. For a chain-video continuation that silently
    corrupted frame becomes the NEXT segment's start-image conditioning, so
    the failure resurfaces several sampling steps later inside that
    segment's sampler, misattributed away from this decode.
    """

    def __init__(self) -> None:
        super().__init__("VAE decode produced non-finite (NaN/Inf) pixels")


class NativeEngineLoadIntegrityError(NativeEngineError):
    """State-dict load produced key mismatches outside a ModelSpec's allowlist.

    Carries the offending missing/unexpected keys (truncated to the first 20
    each) in the message so the failure is self-describing in logs.
    """

    def __init__(
        self,
        message: str,
        *,
        missing: Iterable[str] = (),
        unexpected: Iterable[str] = (),
    ) -> None:
        self.missing: list[str] = list(missing)
        self.unexpected: list[str] = list(unexpected)
        detail = message
        if self.missing:
            detail += f"\n  missing (not in allowlist): {_fmt_keys(self.missing)}"
        if self.unexpected:
            detail += f"\n  unexpected (not in allowlist): {_fmt_keys(self.unexpected)}"
        super().__init__(detail)
