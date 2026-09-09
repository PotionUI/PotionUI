"""Recipe step executors: one `kind` (see `schema.RECOGNIZED_STEP_KINDS`) ->
one `StepExecutor`. `build_default_executor_registry` wires the built-ins onto
a `RecipeExecutorRegistry` and is the one thing `src/bootstrap/container.py`
needs to call.

`workspace.activate` is deliberately absent: it belongs to the first-run
wizard, so `src.features.setup` registers it onto this registry at bootstrap.
Setup depends on recipes, never the reverse.
"""

from __future__ import annotations

from typing import Any, Optional

from src.features.recipes.catalog import RecipeCatalog
from src.features.recipes.executors.artifacts_fetch import ArtifactsFetchExecutor
from src.features.recipes.executors.artifacts_plan import ArtifactsPlanExecutor
from src.features.recipes.executors.backend_detect import BackendDetectExecutor
from src.features.recipes.executors.backend_ensure import BackendEnsureExecutor
from src.features.recipes.executors.base import StepContext, StepExecutor, StepResult
from src.features.recipes.executors.deferred import DeferredStepExecutor
from src.features.recipes.executors.generation_smoke import GenerationSmokeExecutor
from src.features.recipes.executors.models_index import ModelsIndexExecutor
from src.features.recipes.executors.models_index_backend import ModelsIndexBackendExecutor
from src.features.recipes.executors.pipeline_render import PipelineRenderExecutor
from src.features.recipes.executors.plugins_ensure import PluginsEnsureExecutor
from src.features.recipes.executors.preset_ensure import PresetEnsureExecutor
from src.features.recipes.executors.registry import RecipeExecutorRegistry, StepKind
from src.features.recipes.schema import DEFERRED_STEP_KINDS

__all__ = [
    "StepContext",
    "StepExecutor",
    "StepResult",
    "StepKind",
    "RecipeExecutorRegistry",
    "PluginsEnsureExecutor",
    "BackendEnsureExecutor",
    "BackendDetectExecutor",
    "ModelsIndexExecutor",
    "ModelsIndexBackendExecutor",
    "PresetEnsureExecutor",
    "PipelineRenderExecutor",
    "ArtifactsPlanExecutor",
    "ArtifactsFetchExecutor",
    "GenerationSmokeExecutor",
    "DeferredStepExecutor",
    "build_default_executor_registry",
]


def build_default_executor_registry(
    *,
    recipe_catalog: RecipeCatalog,
    plugin_registry,
    backend_registry,
    preset_collaborators,
    user_repository,
    preset_template_loader,
    template_processor,
    pipeline_builder,
    model_repository: Optional[Any] = None,
    generation_orchestrator: Optional[Any] = None,
    download_queue: Optional[Any] = None,
    provider_registry_factory: Optional[Any] = None,
    file_repository: Optional[Any] = None,
    backend_model_indexer: Optional[Any] = None,
    step_kind_registry: Optional[Any] = None,
) -> RecipeExecutorRegistry:
    """Wire the built-in step executors onto a fresh registry.

    Composition-root helper (see `src/bootstrap/container.py`): every
    executor receives its collaborators here, by constructor, rather than
    reaching for a service locator. `model_repository`/`generation_orchestrator`/
    `file_repository` default to their real singletons when omitted
    (constructed lazily inside the executor) so existing callers/tests that
    don't care about those kinds don't need to pass them.
    """
    if model_repository is None:
        from src.features.models.repository import ModelRepository

        model_repository = ModelRepository()

    executors = {
        "plugins.ensure": PluginsEnsureExecutor(plugin_registry),
        "backend.ensure": BackendEnsureExecutor(backend_registry),
        "backend.detect": BackendDetectExecutor(backend_registry),
        "models.index": ModelsIndexExecutor(),
        "models.index_backend": ModelsIndexBackendExecutor(backend_registry, backend_model_indexer),
        "preset.ensure": PresetEnsureExecutor(preset_collaborators, user_repository),
        "pipeline.render": PipelineRenderExecutor(
            preset_template_loader, template_processor, pipeline_builder
        ),
        "artifacts.plan": ArtifactsPlanExecutor(
            model_repository, provider_registry_factory=provider_registry_factory
        ),
        "artifacts.fetch": ArtifactsFetchExecutor(
            download_queue, model_repository, provider_registry_factory=provider_registry_factory
        ),
    }
    if generation_orchestrator is not None:
        executors["generation.smoke"] = GenerationSmokeExecutor(
            preset_template_loader,
            template_processor,
            generation_orchestrator,
            file_repository,
            model_repository=model_repository,
        )
    else:
        # No orchestrator wired (e.g. a minimal test container) - a recipe
        # referencing `generation.smoke` still lints fine, it just can't run
        # until this is provided.
        executors["generation.smoke"] = DeferredStepExecutor("generation.smoke")

    for kind in DEFERRED_STEP_KINDS:
        executors.setdefault(kind, DeferredStepExecutor(kind))

    return RecipeExecutorRegistry(
        recipe_catalog, executors, step_kind_registry=step_kind_registry
    )
