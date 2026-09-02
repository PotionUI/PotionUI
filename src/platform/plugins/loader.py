"""
Plugin loader for discovering and loading plugins from the filesystem.

This module handles the discovery and loading of plugins from both marketplace
and local directories. It parses plugin manifests and loads Python modules.
"""

import re
import sys
import shutil
import importlib.util
from pathlib import Path
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
import yaml
import logging

from pydantic import ValidationError

from src.platform.plugins.manifest import PluginManifestSchema

logger = logging.getLogger(__name__)


@dataclass
class PluginManifest:
    """Parsed plugin manifest with all metadata"""

    # Basic metadata
    id: str
    name: str
    version: str
    description: str
    author: str

    # Plugin type and capabilities
    plugin_type: str  # "frontend-only", "backend-only", or "full-stack"
    capabilities: List[str] = field(default_factory=list)

    # Dependencies (canonical: {python: [...], binaries: [...]})
    dependencies_python: List[str] = field(default_factory=list)
    dependencies_binaries: List[str] = field(default_factory=list)

    # Hooks
    hooks: Dict[str, str] = field(default_factory=dict)  # {hook_name: handler_path} - backend hooks
    frontend_hooks: List[Dict[str, Any]] = field(default_factory=list)  # Frontend hook definitions
    # "hook:handler" strings for backend hooks declared `remote: true` - the
    # subset of `hooks` whose code must also be present on a Remote Native
    # worker. See `BackendHookSpec.remote` (src/platform/plugins/manifest.py).
    remote_hooks: List[str] = field(default_factory=list)

    # Backend components
    pipes: List[Dict[str, Any]] = field(default_factory=list)  # List of pipe configs: {path, register_as}
    presets: List[Dict[str, Any]] = field(default_factory=list)  # Preset roots: [{path}]
    # Modes contributed to OTHER presets: [{target, modes_root}]
    preset_modes: List[Dict[str, Any]] = field(default_factory=list)
    recipes: List[Dict[str, Any]] = field(default_factory=list)  # Setup-recipe roots: [{path}]

    # Frontend components
    frontend_entry: Optional[str] = None  # Path to frontend entry point

    # File paths
    manifest_path: Path = field(default_factory=Path)
    plugin_dir: Path = field(default_factory=Path)

    # Source information
    source: str = "local"  # "marketplace" or "local"

    # Additional metadata
    homepage: Optional[str] = None
    repository: Optional[str] = None
    license: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    category: str = "other"

    # Pages, API routes, and sidebar items
    pages: List[Dict[str, Any]] = field(default_factory=list)
    api_routes: List[Dict[str, Any]] = field(default_factory=list)
    sidebar_items: List[Dict[str, Any]] = field(default_factory=list)

    # Quick actions and sidebar widgets
    quick_actions: List[Dict[str, Any]] = field(default_factory=list)
    sidebar_widgets: List[Dict[str, Any]] = field(default_factory=list)

    # Settings
    settings: List[Dict[str, Any]] = field(default_factory=list)

    # Inert sections consumed by later phases (A3 field registry / A5 renderers)
    field_types: List[Dict[str, Any]] = field(default_factory=list)
    model_metadata_fields: List[Dict[str, Any]] = field(default_factory=list)
    renderers: List[Dict[str, Any]] = field(default_factory=list)
    contributions: List[Dict[str, Any]] = field(default_factory=list)
    # Each entry is normalized to {"name", "description", "payload", "mutable",
    # "use_when", "example"} regardless of whether the manifest used the plain
    # string form or the structured object form.
    provides_hooks: List[Dict[str, Any]] = field(default_factory=list)

    # Documentation pages contributed to the in-app Documentation feature
    docs: List[Dict[str, Any]] = field(default_factory=list)

    # LLM chat extensions. `tools` entries keep the manifest's `class` key
    # (dumped by_alias); `chat_modes` entries mirror ChatModeSpec fields.
    chat_modes: List[Dict[str, Any]] = field(default_factory=list)
    tools: List[Dict[str, Any]] = field(default_factory=list)
    resources: List[Dict[str, Any]] = field(default_factory=list)

    # Automation module node types (A3)
    automation_nodes: List[Dict[str, Any]] = field(default_factory=list)
    automation_templates: List[Dict[str, Any]] = field(default_factory=list)

    # Prompt library import sources: [{id, label, component, backend}]
    prompt_importers: List[Dict[str, Any]] = field(default_factory=list)

    # Phrasebook batch tools: [{id, label, backend, component}]
    phrasebook_ops: List[Dict[str, Any]] = field(default_factory=list)

    # Set when the manifest failed schema validation. The plugin is still
    # discovered (so it's visible/manageable in the admin UI) but the
    # registry puts it straight into PluginState.ERROR with this message.
    validation_error: Optional[str] = None


