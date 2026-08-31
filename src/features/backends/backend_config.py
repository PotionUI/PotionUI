from typing import Any, ClassVar, Dict, List, Optional, get_args
from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_core import PydanticUndefined
from enum import Enum

from src.features.backends.native_hardware import detect_native_hardware_defaults as _hardware_defaults
from src.platform.security.secrets import get_secret_cipher


# Engines are an OPEN set: `native` ships in core, plugins register their own
# (e.g. `comfyui`) through the backend.register hook. There is deliberately no
# enum here - a closed enum could not contain plugin-contributed engines.
NATIVE_ENGINE = "native"

# The native engine's always-present, in-process driver. `native` is the one
# engine with more than one driver: `native.local` (auto-provisioned,
# singleton) today, `native.remote` (user-creatable, multiple instances)
# eventually. See migration 119 and BackendRegistry.
NATIVE_LOCAL_DRIVER = "native.local"

# The native engine's out-of-process driver: dispatches to a headless Remote
# Native worker over HTTP (src/features/remote_execution/). User-creatable,
# not a singleton - an installation can point at any number of workers.
NATIVE_REMOTE_DRIVER = "native.remote"


class BackendStatus(str, Enum):
    AVAILABLE = "available"
    BUSY = "busy"
    OFFLINE = "offline"
    ERROR = "error"


# Fields every backend has, regardless of engine. Everything else a config class
# declares is engine-specific and is described to the admin UI via engine_fields().
BASE_CONFIG_FIELDS = frozenset({"id", "name", "engine", "driver", "enabled", "priority", "timeout_seconds"})


class BaseBackendConfig(BaseModel):
    """
    Base configuration for a backend - a configured instance of an engine.

    The engine is the protocol the backend speaks (declared by presets);
    everything else here is deployment configuration owned by the admin.

    Engine-specific settings are declared as extra fields on subclasses. They are
    reported to the admin UI by `engine_fields()`, so that no frontend code needs
    to know what any particular engine's settings are - plugin engines describe
    themselves.
    """
    id: str = Field(..., description="Unique identifier for the backend")
    name: str = Field(..., description="Human-readable name for the backend")
    engine: str = Field(..., description="Engine this backend provides (e.g. 'native', 'comfyui')")
    # Which registered implementation this backend uses - see BackendRegistry
    # and migration 119. Left blank, it defaults to the engine name: an engine
    # that only ever registered once (the common case, e.g. `comfyui`) has
    # exactly one driver, itself. That default is the permanent contract for
    # "engine-only" registration, not a placeholder to fill in later.
    driver: str = Field(default="", description="Registered implementation this backend uses (defaults to the engine name)")
    enabled: bool = Field(default=True, description="Whether this backend is enabled")
    priority: int = Field(default=1, description="Priority for backend selection (higher = higher priority)")
    timeout_seconds: int = Field(default=300, description="Timeout for generation requests")

    # Human-readable engine name for the admin UI. Subclasses may override.
    engine_label: ClassVar[Optional[str]] = None

    # A singleton driver supports exactly one backend, provisioned automatically,
    # and is never offered in the "create backend" UI.
    engine_singleton: ClassVar[bool] = False

    # Whether this driver's implementation runs in THIS process and owns local
    # resources (GPU VRAM, the host RAM model cache, attention/compile flags).
    # Gates the native-engine "Optimizations" panel (clear-vram, clear-cache,
    # attention backend pin, torch.compile flags, restart) - actions that mean
    # nothing for a backend that drives a separate process/host.
    is_local: ClassVar[bool] = False

    @model_validator(mode="after")
    def _default_driver_to_engine(self) -> "BaseBackendConfig":
        if not self.driver:
            self.driver = self.engine
        return self

    @classmethod
    def engine_fields(cls) -> List[Dict[str, Any]]:
        """
        Describe this engine's own configuration fields for the admin UI.

        Returns one descriptor per non-base field:
            {name, label, type, required, default, description, secret, options}

        `type` is one of "boolean" | "number" | "string". Mark a field secret with
        `Field(json_schema_extra={"secret": True})` to render it as a password input.
        Give a field a fixed choice list with
        `Field(json_schema_extra={"options": [...]})`; for a list that depends on the
        host (e.g. which CUDA devices exist), override `engine_field_options()`.
        """
        dynamic_options = cls.engine_field_options()

        specs: List[Dict[str, Any]] = []
        for name, field in cls.model_fields.items():
            if name in BASE_CONFIG_FIELDS:
                continue

            extra = field.json_schema_extra if isinstance(field.json_schema_extra, dict) else {}
            default = field.get_default(call_default_factory=True)

            specs.append({
                "name": name,
                "label": field.title or name.replace("_", " ").title(),
                "type": _field_type(field.annotation),
                "required": field.is_required(),
                "default": None if default is PydanticUndefined else default,
                "description": field.description,
                "secret": bool(extra.get("secret", False)),
                "options": dynamic_options.get(name, extra.get("options")),
            })
        return specs

    @classmethod
    def engine_field_options(cls) -> Dict[str, List[Any]]:
        """
        Host-dependent choice lists, keyed by field name.

        Overrides any static `options` declared on the field. Default: none.
        """
        return {}

    @classmethod
    def secret_field_names(cls) -> frozenset:
        """Names of fields marked `json_schema_extra={"secret": True}`.

        These hold credentials (e.g. a ComfyUI API key) and must never be
        echoed back to a client - serialization redacts them and update
        merges preserve the stored value when the client omits them.
        """
        names = set()
        for name, field in cls.model_fields.items():
            extra = field.json_schema_extra if isinstance(field.json_schema_extra, dict) else {}
            if extra.get("secret"):
                names.add(name)
        return frozenset(names)

    def quick_actions(self) -> List[Dict[str, Any]]:
        """
        Describe this backend's admin quick actions for the frontend.

        Each entry: {id, label, icon, endpoint, method, confirm, danger,
        poll_health_after}. `endpoint` is called as-is (already includes any
        backend id it needs); the frontend never hardcodes what an engine can
        do here - it only renders what this returns. `poll_health_after` tells
        the frontend to poll GET /health until the app responds again before
        refreshing state (used by actions that restart the process).

        Default: none. Engine-specific subclasses (built-in or plugin-provided)
        override this to describe their own operations.
        """
        return []

    def is_configured(self) -> bool:
        """Whether this backend has everything it needs to actually run.

        Default: always configured - a config class whose required fields
        fail pydantic validation can never be constructed in the first place.
        Overridden by a driver that allows saving with its connection details
        still blank (e.g. `NativeRemoteBackendConfig` before a worker is
        connected or provisioned), so admin surfaces and the enable guard can
        tell "saved" apart from "usable".
        """
        return True


