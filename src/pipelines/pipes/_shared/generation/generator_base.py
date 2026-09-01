"""Shared base class for seed-loop generator pipes.

Model-specific generators implement only `build_context` (normalize inputs
into a per-invocation context) and `generate_one` (produce a single item for
a given seed); the base class owns the seed-planning loop, cancellation
support, and default Gallery/seed-param result emission.

Declaring `is_cancelled` in `process`'s signature is what makes migrated
generators cancellable for free: GenerationEngine introspects the pipe's
`process` signature and only passes `is_cancelled` through when the pipe
declares it (see src/features/generation/generation.py).
"""
from contextlib import nullcontext
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from src.pipelines.outputs import GalleryGenerationOutput, ParamGenerationOutput, VideoGenerationOutput
from src.pipelines.contracts import BasePipe
from src.pipelines.contracts import PipeInput, PipeOutput
from src.pipelines.pipes._shared.generation.progress import ProgressEmitter
from src.pipelines.pipes._shared.generation.seed_plan import plan_seeds


def emit_gallery(
        generation_outputs: callable,
        images: List[Any],
        seeds: Optional[List[int]] = None,
        videos: Optional[List[Any]] = None,
        video_resolution: Optional[Tuple[int, int]] = None,
) -> None:
    """Standalone live-preview Gallery(+ optional seed Param) emission.

    Usable directly by generator pipes that don't inherit
    `BaseGeneratorPipe`'s full seed loop (e.g. generator/sdxl, which keeps
    its own per-mode `process()` architecture but still wants the shared
    emission shape). Pass `seeds=None` to skip the seed ParamGenerationOutput
    entirely, matching pipes whose existing contract never emitted one.

    The emitted media is `temporary` (live preview only): the terminal
    `gallery` pipe (`src/pipelines/pipes/gallery/main.py`) is the single
    node that persists a generation's final artifact, so a generator that
    also persisted here would save every image/video twice.

    `videos`, when given, is a list of video file paths (as produced by
    `encode_frames_to_mp4`), each wrapped in a `VideoGenerationOutput`.
    `images` accepts the pre-wrapped `ImageGenerationOutput`/raw-image list.
    `video_resolution`, when given, is stamped onto every emitted
    `VideoGenerationOutput` so the live WebSocket message carries dimensions
    before the file is persisted and re-probed; one `(width, height)` per
    call is correct because a single `process()` renders every video at the
    same configured resolution.
    """
    video_outputs = (
        [VideoGenerationOutput(video_path=v, temporary=True, resolution=video_resolution) for v in videos]
        if videos else []
    )
    generation_outputs(GalleryGenerationOutput(images=images, videos=video_outputs))
    if seeds is not None:
        generation_outputs(ParamGenerationOutput(name="seed", values=seeds))


def _never_cancelled() -> bool:
    return False


@dataclass
class GeneratorContext:
    """Per-invocation context built once by `build_context` and threaded
    through the seed loop into `generate_one`.

    `quantity` and `input_seeds` drive the seed plan; `extra` is free-form
    pipe-specific payload (conditioning, input images, mask, ...).

    `is_cancelled` is the manager's cancellation probe. `process()` stashes
    it here (always callable, even when the manager passed no `is_cancelled`
    at all) right after `build_context` returns, so `generate_one` -- and
    anything it calls down to a per-step sampling loop -- can observe
    cancellation without every subclass having to override `process()` to
    thread an extra parameter through, the way the pre-migration generators
    did.
    """
    quantity: int
    input_seeds: Optional[List[int]] = None
    extra: Any = None
    is_cancelled: Callable[[], bool] = field(default=_never_cancelled)


