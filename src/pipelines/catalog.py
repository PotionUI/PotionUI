"""Discovery and lookup of the pipes available to this installation.

A pipe is a directory with a `main.py`, so the catalog finds pipes by walking
directories rather than by importing packages: `pipes/<name>/main.py` is a pipe,
`pipes/<name>/<variant>/main.py` is a variant of it (registered as
`<name>/<variant>`). Pipes come from three sources - the ones shipped with the
app, the ones a user installed into the runtime `pipes/custom` directory, and
the ones enabled plugins contribute - and the catalog records which, so callers
can tell a first-party pipe from a plugin's.

Whether a pipe's requirements are satisfied is `installer.py`'s question; the
catalog only stores the answer as each pipe's `PipeStatus`.

Two discovery tiers live side by side:

- A *light scan* (`_ensure_light_discovered` / `_do_light_scan`) walks the
  filesystem layout (and plugin manifests) to learn each pipe's registry key
  and file location WITHOUT exec'ing any module. `get_pipe`/`get_pipe_status`/
  `get_pipe_source` use this tier, then import only the one module a caller
  actually asked for - the mechanism that keeps e.g. a txt2img generation from
  pulling in cv2/diffusers/transformers for pipes it never touches.
- The original *eager* discovery (`discover_pipes` / `_do_discover_pipes`)
  still imports every pipe up front. It backs `get_available_pipes()`, whose
  one real caller (the offline preset test-suite runner) deliberately wants
  every pipe's import to be attempted at boot so a broken import is logged
  there instead of surfacing mid-generation as a confusing "not found".
"""

import importlib.util
import logging
import os
import sys
import threading
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Type

from src.pipelines.contracts import BasePipe, PipeStatus
from src.pipelines.installer import requirements_satisfied

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _PipeLocation:
    """Where a not-yet-imported pipe lives, learned without exec'ing it."""
    file_path: str
    module_name: str
    source: str


