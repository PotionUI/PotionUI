"""
Notification Controller

Handles per-user notification REST endpoints and the `/ws/notifications`
real-time channel. Thin route handlers delegate to NotificationController;
business logic lives in `src.features.notifications.operations`.
"""
import logging
import uuid
from typing import Optional, TYPE_CHECKING
from fastapi import APIRouter, Query, Depends, WebSocket, WebSocketDisconnect

from src.platform.http.base_controller import BaseController, APIResponse
from src.platform.security.current_user import get_current_active_user
from src.features.notifications.dto import CreateNotificationRequest, UpdateNotificationPreferencesRequest
from src.features.notifications import NotificationCollaborators
from src.features.notifications import operations
from src.platform.security.user import User
from src.platform.websocket.notification_connection_manager import notification_connection_manager

if TYPE_CHECKING:
    from src.bootstrap.container import AppContainer


class NotificationController(BaseController):
    """
    Controller for notification operations.

    All endpoints are scoped to the current authenticated user - a
    broadcast notification (user_id=None at creation) is fanned out to a
    per-user row at write time, so reads/mutations here never need to
    reason about broadcasts.
    """

    def __init__(self, collaborators: NotificationCollaborators):
        super().__init__()
        self.collaborators = collaborators
        self.repository = collaborators.repository

    async def list_notifications(
        self,
        user: User,
        limit: int = 50,
        before: Optional[str] = None,
        unread_only: bool = False
    ) -> APIResponse:
        """List the current user's notifications with unread count."""
        try:
            notifications = self.repository.list_for_user(
                user.id, limit=limit, before_id=before, unread_only=unread_only
            )
            unread_count = self.repository.unread_count(user.id)
            return self.success_response(data={
                "notifications": [n.model_dump(mode="json") for n in notifications],
                "unread_count": unread_count
            })
        except Exception as e:
            self.logger.exception(f"Error listing notifications: {e}")
            return self.error_api_response(error="list_notifications_failed", message="Failed to list notifications")

    async def create_notification(self, request: CreateNotificationRequest, user: User) -> APIResponse:
        """Create a notification on behalf of the current user (frontend-originated)."""
        try:
            notifications = operations.notify(
                self.collaborators,
                level=request.level,
                title=request.title,
                message=request.message,
                category=request.category,
                user_id=user.id,
                source="frontend",
                transient=request.transient,
                show_toast=request.show_toast,
                metadata=request.metadata,
                type=request.type,
            )
            return self.success_response(data={
                "notifications": [n.model_dump(mode="json") for n in notifications]
            })
        except ValueError as e:
            return self.error_api_response(error="create_notification_failed", message=str(e))
        except Exception as e:
            self.logger.error(f"Error creating notification: {e}")
            return self.error_api_response(error="create_notification_failed", message=str(e))

    async def mark_all_read(self, user: User) -> APIResponse:
        """Mark all of the current user's notifications as read."""
        try:
            updated = operations.mark_all_read(self.collaborators, user.id)
            return self.success_response(data={"updated": updated})
        except Exception as e:
            self.logger.error(f"Error marking all notifications read: {e}")
            return self.error_api_response(error="mark_all_read_failed", message=str(e))

    async def mark_read(self, notification_id: str, user: User) -> APIResponse:
        """Mark a single notification as read."""
        try:
            success = operations.mark_read(self.collaborators, notification_id, user.id)
            if not success:
                return self.error_api_response(
                    error="notification_not_found", message="Notification not found"
                )
            return self.success_response(data={"id": notification_id})
        except Exception as e:
            self.logger.error(f"Error marking notification read: {e}")
            return self.error_api_response(error="mark_read_failed", message=str(e))

    async def delete_notification(self, notification_id: str, user: User) -> APIResponse:
        """Delete a single notification."""
        try:
            success = operations.delete(self.collaborators, notification_id, user.id)
            if not success:
                return self.error_api_response(
                    error="notification_not_found", message="Notification not found"
                )
            return self.success_response(data={"id": notification_id})
        except Exception as e:
            self.logger.error(f"Error deleting notification: {e}")
            return self.error_api_response(error="delete_notification_failed", message=str(e))

    async def clear_notifications(self, user: User) -> APIResponse:
        """Delete all of the current user's notifications."""
        try:
            deleted = operations.clear(self.collaborators, user.id)
            return self.success_response(data={"deleted": deleted})
        except Exception as e:
            self.logger.error(f"Error clearing notifications: {e}")
            return self.error_api_response(error="clear_notifications_failed", message=str(e))

    async def get_notification_types(self, user: User) -> APIResponse:
        """List all registered notification types with the user-effective enabled state, plus the sound toggle."""
        try:
            preferences = operations.get_preferences(self.collaborators, user.id)
            return self.success_response(data=preferences)
        except Exception as e:
            self.logger.error(f"Error getting notification types: {e}")
            return self.error_api_response(error="get_notification_types_failed", message=str(e))

    async def update_preferences(self, request: UpdateNotificationPreferencesRequest, user: User) -> APIResponse:
        """Partially update the current user's notification preferences (types and/or sound)."""
        try:
            preferences = operations.update_preferences(
                self.collaborators, user.id, types=request.types, sound=request.sound
            )
            return self.success_response(data=preferences)
        except ValueError as e:
            return self.error_api_response(error="update_preferences_failed", message=str(e))
        except Exception as e:
            self.logger.error(f"Error updating notification preferences: {e}")
            return self.error_api_response(error="update_preferences_failed", message=str(e))


