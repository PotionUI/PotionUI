import uuid
from dataclasses import asdict
from typing import TYPE_CHECKING, Dict, Any, List, Optional
from fastapi import APIRouter, Depends, Query

from src.platform.http.base_controller import BaseController, APIResponse
from src.platform.http.error_pipeline import Fail, guarded
from src.platform.security.failure_text import failure_detail
from src.platform.security.current_user import get_current_active_user, get_current_admin_user
from src.features.backends.dto import (
    AttentionBackendRequest,
    BackendCreateRequest,
    BackendUpdateRequest,
    EngineFlagsRequest,
)
from src.features.backends.backend_registry import BackendRegistry
from src.platform.settings.settings import Settings
from src.platform.plugins.runtime_registries import get_global_plugin_registry
from src.features.backends.hooks import BACKEND_HOOKS
from src.features.backends.model_listing import ModelListingNotSupported
from src.platform.security.user import AccountType, User

if TYPE_CHECKING:
    from src.bootstrap.container import AppContainer

# What a client may send back in a secret field to mean "leave the stored value
# unchanged". A redacted read never emits the secret itself, so a well-behaved
# client sends nothing; this sentinel is accepted defensively too.
_MASKED_SECRET = "__stored__"


def _operator_detail(exc: Exception, admin: bool) -> str:
    return failure_detail(exc, True)


def _failed(code: str, message: str, operator: bool = False) -> Fail:
    return Fail(code, message, status=400, detail=_operator_detail if operator else failure_detail)


def _is_blank_secret(value: Any) -> bool:
    """A secret value that must NOT overwrite the stored one: absent, empty, or the mask."""
    if value is None or value == _MASKED_SECRET:
        return True
    return isinstance(value, str) and value.strip() == ""


def _redacted_backend_dump(backend) -> Dict[str, Any]:
    """model_dump() with every secret-marked field removed, plus `configured`.

    Each secret `foo` is replaced by a boolean `has_foo` so the UI can show
    "configured / not configured" without ever receiving the credential.
    `configured` (see `BaseBackendConfig.is_configured`) is the same idea one
    level up: whether the backend has everything it needs to actually run,
    not just whether a credential happens to be non-blank.
    """
    data = backend.model_dump()
    for name in type(backend).secret_field_names():
        present = not _is_blank_secret(data.get(name))
        data.pop(name, None)
        data[f"has_{name}"] = present
    data["configured"] = backend.is_configured()
    return data


def serialize_backend(backend, default_ids: Dict[str, str]) -> Dict[str, Any]:
    data = _redacted_backend_dump(backend)
    data["is_default"] = default_ids.get(backend.engine) == backend.id
    data["quick_actions"] = backend.quick_actions()
    return data


def enabled_backends_payload(backend_config_store) -> List[Dict[str, Any]]:
    default_ids = backend_config_store.get_default_backend_ids()
    return [serialize_backend(backend, default_ids) for backend in backend_config_store.get_enabled_backends()]


def _cuda_allocated_gb() -> float:
    try:
        import torch
        if torch.cuda.is_available():
            return torch.cuda.memory_allocated() / (1024 ** 3)
    except ImportError:
        pass
    return 0.0


