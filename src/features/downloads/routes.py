"""Download API.

HTTP endpoints for download management under /api/downloads and a WebSocket
endpoint at /ws/downloads for real-time progress updates. All HTTP endpoints
require ADMIN role; the WebSocket authenticates (and restricts to
administrators) before accepting.
"""

import asyncio
import json
import logging
from datetime import datetime
from typing import Optional, TYPE_CHECKING
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect

from src.platform.security.current_user import authenticate_websocket_token, get_current_admin_user
from src.platform.security.user import AccountType, User

from src.features.downloads.dto import (
    QueueModelDownloadRequest,
    QueueMediaDownloadRequest,
    QueueBatchDownloadRequest,
    QueueHfRepoDownloadRequest,
    UpdateSettingsRequest,
)
from src.features.downloads.exceptions import (
    DownloadNotFoundException,
    DownloadQueueException,
    DownloadOperationException,
    InvalidStatusException,
    InvalidTypeException,
)
from src.features.downloads.models import DownloadType

if TYPE_CHECKING:
    from src.bootstrap.container import AppContainer

logger = logging.getLogger(__name__)


def build_router(container: "AppContainer") -> APIRouter:
    download_manager = container.download_manager
    download_repository = container.download_repository

    router = APIRouter(prefix="/api/downloads", tags=["Downloads"])

    @router.get("", summary="List Downloads")
    async def list_downloads(
        status: Optional[str] = Query(None, description="Filter by status"),
        type: Optional[str] = Query(None, description="Filter by type (model/media/hf_repo)"),
        limit: int = Query(50, ge=0, le=200),
        offset: int = Query(0, ge=0),
        current_user: User = Depends(get_current_admin_user),
    ):
        """List all downloads with optional filtering. Admin only."""
        try:
            data = download_manager.list_downloads(
                status=status,
                download_type=type,
                limit=limit,
                offset=offset,
                user_id=current_user.id if current_user else None,
            )
            return {"success": True, "data": data}
        except InvalidStatusException as e:
            raise HTTPException(status_code=400, detail=str(e))
        except InvalidTypeException as e:
            raise HTTPException(status_code=400, detail=str(e))

    @router.get("/settings", summary="Get Download Settings")
    async def get_settings(current_user: User = Depends(get_current_admin_user)):
        """Get current download service settings. Admin only."""
        settings = download_manager.get_settings()
        return {"success": True, "data": settings.to_dict()}

    @router.put("/settings", summary="Update Download Settings")
    async def update_settings(
        request: UpdateSettingsRequest,
        current_user: User = Depends(get_current_admin_user),
    ):
        """Update download service settings. Admin only."""
        current = download_manager.get_settings()

        if request.max_concurrent_downloads is not None:
            current.max_concurrent_downloads = request.max_concurrent_downloads
        if request.auto_retry_failed is not None:
            current.auto_retry_failed = request.auto_retry_failed
        if request.max_retries is not None:
            current.max_retries = request.max_retries
        if request.chunk_size_kb is not None:
            current.chunk_size_kb = request.chunk_size_kb
        if request.verify_checksum is not None:
            current.verify_checksum = request.verify_checksum
        if request.default_model_directory is not None:
            current.default_model_directory = request.default_model_directory
        if request.default_media_directory is not None:
            current.default_media_directory = request.default_media_directory

        download_manager.update_settings(current)
        return {"success": True, "data": current.to_dict(), "message": "Settings updated"}

    @router.post("/model", summary="Queue Model Download")
    async def queue_model_download(
        request: QueueModelDownloadRequest,
        current_user: User = Depends(get_current_admin_user),
    ):
        """Queue a model file for download. Admin only."""
        try:
            download = await download_manager.queue_model_download(
                url=request.url,
                destination_dir=request.destination_dir,
                model_type=request.model_type,
                filename=request.filename,
                tags=request.tags,
                checksum_sha256=request.checksum_sha256,
                provider_id=request.provider_id,
                created_by=current_user.id if current_user else None,
            )
            return {"success": True, "data": download.to_dict(), "message": f"Download queued: {download.filename}"}
        except DownloadQueueException as e:
            raise HTTPException(status_code=400, detail=str(e))

    @router.post("/media", summary="Queue Media Download")
    async def queue_media_download(
        request: QueueMediaDownloadRequest,
        current_user: User = Depends(get_current_admin_user),
    ):
        """Queue a media file for download. Admin only."""
        try:
            download = await download_manager.queue_media_download(
                url=request.url,
                destination_dir=request.destination_dir,
                filename=request.filename,
                created_by=current_user.id if current_user else None,
            )
            return {"success": True, "data": download.to_dict(), "message": f"Download queued: {download.filename}"}
        except DownloadQueueException as e:
            raise HTTPException(status_code=400, detail=str(e))

    @router.post("/hf-repo", summary="Queue Hugging Face Repo Download")
    async def queue_hf_repo_download(
        request: QueueHfRepoDownloadRequest,
        current_user: User = Depends(get_current_admin_user),
    ):
        """Queue a whole Hugging Face repo as one grouped download. Admin only."""
        try:
            download = await download_manager.queue_hf_repo_download(
                repo_id=request.repo_id,
                destination_dir=request.destination_dir,
                revision=request.revision,
                allow_patterns=request.allow_patterns,
                created_by=current_user.id if current_user else None,
            )
            return {"success": True, "data": download.to_dict(), "message": f"Download queued: {download.filename}"}
        except DownloadQueueException as e:
            raise HTTPException(status_code=400, detail=str(e))

    @router.post("/batch", summary="Queue Batch Downloads")
    async def queue_batch_downloads(
        request: QueueBatchDownloadRequest,
        current_user: User = Depends(get_current_admin_user),
    ):
        """Queue multiple files for download. Admin only."""
        data = await download_manager.queue_batch_downloads(
            urls=request.urls,
            destination_dir=request.destination_dir,
            download_type=request.download_type,
            user_id=current_user.id if current_user else None,
        )
        return {
            "success": True,
            "data": data,
            "message": f"Queued {data['total_queued']} downloads, {data['total_errors']} errors",
        }

    @router.post("/clear-completed", summary="Clear Completed Downloads")
    async def clear_completed(current_user: User = Depends(get_current_admin_user)):
        """Clear all completed downloads from history. Admin only."""
        try:
            count = download_manager.clear_completed()
            return {"success": True, "message": f"Cleared {count} completed downloads"}
        except DownloadOperationException as e:
            raise HTTPException(status_code=400, detail=str(e))

    @router.post("/clear-cancelled", summary="Clear Cancelled Downloads")
    async def clear_cancelled(current_user: User = Depends(get_current_admin_user)):
        """Clear all cancelled downloads from history. Admin only."""
        try:
            count = download_manager.clear_cancelled()
            return {"success": True, "message": f"Cleared {count} cancelled downloads"}
        except DownloadOperationException as e:
            raise HTTPException(status_code=400, detail=str(e))

    @router.get("/{download_id}", summary="Get Download")
    async def get_download(
        download_id: str,
        current_user: User = Depends(get_current_admin_user),
    ):
        """Get a specific download by ID (with its per-file children for grouped jobs). Admin only."""
        try:
            download = download_manager.get_download(download_id)
            data = download.to_dict()
            if download.type == DownloadType.HF_REPO:
                data["children"] = [c.to_dict() for c in download_repository.get_children(download_id)]
            return {"success": True, "data": data}
        except DownloadNotFoundException as e:
            raise HTTPException(status_code=404, detail=str(e))

    @router.post("/{download_id}/pause", summary="Pause Download")
    async def pause_download(
        download_id: str,
        current_user: User = Depends(get_current_admin_user),
    ):
        """Pause an active download. Admin only."""
        try:
            download = await download_manager.pause_download(download_id)
            return {"success": True, "data": download.to_dict(), "message": "Download paused"}
        except DownloadNotFoundException as e:
            raise HTTPException(status_code=404, detail=str(e))
        except DownloadOperationException as e:
            raise HTTPException(status_code=400, detail=str(e))

    @router.post("/{download_id}/resume", summary="Resume Download")
    async def resume_download(
        download_id: str,
        current_user: User = Depends(get_current_admin_user),
    ):
        """Resume a paused download. Admin only."""
        try:
            download = await download_manager.resume_download(download_id)
            return {"success": True, "data": download.to_dict(), "message": "Download resumed"}
        except DownloadNotFoundException as e:
            raise HTTPException(status_code=404, detail=str(e))
        except DownloadOperationException as e:
            raise HTTPException(status_code=400, detail=str(e))

    @router.post("/{download_id}/cancel", summary="Cancel Download")
    async def cancel_download(
        download_id: str,
        current_user: User = Depends(get_current_admin_user),
    ):
        """Cancel an active or pending download. Admin only."""
        try:
            download = await download_manager.cancel_download(download_id)
            return {"success": True, "data": download.to_dict(), "message": "Download cancelled"}
        except DownloadNotFoundException as e:
            raise HTTPException(status_code=404, detail=str(e))
        except DownloadOperationException as e:
            raise HTTPException(status_code=400, detail=str(e))

    @router.post("/{download_id}/retry", summary="Retry Download")
    async def retry_download(
        download_id: str,
        current_user: User = Depends(get_current_admin_user),
    ):
        """Retry a failed download. Admin only."""
        try:
            download = await download_manager.retry_download(download_id)
            return {"success": True, "data": download.to_dict(), "message": "Download queued for retry"}
        except DownloadNotFoundException as e:
            raise HTTPException(status_code=404, detail=str(e))
        except DownloadOperationException as e:
            raise HTTPException(status_code=400, detail=str(e))

    @router.delete("/{download_id}", summary="Delete Download")
    async def delete_download(
        download_id: str,
        current_user: User = Depends(get_current_admin_user),
    ):
        """Delete a download record. Admin only."""
        try:
            await download_manager.delete_download(download_id)
            return {"success": True, "message": "Download deleted"}
        except DownloadNotFoundException as e:
            raise HTTPException(status_code=404, detail=str(e))
        except DownloadOperationException as e:
            raise HTTPException(status_code=400, detail=str(e))

    return router


