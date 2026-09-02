"""
Canonical plugin manifest schema (pydantic v2).

This is the single source of truth for the shape of a plugin `manifest.yml`.
`src/platform/plugins/loader.py` validates raw manifest data against
`PluginManifestSchema` and converts the result into the downstream-facing
`PluginManifest` dataclass.

Only one format is accepted for `hooks` and `dependencies` - there is no
backward-compat fallback for legacy shapes.
"""

from enum import Enum
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PluginCategory(str, Enum):
    """
    Plugin catalogue grouping, used to organize the plugin list/marketplace UI.

    Unknown or omitted `category:` values in a manifest default to `OTHER`.
    """

    GENERATION = "generation"
    MODELS = "models"
    SYSTEM = "system"
    MEDIA = "media"
    WORKFLOW = "workflow"
    DEVELOPER = "developer"
    OTHER = "other"


class BackendHookSpec(BaseModel):
    """A single backend hook registration: `hooks.backend[]`."""

    model_config = ConfigDict(extra="forbid")

    hook: str
    handler: str
    # True marks a hook whose handler code must exist and run on a Remote
    # Native worker during pipeline execution (e.g. a `prompt.transform` that
    # runs inside the processed pipeline), as opposed to a hook that only runs
    # core-side before dispatch. Defaults to False - most hooks are core-only.
    # Read by `compute_remote_plugin_bundle_fingerprint`
    # (`src/pipelines/remote_fingerprint.py`) to decide whether a hook-only
    # plugin is remote-relevant.
    remote: bool = False


class FrontendHookSpec(BaseModel):
    """A single frontend hook registration: `hooks.frontend[]`."""

    model_config = ConfigDict(extra="forbid")

    hook: str
    component: Optional[str] = None
    handler: Optional[str] = None
    position: Optional[str] = None
    order: int = 0


class HooksSpec(BaseModel):
    """Canonical `hooks:` section - `backend`/`frontend` arrays only."""

    model_config = ConfigDict(extra="forbid")

    backend: List[BackendHookSpec] = Field(default_factory=list)
    frontend: List[FrontendHookSpec] = Field(default_factory=list)


class DependenciesSpec(BaseModel):
    """Canonical `dependencies:` section - `python`/`binaries` lists only."""

    model_config = ConfigDict(extra="forbid")

    python: List[str] = Field(default_factory=list)
    binaries: List[str] = Field(default_factory=list)


class PipeSpec(BaseModel):
    """A pipe registration: `pipes[]`."""

    model_config = ConfigDict(extra="allow")

    path: str
    register_as: Optional[str] = None


class ApiSpec(BaseModel):
    """The `api:` section - path to the plugin's FastAPI router module."""

    model_config = ConfigDict(extra="allow")

    module: str


class PresetsRootSpec(BaseModel):
    """A plugin-contributed preset root: `presets[]`.

    `path` is a directory (relative to the plugin dir) scanned recursively for
    `preset.yml` files, exactly like the core `presets/` tree. Presets keep the
    identity declared in their own `preset.yml` (`id:`), so a preset moved from
    core into a plugin keeps its ULID and stays selectable by existing
    generations.
    """

    model_config = ConfigDict(extra="forbid")

    path: str


class RecipesRootSpec(BaseModel):
    """A plugin-contributed setup-recipe root: `recipes[]`.

    `path` is a directory (relative to the plugin dir) scanned for
    `*.yml` recipe files, exactly like the core `content/recipes/` tree.
    Recipes keep the identity declared in their own `id:`, so a recipe moved
    from core into a plugin keeps referring to the same setup runs.
    """

    model_config = ConfigDict(extra="forbid")

    path: str