def _field_type(annotation) -> str:
    """Map a pydantic annotation onto the three input types the admin UI renders."""
    args = get_args(annotation)
    if args:  # e.g. Optional[str] -> (str, NoneType)
        annotation = next((a for a in args if a is not type(None)), str)
    if annotation is bool:
        return "boolean"
    if annotation in (int, float):
        return "number"
    return "string"


class NativeBackendConfig(BaseBackendConfig):
    """
    Configuration for the built-in, in-process native backend.

    Device, dtype and the VRAM budget live here rather than in global settings:
    they configure how *this engine* loads and runs models, and no other engine
    consults them (a ComfyUI server picks its own device and manages its own VRAM).
    NativeBackend.prepare_pipes injects them into the pipeline.
    """
    engine: str = Field(default=NATIVE_ENGINE)
    driver: str = Field(default=NATIVE_LOCAL_DRIVER)

    engine_label: ClassVar[Optional[str]] = "Native"
    # One GPU, one GenerationEngine - exactly one native.local backend,
    # auto-provisioned. A future native.remote driver is a separate config
    # class with its own (non-singleton) declaration.
    engine_singleton: ClassVar[bool] = True
    # This driver runs in-process and owns this host's GPU/RAM.
    is_local: ClassVar[bool] = True

    # Defaults are hardware-derived (see native_hardware.detect_native_hardware_defaults),
    # not hardcoded: a GPU-less host gets cpu/float32, a modern NVIDIA card gets
    # cuda/bfloat16 and a VRAM ceiling sized off what's actually installed. An
    # explicit admin-set value (including one loaded from a persisted config dict)
    # always wins - `default_factory` only fires when the field is absent.
    device: str = Field(
        default_factory=lambda: _hardware_defaults().device,
        title="Device",
        description="Torch device used to load and run models",
    )
    dtype: str = Field(
        default_factory=lambda: _hardware_defaults().dtype,
        title="Precision",
        description="Data type for model tensors",
        json_schema_extra={"options": ["float32", "float16", "bfloat16"]},
    )
    gpu_max_vram: int = Field(
        default_factory=lambda: _hardware_defaults().gpu_max_vram,
        title="GPU Max VRAM (GB)",
        description="Upper bound on the VRAM this engine will budget for model loading",
    )

    @classmethod
    def engine_field_options(cls) -> Dict[str, List[Any]]:
        """Enumerate the torch devices actually present on this host."""
        devices = ["cpu"]
        try:
            import torch

            if torch.cuda.is_available():
                devices.extend(f"cuda:{i}" for i in range(torch.cuda.device_count()))
                devices.append("cuda")
        except Exception:  # torch missing or CUDA probe failed - cpu is still valid
            pass
        return {"device": devices}

    def quick_actions(self) -> List[Dict[str, Any]]:
        """Clear VRAM, Clear VRAM & Cache (RAM), and Restart Backend, all admin-only.

        Formerly the `clear-local-vram` marketplace plugin's job; that plugin
        existed only because the native engine had no self-description
        mechanism for actions. Now it's the native engine describing its own
        operation, same as `engine_fields()` describes its own config.

        `clear-vram` and `clear-cache` are deliberately distinct: the former
        only frees GPU memory (offloads resident weights to host RAM, which
        stays warm so the next generation re-uploads instead of reloading from
        disk); the latter additionally drops the host RAM cache itself and
        trims the allocator, returning that RAM to the OS - the heavier
        operation for when RAM (not just VRAM) needs to come back down.
        """
        return [
            {
                "id": "clear-vram",
                "label": "Clear VRAM",
                "icon": (
                    "M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6"
                    "m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"
                ),
                "endpoint": f"/api/backends/{self.id}/actions/clear-vram",
                "method": "POST",
                "confirm": (
                    "Clear app VRAM? This offloads all resident model weights off the GPU. "
                    "The RAM cache stays warm, so the next generation re-uploads instead of "
                    "reloading from disk."
                ),
                "danger": False,
                "poll_health_after": False,
            },
            {
                "id": "clear-cache",
                "label": "Clear VRAM & Cache (RAM)",
                "icon": (
                    "M4 7v10c0 1.657 3.582 3 8 3s8-1.343 8-3V7M4 7c0 1.657 3.582 3 8 3s8-1.343 8-3"
                    "M4 7c0-1.657 3.582-3 8-3s8 1.343 8 3m0 5c0 1.657-3.582 3-8 3s-8-1.343-8-3"
                ),
                "endpoint": f"/api/backends/{self.id}/actions/clear-cache",
                "method": "POST",
                "confirm": (
                    "Clear VRAM and the RAM model cache? This drops every cached model entirely "
                    "and returns that RAM to the OS. Next generation will reload models from disk "
                    "(slower than Clear VRAM alone)."
                ),
                "danger": True,
                "poll_health_after": False,
            },
            {
                "id": "restart-backend",
                "label": "Restart Backend",
                "icon": (
                    "M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"
                ),
                "endpoint": "/api/admin/restart",
                "method": "POST",
                "confirm": "Restart the app now? Active generations will be interrupted.",
                "danger": True,
                "poll_health_after": True,
            },
        ]


