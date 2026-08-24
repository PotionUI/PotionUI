"""Remote Native worker process entry point.

Thin wrapper over the worker application factory, mirroring ``api.py``: the
composition root lives in ``src.bootstrap`` (worker_container + worker_app);
this module only loads the environment and exposes the module-level ``app``
object ``uvicorn worker:app`` imports.

Binds ``127.0.0.1`` by default - never ``0.0.0.0`` - so a worker started on a
shared or rented host is not reachable off-box without an operator explicitly
choosing ``POTIONUI_WORKER_HOST``.
"""

import logging
import os

from dotenv import load_dotenv
load_dotenv()

import uvicorn

from src.bootstrap.worker_app import create_worker_app
from src.features.remote_execution.worker.config import WorkerConfig

_config = WorkerConfig.from_env()
app = create_worker_app(_config)


if __name__ == "__main__":
    log_level_name = os.environ.get("POTIONUI_LOG_LEVEL", "INFO").strip().upper()
    log_level = getattr(logging, log_level_name, logging.INFO)
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s | %(levelname)8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    uvicorn.run(
        app,
        host=_config.host,
        port=_config.port,
        reload=False,
        workers=1,
        log_level="info",
    )