class PresetModeContributionSpec(BaseModel):
    """A plugin-contributed MODE targeting an existing preset: `preset_modes[]`
    - distinct from `presets[]`, which contributes whole new presets.

    `modes_root` is a directory (relative to the plugin dir) shaped exactly
    like a preset root's own layout minus `preset.yml`: it must contain a
    `modes/<name>/` subtree (`pipeline.yml` + `form.yml`(`/variants`)), and
    every mode dir found there is contributed to `target` - there is no
    separate per-mode enable list. `target` names the preset by its `id:`;
    a target that isn't installed/enabled is not an error (see
    `docs/presets.md` "Plugin-contributed modes").
    """

    model_config = ConfigDict(extra="forbid")

    target: str
    modes_root: str


class PageSpec(BaseModel):
    """A page registration: `pages[]`."""

    model_config = ConfigDict(extra="allow")

    route: str
    component: str
    label: str
    icon: Optional[str] = None


class SidebarItemSpec(BaseModel):
    """A sidebar entry: `sidebar[]`."""

    model_config = ConfigDict(extra="allow")

    label: str
    route: str
    icon: Optional[str] = None
    order: int = 100
    require_role: Optional[str] = None


class QuickActionSpec(BaseModel):
    """A quick action button: `quick_actions[]`."""

    model_config = ConfigDict(extra="allow")

    id: str
    label: str
    endpoint: str
    method: str = "POST"
    icon: Optional[str] = None
    confirm: Optional[str] = None
    require_role: Optional[str] = None


class SidebarWidgetSpec(BaseModel):
    """A sidebar widget: `sidebar_widgets[]`."""

    model_config = ConfigDict(extra="allow")

    id: str
    component: str
    position: str = "bottom"
    order: int = 100
    label: Optional[str] = None


class SettingSpec(BaseModel):
    """A plugin setting definition: `settings[]`. Shape varies by `type`."""

    model_config = ConfigDict(extra="allow")

    name: str
    type: str
    label: Optional[str] = None
    description: Optional[str] = None
    required: bool = False
    default: Optional[Any] = None
    is_secret: bool = False


# ---------------------------------------------------------------------------
# Inert sections - validated now, consumed by later phases (A3/A4/A5).
# ---------------------------------------------------------------------------

class FieldTypeSpec(BaseModel):
    """A plugin-provided form field type: `field_types[]` (used by A3/A4)."""

    model_config = ConfigDict(extra="forbid")

    type: str
    schema_class: Optional[str] = None
    options_handler: Optional[str] = None
    component: Optional[str] = None
    # Opts this type's values into the Inspirations allowlist snapshot
    # (default-deny: see FieldTypeDefinition.shareable). A plugin field type
    # carrying a file path/upload/user-storage reference must leave this False.
    shareable: bool = False


class ModelMetadataFieldEntry(BaseModel):
    """A plugin-provided model attribute definition: `model_metadata_fields[]`.

    Pure data - upserted directly into `model_attribute_definitions` (source =
    this plugin's id) by `PluginRegistry._register_plugin_model_metadata_fields`,
    no `schema_class`/`options_handler` code loading (unlike `FieldTypeSpec`).
    """

    model_config = ConfigDict(extra="forbid")

    key: str
    label: str
    field_type: str
    model_types: List[str] = Field(default_factory=list)
    config: Dict[str, Any] = Field(default_factory=dict)
    default_value: Optional[Any] = None
    description: Optional[str] = None
    per_user: bool = False
    admin_only: bool = False


class PromptImporterSpec(BaseModel):
    """A plugin-provided prompt import source: `prompt_importers[]`.

    `component` is the plugin frontend asset that renders the import modal;
    `backend` is a `"module.path:ClassName"` reference to a
    `src.plugin_api.prompts.PromptImporter` subclass, loaded the same way a
    `field_types[].schema_class` is.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    label: str
    component: str
    backend: str


class PhrasebookOpSpec(BaseModel):
    """A plugin-provided phrasebook batch tool: `phrasebook_ops[]`.

    `backend` is a `"module.path:ClassName"` reference to a
    `src.plugin_api.phrasebook.PhrasebookBatchOperation` subclass, loaded the
    same way a `prompt_importers[].backend` is; `component` is the optional
    plugin frontend asset that collects the operation's parameters.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    label: str
    backend: str
    component: Optional[str] = None


