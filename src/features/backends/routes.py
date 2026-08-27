import uuid
from dataclasses import asdict
from typing import TYPE_CHECKING, Dict, Any, Optional
from fastapi import APIRouter, Depends, HTTPException, Query

from src.platform.http.base_controller import BaseController, APIResponse
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
from src.platform.security.user import AccountType, User

if TYPE_CHECKING:
    from src.bootstrap.container import AppContainer

# Setting key backing the attention-backend pin; seeded by migration 078.
_ATTENTION_BACKEND_SETTING_KEY = "native_attention_backend"

# Engine-flag setting keys, seeded by migration 105.
_ENGINE_FLAG_SETTING_KEYS = {
    "torch_compile": "native_torch_compile",
    "stream_prefetch": "native_stream_prefetch",
}

# What a client may send back in a secret field to mean "leave the stored value
# unchanged". A redacted read never emits the secret itself, so a well-behaved
# client sends nothing; this sentinel is accepted defensively too.
_MASKED_SECRET = "__stored__"


def _is_blank_secret(value: Any) -> bool:
    """A secret value that must NOT overwrite the stored one: absent, empty, or the mask."""
    if value is None or value == _MASKED_SECRET:
        return True
    return isinstance(value, str) and value.strip() == ""


def _redacted_backend_dump(backend) -> Dict[str, Any]:
    """model_dump() with every secret-marked field removed.

    Each secret `foo` is replaced by a boolean `has_foo` so the UI can show
    "configured / not configured" without ever receiving the credential.
    """
    data = backend.model_dump()
    for name in type(backend).secret_field_names():
        present = not _is_blank_secret(data.get(name))
        data.pop(name, None)
        data[f"has_{name}"] = present
    return data


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
    ):
        super().__init__()
        self.settings = settings
        self.backend_registry = backend_registry
        self.model_lifecycle = model_lifecycle
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

    def _serialize(self, backend, default_ids: Dict[str, str] = None) -> Dict[str, Any]:
        """Serialize a backend config, adding the persisted per-engine default flag."""
        if default_ids is None:
            default_ids = self.backend_config_store.get_default_backend_ids()
        data = _redacted_backend_dump(backend)
        data["is_default"] = default_ids.get(backend.engine) == backend.id
        data["quick_actions"] = backend.quick_actions()
        return data

    async def list_backends(self) -> APIResponse:
        """List all configured backends"""
        try:
            backends = self.backend_config_store.get_backends()
            default_ids = self.backend_config_store.get_default_backend_ids()
            backend_data = [self._serialize(b, default_ids) for b in backends]
            return self.success_response(data=backend_data)
        except HTTPException:
            raise
        except Exception as e:
            self.logger.error(f"Failed to list backends: {str(e)}")
            return self.error_response(
                error="backend_list_failed",
                message=f"Failed to list backends: {str(e)}"
            )

    async def get_enabled_backends(self) -> APIResponse:
        """Get all enabled backends sorted by priority"""
        try:
            backends = self.backend_config_store.get_enabled_backends()
            default_ids = self.backend_config_store.get_default_backend_ids()
            backend_data = [self._serialize(b, default_ids) for b in backends]
            return self.success_response(data=backend_data)
        except HTTPException:
            raise
        except Exception as e:
            self.logger.error(f"Failed to get enabled backends: {str(e)}")
            return self.error_response(
                error="enabled_backends_failed",
                message=f"Failed to get enabled backends: {str(e)}"
            )

    async def get_default_backend(self, engine: str) -> APIResponse:
        """Get the default backend for an engine"""
        try:
            backend = self.backend_config_store.get_default_backend(engine)
            if not backend:
                return self.error_response(
                    error="no_backend_for_engine",
                    message=f"No enabled backend provides engine '{engine}'",
                    status_code=404
                )
            return self.success_response(data=self._serialize(backend))
        except HTTPException:
            raise
        except Exception as e:
            self.logger.error(f"Failed to get default backend: {str(e)}")
            return self.error_response(
                error="default_backend_failed",
                message=f"Failed to get default backend: {str(e)}"
            )

    async def get_engines(self) -> APIResponse:
        """
        Describe all registered engines (built-in + plugin-provided).

        Each descriptor carries the engine's label, whether it is a singleton, and
        the configuration fields it needs - so the admin UI never hardcodes the
        settings of any particular engine.
        """
        try:
            return self.success_response(data=self.backend_registry.get_engine_descriptors())
        except HTTPException:
            raise
        except Exception as e:
            self.logger.error(f"Failed to get engines: {str(e)}")
            return self.error_response(
                error="engines_failed",
                message=f"Failed to get registered engines: {str(e)}"
            )

    async def set_default_backend(self, backend_id: str) -> APIResponse:
        """Make a backend the default for its engine"""
        try:
            self.backend_config_store.set_default_backend(backend_id)
            await self.backend_registry.refresh_backends()
            return self.success_response(message=f"Backend '{backend_id}' is now the default for its engine")
        except ValueError as e:
            return self.error_response(
                error="backend_not_found",
                message=str(e),
                status_code=404
            )
        except HTTPException:
            raise
        except Exception as e:
            self.logger.error(f"Failed to set default backend {backend_id}: {str(e)}")
            return self.error_response(
                error="set_default_failed",
                message=f"Failed to set default backend: {str(e)}"
            )

    async def get_backend(self, backend_id: str) -> APIResponse:
        """Get a specific backend by ID"""
        try:
            backend = self.backend_config_store.get_backend(backend_id)
            if not backend:
                return self.error_response(
                    error="backend_not_found",
                    message=f"Backend '{backend_id}' not found",
                    status_code=404
                )
            return self.success_response(data=self._serialize(backend))
        except HTTPException:
            raise
        except Exception as e:
            self.logger.error(f"Failed to get backend {backend_id}: {str(e)}")
            return self.error_response(
                error="backend_get_failed",
                message=f"Failed to get backend: {str(e)}"
            )

    async def create_backend(self, backend_data: Dict[str, Any]) -> APIResponse:
        """Create a new backend configuration"""
        try:
            # Auto-generate ID if not provided
            if 'id' not in backend_data or not backend_data['id']:
                backend_data['id'] = str(uuid.uuid4())

            # Execute before_create hook
            plugin_registry = get_global_plugin_registry()
            if plugin_registry and plugin_registry.hook_chain:
                context, _ = plugin_registry.hook_chain.execute(
                    BACKEND_HOOKS.before_create,
                    initial_data={"backend_data": backend_data}
                )
                backend_data = context.get("backend_data", backend_data)

            # Validate and parse backend configuration
            backend_config = self.backend_config_store.validate_backend_config(backend_data)

            # Add the backend
            self.backend_config_store.add_backend(backend_config)

            # Refresh the backend registry
            await self.backend_registry.refresh_backends()

            # Execute after_create hook
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
        except ValueError as e:
            return self.error_response(
                error="backend_validation_failed",
                message=str(e),
                status_code=400
            )
        except HTTPException:
            raise
        except Exception as e:
            self.logger.error(f"Failed to create backend: {str(e)}")
            return self.error_response(
                error="backend_create_failed",
                message=f"Failed to create backend: {str(e)}"
            )

    async def update_backend(self, backend_id: str, backend_data: Dict[str, Any]) -> APIResponse:
        """Update an existing backend configuration"""
        try:
            # Execute before_update hook
            plugin_registry = get_global_plugin_registry()
            if plugin_registry and plugin_registry.hook_chain:
                context, _ = plugin_registry.hook_chain.execute(
                    BACKEND_HOOKS.before_update,
                    initial_data={"backend_id": backend_id, "backend_data": backend_data}
                )
                backend_data = context.get("backend_data", backend_data)

            # A partial update carries only changed fields. Merge them onto the
            # existing config so validation sees a complete object. `id` and
            # `engine` are immutable - the engine decides which config class
            # validates this backend.
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

            # Redaction roundtrip: a read never returns secrets, so a client that
            # isn't changing a credential sends it absent/blank/masked. Restore the
            # stored value in that case, so an edit of other fields never wipes it.
            for name in type(existing).secret_field_names():
                if _is_blank_secret(backend_data.get(name)):
                    merged[name] = existing_dump.get(name)

            backend_config = self.backend_config_store.validate_backend_config(merged)

            # Update the backend
            self.backend_config_store.update_backend(backend_id, backend_config)

            # Refresh the backend registry
            await self.backend_registry.refresh_backends()

            # Execute after_update hook
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
        except ValueError as e:
            return self.error_response(
                error="backend_validation_failed",
                message=str(e),
                status_code=400
            )
        except HTTPException:
            raise
        except Exception as e:
            self.logger.error(f"Failed to update backend {backend_id}: {str(e)}")
            return self.error_response(
                error="backend_update_failed",
                message=f"Failed to update backend: {str(e)}"
            )

    async def delete_backend(self, backend_id: str) -> APIResponse:
        """Delete a backend configuration"""
        try:
            # Execute before_delete hook
            plugin_registry = get_global_plugin_registry()
            if plugin_registry and plugin_registry.hook_chain:
                plugin_registry.hook_chain.execute(
                    BACKEND_HOOKS.before_delete,
                    initial_data={"backend_id": backend_id}
                )

            self.backend_config_store.remove_backend(backend_id)

            # Refresh the backend registry
            await self.backend_registry.refresh_backends()

            # Execute after_delete hook
            if plugin_registry and plugin_registry.hook_chain:
                plugin_registry.hook_chain.execute(
                    BACKEND_HOOKS.after_delete,
                    initial_data={"backend_id": backend_id}
                )

            self.logger.info(f"Deleted backend: {backend_id}")
            return self.success_response(message=f"Backend '{backend_id}' deleted successfully")
        except ValueError as e:
            return self.error_response(
                error="backend_delete_failed",
                message=str(e),
                status_code=400
            )
        except HTTPException:
            raise
        except Exception as e:
            self.logger.error(f"Failed to delete backend {backend_id}: {str(e)}")
            return self.error_response(
                error="backend_delete_failed",
                message=f"Failed to delete backend: {str(e)}"
            )

    async def index_backend_models(self, backend_id: str) -> APIResponse:
        """Ask a backend what models it can load, and record the answer.

        Indexing lives on the backend, not on /models, because "which models exist" is a
        fact about a backend. Native answers with a filesystem scan; a ComfyUI server
        answers over HTTP. See docs/models.md.
        """
        from src.features.backends.model_listing import ModelListingNotSupported
        from src.features.models.backend_indexer import backend_model_indexer

        try:
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

            result = await backend_model_indexer.index_backend(backend)
            return self.success_response(
                data=result.to_dict(),
                message=(
                    f"Indexed {result.listed} models from '{backend.name}': "
                    f"{result.created} new, {result.matched} matched, {result.removed} removed"
                )
            )

        except HTTPException:
            raise
        except ModelListingNotSupported as e:
            return self.error_response(
                error="model_listing_not_supported",
                message=str(e),
                status_code=400
            )
        except Exception as e:
            # A backend that cannot be reached must not look like a backend with no
            # models: returning an empty listing would delete every availability row.
            return self.error_response(
                error="backend_index_failed",
                message=f"Failed to index models for backend '{backend_id}': {str(e)}"
            )

    async def get_backend_stats(self, backend_id: str) -> APIResponse:
        """How many models this backend has reported, and how much disk space they
        total - from the last `index_backend_models` run, not a live re-scan.

        Per-backend, not global: "files on disk" only means something for the
        specific backend that indexed them (a remote ComfyUI server's disk isn't
        this host's), so this reports what `model_availability` actually knows
        per backend rather than an instance-wide aggregate.
        """
        from src.features.models.availability_repository import model_availability_repo

        try:
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
        except HTTPException:
            raise
        except Exception as e:
            self.logger.error(f"Failed to get stats for backend {backend_id}: {str(e)}")
            return self.error_response(
                error="backend_stats_failed",
                message=f"Failed to get backend stats: {str(e)}"
            )

    async def test_backend(self, backend_id: str) -> APIResponse:
        """Test backend connection and health"""
        try:
            # Get the backend instance from registry (not just config)
            backend_instance = self.backend_registry.get_backend(backend_id)
            if not backend_instance:
                # Backend might not be instantiated yet, check config exists
                backend_config = self.backend_config_store.get_backend(backend_id)
                if not backend_config:
                    return self.error_response(
                        error="backend_not_found",
                        message=f"Backend '{backend_id}' not found",
                        status_code=404
                    )
                # Config exists but instance doesn't - backend might be disabled
                return self.error_response(
                    error="backend_not_active",
                    message=f"Backend '{backend_id}' is not active (may be disabled)"
                )

            # Perform health check
            health_info = await backend_instance.health_check()

            # Determine success/failure based on status
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
        except HTTPException:
            raise
        except Exception as e:
            self.logger.error(f"Failed to test backend {backend_id}: {str(e)}")
            return self.error_response(
                error="backend_test_failed",
                message=f"Failed to test backend: {str(e)}"
            )

    async def get_backend_health(self, backend_id: str) -> APIResponse:
        """Get health status for a specific backend"""
        try:
            backend_config = self.backend_config_store.get_backend(backend_id)
            if not backend_config:
                return self.error_response(
                    error="backend_not_found",
                    message=f"Backend '{backend_id}' not found",
                    status_code=404
                )

            # Try to get the backend instance and call its health_check
            backend_instance = self.backend_registry.get_backend(backend_id)
            if backend_instance:
                try:
                    health = await backend_instance.health_check()
                    if not isinstance(health, dict):
                        health = health.model_dump() if hasattr(health, 'model_dump') else {"status": str(health)}
                except Exception as e:
                    health = {
                        "status": "error",
                        "error": f"Health check failed: {str(e)}"
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
        except HTTPException:
            raise
        except Exception as e:
            self.logger.error(f"Failed to get backend health {backend_id}: {str(e)}")
            return self.error_response(
                error="backend_health_failed",
                message=f"Failed to get backend health: {str(e)}"
            )

    async def get_backend_system_info(self, backend_id: str) -> APIResponse:
        """Get system information for a specific backend"""
        try:
            backend_config = self.backend_config_store.get_backend(backend_id)
            if not backend_config:
                return self.error_response(
                    error="backend_not_found",
                    message=f"Backend '{backend_id}' not found",
                    status_code=404
                )

            # Every backend implements get_system_info(); it is abstract on BaseBackend.
            backend_instance = self.backend_registry.get_backend(backend_id)
            if backend_instance:
                try:
                    system_info = await backend_instance.get_system_info()
                except Exception as e:
                    system_info = {"error": f"Failed to get system info: {str(e)}"}
            else:
                system_info = {"message": "Backend is not active (may be disabled)"}

            return self.success_response(data=system_info)
        except HTTPException:
            raise
        except Exception as e:
            self.logger.error(f"Failed to get backend system info {backend_id}: {str(e)}")
            return self.error_response(
                error="backend_system_info_failed",
                message=f"Failed to get backend system info: {str(e)}"
            )

    async def list_backend_health(self) -> APIResponse:
        """Get health status for all backends"""
        try:
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
                            "error": f"Health check failed: {str(e)}"
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
        except HTTPException:
            raise
        except Exception as e:
            self.logger.error(f"Failed to get backend health list: {str(e)}")
            return self.error_response(
                error="backend_health_list_failed",
                message=f"Failed to get backend health list: {str(e)}"
            )


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

        Deliberately does NOT touch the ModelLifecycle RAM cache: the
        offloaded weights stay resident in host RAM, so the next generation
        re-uploads to the GPU instead of reloading from disk. For the heavier
        operation that also drops the RAM cache, see `clear_backend_cache`.
        """
        self._require_admin(user)
        try:
            backend_config = self._require_local_backend(backend_id)

            before_gb = _cuda_allocated_gb()

            from src.platform.runtime.native.memory.residency import clear_vram
            result = clear_vram(backend_config.device, self.model_lifecycle)

            if self.model_lifecycle is not None:
                # gc + cuda.empty_cache() only - no host allocator trim, and no
                # cache eviction, so the RAM cache stays warm.
                self.model_lifecycle.cleanup(aggressive=False)

            after_gb = _cuda_allocated_gb()
            freed_gb = max(0.0, before_gb - after_gb)

            self.logger.info(
                f"[CLEAR_VRAM] backend={backend_id} offloaded {result.offloaded_count} resident component(s) "
                f"({result.swept_count} via lifecycle-cache sweep), "
                f"VRAM {before_gb:.2f}GB -> {after_gb:.2f}GB (freed {freed_gb:.2f}GB)"
            )

            return self.success_response(data={
                "message": f"Freed {freed_gb:.2f} GB (was {before_gb:.2f} GB, now {after_gb:.2f} GB)",
                "before_gb": round(before_gb, 2),
                "after_gb": round(after_gb, 2),
                "freed_gb": round(freed_gb, 2),
                "offloaded_count": result.offloaded_count,
                "swept_count": result.swept_count,
            })
        except HTTPException:
            raise
        except Exception as e:
            self.logger.error(f"Failed to clear VRAM for backend {backend_id}: {str(e)}")
            return self.error_response(
                error="clear_vram_failed",
                message=f"Failed to clear VRAM: {str(e)}",
            )

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
        try:
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

            self.model_lifecycle.invalidate()  # evicts every cached model/artifact, trims RAM

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
        except HTTPException:
            raise
        except Exception as e:
            self.logger.error(f"Failed to clear cache for backend {backend_id}: {str(e)}")
            return self.error_response(
                error="clear_cache_failed",
                message=f"Failed to clear cache: {str(e)}",
            )

    @staticmethod
    def _engine_flags() -> Dict[str, bool]:
        """Effective on/off state of the admin-toggleable native engine flags."""
        from src.platform.runtime.native.memory import partial
        from src.platform.runtime.native.optimizations import compile as torch_compile

        return {
            "torch_compile": torch_compile.torch_compile_enabled(),
            "stream_prefetch": partial.stream_prefetch_enabled(),
        }

    async def get_backend_optimizations(self, backend_id: str, user: Optional[User] = None) -> APIResponse:
        """System probe + catalog status for a native backend's Optimizations panel."""
        from src.platform.runtime.native import attention
        from src.platform.runtime.native.optimizations import CATALOG, probe_system

        self._require_admin(user)
        try:
            self._require_local_backend(backend_id)

            probe = probe_system()
            optimizations = [asdict(opt.status(probe)) for opt in CATALOG.values()]
            return self.success_response(data={
                "system": asdict(probe),
                "optimizations": optimizations,
                "pinned_backend": attention.get_backend_override(),
                "engine_flags": self._engine_flags(),
            })
        except HTTPException:
            raise
        except Exception as e:
            self.logger.error(f"Failed to get optimizations for backend {backend_id}: {str(e)}")
            return self.error_response(
                error="backend_optimizations_failed",
                message=f"Failed to get optimizations: {str(e)}",
            )

    async def install_backend_optimization(
        self, backend_id: str, opt_id: str, user: Optional[User] = None
    ) -> APIResponse:
        """Kick off a build for one catalog optimization."""
        from src.platform.runtime.native.optimizations import get_optimization, installer, probe_system

        self._require_admin(user)
        try:
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
        except HTTPException:
            raise
        except Exception as e:
            self.logger.error(f"Failed to install optimization {opt_id} for backend {backend_id}: {str(e)}")
            return self.error_response(
                error="optimization_install_failed",
                message=f"Failed to start install: {str(e)}",
            )

    async def get_current_optimization_job(
        self, backend_id: str, offset: int = 0, user: Optional[User] = None
    ) -> APIResponse:
        """Poll the (at most one) running/finished install job, from a log offset."""
        from src.platform.runtime.native.optimizations import installer

        self._require_admin(user)
        try:
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
        except HTTPException:
            raise
        except Exception as e:
            self.logger.error(f"Failed to get optimization job for backend {backend_id}: {str(e)}")
            return self.error_response(
                error="optimization_job_failed",
                message=f"Failed to get job status: {str(e)}",
            )

    async def cancel_current_optimization_job(self, backend_id: str, user: Optional[User] = None) -> APIResponse:
        """Cancel the currently running install job, if any."""
        from src.platform.runtime.native.optimizations import installer

        self._require_admin(user)
        try:
            self._require_local_backend(backend_id)

            cancelled = await installer.cancel()
            return self.success_response(data={"cancelled": cancelled})
        except HTTPException:
            raise
        except Exception as e:
            self.logger.error(f"Failed to cancel optimization job for backend {backend_id}: {str(e)}")
            return self.error_response(
                error="optimization_cancel_failed",
                message=f"Failed to cancel install: {str(e)}",
            )

    async def set_attention_backend(
        self, backend_id: str, backend: str, user: Optional[User] = None
    ) -> APIResponse:
        """Pin (or clear, via 'auto') the live attention backend and persist the choice."""
        from src.platform.runtime.native import attention

        self._require_admin(user)
        try:
            self._require_local_backend(backend_id)

            requested = (backend or "").strip().lower()
            # attention.known_backends() is the dispatcher's own source of truth
            # (BACKEND_PRIORITY union PIN_ONLY_BACKENDS, e.g. "sparge") -- validate
            # against that instead of duplicating a name list here, or a pin-only
            # backend addition in attention.py silently stops being pinnable
            # through this endpoint (see set_backend_override / get_attention_backend).
            valid_names = attention.known_backends() | {"auto"}
            if requested not in valid_names:
                return self.error_response(
                    error="invalid_backend",
                    message=f"'{backend}' is not one of {sorted(valid_names)}",
                    status_code=400,
                )

            stored_value = "" if requested == "auto" else requested
            self.settings.set_setting(_ATTENTION_BACKEND_SETTING_KEY, stored_value)
            attention.set_backend_override(stored_value)

            return self.success_response(data={
                "pinned_backend": attention.get_backend_override(),
                "active_backend": attention.get_attention_backend(),
            })
        except HTTPException:
            raise
        except Exception as e:
            self.logger.error(f"Failed to set attention backend for backend {backend_id}: {str(e)}")
            return self.error_response(
                error="set_attention_backend_failed",
                message=f"Failed to set attention backend: {str(e)}",
            )

    async def set_engine_flags(
        self,
        backend_id: str,
        torch_compile: Optional[str] = None,
        stream_prefetch: Optional[str] = None,
        user: Optional[User] = None,
    ) -> APIResponse:
        """Toggle native engine flags: persist each provided value and apply it live.

        Same shape as the attention-backend pin: the setting is the durable
        record, the module-level override is what the hot path reads, and both
        are written here so the change takes effect without a restart. An
        untouched setting stays empty and the env var keeps deciding.
        """
        from src.platform.runtime.native.memory import partial
        from src.platform.runtime.native.optimizations import compile as torch_compile_mod

        self._require_admin(user)
        try:
            self._require_local_backend(backend_id)

            setters = {
                "torch_compile": torch_compile_mod.set_torch_compile_override,
                "stream_prefetch": partial.set_stream_prefetch_override,
            }
            requested = {"torch_compile": torch_compile, "stream_prefetch": stream_prefetch}
            for flag, value in requested.items():
                if value is None:
                    continue
                normalized = value.strip().lower()
                if normalized not in ("on", "off"):
                    return self.error_response(
                        error="invalid_engine_flag_value",
                        message=f"{flag} must be 'on' or 'off', got '{value}'",
                        status_code=400,
                    )
                self.settings.set_setting(_ENGINE_FLAG_SETTING_KEYS[flag], normalized)
                setters[flag](normalized)

            return self.success_response(data={"engine_flags": self._engine_flags()})
        except HTTPException:
            raise
        except Exception as e:
            self.logger.error(f"Failed to set engine flags for backend {backend_id}: {str(e)}")
            return self.error_response(
                error="set_engine_flags_failed",
                message=f"Failed to set engine flags: {str(e)}",
            )

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
        try:
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
        except HTTPException:
            raise
        except Exception as e:
            self.logger.error(f"Failed to benchmark attention backends for backend {backend_id}: {str(e)}")
            return self.error_response(
                error="benchmark_failed",
                message=f"Failed to run attention benchmark: {str(e)}",
            )


def build_router(container: "AppContainer") -> APIRouter:
    controller = BackendController(
        container.settings,
        container.backend_registry,
        container.model_lifecycle,
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
    async def list_backend_health(current_user=Depends(get_current_active_user)):
        """Get health status for all configured backends."""
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
    async def get_backend_health(backend_id: str, current_user=Depends(get_current_active_user)):
        """Get health status for a specific backend."""
        return await controller.get_backend_health(backend_id)

    @router.get("/{backend_id}/system-info", response_model=APIResponse, summary="Get Backend System Info")
    async def get_backend_system_info(backend_id: str, current_user=Depends(get_current_active_user)):
        """Get system information for a specific backend."""
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
        """Toggle torch.compile / stream prefetch on the native engine, applied live (admin only)."""
        return await controller.set_engine_flags(
            backend_id,
            torch_compile=body.torch_compile,
            stream_prefetch=body.stream_prefetch,
            user=current_user,
        )

    @router.post(
        "/{backend_id}/optimizations/benchmark",
        response_model=APIResponse,
        summary="Benchmark Native Attention Backends",
    )
    async def benchmark_backend_optimizations(backend_id: str, current_user=Depends(get_current_active_user)):
        """Time every available attention backend on identical tensors (admin only)."""
        return await controller.benchmark_backend_optimizations(backend_id, current_user)

    return router