def build_router(container: "AppContainer") -> APIRouter:
    controller = container.notification_controller

    router = APIRouter(prefix="/api/notifications", tags=["Notifications"])

    @router.get("/", response_model=APIResponse, summary="List Notifications")
    async def list_notifications(
        limit: int = Query(50, description="Max results"),
        before: Optional[str] = Query(None, description="Keyset cursor: id of the oldest already-seen notification"),
        unread_only: bool = Query(False, description="Only return unread notifications"),
        current_user: User = Depends(get_current_active_user)
    ) -> APIResponse:
        """List the current user's notifications."""
        return await controller.list_notifications(
            current_user, limit=limit, before=before, unread_only=unread_only
        )

    @router.post("/", response_model=APIResponse, summary="Create Notification")
    async def create_notification(
        request: CreateNotificationRequest,
        current_user: User = Depends(get_current_active_user)
    ) -> APIResponse:
        """Create a notification for the current user."""
        return await controller.create_notification(request, current_user)

    @router.post("/read-all", response_model=APIResponse, summary="Mark All Notifications Read")
    async def mark_all_read(
        current_user: User = Depends(get_current_active_user)
    ) -> APIResponse:
        """Mark all of the current user's notifications as read."""
        return await controller.mark_all_read(current_user)

    @router.get("/types", response_model=APIResponse, summary="List Notification Types + Preferences")
    async def get_notification_types(
        current_user: User = Depends(get_current_active_user)
    ) -> APIResponse:
        """List all registered notification types with the current user's effective preferences."""
        return await controller.get_notification_types(current_user)

    @router.put("/preferences", response_model=APIResponse, summary="Update Notification Preferences")
    async def update_preferences(
        request: UpdateNotificationPreferencesRequest,
        current_user: User = Depends(get_current_active_user)
    ) -> APIResponse:
        """Partially update the current user's notification preferences (types and/or sound)."""
        return await controller.update_preferences(request, current_user)

    @router.post("/{notification_id}/read", response_model=APIResponse, summary="Mark Notification Read")
    async def mark_read(
        notification_id: str,
        current_user: User = Depends(get_current_active_user)
    ) -> APIResponse:
        """Mark a single notification as read."""
        return await controller.mark_read(notification_id, current_user)

    @router.delete("/{notification_id}", response_model=APIResponse, summary="Delete Notification")
    async def delete_notification(
        notification_id: str,
        current_user: User = Depends(get_current_active_user)
    ) -> APIResponse:
        """Delete a single notification."""
        return await controller.delete_notification(notification_id, current_user)

    @router.delete("/", response_model=APIResponse, summary="Clear Notifications")
    async def clear_notifications(
        current_user: User = Depends(get_current_active_user)
    ) -> APIResponse:
        """Delete all of the current user's notifications."""
        return await controller.clear_notifications(current_user)

    return router


def build_ws_router(container: "AppContainer") -> APIRouter:
    controller = container.notification_controller

    ws_router = APIRouter(tags=["WebSocket"])

    @ws_router.websocket("/ws/notifications")
    async def websocket_notifications_endpoint(websocket: WebSocket, token: str = Query(None)):
        """WebSocket endpoint for real-time per-user notification push."""
        from src.platform.security.current_user import authenticate_websocket_token

        try:
            user, auth_error = authenticate_websocket_token(token)
        except Exception as e:
            logging.error(f"Notification WebSocket auth exception: {e}")
            try:
                await websocket.accept()
                await websocket.close(code=4001, reason="Authentication error")
            except Exception as close_error:
                logging.error(f"Failed to close notification WebSocket after auth error: {close_error}")
            return

        if user is None:
            logging.warning(f"Notification WebSocket auth failed: {auth_error}")
            try:
                await websocket.accept()
                await websocket.close(code=4001, reason=auth_error or "Authentication failed")
            except Exception as e:
                logging.error(f"Error closing notification WebSocket after auth failure: {e}")
            return

        client_id = str(uuid.uuid4())
        await notification_connection_manager.connect(websocket, user.id, client_id)

        try:
            unread_count = controller.repository.unread_count(user.id)
            await websocket.send_json({
                "type": "connection_established",
                "client_id": client_id,
                "unread_count": unread_count
            })

            while True:
                data = await websocket.receive_json()
                if data.get("type") == "ping":
                    await websocket.send_json({"type": "pong"})
        except WebSocketDisconnect:
            pass
        except Exception as e:
            logging.error(f"Notification WebSocket handler error for client {client_id}: {e}")
        finally:
            notification_connection_manager.disconnect(user.id, client_id)

    return ws_router