class BackendController(BaseController):
    def __init__(
        self,
        settings: Settings,
        backend_registry: BackendRegistry,
        model_lifecycle=None,
        model_indexing_coordinator=None,
    ):
        super().__init__()
        self.settings = settings
        self.backend_registry = backend_registry
        self.model_lifecycle = model_lifecycle
        self.model_indexing_coordinator = model_indexing_coordinator
        # Use the backend_config_store from the registry to get plugin-registered types
        self.backend_config_store = backend_registry.backend_config_store

    def _require_admin(self, user: Optional[User]) -> None:
        if not user:
            self.error_response(
                error="authentication_required",
                message="Authentication required to access backend optimizations",
                status_code=401,
            )
        if user.account_type != AccountType.ADMIN:
            self.error_response(
                error="admin_required",
                message="Backend optimizations are available to administrators only",
                status_code=403,
            )

    def _require_local_backend(self, backend_id: str):
        """Look up a backend and confirm it runs in THIS process.

        Optimizations (SageAttention/flash-attn builds, the attention-backend
        pin, torch.compile/stream-prefetch flags, VRAM/RAM-cache clearing) act on
        resources this process itself owns - gated on the `is_local` capability
        of the backend's registered driver, not on its engine: a backend can
        speak the native engine without running locally (a future
        `native.remote` driver), and every non-local backend (a ComfyUI server
        today) drives its own process/host and has nothing here to discover or
        install.
        """
        backend_config = self.backend_config_store.get_backend(backend_id)
        if not backend_config:
            return self.error_response(
                error="backend_not_found",
                message=f"Backend '{backend_id}' not found",
                status_code=404,
            )
        if not type(backend_config).is_local:
            return self.error_response(
                error="optimizations_not_supported",
                message=f"Backend '{backend_id}' does not run locally in this process",
                status_code=400,
            )
        return backend_config

    def _require_native_backend(self, backend_id: str):
        from src.features.backends.backend_config import ENGINE_FLAGS_FIELD

        backend_config = self.backend_config_store.get_backend(backend_id)
        if not backend_config:
            return self.error_response(
                error="backend_not_found",
                message=f"Backend '{backend_id}' not found",
                status_code=404,
            )
        if ENGINE_FLAGS_FIELD not in type(backend_config).model_fields:
            return self.error_response(
                error="engine_flags_not_supported",
                message=f"Backend '{backend_id}' has no native engine settings",
                status_code=400,
            )
        return backend_config

    def _save_engine_flags(self, backend_config, changes: Dict[str, Any]) -> Dict[str, Any]:
        from src.platform.settings.runtime_flags import activate_engine_flags, engine_flag_values

        merged = {**backend_config.engine_flags, **changes}
        backend_config.engine_flags = merged
        self.backend_config_store.update_backend(backend_config.id, backend_config)
        if type(backend_config).is_local:
            return activate_engine_flags(merged)
        return engine_flag_values(merged)

    def _serialize(self, backend, default_ids: Dict[str, str] = None) -> Dict[str, Any]:
        """Serialize a backend config, adding the persisted per-engine default flag."""
        if default_ids is None:
            default_ids = self.backend_config_store.get_default_backend_ids()
        return serialize_backend(backend, default_ids)

    @guarded(None, _failed("backend_list_failed", "Failed to list backends"))
    async def list_backends(self) -> APIResponse:
        """List all configured backends"""
        backends = self.backend_config_store.get_backends()
        default_ids = self.backend_config_store.get_default_backend_ids()
        backend_data = [self._serialize(b, default_ids) for b in backends]
        return self.success_response(data=backend_data)

    @guarded(None, _failed("enabled_backends_failed", "Failed to get enabled backends"))
    async def get_enabled_backends(self) -> APIResponse:
        """Get all enabled backends sorted by priority"""
        return self.success_response(data=enabled_backends_payload(self.backend_config_store))

    @guarded(None, _failed("default_backend_failed", "Failed to get default backend"))
    async def get_default_backend(self, engine: str) -> APIResponse:
        """Get the default backend for an engine"""
        backend = self.backend_config_store.get_default_backend(engine)
        if not backend:
            return self.error_response(
                error="no_backend_for_engine",
                message=f"No enabled backend provides engine '{engine}'",
                status_code=404
            )
        return self.success_response(data=self._serialize(backend))

    @guarded(None, _failed("engines_failed", "Failed to get registered engines"))
    async def get_engines(self) -> APIResponse:
        """
        Describe all registered engines (built-in + plugin-provided).

        Each descriptor carries the engine's label, whether it is a singleton, and
        the configuration fields it needs - so the admin UI never hardcodes the
        settings of any particular engine.
        """
        return self.success_response(data=self.backend_registry.get_engine_descriptors())

    @guarded(
        {ValueError: Fail("backend_not_found", status=404)},
        _failed("set_default_failed", "Failed to set default backend", operator=True),
    )
    async def set_default_backend(self, backend_id: str) -> APIResponse:
        """Make a backend the default for its engine"""
        self.backend_config_store.set_default_backend(backend_id)
        await self.backend_registry.refresh_backends()
        return self.success_response(message=f"Backend '{backend_id}' is now the default for its engine")

    @guarded(None, _failed("backend_get_failed", "Failed to get backend"))
    async def get_backend(self, backend_id: str) -> APIResponse:
        """Get a specific backend by ID"""
        backend = self.backend_config_store.get_backend(backend_id)
        if not backend:
            return self.error_response(
                error="backend_not_found",
                message=f"Backend '{backend_id}' not found",
                status_code=404
            )
        return self.success_response(data=self._serialize(backend))

    @guarded(
        {ValueError: Fail("backend_validation_failed", status=400)},
        _failed("backend_create_failed", "Failed to create backend", operator=True),
    )
    async def create_backend(self, backend_data: Dict[str, Any]) -> APIResponse:
        """Create a new backend configuration"""
        if 'id' not in backend_data or not backend_data['id']:
            backend_data['id'] = str(uuid.uuid4())

        plugin_registry = get_global_plugin_registry()
        if plugin_registry and plugin_registry.hook_chain:
            context, _ = plugin_registry.hook_chain.execute(
                BACKEND_HOOKS.before_create,
                initial_data={"backend_data": backend_data}
            )
            backend_data = context.get("backend_data", backend_data)

        backend_config = self.backend_config_store.validate_backend_config(backend_data)

        if backend_config.enabled and not backend_config.is_configured():
            backend_config.enabled = False

        self.backend_config_store.add_backend(backend_config)

        await self.backend_registry.refresh_backends()

        if plugin_registry and plugin_registry.hook_chain:
            plugin_registry.hook_chain.execute(
                BACKEND_HOOKS.after_create,
                initial_data={"backend_config": backend_config.model_dump()}
            )

        self.logger.info(f"Created backend: {backend_config.id}")
        return self.success_response(
            data=_redacted_backend_dump(backend_config),
            message=f"Backend '{backend_config.name}' created successfully"
        )

    @guarded(
        {ValueError: Fail("backend_validation_failed", status=400)},
        _failed("backend_update_failed", "Failed to update backend", operator=True),
    )
    async def update_backend(self, backend_id: str, backend_data: Dict[str, Any]) -> APIResponse:
        """Update an existing backend configuration"""
        plugin_registry = get_global_plugin_registry()
        if plugin_registry and plugin_registry.hook_chain:
            context, _ = plugin_registry.hook_chain.execute(
                BACKEND_HOOKS.before_update,
                initial_data={"backend_id": backend_id, "backend_data": backend_data}
            )
            backend_data = context.get("backend_data", backend_data)

        existing = self.backend_config_store.get_backend(backend_id)
        if not existing:
            return self.error_response(
                error="backend_not_found",
                message=f"Backend '{backend_id}' not found",
                status_code=404
            )

        existing_dump = existing.model_dump()
        merged = dict(existing_dump)
        merged.update(backend_data)
        merged["id"] = backend_id
        merged["engine"] = existing.engine
        merged["driver"] = existing.driver

        for name in type(existing).secret_field_names():
            if _is_blank_secret(backend_data.get(name)):
                merged[name] = existing_dump.get(name)

        backend_config = self.backend_config_store.validate_backend_config(merged)

        if backend_config.enabled and not backend_config.is_configured():
            raise ValueError("Connect or provision this backend before enabling it.")

        self.backend_config_store.update_backend(backend_id, backend_config)

        await self.backend_registry.refresh_backends()

        if plugin_registry and plugin_registry.hook_chain:
            plugin_registry.hook_chain.execute(
                BACKEND_HOOKS.after_update,
                initial_data={"backend_id": backend_id, "backend_config": backend_config.model_dump()}
            )

        self.logger.info(f"Updated backend: {backend_id}")
        return self.success_response(
            data=_redacted_backend_dump(backend_config),
            message=f"Backend '{backend_config.name}' updated successfully"
        )

    @guarded(
        {ValueError: Fail("backend_delete_failed", status=400)},
        _failed("backend_delete_failed", "Failed to delete backend", operator=True),
    )
    async def delete_backend(self, backend_id: str) -> APIResponse:
        """Delete a backend configuration"""
        plugin_registry = get_global_plugin_registry()
        if plugin_registry and plugin_registry.hook_chain:
            plugin_registry.hook_chain.execute(
                BACKEND_HOOKS.before_delete,
                initial_data={"backend_id": backend_id}
            )

        self.backend_config_store.remove_backend(backend_id)

        await self.backend_registry.refresh_backends()

        if plugin_registry and plugin_registry.hook_chain:
            plugin_registry.hook_chain.execute(
                BACKEND_HOOKS.after_delete,
                initial_data={"backend_id": backend_id}
            )

        self.logger.info(f"Deleted backend: {backend_id}")
        return self.success_response(message=f"Backend '{backend_id}' deleted successfully")

    @guarded(
        {ModelListingNotSupported: Fail("model_listing_not_supported", status=400)},
        _failed("backend_index_failed", "Failed to index models for backend", operator=True),
    )
    async def index_backend_models(self, backend_id: str) -> APIResponse:
        """Ask a backend what models it can load, and record the answer.

        Indexing lives on the backend, not on /models, because "which models exist" is a
        fact about a backend. Native answers with a filesystem scan; a ComfyUI server
        answers over HTTP. See docs/models.md.
        """
        from src.features.backends.backend_config import NATIVE_ENGINE, NATIVE_LOCAL_DRIVER
        from src.features.models.backend_indexer import backend_model_indexer

        backend = self.backend_registry.get_backend(backend_id)
        if not backend:
            if not self.backend_config_store.get_backend(backend_id):
                return self.error_response(
                    error="backend_not_found",
                    message=f"Backend '{backend_id}' not found",
                    status_code=404
                )
            return self.error_response(
                error="backend_not_active",
                message=f"Backend '{backend_id}' is not active (may be disabled)"
            )

        is_native_local = (
            backend.engine == NATIVE_ENGINE and getattr(backend.config, "driver", "") == NATIVE_LOCAL_DRIVER
        )
        if is_native_local:
            if self.model_indexing_coordinator is None:
                return self.error_response(
                    error="model_listing_not_supported",
                    message="Native model indexing is not wired up on this controller",
                    status_code=400,
                )
            self.model_indexing_coordinator.rescan(trigger="admin_reindex")
            return self.success_response(
                data=self.model_indexing_coordinator.status(),
                message=f"Started rescanning model roots for '{backend.name}'",
            )

        result = await backend_model_indexer.index_backend(backend)
        return self.success_response(
            data=result.to_dict(),
            message=(
                f"Indexed {result.listed} models from '{backend.name}': "
                f"{result.created} new, {result.matched} matched, {result.removed} removed, "
                f"{result.orphans_removed} orphans removed"
                + (f", {len(result.type_mismatches)} skipped for a type mismatch" if result.type_mismatches else "")
            )
        )

    @guarded(None, _failed("backend_stats_failed", "Failed to get backend stats"))
    async def get_backend_stats(self, backend_id: str) -> APIResponse:
        """How many models this backend has reported, and how much disk space they
        total - from the last `index_backend_models` run, not a live re-scan.

        Per-backend, not global: "files on disk" only means something for the
        specific backend that indexed them (a remote ComfyUI server's disk isn't
        this host's), so this reports what `model_availability` actually knows
        per backend rather than an instance-wide aggregate.
        """
        from src.features.models.availability_repository import model_availability_repo

        if not self.backend_config_store.get_backend(backend_id):
            return self.error_response(
                error="backend_not_found",
                message=f"Backend '{backend_id}' not found",
                status_code=404
            )

        stats = model_availability_repo.stats_for_backend(backend_id)
        total_size_bytes = stats["total_size_bytes"]
        return self.success_response(data={
            "backend_id": backend_id,
            "indexed_models": stats["indexed_models"],
            "total_size_bytes": total_size_bytes,
            "total_size_gb": round(total_size_bytes / (1024 ** 3), 2) if total_size_bytes else 0,
            "last_indexed_at": stats["last_indexed_at"],
        })

    @guarded(None, _failed("backend_test_failed", "Failed to test backend", operator=True))
    async def test_backend(self, backend_id: str) -> APIResponse:
        backend_instance = self.backend_registry.get_backend(backend_id)
        if not backend_instance:
            backend_config = self.backend_config_store.get_backend(backend_id)
            if not backend_config:
                return self.error_response(
                    error="backend_not_found",
                    message=f"Backend '{backend_id}' not found",
                    status_code=404
                )
            return self.error_response(
                error="backend_not_active",
                message=f"Backend '{backend_id}' is not active (may be disabled)"
            )

        health_info = await backend_instance.health_check()

        status = health_info.get("status", "unknown") if isinstance(health_info, dict) else getattr(health_info, "status", "unknown")
        if status in ("available", "healthy"):
            return self.success_response(
                data=health_info if isinstance(health_info, dict) else health_info,
                message="Backend connection test successful"
            )
        elif status == "offline":
            return self.success_response(
                data=health_info if isinstance(health_info, dict) else health_info,
                message="Backend is offline or unreachable"
            )
        else:
            return self.success_response(
                data=health_info if isinstance(health_info, dict) else health_info,
                message=f"Backend status: {status}"
            )

    @guarded(None, _failed("backend_health_failed", "Failed to get backend health", operator=True))
    async def get_backend_health(self, backend_id: str) -> APIResponse:
        backend_config = self.backend_config_store.get_backend(backend_id)
        if not backend_config:
            return self.error_response(
                error="backend_not_found",
                message=f"Backend '{backend_id}' not found",
                status_code=404
            )

        backend_instance = self.backend_registry.get_backend(backend_id)
        if backend_instance:
            try:
                health = await backend_instance.health_check()
                if not isinstance(health, dict):
                    health = health.model_dump() if hasattr(health, 'model_dump') else {"status": str(health)}
            except Exception as e:
                health = {
                    "status": "error",
                    "error": f"Health check failed: {failure_detail(e, True)}"
                }
        else:
            health = {
                "status": "inactive",
                "message": "Backend is not active (may be disabled)"
            }

        return self.success_response(data={
            "backend_id": backend_config.id,
            "backend_name": backend_config.name,
            "backend_engine": backend_config.engine,
            "enabled": backend_config.enabled,
            "health": health
        })

    @guarded(None, _failed("backend_system_info_failed", "Failed to get backend system info", operator=True))
    async def get_backend_system_info(self, backend_id: str) -> APIResponse:
        backend_config = self.backend_config_store.get_backend(backend_id)
        if not backend_config:
            return self.error_response(
                error="backend_not_found",
                message=f"Backend '{backend_id}' not found",
                status_code=404
            )

        backend_instance = self.backend_registry.get_backend(backend_id)
        if backend_instance:
            try:
                system_info = await backend_instance.get_system_info()
            except Exception as e:
                system_info = {"error": f"Failed to get system info: {failure_detail(e, True)}"}
        else:
            system_info = {"message": "Backend is not active (may be disabled)"}

        return self.success_response(data=system_info)

    @guarded(None, _failed("backend_health_list_failed", "Failed to get backend health list", operator=True))
    async def list_backend_health(self) -> APIResponse:
        backend_configs = self.backend_config_store.get_backends()
        health_list = []

        for backend_config in backend_configs:
            # Try to get the backend instance and call its health_check
            backend_instance = self.backend_registry.get_backend(backend_config.id)
            if backend_instance:
                try:
                    health = await backend_instance.health_check()
                    if not isinstance(health, dict):
                        health = health.model_dump() if hasattr(health, 'model_dump') else {"status": str(health)}
                except Exception as e:
                    health = {
                        "status": "error",
                        "error": f"Health check failed: {failure_detail(e, True)}"
                    }
            else:
                health = {
                    "status": "inactive",
                    "message": "Backend is not active (may be disabled)"
                }

            health_list.append({
                "backend_id": backend_config.id,
                "backend_name": backend_config.name,
                "backend_engine": backend_config.engine,
                "enabled": backend_config.enabled,
                "health": health
            })

        return self.success_response(data=health_list)


    @guarded(None, _failed("clear_vram_failed", "Failed to clear VRAM"), admin="user")
    async def clear_backend_vram(self, backend_id: str, user: Optional[User] = None) -> APIResponse:
        """Offload every GPU-resident model to host RAM (VRAM-only teardown).

        This is the native engine's "Clear VRAM" quick action (see
        NativeBackendConfig.quick_actions()) - it replaces the old
        `clear-local-vram` marketplace plugin, which duplicated this same
        teardown outside the engine that owns it. Shares its implementation
        (`residency.clear_vram`) with the automation node's equivalent
        "backend_action" so both cover the same ground: the residency ledger
        AND a fallback sweep of the model-lifecycle cache for anything
        GPU-resident that never registered with the ledger.
        """
        self._require_admin(user)
        backend_config = self._require_local_backend(backend_id)

        before_gb = _cuda_allocated_gb()

        from src.platform.runtime.native.memory.residency import clear_vram
        result = clear_vram(backend_config.device, self.model_lifecycle)

        if self.model_lifecycle is not None:
            self.model_lifecycle.cleanup(aggressive=False)

        after_gb = _cuda_allocated_gb()
        freed_gb = max(0.0, before_gb - after_gb)

        self.logger.info(
            f"[CLEAR_VRAM] backend={backend_id} offloaded {result.offloaded_count}, "
            f"unloaded {result.unloaded_count}, failed {result.failed_count} "
            f"({result.swept_count} via lifecycle-cache sweep), "
            f"VRAM {before_gb:.2f}GB -> {after_gb:.2f}GB (freed {freed_gb:.2f}GB)"
        )

        return self.success_response(data={
            "message": f"Freed {freed_gb:.2f} GB (was {before_gb:.2f} GB, now {after_gb:.2f} GB)",
            "before_gb": round(before_gb, 2),
            "after_gb": round(after_gb, 2),
            "freed_gb": round(freed_gb, 2),
            "offloaded_count": result.offloaded_count,
            "unloaded_count": result.unloaded_count,
            "failed_count": result.failed_count,
            "swept_count": result.swept_count,
        })

    @guarded(None, _failed("clear_cache_failed", "Failed to clear cache"), admin="user")
    async def clear_backend_cache(self, backend_id: str, user: Optional[User] = None) -> APIResponse:
        """Drop every cached model/artifact from the native engine's RAM cache.

        This is the native engine's "Clear VRAM & Cache (RAM)" quick action:
        the heavier sibling of `clear_backend_vram`. `ModelLifecycle.invalidate()`
        evicts every cache entry (freeing both VRAM and the RAM it occupied)
        and runs `cleanup(aggressive=True)`, which trims the host allocator so
        the freed RAM is actually returned to the OS. Next generation reloads
        every model from disk.
        """
        self._require_admin(user)
        self._require_local_backend(backend_id)

        if self.model_lifecycle is None:
            return self.error_response(
                error="model_lifecycle_unavailable",
                message="Model lifecycle manager is not available",
                status_code=500,
            )

        before_gb = _cuda_allocated_gb()
        stats_before = self.model_lifecycle.stats()
        cleared_keys = stats_before.get("keys", [])

        self.model_lifecycle.invalidate()

        after_gb = _cuda_allocated_gb()
        freed_gb = max(0.0, before_gb - after_gb)

        self.logger.info(
            f"[CLEAR_CACHE] backend={backend_id} cache keys cleared={cleared_keys}, "
            f"VRAM {before_gb:.2f}GB -> {after_gb:.2f}GB (freed {freed_gb:.2f}GB)"
        )

        return self.success_response(data={
            "message": f"Freed {freed_gb:.2f} GB (was {before_gb:.2f} GB, now {after_gb:.2f} GB)",
            "before_gb": round(before_gb, 2),
            "after_gb": round(after_gb, 2),
            "freed_gb": round(freed_gb, 2),
            "cache_keys_cleared": cleared_keys,
        })

    @staticmethod
    def _engine_flags(backend_config) -> Dict[str, Any]:
        from src.platform.settings.runtime_flags import engine_flag_values

        return engine_flag_values(getattr(backend_config, "engine_flags", {}))

    async def get_engine_flags(self, backend_id: str, user: Optional[User] = None) -> APIResponse:
        self._require_admin(user)
        backend_config = self._require_native_backend(backend_id)
        return self.success_response(data={
            "engine_flags": self._engine_flags(backend_config),
            "is_local": bool(type(backend_config).is_local),
        })

    @guarded(None, _failed("backend_optimizations_failed", "Failed to get optimizations"), admin="user")
    async def get_backend_optimizations(self, backend_id: str, user: Optional[User] = None) -> APIResponse:
        """System probe + catalog status for a native backend's Optimizations panel."""
        from src.platform.runtime.native.kernels import get_backend_override
        from src.platform.runtime.native.optimizations import CATALOG, probe_system

        self._require_admin(user)
        backend_config = self._require_local_backend(backend_id)

        probe = probe_system()
        optimizations = [asdict(opt.status(probe)) for opt in CATALOG.values()]
        return self.success_response(data={
            "system": asdict(probe),
            "optimizations": optimizations,
            "pinned_backend": get_backend_override(),
            "engine_flags": self._engine_flags(backend_config),
        })

    @guarded(None, _failed("optimization_install_failed", "Failed to start install"), admin="user")
    async def install_backend_optimization(
        self, backend_id: str, opt_id: str, user: Optional[User] = None
    ) -> APIResponse:
        """Kick off a build for one catalog optimization."""
        from src.platform.runtime.native.optimizations import get_optimization, installer, probe_system

        self._require_admin(user)
        self._require_local_backend(backend_id)

        opt = get_optimization(opt_id)
        if not opt:
            return self.error_response(
                error="unknown_optimization",
                message=f"Unknown optimization '{opt_id}'",
                status_code=404,
            )

        probe = probe_system()
        requirements = opt.requirements(probe)
        unmet = [r for r in requirements if not r.met]
        if unmet:
            return self.error_response(
                error="requirements_not_met",
                message="Unmet requirements: " + "; ".join(f"{r.label}: {r.detail}" for r in unmet),
                status_code=400,
            )

        try:
            job = installer.start(opt, probe)
        except RuntimeError as e:
            return self.error_response(
                error="install_in_progress",
                message=str(e),
                status_code=409,
            )

        return self.success_response(data={"opt_id": opt_id, "status": job.status})

    @guarded(None, _failed("optimization_job_failed", "Failed to get job status"), admin="user")
    async def get_current_optimization_job(
        self, backend_id: str, offset: int = 0, user: Optional[User] = None
    ) -> APIResponse:
        """Poll the (at most one) running/finished install job, from a log offset."""
        from src.platform.runtime.native.optimizations import installer

        self._require_admin(user)
        self._require_local_backend(backend_id)

        job = installer.current_job
        if job is None:
            return self.success_response(data={"active": False})

        new_lines = [line for _, line in job.log[offset:]]
        return self.success_response(data={
            "active": job.status == "running",
            "status": job.status,
            "opt_id": job.opt_id,
            "log": new_lines,
            "next_offset": len(job.log),
            "result": job.result,
            "error": job.error,
        })

    @guarded(None, _failed("optimization_cancel_failed", "Failed to cancel install"), admin="user")
    async def cancel_current_optimization_job(self, backend_id: str, user: Optional[User] = None) -> APIResponse:
        """Cancel the currently running install job, if any."""
        from src.platform.runtime.native.optimizations import installer

        self._require_admin(user)
        self._require_local_backend(backend_id)

        cancelled = await installer.cancel()
        return self.success_response(data={"cancelled": cancelled})

    @guarded(None, _failed("set_attention_backend_failed", "Failed to set attention backend"), admin="user")
    async def set_attention_backend(
        self, backend_id: str, backend: str, user: Optional[User] = None
    ) -> APIResponse:
        """Pin (or clear, via 'auto') the live attention backend and persist the choice."""
        from src.platform.runtime.native.kernels import get_attention_backend, get_backend_override, known_backends

        self._require_admin(user)
        backend_config = self._require_local_backend(backend_id)

        requested = (backend or "").strip().lower()
        valid_names = known_backends() | {"auto"}
        if requested not in valid_names:
            return self.error_response(
                error="invalid_backend",
                message=f"'{backend}' is not one of {sorted(valid_names)}",
                status_code=400,
            )

        stored_value = "" if requested == "auto" else requested
        self._save_engine_flags(backend_config, {"native_attention_backend": stored_value})

        return self.success_response(data={
            "pinned_backend": get_backend_override(),
            "active_backend": get_attention_backend(),
        })

    @guarded(None, _failed("set_engine_flags_failed", "Failed to set engine flags"), admin="user")
    async def set_engine_flags(
        self, backend_id: str, flags: Dict[str, Any], user: Optional[User] = None,
    ) -> APIResponse:
        from src.platform.settings.runtime_flags import normalize_engine_flags

        self._require_admin(user)
        backend_config = self._require_native_backend(backend_id)
        try:
            changes = normalize_engine_flags(flags or {})
        except ValueError as exc:
            return self.error_response(
                error="invalid_engine_flag_value",
                message=str(exc),
                status_code=400,
            )
        resolved = self._save_engine_flags(backend_config, changes)
        return self.success_response(data={"engine_flags": resolved})

    @guarded(None, _failed("benchmark_failed", "Failed to run attention benchmark"), admin="user")
    async def benchmark_backend_optimizations(self, backend_id: str, user: Optional[User] = None) -> APIResponse:
        """Time every available attention backend on identical tensors, so a user
        can verify an install actually delivered a speedup (admin only).

        Note: this does not check for an in-progress generation. `InProcessBackend`
        only tracks that via a private `_active` set with no public accessor, and
        reaching into it would be exactly the kind of backend-internals coupling
        this controller otherwise avoids - skipped rather than invented. A running
        generation and a concurrent benchmark will contend for the same GPU.
        """
        from src.platform.runtime.native.optimizations import run_benchmark

        self._require_admin(user)
        self._require_local_backend(backend_id)

        try:
            result = await run_benchmark()
        except RuntimeError as e:
            message = str(e)
            if "already running" in message:
                return self.error_response(
                    error="benchmark_in_progress",
                    message=message,
                    status_code=409,
                )
            return self.error_response(
                error="cuda_unavailable",
                message=message,
                status_code=400,
            )

        return self.success_response(data=result)


