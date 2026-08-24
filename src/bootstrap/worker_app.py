"""Worker process FastAPI application factory.

Mirrors ``src.bootstrap.app``, but the worker has no database, no settings
table, and no session/auth model to wire - ``create_worker_app()`` is
deliberately much smaller than ``create_app()``. No heavy model stack is
imported at module level here: probing torch/CUDA happens lazily, inside a
request handler (``capabilities.probe_capabilities``) and at container build
time (a cheap ``torch.cuda.is_available()`` check, never a weight load) - so a
CPU-only container can still import and boot this module.
"""

from __future__ import annotations

from typing import Optional

from fastapi import FastAPI

from src.bootstrap.worker_container import WorkerContainer, build_worker_container
from src.features.remote_execution.worker.config import WorkerConfig
from src.features.remote_execution.worker.routes import build_worker_router


def create_worker_app(
    config: Optional[WorkerConfig] = None,
    container: Optional[WorkerContainer] = None,
) -> FastAPI:
    resolved_container = container or build_worker_container(config or WorkerConfig.from_env())

    app = FastAPI(title="PotionUI Remote Native Worker", version="1")
    app.state.worker_container = resolved_container
    app.include_router(build_worker_router(resolved_container))

    @app.get("/health")
    async def health():
        return {"status": "ok", "worker_id": resolved_container.config.worker_id}

    return app
