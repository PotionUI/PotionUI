"""Shared progress/preview emission for generator pipes.

Wraps the `generation_outputs` callable so pipes stop hand-constructing the
same `ProgressGenerationOutput` / temporary-preview `ImageGenerationOutput`
shapes over and over.
"""
from typing import Optional

from src.pipelines.outputs import ProgressGenerationOutput, ImageGenerationOutput
from src.pipelines.outputs import Progress, Icon


class ProgressEmitter:
    """Emits progress/state/preview outputs for a single pipe invocation."""

    def __init__(self, generation_outputs: callable, title: Optional[str] = None):
        # Public: pipes that must hand the raw callback through to a
        # lower-level API (e.g. a Model wrapper's txt2img/img2img) can use
        # `progress.emit` directly instead of re-threading generation_outputs.
        self.emit = generation_outputs
        self._title = title

    def step(
            self,
            current: int,
            total: int,
            state: str = "",
            icon: Optional[Icon] = None,
    ) -> None:
        """Emit a ProgressGenerationOutput for step `current` of `total`."""
        self.emit(ProgressGenerationOutput(
            title=self._title,
            state=state,
            icon=icon,
            progress=Progress(current=current, max=total),
        ))

    def state(self, message: str, icon: Optional[Icon] = None) -> None:
        """Emit a ProgressGenerationOutput carrying only a state message (no progress bar)."""
        self.emit(ProgressGenerationOutput(title=self._title, state=message, icon=icon))

    def preview(self, image, **meta) -> None:
        """Emit a temporary (workbench-only) image preview."""
        self.emit(ImageGenerationOutput(image=image, temporary=True, **meta))


def native_step_hooks(gen, progress: "ProgressEmitter", on_progress, *, preview: bool = True, extra=()):
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
        preview_hook = make_preview_hook(spec, progress.preview)
        if preview_hook is not None:
            hooks.append(preview_hook)
    hooks.extend(extra)
    return hooks