class PluginLoader:
    """
    Loads plugins from filesystem directories.

    Scans content/plugins/marketplace/ and content/plugins/local/ directories for plugin
    manifests and loads their components.
    """

    def __init__(
        self,
        marketplace_dir: str = "content/plugins/marketplace",
        local_dir: str = "content/plugins/local"
    ):
        self.marketplace_dir = Path(marketplace_dir)
        self.local_dir = Path(local_dir)
        self._loaded_modules: Dict[str, Any] = {}

    def discover_plugins(self) -> List[PluginManifest]:
        """
        Discover all plugins from configured directories.

        Returns:
            List of discovered plugin manifests
        """
        plugins = []

        # Scan marketplace directory
        if self.marketplace_dir.exists():
            logger.info(f"Scanning marketplace directory: {self.marketplace_dir}")
            marketplace_plugins = self._scan_directory(
                self.marketplace_dir,
                source="marketplace"
            )
            plugins.extend(marketplace_plugins)
            logger.info(f"Found {len(marketplace_plugins)} marketplace plugins")

        # Scan local directory
        if self.local_dir.exists():
            logger.info(f"Scanning local directory: {self.local_dir}")
            local_plugins = self._scan_directory(self.local_dir, source="local")
            plugins.extend(local_plugins)
            logger.info(f"Found {len(local_plugins)} local plugins")

        logger.info(f"Total plugins discovered: {len(plugins)}")
        return plugins

    def _scan_directory(self, directory: Path, source: str) -> List[PluginManifest]:
        """
        Scan a directory for plugin manifests.

        Args:
            directory: Directory to scan
            source: Source type ("marketplace" or "local")

        Returns:
            List of plugin manifests found
        """
        plugins = []

        if not directory.exists():
            return plugins

        # Look for manifest.yml files in subdirectories
        for plugin_dir in directory.iterdir():
            if not plugin_dir.is_dir():
                continue

            manifest_path = plugin_dir / "manifest.yml"

            if manifest_path.exists():
                try:
                    manifest = self._load_manifest(manifest_path, plugin_dir, source)
                    if manifest:
                        plugins.append(manifest)
                        if manifest.validation_error:
                            logger.error(
                                f"Plugin manifest invalid: {manifest.id} ({manifest_path}): "
                                f"{manifest.validation_error}"
                            )
                        else:
                            logger.debug(f"Loaded plugin manifest: {manifest.name} ({manifest.id})")
                except Exception as e:
                    logger.error(f"Error loading manifest {manifest_path}: {e}", exc_info=True)

        return plugins

    def _load_manifest(
        self,
        manifest_path: Path,
        plugin_dir: Path,
        source: str
    ) -> Optional[PluginManifest]:
        """
        Load and validate a plugin manifest file against `PluginManifestSchema`.

        On success, returns a fully populated `PluginManifest`. On a schema
        validation error (or unparsable YAML), the plugin is still returned
        as a `PluginManifest` with `validation_error` set so the registry can
        put it in `PluginState.ERROR` - the plugin stays visible/discoverable
        instead of silently disappearing.

        Returns None only when there isn't even enough information to
        identify the plugin (e.g. an empty manifest file).

        Args:
            manifest_path: Path to manifest.yml
            plugin_dir: Plugin directory
            source: Source type ("marketplace" or "local")

        Returns:
            Parsed (or error-tagged) PluginManifest, or None if unusable
        """
        try:
            with open(manifest_path, 'r') as f:
                raw = yaml.safe_load(f)
        except yaml.YAMLError as e:
            logger.error(f"YAML parsing error in {manifest_path}: {e}")
            return self._build_error_manifest(
                plugin_dir, manifest_path, source, f"YAML parsing error: {e}"
            )

        if not raw:
            logger.warning(f"Empty manifest file: {manifest_path}")
            return None

        if not isinstance(raw, dict):
            logger.error(f"Manifest {manifest_path} must be a mapping, got {type(raw).__name__}")
            return self._build_error_manifest(
                plugin_dir, manifest_path, source,
                f"Manifest must be a mapping, got {type(raw).__name__}"
            )

        try:
            schema = PluginManifestSchema.model_validate(raw)
        except ValidationError as e:
            message = self._format_validation_error(e)
            logger.error(f"Manifest validation failed for {manifest_path}: {message}")
            return self._build_error_manifest(
                plugin_dir, manifest_path, source, message, fallback_id=raw.get('id')
            )
        except Exception as e:
            logger.error(f"Error loading manifest {manifest_path}: {e}", exc_info=True)
            return self._build_error_manifest(
                plugin_dir, manifest_path, source, str(e), fallback_id=raw.get('id')
            )

        return self._manifest_from_schema(schema, manifest_path, plugin_dir, source)

    @staticmethod
    def _format_validation_error(error: ValidationError) -> str:
        """Format a pydantic ValidationError into a single readable line."""
        parts = []
        for err in error.errors():
            location = ".".join(str(loc) for loc in err["loc"]) or "<root>"
            parts.append(f"{location}: {err['msg']}")
        return "; ".join(parts)

    def _build_error_manifest(
        self,
        plugin_dir: Path,
        manifest_path: Path,
        source: str,
        message: str,
        fallback_id: Optional[str] = None
    ) -> PluginManifest:
        """Build a placeholder PluginManifest for a manifest that failed validation."""
        plugin_id = fallback_id or plugin_dir.name
        return PluginManifest(
            id=plugin_id,
            name=plugin_id,
            version="0.0.0",
            description="",
            author="",
            # Not a real manifest type, but must satisfy the `plugins.type`
            # CHECK constraint - the registry surfaces this plugin as
            # PluginState.ERROR via `validation_error`, which is what the
            # admin UI actually keys off, not this placeholder value.
            plugin_type="backend-only",
            manifest_path=manifest_path,
            plugin_dir=plugin_dir,
            source=source,
            validation_error=message,
        )

    def _manifest_from_schema(
        self,
        schema: PluginManifestSchema,
        manifest_path: Path,
        plugin_dir: Path,
        source: str
    ) -> PluginManifest:
        """Convert a validated PluginManifestSchema into the downstream PluginManifest."""
        hooks = {h.hook: h.handler for h in schema.hooks.backend}
        remote_hooks = [f"{h.hook}:{h.handler}" for h in schema.hooks.backend if h.remote]
        frontend_hooks = [
            {
                'hook_name': h.hook,
                'component_path': h.component,
                'handler_path': h.handler,
                'position': h.position,
                'sort_order': h.order,
            }
            for h in schema.hooks.frontend
        ]

        return PluginManifest(
            id=schema.id,
            name=schema.name,
            version=schema.version,
            description=schema.description,
            author=schema.author,
            plugin_type=schema.type,
            capabilities=schema.capabilities,
            dependencies_python=schema.dependencies.python,
            dependencies_binaries=schema.dependencies.binaries,
            hooks=hooks,
            frontend_hooks=frontend_hooks,
            remote_hooks=remote_hooks,
            pipes=[p.model_dump() for p in schema.pipes],
            presets=[p.model_dump() for p in schema.presets],
            preset_modes=[p.model_dump() for p in schema.preset_modes],
            recipes=[r.model_dump() for r in schema.recipes],
            frontend_entry=schema.frontend,
            manifest_path=manifest_path,
            plugin_dir=plugin_dir,
            source=source,
            homepage=schema.homepage,
            repository=schema.repository,
            license=schema.license,
            tags=schema.tags,
            category=schema.category.value,
            pages=[p.model_dump() for p in schema.pages],
            api_routes={"module": schema.api.module} if schema.api else {},
            sidebar_items=[s.model_dump() for s in schema.sidebar],
            quick_actions=[q.model_dump() for q in schema.quick_actions],
            sidebar_widgets=[w.model_dump() for w in schema.sidebar_widgets],
            settings=[s.model_dump() for s in schema.settings],
            field_types=[f.model_dump() for f in schema.field_types],
            model_metadata_fields=[f.model_dump() for f in schema.model_metadata_fields],
            renderers=[r.model_dump() for r in schema.renderers],
            contributions=[c.model_dump() for c in schema.contributions],
            provides_hooks=[
                {"name": h, "description": "", "payload": {}, "mutable": [], "use_when": [], "example": ""}
                if isinstance(h, str)
                else h.model_dump()
                for h in schema.provides_hooks
            ],
            docs=[d.model_dump() for d in schema.docs],
            chat_modes=[m.model_dump() for m in schema.chat_modes],
            tools=[t.model_dump(by_alias=True) for t in schema.tools],
            resources=[r.model_dump() for r in schema.resources],
            automation_nodes=[n.model_dump() for n in schema.automation_nodes],
            automation_templates=[t.model_dump() for t in schema.automation_templates],
            prompt_importers=[p.model_dump() for p in schema.prompt_importers],
            phrasebook_ops=[o.model_dump() for o in schema.phrasebook_ops],
        )

    def load_plugin_module(
        self,
        manifest: PluginManifest,
        module_path: str
    ) -> Optional[Any]:
        """
        Load a Python module from a plugin.

        Args:
            manifest: Plugin manifest
            module_path: Relative path to module (e.g., "hooks.generation")

        Returns:
            Loaded module or None if loading fails
        """
        # Check if already loaded
        cache_key = f"{manifest.id}.{module_path}"
        if cache_key in self._loaded_modules:
            return self._loaded_modules[cache_key]

        try:
            # Construct full path
            module_file = manifest.plugin_dir / f"{module_path.replace('.', '/')}.py"

            if not module_file.exists():
                logger.error(f"Module file not found: {module_file}")
                return None

            # Add the plugin directory to sys.path temporarily to allow imports
            plugin_dir_str = str(manifest.plugin_dir)
            if plugin_dir_str not in sys.path:
                sys.path.insert(0, plugin_dir_str)

            # Create unique module name that supports relative imports
            # Use the plugin directory name as the package root
            package_name = manifest.id.replace('-', '_')
            module_name = f"{package_name}.{module_path}"

            # Ensure parent packages exist in sys.modules
            self._ensure_parent_packages(package_name, module_path, manifest.plugin_dir)

            # Load the module
            spec = importlib.util.spec_from_file_location(
                module_name,
                module_file,
                submodule_search_locations=[str(manifest.plugin_dir)]
            )
            if spec is None or spec.loader is None:
                logger.error(f"Failed to create module spec for {module_file}")
                return None

            module = importlib.util.module_from_spec(spec)

            # Set __package__ to enable relative imports
            parts = module_path.rsplit('.', 1)
            if len(parts) > 1:
                module.__package__ = f"{package_name}.{parts[0]}"
            else:
                module.__package__ = package_name

            sys.modules[module_name] = module
            spec.loader.exec_module(module)

            # Cache the loaded module
            self._loaded_modules[cache_key] = module

            logger.debug(f"Loaded module {module_path} from plugin {manifest.name}")
            return module

        except Exception as e:
            logger.error(
                f"Error loading module {module_path} from plugin {manifest.name}: {e}",
                exc_info=True
            )
            return None

    def _ensure_parent_packages(
        self,
        package_name: str,
        module_path: str,
        plugin_dir: Path
    ) -> None:
        """
        Ensure parent packages exist in sys.modules for relative imports.

        Args:
            package_name: Root package name (e.g., "civitai_provider")
            module_path: Module path (e.g., "hooks.provider_hooks")
            plugin_dir: Plugin directory path
        """
        # Ensure root package exists
        if package_name not in sys.modules:
            root_init = plugin_dir / "__init__.py"
            if root_init.exists():
                spec = importlib.util.spec_from_file_location(
                    package_name,
                    root_init,
                    submodule_search_locations=[str(plugin_dir)]
                )
                if spec and spec.loader:
                    module = importlib.util.module_from_spec(spec)
                    module.__path__ = [str(plugin_dir)]
                    sys.modules[package_name] = module
                    spec.loader.exec_module(module)
            else:
                # Create a namespace package
                import types
                module = types.ModuleType(package_name)
                module.__path__ = [str(plugin_dir)]
                module.__package__ = package_name
                sys.modules[package_name] = module

        # Ensure intermediate packages exist
        parts = module_path.split('.')
        for i in range(len(parts) - 1):
            subpackage_parts = parts[:i + 1]
            subpackage_name = f"{package_name}.{'.'.join(subpackage_parts)}"
            subpackage_path = plugin_dir / '/'.join(subpackage_parts)

            if subpackage_name not in sys.modules:
                init_file = subpackage_path / "__init__.py"
                if init_file.exists():
                    spec = importlib.util.spec_from_file_location(
                        subpackage_name,
                        init_file,
                        submodule_search_locations=[str(subpackage_path)]
                    )
                    if spec and spec.loader:
                        module = importlib.util.module_from_spec(spec)
                        module.__path__ = [str(subpackage_path)]
                        sys.modules[subpackage_name] = module
                        spec.loader.exec_module(module)
                else:
                    # Create a namespace package
                    import types
                    module = types.ModuleType(subpackage_name)
                    module.__path__ = [str(subpackage_path)]
                    module.__package__ = subpackage_name
                    sys.modules[subpackage_name] = module

    def load_hook_handler(
        self,
        manifest: PluginManifest,
        handler_path: str
    ) -> Optional[callable]:
        """
        Load a hook handler function from a plugin.

        Args:
            manifest: Plugin manifest
            handler_path: Path to handler in format "module.function"

        Returns:
            Callable handler function or None if loading fails
        """
        try:
            # Split into module path and function name
            parts = handler_path.rsplit('.', 1)
            if len(parts) != 2:
                logger.error(f"Invalid handler path format: {handler_path}")
                return None

            module_path, function_name = parts

            # Load the module
            module = self.load_plugin_module(manifest, module_path)
            if module is None:
                return None

            # Get the handler function
            if not hasattr(module, function_name):
                logger.error(
                    f"Handler function '{function_name}' not found in module {module_path}"
                )
                return None

            handler = getattr(module, function_name)

            if not callable(handler):
                logger.error(f"Handler '{handler_path}' is not callable")
                return None

            logger.debug(f"Loaded handler {handler_path} from plugin {manifest.name}")
            return handler

        except Exception as e:
            logger.error(
                f"Error loading handler {handler_path} from plugin {manifest.name}: {e}",
                exc_info=True
            )
            return None

    def load_class(
        self,
        manifest: PluginManifest,
        class_path: str
    ) -> Optional[type]:
        """
        Load a class from a plugin, given a `"module.path:ClassName"` reference.

        Used for manifest `field_types[].schema_class` entries - the same
        module-loading machinery as `load_hook_handler`, but resolving a class
        instead of a function.

        Args:
            manifest: Plugin manifest
            class_path: Reference in the form "module.path:ClassName"

        Returns:
            The class object, or None if loading fails
        """
        parts = class_path.rsplit(':', 1)
        if len(parts) != 2:
            logger.error(f"Invalid class path format (expected 'module.path:ClassName'): {class_path}")
            return None

        module_path, class_name = parts

        module = self.load_plugin_module(manifest, module_path)
        if module is None:
            return None

        if not hasattr(module, class_name):
            logger.error(f"Class '{class_name}' not found in module {module_path}")
            return None

        klass = getattr(module, class_name)
        if not isinstance(klass, type):
            logger.error(f"'{class_path}' is not a class")
            return None

        return klass

    _PIP_SPEC_SPLIT_RE = re.compile(r'[<>=!~\[]')

    def validate_dependencies(self, manifest: PluginManifest) -> tuple[bool, List[str]]:
        """
        Validate that all plugin dependencies are available.

        `dependencies.python` entries are pip-style requirement strings
        (e.g. "numpy>=1.24.0") checked via `importlib.import_module`.
        `dependencies.binaries` entries are system executable names checked
        explicitly via `shutil.which` - there is no fallback between the two.

        Args:
            manifest: Plugin manifest

        Returns:
            Tuple of (all_satisfied, list_of_missing)
        """
        missing = []

        for requirement in manifest.dependencies_python:
            package_name = self._PIP_SPEC_SPLIT_RE.split(requirement, maxsplit=1)[0].strip()
            module_name = package_name.replace('-', '_')
            try:
                importlib.import_module(module_name)
            except ImportError:
                missing.append(requirement)

        for binary in manifest.dependencies_binaries:
            if shutil.which(binary) is None:
                missing.append(binary)

        return len(missing) == 0, missing

    def evict_plugin(self, plugin_id: str) -> int:
        """
        Evict only `plugin_id`'s cached modules (cache keys are
        `f"{manifest.id}.{module_path}"`), leaving every other plugin's
        cached modules untouched. Used on disable/reload so a plugin's
        stale module objects don't linger once its routes/hooks are torn
        down, without forcing every other enabled plugin to re-import.

        Returns the number of cache entries evicted.
        """
        prefix = f"{plugin_id}."
        stale_keys = [k for k in self._loaded_modules if k == plugin_id or k.startswith(prefix)]
        for key in stale_keys:
            del self._loaded_modules[key]
        if stale_keys:
            logger.debug(f"Evicted {len(stale_keys)} cached module(s) for plugin {plugin_id}")
        return len(stale_keys)
