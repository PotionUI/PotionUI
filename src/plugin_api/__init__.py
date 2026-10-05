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
`.identity`, `.limits`, `.media`, `.organize`, `.phrasebook`, `.recipes`, `.sampling`) are the same
names, grouped, and each one's docstring explains what it is for. Import from
whichever reads better.

If you need something the application can do but this module does not expose,
that is a gap in the API - ask for it to be added rather than importing around
it. See docs/plugin-api.md.
"""

import importlib

# Who is calling - the identity every request carries.
from src.plugin_api.identity import (
    AccountType,
    ExternalLoginError,
    ExternalSession,
    User,
    authenticate_websocket_token,
    get_current_active_user,
    get_current_admin_user,
    get_user,
    list_user_ids,
    register_login_provider,
    sign_in_external,
    unregister_login_provider,
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
    RemoteDownloadRef,
    ensure_providers_discovered,
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
    ComputeFieldDescriptorV1,
    ComputeFieldOptionV1,
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
    TextArtifactAction,
    TextGenerationOutput,
    VideoGenerationOutput,
    output_type_registry,
)

# Presets, and starting a generation.
from src.plugin_api.presets import (
    DirectorBindingError,
    FieldDescription,
    FilePresetRepository,
    GalleryItem,
    GenerationRequest,
    ModeDescription,
    PRESET_COVER_VIDEO_MAX_BYTES,
    PRESET_MEDIA_MAX_BYTES,
    PresetCollaborators,
    PresetDescription,
    PresetMedia,
    bind_director_media,
    describe_preset,
    downscale_and_save_webp,
    lint_preset_dir,
    preset_operations,
    PromptPair,
)

# Keeping data. `db` is deliberately absent here - see __getattr__ below.
from src.plugin_api.storage import (
    PluginRepository,
    SettingRepository,
    Settings,
    generate_ulid,
)

# Contributing an automation node.
from src.plugin_api.automation import (
    NodeExecutionContext,
    NodeField,
    NodeResult,
)

# Working with images.
from src.plugin_api.media import (
    FfmpegUnavailableError,
    TranscodeSpec,
    VideoProbe,
    VideoTranscodeError,
    convert_image_to_base64,
    find_ffmpeg,
    probe_video,
    transcode_video,
)

# Contributing a prompt import source.
from src.plugin_api.prompts import (
    PromptImporter,
    PromptImportOutcome,
    create_prompt_for_user,
    find_prompt_source_ids,
    import_prompts_for_user,
)

# Shipping a recipe, and contributing a recipe step kind.
from src.plugin_api.recipes import (
    Recipe,
    RecipeArtifact,
    RecipePresetRef,
    RecipeRun,
    RecipeRunStatus,
    RecipeSmokeRef,
    RecipeStep,
    RecipeStepStatus,
    StepContext,
    StepExecutor,
    StepResult,
)

from src.plugin_api.organize import (
    OrganizeActionBlocked,
    OrganizeChange,
    OrganizeItem,
)

from src.plugin_api.limits import (
    LimitEvents,
    LimitExceeded,
    LimitKind,
    MeasureContext,
    Usage,
)

from src.plugin_api.setup import SetupCheck, SetupCheckContext, SetupCheckResult

# Contributing a phrasebook batch tool.
from src.plugin_api.phrasebook import (
    BatchOperationError,
    BatchOutcome,
    BatchPreview,
    PhrasebookBatchContext,
    PhrasebookBatchOperation,
)

# Model metadata field identifiers, and a model's marketplace provider link.
from src.plugin_api.models import (
    MODEL_DIRECTORY_ALIASES,
    WellKnownModelMetadataField,
    get_model_provider_info,
    model_for_path,
    model_type_dirs,
    model_write_dir,
    resolve_model_file,
    type_for_folder_name,
)

# Contributing a step algorithm or a sigma schedule to the native engine.
from src.platform.plugins.sampling import (
    OptionSpec,
    SamplerDefinition,
    ScheduleContext,
    ScheduleDefinition,
    sampler_registry,
    schedule_registry,
)

__all__ = [
    "OrganizeActionBlocked",
    "OrganizeChange",
    "OrganizeItem",
    "LimitEvents",
    "LimitExceeded",
    "LimitKind",
    "MeasureContext",
    "Usage",
    # Identity
    "AccountType",
    "ExternalLoginError",
    "ExternalSession",
    "User",
    "authenticate_websocket_token",
    "get_current_active_user",
    "get_current_admin_user",
    "get_user",
    "list_user_ids",
    "register_login_provider",
    "sign_in_external",
    "unregister_login_provider",
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
    "RemoteDownloadRef",
    "ensure_providers_discovered",
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
    "ComputeFieldDescriptorV1",
    "ComputeFieldOptionV1",
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
    "TextArtifactAction",
    "TextGenerationOutput",
    "VideoGenerationOutput",
    "output_type_registry",
    # Native engine generation
    "Conditioning",
    "GeneratorContext",
    "GeneratorKrea2Pipe",
    "NativeGeneratorHandle",
    "ProgressEmitter",
    "native_step_hooks",
    # Presets and generation
    "DirectorBindingError",
    "FieldDescription",
    "FilePresetRepository",
    "GalleryItem",
    "GenerationRequest",
    "ModeDescription",
    "PresetCollaborators",
    "PRESET_COVER_VIDEO_MAX_BYTES",
    "PRESET_MEDIA_MAX_BYTES",
    "PresetDescription",
    "PresetMedia",
    "bind_director_media",
    "describe_preset",
    "downscale_and_save_webp",
    "lint_preset_dir",
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
    "FfmpegUnavailableError",
    "TranscodeSpec",
    "VideoProbe",
    "VideoTranscodeError",
    "find_ffmpeg",
    "probe_video",
    "transcode_video",
    # Prompt import sources
    "PromptImporter",
    "PromptImportOutcome",
    "create_prompt_for_user",
    "find_prompt_source_ids",
    "import_prompts_for_user",
    # Recipes and recipe step kinds
    "Recipe",
    "RecipeArtifact",
    "RecipePresetRef",
    "RecipeRun",
    "RecipeRunStatus",
    "RecipeSmokeRef",
    "RecipeStep",
    "RecipeStepStatus",
    "StepContext",
    "StepExecutor",
    "StepResult",
    # Phrasebook batch tools
    "BatchOperationError",
    "BatchOutcome",
    "BatchPreview",
    "PhrasebookBatchContext",
    "PhrasebookBatchOperation",
    # Model metadata fields, and a model's marketplace provider link
    "WellKnownModelMetadataField",
    "get_model_provider_info",
    "MODEL_DIRECTORY_ALIASES",
    "model_for_path",
    "model_type_dirs",
    "model_write_dir",
    "resolve_model_file",
    "type_for_folder_name",
    "SetupCheck",
    "SetupCheckContext",
    "SetupCheckResult",
    # Samplers and schedules
    "OptionSpec",
    "SamplerDefinition",
    "SamplingCancelled",
    "ScheduleContext",
    "ScheduleDefinition",
    "sample_euler",
    "sampler_registry",
    "schedule_registry",
]


_TORCH_BACKED_EXPORTS = {
    "Conditioning": "src.plugin_api.native",
    "GeneratorContext": "src.plugin_api.native",
    "GeneratorKrea2Pipe": "src.plugin_api.native",
    "NativeGeneratorHandle": "src.plugin_api.native",
    "ProgressEmitter": "src.plugin_api.native",
    "native_step_hooks": "src.plugin_api.native",
    "BackgroundMattingModel": "src.plugin_api.media",
    "SamplingCancelled": "src.plugin_api.sampling",
    "sample_euler": "src.plugin_api.sampling",
}


def __getattr__(name):
    """`db` is resolved on access, not bound here at import time - a plugin
    importing this module before a test patches the process-default
    `Database` singleton would otherwise keep the pre-patch reference for the
    rest of the process. Plugins should still reach for `db` inside the
    function that uses it, not at their own module top level, so that even
    the binding they make is short-lived."""
    if name == "db":
        from src.platform.database.database import db
        return db
    module_name = _TORCH_BACKED_EXPORTS.get(name)
    if module_name is not None:
        return getattr(importlib.import_module(module_name), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