def build_ws_router(container: "AppContainer") -> APIRouter:
    connection_manager = container.download_connection_manager

    ws_router = APIRouter(tags=["WebSocket"])

    @ws_router.websocket("/ws/downloads")
    async def downloads_websocket_endpoint(
        websocket: WebSocket,
        client_id: str = Query(default=None),
        token: str = Query(default=None),
    ):
        """
        WebSocket endpoint for real-time download progress and status updates.

        Downloads are admin-only state, so the connection is authenticated (and
        restricted to administrators) before it is accepted - the progress stream
        would otherwise leak download URLs, filenames and activity to any client.

        Supported message types (client -> server):
        - subscribe_download: Subscribe to a specific download's updates
        - unsubscribe_download: Unsubscribe from a specific download's updates
        - subscribe_all_downloads: Subscribe to all download updates
        - ping: Heartbeat ping
        """
        user, auth_error = authenticate_websocket_token(token)
        if user is None or user.account_type != AccountType.ADMIN:
            await websocket.accept()
            await websocket.close(code=4001, reason=auth_error or "Authentication failed")
            return

        if not client_id:
            client_id = str(uuid4())

        connected = await connection_manager.connect(websocket, client_id)
        if not connected:
            return

        # Send connection established message
        try:
            await websocket.send_json({
                "type": "connection_established",
                "client_id": client_id,
            })
        except Exception as e:
            logger.error("Failed to send connection_established to %s: %s", client_id, e)
            connection_manager.disconnect(client_id)
            return

        # Start heartbeat task
        heartbeat_task = asyncio.create_task(_send_heartbeat(connection_manager, websocket, client_id))

        try:
            while True:
                data = await websocket.receive_text()
                try:
                    message = json.loads(data)
                    await _handle_ws_message(connection_manager, client_id, message)
                except json.JSONDecodeError:
                    logger.warning("Invalid JSON from downloads client %s: %s", client_id, data)
                    await websocket.send_json({"type": "error", "message": "Invalid JSON format"})

        except WebSocketDisconnect:
            logger.info("Downloads client %s disconnected", client_id)
        except Exception as e:
            logger.error("Downloads WebSocket error for client %s: %s", client_id, e)
        finally:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass
            connection_manager.disconnect(client_id)

    return ws_router


