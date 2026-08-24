"""`generation.smoke` - prove the setup actually produces an image, by running
one, for real, through the normal generation path (`GenerationOrchestrator.
start_generation` - the exact entry point the generation API route uses, not
a side door). No fakes here; tests around this executor fake the orchestrator
itself at the constructor boundary (see `tests/features/setup/executors/
test_generation_smoke.py`) rather than ever touching a real backend/GPU.

Success is "the generation reached `completed` and produced at least one
non-temporary output" - `min_outputs: 1` in spirit, mirroring
`scripts/preset_test_suite.py`'s own case-outcome check. Form data comes from
the same fixture-building `_fixture_form.py` shares with `pipeline.render`,
overlaid with the recipe's `smoke:` section (small resolution/step count,
kept fast on purpose) - see `Recipe.smoke` / `RecipeSmokeRef` in
`recipe_schema.py`.

A missing backend/GPU is a normal, expected failure mode for a fresh
install - it is reported as a plain sentence with a `suggested_repair`, never
an unhandled traceback (the orchestrator's own errors are caught here).

Model-typed fields must resolve to a REAL indexed model, not `_fixture_form.py`'s
`/SETUP-CHECK` dry-run placeholder, or the loader receives a bogus checkpoint
path. This executor passes its own `recipe`/`model_repository` into
`build_fixture_form_data` (see `_fixture_form._resolve_model_fields`); a field
whose recipe artifact isn't indexed yet fails the step up front via
`RequiredModelMissing`, naming the missing file, instead of reaching the loader.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from src.features.presets.loader import PresetTemplateLoader
from src.features.setup.executors._async_bridge import run_sync
from src.features.setup.executors._fixture_form import RequiredModelMissing, build_fixture_form_data
from src.features.setup.executors.base import StepContext, StepResult
from src.platform.templating import TemplateProcessor

_TERMINAL_STATES = {"completed", "failed", "cancelled"}
_TIMEOUT_SECONDS = 300


class GenerationSmokeExecutor:
    def __init__(
        self,
        preset_template_loader: PresetTemplateLoader,
        template_processor: TemplateProcessor,
        generation_orchestrator: Any,
        file_repository: Any = None,
        model_repository: Any = None,
    ):
        self.preset_template_loader = preset_template_loader
        self.template_processor = template_processor
        self.orchestrator = generation_orchestrator
        if file_repository is None:
            from src.features.generation.file_repository import FileRepository

            file_repository = FileRepository()
        self.file_repository = file_repository
        if model_repository is None:
            from src.features.models.repository import model_repo as model_repository
        self.model_repository = model_repository

    def execute(self, context: StepContext) -> StepResult:
        preset_id = context.step.params.get("preset_id")
        mode = context.step.params.get("mode")
        if not preset_id or not mode:
            return StepResult.fail(
                "GENERATION_SMOKE_MISCONFIGURED",
                "This step doesn't say which preset and mode to run.",
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
                f"Preset '{preset_template.name}' has no '{mode}' mode to run.",
            )

        smoke = context.recipe.smoke
        overrides = dict(smoke.form) if smoke else {}
        if smoke and smoke.seed is not None:
            overrides.setdefault("seed", smoke.seed)

        try:
            form_data = build_fixture_form_data(
                preset_template,
                mode,
                preset_template_loader=self.preset_template_loader,
                template_processor=self.template_processor,
                overrides=overrides,
                recipe=context.recipe,
                model_repository=self.model_repository,
            )
        except RequiredModelMissing as exc:
            return StepResult.fail(
                "GENERATION_SMOKE_MODEL_MISSING",
                f"The test generation needs '{exc.display_name}' ({exc.filename}), "
                "but it isn't indexed on this installation yet.",
                suggested_repair=(
                    "Make sure the file is in your models folder, then retry this step "
                    "(or run 'Find models you already have' again first)."
                ),
            )
        except Exception as exc:
            return StepResult.fail(
                "GENERATION_SMOKE_FORM_FAILED",
                f"Couldn't prepare a test generation for '{preset_template.name}': {exc}",
            )

        try:
            outcome = run_sync(
                self._run(
                    preset_id=preset_id,
                    mode=mode,
                    form_data=form_data,
                    user_id=context.owner_user_id,
                    prompt=smoke.prompt if smoke else "",
                    negative_prompt=smoke.negative_prompt if smoke else "",
                )
            )
        except Exception as exc:
            return StepResult.fail(
                "GENERATION_SMOKE_FAILED",
                f"Running a test generation failed: {exc}",
                suggested_repair=(
                    "Make sure a backend for this preset's engine is running and reachable, "
                    "then retry this step."
                ),
            )

        if outcome["state"] != "completed" or outcome["output_count"] < 1:
            detail = outcome.get("error") or f"the generation ended as '{outcome['state']}' with no output"
            return StepResult.fail(
                "GENERATION_SMOKE_FAILED",
                f"The test generation didn't produce an image: {detail}.",
                suggested_repair=(
                    "Check that a backend for this preset's engine (a GPU worker, or a configured "
                    "server) is running, then retry this step."
                ),
            )

        filename, file_path = self._first_output_file(outcome["generation_id"])
        return StepResult.ok(
            {
                "generation_id": outcome["generation_id"],
                "output_count": outcome["output_count"],
                "preset_id": preset_id,
                "mode": mode,
                "filename": filename,
                "file_path": file_path,
            }
        )

    def _first_output_file(self, generation_id: Optional[str]):
        """The saved output's filename/path (see `src.features.generation.
        file_repository.FileRepository`), so the UI can show the first image
        without a second round trip - `None`/`None` if lookup fails (the
        generation itself already succeeded; this is best-effort)."""
        if generation_id is None:
            return None, None
        try:
            files = self.file_repository.get_generation_files(generation_id)
        except Exception:
            return None, None
        if not files:
            return None, None
        first = files[0]
        file_path = getattr(first, "file_path", None)
        filename = file_path.rsplit("/", 1)[-1] if file_path else None
        return filename, file_path

    async def _run(
        self,
        *,
        preset_id: str,
        mode: str,
        form_data: Dict[str, Any],
        user_id: Optional[str],
        prompt: str,
        negative_prompt: str,
    ) -> Dict[str, Any]:
        from src.features.generation.dto import GenerationRequest, PromptPair
        from src.pipelines.outputs import ErrorGenerationOutput, GalleryGenerationOutput, ImageGenerationOutput

        output_count = 0
        error_box: Dict[str, str] = {}
        done_event = asyncio.Event()

        async def _collect(generation_id: str, output) -> None:
            nonlocal output_count
            # The orchestrator AWAITS this callback (see
            # `preset_suite.runner.HeadlessGenerationClient._collect`, the
            # pattern this mirrors) - it must be a coroutine. The final
            # `(generation_id, None)` call is the authoritative completion
            # signal, fired for both success and failure.
            if output is None:
                done_event.set()
                return
            if isinstance(output, ImageGenerationOutput) and not output.temporary:
                output_count += 1
            elif isinstance(output, GalleryGenerationOutput):
                output_count += len(output.images)
            elif isinstance(output, ErrorGenerationOutput):
                error_box["error"] = getattr(output, "error", "generation error")

        prompts = None
        if prompt or negative_prompt:
            prompts = [PromptPair(positive=prompt or "", negative=negative_prompt or "")]

        request = GenerationRequest(preset_id=preset_id, mode=mode, form_data=form_data, prompts=prompts)
        result = await self.orchestrator.start_generation(request, user_id or "setup", _collect)
        generation_id = result.get("generation_id")

        try:
            await asyncio.wait_for(self._wait_for_completion(done_event, generation_id), timeout=_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            return {
                "generation_id": generation_id,
                "state": "failed",
                "output_count": output_count,
                "error": f"timed out waiting for completion after {_TIMEOUT_SECONDS}s",
            }

        state = await self._terminal_state(generation_id)
        return {
            "generation_id": generation_id,
            "state": state,
            "output_count": output_count,
            "error": error_box.get("error"),
        }

    async def _wait_for_completion(self, done_event: asyncio.Event, generation_id: Optional[str]) -> None:
        while True:
            if done_event.is_set():
                return
            if await self._terminal_state(generation_id) in _TERMINAL_STATES:
                return
            try:
                await asyncio.wait_for(done_event.wait(), timeout=0.5)
                return
            except asyncio.TimeoutError:
                continue

    async def _terminal_state(self, generation_id: Optional[str]) -> Optional[str]:
        if generation_id is None:
            return None
        status = await self.orchestrator.get_generation_status(generation_id)
        if status is None:
            return None
        if isinstance(status, dict):
            return status.get("status")
        if hasattr(status, "model_dump"):
            return status.model_dump().get("status")
        state = getattr(status, "state", None)
        return getattr(state, "value", None)
