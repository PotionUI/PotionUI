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
  `GenerationEngine` is created per backend, not once per process.

Routers and the controllers bound to them are assembled in `src.bootstrap.app`.
"""

from __future__ import annotations

import dataclasses
import functools
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, TYPE_CHECKING

import src.platform.plugins.runtime_registries as _rr

from src.features.generation.engine import GenerationEngine
from src.features.generation.history_facade import GenerationHistoryFacade
from src.features.media.image import ImageWriter
from src.platform.templating import TemplateProcessor
from src.platform.runtime.gpu import GpuMonitor
from src.platform.observability.system_probe import SystemMonitor
from src.platform.runtime.memory_advisor import MemoryAdvisor
from src.platform.runtime.model_lifecycle.lifecycle import ModelLifecycle
from src.features.models.directory import ModelDirectories
from src.pipelines.catalog import PipeCatalog
from src.pipelines.installer import PipeInstaller
from src.features.pipes import PipeInstallRunner
from src.features.presets import PresetTemplateLoader, PresetProcessor
from src.platform.settings.settings import Settings
from src.features.backends.backend_registry import BackendRegistry
from src.platform.plugins import PluginRegistry
from src.platform.plugins.router_mounter import PluginRouterMounter
from src.features.generation.hooks import OUTPUT_TYPE_HOOKS
from src.platform.security import AuthConfig, PasswordHasher, TokenCodec, Auth, ClaimTokenStore
from src.features.setup import InstanceClaimRepository
from src.features.setup.runner import SetupRunner
from src.features.setup.recipe_catalog import RecipeCatalog
from src.features.phrasebook.preview_generator import PhrasebookPreviewGenerator
from src.features.chat import ChatRuntime, ResponseProcessor
from src.features.downloads import DownloadQueue, DownloadRepository
from src.platform.plugins.field_types import FieldTypeRegistry, field_type_registry as _shared_field_type_registry
from src.platform.plugins.prompt_importers import (
    PromptImporterRegistry,
    prompt_importer_registry as _shared_prompt_importer_registry,
)
from src.features.fields.builtin import register_builtin_fields
from src.features.models.attributes.repository import AttributeDefinitionRepository
from src.features.models.attributes.user_repository import UserModelAttributeRepository
from src.features.models.attributes.editor import ModelAttributeDefinitionsEditor
from src.features.models.attributes.seeding import ensure_builtin_attribute_definitions
from src.features.models import ModelIndexCollaborators, build_model_index_collaborators
from src.features.presets.collaborators import PresetCollaborators
from src.features.presets.name_resolver import PresetNameResolver
from src.features.system_monitor import SystemMonitorCoordinator

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
    from src.features.llm_memory.repository import LLMMemoryRepository
    from src.platform.websocket.connection_hub import ConnectionHub
    from src.platform.websocket.download_connection_hub import DownloadConnectionHub
    from src.features.models.repository import ModelRepository
    from src.features.tags.repository import TagRepository
    from src.features.model_library.repository.model_collection_repository import ModelCollectionRepository
    from src.features.model_library.repository.user_model_meta_repository import UserModelMetaRepository
    from src.features.collections.repository import CollectionRepository
    from src.features.automation.engine import AutomationEngine
    from src.features.automation.runtime import AutomationRuntime
    from src.platform.plugins.automation_templates import AutomationTemplateRegistry
    from src.features.media import MediaStore, MediaTypeResolver, FilePathResolver, ImageProcessor
    from src.features.presets.file_repository import FilePresetRepository
    from src.features.presets.repository import DatabasePresetRepository
    from src.features.prompt_database.collaborators import PromptDatabaseCollaborators
    from src.features.prompt_database.repository import PromptRepository
    from src.features.prompt_enhancement.repository import EnhancementFeedbackRepository
    from src.features.prompt_enhancement import PromptEnhancementCollaborators
    from src.features.media_index.repository import MediaIndexRepository
    from src.features.media_index.indexer import MediaIndexer
    from src.features.media_index.routes import MediaIndexController
    from src.features.stats.repository import StatsRepository
    from src.features.stats.generation_stats_repository import GenerationStatsRepository
    from src.features.sessions.repository import SessionRepository
    from src.features.sessions.version_repository import SessionVersionRepository
    from src.features.workspaces.repository import WorkspaceRepository
    from src.features.user_groups.repository import UserGroupRepository
    from src.features.system_monitor.routes import SystemMonitorController
    from src.features.provisioning.registry import ComputeProvisionerRegistry
    from src.features.provisioning.repository import ProvisionedComputeRepository
    from src.features.provisioning.routes import ProvisioningController
    from src.features.plugins.routes import PluginController
    from src.features.llm.routes import LLMController
    from src.features.llm.tools.governance import ToolGovernanceEditor, ToolGovernanceRepository
    from src.features.llm.tools.governance_routes import ToolGovernanceController
    from src.features.users.routes import UserController
    from src.features.notifications.routes import NotificationController
    from src.features.chat.routes import ChatController
    from src.features.chat.turns import ChatTurnRegistry
    from src.features.developer.routes import DeveloperController
    from src.features.docs.routes import DocsController
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
    from src.features.media.editing.editor import MediaEditor
    from src.features.media.editing.routes import MediaEditController
    from src.features.library import LibraryCollaborators, LibraryRepository
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
    settings: Settings
    plugin_registry: PluginRegistry
    plugin_router_mounter: PluginRouterMounter
    field_type_registry: FieldTypeRegistry
    attribute_definition_repository: "AttributeDefinitionRepository"
    user_model_attribute_repository: "UserModelAttributeRepository"
    model_attributes_manager: "ModelAttributeDefinitionsEditor"
    prompt_importer_registry: PromptImporterRegistry
    tool_registry: "ToolRegistry"
    tool_executor: "ToolExecutor"
    chat_mode_registry: "ChatModeRegistry"
    resource_registry: "ResourceRegistry"

    # Core managers
    gpu_monitor: GpuMonitor
    system_monitor: SystemMonitor
    system_monitor_coordinator: SystemMonitorCoordinator
    memory_advisor: MemoryAdvisor
    model_lifecycle: ModelLifecycle
    model_directories: ModelDirectories
    pipe_catalog: PipeCatalog
    pipe_install_runner: PipeInstallRunner
    preset_template_loader: PresetTemplateLoader
    preset_processor: PresetProcessor
    template_processor: TemplateProcessor
    image_writer: ImageWriter
    backend_registry: BackendRegistry
    field_factory: FieldFactory

    # Compute provisioning
    compute_provisioner_registry: "ComputeProvisionerRegistry"
    provisioned_compute_repository: "ProvisionedComputeRepository"
    provisioning_controller: "ProvisioningController"

    # LLM
    llm_repository: LLMRepository
    llm_service: LLMGateway
    llm_memory_repository: "LLMMemoryRepository"

    # Plugins
    plugin_repository: PluginRepository
    plugin_controller: "PluginController"
    llm_controller: "LLMController"
    tool_governance_repository: "ToolGovernanceRepository"
    tool_governance_editor: "ToolGovernanceEditor"
    tool_governance_controller: "ToolGovernanceController"
    mcp_token_repository: "McpTokenRepository"
    mcp_tool_collaborators: "McpToolCollaborators"

    # Auth
    user_repository: UserRepository
    instance_claim_repository: InstanceClaimRepository
    claim_token_store: ClaimTokenStore
    auth_config: AuthConfig
    password_hasher: PasswordHasher
    token_codec: TokenCodec
    auth: Auth
    setup_runner: SetupRunner
    recipe_catalog: RecipeCatalog
    user_controller: "UserController"

    # Downloads
    download_repository: DownloadRepository
    download_connection_hub: "DownloadConnectionHub"
    download_queue: DownloadQueue

    # Notification
    notification_repository: "NotificationRepository"
    # A bound callable (`functools.partial(operations.notify, collaborators)`),
    # not a class instance - see its construction below.
    notification_manager: Callable[..., Any]
    notification_controller: "NotificationController"

    # Phrasebook
    phrasebook_category_repo: PhrasebookCategoryRepository
    phrasebook_value_repo: PhrasebookValueRepository
    phrasebook_preview_generator: PhrasebookPreviewGenerator

    # Chat
    chat_repository: ChatRepository
    response_processor: ResponseProcessor
    chat_runtime: ChatRuntime
    chat_turn_registry: "ChatTurnRegistry"
    chat_controller: "ChatController"

    # Developer / docs
    developer_controller: "DeveloperController"
    docs_controller: "DocsController"

    # Forms / fields
    form_controller: "FormController"
    field_controller: "FieldController"

    # Generation
    generation_status_tracker: GenerationStatusTracker
    pipeline_builder: PipelineBuilder
    output_processor: OutputProcessor
    connection_hub: "ConnectionHub"
    generation_orchestrator: GenerationOrchestrator
    file_service: FileStore
    phrasebook_controller: "PhrasebookController"
    generation_repository: GenerationRepository
    generation_history_facade: GenerationHistoryFacade
    run_report_repository: GenerationRunReportRepository
    run_report_recorder: RunReportRecorder

    # Segments
    segment_category_repo: SegmentCategoryRepository
    saved_segment_repo: SavedSegmentRepository
    segment_template_repo: SegmentTemplateRepository
    segment_controller: "SegmentController"

    # Model index / library
    model_repository: "ModelRepository"
    tag_repository: "TagRepository"
    model_index_manager: ModelIndexCollaborators
    model_controller: "ModelController"
    model_collection_repository: "ModelCollectionRepository"
    user_model_meta_repository: "UserModelMetaRepository"
    model_collection_controller: "ModelCollectionController"

    # Tags
    tag_controller: "TagController"

    # Collections
    collection_repository: "CollectionRepository"
    collection_controller: "CollectionController"

    # Automation
    automation_engine: "AutomationEngine"
    automation_runtime: "AutomationRuntime"
    automation_controller: "AutomationController"
    automation_template_registry: "AutomationTemplateRegistry"

    # Media
    media_type_resolver: "MediaTypeResolver"
    file_resolver: "FilePathResolver"
    image_processor: "ImageProcessor"
    media_store: "MediaStore"
    media_controller: "MediaController"
    media_editor: "MediaEditor"
    media_edit_controller: "MediaEditController"

    # Library
    library_repository: "LibraryRepository"
    library_collaborators: "LibraryCollaborators"
    library_controller: "LibraryController"

    # Inspirations
    inspiration_repository: "InspirationRepository"
    inspiration_collaborators: "InspirationCollaborators"
    inspiration_controller: "InspirationController"

    # Presets
    file_preset_repository: "FilePresetRepository"
    database_preset_repository: "DatabasePresetRepository"
    preset_manager: PresetCollaborators
    preset_controller: "PresetController"

    # Prompt database / enhancement
    prompt_repository: "PromptRepository"
    prompt_database: "PromptDatabaseCollaborators"
    prompt_database_controller: "PromptDatabaseController"
    enhancement_feedback_repository: "EnhancementFeedbackRepository"
    prompt_enhancement_manager: "PromptEnhancementCollaborators"

    # Media index (system tags + reusable index queue)
    media_index_repository: "MediaIndexRepository"
    media_indexer: "MediaIndexer"
    media_index_controller: "MediaIndexController"

    # Stats
    stats_repository: "StatsRepository"
    stats_controller: "StatsController"
    # Durable generation_stats store (separate from the StatsRepository above,
    # which aggregates the live `generations` table).
    generation_stats_repository: "GenerationStatsRepository"

    # Sessions
    session_repository: "SessionRepository"
    # Session history (versions) -- separate repository over session_versions
    # (migration 092); see src/features/sessions/manager.py.
    session_version_repository: "SessionVersionRepository"
    session_controller: "SessionController"

    # Workspaces
    workspace_repository: "WorkspaceRepository"
    workspace_controller: "WorkspaceController"

    # User groups
    user_group_repository: "UserGroupRepository"
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
    settings = Settings(setting_repository)
    models_dir = Path(settings.get_setting("models_dir", "models"))

    # Where saved bytes actually live - local disk by default, optionally S3
    # (see StorageSettings). Built this early because it is a single
    # process-wide singleton every writer/reader of uploads and generation
    # output shares (FileStore, OutputProcessor, MediaStore, the generation
    # history archive, the phrasebook preview generator, ...) - none of them
    # build their own.
    from src.platform.filesystem.storage_settings import StorageSettings

    storage_settings = StorageSettings(settings)
    storage_driver = storage_settings.build_driver(
        settings.get_file_storage_directory()
    )

    from src.platform.observability.profiling import configure_settings as _configure_profiling_settings
    _configure_profiling_settings(settings)

    # Wire the chat LLM call-trace collector (admin session-debug viewer) —
    # provider clients call trace_collector.record() unconditionally; it is a
    # no-op until this recorder is installed and stays gated by the
    # chat_llm_call_tracing setting from here on.
    from src.features.llm import trace_collector as _chat_trace_collector
    from src.features.llm.trace_recorder import ChatCallTraceRecorder
    from src.features.llm.trace_repository import chat_call_trace_repository
    _chat_trace_collector.set_recorder(
        ChatCallTraceRecorder(chat_call_trace_repository, settings)
    )

    # Field-type registry (src/platform/plugins/field_types.py) - the single
    # source of truth for form field dispatch, shared by FieldFactory,
    # src.features.forms.operations, the field_controller endpoint, and the
    # plugin enable/disable path. This is the process-wide singleton (mirrors
    # `output_type_registry`) so any ad-hoc `FieldFactory(...)`
    # construction elsewhere (e.g. PresetFormSerializer)
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
    model_attributes_manager = ModelAttributeDefinitionsEditor(
        attribute_definition_repository, user_model_attribute_repository
    )
    ensure_builtin_attribute_definitions(attribute_definition_repository)

    # Built ahead of the rest of the module graph so builtin field types
    # (which need `template_processor` for the select-options loader) can be
    # registered before any plugin is enabled.
    model_directories = ModelDirectories(models_dir.__str__())
    template_processor = TemplateProcessor(settings)

    # Initialize plugin system first (so it can be used by other managers).
    # PluginRouterMounter mounts/unmounts `api.module` routers on plugin
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

    plugin_router_mounter = PluginRouterMounter()
    plugin_registry = PluginRegistry(
        marketplace_dir="content/plugins/marketplace",
        local_dir="content/plugins/local",
        field_registry=field_type_registry,
        model_attributes_manager=model_attributes_manager,
        router_mounter=plugin_router_mounter,
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

    register_builtin_fields(field_type_registry, template_processor=template_processor)

    # Register the 13 builtin automation node types (triggers/conditions/
    # actions) onto the shared node_type_registry. Imported for
    # side-effect, mirroring register_builtin_fields above.
    import src.features.automation.nodes  # noqa: F401

    # Sync enabled plugins from database to registry - after builtin field
    # types are registered, so a plugin field type colliding with a core
    # one fails plugin enable rather than crashing builtin registration.
    plugin_repository = PluginRepository()
    _sync_enabled_plugins(plugin_registry, plugin_repository)

    # Download queue: built early (repository only - see below) because model
    # fetches across the app (admin queueing, model recommendations, setup
    # runs, lazy first-use HF loaders) all route through this one manager for
    # unified history. The connection manager is the module-level singleton
    # from the platform websocket layer, injected as an instance (mirrors the
    # notification wiring below).
    from src.platform.websocket.download_connection_hub import download_connection_hub

    download_repository = DownloadRepository()

    # Initialize core managers
    gpu_monitor = GpuMonitor()
    system_monitor = SystemMonitor()
    memory_advisor = MemoryAdvisor(gpu_monitor=gpu_monitor, settings=settings)
    model_lifecycle = ModelLifecycle(gpu_monitor=gpu_monitor, settings=settings)
    pipe_catalog = PipeCatalog("src/pipelines/pipes", "pipes/custom", plugin_registry=plugin_registry)
    pipe_install_runner = PipeInstallRunner(pipe_catalog, PipeInstaller(pipe_catalog))
    preset_processor = PresetProcessor(template_processor, model_directories, settings, preset_template_loader)
    image_writer = ImageWriter(template_processor, settings)

    # Initialize LLM components
    llm_repository = LLMRepository()
    llm_service = LLMGateway(llm_repository=llm_repository, model_lifecycle=model_lifecycle)
    from src.features.llm.tools.governance import ToolGovernanceRepository

    tool_governance_repository = ToolGovernanceRepository()

    # Initialize auth components
    user_repository = UserRepository()
    instance_claim_repository = InstanceClaimRepository()
    claim_token_store = ClaimTokenStore(settings)
    auth_config = AuthConfig(settings)
    password_hasher = PasswordHasher()
    token_codec = TokenCodec(auth_config)
    auth = Auth(
        user_repository=user_repository,
        password_hasher=password_hasher,
        token_codec=token_codec,
        auth_config=auth_config,
        plugin_registry=plugin_registry,
        instance_claim=instance_claim_repository,
        claim_tokens=claim_token_store,
        settings=settings,
    )
    setup_runner = SetupRunner()
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
    from src.features.phrasebook import operations as phrasebook_operations

    phrasebook_category_repo = PhrasebookCategoryRepository()
    phrasebook_value_repo = PhrasebookValueRepository()
    # Bound partial handed to the @phrasebook resource provider (platform code,
    # which must not import a feature module directly - see tests/architecture).
    phrasebook_search = functools.partial(
        phrasebook_operations.search_phrasebook, phrasebook_category_repo, phrasebook_value_repo
    )
    phrasebook_preview_generator = PhrasebookPreviewGenerator(
        category_repository=phrasebook_category_repo,
        value_repository=phrasebook_value_repo,
        settings=settings,
        storage_driver=storage_driver,
    )

    def make_generation_engine() -> GenerationEngine:
        """One manager per backend, over shared collaborators. See BackendRegistry."""
        return GenerationEngine(
            gpu=gpu_monitor,
            model_directories=model_directories,
            pipe_catalog=pipe_catalog,
            settings=settings,
            system_monitor=system_monitor,
            memory_advisor=memory_advisor,
            llm_service=llm_service,
            models=model_lifecycle,
            assets=download_queue,
        )

    backend_registry = BackendRegistry(
        generation_engine_factory=make_generation_engine,
        plugin_registry=plugin_registry,
        pipe_catalog=pipe_catalog,
    )

    # Now that backend_registry exists, the download queue can validate and
    # dispatch a `destination_backend_id` (fetch straight onto a native.remote
    # worker's depot) alongside its always-available local-disk path.
    from src.features.models.backend_indexer import backend_model_indexer

    download_queue = DownloadQueue(
        download_repository=download_repository,
        plugin_registry=plugin_registry,
        settings=settings,
        connection_hub=download_connection_hub,
        backend_registry=backend_registry,
        backend_model_indexer=backend_model_indexer,
    )

    field_factory = FieldFactory(preset_template_loader, template_processor, field_registry=field_type_registry)

    # Compute provisioning (rented GPU compute running the Remote Native worker -
    # see docs/remote-native.md). Provisioners are collected from plugins via
    # the compute.register hook, mirroring BackendRegistry's own plugin-engine
    # discovery, one line above.
    from src.features.provisioning.registry import ComputeProvisionerRegistry
    from src.features.provisioning.repository import ProvisionedComputeRepository
    from src.features.provisioning.routes import ProvisioningController

    compute_provisioner_registry = ComputeProvisionerRegistry(plugin_registry=plugin_registry)
    provisioned_compute_repository = ProvisionedComputeRepository()
    provisioning_controller = ProvisioningController(
        compute_provisioner_registry, provisioned_compute_repository, backend_registry
    )

    # Initialize system monitor manager
    system_monitor_coordinator = SystemMonitorCoordinator(
        system_monitor=system_monitor,
        gpu_monitor=gpu_monitor,
        plugin_registry=plugin_registry
    )

    # Initialize system monitor controller
    from src.features.system_monitor.routes import SystemMonitorController
    system_monitor_controller = SystemMonitorController(system_monitor_coordinator)

    # Initialize plugin components
    # Wire the preset loader + pipe catalog so enabling/disabling a
    # plugin rescans both live (plugin-shipped presets, plugin-contributed
    # modes, and plugin-shipped pipes appear/disappear without a restart).
    from src.features.plugins.routes import PluginController
    plugin_controller = PluginController(
        plugin_repository=plugin_repository,
        plugin_registry=plugin_registry,
        preset_loader=preset_template_loader,
        pipe_catalog=pipe_catalog,
        recipe_catalog=recipe_catalog,
    )

    # Initialize LLM controller
    from src.features.llm.routes import LLMController
    llm_controller = LLMController(
        llm_repository=llm_repository,
        llm_service=llm_service,
        settings=settings,
        plugin_registry=plugin_registry,
        tool_governance_repository=tool_governance_repository,
        download_queue=download_queue,
    )

    # Initialize user components
    from src.features.users.routes import UserController
    user_controller = UserController(
        user_repository=user_repository,
        password_hasher=password_hasher,
        plugin_registry=plugin_registry,
        settings=settings,
    )

    # Initialize notification components. The connection manager is the
    # module-level singleton from the platform websocket layer
    # (src/platform/websocket), injected here as an instance rather than
    # imported at module scope.
    from src.features.notifications.repository import NotificationRepository
    from src.platform.websocket.notification_connection_hub import notification_connection_hub
    from src.features.notifications import NotificationCollaborators
    from src.features.notifications import operations as notification_operations
    from src.features.notifications.routes import NotificationController

    notification_repository = NotificationRepository()
    notification_collaborators = NotificationCollaborators(
        repository=notification_repository,
        users=user_repository,
        plugins=plugin_registry,
        connections=notification_connection_hub,
        settings=settings,
    )
    # A bound callable, not a class instance: every unrelated feature that
    # takes a `notification_manager` collaborator (generation, automation,
    # inspirations, the plugin lifecycle hooks) only ever calls it, duck-typed
    # as `notification_manager(...)` - see `operations.notify`'s docstring.
    notification_manager = functools.partial(notification_operations.notify, notification_collaborators)
    _rr._global_notification_manager = notification_manager
    notification_controller = NotificationController(notification_collaborators)

    # Initialize LLM memory components
    from src.features.llm_memory.repository import LLMMemoryRepository

    llm_memory_repository = LLMMemoryRepository()

    # Initialize the LLM tool executor. The tool/chat-mode/resource
    # registries themselves were created earlier (before the plugin
    # registry) so startup plugin enabling could populate them.
    from src.features.llm.tools.executor import ToolExecutor

    tool_executor = ToolExecutor(tool_registry=tool_registry, llm_service=llm_service)

    # Global tool governance (admin enable/lock + per-user opt-out), layered
    # on top of the mode/session tool filtering above.
    from src.features.llm.tools.governance import ToolGovernanceEditor
    from src.features.llm.tools.governance_routes import ToolGovernanceController

    tool_governance_editor = ToolGovernanceEditor(
        repository=tool_governance_repository, tool_registry=tool_registry
    )
    tool_governance_controller = ToolGovernanceController(
        repository=tool_governance_repository,
        manager=tool_governance_editor,
        tool_registry=tool_registry,
        llm_repository=llm_repository,
    )

    # Initialize chat components (with tool executor and manager references)
    chat_repository = ChatRepository()
    response_processor = ResponseProcessor(plugin_registry=plugin_registry)
    chat_runtime = ChatRuntime(
        chat_repository=chat_repository,
        llm_service=llm_service,
        response_processor=response_processor,
        plugin_registry=plugin_registry,
        chat_mode_registry=chat_mode_registry,
        tool_executor=tool_executor,
        segment_category_repository=None,  # Will be set after the segment repositories are created
        saved_segment_repository=None,
        segment_template_repository=None,
        model_index_manager=None,  # Will be set after model_index_manager is created
        preset_manager=None,  # Will be set after preset_manager is created
        phrasebook_category_repository=phrasebook_category_repo,
        phrasebook_value_repository=phrasebook_value_repo,
        phrasebook_search=phrasebook_search,
        resource_registry=resource_registry,
        settings=settings,
    )

    # Initialize pre-chat actions
    from src.features.chat.pre_chat_actions import PreChatActionRegistry
    pre_chat_action_registry = PreChatActionRegistry(
        plugin_registry=plugin_registry,
        llm_repository=llm_repository,
    )
    chat_runtime.pre_chat_action_registry = pre_chat_action_registry

    # Chat controller. Turns are owned by a per-process registry so a client
    # disconnect (page reload) can't kill an in-flight response.
    from src.features.chat.routes import ChatController
    from src.features.chat.turns import ChatTurnRegistry
    _turn_timeout = 1800
    try:
        _turn_timeout = int(settings.get_setting("chat_turn_timeout_seconds", 1800))
    except (TypeError, ValueError):
        pass
    chat_turn_registry = ChatTurnRegistry(turn_timeout_seconds=_turn_timeout)
    chat_controller = ChatController(chat_runtime, chat_turn_registry)

    # Developer components
    from src.features.developer.pipes_documenter import PipesDocumenter
    from src.features.developer.template_functions_documenter import TemplateFunctionsDocumenter
    pipes_documenter = PipesDocumenter(pipe_catalog)
    template_functions_documenter = TemplateFunctionsDocumenter()
    from src.features.developer.routes import DeveloperController
    developer_controller = DeveloperController(template_functions_documenter, preset_template_loader)

    # Docs components (in-app Documentation feature - aggregates repo
    # markdown, plugin-manifest `docs:` entries, and live-reference APIs
    # into a role-filtered tree)
    from src.features.docs.routes import DocsController
    docs_controller = DocsController(plugin_registry, base_docs_path="docs", pipes_documenter=pipes_documenter)

    # Form components (the field-type registry was populated earlier, ahead
    # of plugin discovery, so builtin field types could be registered first)
    from src.features.forms.routes import FormController
    form_controller = FormController(field_type_registry, plugin_registry)

    # Field-type registry controller
    from src.features.fields.routes import FieldController
    field_controller = FieldController(field_type_registry)

    # Application layer services
    from src.platform.websocket.connection_hub import ConnectionHub

    pipeline_builder = PipelineBuilder(preset_template_loader, preset_processor)
    output_processor = OutputProcessor(settings, storage_driver=storage_driver)

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

    connection_hub = ConnectionHub()
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
    # to write a row at its generation.after_complete seam (see
    # `src.features.stats.operations.record_completion`; the orchestrator
    # builds its own `FilePresetRepository` wrapper around
    # `preset_template_loader` to resolve a preset's display name at write time).
    from src.features.stats.generation_stats_repository import GenerationStatsRepository

    generation_stats_repository = GenerationStatsRepository()

    file_service = FileStore(storage_driver=storage_driver)

    # Media index: built ahead of the orchestrator (like the stats manager)
    # so completed generations can queue their files for system tagging.
    from src.features.media_index.repository import MediaIndexRepository
    from src.features.media_index.indexer import MediaIndexer
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
        settings, download_queue=download_queue, model_lifecycle=model_lifecycle,
    )
    gallery_vector_store = GalleryVectorStore(
        persist_dir=str(Path(settings.get_setting("file_storage_directory", "storage")) / "chromadb"),
        embedder_slug=vision_embedder.embedder_slug,
    )
    text_embedding_provider = build_embedding_provider(settings, download_queue=download_queue)
    gallery_prompt_vector_store = GalleryPromptVectorStore(
        persist_dir=str(Path(settings.get_setting("file_storage_directory", "storage")) / "chromadb"),
        embedder_slug=text_embedding_provider.embedder_slug,
    )
    media_indexer = MediaIndexer(
        repository=media_index_repository,
        tagger_provider=build_tagger_provider(
            settings, download_queue=download_queue, model_lifecycle=model_lifecycle,
        ),
        file_service=file_service,
        vision_embedder=vision_embedder,
        gallery_vector_store=gallery_vector_store,
        text_embedding_provider=text_embedding_provider,
        gallery_prompt_vector_store=gallery_prompt_vector_store,
    )
    media_index_controller = MediaIndexController(media_indexer)

    generation_orchestrator = GenerationOrchestrator(
        pipeline_builder, backend_registry, connection_hub, settings, output_processor,
        preset_template_loader, status_tracker=generation_status_tracker,
        notification_manager=notification_manager,
        database_preset_repository=_preset_repo_for_orchestrator,
        model_access_policy=model_access_policy,
        user_repository=user_repository,
        generation_stats_repository=generation_stats_repository,
        media_indexer=media_indexer,
        gpu_monitor=gpu_monitor,
    )

    # Initialize generation history manager
    generation_repository = GenerationRepository()
    run_report_repository = GenerationRunReportRepository()
    run_report_recorder = RunReportRecorder(run_report_repository)
    generation_history_facade = GenerationHistoryFacade(
        generation_repo=generation_repository,
        file_service=file_service,
        plugin_registry=plugin_registry,
        media_index_repository=media_index_repository,
        settings=settings,
        media_indexer=media_indexer,
        preset_name_resolver=PresetNameResolver(preset_template_loader)
    )

    # Phrasebook controller (needs generation_orchestrator)
    from src.features.phrasebook.routes import PhrasebookController
    phrasebook_controller = PhrasebookController(
        category_repository=phrasebook_category_repo,
        value_repository=phrasebook_value_repo,
        plugin_registry=plugin_registry,
        preview_generator=phrasebook_preview_generator,
        generation_orchestrator=generation_orchestrator
    )

    # Segment components
    from src.features.segments.routes import SegmentController
    segment_category_repo = SegmentCategoryRepository()
    saved_segment_repo = SavedSegmentRepository()
    segment_template_repo = SegmentTemplateRepository()
    segment_controller = SegmentController(
        category_repository=segment_category_repo,
        segment_repository=saved_segment_repo,
        template_repository=segment_template_repo,
        plugin_registry=plugin_registry,
    )

    # Model index components
    from src.features.models.repository import ModelRepository
    from src.features.tags.repository import TagRepository
    from src.features.models.routes import ModelController
    model_repository = ModelRepository()
    tag_repository = TagRepository()
    def _generation_active() -> bool:
        snapshot = generation_orchestrator.queue.snapshot()
        return bool(snapshot["pending"]) or bool(snapshot["running"])

    model_index_manager = build_model_index_collaborators(
        model_repository=model_repository,
        tag_repository=tag_repository,
        plugin_registry=plugin_registry,
        settings=settings,
        download_queue=download_queue,
        models_root=models_dir,
        generation_active=_generation_active,
        storage_driver=storage_driver,
        attribute_definition_repository=attribute_definition_repository,
        user_attribute_repository=user_model_attribute_repository,
    )

    # Model library components (favorites/custom names + model collections)
    from src.features.model_library.repository.model_collection_repository import ModelCollectionRepository
    from src.features.model_library.repository.user_model_meta_repository import UserModelMetaRepository
    from src.features.model_library.routes import ModelCollectionController

    model_collection_repository = ModelCollectionRepository()
    user_model_meta_repository = UserModelMetaRepository()
    model_collection_controller = ModelCollectionController(model_collection_repository)

    model_controller = ModelController(
        model_index_manager, user_model_meta_repository, download_queue,
        attribute_definition_repository=attribute_definition_repository,
        model_attributes_manager=model_attributes_manager,
    )

    # Tag components
    from src.features.tags.routes import TagController
    from src.features.presets.repository import DatabasePresetRepository
    from src.features.presets.file_repository import FilePresetRepository

    tag_controller = TagController(
        tag_repository=tag_repository,
        plugin_registry=plugin_registry,
        database_preset_repository=DatabasePresetRepository(),
        file_preset_repository=FilePresetRepository(preset_template_loader),
    )

    # Collection components
    from src.features.collections.repository import CollectionRepository
    from src.features.collections.routes import CollectionController

    collection_repository = CollectionRepository()
    collection_controller = CollectionController(collection_repository)

    # Automation module components. The connection manager is the module-level
    # singleton from the platform websocket layer (src/platform/websocket),
    # imported here as an instance rather than at module scope, mirroring the
    # notification wiring above.
    from src.features.automation.repository import automation_repo
    from src.features.automation.context import AutomationServices
    from src.features.automation.engine import AutomationEngine
    from src.features.automation.runtime import AutomationRuntime
    from src.platform.websocket.automation_connection_hub import automation_connection_hub
    from src.features.automation.routes import AutomationController

    # `action.index_model` needs `index_single_model()` with its SHA256 dedup,
    # which lives on `src.features.models.indexer.ModelScanner` - the same
    # scanner `ModelIndexCollaborators` uses. Imported as the lazy proxy
    # singleton because its `__init__` reads the settings DB.
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
        gpu_monitor=gpu_monitor,
        settings=settings,
        backend_config_store=backend_registry.backend_config_store,
        model_lifecycle=model_lifecycle,
        backend_registry=backend_registry,
        backend_model_indexer=backend_model_indexer,
        user_group_repository=_user_group_repo_for_automation,
        media_indexer=media_indexer,
        generation_status_tracker=generation_status_tracker,
        model_collection_repository=model_collection_repository,
    )
    automation_engine = AutomationEngine(
        automation_repo,
        services=automation_services,
        plugin_registry=plugin_registry,
        registry=automation_node_type_registry,
        emit_ws=automation_connection_hub.broadcast,
    )
    automation_runtime = AutomationRuntime(
        automation_repo,
        automation_engine,
        plugin_registry=plugin_registry,
        registry=automation_node_type_registry,
        template_registry=automation_template_registry,
    )
    automation_controller = AutomationController(automation_runtime, registry=automation_node_type_registry)

    # Media components
    from src.features.generation.file_repository import file_repo, FileRepository
    from src.features.media import MediaStore, MediaTypeResolver, FilePathResolver, ImageProcessor, UploadRepository
    from src.features.media.routes import MediaController

    media_type_resolver = MediaTypeResolver()
    file_resolver = FilePathResolver(settings, preset_template_loader)
    image_processor = ImageProcessor()
    upload_repository = UploadRepository()
    media_store = MediaStore(
        file_resolver=file_resolver,
        image_processor=image_processor,
        media_type_resolver=media_type_resolver,
        file_repository=file_repo,
        generation_repository=generation_repository,
        settings=settings,
        file_service=file_service,
        plugin_registry=plugin_registry,
        upload_repository=upload_repository,
        storage_driver=storage_driver,
    )
    media_controller = MediaController(media_store)

    from src.features.media.editing.editor import MediaEditor
    from src.features.media.editing.routes import MediaEditController

    media_editor = MediaEditor(
        upload_repository=upload_repository,
        media_type_resolver=media_type_resolver,
        storage_driver=storage_driver,
    )
    media_edit_controller = MediaEditController(media_editor)

    # Library components (uploads as first-class, curatable resources)
    from src.features.library import LibraryCollaborators, LibraryRepository
    from src.features.library.routes import LibraryController

    library_repository = LibraryRepository()
    library_collaborators = LibraryCollaborators(
        repository=library_repository,
        upload_repository=upload_repository,
        tag_repository=tag_repository,
        file_repository=file_repo,
        file_resolver=file_resolver,
        file_store=file_service,
        storage_driver=storage_driver,
    )
    library_controller = LibraryController(library_collaborators)

    # Inspirations components (cross-user publishing of generations)
    from src.features.generation.parameter_repository import GenerationParameterRepository
    from src.features.inspirations import InspirationCollaborators, InspirationRepository
    from src.features.inspirations.routes import InspirationController

    inspiration_repository = InspirationRepository()
    inspiration_collaborators = InspirationCollaborators(
        repository=inspiration_repository,
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
    inspiration_controller = InspirationController(inspiration_collaborators)

    # Preset manager components
    from src.features.presets.file_repository import FilePresetRepository
    from src.features.presets.repository import DatabasePresetRepository
    from src.features.presets.routes import PresetController

    from src.features.user_groups.repository import UserGroupRepository as _UGR
    file_preset_repository = FilePresetRepository(preset_template_loader)
    database_preset_repository = DatabasePresetRepository()
    _user_group_repo_for_presets = _UGR()
    preset_manager = PresetCollaborators(
        preset_loader=preset_template_loader,
        preset_processor=preset_processor,
        template_processor=template_processor,
        file_repo=file_preset_repository,
        db_repo=database_preset_repository,
        user_repo=user_repository,
        group_repo=_user_group_repo_for_presets,
        pipeline_builder=pipeline_builder,
        pipe_catalog=pipe_catalog,
        plugins=plugin_registry,
        settings=settings
    )
    preset_controller = PresetController(
        preset_manager, backend_registry, media_store,
        model_access_policy=model_access_policy,
    )

    # Setup executors: wire the built-in step executors (one per recipe step
    # `kind` - see src/features/setup/executors/) onto the run manager's
    # executor-registry seam. Built here, not up near `recipe_catalog`, because
    # it needs preset_manager/backend_registry/pipeline_builder, constructed above.
    from src.features.setup.executors import build_default_executor_registry

    setup_runner.register_executor_registry(
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
            download_queue=download_queue,
        )
    )

    # Prompt database components
    from src.features.prompt_database.repository import PromptRepository
    from src.features.prompt_database.collaborators import PromptDatabaseCollaborators
    from src.features.prompt_database.vector_store import PromptVectorStore
    from src.features.prompt_database.routes import PromptDatabaseController

    prompt_repository = PromptRepository()
    # `text_embedding_provider`, built above for the media index's prompt_embed
    # pass, is the same provider/model - reused here under its established
    # name rather than constructing a second instance.
    embedding_provider = text_embedding_provider
    prompt_vector_store = PromptVectorStore(
        persist_dir=str(Path(settings.get_setting("file_storage_directory", "storage")) / "chromadb"),
        embedder_slug=embedding_provider.embedder_slug,
    )
    prompt_database = PromptDatabaseCollaborators(
        repository=prompt_repository,
        vector_store=prompt_vector_store,
        embedding_provider=embedding_provider,
        plugin_registry=plugin_registry,
    )
    prompt_database_controller = PromptDatabaseController(prompt_database)

    # Wire up deferred service references for ChatRuntime's tool context
    chat_runtime.segment_category_repository = segment_category_repo
    chat_runtime.saved_segment_repository = saved_segment_repo
    chat_runtime.segment_template_repository = segment_template_repo
    chat_runtime.model_index_manager = model_index_manager
    chat_runtime.preset_manager = preset_manager
    chat_runtime.prompt_database = prompt_database
    chat_runtime.generation_orchestrator = generation_orchestrator
    chat_runtime.llm_memory_repository = llm_memory_repository
    chat_runtime.media_indexer = media_indexer
    chat_runtime.tool_governance_repository = tool_governance_repository
    chat_runtime.collection_repository = collection_repository
    chat_runtime.tag_repository = tag_repository
    chat_runtime.generation_history_facade = generation_history_facade
    # Generation repositories for the @generations resource provider
    from src.features.generation.model_repository import GenerationModelRepository
    from src.features.generation.parameter_repository import GenerationParameterRepository
    chat_runtime.generation_repository = generation_repository
    chat_runtime.generation_parameter_repository = GenerationParameterRepository()
    chat_runtime.generation_model_repository = GenerationModelRepository()

    # Prompt enhancement pipeline (used by the enhance_prompt tool)
    from src.features.prompt_enhancement import PromptEnhancementCollaborators
    from src.features.prompt_enhancement.repository import (
        EnhancementFeedbackRepository,
    )

    enhancement_feedback_repository = EnhancementFeedbackRepository()
    prompt_enhancement_manager = PromptEnhancementCollaborators(
        llm_service=llm_service,
        prompt_database=prompt_database,
        model_index_manager=model_index_manager,
        llm_memory_repository=llm_memory_repository,
        feedback_repository=enhancement_feedback_repository,
        preset_manager=preset_manager,
    )
    chat_runtime.prompt_enhancement_manager = prompt_enhancement_manager

    # MCP (Model Context Protocol): per-user tokens exposing the same tool
    # surface a `generation`-mode chat session sees. Built here, after every
    # collaborator the tool context needs (segment/model/preset/prompt
    # database/generation/memory/media managers) is available — the same
    # ordering constraint chat_runtime's late-bound assignments above solve.
    from src.features.mcp.protocol import McpToolCollaborators
    from src.features.mcp.repository import McpTokenRepository

    mcp_token_repository = McpTokenRepository()
    mcp_tool_collaborators = McpToolCollaborators(
        tool_registry=tool_registry,
        tool_governance_repository=tool_governance_repository,
        llm_repository=llm_repository,
        segment_category_repository=segment_category_repo,
        saved_segment_repository=saved_segment_repo,
        segment_template_repository=segment_template_repo,
        model_index_manager=model_index_manager,
        preset_manager=preset_manager,
        phrasebook_category_repository=phrasebook_category_repo,
        phrasebook_value_repository=phrasebook_value_repo,
        prompt_database=prompt_database,
        generation_orchestrator=generation_orchestrator,
        llm_memory_repository=llm_memory_repository,
        prompt_enhancement_manager=prompt_enhancement_manager,
        media_indexer=media_indexer,
        settings=settings,
        collection_repository=collection_repository,
        tag_repository=tag_repository,
        plugin_registry=plugin_registry,
        generation_history_facade=generation_history_facade,
    )

    pre_chat_action_registry.discover_actions()

    # Stats components (depends on file_preset_repository for preset display names)
    from src.features.stats.repository import StatsRepository
    from src.features.stats.routes import StatsController

    stats_repository = StatsRepository()
    # `generation_stats_repository` was built earlier, ahead of
    # `generation_orchestrator`'s construction -- reused here, not rebuilt.
    stats_controller = StatsController(
        stats_repository=stats_repository,
        file_preset_repository=file_preset_repository,
        generation_stats_repository=generation_stats_repository,
    )

    # Session components
    from src.features.sessions.repository import SessionRepository
    from src.features.sessions.routes import SessionController

    session_repository = SessionRepository()
    # --- Session history (versions) ---
    # Appends an immutable snapshot on every save; `sessions` itself stays the
    # "current" state. `file_preset_repository` resolves a preset id's on-disk
    # display name, denormalized into each version's `summary` column at write
    # time (see migration 092 / SessionVersionRepository).
    from src.features.sessions.version_repository import SessionVersionRepository

    session_version_repository = SessionVersionRepository()
    session_controller = SessionController(
        session_repository=session_repository,
        plugin_registry=plugin_registry,
        session_version_repository=session_version_repository,
        file_preset_repository=file_preset_repository,
    )

    # Workspace components
    from src.features.workspaces.repository import WorkspaceRepository
    from src.features.workspaces.routes import WorkspaceController

    workspace_repository = WorkspaceRepository()
    workspace_controller = WorkspaceController(workspace_repository=workspace_repository)

    # User group components
    from src.features.user_groups.repository import UserGroupRepository
    from src.features.user_groups.routes import UserGroupController

    user_group_repository = UserGroupRepository()
    user_group_controller = UserGroupController(
        user_group_repository=user_group_repository,
        plugin_registry=plugin_registry,
    )

    # Assemble the container from the locals built above (field name == local
    # variable name). A missing/misnamed field surfaces immediately here.
    _locals = locals()
    return AppContainer(**{f.name: _locals[f.name] for f in dataclasses.fields(AppContainer)})