async def _send_heartbeat(connection_manager, websocket: WebSocket, client_id: str) -> None:
    """Send periodic heartbeat messages to keep the connection alive.

    Args:
        connection_manager: The download connection manager
        websocket: The client WebSocket connection
        client_id: The client's unique identifier
    """
    try:
        while True:
            await asyncio.sleep(30)
            if connection_manager.is_client_connected(client_id):
                try:
                    await websocket.send_json({
                        "type": "heartbeat",
                        "timestamp": datetime.now().isoformat(),
                    })
                except Exception as e:
                    logger.error("Failed to send heartbeat to %s: %s", client_id, e)
                    break
            else:
                break
    except asyncio.CancelledError:
        pass


async def _handle_ws_message(connection_manager, client_id: str, message: dict) -> None:
    """Route an incoming WebSocket message to the appropriate handler.

    Args:
        connection_manager: The download connection manager
        client_id: The sending client's identifier
        message: Parsed message dict
    """
    message_type = message.get("type")

    if message_type == "subscribe_download":
        download_id = message.get("download_id")
        if download_id:
            success = await connection_manager.subscribe_to_download(client_id, download_id)
            await connection_manager.send_to_client(client_id, {
                "type": "subscribed" if success else "subscription_error",
                "download_id": download_id,
                "message": "Subscribed to download" if success else "Failed to subscribe",
            })

    elif message_type == "unsubscribe_download":
        download_id = message.get("download_id")
        if download_id:
            await connection_manager.unsubscribe_from_download(client_id, download_id)
            await connection_manager.send_to_client(client_id, {
                "type": "unsubscribed",
                "download_id": download_id,
            })

    elif message_type == "subscribe_all_downloads":
        success = connection_manager.subscribe_to_all_downloads(client_id)
        await connection_manager.send_to_client(client_id, {
            "type": "subscribed_all",
            "success": success,
            "message": "Subscribed to all download updates" if success else "Failed to subscribe",
        })

    elif message_type == "ping":
        await connection_manager.send_to_client(client_id, {
            "type": "pong",
            "timestamp": datetime.now().isoformat(),
        })

    else:
        logger.warning("Unknown message type from downloads client %s: %s", client_id, message_type)
