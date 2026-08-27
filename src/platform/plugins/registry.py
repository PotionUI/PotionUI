"""
Plugin registry for managing plugin lifecycle and execution.

This module provides the central registry for managing plugins, their state,
and their integration with the hook system.
"""

import threading
from typing import Dict, List, Optional, Set, Tuple, TYPE_CHECKING
from enum import Enum
import logging

from src.platform.plugins.loader import PluginLoader, PluginManifest
from src.platform.plugins.hooks import HookChain, HookContext, hooks_registry
from src.platform.plugins.lifecycle_hooks import PLUGIN_LIFECYCLE_HOOKS
from src.platform.plugins.router_mounter import PluginRouterMounter
from src.platform.plugins.field_types import FieldTypeDefinition, FieldTypeRegistry, DuplicateFieldTypeError
from src.platform.plugins.prompt_importers import PromptImporterRegistry
from src.platform.plugins.automation_templates import (
    AutomationTemplateRegistrationError,
    AutomationTemplateRegistry,
    plugin_template_source,
)

if TYPE_CHECKING:
    # Type-only: `src.features.models.attributes` is a features-layer module,
    # so platform may not import it at runtime (see tests/architecture). The
    # manager is duck-typed here (`.upsert_from_plugin` / `.remove_source`).
    from src.features.models.attributes.editor import ModelAttributeDefinitionsEditor

logger = logging.getLogger(__name__)

# Hook names already warned about having a failing handler - a plugin that
# fails a hook on every call (e.g. every generation, every request) would
# otherwise re-log the same warning constantly instead of once per process.
_warned_hook_failures: Set[str] = set()


class PluginState(Enum):
    """Plugin lifecycle states"""
    DISCOVERED = "discovered"  # Found but not loaded
    LOADED = "loaded"  # Loaded but not enabled
    ENABLED = "enabled"  # Active and hooks registered
    ERROR = "error"  # Failed to load or enable
    DISABLED = "disabled"  # Explicitly disabled by user


