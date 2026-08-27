"""PotionUI API entry point.

Thin wrapper over the application factory. The composition root lives in
`src.bootstrap` (container + create_app); this module only loads the
environment and exposes the module-level `app` object that `uvicorn api:app`
(see run.sh) imports.
"""

import logging
import os

# Load environment variables from .env file
from dotenv import load_dotenv
load_dotenv()

import uvicorn

from src.bootstrap.app import create_app

app = create_app()


if __name__ == "__main__":
    _log_level_name = os.environ.get("POTIONUI_LOG_LEVEL", "INFO").strip().upper()
    _log_level = getattr(logging, _log_level_name, None)
    if not isinstance(_log_level, int):
        _log_level = logging.INFO

    # Configure more detailed logging
    logging.basicConfig(
        level=_log_level,
        format='%(asctime)s | %(levelname)8s | %(name)s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # Create a logger for API requests/responses
    api_logger = logging.getLogger("api.requests")
    api_logger.setLevel(_log_level)

    # Note: Using reload=False to prevent multiple processes from being created
    # This ensures that there is only one instance of the ConnectionHub class
    # If you need to reload the server during development, restart it manually
    #
    # Pass the `app` object directly (not the "api:app" import string): running
    # `python api.py` executes this file as `__main__`, so an import string would
    # make uvicorn re-import it as a *separate* `api` module and run the whole
    # module body a second time - re-registering builtin field types onto the
    # shared registry singleton and crashing with DuplicateFieldTypeError.
    # The object form is safe here because reload/workers are disabled.
    #
    # Binds 127.0.0.1 by default - never 0.0.0.0 - matching worker.py; LAN
    # access needs POTIONUI_HOST=0.0.0.0 set explicitly (run.sh --lan does
    # this itself by invoking uvicorn directly, bypassing this block).
    uvicorn.run(
        app,
        host=os.environ.get("POTIONUI_HOST", "127.0.0.1"),
        port=8005,
        reload=False,  # Changed from True to False to prevent multiple processes
        workers=1,     # Explicitly set to 1 worker to prevent multiple processes
        log_level="info",
        # Optimize for concurrent connections and reduce blocking
        limit_concurrency=1000,  # Allow more concurrent connections
        limit_max_requests=10000,  # Handle more requests before restarting
        timeout_keep_alive=30,  # Keep connections alive longer
        timeout_graceful_shutdown=30,  # Allow time for graceful shutdown
        # Enable HTTP/1.1 pipelining for better performance
        http="httptools",
        loop="uvloop" if hasattr(__import__('uvloop'), 'install') else "asyncio"
    )
