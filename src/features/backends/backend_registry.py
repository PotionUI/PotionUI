from typing import Callable, Dict, List, Optional, Any, Type

from .base_backend import BaseBackend
from .in_process_backend import InProcessBackend
from .native_backend import NativeBackend
from .native_remote_backend import RemoteNativeBackend
from .pipeline_executor import PipelineExecutor
from .backend_config import (
    BackendConfigStore,
    NativeBackendConfig,
    NativeRemoteBackendConfig,
    BackendHealth,
    BaseBackendConfig,
    NATIVE_LOCAL_DRIVER,
    NATIVE_REMOTE_DRIVER,
    config_class_engine,
)
from src.platform.observability.logger import logger
from src.features.backends.hooks import BACKEND_HOOKS


class NoBackendForEngineError(RuntimeError):
    """Raised when no enabled backend provides the engine a preset requires."""


class BackendRegistry:
    """
    Registry of backends - configured instances of engines.

    An engine is the protocol a pipeline speaks (declared by the preset); a
    backend is a configured instance of one (owned by the admin). See
    docs/backends.md.
    """

    def __init__(
        self,
        generation_engine_factory: Callable[[], PipelineExecutor],
        plugin_registry=None,  # Optional for backward compatibility
        pipe_catalog=None,
    ):
        # A factory, not an instance: each backend executes on its own
        # PipelineExecutor so that backends run in parallel without sharing a
        # cancellation flag. The executors still share their injected
        # collaborators (GpuMonitor, ModelLifecycle, ...), which are
        # built to be shared; only the cancellation flag is per-run state.
        self.generation_engine_factory = generation_engine_factory
        self.plugin_registry = plugin_registry
        # The process's one PipeCatalog instance, handed to any backend that
        # asks for it via bind_remote_context (see _create_backend_instance) -
        # a native.remote backend needs it to compute the same
        # compute_pipe_catalog_fingerprint() the worker handshake is checked
        # against. Optional so existing tests that build a BackendRegistry
        # without one keep working; those tests never instantiate a driver
        # that asks for it.
        self.pipe_catalog = pipe_catalog

        # engine -> class. Populated by built-ins, then plugins.
        self._registered_backend_types: Dict[str, Type[BaseBackend]] = {}
        self._registered_config_types: Dict[str, Type[BaseBackendConfig]] = {}

        self._register_builtin_backends()
        self._load_plugin_backends()

        self.backend_config_store = BackendConfigStore(
            registered_config_types=self._registered_config_types
        )

        # Cache of instantiated backends
        self._backends_cache: Dict[str, BaseBackend] = {}
        self._backend_health_cache: Dict[str, BackendHealth] = {}

        # Deliberately NOT self._initialize_backends() here. Auto-provisioning
        # the native backend resolves its device/dtype/gpu_max_vram defaults
        # (detect_native_hardware_defaults), which probes CUDA and can cost
        # 0.5-2s on a cold GPU - a cost process boot must not pay. See
        # _ensure_backends_initialized: the first real read (an admin listing,
        # a generation looking up a backend, ...) pays it instead, once.
        self._backends_initialized = False

    def _register_builtin_backends(self):
        """Register the built-in native engine's in-process driver.

        Keyed by DRIVER, not engine: `native.local` is the always-present,
        auto-provisioned implementation. A plugin engine that only ever
        registers once (the common case, e.g. `comfyui`) keys its own
        `backend.register` payload by its engine name, which - by the
        engine-only-registration contract - IS that engine's one driver.
        """
        self._registered_backend_types[NATIVE_LOCAL_DRIVER] = NativeBackend
        self._registered_config_types[NATIVE_LOCAL_DRIVER] = NativeBackendConfig

        self._registered_backend_types[NATIVE_REMOTE_DRIVER] = RemoteNativeBackend
        self._registered_config_types[NATIVE_REMOTE_DRIVER] = NativeRemoteBackendConfig

        logger.info("[BACKEND_REGISTRY] Registered built-in drivers: native.local, native.remote")

    def _load_plugin_backends(self):
        """Execute backend.register hook to collect plugin-provided engines"""
        if not self.plugin_registry:
            logger.debug("[BACKEND_REGISTRY] No plugin registry available, skipping plugin engines")
            return

        try:
            context, success = self.plugin_registry.execute_hook(
                BACKEND_HOOKS.register,
                initial_data={'backend_types': {}, 'config_types': {}}
            )

            if success or context:
                plugin_backend_types = context.data.get('backend_types', {})
                plugin_config_types = context.data.get('config_types', {})

                self._registered_backend_types.update(plugin_backend_types)
                self._registered_config_types.update(plugin_config_types)

                if plugin_backend_types:
                    logger.info(
                        f"[BACKEND_REGISTRY] Loaded {len(plugin_backend_types)} plugin engines: "
                        f"{list(plugin_backend_types.keys())}"
                    )
        except Exception as e:
            logger.error(f"[BACKEND_REGISTRY] Failed to load plugin engines: {str(e)}")

    def get_supported_engines(self) -> List[str]:
        """Return the names of all registered engines (built-in + plugins),
        deduplicated from the driver-keyed registry - an engine with more than
        one driver (native) is reported once."""
        seen = set()
        engines = []
        for key, config_class in self._registered_config_types.items():
            engine = config_class_engine(config_class) or key
            if engine not in seen:
                seen.add(engine)
                engines.append(engine)
        return engines

    def get_engine_descriptors(self) -> List[Dict[str, Any]]:
        """
        Describe every registered DRIVER for the admin UI - one descriptor per
        driver, not deduped by engine. `native` has two drivers
        (`native.local`, always-present and singleton; `native.remote`,
        user-creatable) with different config classes and different fields;
        collapsing them to one "native" descriptor would silently hide
        whichever driver lost the dedup (see git history - `native.remote`'s
        own fields, base_url/worker_token/timeouts, were unreachable through
        this endpoint until this fixed it). An engine with exactly one driver
        (the common case, e.g. `comfyui`) still reports exactly one descriptor.

        Each descriptor carries its own label, `singleton` (exactly one
        auto-provisioned backend, never creatable) and `creatable` (the
        create-backend form may offer it - always `not singleton` today,
        reported explicitly so the frontend never re-derives it), and the
        configuration fields the driver's own config class declares. This is
        what lets the frontend render a create/edit form for a plugin-provided
        engine without knowing anything about it.
        """
        descriptors = []
        for driver, config_class in self._registered_config_types.items():
            engine = config_class_engine(config_class) or driver
            singleton = bool(getattr(config_class, "engine_singleton", False))
            descriptors.append({
                "engine": engine,
                "driver": driver,
                "label": getattr(config_class, "engine_label", None) or engine,
                "singleton": singleton,
                "creatable": not singleton,
                "fields": config_class.engine_fields(),
            })
        return descriptors

    def get_registered_backend_types(self) -> Dict[str, Type[BaseBackend]]:
        """Return all registered driver -> backend classes"""
        return self._registered_backend_types.copy()

    def get_registered_config_types(self) -> Dict[str, Type[BaseBackendConfig]]:
        """Return all registered driver -> config classes"""
        return self._registered_config_types.copy()

    def register_engine(
        self,
        driver: str,
        backend_class: Type[BaseBackend],
        config_class: Type[BaseBackendConfig]
    ):
        """Dynamically register a new driver at runtime.

        `driver` is the registration key an engine-only registration keys by
        its engine name (see `_register_builtin_backends`'s docstring).
        """
        self._registered_backend_types[driver] = backend_class
        self._registered_config_types[driver] = config_class
        self.backend_config_store._registered_config_types[driver] = config_class

        logger.info(f"[BACKEND_REGISTRY] Dynamically registered driver: {driver}")

    def _ensure_backends_initialized(self) -> None:
        """Run `_initialize_backends` on first real access rather than at construction.

        ``getattr`` default: tests construct a `BackendRegistry` via
        `__new__` (bypassing `__init__`) and wire only `_backends_cache`
        directly, with no `_backends_initialized` attribute at all - treat
        that the same as "already initialized" rather than raising.
        """
        if getattr(self, "_backends_initialized", True):
            return
        self._backends_initialized = True
        self._initialize_backends()

    def _initialize_backends(self):
        """Initialize all configured backends"""
        try:
            for config in self.backend_config_store.get_backends():
                if not config.enabled:
                    continue
                try:
                    self._backends_cache[config.id] = self._create_backend_instance(config)
                    logger.info(f"[BACKEND_REGISTRY] Initialized backend: {config.name} ({config.engine})")
                except Exception as e:
                    logger.error(f"[BACKEND_REGISTRY] Failed to initialize backend {config.id}: {str(e)}")

            logger.info(f"[BACKEND_REGISTRY] Initialized {len(self._backends_cache)} backends")

        except Exception as e:
            logger.error(f"[BACKEND_REGISTRY] Failed to initialize backends: {str(e)}")

    def _create_backend_instance(self, config: BaseBackendConfig) -> BaseBackend:
        """Create a backend instance from its config, by the DRIVER it declares.

        Instantiation is driver-scoped even though selection/defaults/priority
        stay engine-scoped everywhere else - see docs/backends.md.
        """
        driver = config.driver or config.engine
        backend_class = self._registered_backend_types.get(driver)
        if backend_class is None:
            raise ValueError(
                f"No backend implementation registered for driver '{driver}' "
                f"(engine '{config.engine}')"
            )

        backend = backend_class(backend_config=config)

        # In-process backends execute through a PipelineExecutor and need a
        # handle to one. One executor per backend - see __init__.
        if isinstance(backend, InProcessBackend):
            backend.set_generation_engine(self.generation_engine_factory())

        # Duck-typed rather than an isinstance check against one concrete
        # class: any driver (built-in or, one day, plugin-provided) that
        # needs the process's PipeCatalog/PluginRegistry declares so by
        # exposing this method, the same way InProcessBackend declares its
        # need via set_generation_engine above.
        if hasattr(backend, "bind_remote_context"):
            backend.bind_remote_context(pipe_catalog=self.pipe_catalog, plugin_registry=self.plugin_registry)

        return backend

    def get_backend(self, backend_id: str) -> Optional[BaseBackend]:
        """Get a backend by ID"""
        self._ensure_backends_initialized()
        return self._backends_cache.get(backend_id)

    def get_all_backends(self) -> Dict[str, BaseBackend]:
        """Get all registered backends"""
        self._ensure_backends_initialized()
        return self._backends_cache.copy()

    def get_available_backends(self) -> List[BaseBackend]:
        """Get all available backends sorted by priority (highest first)"""
        self._ensure_backends_initialized()
        available = [b for b in self._backends_cache.values() if b.is_available()]
        return sorted(available, key=lambda b: b.config.priority, reverse=True)

    def get_backends_for_engine(self, engine: str) -> List[BaseBackend]:
        """Get all available backends providing an engine, highest priority first"""
        return [b for b in self.get_available_backends() if b.engine == engine]

    def select_backend_for_generation(
        self,
        engine: str,
        backend_id: Optional[str] = None,
        allowed_backend_ids: Optional[List[str]] = None,
    ) -> BaseBackend:
        """
        Select the backend that will execute a generation.

        Selection algorithm:
        1. Candidates are the enabled backends providing `engine`.
        2. Candidates are narrowed to `allowed_backend_ids`, when given - the backends
           that hold every model the user selected (see src/features/models/availability.py).
        3. If `backend_id` is given, use it - but only if it is a candidate.
        4. Otherwise use the engine's default backend, if one is marked.
        5. Otherwise use the highest-priority candidate.

        Args:
            engine: The engine required by the preset
            backend_id: Specific backend to pin to (optional)
            allowed_backend_ids: Restrict candidates to these backends (optional)

        Returns:
            The selected backend

        Raises:
            NoBackendForEngineError: if no enabled backend provides the engine,
                or the requested backend_id is not a candidate.
        """
        candidates = self.get_backends_for_engine(engine)

        if allowed_backend_ids is not None:
            allowed = set(allowed_backend_ids)
            narrowed = [b for b in candidates if b.backend_id in allowed]
            if not narrowed:
                raise NoBackendForEngineError(
                    f"No enabled '{engine}' backend can load every selected model."
                )
            candidates = narrowed

        if not candidates:
            available = {b.engine for b in self._backends_cache.values() if b.is_available()}
            raise NoBackendForEngineError(
                f"No enabled backend provides engine '{engine}'. "
                f"Available engines: {sorted(available) or 'none'}. "
                f"Is the plugin providing '{engine}' enabled, and a backend configured for it?"
            )

        if backend_id:
            for backend in candidates:
                if backend.backend_id == backend_id:
                    logger.debug(f"[BACKEND_REGISTRY] Selected requested backend: {backend.name}")
                    return backend
            raise NoBackendForEngineError(
                f"Requested backend '{backend_id}' is not an enabled backend for engine '{engine}'"
            )

        default_config = self.backend_config_store.get_default_backend(engine)
        if default_config:
            default_backend = self._backends_cache.get(default_config.id)
            if default_backend in candidates:
                logger.debug(
                    f"[BACKEND_REGISTRY] Selected default backend for engine "
                    f"'{engine}': {default_backend.name}"
                )
                return default_backend

        backend = candidates[0]  # already sorted by priority
        logger.debug(
            f"[BACKEND_REGISTRY] Selected backend: {backend.name} "
            f"(engine={engine}, priority={backend.config.priority})"
        )
        return backend

    async def refresh_backends(self):
        """Refresh backend configurations and instances"""
        try:
            self._backends_cache.clear()
            self._backend_health_cache.clear()
            self.backend_config_store._backends_cache = None
            self._initialize_backends()
            self._backends_initialized = True

            logger.info("[BACKEND_REGISTRY] Refreshed all backends")

        except Exception as e:
            logger.error(f"[BACKEND_REGISTRY] Failed to refresh backends: {str(e)}")

    def get_backend_health(self, backend_id: str) -> Optional[BackendHealth]:
        """Get cached health status for a backend"""
        return self._backend_health_cache.get(backend_id)

    async def add_backend(self, backend_config: BaseBackendConfig):
        """Add a new backend to the registry"""
        self._ensure_backends_initialized()
        self.backend_config_store.add_backend(backend_config)

        if backend_config.enabled:
            self._backends_cache[backend_config.id] = self._create_backend_instance(backend_config)
            logger.info(f"[BACKEND_REGISTRY] Added backend: {backend_config.name}")

    async def update_backend(self, backend_id: str, backend_config: BaseBackendConfig):
        """Update an existing backend in the registry"""
        self._ensure_backends_initialized()
        self.backend_config_store.update_backend(backend_id, backend_config)

        self._backends_cache.pop(backend_id, None)

        if backend_config.enabled:
            self._backends_cache[backend_config.id] = self._create_backend_instance(backend_config)
            logger.info(f"[BACKEND_REGISTRY] Updated backend: {backend_config.name}")

    async def remove_backend(self, backend_id: str):
        """Remove a backend from the registry"""
        self._ensure_backends_initialized()
        self.backend_config_store.remove_backend(backend_id)
        self._backends_cache.pop(backend_id, None)
        self._backend_health_cache.pop(backend_id, None)

        logger.info(f"[BACKEND_REGISTRY] Removed backend: {backend_id}")