class PluginRegistry:
    """
    Central registry for managing plugins.

    This registry handles:
    - Plugin discovery and loading
    - Enable/disable state management
    - Hook registration and execution
    - Thread-safe operations
    """

    def __init__(
        self,
        marketplace_dir: str = "content/plugins/marketplace",
        local_dir: str = "content/plugins/local",
        field_registry: Optional[FieldTypeRegistry] = None,
        model_attributes_manager: Optional["ModelAttributeDefinitionsEditor"] = None,
        router_mounter: Optional[PluginRouterMounter] = None,
        tool_registry=None,
        chat_mode_registry=None,
        resource_registry=None,
        automation_node_registry=None,
        automation_template_registry: Optional[AutomationTemplateRegistry] = None,
        prompt_importer_registry: Optional[PromptImporterRegistry] = None,
    ):
        self.loader = PluginLoader(marketplace_dir, local_dir)
        self.hook_chain = HookChain()
        # Registry plugin-provided form field types are wired into on enable
        # and removed from on disable. Optional - the app runs fine (just
        # without plugin field types) if this isn't injected.
        self.field_registry = field_registry
        # Plugin-provided model attribute definitions (DB-backed - see
        # src.features.models.attributes) are upserted on enable and removed by
        # source on disable. Optional like field_registry.
        self.model_attributes_manager = model_attributes_manager
        # Mounts/unmounts `api.module` routers on enable/disable. Optional -
        # the app runs fine (just without dynamic API routes) if this isn't
        # attached to a live FastAPI app yet (e.g. during discovery-only use
        # or in unit tests that don't need HTTP routes).
        self.router_mounter = router_mounter
        # LLM chat extension registries (ToolRegistry, ChatModeRegistry,
        # ResourceRegistry). Optional like field_registry - plugins declaring
        # `tools:`/`chat_modes:`/`resources:` manifest sections register into
        # these on enable and are removed (by source) on disable. They must be
        # injected before the first enable_plugin call for those sections to
        # take effect.
        self.tool_registry = tool_registry
        self.chat_mode_registry = chat_mode_registry
        self.resource_registry = resource_registry
        # Automation module node-type registry (src.platform.plugins.automation_nodes.
        # NodeTypeRegistry). Optional like the others - plugins declaring
        # `automation_nodes:` register into it on enable and are removed (by
        # source) on disable.
        self.automation_node_registry = automation_node_registry
        # Immutable automation templates contributed by enabled plugins. They
        # register after node types so a template may depend on nodes from the
        # same plugin, and disappear with that plugin on disable.
        self.automation_template_registry = automation_template_registry
        # Prompt-library import sources contributed by enabled plugins.
        # Optional like the others - plugins declaring `prompt_importers:`
        # register into it on enable and are removed (by source) on disable.
        self.prompt_importer_registry = prompt_importer_registry

        # Plugin storage
        self._plugins: Dict[str, PluginManifest] = {}
        self._plugin_states: Dict[str, PluginState] = {}
        self._plugin_errors: Dict[str, str] = {}

        # Discovery state
        self._discovered = False
        self._lock = threading.Lock()

        # Track which plugins have registered which hooks
        self._plugin_hooks: Dict[str, Set[str]] = {}  # plugin_id -> set of hook names

    def _ensure_discovered(self):
        """Ensure plugins are discovered (lazy loading with thread safety)"""
        if not self._discovered:
            with self._lock:
                # Double-check pattern for thread safety
                if not self._discovered:
                    self._do_discover_plugins()
                    self._discovered = True

    def _do_discover_plugins(self):
        """Internal method to actually discover plugins"""
        logger.info("Starting plugin discovery...")

        try:
            manifests = self.loader.discover_plugins()

            for manifest in manifests:
                self._plugins[manifest.id] = manifest
                self._plugin_hooks[manifest.id] = set()

                if manifest.validation_error:
                    self._plugin_states[manifest.id] = PluginState.ERROR
                    self._plugin_errors[manifest.id] = manifest.validation_error
                else:
                    self._plugin_states[manifest.id] = PluginState.DISCOVERED

            logger.info(f"Discovered {len(self._plugins)} plugins")

        except Exception as e:
            logger.error(f"Error during plugin discovery: {e}", exc_info=True)

    def discover_plugins(self):
        """Force plugin discovery"""
        with self._lock:
            self._do_discover_plugins()
            self._discovered = True

    def get_all_plugins(self) -> List[PluginManifest]:
        """Get all discovered plugins"""
        self._ensure_discovered()
        return list(self._plugins.values())

    def get_plugin(self, plugin_id: str) -> Optional[PluginManifest]:
        """Get a specific plugin by ID"""
        self._ensure_discovered()
        return self._plugins.get(plugin_id)

    def get_plugin_state(self, plugin_id: str) -> Optional[PluginState]:
        """Get the current state of a plugin"""
        self._ensure_discovered()
        return self._plugin_states.get(plugin_id)

    def get_plugin_error(self, plugin_id: str) -> Optional[str]:
        """Get the error message for a plugin in ERROR state"""
        self._ensure_discovered()
        return self._plugin_errors.get(plugin_id)

    def get_enabled_plugins(self) -> List[PluginManifest]:
        """Get all currently enabled plugins"""
        self._ensure_discovered()
        return [
            manifest for plugin_id, manifest in self._plugins.items()
            if self._plugin_states.get(plugin_id) == PluginState.ENABLED
        ]

    def enable_plugin(self, plugin_id: str) -> bool:
        """
        Enable a plugin and register its hooks.

        Args:
            plugin_id: ID of the plugin to enable

        Returns:
            True if successfully enabled, False otherwise
        """
        self._ensure_discovered()

        with self._lock:
            if plugin_id not in self._plugins:
                logger.error(f"Plugin not found: {plugin_id}")
                return False

            manifest = self._plugins[plugin_id]
            current_state = self._plugin_states.get(plugin_id)

            # Check if already enabled
            if current_state == PluginState.ENABLED:
                logger.debug(f"Plugin {plugin_id} is already enabled")
                return True

            # A plugin with an invalid manifest can never be enabled
            if manifest.validation_error:
                logger.error(
                    f"Cannot enable plugin {plugin_id}: invalid manifest "
                    f"({manifest.validation_error})"
                )
                self._plugin_states[plugin_id] = PluginState.ERROR
                self._plugin_errors[plugin_id] = manifest.validation_error
                return False

            try:
                # Validate dependencies
                deps_satisfied, missing_deps = self.loader.validate_dependencies(manifest)
                if not deps_satisfied:
                    error_msg = f"Missing dependencies: {', '.join(missing_deps)}"
                    logger.error(f"Cannot enable plugin {plugin_id}: {error_msg}")
                    self._plugin_states[plugin_id] = PluginState.ERROR
                    self._plugin_errors[plugin_id] = error_msg
                    return False

                # Load and register hooks
                for hook_name, handler_path in manifest.hooks.items():
                    if hooks_registry.get(hook_name) is None:
                        logger.warning(
                            f"Plugin {plugin_id} registers handler for undeclared hook "
                            f"'{hook_name}' (typo, or a hook point that no longer exists?)"
                        )

                    handler = self.loader.load_hook_handler(manifest, handler_path)
                    if handler is None:
                        error_msg = f"Failed to load hook handler: {handler_path}"
                        logger.error(f"Cannot enable plugin {plugin_id}: {error_msg}")
                        self._plugin_states[plugin_id] = PluginState.ERROR
                        self._plugin_errors[plugin_id] = error_msg
                        return False

                    # Register the handler with the hook chain
                    self.hook_chain.register(hook_name, plugin_id, handler)
                    self._plugin_hooks[plugin_id].add(hook_name)

                # Load and register plugin-provided form field types
                if self.field_registry is not None and manifest.field_types:
                    error_msg = self._register_plugin_field_types(manifest)
                    if error_msg:
                        logger.error(f"Cannot enable plugin {plugin_id}: {error_msg}")
                        self.field_registry.unregister_source(plugin_id)
                        self._plugin_states[plugin_id] = PluginState.ERROR
                        self._plugin_errors[plugin_id] = error_msg
                        return False

                # Load and register plugin-provided model attribute definitions
                if self.model_attributes_manager is not None and manifest.model_metadata_fields:
                    error_msg = self._register_plugin_model_metadata_fields(manifest)
                    if error_msg:
                        logger.error(f"Cannot enable plugin {plugin_id}: {error_msg}")
                        self.model_attributes_manager.remove_source(plugin_id)
                        self._plugin_states[plugin_id] = PluginState.ERROR
                        self._plugin_errors[plugin_id] = error_msg
                        return False

                # Load and register LLM chat extensions (tools, chat modes,
                # resource providers)
                for register_step in (
                    self._register_plugin_tools,
                    self._register_plugin_chat_modes,
                    self._register_plugin_resources,
                    self._register_plugin_automation_nodes,
                    self._register_plugin_automation_templates,
                    self._register_plugin_prompt_importers,
                ):
                    error_msg = register_step(manifest)
                    if error_msg:
                        logger.error(f"Cannot enable plugin {plugin_id}: {error_msg}")
                        self._rollback_partial_enable(plugin_id)
                        self._plugin_states[plugin_id] = PluginState.ERROR
                        self._plugin_errors[plugin_id] = error_msg
                        return False

                # Mount the plugin's API router(s), if it declares any
                if self.router_mounter is not None:
                    if not self.router_mounter.mount(manifest, loader=self.loader):
                        error_msg = "Failed to mount plugin API router"
                        logger.error(f"Cannot enable plugin {plugin_id}: {error_msg}")
                        self._rollback_partial_enable(plugin_id)
                        self._plugin_states[plugin_id] = PluginState.ERROR
                        self._plugin_errors[plugin_id] = error_msg
                        return False

                # Update state
                self._plugin_states[plugin_id] = PluginState.ENABLED
                if plugin_id in self._plugin_errors:
                    del self._plugin_errors[plugin_id]

                logger.info(
                    f"Enabled plugin {manifest.name} ({plugin_id}) with "
                    f"{len(manifest.hooks)} hooks"
                )
                return True

            except Exception as e:
                error_msg = f"Error enabling plugin: {e}"
                logger.error(f"Failed to enable plugin {plugin_id}: {error_msg}", exc_info=True)
                self._plugin_states[plugin_id] = PluginState.ERROR
                self._plugin_errors[plugin_id] = error_msg
                return False

    def _register_plugin_field_types(self, manifest: PluginManifest) -> Optional[str]:
        """
        Load and register a plugin's `field_types:` manifest entries onto
        `self.field_registry`.

        Returns an error message string on failure (schema class / options
        handler failed to load, or the type name collides with an existing
        registration), or None on success.
        """
        plugin_id = manifest.id

        for field_type in manifest.field_types:
            type_name = field_type.get('type')
            if not type_name:
                return "field_types entry missing 'type'"

            schema_cls = None
            schema_class_ref = field_type.get('schema_class')
            if schema_class_ref:
                schema_cls = self.loader.load_class(manifest, schema_class_ref)
                if schema_cls is None:
                    return f"Failed to load field schema class: {schema_class_ref}"

            options_provider = None
            options_handler_ref = field_type.get('options_handler')
            if options_handler_ref:
                options_provider = self.loader.load_hook_handler(manifest, options_handler_ref)
                if options_provider is None:
                    return f"Failed to load field options handler: {options_handler_ref}"

            component = field_type.get('component')
            frontend_component = f"plugin:{plugin_id}:{component}" if component else ""

            try:
                self.field_registry.register(FieldTypeDefinition(
                    type_name=type_name,
                    schema_cls=schema_cls,
                    options_provider=options_provider,
                    frontend_component=frontend_component,
                    container=False,
                    source=plugin_id,
                    shareable=bool(field_type.get('shareable', False)),
                ))
            except DuplicateFieldTypeError as e:
                return str(e)

        return None

    def _register_plugin_model_metadata_fields(self, manifest: PluginManifest) -> Optional[str]:
        """
        Upsert a plugin's `model_metadata_fields:` manifest entries into
        `self.model_attributes_manager` as definitions it owns.

        Returns an error message string on failure (missing required key, or
        the `key` collides with a definition owned by someone else - including
        core), or None on success.
        """
        return self.model_attributes_manager.upsert_from_plugin(manifest.id, manifest.model_metadata_fields)

    def _register_plugin_prompt_importers(self, manifest: PluginManifest) -> Optional[str]:
        """
        Load and register a plugin's `prompt_importers:` manifest entries onto
        `self.prompt_importer_registry`.

        Returns an error message string on failure (backend class failed to
        load/instantiate, or the id collides with an existing registration),
        or None on success.
        """
        skip, error = self._require_registry(
            manifest.prompt_importers, self.prompt_importer_registry, "prompt_importers", "prompt importer"
        )
        if skip:
            return error

        from src.platform.plugins.prompt_importers import (
            DuplicatePromptImporterError,
            PromptImporterDefinition,
        )

        plugin_id = manifest.id

        for entry in manifest.prompt_importers:
            importer_id = entry.get('id')
            if not importer_id:
                return "prompt_importers entry missing 'id'"

            backend_ref = entry.get('backend')
            if not backend_ref:
                return f"prompt_importers entry '{importer_id}' missing 'backend'"
            backend_cls = self.loader.load_class(manifest, backend_ref)
            if backend_cls is None:
                return f"Failed to load prompt importer backend: {backend_ref}"

            try:
                backend = backend_cls()
            except Exception as e:
                return f"Failed to instantiate prompt importer backend '{backend_ref}': {e}"

            component = entry.get('component')
            frontend_component = f"plugin:{plugin_id}:{component}" if component else ""

            try:
                self.prompt_importer_registry.register(PromptImporterDefinition(
                    importer_id=importer_id,
                    label=entry.get('label') or importer_id,
                    frontend_component=frontend_component,
                    backend=backend,
                    source=plugin_id,
                ))
            except DuplicatePromptImporterError as e:
                return str(e)

        return None

    def _rollback_partial_enable(self, plugin_id: str) -> None:
        """Tear down everything a partially-enabled plugin registered so far:
        hooks, field types, and LLM chat extensions (tools/modes/resources)."""
        for hook_name in self._plugin_hooks.get(plugin_id, set()):
            self.hook_chain.unregister(hook_name, plugin_id)
        if plugin_id in self._plugin_hooks:
            self._plugin_hooks[plugin_id].clear()
        if self.field_registry is not None:
            self.field_registry.unregister_source(plugin_id)
        if self.model_attributes_manager is not None:
            self.model_attributes_manager.remove_source(plugin_id)
        self._unregister_chat_extensions(plugin_id)
        if self.automation_node_registry is not None:
            self.automation_node_registry.unregister_source(plugin_id)
        if self.automation_template_registry is not None:
            self.automation_template_registry.unregister_source(plugin_template_source(plugin_id))
        if self.prompt_importer_registry is not None:
            self.prompt_importer_registry.unregister_source(plugin_id)

    def _require_registry(
        self, items, registry, attr_name: str, singular: str
    ) -> Tuple[bool, Optional[str]]:
        """The two-line guard every `_register_plugin_*` step starts with.

        Returns `(True, None)` when `items` is empty (nothing to register -
        the caller should return `None`), `(True, error)` when `items` is
        present but `registry` (the extension point) wasn't wired up for
        this process, or `(False, None)` when the caller should proceed to
        register `items` into `registry`.
        """
        if not items:
            return True, None
        if registry is None:
            return True, f"Plugin declares {attr_name} but no {singular} registry is available"
        return False, None

    def _unregister_chat_extensions(self, plugin_id: str) -> None:
        """Remove the plugin's tools, chat modes, and resource providers."""
        if self.tool_registry is not None:
            self.tool_registry.unregister_source(plugin_id)
        if self.chat_mode_registry is not None:
            self.chat_mode_registry.unregister_source(plugin_id)
        if self.resource_registry is not None:
            self.resource_registry.unregister_source(plugin_id)

    def _register_plugin_automation_nodes(self, manifest: PluginManifest) -> Optional[str]:
        """
        Load and register a plugin's `automation_nodes:` manifest entries onto
        `self.automation_node_registry`.

        Returns an error message string on failure, or None on success.
        """
        skip, error = self._require_registry(
            manifest.automation_nodes, self.automation_node_registry, "automation_nodes", "automation node"
        )
        if skip:
            return error

        from src.platform.plugins.automation_nodes import DuplicateNodeTypeError, NodeField, NodeTypeSpec

        plugin_id = manifest.id

        for spec in manifest.automation_nodes:
            key = spec.get('key')
            if not key:
                return "automation_nodes entry missing 'key'"

            handler = None
            handler_ref = spec.get('handler')
            if handler_ref:
                handler = self.loader.load_hook_handler(manifest, handler_ref)
                if handler is None:
                    return f"Failed to load automation node handler: {handler_ref}"

            start_handler = None
            start_ref = spec.get('start_handler')
            if start_ref:
                start_handler = self.loader.load_hook_handler(manifest, start_ref)
                if start_handler is None:
                    return f"Failed to load automation node start_handler: {start_ref}"

            stop_handler = None
            stop_ref = spec.get('stop_handler')
            if stop_ref:
                stop_handler = self.loader.load_hook_handler(manifest, stop_ref)
                if stop_handler is None:
                    return f"Failed to load automation node stop_handler: {stop_ref}"

            # Manifest declares outputs as plain dicts ({key, type?, label?, ...});
            # `NodeField` is the in-process shape the catalog serializer expects.
            try:
                outputs = tuple(NodeField(**field_def) for field_def in (spec.get('outputs') or []))
            except TypeError as exc:
                return f"Invalid automation node 'outputs' for '{key}': {exc}"

            try:
                self.automation_node_registry.register(NodeTypeSpec(
                    key=key,
                    kind=spec.get('kind'),
                    title=spec.get('title', key),
                    description=spec.get('description', ''),
                    icon=spec.get('icon', ''),
                    category=spec.get('category', 'general'),
                    config_schema=list(spec.get('config_schema') or []),
                    outputs=outputs,
                    dynamic_outputs=bool(spec.get('dynamic_outputs', False)),
                    execute=handler,
                    start=start_handler,
                    stop=stop_handler,
                    source=plugin_id,
                    dynamic_ports_config_key=spec.get('dynamic_ports_config_key'),
                ))
            except DuplicateNodeTypeError as e:
                return str(e)

        return None

    def _register_plugin_automation_templates(self, manifest: PluginManifest) -> Optional[str]:
        """Register template files declared by an enabled plugin manifest."""
        skip, error = self._require_registry(
            manifest.automation_templates, self.automation_template_registry,
            "automation_templates", "automation template"
        )
        if skip:
            return error

        for spec in manifest.automation_templates:
            try:
                self.automation_template_registry.register_from_file(
                    source=plugin_template_source(manifest.id),
                    source_name=manifest.name,
                    template_id=spec["id"],
                    title=spec["title"],
                    description=spec["description"],
                    category=spec["category"],
                    icon=spec["icon"],
                    tags=list(spec["tags"]),
                    path=manifest.plugin_dir / spec["path"],
                    root=manifest.plugin_dir,
                )
            except AutomationTemplateRegistrationError as exc:
                return str(exc)
            except Exception as exc:
                # Anything else (e.g. a NUL byte in `path` raising ValueError out
                # of Path.resolve()) must not escape as a bare exception: that
                # would skip `_rollback_partial_enable` in the caller's loop and
                # leave this plugin's earlier hooks/registrations live under an
                # ERROR state. Contain it here like the other best-effort
                # register steps and report it as a registration failure.
                return f"Failed to register automation template '{spec.get('id', '?')}': {exc}"
        return None

    def _register_plugin_tools(self, manifest: PluginManifest) -> Optional[str]:
        """
        Load and register a plugin's `tools:` manifest entries onto
        `self.tool_registry`.

        Returns an error message string on failure, or None on success.
        """
        skip, error = self._require_registry(manifest.tools, self.tool_registry, "tools", "tool")
        if skip:
            return error

        plugin_id = manifest.id

        for spec in manifest.tools:
            class_ref = spec.get('class')
            if not class_ref:
                return "tools entry missing 'class'"

            tool_cls = self.loader.load_class(manifest, class_ref)
            if tool_cls is None:
                return f"Failed to load tool class: {class_ref}"

            try:
                tool = tool_cls()
            except Exception as e:
                return f"Failed to instantiate tool class {class_ref}: {e}"

            # Per-instance mode scoping override (BaseTool.modes is a plain
            # class attribute, so an instance attribute shadows it).
            if spec.get('modes') is not None:
                tool.modes = list(spec['modes'])

            existing = self.tool_registry.get(tool.name)
            if existing is not None:
                return f"Tool name collision: '{tool.name}' is already registered"

            self.tool_registry.register(tool, source=plugin_id)

        return None

    def _register_plugin_chat_modes(self, manifest: PluginManifest) -> Optional[str]:
        """
        Load and register a plugin's `chat_modes:` manifest entries onto
        `self.chat_mode_registry`.

        Returns an error message string on failure, or None on success.
        """
        skip, error = self._require_registry(
            manifest.chat_modes, self.chat_mode_registry, "chat_modes", "chat mode"
        )
        if skip:
            return error

        from src.platform.plugins.chat_modes import ChatMode, DuplicateChatModeError

        plugin_id = manifest.id

        for spec in manifest.chat_modes:
            system_prompt = spec.get('system_prompt')
            prompt_file = spec.get('system_prompt_file')
            if system_prompt is None and prompt_file:
                prompt_path = (manifest.plugin_dir / prompt_file).resolve()
                if not str(prompt_path).startswith(str(manifest.plugin_dir.resolve())):
                    return f"system_prompt_file escapes the plugin directory: {prompt_file}"
                try:
                    system_prompt = prompt_path.read_text(encoding='utf-8')
                except OSError as e:
                    return f"Failed to read system_prompt_file '{prompt_file}': {e}"
            if system_prompt is None:
                return f"chat_modes entry '{spec.get('id')}' has no system prompt"

            context_contributor = None
            contributor_ref = spec.get('context_contributor')
            if contributor_ref:
                context_contributor = self.loader.load_hook_handler(manifest, contributor_ref)
                if context_contributor is None:
                    return f"Failed to load context_contributor: {contributor_ref}"

            mode = ChatMode(
                id=spec['id'],
                name=spec.get('name', spec['id']),
                description=spec.get('description', ''),
                system_prompt=system_prompt,
                tool_names=list(spec.get('tools') or []),
                icon=spec.get('icon'),
                default_route_prefixes=list(spec.get('default_route_prefixes') or []),
                resource_namespaces=spec.get('resource_namespaces'),
                context_contributor=context_contributor,
                llm_options=dict(spec.get('llm_options') or {}),
                structured_reply=spec.get('structured_reply', True),
                source=plugin_id,
            )

            try:
                self.chat_mode_registry.register(mode)
            except DuplicateChatModeError as e:
                return str(e)

        return None

    def _register_plugin_resources(self, manifest: PluginManifest) -> Optional[str]:
        """
        Load and register a plugin's `resources:` manifest entries onto
        `self.resource_registry`.

        Returns an error message string on failure, or None on success.
        """
        skip, error = self._require_registry(
            manifest.resources, self.resource_registry, "resources", "resource"
        )
        if skip:
            return error

        from src.platform.resources.registry import DuplicateResourceNamespaceError

        plugin_id = manifest.id

        for spec in manifest.resources:
            provider_ref = spec.get('provider')
            if not provider_ref:
                return "resources entry missing 'provider'"

            provider_cls = self.loader.load_class(manifest, provider_ref)
            if provider_cls is None:
                return f"Failed to load resource provider class: {provider_ref}"

            try:
                provider = provider_cls()
            except Exception as e:
                return f"Failed to instantiate resource provider {provider_ref}: {e}"

            declared_namespace = spec.get('namespace')
            if declared_namespace and provider.namespace != declared_namespace:
                return (
                    f"Resource provider {provider_ref} owns namespace "
                    f"'{provider.namespace}' but the manifest declares '{declared_namespace}'"
                )

            if spec.get('modes') is not None:
                provider.modes = list(spec['modes'])

            try:
                self.resource_registry.register(provider, source=plugin_id)
            except DuplicateResourceNamespaceError as e:
                return str(e)

        return None

    def disable_plugin(self, plugin_id: str) -> bool:
        """
        Disable a plugin and unregister its hooks.

        Args:
            plugin_id: ID of the plugin to disable

        Returns:
            True if successfully disabled, False otherwise
        """
        self._ensure_discovered()

        with self._lock:
            if plugin_id not in self._plugins:
                logger.error(f"Plugin not found: {plugin_id}")
                return False

            current_state = self._plugin_states.get(plugin_id)

            # Check if already disabled
            if current_state == PluginState.DISABLED:
                logger.debug(f"Plugin {plugin_id} is already disabled")
                return True

            try:
                # Unregister everything this plugin registered (hooks, field
                # types, model attributes, chat extensions, automation nodes,
                # prompt importers) - the same teardown a partial enable rolls
                # back, plus the two extras below that only apply on a full
                # disable.
                self._rollback_partial_enable(plugin_id)

                # Unmount the plugin's API router(s), if any were mounted
                if self.router_mounter is not None:
                    self.router_mounter.unmount(plugin_id)

                # Evict this plugin's cached modules only - other enabled
                # plugins keep their already-imported modules untouched.
                self.loader.evict_plugin(plugin_id)

                # Update state
                self._plugin_states[plugin_id] = PluginState.DISABLED

                logger.info(f"Disabled plugin {plugin_id}")
                return True

            except Exception as e:
                logger.error(f"Error disabling plugin {plugin_id}: {e}", exc_info=True)
                return False

    def run_boot_hook(self, plugin_id: str) -> None:
        """
        Fire one plugin's per-process boot hook.

        Dispatched to that plugin's handler only (see
        `HookChain.execute_for_plugin`): the payload's subject and the handler's
        owner are the same plugin. Never raises - a plugin whose boot handler
        blows up is logged and skipped, because the two callers are app startup
        and an admin enable, neither of which may fail over one plugin's
        initialization.
        """
        try:
            self.hook_chain.execute_for_plugin(
                PLUGIN_LIFECYCLE_HOOKS.boot,
                plugin_id,
                initial_data={"plugin_id": plugin_id},
            )
        except Exception as e:
            logger.error(
                f"Error running boot hook for plugin {plugin_id}: {e}", exc_info=True
            )

    def run_boot_hooks(self) -> None:
        """Fire the boot hook for every currently enabled plugin, in registry order."""
        for manifest in self.get_enabled_plugins():
            self.run_boot_hook(manifest.id)

    def execute_hook(
        self,
        hook_name: str,
        context: Optional[HookContext] = None,
        initial_data: Optional[Dict] = None
    ) -> tuple[HookContext, bool]:
        """
        Execute a hook with all registered handlers.

        Args:
            hook_name: Name of the hook to execute
            context: Optional pre-built context
            initial_data: Optional initial data for the context

        Returns:
            Tuple of (final_context, success)
        """
        self._ensure_discovered()

        try:
            final_context, results = self.hook_chain.execute(
                hook_name,
                context=context,
                initial_data=initial_data
            )

            # Check if any handlers failed
            any_failed = any(not result.success for result in results)

            if any_failed:
                failed_plugins = [
                    result.plugin_id for result in results if not result.success
                ]
                if hook_name not in _warned_hook_failures:
                    _warned_hook_failures.add(hook_name)
                    logger.warning(
                        f"Hook {hook_name} had failures from plugins: {', '.join(failed_plugins)}"
                    )

            return final_context, not any_failed

        except Exception as e:
            logger.error(f"Error executing hook {hook_name}: {e}", exc_info=True)
            # Return original or empty context on error
            if context is None:
                context = HookContext(
                    hook_name=hook_name,
                    plugin_id="system",
                    data=initial_data or {}
                )
            return context, False

    def get_plugins_for_hook(self, hook_name: str) -> List[str]:
        """
        Get all plugins that have handlers for a specific hook.

        Args:
            hook_name: Name of the hook

        Returns:
            List of plugin IDs
        """
        self._ensure_discovered()
        return [
            plugin_id for plugin_id, hooks in self._plugin_hooks.items()
            if hook_name in hooks
        ]

    def reload_plugin(self, plugin_id: str) -> bool:
        """
        Reload a plugin (disable then re-enable).

        Args:
            plugin_id: ID of the plugin to reload

        Returns:
            True if successfully reloaded, False otherwise
        """
        self._ensure_discovered()

        # Disable first (this also evicts this plugin's cached modules -
        # see PluginLoader.evict_plugin - so the re-enable below re-imports
        # fresh code without disturbing other enabled plugins' modules)
        if not self.disable_plugin(plugin_id):
            return False

        # Re-enable
        return self.enable_plugin(plugin_id)

