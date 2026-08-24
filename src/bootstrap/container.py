"""Application composition root.

`build_container()` constructs the process singletons in explicit dependency
order and returns them on a typed `AppContainer`. Construction is ordered:
a singleton may only depend on ones built above it.

Two constructs need care when editing:

- Some managers are wired in two phases — they are constructed first and have
  collaborator references attached afterwards, because the collaborators need
  the manager itself (a dependency cycle that cannot be resolved at
  construction time).
- `BackendRegistry` receives a factory closure rather than an instance: a
  `GenerationManager` is created per backend, not once per process.

Routers and the controllers bound to them are assembled in `src.bootstrap.app`.
"""

from __future__ import annotations

import dataclasses
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import src.platform.plugins.runtime_registries as _rr

from src.features.generation.generation import GenerationManager
from src.features.generation.history_manager import GenerationHistoryManager
from src.features.media.image import ImageManager
from src.platform.templating import TemplateProcessor
from src.platform.runtime.gpu import GpuManager
from src.platform.observability.system_probe import SystemMonitor
from src.platform.runtime.memory import MemoryManager
from src.platform.runtime.model_lifecycle.manager import ModelLifecycleManager
from src.features.models.directory import ModelManager, ModelIndexer
from src.pipelines.catalog import PipeCatalog
from src.pipelines.installer import PipeInstaller
from src.features.pipes import PipeInstallManager
from src.features.presets import PresetTemplateLoader, PresetProcessor
from src.platform.settings.settings import SettingsManager
from src.features.backends.backend_registry import BackendRegistry
from src.platform.plugins import PluginRegistry
from src.features.plugins.manager import PluginManager
from src.platform.plugins.router_manager import PluginRouterManager
from src.features.generation.hooks import OUTPUT_TYPE_HOOKS
from src.platform.security import AuthConfig, PasswordHasher, TokenManager, AuthManager, ClaimTokenManager
from src.features.setup import SetupManager, InstanceClaimRepository
from src.features.setup.run_manager import SetupRunManager
from src.features.setup.recipe_catalog import RecipeCatalog
from src.features.phrasebook import PhrasebookManager
from src.features.phrasebook.preview_generator import PhrasebookPreviewGenerator
from src.features.chat import ChatManager, ResponseProcessor
from src.features.developer import DeveloperManager
from src.features.downloads import DownloadManager, DownloadRepository
from src.features.forms import FormManager
from src.platform.plugins.field_types import FieldTypeRegistry, field_type_registry as _shared_field_type_registry
from src.platform.plugins.prompt_importers import (
    PromptImporterRegistry,
    prompt_importer_registry as _shared_prompt_importer_registry,
)
from src.features.fields.builtin import register_builtin_fields
from src.features.models.attributes.repository import AttributeDefinitionRepository
from src.features.models.attributes.user_repository import UserModelAttributeRepository
from src.features.models.attributes.manager import ModelAttributeDefinitionsManager
from src.features.models.attributes.seeding import ensure_builtin_attribute_definitions
from src.features.llm import LLMManager
from src.features.models import ModelIndexManager
from src.features.presets.manager import PresetManager
from src.features.presets.name_resolver import PresetNameResolver
from src.features.segments import SegmentManager
from src.features.system_monitor import SystemMonitorManager
from src.features.users import UserManager
from src.features.user_groups import UserGroupManager

from src.features.fields.field_factory import FieldFactory
from src.platform.filesystem import FileStore
from src.features.generation.orchestrator import GenerationOrchestrator
from src.features.generation.output_processor import OutputProcessor
from src.features.generation.pipeline_builder import PipelineBuilder
from src.features.generation.status_tracker import GenerationStatusTracker
from src.platform.settings.repository import SettingRepository
from src.features.llm.repository import LLMRepository
from src.features.plugins.repository import PluginRepository
from src.features.users.repository import UserRepository
from src.features.phrasebook.repository import (
    PhrasebookCategoryRepository,
    PhrasebookValueRepository,
)
from src.features.chat.repository import ChatRepository
from src.features.generation.repository import GenerationRepository
from src.features.generation.run_report_repository import GenerationRunReportRepository
from src.features.generation.run_report_recorder import RunReportRecorder
from src.features.segments.repository import (
    SavedSegmentRepository,
    SegmentCategoryRepository,
    SegmentTemplateRepository,
)
from src.features.llm.gateway import LLMGateway

if TYPE_CHECKING:
    # Types constructed inside build_container via lazy imports; referenced only
    # by the AppContainer field annotations (never evaluated at runtime thanks
    # to `from __future__ import annotations`).
    from src.features.llm.tools.registry import ToolRegistry
    from src.features.llm.tools.executor import ToolExecutor
    from src.features.chat.modes import ChatModeRegistry
    from src.platform.resources import ResourceRegistry
    from src.features.notifications.repository import NotificationRepository
    from src.features.notifications import NotificationManager
    from src.features.llm_memory.repository import LLMMemoryRepository
    from src.features.llm_memory import LLMMemoryManager
    from src.platform.websocket.connection_manager import ConnectionManager
    from src.platform.websocket.download_connection_manager import DownloadConnectionManager
    from src.features.models.repository import ModelRepository
    from src.features.tags.repository import TagRepository
    from src.features.model_library.repository.model_collection_repository import ModelCollectionRepository
    from src.features.model_library.repository.user_model_meta_repository import UserModelMetaRepository
    from src.features.model_library import ModelLibraryManager
    from src.features.tags import TagManager
    from src.features.collections.repository import CollectionRepository
    from src.features.collections import CollectionManager
    from src.features.automation.engine import AutomationEngine
    from src.features.automation.manager import AutomationManager
    from src.platform.plugins.automation_templates import AutomationTemplateRegistry
    from src.features.media import MediaManager, MediaTypeResolver, FilePathResolver, ImageProcessor
    from src.features.presets.file_repository import FilePresetRepository
    from src.features.presets.repository import DatabasePresetRepository
    from src.features.prompt_database.manager import PromptDatabaseManager
    from src.features.prompt_database.repository import PromptRepository
    from src.features.prompt_enhancement.repository import EnhancementFeedbackRepository
    from src.features.prompt_enhancement import PromptEnhancementManager
    from src.features.media_index.repository import MediaIndexRepository
    from src.features.media_index.manager import MediaIndexManager
    from src.features.media_index.routes import MediaIndexController
    from src.features.stats.repository import StatsRepository
    from src.features.stats import StatsManager
    from src.features.stats.generation_stats_repository import GenerationStatsRepository
    from src.features.stats.generation_stats_manager import GenerationStatsManager
    from src.features.sessions.repository import SessionRepository
    from src.features.sessions.version_repository import SessionVersionRepository
    from src.features.sessions import SessionManager
    from src.features.workspaces.repository import WorkspaceRepository
    from src.features.workspaces import WorkspaceManager
    from src.features.user_groups.repository import UserGroupRepository
    from src.features.system_monitor.routes import SystemMonitorController
    from src.features.plugins.routes import PluginController
    from src.features.llm.routes import LLMController
    from src.features.llm.tools.governance import ToolGovernanceManager, ToolGovernanceRepository
    from src.features.llm.tools.governance_routes import ToolGovernanceController
    from src.features.users.routes import UserController
    from src.features.notifications.routes import NotificationController
    from src.features.chat.routes import ChatController
    from src.features.chat.turns import ChatTurnRegistry
    from src.features.developer.routes import DeveloperController
    from src.features.docs.routes import DocsController
    from src.features.docs.manager import DocsManager
    from src.features.forms.routes import FormController
    from src.features.fields.routes import FieldController
    from src.features.phrasebook.routes import PhrasebookController
    from src.features.segments.routes import SegmentController
    from src.features.models.routes import ModelController
    from src.features.model_library.routes import ModelCollectionController
    from src.features.tags.routes import TagController
    from src.features.collections.routes import CollectionController
    from src.features.automation.routes import AutomationController
    from src.features.media.routes import MediaController
    from src.features.media.editing.manager import MediaEditManager
    from src.features.media.editing.routes import MediaEditController
    from src.features.library import LibraryManager, LibraryRepository
    from src.features.library.routes import LibraryController
    from src.features.presets.routes import PresetController
    from src.features.prompt_database.routes import PromptDatabaseController
    from src.features.stats.routes import StatsController
    from src.features.sessions.routes import SessionController
    from src.features.workspaces.routes import WorkspaceController
    from src.features.user_groups.routes import UserGroupController