class NativeRemoteBackendConfig(BaseBackendConfig):
    """
    Configuration for a `native.remote` backend - one headless Remote Native
    worker (src/features/remote_execution/worker/) this installation dispatches
    execution packages to over HTTP/SSE.

    Unlike `native.local`, this driver owns no local GPU/RAM: device/dtype/VRAM
    are the worker's own decision (see `docs/remote-native.md`), so
    RemoteNativeBackend.prepare_pipes must NOT inject them the way
    NativeBackend.prepare_pipes does - that is the entire point of running a
    package off-box.
    """
    engine: str = Field(default=NATIVE_ENGINE)
    driver: str = Field(default=NATIVE_REMOTE_DRIVER)

    engine_label: ClassVar[Optional[str]] = "Native (Remote Worker)"
    # Any number of workers may be configured, unlike the single auto-provisioned
    # native.local backend.
    engine_singleton: ClassVar[bool] = False
    # This driver dispatches to another process/host; it owns no local GPU/RAM.
    is_local: ClassVar[bool] = False

    # Both blank is a legal, "not yet connected" state: a `native.remote`
    # backend row may be created before a worker exists at all, so an admin
    # can provision straight into it later (see `is_configured` and
    # `src.features.provisioning.operations.provision_compute`). `enabled`
    # is guarded separately - see `BackendController` - so an unconfigured
    # row can never be selected for dispatch.
    base_url: str = Field(
        default="",
        title="Worker URL",
        description="Base URL of the Remote Native worker, e.g. http://10.0.0.5:8100",
    )
    worker_token: str = Field(
        default="",
        title="Worker Token",
        description="Bearer token the worker's POTIONUI_WORKER_TOKEN was started with",
        json_schema_extra={"secret": True},
    )
    connect_timeout_seconds: float = Field(
        default=10.0,
        title="Connect Timeout (s)",
        description="How long to wait to establish a connection to the worker",
    )
    request_timeout_seconds: float = Field(
        default=60.0,
        title="Request Timeout (s)",
        description="How long to wait for a non-streaming worker response (handshake, submit, asset upload, artifact download)",
    )

    @field_validator('base_url')
    @classmethod
    def _normalize_base_url(cls, v):
        return v.rstrip('/') if v else v

    def is_configured(self) -> bool:
        return bool(self.base_url and self.worker_token)


