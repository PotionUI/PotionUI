"""Shared base class for model/checkpoint loader pipes.

C4 migrated every loader pipe onto `MODELS.acquire()` inline, and they all
converged on the same shape: emit a progress message, emit a
ModelsGenerationOutput describing what's being loaded, then acquire (or
directly load, if no MODELS service was injected — e.g. isolated pipe
tests). This lifts that shared shape into a base class; subclasses implement
only the model-specific parts.
"""
from typing import Any, Dict, List, Optional

from src.pipelines.outputs import ModelsGenerationOutput, ProgressGenerationOutput
from src.pipelines.contracts import BasePipe
from src.pipelines.contracts import PipeInput, PipeOutput
from src.pipelines.pipes._shared.generation.loader_helpers import COLD_LOAD_NOTE


class BaseModelLoaderPipe(BasePipe):
    """Owns the MODELS-acquire + ModelsGenerationOutput emission shared by
    model/checkpoint loader pipes.
    """

    def progress_message(self) -> str:
        """Message for the ProgressGenerationOutput emitted before loading."""
        raise NotImplementedError

    def describe_models(self) -> List[Any]:
        """Return the list of ModelGenerationOutput entries describing what's
        being loaded (checkpoint, active LoRAs, etc.)."""
        raise NotImplementedError

    def cache_key(self) -> str:
        """Stable MODELS.acquire() cache key for this loader (e.g. "checkpoint_loader/sdxl")."""
        raise NotImplementedError

    def fingerprint(self) -> str:
        """Fingerprint capturing everything that should bust the cache on change
        (paths, active LoRAs, device/dtype, ...)."""
        raise NotImplementedError

    def load_model(self, pipe_input: PipeInput) -> Any:
        """Actually construct/load the model. Only called on a cache miss (or
        when no MODELS service is available)."""
        raise NotImplementedError

    def after_acquire(self, model: Any, pipe_input: PipeInput, fingerprint: str) -> None:
        """Optional hook run after acquire/load, on both cache hits and misses
        (e.g. re-applying a cheap idempotent `model.load(mode=...)` to a reused
        model, as checkpoint_loader/sdxl and checkpoint_loader/chroma do)."""
        pass

    def build_output(self, model: Any, pipe_input: PipeInput, fingerprint: str) -> Dict[str, Any]:
        """Default PipeOutput payload: {"model": model}. Override to add
        additional outputs (e.g. text_encoder/vae)."""
        return {"model": model}

    def validate(self) -> None:
        """Optional pre-flight validation (e.g. rejecting a model_template
        whose `base` doesn't match this loader variant). Runs before any
        output is emitted. Default: no-op."""
        pass

    def process(
            self,
            pipe_input: PipeInput,
            generation_outputs: callable,
    ) -> PipeOutput:
        self.validate()

        models = pipe_input.input.get("MODELS", None)
        state = self.progress_message()
        is_cached = getattr(models, "is_cached", None)
        if callable(is_cached) and not is_cached(self.cache_key()):
            state += COLD_LOAD_NOTE
        generation_outputs(ProgressGenerationOutput(state=state))
        generation_outputs(ModelsGenerationOutput(models=self.describe_models()))

        fingerprint = self.fingerprint()

        if models is not None:
            model = models.acquire(
                key=self.cache_key(),
                fingerprint=fingerprint,
                loader=lambda: self.load_model(pipe_input),
            )
        else:
            # No MODELS service injected (e.g. isolated pipe test) - load
            # directly, no cross-generation reuse.
            model = self.load_model(pipe_input)

        self.after_acquire(model, pipe_input, fingerprint)

        return PipeOutput(output=self.build_output(model, pipe_input, fingerprint))
