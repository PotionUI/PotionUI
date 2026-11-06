"""Shared progress/preview emission for generator pipes.

Wraps the `generation_outputs` callable so pipes stop hand-constructing the
same `ProgressGenerationOutput` / temporary-preview `ImageGenerationOutput`
shapes over and over.
"""
from typing import Optional

from src.pipelines.outputs import ProgressGenerationOutput, ImageGenerationOutput, PreviewClipGenerationOutput
from src.pipelines.outputs import Progress, Icon


class ProgressEmitter:
    """Emits progress/state/preview outputs for a single pipe invocation."""

    def __init__(self, generation_outputs: callable, title: Optional[str] = None):
        # Public: pipes that must hand the raw callback through to a
        # lower-level API (e.g. a Model wrapper's txt2img/img2img) can use
        # `progress.emit` directly instead of re-threading generation_outputs.
        self.emit = generation_outputs
        self._raw_emit = generation_outputs
        self._title = title

    def scope_item(self, index: int, count: int, per_prompt: Optional[int] = None, prompt_count: int = 0) -> None:
        if count <= 1:
            self.emit = self._raw_emit
            return

        def emit(output) -> None:
            if isinstance(output, ProgressGenerationOutput):
                self._annotate(output, index, count, per_prompt, prompt_count)
            self._raw_emit(output)

        self.emit = emit

    @staticmethod
    def _annotate(output, index, count, per_prompt, prompt_count) -> None:
        output.item_index = index
        output.item_count = count
        if per_prompt is not None:
            output.prompt_index = min(index // per_prompt, prompt_count - 1)
            output.prompt_count = prompt_count
        if output.progress is not None and output.bar and output.progress.max > 0:
            fraction = min(1.0, max(0.0, output.progress.current / output.progress.max))
            within = index % per_prompt if per_prompt else 0
            output.item_progress = (within + fraction) / per_prompt if per_prompt else fraction
            output.progress = Progress(round((index + fraction) * 10000), count * 10000)

    def step(
            self,
            current: int,
            total: int,
            state: str = "",
            icon: Optional[Icon] = None,
            segment_id: Optional[str] = None,
    ) -> None:
        """Emit a ProgressGenerationOutput for step `current` of `total`.

        `segment_id` names the Video Director segment this step belongs to,
        for a multi-segment chain/window generation -- see
        `ProgressGenerationOutput.segment_id`. Omitted (`None`) for every
        non-Director pipe.
        """
        self.emit(ProgressGenerationOutput(
            title=self._title,
            state=state,
            icon=icon,
            progress=Progress(current=current, max=total),
            segment_id=segment_id,
            current_step_num=current,
            total_steps=total,
        ))

    def state(self, message: str, icon: Optional[Icon] = None) -> None:
        """Emit a ProgressGenerationOutput carrying only a state message (no progress bar)."""
        self.emit(ProgressGenerationOutput(title=self._title, state=message, icon=icon))

    def preview(self, image, **meta) -> None:
        """Emit a temporary (workbench-only) image preview."""
        self.emit(ImageGenerationOutput(image=image, temporary=True, **meta))

    def preview_clip(self, clip) -> None:
        self.emit(PreviewClipGenerationOutput(
            sprite=clip.sprite,
            encoded=clip.encoded,
            frames=clip.frames,
            columns=clip.columns,
            frame_width=clip.frame_width,
            frame_height=clip.frame_height,
            fps=clip.fps,
            step=clip.step,
            total_steps=clip.total_steps,
            source_indices=clip.source_indices,
            long_edge=clip.long_edge,
            update_index=clip.update_index,
            updates_max=clip.updates_max,
            every=clip.every,
        ))


def native_step_hooks(gen, progress: "ProgressEmitter", on_progress, *, preview: bool = True, decoder_path=None, extra=()):
    """Sampler hook list for a native generator: progress + (optional) preview.

    Every native generator wires the same ``[ProgressHook(on_progress)]``; this
    adds the live workbench preview (``PreviewHook`` decoding the running x0 to a
    cheap RGB image via the DiT family's latent factors) when ``preview`` is on and
    the family has vendored factors. Unknown families / an absent spec yield just
    the progress hook, so a caller unconditionally passes the returned list as
    ``hooks=``. ``make_preview_hook`` guards decode+emit, and the sampler isolates
    hook failures regardless, so a preview error can never break generation.

    ``extra`` appends pipe-supplied hooks (see
    ``BaseGeneratorPipe.extra_step_hooks``) — the one place both the txt2img and
    img2img sampling paths agree on, so a pipe declaring a hook gets it in both.
    """
    from src.platform.runtime.native.sampling import ProgressHook, make_preview_hook

    hooks = [ProgressHook(on_progress)]
    spec = getattr(gen, "spec", None)
    if preview and spec is not None:
        preview_hook = make_preview_hook(spec, progress.preview, decoder_path=decoder_path)
        if preview_hook is not None:
            hooks.append(preview_hook)
    hooks.extend(extra)
    return hooks