class BackendHealth(BaseModel):
    """Backend health status"""
    backend_id: str
    status: BackendStatus
    last_check: Optional[str] = None
    response_time_ms: Optional[float] = None
    error_message: Optional[str] = None
    gpu_info: Optional[Dict[str, Any]] = None
    system_info: Optional[Dict[str, Any]] = None


def config_class_engine(config_class: type) -> str:
    """The engine name a registered config CLASS declares by default, or "" if
    it doesn't declare one (e.g. a test double that isn't a BaseBackendConfig).

    Registries key implementations/configs by driver, but engine-facing
    surfaces (the admin "create backend" engine picker, `validate_backend_config`,
    which key off the client-supplied `engine`) need the engine name back
    without instantiating the class - every real config class declares one via
    a field default (`engine: str = Field(default=...)`), the same convention
    `engine_fields()` already reads defaults through.
    """
    fields = getattr(config_class, "model_fields", None)
    if not fields:
        return ""
    field = fields.get("engine")
    if field is None:
        return ""
    default = field.get_default(call_default_factory=True)
    return default if isinstance(default, str) else ""


def _encrypt_secret_fields(config: Dict[str, Any], secret_fields: frozenset) -> None:
    """Encrypt the secret-marked entries of a backend config dict, in place.

    Only the declared credential fields are wrapped, so the rest of the blob
    stays plain JSON that a migration can still read and rewrite. Which fields
    are secret is knowledge the config class holds, which is why this happens
    here rather than in the repository - the read side needs no such knowledge,
    because the envelope announces itself.
    """
    if not secret_fields:
        return
    cipher = get_secret_cipher()
    for name in secret_fields:
        value = config.get(name)
        if isinstance(value, str) and value:
            config[name] = cipher.encrypt(value)


