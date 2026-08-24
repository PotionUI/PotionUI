"""`pipeline.render` - a dry-run of the recipe's preset pipeline through the
canonical `PipelineBuilder`, with fixture form data. No GPU work, no backend
call, no real generation: this only proves the preset's Jinja2 templates
render to a valid pipe list before the wizard promises the owner a working
setup.

Fixture-form-data construction lives in `_fixture_form.py`, shared with
`generation.smoke` (which runs the same kind of fixture form through a real
generation instead of just the builder).
"""

from __future__ import annotations

from src.features.generation.pipeline_builder import PipelineBuilder
from src.features.presets.loader import PresetTemplateLoader
from src.features.setup.executors._fixture_form import build_fixture_form_data
from src.features.setup.executors.base import StepContext, StepResult
from src.platform.templating import TemplateProcessor


class PipelineRenderExecutor:
    """Builds fixture form data for one preset/mode and renders it through the
    real `PipelineBuilder`. Never touches a backend, a model file, or the GPU."""

    def __init__(
        self,
        preset_template_loader: PresetTemplateLoader,
        template_processor: TemplateProcessor,
        pipeline_builder: PipelineBuilder,
    ):
        self.preset_template_loader = preset_template_loader
        self.template_processor = template_processor
        self.pipeline_builder = pipeline_builder

    def execute(self, context: StepContext) -> StepResult:
        preset_id = context.step.params.get("preset_id")
        mode = context.step.params.get("mode")
        if not preset_id or not mode:
            return StepResult.fail(
                "PIPELINE_RENDER_MISCONFIGURED",
                "This step doesn't say which preset and mode to check.",
            )

        preset_template = self.preset_template_loader.load_preset_by_id(preset_id)
        if preset_template is None:
            return StepResult.fail(
                "PRESET_MISSING_ON_DISK",
                f"The preset this setup needs ('{preset_id}') isn't available on this installation.",
            )
        if mode not in preset_template.modes:
            return StepResult.fail(
                "MODE_NOT_FOUND",
                f"Preset '{preset_template.name}' has no '{mode}' mode to check.",
            )

        try:
            form_data = self._build_fixture_form_data(preset_template, mode)
            built = self.pipeline_builder.build_pipeline(
                preset_template, form_data, mode=mode, user_id=context.owner_user_id
            )
        except Exception as exc:
            return StepResult.fail(
                "PIPELINE_RENDER_FAILED",
                (
                    f"The '{mode}' setup for '{preset_template.name}' didn't check out: {exc} "
                    "This usually points at a problem in the preset itself, not your setup."
                ),
                suggested_repair="Report this to the preset's maintainer, or try a different recipe.",
            )

        return StepResult.ok(
            {
                "preset_id": preset_id,
                "mode": mode,
                "pipe_count": len(built.pipes),
            }
        )

    def _build_fixture_form_data(self, preset_template, mode: str):
        return build_fixture_form_data(
            preset_template,
            mode,
            preset_template_loader=self.preset_template_loader,
            template_processor=self.template_processor,
        )
