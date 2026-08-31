"""Router registration for the FastAPI app.

Each feature exposes a `build_router(container)` factory (some also expose
`build_ws_router` / `build_admin_router`) that constructs its router bound to
controllers drawn from the container. Registration order is load-bearing:
`model_collection_router` must precede `model_router` (see the inline comment)
or its paths are shadowed.
"""

from fastapi import FastAPI

from src.bootstrap.container import AppContainer

from src.features.auth.routes import build_router as build_auth_router
from src.features.setup.routes import build_router as build_setup_router
from src.features.users.routes import build_router as build_user_router
from src.features.sessions.routes import build_router as build_session_router
from src.features.workspaces.routes import build_router as build_workspace_router
from src.features.presets.routes import build_router as build_preset_router
from src.features.forms.routes import build_router as build_form_router
from src.features.fields.routes import build_router as build_field_router
from src.features.docs.routes import build_router as build_docs_router
from src.features.downloads.routes import (
    build_router as build_downloads_router,
    build_ws_router as build_downloads_ws_router,
)
from src.features.generation.routes import (
    build_router as build_generation_router,
    build_ws_router as build_generation_ws_router,
    build_admin_router as build_generation_admin_router,
)
from src.features.settings.routes import (
    build_router as build_settings_router,
    build_admin_router as build_settings_admin_router,
)
from src.features.stats.routes import build_router as build_stats_router
from src.features.backends.routes import build_router as build_backend_router
from src.features.provisioning.routes import build_admin_router as build_provisioning_admin_router
from src.features.remote_execution.routes import build_admin_router as build_remote_models_admin_router
from src.features.media.routes import build_router as build_media_router
from src.features.media.editing.routes import build_router as build_media_edit_router
from src.features.library.routes import build_router as build_library_router
from src.features.inspirations.routes import (
    build_router as build_inspiration_router,
    build_media_router as build_inspiration_media_router,
)
from src.features.system_monitor.routes import (
    build_router as build_system_router,
    build_ws_router as build_system_ws_router,
)
from src.features.llm.routes import build_router as build_llm_router
from src.features.llm.tools.governance_routes import build_router as build_tool_governance_router
from src.features.chat.routes import build_router as build_chat_router
from src.features.segments.routes import build_router as build_segment_router
from src.features.phrasebook.routes import build_router as build_phrasebook_router
from src.features.user_groups.routes import build_router as build_user_group_router
from src.features.model_library.routes import build_router as build_model_collection_router
from src.features.models.routes import build_router as build_model_router
from src.features.tags.routes import build_router as build_tag_router
from src.features.collections.routes import build_router as build_collection_router
from src.features.models.dictionary_routes import build_router as build_dictionary_router
from src.features.developer.routes import build_router as build_developer_router
from src.features.pipes.routes import build_router as build_pipe_router
from src.features.plugins.routes import build_router as build_plugin_router
from src.features.providers.routes import build_router as build_provider_router
from src.features.settings.admin_websocket import build_router as build_admin_ws_router
from src.features.keybindings.routes import build_router as build_keybinding_router
from src.features.prompt_database.routes import build_router as build_prompt_database_router
from src.features.media_index.routes import build_router as build_media_index_router
from src.features.notifications.routes import (
    build_router as build_notification_router,
    build_ws_router as build_notification_ws_router,
)
from src.features.automation.routes import (
    build_router as build_automation_router,
    build_ws_router as build_automation_ws_router,
)
from src.features.mcp.routes import build_router as build_mcp_router


def register_routers(app: FastAPI, container: AppContainer) -> None:
    """Build every feature router from the container and include it on `app`,
    preserving registration order."""
    app.include_router(build_auth_router(container))  # Authentication routes (no auth required)
    app.include_router(build_setup_router(container))  # Public first-run setup status (no auth)
    app.include_router(build_user_router(container))  # User management routes
    app.include_router(build_session_router(container))  # Session management routes
    app.include_router(build_workspace_router(container))  # Workspace management routes
    app.include_router(build_preset_router(container))
    app.include_router(build_form_router(container))
    app.include_router(build_field_router(container))
    app.include_router(build_docs_router(container))  # Documentation feature endpoints
    app.include_router(build_downloads_router(container))  # Download queue endpoints
    app.include_router(build_downloads_ws_router(container))  # Download WebSocket
    app.include_router(build_generation_router(container))
    app.include_router(build_generation_ws_router(container))  # WebSocket routes
    app.include_router(build_generation_admin_router(container))  # /api/admin/generations (global, run reports)
    app.include_router(build_settings_router(container))
    app.include_router(build_settings_admin_router(container))  # /api/admin app-level actions (restart, ...)
    app.include_router(build_stats_router(container))
    app.include_router(build_backend_router(container))  # All backend endpoints consolidated here
    app.include_router(build_provisioning_admin_router(container))  # Compute provisioning (admin-only)
    app.include_router(build_remote_models_admin_router(container))  # Remote worker model sync (admin-only)
    app.include_router(build_media_router(container))  # Media endpoints
    app.include_router(build_media_edit_router(container))  # Crop/trim/rotate a library resource
    app.include_router(build_library_router(container))  # User media library
    app.include_router(build_inspiration_router(container))  # Inspirations - cross-user publishing
    app.include_router(build_inspiration_media_router(container))  # Serves copied inspiration media
    app.include_router(build_system_router(container))
    app.include_router(build_system_ws_router(container))  # System WebSocket
    app.include_router(build_llm_router(container))
    app.include_router(build_tool_governance_router(container))  # /api/llm/configurations/{id}/toolset (admin) + /api/llm/toolset/preferences (user)
    app.include_router(build_chat_router(container))
    app.include_router(build_segment_router(container))
    app.include_router(build_phrasebook_router(container))
    app.include_router(build_user_group_router(container))
    app.include_router(build_model_collection_router(container))  # Model collection endpoints - MUST precede model_router so
                                                                  # /api/models/collections isn't swallowed by GET /api/models/{model_id}
    app.include_router(build_model_router(container))  # Model management endpoints (prefix already defined in router)
    app.include_router(build_tag_router(container))  # Tag management endpoints (prefix defined in router)
    app.include_router(build_collection_router(container))  # Collection/album management endpoints (prefix defined in router)
    app.include_router(build_dictionary_router(container), prefix="/api/dictionaries", tags=["Dictionaries"])
    app.include_router(build_developer_router(container))
    app.include_router(build_pipe_router(container))  # Pipe requirement installation (admin only)
    app.include_router(build_plugin_router(container))  # Plugin management endpoints
    app.include_router(build_provider_router(container))  # Provider management endpoints (prefix already defined in router)
    app.include_router(build_admin_ws_router(container))  # Admin WebSocket endpoint
    app.include_router(build_keybinding_router(container))  # Keybinding management endpoints
    app.include_router(build_prompt_database_router(container))  # Prompt database endpoints
    app.include_router(build_media_index_router(container))  # Media index (system tags) admin endpoints
    app.include_router(build_notification_router(container))  # Notification endpoints
    app.include_router(build_notification_ws_router(container))  # Notification WebSocket endpoint
    app.include_router(build_automation_router(container))  # Automation endpoints
    app.include_router(build_automation_ws_router(container))  # Automation WebSocket endpoint
    app.include_router(build_mcp_router(container))  # MCP tokens + JSON-RPC endpoint