def build_router(container: "AppContainer") -> APIRouter:
    controller = BackendController(
        container.settings,
        container.backend_registry,
        container.model_lifecycle,
        model_indexing_coordinator=container.model_index_manager.indexing,
    )
    router = APIRouter(prefix="/api/backends", tags=["Backends"])

    @router.get("", response_model=APIResponse, summary="Get All Backends")
    async def list_backends(current_user=Depends(get_current_active_user)):
        """Get all configured backends including local and cloud instances."""
        return await controller.list_backends()

    @router.get("/enabled", response_model=APIResponse, summary="Get Enabled Backends")
    async def get_enabled_backends(current_user=Depends(get_current_active_user)):
        """Get all enabled backends sorted by priority for load balancing."""
        return await controller.get_enabled_backends()

    @router.get("/default", response_model=APIResponse, summary="Get Default Backend For Engine")
    async def get_default_backend(engine: str, current_user=Depends(get_current_active_user)):
        """Get the default backend for an engine."""
        return await controller.get_default_backend(engine)

    @router.get("/health", response_model=APIResponse, summary="Get All Backends Health")
    async def list_backend_health(current_user=Depends(get_current_admin_user)):
        return await controller.list_backend_health()

    @router.get("/engines", response_model=APIResponse, summary="Get Registered Engines")
    async def get_engines(current_user=Depends(get_current_active_user)):
        """Get all registered engines (built-in + plugin-provided)."""
        return await controller.get_engines()

    @router.post("", response_model=APIResponse, summary="Create Backend")
    async def create_backend(backend_data: BackendCreateRequest, current_user=Depends(get_current_admin_user)):
        """Create a new backend instance for an engine (admin only)."""
        return await controller.create_backend(backend_data.model_dump())

    @router.get("/{backend_id}", response_model=APIResponse, summary="Get Backend Details")
    async def get_backend(backend_id: str, current_user=Depends(get_current_active_user)):
        """Get detailed configuration for a specific backend by ID."""
        return await controller.get_backend(backend_id)

    @router.put("/{backend_id}", response_model=APIResponse, summary="Update Backend")
    async def update_backend(backend_id: str, backend_data: BackendUpdateRequest, current_user=Depends(get_current_admin_user)):
        """Update an existing backend configuration with new settings (admin only)."""
        # Filter out None values for partial updates
        update_data = {k: v for k, v in backend_data.model_dump().items() if v is not None}
        return await controller.update_backend(backend_id, update_data)

    @router.delete("/{backend_id}", response_model=APIResponse, summary="Delete Backend")
    async def delete_backend(backend_id: str, current_user=Depends(get_current_admin_user)):
        """Delete a backend configuration (admin only)."""
        return await controller.delete_backend(backend_id)

    @router.post("/{backend_id}/test", response_model=APIResponse, summary="Test Backend Connection")
    async def test_backend(backend_id: str, current_user=Depends(get_current_admin_user)):
        """Test connectivity and health of a specific backend (admin only)."""
        return await controller.test_backend(backend_id)

    @router.post("/{backend_id}/index-models", response_model=APIResponse, summary="Index Backend Models")
    async def index_backend_models(backend_id: str, current_user=Depends(get_current_admin_user)):
        """Enumerate the models this backend can load and record their availability (admin only)."""
        return await controller.index_backend_models(backend_id)

    @router.get("/{backend_id}/stats", response_model=APIResponse, summary="Get Backend Model Stats")
    async def get_backend_stats(backend_id: str, current_user=Depends(get_current_active_user)):
        """Indexed model count, total disk size, and last-indexed time for one backend."""
        return await controller.get_backend_stats(backend_id)

    @router.post("/{backend_id}/set-default", response_model=APIResponse, summary="Set Default Backend")
    async def set_default_backend(backend_id: str, current_user=Depends(get_current_admin_user)):
        """Make this backend the default for its engine (admin only)."""
        return await controller.set_default_backend(backend_id)

    @router.get("/{backend_id}/health", response_model=APIResponse, summary="Get Backend Health")
    async def get_backend_health(backend_id: str, current_user=Depends(get_current_admin_user)):
        return await controller.get_backend_health(backend_id)

    @router.get("/{backend_id}/system-info", response_model=APIResponse, summary="Get Backend System Info")
    async def get_backend_system_info(backend_id: str, current_user=Depends(get_current_admin_user)):
        return await controller.get_backend_system_info(backend_id)

    @router.post("/{backend_id}/actions/clear-vram", response_model=APIResponse, summary="Clear Native Backend VRAM")
    async def clear_backend_vram(backend_id: str, current_user=Depends(get_current_active_user)):
        """Offload GPU-resident models to host RAM; the RAM cache stays warm (admin only)."""
        return await controller.clear_backend_vram(backend_id, current_user)

    @router.post(
        "/{backend_id}/actions/clear-cache", response_model=APIResponse,
        summary="Clear Native Backend VRAM & RAM Cache",
    )
    async def clear_backend_cache(backend_id: str, current_user=Depends(get_current_active_user)):
        """Drop every cached model/artifact from the native engine's RAM cache (admin only)."""
        return await controller.clear_backend_cache(backend_id, current_user)

    @router.get("/{backend_id}/optimizations", response_model=APIResponse, summary="Get Native Backend Optimizations")
    async def get_backend_optimizations(backend_id: str, current_user=Depends(get_current_active_user)):
        """System probe + acceleration-library catalog status for a native backend (admin only)."""
        return await controller.get_backend_optimizations(backend_id, current_user)

    @router.post(
        "/{backend_id}/optimizations/{opt_id}/install",
        response_model=APIResponse,
        summary="Install A Native Backend Optimization",
    )
    async def install_backend_optimization(
        backend_id: str, opt_id: str, current_user=Depends(get_current_active_user)
    ):
        """Start building/installing one catalog optimization (admin only)."""
        return await controller.install_backend_optimization(backend_id, opt_id, current_user)

    @router.get(
        "/{backend_id}/optimizations/jobs/current",
        response_model=APIResponse,
        summary="Get Current Optimization Install Job",
    )
    async def get_current_optimization_job(
        backend_id: str, offset: int = Query(0, ge=0), current_user=Depends(get_current_active_user)
    ):
        """Poll the (at most one) install job's log from a given offset (admin only)."""
        return await controller.get_current_optimization_job(backend_id, offset, current_user)

    @router.post(
        "/{backend_id}/optimizations/jobs/current/cancel",
        response_model=APIResponse,
        summary="Cancel Current Optimization Install Job",
    )
    async def cancel_current_optimization_job(backend_id: str, current_user=Depends(get_current_active_user)):
        """Cancel the currently running install job, if any (admin only)."""
        return await controller.cancel_current_optimization_job(backend_id, current_user)

    @router.put(
        "/{backend_id}/optimizations/attention-backend",
        response_model=APIResponse,
        summary="Pin Native Attention Backend",
    )
    async def set_attention_backend(
        backend_id: str, body: AttentionBackendRequest, current_user=Depends(get_current_active_user)
    ):
        """Pin (or clear, via 'auto') the live attention backend (admin only)."""
        return await controller.set_attention_backend(backend_id, body.backend, current_user)

    @router.put(
        "/{backend_id}/optimizations/engine-flags",
        response_model=APIResponse,
        summary="Set Native Engine Flags",
    )
    async def set_engine_flags(
        backend_id: str, body: EngineFlagsRequest, current_user=Depends(get_current_active_user)
    ):
        return await controller.set_engine_flags(backend_id, body.flags, user=current_user)

    @router.get("/{backend_id}/engine-flags", response_model=APIResponse, summary="Get Native Engine Settings")
    async def get_engine_flags(backend_id: str, current_user=Depends(get_current_active_user)):
        return await controller.get_engine_flags(backend_id, current_user)

    @router.post(
        "/{backend_id}/optimizations/benchmark",
        response_model=APIResponse,
        summary="Benchmark Native Attention Backends",
    )
    async def benchmark_backend_optimizations(backend_id: str, current_user=Depends(get_current_active_user)):
        """Time every available attention backend on identical tensors (admin only)."""
        return await controller.benchmark_backend_optimizations(backend_id, current_user)

    return router