class BaseGeneratorPipe(BasePipe):
    """Owns the seed-plan loop, cancellation, and default result emission
    shared by seed-based generator pipes ("one seed -> one generated item").
    """

    def build_context(self, pipe_input: PipeInput) -> GeneratorContext:
        """Normalize pipe_input into a GeneratorContext.

        Override to pull model-specific inputs (conditioning, images, mask,
        ...) into `ctx.extra` and to determine `quantity`/`input_seeds`.
        """
        raise NotImplementedError

    def generate_one(self, ctx: GeneratorContext, index: int, seed: int, progress: ProgressEmitter) -> Any:
        """Generate a single item for `seed`. Must be implemented by subclasses."""
        raise NotImplementedError

    def extra_step_hooks(self) -> Tuple[Any, ...]:
        """Sampler step hooks this pipe adds on top of progress/preview, valid
        for the sampling call currently in flight. Empty by default; overridden
        by pipes that steer the loop (step-windowed LoRA). Read by
        `native_step_hooks`, so both the txt2img and img2img paths pick it up.
        """
        return ()

    def generation_scope(self, ctx: GeneratorContext, index: int):
        """Context manager wrapping ONE `generate_one` call, for per-item state
        that must be torn down even when generation raises.

        Wrapped at the seed loop rather than inside `generate_one` on purpose:
        subclasses (and plugin pipes) routinely override `generate_one`
        wholesale, and an override must not be able to silently opt out of a
        teardown that protects shared, cached state.
        """
        return nullcontext()

    def emit_results(self, generation_outputs: callable, results: List[Any], used_seeds: List[int]) -> None:
        """Default: emit a GalleryGenerationOutput of images plus a "seed"
        ParamGenerationOutput. Override for audio/video outputs, or to
        change/skip the seed param emission to match a pipe's existing
        contract (e.g. some generators never emitted a seed param).
        """
        emit_gallery(generation_outputs, results, used_seeds)

    def build_output(self, results: List[Any]) -> Dict[str, Any]:
        """Default PipeOutput payload: {"image": [...]}. Override for
        audio/video pipes producing a different output key."""
        return {"image": [getattr(r, "image", r) for r in results]}

    def process(
            self,
            pipe_input: PipeInput,
            generation_outputs: callable,
            is_cancelled: Optional[callable] = None,
    ) -> PipeOutput:
        ctx = self.build_context(pipe_input)
        ctx.is_cancelled = is_cancelled or _never_cancelled
        seeds = plan_seeds(ctx.input_seeds, int(self.config.get("seed", -1)), ctx.quantity)
        progress = ProgressEmitter(generation_outputs, title=self.name)

        results: List[Any] = []
        used_seeds: List[int] = []
        try:
            for i, seed in enumerate(seeds):
                if ctx.is_cancelled():
                    break
                with self.generation_scope(ctx, i):
                    result = self.generate_one(ctx, i, seed, progress)
                if ctx.is_cancelled():
                    # A cancellation observed mid-`generate_one` (e.g. a
                    # sampling loop that noticed and raced past its own
                    # SamplingCancelled) must not turn into a normal result --
                    # otherwise a half-sampled item still reaches the gallery.
                    break
                results.append(result)
                used_seeds.append(seed)
        except Exception:
            # A failed generation must not leave the engine's DiT/VAE resident —
            # release GPU VRAM before the exception propagates (the app was seen
            # holding ~30GB after a decode OOM). See `_release_gpu_on_error`.
            self._release_gpu_on_error(ctx)
            raise

        self.emit_results(generation_outputs, results, used_seeds)
        return PipeOutput(output=self.build_output(results))

    def _release_gpu_on_error(self, ctx: GeneratorContext) -> None:
        """Best-effort GPU cleanup on a failed generation.

        Duck-typed so this shared base stays decoupled from the native engine:
        ``ctx.extra`` itself, and every value inside it when it's a dict (the
        NativeGenerator engine every image-family generator stows there under
        a key), is checked for a ``release_gpu()`` method. The multi-pipe
        video families (Wan/LTX) instead build ``ctx.extra`` as a single
        per-invocation dataclass (conditioning, router, VAE, ...) rather than
        a dict — checking ``ctx.extra`` directly is what makes THIS cleanup
        fire for them too, as long as that dataclass defines its own
        ``release_gpu()``. Never raises — cleanup that raised would mask the
        original generation failure.
        """
        candidates = [ctx.extra]
        if isinstance(ctx.extra, dict):
            candidates.extend(ctx.extra.values())
        for value in candidates:
            release = getattr(value, "release_gpu", None)
            if callable(release):
                try:
                    release()
                except Exception:
                    pass
