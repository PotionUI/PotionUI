"""FastAPI application factory.

`create_app()` assembles the application: run migrations, build the singleton
container, register error handlers / middleware / routers, mount plugin
routers, and expose the health endpoint.

Each feature exposes a `build_router(container)` factory that constructs its
router bound to controllers drawn from the container; `register_routers` calls
them in order.
"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI

import src.platform.plugins.runtime_registries as _rr
from src.bootstrap.container import AppContainer, build_container
from src.bootstrap.secrets_preflight import run_secret_preflight
from src.bootstrap.errors import register_error_handlers
from src.bootstrap.middleware import register_middleware
from src.bootstrap.routers import register_routers
from src.bootstrap.static_frontend import mount_frontend

from src.platform.security.current_user import set_auth
from src.platform.version import POTIONUI_VERSION
from src.platform.websocket.notification_connection_hub import notification_connection_hub
from src.platform.websocket.automation_connection_hub import automation_connection_hub


# Define Swagger tags for endpoint grouping
tags_metadata = [
    {
        "name": "System & Health",
        "description": "Health checks, system monitoring, and GPU statistics",
    },
    {
        "name": "Stats",
        "description": "Usage statistics and generation metrics",
    },
    {
        "name": "authentication",
        "description": "User registration, login, and access tokens",
    },
    {
        "name": "users",
        "description": "User account administration",
    },
    {
        "name": "user-groups",
        "description": "User group management and membership",
    },
    {
        "name": "Presets",
        "description": "Preset management, configuration, and dynamic form schemas",
    },
    {
        "name": "Forms",
        "description": "Dynamic form handling, validation, and field options",
    },
    {
        "name": "Fields",
        "description": "Form field-type registry",
    },
    {
        "name": "Generation",
        "description": "Image generation operations, status tracking, and history",
    },
    {
        "name": "Media",
        "description": "Serving generated images, uploads, temporary files, and preset assets",
    },
    {
        "name": "Collections",
        "description": "User-curated collections of generated images",
    },
    {
        "name": "Model Collections",
        "description": "User-curated collections of models",
    },
    {
        "name": "Workspaces",
        "description": "Workspaces for organizing saved work",
    },
    {
        "name": "Settings",
        "description": "Application configuration, models, and system preferences",
    },
    {
        "name": "Backends",
        "description": "Backend management, deployment, and connection testing",
    },
    {
        "name": "LLM",
        "description": "Language model configurations, generation, and prompt management",
    },
    {
        "name": "Chat",
        "description": "AI chat sessions, tools, resources, and persistent memory",
    },
    {
        "name": "Prompts",
        "description": "Saved prompt database with import and semantic search",
    },
    {
        "name": "Phrasebook",
        "description": "Phrasebook categories and values for prompt authoring",
    },
    {
        "name": "Segments",
        "description": "Saved reusable prompt segments",
    },
    {
        "name": "Segment Templates",
        "description": "Reusable segment templates",
    },
    {
        "name": "Segment Categories",
        "description": "Categories for organizing saved segments",
    },
    {
        "name": "Models",
        "description": "Model indexing, management, and provider integration",
    },
    {
        "name": "Downloads",
        "description": "Download queue, history, and settings for model/media fetches",
    },
    {
        "name": "Providers",
        "description": "Marketplace provider management for fetching model metadata",
    },
    {
        "name": "Plugins",
        "description": "Plugin discovery, configuration, and static assets",
    },
    {
        "name": "Automations",
        "description": "Node-graph automations, triggers, and execution",
    },
    {
        "name": "Notifications",
        "description": "User notifications and delivery",
    },
    {
        "name": "Keybindings",
        "description": "Customizable keyboard shortcuts",
    },
    {
        "name": "Documentation",
        "description": "Live reference documentation for pipes, techniques, and models",
    },
    {
        "name": "Developer",
        "description": "Developer tools: template-function docs and preset/doc linting",
    },
    {
        "name": "WebSocket",
        "description": "Real-time WebSocket connections for live updates",
    },
    {
        "name": "Sessions",
        "description": "Session management for saving and loading preset configurations",
    },
    {
        "name": "Tags",
        "description": "Tag management for models and generations",
    },
    {
        "name": "Dictionaries",
        "description": "Reference data and lookup values for frontend components",
    },
]

# API description shown on /docs.
_API_DESCRIPTION = """
    ## AI Image Generation API with Dynamic Preset System

    PotionUI is a powerful AI image generation platform that supports multiple diffusion models
    (Stable Diffusion, FLUX, etc.) with a unique dynamic preset system for different model configurations.

    ### Key Features
    - **Dynamic Presets**: YAML-based configurations with custom forms
    - **Pipeline Architecture**: Modular processing with configurable pipes
    - **Real-time Updates**: WebSocket support for generation progress
    - **Multi-backend Support**: Local and cloud backend deployment
    - **LLM Integration**: AI-powered prompt enhancement and commands

    ### Getting Started
    1. Browse available presets in the **Presets** section
    2. Configure your settings in the **Settings** section
    3. Start generating images using the **Generation** endpoints
    4. Monitor progress via **WebSocket** connections
    """


def run_migrations_sync():
    """Run database migrations synchronously before container construction (DI needs DB access)."""
    from src.platform.database import migration_runner

    logging.info("Checking database migrations...")
    if migration_runner.has_pending_migrations():
        logging.info("Pending migrations found, applying...")
        migration_runner.run_migrations()
        logging.info("Database initialization completed")
    else:
        logging.info("Database already up to date")


# Environment variable -> `settings` table key. Both settings are read fresh
# from the database by every call site that needs them (Settings,
# ModelScanner, ArtifactsFetchExecutor, ...) rather than cached off the
# container, so the only way to redirect them consistently for a whole
# process is to overwrite the row itself before anything reads it - a plain
# env var read at one call site would leave the others pointed at the old
# value. See src/platform/settings/settings.py's `get_models_dir` /
# `get_file_storage_directory` for the read side.
_ENV_SETTING_OVERRIDES = {
    "POTIONUI_MODELS_DIR": "models_dir",
    "POTIONUI_STORAGE_PATH": "file_storage_directory",
}


def apply_startup_env_overrides() -> None:
    """Redirect DB-backed settings from the environment, before the container
    (and everything it constructs from `settings.get_setting(...)` at
    build time, e.g. `ModelDirectories`) is built.

    `models_dir` and `file_storage_directory` are ordinary admin-editable
    settings seeded by migration with a fixed default (`"models"` /
    `"storage"`). This is the seam the ephemeral onboarding harness
    (`tests/e2e/harness/onboarding_e2e.py`) and any other disposable/test
    instance use to point a *fresh* instance at their own directories (a temp
    dir's storage, a read-only model depot) without a running instance to
    call the settings API on.

    No-op when none of `_ENV_SETTING_OVERRIDES`'s variables are set - a normal
    deployment never touches this. Must run after `run_migrations_sync()`
    (the `settings` table must exist) and before `build_container()` (so the
    override is what gets read at construction time).
    """
    overrides = {
        key: os.environ[env_var]
        for env_var, key in _ENV_SETTING_OVERRIDES.items()
        if os.environ.get(env_var)
    }
    if not overrides:
        return

    from src.platform.settings.repository import SettingRepository

    setting_repository = SettingRepository()
    for key, value in overrides.items():
        setting_repository.update_setting_value_by_key(key, value)
    logging.info(
        "Applied startup settings overrides from the environment: %s",
        {k: v for k, v in overrides.items()},
    )


def _seed_runtime_from_container(container: AppContainer) -> None:
    """Bind process-wide dependencies that live outside the router factories."""
    # Auth dependency (get_current_active_user) resolves through this Auth.
    set_auth(container.auth)

    # Seed the in-memory attention-backend pin from its persisted setting.
    # get_attention_backend() never reads the DB (it's a per-forward hot path);
    # this is the one place the setting is loaded into memory. Non-fatal if the
    # setting or DB isn't ready yet - the dispatcher just falls back to "auto".
    try:
        from src.platform.runtime.native import attention as native_attention

        native_attention.set_backend_override(
            container.settings.get_setting("native_attention_backend")
        )
    except Exception as e:
        logging.warning(f"Could not seed native attention backend pin: {e}")

    # Same pattern for the engine-flag toggles: the hot paths read module-level
    # overrides, seeded once here from their persisted settings. An empty/missing
    # setting leaves the override clear, so the env vars keep deciding.
    try:
        from src.platform.runtime.native.memory import partial as native_partial
        from src.platform.runtime.native.optimizations import compile as native_compile

        native_compile.set_torch_compile_override(
            container.settings.get_setting("native_torch_compile")
        )
        native_partial.set_stream_prefetch_override(
            container.settings.get_setting("native_stream_prefetch")
        )
    except Exception as e:
        logging.warning(f"Could not seed native engine flags: {e}")

    # Setup claim token lifecycle. While the instance has no owner, ensure a
    # one-time token exists on disk (0600) so a remote operator can claim it via
    # the bootstrap CLI; once claimed, make sure no stale token lingers. Never
    # logs the token value itself (only its path, inside ClaimTokenStore).
    try:
        if container.instance_claim_repository.is_claimed():
            container.claim_token_store.clear()
        else:
            container.claim_token_store.ensure_token()
    except Exception as e:
        logging.warning(f"Could not initialize the setup claim token: {e}")


def create_app(container: Optional[AppContainer] = None) -> FastAPI:
    """Build and return the fully-assembled FastAPI application."""
    # Run database migrations BEFORE building the container (it needs DB access).
    run_migrations_sync()
    apply_startup_env_overrides()

    if container is None:
        container = build_container()

    # Publish the live container so plugin backends can reach process singletons.
    _rr._container = container

    run_secret_preflight(
        container.plugin_repository,
        container.backend_registry.backend_config_store,
        container.llm_repository.config_repo,
        container.plugin_registry,
    )

    automation_runtime = container.automation_runtime

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """Application lifespan events"""
        # Startup
        from src.bootstrap.middleware import DEBUG_MODE
        logging.info("Starting PotionUI API server...")
        logging.info(f"DEBUG mode: {'ENABLED' if DEBUG_MODE else 'DISABLED'} (set DEBUG=true to enable verbose logging)")
        # Note: Migrations are run synchronously before app creation (see run_migrations_sync())

        # Capture the running loop so NotificationConnectionHub.schedule_send()
        # can bridge sync->async sends from worker threads (e.g. generation
        # completion) via run_coroutine_threadsafe.
        notification_connection_hub.set_loop(asyncio.get_running_loop())

        # Same for the automation module: bind the loop to both the WS broadcast
        # bridge and the engine (enqueue_trigger is sync-callable from any
        # thread - watchdog's observer thread, a schedule loop, etc.), then start
        # every enabled automation's triggers.
        automation_connection_hub.set_loop(asyncio.get_running_loop())
        automation_runtime.engine.set_loop(asyncio.get_running_loop())
        await automation_runtime.start_all_enabled()

        # Start the download worker (on its own persistent loop) so downloads
        # interrupted by a restart resume without waiting for a queue call.
        await container.download_queue.start()

        # Generation state lives only in-process, so a pending/running row
        # surviving a restart means the process died mid-generation, not that
        # it's still running. Fail those before the frontend can adopt one.
        reconciled = container.generation_repository.reconcile_interrupted_generations()
        if reconciled:
            logging.info(f"Reconciled {reconciled} interrupted generation(s) as failed after restart")

        # Same idea for native.remote rows: reclaim any lease a dead process
        # left dangling, expire anything past its package deadline, fail
        # anything that exhausted its attempts, and best-effort resume the
        # event history of whatever is still genuinely in flight on a worker.
        # Bounded per-row (RemoteExecutionReconciler's own timeout) so one
        # unreachable worker cannot hold up startup.
        try:
            from src.features.remote_execution.reconciler import RemoteExecutionReconciler

            reconciler = RemoteExecutionReconciler(
                backend_config_store=container.backend_registry.backend_config_store,
            )
            await reconciler.reconcile()
        except Exception as exc:
            logging.error(f"Remote execution reconciliation failed at startup: {exc}")

        # Heartbeat for rented compute: a pod paused or deleted in the
        # provider's console is reflected on its row (and its backend
        # disabled) within one interval, not on the next admin click.
        container.compute_status_monitor.start()

        yield

        # Shutdown
        logging.info("Shutting down PotionUI API server...")
        await container.compute_status_monitor.stop()
        await container.download_queue.stop()
        await automation_runtime.stop_all()

    # Create FastAPI app
    app = FastAPI(
        title="PotionUI API",
        description=_API_DESCRIPTION,
        version=POTIONUI_VERSION,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_tags=tags_metadata
    )

    # Global exception handlers for better error reporting
    register_error_handlers(app)

    # Middleware for compression, CORS, docs headers, and logging
    register_middleware(app)

    # Bind process-wide dependencies, then build and include every router from
    # the container.
    _seed_runtime_from_container(container)
    register_routers(app, container)

    # Health check endpoint
    @app.get("/health", tags=["System & Health"], summary="Health Check")
    async def health_check():
        """Check if the API service is running and healthy."""
        return {"status": "healthy", "service": "potionui-api"}

    # Mount plugin API routers (dynamically - PluginRouterMounter tracks which
    # routes belong to which plugin so operations.enable_plugin/disable_plugin
    # can mount/unmount them again at runtime without restarting the process)
    try:
        plugin_registry = container.plugin_registry
        plugin_router_mounter = container.plugin_router_mounter
        plugin_router_mounter.attach(app)
        plugin_router_mounter.mount_all_enabled(
            plugin_registry.get_enabled_plugins(), loader=plugin_registry.loader
        )
    except Exception as e:
        logging.error(f"Failed to mount plugin routers: {e}")

    # Serve the built SPA (frontend/build), if it exists, so production is a
    # single process. Must be last: the catch-all route only ever sees
    # requests every router above (including plugin routers) didn't match.
    mount_frontend(app)

    return app
