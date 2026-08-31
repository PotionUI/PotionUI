"""The public API for PotionUI plugins.

**This is the only part of `src/` a plugin may import.** Everything else is
internal: it gets moved, renamed and rewritten without notice, and a plugin that
reaches into it will break. What is exported here will not be removed or changed
out from under you without a deprecation.

Import from the package itself - every name below is available directly:

    from src.plugin_api import User, AccountType, get_current_active_user
    from src.plugin_api import BaseTool, ToolContext, ToolResult

The capability modules (`src.plugin_api.providers`, `.chat`, `.backends`,
`.compute`, `.pipes`, `.native`, `.hooks`, `.storage`, `.presets`, `.forms`,
`.identity`, `.media`) are the same names, grouped, and each one's docstring
explains what it is for. Import from whichever reads better.

If you need something the application can do but this module does not expose,
that is a gap in the API - ask for it to be added rather than importing around
it. See docs/plugin-api.md.
"""

# Who is calling - the identity every request carries.
from src.plugin_api.identity import (
    AccountType,
    User,
    authenticate_websocket_token,
    get_current_active_user,
    get_current_admin_user,
)

# Hooking into the application, and reaching its wired-up managers.
from src.plugin_api.hooks import (
    GenerationNotFoundException,
    HookContext,
    HookResult,
    HookSpec,
    ModelLifecycle,
    PluginRegistry,
    get_container,
    get_global_plugin_registry,
    get_global_tool_registry,
    hooks_registry,
)

# Talking to a model marketplace.
from src.plugin_api.providers import (
    MarketplaceProviderBase,
    ModelInfo,
    ProviderCapability,
    ProviderConnectionError,
    ProviderError,
    ProviderMetadata,
    ProviderModelInfo,
    ProviderNotFoundError,
    ProviderPromptItem,
    ProviderRateLimitError,
    ProviderSearchResult,
    get_provider_registry,
)

# Extending the chat assistant.
from src.plugin_api.chat import (
    BaseTool,
    PreChatAction,
    ToolContext,
    ToolResult,
    ToolSource,
)

# Contributing an engine.
from src.plugin_api.backends import (
    BackendHealth,
    BackendModel,
    BackendStatus,
    BaseBackendConfig,
    InProcessBackend,
    ModelListingNotSupported,
    deduplicate,
)

# Provisioning rented GPU compute.
from src.plugin_api.compute import (
    COMPUTE_HOOKS,
    ComputeGpuType,
    ComputeProvisioner,
    ComputeProvisionerError,
    ComputeStatus,
    ProvisionRequest,
    ProvisionResult,
)

# Contributing a pipe, and the outputs it emits while it runs.
from src.plugin_api.pipes import (
    AudioGenerationOutput,
    BasePipe,
    ComfyUIWorkflowGenerationOutput,
    DuplicateOutputTypeError,
    GalleryGenerationOutput,
    GenerationExecutionError,
    GenerationOutput,
    IOType,
    Icon,
    ImageGenerationOutput,
    MeshGenerationOutput,
    OutputTypeSpec,
    PipeConfigSpec,
    PipeInput,
    PipeInputSpec,
    PipeOutput,
    PipeOutputSpec,
    Progress,
    ProgressGenerationOutput,
    SerializeContext,
    VideoGenerationOutput,
    logger,
    output_type_registry,
)

# Driving generation against the native (in-process) engine directly.
from src.plugin_api.native import (
    Conditioning,
    GeneratorContext,
    GeneratorKrea2Pipe,
    NativeGeneratorHandle,
    ProgressEmitter,
    native_step_hooks,
)

# Presets, and starting a generation.
from src.plugin_api.presets import (
    FilePresetRepository,
    GenerationRequest,
    PresetCollaborators,
    preset_operations,
    PromptPair,
)

# Keeping data.
from src.plugin_api.storage import (
    PluginRepository,
    SettingRepository,
    Settings,
    db,
    generate_ulid,
)

# Contributing an automation node.
from src.plugin_api.automation import (
    NodeExecutionContext,
    NodeField,
    NodeResult,
)

# Working with images.
from src.plugin_api.media import BackgroundMattingModel, convert_image_to_base64

# Contributing a prompt import source.
from src.plugin_api.prompts import PromptImporter, PromptImportOutcome, create_prompt_for_user

# Model metadata field identifiers.
from src.plugin_api.models import WellKnownModelMetadataField

__all__ = [
    # Identity
    "AccountType",
    "User",
    "authenticate_websocket_token",
    "get_current_active_user",
    "get_current_admin_user",
    # Hooks and runtime
    "GenerationNotFoundException",
    "HookContext",
    "HookResult",
    "HookSpec",
    "ModelLifecycle",
    "PluginRegistry",
    "get_container",
    "get_global_plugin_registry",
    "get_global_tool_registry",
    "hooks_registry",
    # Providers
    "MarketplaceProviderBase",
    "ModelInfo",
    "ProviderCapability",
    "ProviderConnectionError",
    "ProviderError",
    "ProviderMetadata",
    "ProviderModelInfo",
    "ProviderNotFoundError",
    "ProviderPromptItem",
    "ProviderRateLimitError",
    "ProviderSearchResult",
    "get_provider_registry",
    # Chat
    "BaseTool",
    "PreChatAction",
    "ToolContext",
    "ToolResult",
    "ToolSource",
    # Backends and engines
    "BackendHealth",
    "BackendModel",
    "BackendStatus",
    "BaseBackendConfig",
    "InProcessBackend",
    "ModelListingNotSupported",
    "deduplicate",
    # Compute provisioning
    "COMPUTE_HOOKS",
    "ComputeGpuType",
    "ComputeProvisioner",
    "ComputeProvisionerError",
    "ComputeStatus",
    "ProvisionRequest",
    "ProvisionResult",
    # Pipes
    "AudioGenerationOutput",
    "BasePipe",
    "ComfyUIWorkflowGenerationOutput",
    "DuplicateOutputTypeError",
    "GalleryGenerationOutput",
    "GenerationExecutionError",
    "GenerationOutput",
    "IOType",
    "Icon",
    "ImageGenerationOutput",
    "MeshGenerationOutput",
    "OutputTypeSpec",
    "PipeConfigSpec",
    "PipeInput",
    "PipeInputSpec",
    "PipeOutput",
    "PipeOutputSpec",
    "Progress",
    "ProgressGenerationOutput",
    "SerializeContext",
    "VideoGenerationOutput",
    "logger",
    "output_type_registry",
    # Native engine generation
    "Conditioning",
    "GeneratorContext",
    "GeneratorKrea2Pipe",
    "NativeGeneratorHandle",
    "ProgressEmitter",
    "native_step_hooks",
    # Presets and generation
    "FilePresetRepository",
    "GenerationRequest",
    "PresetCollaborators",
    "preset_operations",
    "PromptPair",
    # Storage
    "PluginRepository",
    "SettingRepository",
    "Settings",
    "db",
    "generate_ulid",
    # Automation nodes
    "NodeExecutionContext",
    "NodeField",
    "NodeResult",
    # Media
    "BackgroundMattingModel",
    "convert_image_to_base64",
    # Prompt import sources
    "PromptImporter",
    "PromptImportOutcome",
    "create_prompt_for_user",
    # Model metadata fields
    "WellKnownModelMetadataField",
]
