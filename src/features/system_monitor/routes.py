"""
System Monitor Controller

Handles system monitoring endpoints for GPU, RAM, and CPU statistics.
Delegates business logic to SystemMonitorCoordinator in the core layer.
"""
import uuid
from typing import TYPE_CHECKING
from fastapi import APIRouter, WebSocket, Depends, Query

from src.platform.http.base_controller import BaseController, APIResponse
from src.platform.security.current_user import get_current_active_user, get_current_admin_user, authenticate_websocket_token
from src.features.system_monitor import SystemMonitorCoordinator
from src.features.system_monitor.backend_snapshots import collect_backend_snapshots
from src.features.system_monitor.access import monitor_visible_to, require_monitor_access
from src.platform.security.user import User
from src.platform.runtime.gpu_profile import detect_gpu_profile

if TYPE_CHECKING:
    from src.bootstrap.container import AppContainer


class SystemMonitorController(BaseController):
    """Controller for system monitoring operations."""

    def __init__(self, manager: SystemMonitorCoordinator):
        super().__init__()
        self.manager = manager

    async def get_system_stats(self, user: User) -> APIResponse:
        """Get current system statistics."""
        try:
            stats = await self.manager.collect_system_stats()
            return self.success_response(data=stats)
        except ValueError as e:
            return self.error_response(str(e))
        except Exception as e:
            self.logger.error(f"Error getting system stats: {e}")
            return self.error_response(f"Failed to get system stats: {str(e)}")

    async def set_monitoring_interval(self, interval: float, user: User) -> APIResponse:
        """Set the monitoring update interval."""
        try:
            self.manager.set_monitoring_interval(interval)
            return self.success_response(data={
                "monitoring_interval": self.manager.monitoring_interval,
                "message": f"Monitoring interval set to {interval} seconds"
            })
        except ValueError as e:
            return self.error_response(str(e))
        except Exception as e:
            self.logger.error(f"Error setting monitoring interval: {e}")
            return self.error_response(f"Failed to set monitoring interval: {str(e)}")

    async def handle_websocket(self, websocket: WebSocket, client_id: str, still_allowed=None) -> None:
        """Handle system monitoring WebSocket connection."""
        await self.manager.handle_websocket_connection(
            websocket=websocket,
            client_id=client_id,
            accept_callback=websocket.accept,
            receive_callback=websocket.receive_text,
            still_allowed=still_allowed
        )


def build_router(container: "AppContainer") -> APIRouter:
    controller = container.system_monitor_controller
    router = APIRouter(prefix="/api/system", tags=["System & Health"])

    async def monitor_user(current_user=Depends(get_current_active_user)):
        return require_monitor_access(container.plugin_repository, current_user)

    # Route handlers
    @router.get("/stats", response_model=APIResponse, summary="Get System Statistics")
    async def get_system_stats(current_user=Depends(monitor_user)):
        """Get current system statistics including GPU, RAM, and CPU usage."""
        return await controller.get_system_stats(current_user)

    @router.get("/gpu-profile", summary="Get GPU Profile")
    async def get_gpu_profile(current_user=Depends(monitor_user)):
        return detect_gpu_profile().to_dict()

    @router.get("/backends", response_model=APIResponse, summary="Get Per-Backend Hardware Snapshot")
    async def get_backend_snapshots(current_user=Depends(get_current_admin_user)):
        try:
            host_stats = await container.system_monitor_coordinator.collect_system_stats()
        except ValueError:
            host_stats = {}
        configs = container.backend_registry.backend_config_store.get_enabled_backends()
        snapshots = await collect_backend_snapshots(
            configs, container.backend_registry, container.generation_status_tracker, host_stats
        )
        return controller.success_response(data=snapshots)

    @router.get("/vram", summary="Get Total GPU Memory")
    async def get_total_vram(current_user=Depends(get_current_active_user)):
        profile = detect_gpu_profile()
        return {"vram_gb": profile.vram_gb if profile.has_gpu else None}

    @router.post("/monitoring/interval", response_model=APIResponse, summary="Set Monitoring Interval")
    async def set_system_monitoring_interval(interval: float, current_user=Depends(get_current_admin_user)):
        """Set the system monitoring update interval in seconds."""
        return await controller.set_monitoring_interval(interval, current_user)

    return router


def build_ws_router(container: "AppContainer") -> APIRouter:
    controller = container.system_monitor_controller
    ws_router = APIRouter(tags=["WebSocket"])

    # WebSocket endpoints for system monitoring
    @ws_router.websocket("/ws/system")
    async def websocket_system_endpoint(websocket: WebSocket, token: str = Query(None)):
        """WebSocket endpoint for real-time system monitoring and GPU statistics."""
        # Authenticate the user before accepting connection
        user, auth_error = authenticate_websocket_token(token)
        if user is None:
            await websocket.close(code=4001, reason=auth_error)
            return

        if not monitor_visible_to(container.plugin_repository, user):
            await websocket.close(code=4003, reason="System monitor is restricted to administrators")
            return

        client_id = str(uuid.uuid4())
        await controller.handle_websocket(
            websocket,
            client_id,
            still_allowed=lambda: monitor_visible_to(container.plugin_repository, user),
        )

    return ws_router

