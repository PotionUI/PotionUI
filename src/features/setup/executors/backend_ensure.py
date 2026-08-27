"""`backend.ensure` - confirm a backend exists for the recipe's engine.

The native engine auto-provisions its own backend row the first time anything
asks `BackendConfigStore` for the configured backends (see
`BackendConfigStore._load_backends`, which inserts a "native" row if none
exists yet) - and `BackendRegistry` already does that once at process start.
So for `engine: native` this step is mostly a confirmation.

For a plugin-provided engine (e.g. `comfyui`) with no backend configured yet,
this fails with a plain pointer at Administration -> Backends: creating a
*remote* backend needs admin-supplied connection details (host, port, API
key) this recipe cannot invent. A recipe that wants to *detect* an
already-running server rather than blindly create one is a wave-2 concern -
see the T3.1 deliverable notes for why `content/recipes/marketplace/comfyui-detect.yml` isn't
shipped in this wave.
"""

from __future__ import annotations

from src.features.backends.backend_registry import BackendRegistry
from src.features.setup.executors.base import StepContext, StepResult


class BackendEnsureExecutor:
    def __init__(self, backend_registry: BackendRegistry):
        self.backend_registry = backend_registry

    def execute(self, context: StepContext) -> StepResult:
        engine = context.step.params.get("engine") or context.recipe.engine
        backends = self.backend_registry.get_backends_for_engine(engine)
        if backends:
            backend = backends[0]
            return StepResult.ok(
                {
                    "engine": engine,
                    "backend_id": backend.backend_id,
                    "backend_name": backend.name,
                }
            )

        return StepResult.fail(
            "NO_BACKEND_FOR_ENGINE",
            f"No backend is configured for the '{engine}' engine yet.",
            suggested_repair="Open Administration -> Backends and add or enable one for this engine.",
        )