@dataclass
class AppContainer:
    """Typed handle to every process singleton built by `build_container()`.

    Grouped to mirror the sections of the original `AppModule.configure()`.
    Field names match the local variable names in `build_container()`; the
    container is assembled by name at the end of construction.
    """

    # Core registries / settings
    setting_repository: SettingRepository
    settings_manager: SettingsManager
    plugin_registry: PluginRegistry
    plugin_router_manager: PluginRouterManager
    field_type_registry: FieldTypeRegistry
    attribute_definition_repository: "AttributeDefinitionRepository"
    user_model_attribute_repository: "UserModelAttributeRepository"
    model_attributes_manager: "ModelAttributeDefinitionsManager"
    prompt_importer_registry: PromptImporterRegistry
    tool_registry: "ToolRegistry"
    tool_executor: "ToolExecutor"
    chat_mode_registry: "ChatModeRegistry"
    resource_registry: "ResourceRegistry"

    # Core managers
    gpu_manager: GpuManager
    system_monitor: SystemMonitor
    system_monitor_manager: SystemMonitorManager
    memory_manager: MemoryManager
    model_lifecycle_manager: ModelLifecycleManager
    model_manager: ModelManager
    pipe_catalog: PipeCatalog
    pipe_install_manager: PipeInstallManager
    model_indexer: ModelIndexer
    preset_template_loader: PresetTemplateLoader
    preset_processor: PresetProcessor
    template_processor: TemplateProcessor
    image_manager: ImageManager
    backend_registry: BackendRegistry
    field_factory: FieldFactory

    # LLM
    llm_repository: LLMRepository
    llm_service: LLMGateway
    llm_manager: LLMManager
    llm_memory_repository: "LLMMemoryRepository"
    llm_memory_manager: "LLMMemoryManager"

    # Plugins
    plugin_repository: PluginRepository
    plugin_manager: PluginManager
    plugin_controller: "PluginController"
    llm_controller: "LLMController"
    tool_governance_repository: "ToolGovernanceRepository"
    tool_governance_manager: "ToolGovernanceManager"
    tool_governance_controller: "ToolGovernanceController"
    mcp_token_repository: "McpTokenRepository"
    mcp_manager: "McpManager"
    mcp_protocol_manager: "McpProtocolManager"

    # Auth
    user_repository: UserRepository
    instance_claim_repository: InstanceClaimRepository
    claim_token_manager: ClaimTokenManager
    auth_config: AuthConfig
    password_hasher: PasswordHasher
    token_manager: TokenManager
    auth_manager: AuthManager
    setup_manager: SetupManager
    setup_run_manager: SetupRunManager
    recipe_catalog: RecipeCatalog
    user_manager: UserManager
    user_controller: "UserController"

    # Downloads
    download_repository: DownloadRepository
    download_connection_manager: "DownloadConnectionManager"
    download_manager: DownloadManager

    # Notification
    notification_repository: "NotificationRepository"
    notification_manager: "NotificationManager"
    notification_controller: "NotificationController"

    # Phrasebook
    phrasebook_category_repo: PhrasebookCategoryRepository
    phrasebook_value_repo: PhrasebookValueRepository
    phrasebook_manager: PhrasebookManager
    phrasebook_preview_generator: PhrasebookPreviewGenerator

    # Chat
    chat_repository: ChatRepository
    response_processor: ResponseProcessor
    chat_manager: ChatManager
    chat_turn_registry: "ChatTurnRegistry"
    chat_controller: "ChatController"

    # Developer / docs
    developer_manager: DeveloperManager
    developer_controller: "DeveloperController"
    docs_manager: "DocsManager"
    docs_controller: "DocsController"

    # Forms / fields
    form_manager: FormManager
    form_controller: "FormController"
    field_controller: "FieldController"

    # Generation
    generation_status_tracker: GenerationStatusTracker
    pipeline_builder: PipelineBuilder
    output_processor: OutputProcessor
    connection_manager: "ConnectionManager"
    generation_orchestrator: GenerationOrchestrator
    file_service: FileStore
    phrasebook_controller: "PhrasebookController"
    generation_repository: GenerationRepository
    generation_history_manager: GenerationHistoryManager
    run_report_repository: GenerationRunReportRepository
    run_report_recorder: RunReportRecorder

    # Segments
    segment_category_repo: SegmentCategoryRepository
    saved_segment_repo: SavedSegmentRepository
    segment_template_repo: SegmentTemplateRepository
    segment_manager: SegmentManager
    segment_controller: "SegmentController"

    # Model index / library
    model_repository: "ModelRepository"
    tag_repository: "TagRepository"
    model_index_manager: ModelIndexManager
    model_controller: "ModelController"
    model_collection_repository: "ModelCollectionRepository"
    user_model_meta_repository: "UserModelMetaRepository"
    model_library_manager: "ModelLibraryManager"
    model_collection_controller: "ModelCollectionController"

    # Tags
    tag_manager: "TagManager"
    tag_controller: "TagController"

    # Collections
    collection_repository: "CollectionRepository"
    collection_manager: "CollectionManager"
    collection_controller: "CollectionController"

    # Automation
    automation_engine: "AutomationEngine"
    automation_manager: "AutomationManager"
    automation_controller: "AutomationController"
    automation_template_registry: "AutomationTemplateRegistry"

    # Media
    media_type_resolver: "MediaTypeResolver"
    file_resolver: "FilePathResolver"
    image_processor: "ImageProcessor"
    media_manager: "MediaManager"
    media_controller: "MediaController"
    media_edit_manager: "MediaEditManager"
    media_edit_controller: "MediaEditController"

    # Library
    library_repository: "LibraryRepository"
    library_manager: "LibraryManager"
    library_controller: "LibraryController"

    # Inspirations
    inspiration_repository: "InspirationRepository"
    inspiration_manager: "InspirationManager"
    inspiration_controller: "InspirationController"

    # Presets
    file_preset_repository: "FilePresetRepository"
    database_preset_repository: "DatabasePresetRepository"
    preset_manager: PresetManager
    preset_controller: "PresetController"

    # Prompt database / enhancement
    prompt_repository: "PromptRepository"
    prompt_database_manager: "PromptDatabaseManager"
    prompt_database_controller: "PromptDatabaseController"
    enhancement_feedback_repository: "EnhancementFeedbackRepository"
    prompt_enhancement_manager: "PromptEnhancementManager"

    # Media index (system tags + reusable index queue)
    media_index_repository: "MediaIndexRepository"
    media_index_manager: "MediaIndexManager"
    media_index_controller: "MediaIndexController"

    # Stats
    stats_repository: "StatsRepository"
    stats_manager: "StatsManager"
    stats_controller: "StatsController"
    # Durable generation_stats store (separate from the StatsRepository above,
    # which aggregates the live `generations` table).
    generation_stats_repository: "GenerationStatsRepository"
    generation_stats_manager: "GenerationStatsManager"

    # Sessions
    session_repository: "SessionRepository"
    # Session history (versions) -- separate repository over session_versions
    # (migration 092); see src/features/sessions/manager.py.
    session_version_repository: "SessionVersionRepository"
    session_manager: "SessionManager"
    session_controller: "SessionController"

    # Workspaces
    workspace_repository: "WorkspaceRepository"
    workspace_manager: "WorkspaceManager"
    workspace_controller: "WorkspaceController"

    # User groups
    user_group_repository: "UserGroupRepository"
    user_group_manager: UserGroupManager
    user_group_controller: "UserGroupController"

    # System monitor controller (built alongside the manager, above)
    system_monitor_controller: "SystemMonitorController"