class PipeCatalog:
    def __init__(self, core_pipes_path: str, custom_pipes_path: str, plugin_registry=None):
        self.core_pipes_path = core_pipes_path
        self.custom_pipes_path = custom_pipes_path
        self.plugin_registry = plugin_registry
        self.pipes: Dict[str, Type[BasePipe]] = {}
        self.pipe_status: Dict[str, PipeStatus] = {}
        self.pipe_sources: Dict[str, str] = {}  # Track pipe source: "core", "custom", or plugin ID
        self._discovered = False
        self._lock = threading.Lock()

        # Light-scan tier: registry key -> location, populated without imports.
        self._locations: Dict[str, _PipeLocation] = {}
        self._light_discovered = False
        self._light_lock = threading.Lock()

    def _ensure_discovered(self):
        """Ensure pipes are discovered (lazy loading with thread safety)"""
        if not self._discovered:
            with self._lock:
                # Double-check pattern for thread safety
                if not self._discovered:
                    self._do_discover_pipes()
                    self._discovered = True

    def _ensure_light_discovered(self):
        """Ensure pipe locations are known, without importing any pipe module."""
        if self._discovered:
            return  # full eager discovery already loaded everything
        if not self._light_discovered:
            with self._light_lock:
                if not self._light_discovered and not self._discovered:
                    self._do_light_scan()
                    self._light_discovered = True

    def get_available_pipes(self) -> List[Type[BasePipe]]:
        """Get all available pipes (imports every pipe module - see module docstring)"""
        self._ensure_discovered()
        return list(self.pipes.values())

    def get_pipe(self, name: str) -> Optional[Type[BasePipe]]:
        """Get pipe class by name, importing just that pipe's module on first use."""
        self._ensure_light_discovered()

        # .get() (not `if name in self.pipes: return self.pipes[name]`): a
        # concurrent rescan_plugin_pipes() can remove a disabled
        # plugin's entry between those two reads under the old idiom - a
        # single .get() call can't be split by another thread's mutation.
        cached = self.pipes.get(name)
        if cached is not None:
            return cached

        location = self._locations.get(name)
        if location is None:
            return None

        with self._light_lock:
            if name not in self.pipes:
                pipe_class = self._load_pipe_module(location.file_path, location.module_name)
                if pipe_class:
                    self._register(name, pipe_class, location.source)

        return self.pipes.get(name)

    def get_pipe_status(self, name: str) -> PipeStatus:
        """Get current status of a pipe (imports it if not already loaded)"""
        self._ensure_light_discovered()
        if name not in self.pipe_status:
            self.get_pipe(name)
        return self.pipe_status.get(name, PipeStatus.NOT_INSTALLED)

    def get_pipe_source(self, name: str) -> Optional[str]:
        """
        Get the source of a pipe. Known from the light scan - never imports.

        Args:
            name: Pipe name

        Returns:
            Source identifier: "core", "custom", or plugin ID, or None if pipe not found
        """
        self._ensure_light_discovered()
        if name in self.pipe_sources:
            return self.pipe_sources[name]
        location = self._locations.get(name)
        return location.source if location else None

    def _do_discover_pipes(self):
        """Internal method to actually discover pipes"""
        self.pipes.clear()
        self.pipe_status.clear()
        self.pipe_sources.clear()

        # Discover core and custom pipes
        self._discover_filesystem_pipes(self.core_pipes_path, "core")
        self._discover_filesystem_pipes(self.custom_pipes_path, "custom")

        # Discover plugin pipes if plugin registry is available
        if self.plugin_registry is not None:
            self._discover_plugin_pipes()

        logger.info(f"Discovered {len(self.pipes)} pipes (including variants)")

    def _register(self, name: str, pipe_class: Type[BasePipe], source: str):
        """Record a discovered pipe under `name`, with its source and status."""
        self.pipes[name] = pipe_class
        self.pipe_status[name] = (
            PipeStatus.INSTALLED if requirements_satisfied(pipe_class)
            else PipeStatus.NOT_INSTALLED
        )
        self.pipe_sources[name] = source

    def _do_light_scan(self):
        """Learn every pipe's registry key + file location without importing it.

        Relies on the filesystem convention the module docstring describes: a
        main pipe's registry key is its directory name, a variant's is
        `<dir>/<variant>`. That convention is what `_load_pipe_module` would
        otherwise have to import each module to rediscover via `pipe_class.name`.
        """
        self._locations.clear()

        self._light_scan_filesystem(self.core_pipes_path, "core")
        self._light_scan_filesystem(self.custom_pipes_path, "custom")

        if self.plugin_registry is not None:
            self._light_scan_plugin_pipes()

        logger.info(f"Light-scanned {len(self._locations)} pipe locations (no imports)")

    def _light_scan_filesystem(self, base_path: str, source: str):
        """Same directory walk as `_discover_filesystem_pipes`, but recording
        locations instead of importing modules."""
        if not os.path.exists(base_path):
            return

        for pipe_dir in os.listdir(base_path):
            pipe_path = os.path.join(base_path, pipe_dir)
            if not os.path.isdir(pipe_path):
                continue

            main_file = os.path.join(pipe_path, "main.py")
            if os.path.exists(main_file):
                module_name = f"pipes.{pipe_dir}"
                self._locations[pipe_dir] = _PipeLocation(main_file, module_name, source)
                self.pipe_sources.setdefault(pipe_dir, source)

            for variant_dir in os.listdir(pipe_path):
                variant_path = os.path.join(pipe_path, variant_dir)
                if not os.path.isdir(variant_path):
                    continue

                variant_main = os.path.join(variant_path, "main.py")
                if os.path.exists(variant_main):
                    variant_name = f"{pipe_dir}/{variant_dir}"
                    module_name = f"pipes.{pipe_dir}.{variant_dir}"
                    self._locations[variant_name] = _PipeLocation(variant_main, module_name, source)
                    self.pipe_sources.setdefault(variant_name, source)

    def _light_scan_plugin_pipes(self):
        """Plugin counterpart of `_light_scan_filesystem`.

        Manifest-declared pipes with a `register_as` alias have a key that's
        known from the manifest alone, so those stay lazy. A manifest pipe
        without `register_as`, or an auto-discovered plugin pipe, keys itself
        off `pipe_class.name` - only knowable after import - so those fall
        back to importing eagerly, same as before, scoped to that one plugin.
        """
        try:
            enabled_plugins = self.plugin_registry.get_enabled_plugins()

            for plugin_manifest in enabled_plugins:
                manifest_pipes = getattr(plugin_manifest, 'pipes', None)

                if manifest_pipes:
                    self._light_scan_manifest_pipes(plugin_manifest, manifest_pipes)
                else:
                    self._auto_discover_plugin_pipes(plugin_manifest)

        except Exception as e:
            logger.error(f"Error light-scanning plugin pipes: {e}", exc_info=True)

    def _light_scan_manifest_pipes(self, plugin_manifest, manifest_pipes: list):
        """Record locations for manifest-declared pipes that carry a
        `register_as` alias; import immediately (via the existing eager path)
        for the ones that don't, since their key needs the loaded class."""
        plugin_id = plugin_manifest.id
        plugin_dir = plugin_manifest.plugin_dir

        for pipe_config in manifest_pipes:
            register_as = pipe_config.get('register_as')
            if not register_as:
                self._load_manifest_declared_pipes(plugin_manifest, [pipe_config])
                continue

            pipe_path_str = pipe_config.get('path', '')
            pipe_path = plugin_dir / pipe_path_str
            main_file = pipe_path / "main.py"

            if not main_file.exists():
                logger.warning(f"Plugin {plugin_id} declares pipe at {pipe_path_str} but main.py not found")
                continue

            module_name = f"plugin_{plugin_id}.pipes.{pipe_path.name}"
            self._locations[register_as] = _PipeLocation(str(main_file), module_name, plugin_id)
            self.pipe_sources.setdefault(register_as, plugin_id)

    def _discover_filesystem_pipes(self, base_path: str, source: str):
        """
        Discover pipes from a filesystem directory.

        Args:
            base_path: Path to scan for pipes
            source: Source identifier ("core" or "custom")
        """
        if not os.path.exists(base_path):
            return

        # First, discover main pipes (those with main.py directly in pipe folder)
        for pipe_dir in os.listdir(base_path):
            pipe_path = os.path.join(base_path, pipe_dir)
            if not os.path.isdir(pipe_path):
                continue

            main_file = os.path.join(pipe_path, "main.py")
            if os.path.exists(main_file):
                # This is a main pipe
                module_name = f"pipes.{pipe_dir}"
                pipe_class = self._load_pipe_module(main_file, module_name)
                if pipe_class:
                    self._register(pipe_class.name, pipe_class, source)

            # Now check for variants (subdirectories with main.py)
            for variant_dir in os.listdir(pipe_path):
                variant_path = os.path.join(pipe_path, variant_dir)
                if not os.path.isdir(variant_path):
                    continue

                variant_main = os.path.join(variant_path, "main.py")
                if os.path.exists(variant_main):
                    # This is a variant
                    variant_name = f"{pipe_dir}/{variant_dir}"
                    module_name = f"pipes.{pipe_dir}.{variant_dir}"
                    pipe_class = self._load_pipe_module(variant_main, module_name)
                    if pipe_class:
                        # Override the pipe name to include variant
                        self._register(variant_name, pipe_class, source)

    def _discover_plugin_pipes(self):
        """
        Discover pipes from enabled plugins.

        Supports two modes:
        1. Manifest-declared pipes: Plugin manifest has a 'pipes' section specifying
           which pipes to load and how to register them (with optional 'register_as' alias)
        2. Auto-discovery: Scans backend/pipes/ directory and registers with prefixed names

        Plugin pipes are tagged with their source plugin ID.
        """
        try:
            # Get all enabled plugins
            enabled_plugins = self.plugin_registry.get_enabled_plugins()

            for plugin_manifest in enabled_plugins:
                # Check if manifest declares pipes explicitly
                manifest_pipes = getattr(plugin_manifest, 'pipes', None)

                if manifest_pipes:
                    # Use manifest-declared pipes
                    self._load_manifest_declared_pipes(plugin_manifest, manifest_pipes)
                else:
                    # Fall back to auto-discovery
                    self._auto_discover_plugin_pipes(plugin_manifest)

        except Exception as e:
            logger.error(f"Error discovering plugin pipes: {e}", exc_info=True)

    def _load_manifest_declared_pipes(self, plugin_manifest, manifest_pipes: list):
        """
        Load pipes declared in the plugin manifest.

        Manifest pipes format:
        pipes:
          - path: "backend/pipes/comfyui"      # Path to pipe directory
            register_as: "comfyui"             # Optional: name to register as (no prefix)

        Args:
            plugin_manifest: The plugin manifest object
            manifest_pipes: List of pipe declarations from manifest
        """
        plugin_id = plugin_manifest.id
        plugin_dir = plugin_manifest.plugin_dir

        for pipe_config in manifest_pipes:
            pipe_path_str = pipe_config.get('path', '')
            register_as = pipe_config.get('register_as')  # Optional custom name

            pipe_path = plugin_dir / pipe_path_str
            main_file = pipe_path / "main.py"

            if not main_file.exists():
                logger.warning(f"Plugin {plugin_id} declares pipe at {pipe_path_str} but main.py not found")
                continue

            # Load the pipe module
            module_name = f"plugin_{plugin_id}.pipes.{pipe_path.name}"
            pipe_class = self._load_pipe_module(str(main_file), module_name)

            if pipe_class:
                # Use register_as if provided, otherwise use prefixed name
                if register_as:
                    pipe_name = register_as
                    logger.info(f"Plugin {plugin_id} registering pipe as '{register_as}'")
                else:
                    pipe_name = f"plugin:{plugin_id}:{pipe_class.name}"

                # Check for conflicts with existing pipes
                if pipe_name in self.pipes:
                    existing_source = self.pipe_sources.get(pipe_name, 'unknown')
                    logger.warning(
                        f"Plugin {plugin_id} pipe '{pipe_name}' conflicts with existing pipe from {existing_source}. "
                        f"Plugin pipe will override."
                    )

                self._register(pipe_name, pipe_class, plugin_id)

                logger.debug(f"Loaded pipe '{pipe_name}' from plugin {plugin_id}")

    def _auto_discover_plugin_pipes(self, plugin_manifest):
        """
        Auto-discover pipes from a plugin's backend/pipes/ directory.

        All auto-discovered pipes are prefixed with plugin:{plugin_id}: to avoid conflicts.

        Args:
            plugin_manifest: The plugin manifest object
        """
        plugin_id = plugin_manifest.id
        plugin_dir = plugin_manifest.plugin_dir
        pipes_dir = plugin_dir / "backend" / "pipes"

        if not pipes_dir.exists():
            return

        logger.debug(f"Auto-discovering pipes for plugin {plugin_id} at {pipes_dir}")

        # Discover pipes in the plugin directory
        for pipe_dir_name in os.listdir(pipes_dir):
            pipe_path = pipes_dir / pipe_dir_name

            if not pipe_path.is_dir():
                continue

            main_file = pipe_path / "main.py"
            if main_file.exists():
                # This is a main pipe from a plugin
                module_name = f"plugin_{plugin_id}.pipes.{pipe_dir_name}"
                pipe_class = self._load_pipe_module(str(main_file), module_name)

                if pipe_class:
                    # Prefix plugin pipes with plugin ID to avoid conflicts
                    prefixed_name = f"plugin:{plugin_id}:{pipe_class.name}"
                    self._register(prefixed_name, pipe_class, plugin_id)

                    logger.debug(f"Loaded pipe {prefixed_name} from plugin {plugin_id}")

            # Check for variants in plugin pipes
            for variant_dir_name in os.listdir(pipe_path):
                variant_path = pipe_path / variant_dir_name

                if not variant_path.is_dir():
                    continue

                variant_main = variant_path / "main.py"
                if variant_main.exists():
                    # This is a variant from a plugin
                    variant_name = f"{pipe_dir_name}/{variant_dir_name}"
                    module_name = f"plugin_{plugin_id}.pipes.{pipe_dir_name}.{variant_dir_name}"
                    pipe_class = self._load_pipe_module(str(variant_main), module_name)

                    if pipe_class:
                        # Prefix plugin pipe variants with plugin ID
                        prefixed_name = f"plugin:{plugin_id}:{variant_name}"
                        self._register(prefixed_name, pipe_class, plugin_id)

                        logger.debug(f"Loaded variant pipe {prefixed_name} from plugin {plugin_id}")

    def discover_pipes(self):
        """Auto-discover and load all available pipes"""
        self._do_discover_pipes()
        self._discovered = True

    def rescan_plugin_pipes(self) -> None:
        """Refresh the plugin-contributed portion of the catalog after a
        plugin is enabled or disabled.

        Core/custom filesystem pipes are untouched - their locations never
        change from a plugin toggle, so only the plugin-sourced entries in
        ``self._locations``/``self.pipes``/``self.pipe_status``/
        ``self.pipe_sources`` are touched:

        - Enable: re-runs the (idempotent) plugin scan so a newly-enabled
          plugin's pipes become resolvable - a ``register_as``-aliased
          manifest pipe gets a location recorded (lazy, imported on first
          ``get_pipe()``); an un-aliased manifest pipe or an auto-discovered
          one imports immediately, exactly as booting with the plugin already
          enabled would have done.
        - Disable: removes every entry whose recorded source is a plugin no
          longer in the enabled set, so a disabled plugin's pipe stops
          resolving instead of continuing to serve an already-imported class
          (the ``krea2-edit`` plugin ships one such pipe - the case that
          motivated this audit).

        Held under ``self._light_lock`` (the same lock ``get_pipe()``'s
        first-import path takes) so this serializes against a concurrent
        first-import/rescan rather than racing it; it does NOT block
        ``get_pipe()``'s already-cached fast path (a plain dict ``.get()``).
        A no-op before anything has been scanned yet - the next real request
        does a correct first scan on its own.
        """
        if self.plugin_registry is None:
            return
        if not self._light_discovered and not self._discovered:
            return

        with self._light_lock:
            enabled_ids = {m.id for m in self.plugin_registry.get_enabled_plugins()}

            def _is_stale_plugin_source(source: Optional[str]) -> bool:
                return source is not None and source not in ("core", "custom") and source not in enabled_ids

            stale_locations = [k for k, loc in self._locations.items() if _is_stale_plugin_source(loc.source)]
            for key in stale_locations:
                del self._locations[key]

            stale_registered = [name for name, source in self.pipe_sources.items() if _is_stale_plugin_source(source)]
            for name in stale_registered:
                self.pipes.pop(name, None)
                self.pipe_status.pop(name, None)
                self.pipe_sources.pop(name, None)

            self._light_scan_plugin_pipes()

        logger.info(f"Rescanned plugin pipes: {len(enabled_ids)} plugin(s) enabled, "
                    f"removed {len(stale_locations) + len(stale_registered)} stale entrie(s)")

    def _load_pipe_module(self, path: str, module_name: str) -> Optional[Type[BasePipe]]:
        """Load a pipe module from file"""
        try:
            spec = importlib.util.spec_from_file_location(module_name, path)
            if spec is None or spec.loader is None:
                return None

            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)

            # Find the pipe class in the module. Only consider classes DEFINED
            # in this module (attr.__module__ check) — main.py files import
            # shared base classes like BaseModelLoaderPipe, and dir() is
            # alphabetical, so without the check an imported "Base*" class
            # sorting before the concrete pipe gets registered instead of it.
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if (isinstance(attr, type) and
                        issubclass(attr, BasePipe) and
                        attr is not BasePipe and
                        attr.__module__ == module_name):
                    return attr

        except Exception as e:
            logger.error(f"Error loading pipe {module_name}: {e}", exc_info=True)
        return None

    def remote_relevant_plugin_ids(self) -> Set[str]:
        """Plugin IDs that contributed at least one registered pipe.

        Reads off ``pipe_sources`` rather than raw manifest declarations, so a
        plugin whose manifest declares ``pipes:`` (or ships an auto-discoverable
        ``backend/pipes/``) but none of which actually resolved (e.g. a missing
        ``main.py``) is correctly excluded - same ground truth ``get_pipe_source()``
        serves, not a re-derived guess. Triggers only the light-scan tier (no
        pipe module is imported to answer this).
        """
        self._ensure_light_discovered()
        return {
            source for source in self.pipe_sources.values()
            if source not in ("core", "custom")
        }