class BackendConfigStore:
    """Manager for backend configurations"""

    def __init__(self, backend_repository=None, registered_config_types=None):
        if backend_repository is None:
            from src.features.backends.repository import backend_repo
            backend_repository = backend_repo
        self.backend_repository = backend_repository
        self._backends_cache = None

        # Registered config types from BackendRegistry, keyed by DRIVER (includes
        # plugin engines - an engine-only registration's key is its engine name,
        # which is also its one driver; see BackendRegistry).
        self._registered_config_types: Dict[str, type] = registered_config_types or {}

        # The native engine's local driver is always available
        if NATIVE_LOCAL_DRIVER not in self._registered_config_types:
            self._registered_config_types[NATIVE_LOCAL_DRIVER] = NativeBackendConfig

    def get_backends(self) -> List[BaseBackendConfig]:
        """Get all configured backends"""
        if self._backends_cache is None:
            self._load_backends()
        return self._backends_cache

    def get_backend(self, backend_id: str) -> Optional[BaseBackendConfig]:
        """Get a specific backend by ID"""
        for backend in self.get_backends():
            if backend.id == backend_id:
                return backend
        return None

    def get_enabled_backends(self) -> List[BaseBackendConfig]:
        """Get all enabled backends sorted by priority"""
        backends = [b for b in self.get_backends() if b.enabled]
        return sorted(backends, key=lambda x: x.priority, reverse=True)

    def get_backends_for_engine(self, engine: str) -> List[BaseBackendConfig]:
        """Get all enabled backends providing a given engine, highest priority first"""
        return [b for b in self.get_enabled_backends() if b.engine == engine]

    def add_backend(self, backend_config: BaseBackendConfig) -> None:
        """Add a new backend configuration"""
        backends = self.get_backends()

        if any(b.id == backend_config.id for b in backends):
            raise ValueError(f"Backend with ID '{backend_config.id}' already exists")

        # A singleton driver (native.local: in-process, single-GPU) supports
        # exactly one backend, provisioned automatically. Scoped to the DRIVER's
        # own config class, not the engine, so a future non-singleton driver of
        # the same engine (e.g. native.remote) is unaffected.
        if type(backend_config).engine_singleton:
            raise ValueError(
                f"The '{backend_config.driver}' backend is provisioned automatically "
                "and supports exactly one instance."
            )

        backends.append(backend_config)
        self._save_backends(backends)

    def update_backend(self, backend_id: str, backend_config: BaseBackendConfig) -> None:
        """Update an existing backend configuration"""
        backends = self.get_backends()

        for i, backend in enumerate(backends):
            if backend.id == backend_id:
                backend_config.id = backend_id  # Preserve the original ID
                backends[i] = backend_config
                self._save_backends(backends)
                return

        raise ValueError(f"Backend with ID '{backend_id}' not found")

    def remove_backend(self, backend_id: str) -> None:
        """Remove a backend configuration"""
        backends = self.get_backends()

        for backend in backends:
            if backend.id == backend_id and type(backend).engine_singleton:
                raise ValueError(f"Cannot remove the '{backend.driver}' backend")

        updated_backends = [b for b in backends if b.id != backend_id]

        if len(updated_backends) == len(backends):
            raise ValueError(f"Backend with ID '{backend_id}' not found")

        self._save_backends(updated_backends)

    def _entity_to_config(self, backend_entity) -> Optional[BaseBackendConfig]:
        """Convert a persisted Backend entity into its registered config object.

        Looked up by DRIVER, not engine: a row whose driver has no registered
        implementation (its plugin is disabled, or - for `native.remote` today -
        no implementation exists yet) is reported unparseable exactly like an
        unknown-engine row always has been, and simply doesn't appear in
        `get_backends()`. The row itself is untouched in the repository.
        """
        config_class = self._registered_config_types.get(backend_entity.driver)
        if not config_class:
            return None

        backend_data = {
            'id': backend_entity.id,
            'name': backend_entity.name,
            'engine': backend_entity.engine,
            'driver': backend_entity.driver,
            'enabled': backend_entity.enabled,
            'priority': backend_entity.config.get('priority', 1),
            'timeout_seconds': backend_entity.config.get('timeout_seconds', 300),
            **backend_entity.config,  # Include all engine-specific config fields
        }
        return config_class(**backend_data)

    def _load_backends(self) -> None:
        """Load backends from repository"""
        from src.features.backends.records import Backend
        from src.platform.observability.logger import logger

        backend_entities = self.backend_repository.get_all()

        # The native.local backend always exists - it is this process. Scoped
        # to the driver, not the engine, so a native.remote row (once that
        # driver exists) neither suppresses nor is suppressed by this.
        if not any(b.driver == NATIVE_LOCAL_DRIVER for b in backend_entities):
            native_backend = Backend(
                id="native",
                name="Local Generation",
                engine=NATIVE_ENGINE,
                driver=NATIVE_LOCAL_DRIVER,
                enabled=True,
                is_default=True,
                config={}
            )
            self.backend_repository.create(native_backend)
            backend_entities.append(native_backend)

        backends = []
        for backend_entity in backend_entities:
            try:
                config = self._entity_to_config(backend_entity)
                if config is None:
                    logger.warning(
                        f"[BACKEND_CONFIG] Backend '{backend_entity.id}' declares unknown "
                        f"driver '{backend_entity.driver}' (is its plugin enabled?)"
                    )
                    continue
                backends.append(config)
            except Exception as e:
                logger.error(f"[BACKEND_CONFIG] Error parsing backend '{backend_entity.id}': {e}")

        self._backends_cache = backends

    def _save_backends(self, backends: List[BaseBackendConfig]) -> None:
        """Save backends to repository"""
        from src.features.backends.records import Backend

        current_backends = {b.id: b for b in self.backend_repository.get_all()}

        for backend_config in backends:
            config_dict = backend_config.model_dump()
            base_fields = {'id', 'name', 'engine', 'driver', 'enabled'}
            config = {k: v for k, v in config_dict.items() if k not in base_fields}
            _encrypt_secret_fields(config, type(backend_config).secret_field_names())

            existing = current_backends.get(backend_config.id)

            backend_entity = Backend(
                id=backend_config.id,
                name=backend_config.name,
                engine=backend_config.engine,
                driver=backend_config.driver,
                enabled=backend_config.enabled,
                # The default flag is managed via BackendRepository.set_default(),
                # not through config saves - preserve whatever is persisted.
                is_default=existing.is_default if existing else False,
                config=config
            )

            if existing:
                self.backend_repository.update(backend_config.id, backend_entity)
            else:
                self.backend_repository.create(backend_entity)

        # Remove backends that are no longer in the list
        for backend_id in set(current_backends.keys()) - {b.id for b in backends}:
            self.backend_repository.delete(backend_id)

        self._backends_cache = backends

    def encrypt_stored_credentials(self) -> int:
        """Encrypt any backend credential still stored in the clear.

        Runs at startup rather than in a migration: which config fields are
        secret is declared by the engine's config class, and plugin-contributed
        engines only exist once the plugin registry has been built. Idempotent -
        an already-enveloped value is left alone.

        Returns the number of backends rewritten.
        """
        import json

        cipher = get_secret_cipher()
        rewritten = 0
        for row in self.backend_repository.iter_encrypted_configs():
            entity = self.backend_repository.get_by_id(row['id'])
            if entity is None:
                continue
            config_class = self._registered_config_types.get(entity.driver)
            if config_class is None:
                continue
            secret_fields = config_class.secret_field_names()
            if not secret_fields:
                continue
            try:
                raw_config = json.loads(row['config']) if row['config'] else {}
            except (TypeError, ValueError):
                continue
            changed = False
            for name in secret_fields:
                value = raw_config.get(name)
                if isinstance(value, str) and value and not cipher.is_encrypted(value):
                    raw_config[name] = cipher.encrypt(value)
                    changed = True
            if changed:
                self.backend_repository.replace_config(row['id'], json.dumps(raw_config))
                rewritten += 1
        if rewritten:
            self._backends_cache = None
        return rewritten

    def get_default_backend(self, engine: str) -> Optional[BaseBackendConfig]:
        """
        Get the default backend for an engine.

        Falls back to the highest-priority enabled backend of that engine.
        Returns None if the engine has no enabled backend.
        """
        default_entity = self.backend_repository.get_default(engine)
        if default_entity and default_entity.enabled:
            config = self._entity_to_config(default_entity)
            if config is not None:
                return config

        candidates = self.get_backends_for_engine(engine)
        return candidates[0] if candidates else None

    def get_default_backend_ids(self) -> Dict[str, str]:
        """Map of engine -> id of the backend marked default for that engine."""
        return {
            b.engine: b.id
            for b in self.backend_repository.get_all()
            if b.is_default
        }

    def set_default_backend(self, backend_id: str) -> None:
        """Mark a backend as the default for its engine."""
        backend = self.get_backend(backend_id)
        if not backend:
            raise ValueError(f"Backend with ID '{backend_id}' not found")
        self.backend_repository.set_default(backend_id, backend.engine)

    def validate_backend_config(self, backend_config: Dict[str, Any]) -> BaseBackendConfig:
        """Validate and parse backend configuration using registered engines.

        The client (the admin "create backend" form, `backend.detect`) sends
        `engine`, never `driver` - the engine-only-registration default (driver
        == engine name) is exactly what resolves that key against the
        driver-keyed registry. A client that DOES send an explicit `driver`
        (a future native.remote picker) is honored instead.
        """
        engine = backend_config.get('engine')
        driver = backend_config.get('driver') or engine
        config_class = self._registered_config_types.get(driver)

        if not config_class:
            raise ValueError(f"Unknown engine: {engine}")
        return config_class(**backend_config)

    def get_supported_engines(self) -> List[str]:
        """Return all registered engines (built-in + plugin-provided), deduplicated
        from the driver-keyed registry (an engine with more than one driver, e.g.
        native, is reported once). Falls back to the registration key itself for
        a config class that doesn't self-describe an engine."""
        seen = set()
        engines = []
        for key, config_class in self._registered_config_types.items():
            engine = config_class_engine(config_class) or key
            if engine not in seen:
                seen.add(engine)
                engines.append(engine)
        return engines