def _sync_enabled_plugins(registry: PluginRegistry, plugin_repo: PluginRepository):
    """Sync enabled plugins from database to registry on startup.

    `registry.enable_plugin` only registers a plugin's handlers; the
    `plugin.lifecycle.enable` chain is not run here, because a restart is not a
    disabled->enabled transition. Per-process initialization is
    `plugin.lifecycle.boot`, fired for every enabled plugin once the whole set
    is up so a boot handler can see its peers.
    """
    import logging
    logger = logging.getLogger(__name__)

    try:
        # Get all plugins from database that are enabled
        all_plugins = plugin_repo.get_all_plugins()
        enabled_count = 0

        for plugin in all_plugins:
            if plugin.enabled:
                # Enable the plugin in the registry
                if registry.enable_plugin(plugin.id):
                    enabled_count += 1
                    logger.info(f"Enabled plugin from database: {plugin.id}")
                else:
                    logger.warning(f"Failed to enable plugin from database: {plugin.id}")

        logger.info(f"Synced {enabled_count} enabled plugins from database")

        registry.run_boot_hooks()

    except Exception as e:
        logger.error(f"Error syncing enabled plugins: {e}")


def build_container() -> AppContainer:
    # Initialize settings repository and manager first
    setting_repository = SettingRepository()
    settings_manager = SettingsManager(setting_repository)
    models_dir = Path(settings_manager.get_setting("models_dir", "models"))

    # Where saved bytes actually live - local disk by default, optionally S3
    # (see StorageSettingsManager). Built this early because it is a single
    # process-wide singleton every writer/reader of uploads and generation
    # output shares (FileStore, OutputProcessor, MediaManager, the generation
    # history archive, the phrasebook preview generator, ...) - none of them
    # build their own.
    from src.platform.filesystem.storage_settings import StorageSettingsManager

    storage_settings_manager = StorageSettingsManager(settings_manager)
    storage_driver = storage_settings_manager.build_driver(
        settings_manager.get_file_storage_directory()
    )

    from src.platform.observability.profiling import configure_settings_manager as _configure_profiling_settings
    _configure_profiling_settings(settings_manager)

    # Wire the chat LLM call-trace collector (admin session-debug viewer) —
    # provider clients call trace_collector.record() unconditionally; it is a
    # no-op until this recorder is installed and stays gated by the
    # chat_llm_call_tracing setting from here on.
    from src.features.llm import trace_collector as _chat_trace_collector
    from src.features.llm.trace_recorder import ChatCallTraceRecorder
    from src.features.llm.trace_repository import chat_call_trace_repository
    _chat_trace_collector.set_recorder(
        ChatCallTraceRecorder(chat_call_trace_repository, settings_manager)
    )

    # Field-type registry (src/platform/plugins/field_types.py) - the single
    # source of truth for form field dispatch, shared by FieldFactory,
    # FormManager, the field_controller endpoint, and the plugin
    # enable/disable path. This is the process-wide singleton (mirrors
    # `output_type_registry`) so any ad-hoc `FieldFactory(...)`
    # construction elsewhere (e.g. DeveloperManager, PresetFormSerializer)
    # sees the same, already-populated registry. Populated before plugins
    # are discovered/enabled so core types are registered first (a plugin
    # claiming a core type name fails to enable with a collision error,
    # rather than a core registration silently failing later).
    field_type_registry = _shared_field_type_registry

    # Model attribute definitions (a LoRA's default `strength`, trigger words,
    # ...) - DB-backed and UI-managed (Attributes v2, migration 135). Built
    # before the plugin registry for the same reason as field_type_registry
    # above: a plugin claiming a core-owned key fails to enable with a
    # collision error rather than silently overriding it. `ensure_...` only
    # inserts a builtin that's missing - it never overwrites an admin's edits.
    attribute_definition_repository = AttributeDefinitionRepository()
    user_model_attribute_repository = UserModelAttributeRepository()
    model_attributes_manager = ModelAttributeDefinitionsManager(
        attribute_definition_repository, user_model_attribute_repository
    )
    ensure_builtin_attribute_definitions(attribute_definition_repository)

    # Managers FormManager needs, built ahead of the rest of the module
    # graph so builtin field types (which need FormManager's option
    # loaders) can be registered before any plugin is enabled.
    model_manager = ModelManager(models_dir.__str__())
    template_processor = TemplateProcessor(settings_manager)

    # Initialize plugin system first (so it can be used by other managers).
    # PluginRouterManager mounts/unmounts `api.module` routers on plugin
    # enable/disable at runtime - it's dependency-free until `attach(app)`
    # is called in create_app() once the FastAPI app exists.
    # LLM chat extension registries, created BEFORE the plugin registry so
    # plugins enabled at startup (_sync_enabled_plugins below) can register
    # their tools/chat_modes/resources into them. Builtins are registered
    # first so a plugin colliding with a builtin fails plugin enable.
    from src.features.llm.tools.registry import ToolRegistry
    from src.features.llm.tools.builtin import register_builtin_tools
    from src.features.chat.modes import (
        ChatModeRegistry,
        build_generation_mode,
        build_history_mode,
        build_models_mode,
        build_phrasebook_mode,
        build_prompts_mode,
    )
    from src.platform.resources import ResourceRegistry

    tool_registry = ToolRegistry()
    _rr._global_tool_registry = tool_registry
    register_builtin_tools(tool_registry)

    chat_mode_registry = ChatModeRegistry()
    builtin_modes = [
        build_generation_mode(),
        build_history_mode(),
        build_models_mode(),
        build_phrasebook_mode(),
        build_prompts_mode(),
    ]
    for mode in builtin_modes:
        chat_mode_registry.register(mode)
    import logging
    logging.getLogger(__name__).info(
        f"Registered {len(builtin_modes)} builtin chat modes: {[m.id for m in builtin_modes]}"
    )

    resource_registry = ResourceRegistry()
    from src.platform.resources.builtin import register_builtin_resource_providers
    register_builtin_resource_providers(resource_registry)

    # Automation module node-type registry (src.platform.plugins.automation_nodes) -
    # created before the plugin registry so plugin-provided
    # `automation_nodes:` entries can be registered during
    # `_sync_enabled_plugins` below.
    from src.platform.plugins.automation_nodes import node_type_registry as automation_node_type_registry
    from src.platform.plugins.automation_templates import AutomationTemplateRegistry
    from src.features.automation.templates import register_builtin_templates

    automation_template_registry = AutomationTemplateRegistry()
    register_builtin_templates(automation_template_registry)

    # Prompt-library import source registry - created before the plugin
    # registry so plugin-provided `prompt_importers:` entries can be
    # registered during `_sync_enabled_plugins` below. There are no builtin
    # importers, so this starts empty.
    prompt_importer_registry = _shared_prompt_importer_registry

    plugin_router_manager = PluginRouterManager()
    plugin_registry = PluginRegistry(
        marketplace_dir="content/plugins/marketplace",
        local_dir="content/plugins/local",
        field_registry=field_type_registry,
        model_attributes_manager=model_attributes_manager,
        router_manager=plugin_router_manager,
        tool_registry=tool_registry,
        chat_mode_registry=chat_mode_registry,
        resource_registry=resource_registry,
        automation_node_registry=automation_node_type_registry,
        automation_template_registry=automation_template_registry,
        prompt_importer_registry=prompt_importer_registry,
    )
    _rr._global_plugin_registry = plugin_registry  # Set the global reference

    # Built after the plugin registry so enabled plugins that own a `presets:`
    # root contribute their presets at load time (loading is lazy, so the
    # startup plugin sync below is applied before the first load).
    preset_template_loader = PresetTemplateLoader(
        ["content/presets/marketplace", "content/presets/local"], plugin_registry=plugin_registry
    )

    form_manager = FormManager(
        template_processor=template_processor,
        model_manager=model_manager,
        settings_manager=settings_manager,
        plugin_registry=plugin_registry,
        field_registry=field_type_registry
    )
    register_builtin_fields(field_type_registry, form_manager=form_manager)

    # Register the 13 builtin automation node types (triggers/conditions/
    # actions) onto the shared node_type_registry. Imported for
    # side-effect, mirroring register_builtin_fields above.
    import src.features.automation.nodes  # noqa: F401

    # Sync enabled plugins from database to registry - after builtin field
    # types are registered, so a plugin field type colliding with a core
    # one fails plugin enable rather than crashing builtin registration.
    plugin_repository = PluginRepository()
    _sync_enabled_plugins(plugin_registry, plugin_repository)

    # Download queue: built early because model fetches across the app (admin
    # queueing, model recommendations, setup runs, lazy first-use HF loaders)
    # all route through this one manager for unified history. The connection
    # manager is the module-level singleton from the platform websocket layer,
    # injected as an instance (mirrors the notification wiring below).
    from src.platform.websocket.download_connection_manager import download_connection_manager

    download_repository = DownloadRepository()
    download_manager = DownloadManager(
        download_repository=download_repository,
        plugin_registry=plugin_registry,
        settings_manager=settings_manager,
        connection_manager=download_connection_manager,
    )

    # Initialize core managers
    gpu_manager = GpuManager()
    system_monitor = SystemMonitor()
    memory_manager = MemoryManager(gpu_manager=gpu_manager, settings_manager=settings_manager)
    model_lifecycle_manager = ModelLifecycleManager(gpu_manager=gpu_manager, settings_manager=settings_manager)
    pipe_catalog = PipeCatalog("src/pipelines/pipes", "pipes/custom", plugin_registry=plugin_registry)
    pipe_install_manager = PipeInstallManager(pipe_catalog, PipeInstaller(pipe_catalog))
    model_indexer = ModelIndexer(model_manager)
    preset_processor = PresetProcessor(template_processor, model_manager, settings_manager, preset_template_loader)
    image_manager = ImageManager(template_processor, settings_manager)

    # Initialize LLM components
    llm_repository = LLMRepository()
    llm_service = LLMGateway(llm_repository=llm_repository, model_lifecycle_manager=model_lifecycle_manager)
    from src.features.llm.tools.governance import ToolGovernanceRepository

    tool_governance_repository = ToolGovernanceRepository()
    llm_manager = LLMManager(
        llm_repository=llm_repository,
        llm_service=llm_service,
        settings_manager=settings_manager,
        plugin_registry=plugin_registry,
        tool_governance_repository=tool_governance_repository,
    )

    # Initialize auth components
    user_repository = UserRepository()
    instance_claim_repository = InstanceClaimRepository()
    claim_token_manager = ClaimTokenManager(settings_manager)
    auth_config = AuthConfig(settings_manager)
    password_hasher = PasswordHasher()
    token_manager = TokenManager(auth_config)
    auth_manager = AuthManager(
        user_repository=user_repository,
        password_hasher=password_hasher,
        token_manager=token_manager,
        auth_config=auth_config,
        plugin_registry=plugin_registry,
        instance_claim=instance_claim_repository,
        claim_tokens=claim_token_manager,
        settings_manager=settings_manager,
    )
    setup_manager = SetupManager(
        instance_claim_repository=instance_claim_repository,
        claim_token_manager=claim_token_manager,
        settings_manager=settings_manager,
    )
    setup_run_manager = SetupRunManager()
    # Recipe catalog (discovers/validates content/recipes/{marketplace,local}/*.yml).
    # The executor registry is wired further down (see "setup executors")
    # because it needs preset_manager/backend_registry, built later in this
    # function. `POTIONUI_RECIPES_DIR` lets an ephemeral/test instance point
    # the catalog at a disposable directory instead of the repo's real
    # `content/recipes/`.
    recipe_catalog = RecipeCatalog(
        os.getenv("POTIONUI_RECIPES_DIR", "content/recipes"), plugin_registry=plugin_registry
    )

    # Initialize phrasebook components
    phrasebook_category_repo = PhrasebookCategoryRepository()
    phrasebook_value_repo = PhrasebookValueRepository()
    phrasebook_manager = PhrasebookManager(
        category_repository=phrasebook_category_repo,
        value_repository=phrasebook_value_repo,
        plugin_registry=plugin_registry
    )
    phrasebook_preview_generator = PhrasebookPreviewGenerator(
        category_repository=phrasebook_category_repo,
        value_repository=phrasebook_value_repo,
        settings_manager=settings_manager,
        storage_driver=storage_driver,
    )

    def make_generation_manager() -> GenerationManager:
        """One manager per backend, over shared collaborators. See BackendRegistry."""
        return GenerationManager(
            gpu=gpu_manager,
            model_manager=model_manager,
            pipe_catalog=pipe_catalog,
            settings_manager=settings_manager,
            system_monitor=system_monitor,
            memory_manager=memory_manager,
            llm_service=llm_service,
            models=model_lifecycle_manager,
            assets=download_manager,
        )

    backend_registry = BackendRegistry(
        generation_manager_factory=make_generation_manager,
        plugin_registry=plugin_registry,
        pipe_catalog=pipe_catalog,
    )
    field_factory = FieldFactory(preset_template_loader, template_processor, field_registry=field_type_registry)

    # Initialize system monitor manager
    system_monitor_manager = SystemMonitorManager(
        system_monitor=system_monitor,
        gpu_manager=gpu_manager,
        plugin_registry=plugin_registry
    )

    # Initialize system monitor controller
    from src.features.system_monitor.routes import SystemMonitorController
    system_monitor_controller = SystemMonitorController(system_monitor_manager)

    # Initialize plugin components
    # Wire the preset loader + pipe catalog so enabling/disabling a
    # plugin rescans both live (plugin-shipped presets, plugin-contributed
    # modes, and plugin-shipped pipes appear/disappear without a restart).
    plugin_manager = PluginManager(
        plugin_repository=plugin_repository,
        plugin_registry=plugin_registry,
        preset_loader=preset_template_loader,
        pipe_catalog=pipe_catalog,
        recipe_catalog=recipe_catalog,
    )
    from src.features.plugins.routes import PluginController
    plugin_controller = PluginController(plugin_manager, plugin_repository)

    # Initialize LLM controller
    from src.features.llm.routes import LLMController
    llm_controller = LLMController(llm_manager, download_manager)

    # Initialize user components
    from src.features.users.routes import UserController
    user_manager = UserManager(
        user_repository=user_repository,
        password_hasher=password_hasher,
        plugin_registry=plugin_registry,
        settings_manager=settings_manager
    )
    user_controller = UserController(user_manager)

    # Initialize notification components. The connection manager is the
    # module-level singleton from the platform websocket layer
    # (src/platform/websocket), injected here as an instance rather than
    # imported at module scope.
    from src.features.notifications.repository import NotificationRepository
    from src.platform.websocket.notification_connection_manager import notification_connection_manager
    from src.features.notifications import NotificationManager
    from src.features.notifications.routes import NotificationController

    notification_repository = NotificationRepository()
    notification_manager = NotificationManager(
        notification_repository=notification_repository,
        user_repository=user_repository,
        plugin_registry=plugin_registry,
        connection_manager=notification_connection_manager,
        settings_manager=settings_manager
    )
    _rr._global_notification_manager = notification_manager
    notification_controller = NotificationController(notification_manager, notification_repository)

    # Initialize LLM memory components
    from src.features.llm_memory.repository import LLMMemoryRepository
    from src.features.llm_memory import LLMMemoryManager

    llm_memory_repository = LLMMemoryRepository()
    llm_memory_manager = LLMMemoryManager(repository=llm_memory_repository)

    # Initialize the LLM tool executor. The tool/chat-mode/resource
    # registries themselves were created earlier (before the plugin
    # registry) so startup plugin enabling could populate them.
    from src.features.llm.tools.executor import ToolExecutor

    tool_executor = ToolExecutor(tool_registry=tool_registry, llm_service=llm_service)

    # Global tool governance (admin enable/lock + per-user opt-out), layered
    # on top of the mode/session tool filtering above.
    from src.features.llm.tools.governance import ToolGovernanceManager
    from src.features.llm.tools.governance_routes import ToolGovernanceController

    tool_governance_manager = ToolGovernanceManager(
        repository=tool_governance_repository, tool_registry=tool_registry
    )
    tool_governance_controller = ToolGovernanceController(
        repository=tool_governance_repository,
        manager=tool_governance_manager,
        tool_registry=tool_registry,
        llm_repository=llm_repository,
    )

    # Initialize chat components (with tool executor and manager references)
    chat_repository = ChatRepository()
    response_processor = ResponseProcessor(plugin_registry=plugin_registry)
    chat_manager = ChatManager(
        chat_repository=chat_repository,
        llm_service=llm_service,
        response_processor=response_processor,
        plugin_registry=plugin_registry,
        chat_mode_registry=chat_mode_registry,
        tool_executor=tool_executor,
        segment_manager=None,  # Will be set after segment_manager is created
        model_index_manager=None,  # Will be set after model_index_manager is created
        preset_manager=None,  # Will be set after preset_manager is created
        phrasebook_manager=phrasebook_manager,
        resource_registry=resource_registry,
        settings_manager=settings_manager,
    )

    # Initialize pre-chat actions
    from src.features.chat.pre_chat_actions import PreChatActionManager
    pre_chat_action_manager = PreChatActionManager(
        plugin_registry=plugin_registry,
        llm_repository=llm_repository,
    )
    chat_manager.pre_chat_action_manager = pre_chat_action_manager

    # Chat controller. Turns are owned by a per-process registry so a client
    # disconnect (page reload) can't kill an in-flight response.
    from src.features.chat.routes import ChatController
    from src.features.chat.turns import ChatTurnRegistry
    _turn_timeout = 1800
    try:
        _turn_timeout = int(settings_manager.get_setting("chat_turn_timeout_seconds", 1800))
    except (TypeError, ValueError):
        pass
    chat_turn_registry = ChatTurnRegistry(turn_timeout_seconds=_turn_timeout)
    chat_controller = ChatController(chat_manager, chat_turn_registry)

    # Developer components
    developer_manager = DeveloperManager(
        pipe_catalog=pipe_catalog,
        preset_loader=preset_template_loader,
        template_processor=template_processor
    )
    from src.features.developer.routes import DeveloperController
    developer_controller = DeveloperController(developer_manager)

    # Docs components (in-app Documentation feature - aggregates repo
    # markdown, plugin-manifest `docs:` entries, and live-reference APIs
    # into a role-filtered tree)
    from src.features.docs.manager import DocsManager
    from src.features.docs.routes import DocsController
    docs_manager = DocsManager(plugin_registry, base_docs_path="docs")
    docs_controller = DocsController(docs_manager, developer_manager)

    # Form components (form_manager was constructed earlier, ahead of
    # plugin discovery, so builtin field types could be registered first)
    from src.features.forms.routes import FormController
    form_controller = FormController(form_manager)

    # Field-type registry controller
    from src.features.fields.routes import FieldController
    field_controller = FieldController(field_type_registry)

    # Application layer services
    from src.platform.websocket.connection_manager import ConnectionManager

    pipeline_builder = PipelineBuilder(preset_template_loader, preset_processor)
    output_processor = OutputProcessor(settings_manager, storage_driver=storage_driver)

    # Let plugins register additional GenerationOutput types (handler,
    # WS serializer, message type) on the shared output_type_registry.
    from src.features.generation.output_types import output_type_registry
    try:
        plugin_registry.execute_hook(
            OUTPUT_TYPE_HOOKS.register,
            initial_data={'registry': output_type_registry}
        )
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Error executing output_type.register hook: {e}")

    # Let plugins register additional notification types on the shared
    # notification_type_registry (so they show up in the preferences UI).
    from src.features.notifications.types import notification_type_registry
    from src.features.notifications.hooks import NOTIFICATION_TYPE_HOOKS
    try:
        plugin_registry.execute_hook(
            NOTIFICATION_TYPE_HOOKS.register,
            initial_data={'registry': notification_type_registry}
        )
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Error executing notification_type.register hook: {e}")

    connection_manager = ConnectionManager()
    generation_status_tracker = GenerationStatusTracker()

    # Stateless DB-wrapper repositories/policy the orchestrator needs for admin
    # form overrides + model-access enforcement - instantiated here (ahead of the
    # preset/model sections below, which build their own copies).
    from src.features.presets.repository import DatabasePresetRepository as _DatabasePresetRepositoryForOrchestrator
    from src.features.models.repository import ModelRepository as _ModelRepositoryForOrchestrator
    from src.features.models.access_policy import ModelAccessPolicy

    _preset_repo_for_orchestrator = _DatabasePresetRepositoryForOrchestrator()
    model_access_policy = ModelAccessPolicy(_ModelRepositoryForOrchestrator())

    # Durable per-generation stats store. Built here (ahead of the main "Stats
    # components" block) because the orchestrator needs it at construction time
    # to write a row at its generation.after_complete seam. `FilePresetRepository`
    # is a cheap stateless wrapper over `preset_template_loader`.
    from src.features.stats.generation_stats_repository import GenerationStatsRepository
    from src.features.stats.generation_stats_manager import GenerationStatsManager
    from src.features.presets.file_repository import FilePresetRepository as _FilePresetRepositoryForStats

    generation_stats_repository = GenerationStatsRepository()
    generation_stats_manager = GenerationStatsManager(
        generation_stats_repository=generation_stats_repository,
        file_preset_repository=_FilePresetRepositoryForStats(preset_template_loader),
    )

    file_service = FileStore(storage_driver=storage_driver)

    # Media index: built ahead of the orchestrator (like the stats manager)
    # so completed generations can queue their files for system tagging.
    from src.features.media_index.repository import MediaIndexRepository
    from src.features.media_index.manager import MediaIndexManager
    from src.features.media_index.tagger import build_tagger_provider
    from src.features.media_index.vision_embedder import build_vision_embedder
    from src.features.media_index.gallery_vector_store import GalleryVectorStore
    from src.features.media_index.gallery_prompt_vector_store import GalleryPromptVectorStore
    from src.features.media_index.routes import MediaIndexController
    # Reuses the prompt-database text embedder (see the "Prompt database
    # components" block below) rather than standing up a second one - built
    # here, ahead of that block, since the media index is wired first.
    from src.features.prompt_database.embedding import build_embedding_provider

    media_index_repository = MediaIndexRepository()
    vision_embedder = build_vision_embedder(
        settings_manager, download_manager=download_manager, model_lifecycle_manager=model_lifecycle_manager,
    )
    gallery_vector_store = GalleryVectorStore(
        persist_dir=str(Path(settings_manager.get_setting("file_storage_directory", "storage")) / "chromadb"),
        embedder_slug=vision_embedder.embedder_slug,
    )
    text_embedding_provider = build_embedding_provider(settings_manager, download_manager=download_manager)
    gallery_prompt_vector_store = GalleryPromptVectorStore(
        persist_dir=str(Path(settings_manager.get_setting("file_storage_directory", "storage")) / "chromadb"),
        embedder_slug=text_embedding_provider.embedder_slug,
    )
    media_index_manager = MediaIndexManager(
        repository=media_index_repository,
        tagger_provider=build_tagger_provider(
            settings_manager, download_manager=download_manager, model_lifecycle_manager=model_lifecycle_manager,
        ),
        file_service=file_service,
        vision_embedder=vision_embedder,
        gallery_vector_store=gallery_vector_store,
        text_embedding_provider=text_embedding_provider,
        gallery_prompt_vector_store=gallery_prompt_vector_store,
    )
    media_index_controller = MediaIndexController(media_index_manager)

    generation_orchestrator = GenerationOrchestrator(
        pipeline_builder, backend_registry, connection_manager, settings_manager, output_processor,
        preset_template_loader, status_tracker=generation_status_tracker,
        notification_manager=notification_manager,
        database_preset_repository=_preset_repo_for_orchestrator,
        model_access_policy=model_access_policy,
        user_repository=user_repository,
        generation_stats_manager=generation_stats_manager,
        media_index_manager=media_index_manager,
        gpu_manager=gpu_manager,
    )

    # Initialize generation history manager
    generation_repository = GenerationRepository()
    run_report_repository = GenerationRunReportRepository()
    run_report_recorder = RunReportRecorder(run_report_repository)
    generation_history_manager = GenerationHistoryManager(
        generation_repo=generation_repository,
        file_service=file_service,
        plugin_registry=plugin_registry,
        media_index_repository=media_index_repository,
        settings_manager=settings_manager,
        media_index_manager=media_index_manager,
        preset_name_resolver=PresetNameResolver(preset_template_loader)
    )

    # Phrasebook controller (needs generation_orchestrator)
    from src.features.phrasebook.routes import PhrasebookController
    phrasebook_controller = PhrasebookController(
        phrasebook_manager=phrasebook_manager,
        category_repository=phrasebook_category_repo,
        value_repository=phrasebook_value_repo,
        preview_generator=phrasebook_preview_generator,
        generation_orchestrator=generation_orchestrator
    )

    # Segment components
    from src.features.segments.routes import SegmentController
    segment_category_repo = SegmentCategoryRepository()
    saved_segment_repo = SavedSegmentRepository()
    segment_template_repo = SegmentTemplateRepository()
    segment_manager = SegmentManager(
        category_repository=segment_category_repo,
        saved_segment_repository=saved_segment_repo,
        template_repository=segment_template_repo,
        plugin_registry=plugin_registry
    )
    segment_controller = SegmentController(segment_manager)

    # Model index components
    from src.features.models.repository import ModelRepository
    from src.features.tags.repository import TagRepository
    from src.features.models.routes import ModelController
    model_repository = ModelRepository()
    tag_repository = TagRepository()
    def _generation_active() -> bool:
        snapshot = generation_orchestrator.queue.snapshot()
        return bool(snapshot["pending"]) or bool(snapshot["running"])

    model_index_manager = ModelIndexManager(
        model_repository=model_repository,
        tag_repository=tag_repository,
        plugin_registry=plugin_registry,
        settings_manager=settings_manager,
        download_manager=download_manager,
        models_root=models_dir,
        generation_active=_generation_active,
        storage_driver=storage_driver,
        attribute_definition_repository=attribute_definition_repository,
        user_attribute_repository=user_model_attribute_repository,
    )

    # Model library components (favorites/custom names + model collections)
    from src.features.model_library.repository.model_collection_repository import ModelCollectionRepository
    from src.features.model_library.repository.user_model_meta_repository import UserModelMetaRepository
    from src.features.model_library import ModelLibraryManager
    from src.features.model_library.routes import ModelCollectionController

    model_collection_repository = ModelCollectionRepository()
    user_model_meta_repository = UserModelMetaRepository()
    model_library_manager = ModelLibraryManager(
        model_collection_repository=model_collection_repository,
        user_model_meta_repository=user_model_meta_repository
    )
    model_collection_controller = ModelCollectionController(model_library_manager)

    model_controller = ModelController(
        model_index_manager, model_library_manager, download_manager,
        attribute_definition_repository=attribute_definition_repository,
        model_attributes_manager=model_attributes_manager,
    )

    # Tag components
    from src.features.tags import TagManager
    from src.features.tags.routes import TagController
    from src.features.presets.repository import DatabasePresetRepository
    from src.features.presets.file_repository import FilePresetRepository

    tag_manager = TagManager(
        tag_repository=tag_repository,
        plugin_registry=plugin_registry,
        database_preset_repository=DatabasePresetRepository(),
        file_preset_repository=FilePresetRepository(preset_template_loader)
    )
    tag_controller = TagController(tag_manager)

    # Collection components
    from src.features.collections.repository import CollectionRepository
    from src.features.collections import CollectionManager
    from src.features.collections.routes import CollectionController

    collection_repository = CollectionRepository()
    collection_manager = CollectionManager(collection_repository=collection_repository)
    collection_controller = CollectionController(collection_manager)

    # Automation module components. The connection manager is the module-level
    # singleton from the platform websocket layer (src/platform/websocket),
    # imported here as an instance rather than at module scope, mirroring the
    # notification wiring above.
    from src.features.automation.repository import automation_repo
    from src.features.automation.context import AutomationServices
    from src.features.automation.engine import AutomationEngine
    from src.features.automation.manager import AutomationManager
    from src.platform.websocket.automation_connection_manager import automation_connection_manager
    from src.features.automation.routes import AutomationController

    # NOT the `model_indexer` local above (that's `src.features.models.directory.ModelIndexer`,
    # a whole-directory scanner with no single-file entry point). `action.index_model`
    # needs `index_single_model()` with its SHA256 dedup, which lives on
    # `src.features.models.indexer.ModelScanner` - the same scanner
    # `ModelIndexManager` uses. Imported as the lazy proxy singleton because its
    # `__init__` reads the settings DB.
    from src.features.models.indexer import model_scanner as file_model_indexer

    # `action.index_models` runs the same per-backend availability indexing the
    # admin "Index models" button does: live backend instances come from the
    # registry, the reconciliation from the module-singleton indexer.
    from src.features.models.backend_indexer import backend_model_indexer

    # `action.assign_user_to_group` needs a UserGroupRepository before the "User
    # group components" block constructs the container's own instance - a second
    # stateless instance is cheap (it opens its own cursor per call).
    from src.features.user_groups.repository import UserGroupRepository as _UGRForAutomation
    _user_group_repo_for_automation = _UGRForAutomation()

    automation_services = AutomationServices(
        model_index_manager=model_index_manager,
        model_indexer=file_model_indexer,
        model_repository=model_repository,
        tag_repository=tag_repository,
        notification_manager=notification_manager,
        gpu_manager=gpu_manager,
        settings_manager=settings_manager,
        backend_config_manager=backend_registry.backend_config_manager,
        model_lifecycle_manager=model_lifecycle_manager,
        backend_registry=backend_registry,
        backend_model_indexer=backend_model_indexer,
        user_group_repository=_user_group_repo_for_automation,
        media_index_manager=media_index_manager,
        generation_status_tracker=generation_status_tracker,
        model_collection_repository=model_collection_repository,
    )
    automation_engine = AutomationEngine(
        automation_repo,
        services=automation_services,
        plugin_registry=plugin_registry,
        registry=automation_node_type_registry,
        emit_ws=automation_connection_manager.broadcast,
    )
    automation_manager = AutomationManager(
        automation_repo,
        automation_engine,
        plugin_registry=plugin_registry,
        registry=automation_node_type_registry,
        template_registry=automation_template_registry,
    )
    automation_controller = AutomationController(automation_manager, registry=automation_node_type_registry)

    # Media components
    from src.features.generation.file_repository import file_repo, FileRepository
    from src.features.media import MediaManager, MediaTypeResolver, FilePathResolver, ImageProcessor, UploadRepository
    from src.features.media.routes import MediaController

    media_type_resolver = MediaTypeResolver()
    file_resolver = FilePathResolver(settings_manager, preset_template_loader)
    image_processor = ImageProcessor()
    upload_repository = UploadRepository()
    media_manager = MediaManager(
        file_resolver=file_resolver,
        image_processor=image_processor,
        media_type_resolver=media_type_resolver,
        file_repository=file_repo,
        generation_repository=generation_repository,
        settings_manager=settings_manager,
        file_service=file_service,
        plugin_registry=plugin_registry,
        upload_repository=upload_repository,
        storage_driver=storage_driver,
    )
    media_controller = MediaController(media_manager)

    from src.features.media.editing.manager import MediaEditManager
    from src.features.media.editing.routes import MediaEditController

    media_edit_manager = MediaEditManager(
        upload_repository=upload_repository,
        media_type_resolver=media_type_resolver,
        storage_driver=storage_driver,
    )
    media_edit_controller = MediaEditController(media_edit_manager)

    # Library components (uploads as first-class, curatable resources)
    from src.features.library import LibraryManager, LibraryRepository
    from src.features.library.routes import LibraryController

    library_repository = LibraryRepository()
    library_manager = LibraryManager(
        library_repository=library_repository,
        upload_repository=upload_repository,
        tag_repository=tag_repository,
        file_repository=file_repo,
        file_resolver=file_resolver,
        file_store=file_service,
        storage_driver=storage_driver,
    )
    library_controller = LibraryController(library_manager)

    # Inspirations components (cross-user publishing of generations)
    from src.features.generation.parameter_repository import GenerationParameterRepository
    from src.features.inspirations import InspirationManager, InspirationRepository
    from src.features.inspirations.routes import InspirationController

    inspiration_repository = InspirationRepository()
    inspiration_manager = InspirationManager(
        inspiration_repository=inspiration_repository,
        generation_repository=generation_repository,
        generation_parameter_repository=GenerationParameterRepository(),
        preset_name_resolver=PresetNameResolver(preset_template_loader),
        preset_template_loader=preset_template_loader,
        field_type_registry=field_type_registry,
        file_store=file_service,
        file_resolver=file_resolver,
        storage_driver=storage_driver,
        upload_repository=upload_repository,
        notification_manager=notification_manager,
    )
    inspiration_controller = InspirationController(
        inspiration_manager=inspiration_manager,
        inspiration_repository=inspiration_repository,
        file_store=file_service,
        storage_driver=storage_driver,
    )

    # Preset manager components
    from src.features.presets.file_repository import FilePresetRepository
    from src.features.presets.repository import DatabasePresetRepository
    from src.features.presets.routes import PresetController

    from src.features.user_groups.repository import UserGroupRepository as _UGR
    file_preset_repository = FilePresetRepository(preset_template_loader)
    database_preset_repository = DatabasePresetRepository()
    _user_group_repo_for_presets = _UGR()
    preset_manager = PresetManager(
        preset_loader=preset_template_loader,
        preset_processor=preset_processor,
        template_processor=template_processor,
        file_preset_repository=file_preset_repository,
        database_preset_repository=database_preset_repository,
        user_repository=user_repository,
        user_group_repository=_user_group_repo_for_presets,
        pipeline_builder=pipeline_builder,
        pipe_catalog=pipe_catalog,
        plugin_registry=plugin_registry,
        settings_manager=settings_manager
    )
    preset_controller = PresetController(
        preset_manager, backend_registry, media_manager,
        model_access_policy=model_access_policy,
    )

    # Setup executors: wire the built-in step executors (one per recipe step
    # `kind` - see src/features/setup/executors/) onto the run manager's
    # executor-registry seam. Built here, not up near `recipe_catalog`, because
    # it needs preset_manager/backend_registry/pipeline_builder, constructed above.
    from src.features.setup.executors import build_default_executor_registry

    setup_run_manager.register_executor_registry(
        build_default_executor_registry(
            recipe_catalog=recipe_catalog,
            plugin_registry=plugin_registry,
            backend_registry=backend_registry,
            preset_manager=preset_manager,
            user_repository=user_repository,
            preset_template_loader=preset_template_loader,
            template_processor=template_processor,
            pipeline_builder=pipeline_builder,
            model_repository=model_repository,
            generation_orchestrator=generation_orchestrator,
            backend_model_indexer=backend_model_indexer,
            download_manager=download_manager,
        )
    )

    # Prompt database components
    from src.features.prompt_database.repository import PromptRepository
    from src.features.prompt_database.manager import PromptDatabaseManager
    from src.features.prompt_database.vector_store import PromptVectorStore
    from src.features.prompt_database.routes import PromptDatabaseController

    prompt_repository = PromptRepository()
    # `text_embedding_provider`, built above for the media index's prompt_embed
    # pass, is the same provider/model - reused here under its established
    # name rather than constructing a second instance.
    embedding_provider = text_embedding_provider
    prompt_vector_store = PromptVectorStore(
        persist_dir=str(Path(settings_manager.get_setting("file_storage_directory", "storage")) / "chromadb"),
        embedder_slug=embedding_provider.embedder_slug,
    )
    prompt_database_manager = PromptDatabaseManager(
        repository=prompt_repository,
        vector_store=prompt_vector_store,
        embedding_provider=embedding_provider,
        plugin_registry=plugin_registry,
    )
    prompt_database_controller = PromptDatabaseController(prompt_database_manager)

    # Wire up deferred service references for ChatManager's tool context
    chat_manager.segment_manager = segment_manager
    chat_manager.model_index_manager = model_index_manager
    chat_manager.preset_manager = preset_manager
    chat_manager.prompt_database_manager = prompt_database_manager
    chat_manager.generation_orchestrator = generation_orchestrator
    chat_manager.llm_memory_manager = llm_memory_manager
    chat_manager.media_index_manager = media_index_manager
    chat_manager.tool_governance_repository = tool_governance_repository
    chat_manager.collection_manager = collection_manager
    chat_manager.tag_manager = tag_manager
    chat_manager.generation_history_manager = generation_history_manager
    # Generation repositories for the @generations resource provider
    from src.features.generation.model_repository import GenerationModelRepository
    from src.features.generation.parameter_repository import GenerationParameterRepository
    chat_manager.generation_repository = generation_repository
    chat_manager.generation_parameter_repository = GenerationParameterRepository()
    chat_manager.generation_model_repository = GenerationModelRepository()

    # Prompt enhancement pipeline (used by the enhance_prompt tool)
    from src.features.prompt_enhancement import PromptEnhancementManager
    from src.features.prompt_enhancement.repository import (
        EnhancementFeedbackRepository,
    )

    enhancement_feedback_repository = EnhancementFeedbackRepository()
    prompt_enhancement_manager = PromptEnhancementManager(
        llm_service=llm_service,
        prompt_database_manager=prompt_database_manager,
        model_index_manager=model_index_manager,
        llm_memory_manager=llm_memory_manager,
        feedback_repository=enhancement_feedback_repository,
        preset_manager=preset_manager,
    )
    chat_manager.prompt_enhancement_manager = prompt_enhancement_manager

    # MCP (Model Context Protocol): per-user tokens exposing the same tool
    # surface a `generation`-mode chat session sees. Built here, after every
    # collaborator the tool context needs (segment/model/preset/prompt
    # database/generation/memory/media managers) is available — the same
    # ordering constraint chat_manager's late-bound assignments above solve.
    from src.features.mcp.manager import McpManager
    from src.features.mcp.protocol import McpProtocolManager
    from src.features.mcp.repository import McpTokenRepository

    mcp_token_repository = McpTokenRepository()
    mcp_manager = McpManager(
        token_repository=mcp_token_repository,
        settings_manager=settings_manager,
        user_repository=user_repository,
    )
    mcp_protocol_manager = McpProtocolManager(
        tool_registry=tool_registry,
        tool_governance_repository=tool_governance_repository,
        llm_repository=llm_repository,
        segment_manager=segment_manager,
        model_index_manager=model_index_manager,
        preset_manager=preset_manager,
        phrasebook_manager=phrasebook_manager,
        prompt_database_manager=prompt_database_manager,
        generation_orchestrator=generation_orchestrator,
        llm_memory_manager=llm_memory_manager,
        prompt_enhancement_manager=prompt_enhancement_manager,
        media_index_manager=media_index_manager,
        settings_manager=settings_manager,
        collection_manager=collection_manager,
        tag_manager=tag_manager,
        generation_history_manager=generation_history_manager,
    )

    pre_chat_action_manager.discover_actions()

    # Stats components (depends on file_preset_repository for preset display names)
    from src.features.stats.repository import StatsRepository
    from src.features.stats import StatsManager
    from src.features.stats.routes import StatsController

    stats_repository = StatsRepository()
    stats_manager = StatsManager(
        stats_repository=stats_repository,
        file_preset_repository=file_preset_repository,
    )
    # `generation_stats_repository`/`generation_stats_manager` were built earlier,
    # ahead of `generation_orchestrator`'s construction -- reused here, not rebuilt.
    stats_controller = StatsController(
        stats_manager=stats_manager,
        stats_repository=stats_repository,
        generation_stats_manager=generation_stats_manager,
        generation_stats_repository=generation_stats_repository,
    )

    # Session components
    from src.features.sessions.repository import SessionRepository
    from src.features.sessions import SessionManager
    from src.features.sessions.routes import SessionController

    session_repository = SessionRepository()
    # --- Session history (versions) ---
    # Appends an immutable snapshot on every save; `sessions` itself stays the
    # "current" state. `file_preset_repository` resolves a preset id's on-disk
    # display name, denormalized into each version's `summary` column at write
    # time (see migration 092 / SessionVersionRepository).
    from src.features.sessions.version_repository import SessionVersionRepository

    session_version_repository = SessionVersionRepository()
    session_manager = SessionManager(
        session_repository=session_repository,
        plugin_registry=plugin_registry,
        session_version_repository=session_version_repository,
        file_preset_repository=file_preset_repository,
    )
    session_controller = SessionController(session_manager)

    # Workspace components
    from src.features.workspaces.repository import WorkspaceRepository
    from src.features.workspaces import WorkspaceManager
    from src.features.workspaces.routes import WorkspaceController

    workspace_repository = WorkspaceRepository()
    workspace_manager = WorkspaceManager(
        workspace_repository=workspace_repository,
        plugin_registry=plugin_registry
    )
    workspace_controller = WorkspaceController(workspace_manager)

    # User group components
    from src.features.user_groups.repository import UserGroupRepository
    from src.features.user_groups.routes import UserGroupController

    user_group_repository = UserGroupRepository()
    user_group_manager = UserGroupManager(
        user_group_repository=user_group_repository,
        plugin_registry=plugin_registry
    )
    user_group_controller = UserGroupController(user_group_manager)

    # Assemble the container from the locals built above (field name == local
    # variable name). A missing/misnamed field surfaces immediately here.
    _locals = locals()
    return AppContainer(**{f.name: _locals[f.name] for f in dataclasses.fields(AppContainer)})