class DocEntry(BaseModel):
    """A plugin-provided documentation page: `docs[]` (used by the docs feature)."""

    model_config = ConfigDict(extra="forbid")

    title: str
    path: str
    audience: str = "user"
    order: int = 100
    category: Optional[str] = None
    category_order: Optional[int] = None


class RendererSpec(BaseModel):
    """A plugin-provided renderer: `renderers[]` (used by A5)."""

    model_config = ConfigDict(extra="forbid")

    kind: str
    key: str
    component: str


class ProvidedHookSpec(BaseModel):
    """A plugin-provided hook point with structured docs: `provides_hooks[]` object form."""

    model_config = ConfigDict(extra="forbid")

    name: str
    description: str = ""
    payload: Dict[str, Dict[str, str]] = Field(default_factory=dict)
    mutable: List[str] = Field(default_factory=list)
    use_when: List[str] = Field(default_factory=list)
    example: str = ""


class ContributionSpec(BaseModel):
    """A plugin-provided UI extension slot contribution: `contributions[]` (used by A5)."""

    model_config = ConfigDict(extra="forbid")

    slot: str
    component: str
    label: Optional[str] = None
    icon: Optional[str] = None
    route: Optional[str] = None
    order: int = 100
    require_role: Optional[str] = None


class ChatModeSpec(BaseModel):
    """A plugin-provided chat mode: `chat_modes[]`.

    Exactly one of `system_prompt` (inline text) or `system_prompt_file`
    (plugin-relative path) must be provided. The prompt may contain the
    ``{{TOOL_HINTS}}`` placeholder.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    description: str = ""
    icon: Optional[str] = None
    system_prompt: Optional[str] = None
    system_prompt_file: Optional[str] = None
    tools: List[str] = Field(default_factory=list)
    default_route_prefixes: List[str] = Field(default_factory=list)
    resource_namespaces: Optional[List[str]] = None
    context_contributor: Optional[str] = None  # "module.function" handler ref
    llm_options: Dict[str, Any] = Field(default_factory=dict)
    structured_reply: bool = True

    @model_validator(mode="after")
    def _validate_prompt_source(self) -> "ChatModeSpec":
        if self.system_prompt is not None and self.system_prompt_file is not None:
            raise ValueError("system_prompt and system_prompt_file are mutually exclusive")
        if self.system_prompt is None and self.system_prompt_file is None:
            raise ValueError("one of system_prompt or system_prompt_file is required")
        return self


class ChatToolSpec(BaseModel):
    """A plugin-provided LLM chat tool: `tools[]`."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    tool_class: str = Field(alias="class")  # "module.path:ClassName"
    modes: Optional[List[str]] = None  # omitted/None = global tool


class ResourceProviderSpec(BaseModel):
    """A plugin-provided @resource provider: `resources[]`."""

    model_config = ConfigDict(extra="forbid")

    namespace: str
    provider: str  # "module.path:ClassName"
    modes: Optional[List[str]] = None  # omitted/None = visible in all modes


class AutomationNodeSpec(BaseModel):
    """A plugin-provided automation node type: `automation_nodes[]`."""

    model_config = ConfigDict(extra="forbid")

    key: str
    kind: str  # "trigger" | "condition" | "action"
    title: str
    description: str = ""
    icon: str = ""
    category: str = "general"
    config_schema: List[Dict[str, Any]] = Field(default_factory=list)
    # Mirrors `NodeTypeSpec.outputs` - the data contract this node hands
    # downstream, one dict per field: {key, type?, label?, description?, example?}.
    # `extra="forbid"` above means a plugin cannot declare these unless they are
    # named here.
    outputs: List[Dict[str, Any]] = Field(default_factory=list)
    # Mirrors `NodeTypeSpec.dynamic_outputs` - set when the payload shape isn't
    # statically knowable (a custom trigger firing arbitrary data).
    dynamic_outputs: bool = False
    handler: Optional[str] = None  # "module.function" - execute() for condition/action nodes
    start_handler: Optional[str] = None  # "module.function" - start() for custom trigger nodes
    stop_handler: Optional[str] = None  # "module.function" - stop() for custom trigger nodes
    # Mirrors `NodeTypeSpec.dynamic_ports_config_key` (src/platform/plugins/automation_nodes.py) -
    # lets a plugin-provided condition/action derive its output ports from its
    # own config at edit/run time (switch-style nodes), same as `condition.switch`.
    dynamic_ports_config_key: Optional[str] = None


