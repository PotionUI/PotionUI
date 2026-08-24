"""`models.index_backend` - ask a specific backend what it can load (contrast
with `models.index`'s filesystem-only `ModelScanner`; see that executor's own
docstring). Wraps `src.features.models.backend_indexer.BackendModelIndexer.
index_backend`, the same per-backend availability indexing the admin
"Index models" button already runs (see the automation `action.index_models`
node for the other caller of this same indexer)."""

from __future__ import annotations

from src.features.backends.backend_registry import BackendRegistry
from src.features.setup.executors._async_bridge import run_sync
from src.features.setup.executors.base import StepContext, StepResult


class ModelsIndexBackendExecutor:
    def __init__(self, backend_registry: BackendRegistry, backend_model_indexer=None):
        self.backend_registry = backend_registry
        if backend_model_indexer is None:
            from src.features.models.backend_indexer import backend_model_indexer as _default

            backend_model_indexer = _default
        self.backend_model_indexer = backend_model_indexer

    def execute(self, context: StepContext) -> StepResult:
        engine = context.step.params.get("engine") or context.recipe.engine
        backends = self.backend_registry.get_backends_for_engine(engine)
        if not backends:
            return StepResult.fail(
                "NO_BACKEND_FOR_ENGINE",
                f"No backend is configured for the '{engine}' engine yet, so its models can't be indexed.",
                suggested_repair="Open Administration -> Backends and add or enable one for this engine.",
            )

        try:
            result = run_sync(self.backend_model_indexer.index_backend(backends[0]))
        except Exception as exc:
            return StepResult.fail(
                "MODEL_INDEX_FAILED",
                f"Listing models from the '{engine}' backend failed: {exc}",
                suggested_repair="Make sure the backend is reachable, then retry this step.",
            )

        return StepResult.ok(result.to_dict())