class AutomationTemplateSpec(BaseModel):
    """A plugin-provided immutable automation template: `automation_templates[]`."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    title: str = Field(min_length=1)
    path: str = Field(min_length=1)
    description: str = ""
    category: str = "general"
    icon: str = "bolt"
    tags: List[str] = Field(default_factory=list)


class PluginManifestSchema(BaseModel):
    """
    Canonical plugin manifest schema.

    Validated with `model_validate()` against the raw YAML dict loaded from
    `manifest.yml`. Unknown top-level keys are rejected (`extra="forbid"`).
    """

    model_config = ConfigDict(extra="forbid")

    # Required
    id: str
    name: str
    version: str
    description: str
    author: str
    type: Literal["frontend-only", "backend-only", "full-stack"]

    # Optional metadata
    category: PluginCategory = PluginCategory.OTHER
    capabilities: List[str] = Field(default_factory=list)
    homepage: Optional[str] = None
    repository: Optional[str] = None
    license: Optional[str] = None
    tags: List[str] = Field(default_factory=list)

    # Hooks / dependencies (canonical formats only)
    hooks: HooksSpec = Field(default_factory=HooksSpec)
    dependencies: DependenciesSpec = Field(default_factory=DependenciesSpec)

    # Backend components
    pipes: List[PipeSpec] = Field(default_factory=list)
    api: Optional[ApiSpec] = None

    # Preset roots this plugin contributes, scanned like the core presets/ tree
    presets: List[PresetsRootSpec] = Field(default_factory=list)
    # Modes this plugin contributes to OTHER (already-installed) presets - see
    # PresetModeContributionSpec.
    preset_modes: List[PresetModeContributionSpec] = Field(default_factory=list)
    # Setup-recipe roots this plugin contributes, scanned like the core
    # content/recipes/ tree - see RecipesRootSpec.
    recipes: List[RecipesRootSpec] = Field(default_factory=list)

    # Frontend components
    frontend: Optional[str] = None  # Path to a frontend entry point, if any
    pages: List[PageSpec] = Field(default_factory=list)
    sidebar: List[SidebarItemSpec] = Field(default_factory=list)
    quick_actions: List[QuickActionSpec] = Field(default_factory=list)
    sidebar_widgets: List[SidebarWidgetSpec] = Field(default_factory=list)
    settings: List[SettingSpec] = Field(default_factory=list)

    # Inert sections used by later phases
    field_types: List[FieldTypeSpec] = Field(default_factory=list)
    model_metadata_fields: List[ModelMetadataFieldEntry] = Field(default_factory=list)
    renderers: List[RendererSpec] = Field(default_factory=list)
    contributions: List[ContributionSpec] = Field(default_factory=list)
    provides_hooks: List[Union[str, ProvidedHookSpec]] = Field(default_factory=list)

    # Documentation pages this plugin contributes to the in-app Documentation feature
    docs: List[DocEntry] = Field(default_factory=list)

    # LLM chat extensions
    chat_modes: List[ChatModeSpec] = Field(default_factory=list)
    tools: List[ChatToolSpec] = Field(default_factory=list)
    resources: List[ResourceProviderSpec] = Field(default_factory=list)

    # Automation module node types
    automation_nodes: List[AutomationNodeSpec] = Field(default_factory=list)
    automation_templates: List[AutomationTemplateSpec] = Field(default_factory=list)

    # Prompt library import sources
    prompt_importers: List[PromptImporterSpec] = Field(default_factory=list)

    # Phrasebook batch tools
    phrasebook_ops: List[PhrasebookOpSpec] = Field(default_factory=list)
