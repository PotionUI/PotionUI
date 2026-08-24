"""
Admin WebSocket Controller for handling admin panel real-time updates.

Handles WebSocket connections and message routing for admin operations.
"""
import json
import logging
import asyncio
from datetime import datetime
from typing import TYPE_CHECKING
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from uuid import uuid4

from src.platform.security.current_user import authenticate_websocket_token
from src.platform.security.user import AccountType
from src.platform.websocket.admin_connection_manager import admin_connection_manager

if TYPE_CHECKING:
    from src.bootstrap.container import AppContainer

logger = logging.getLogger(__name__)

router = APIRouter()


@router.websocket("/ws/admin")
async def admin_websocket_endpoint(
    websocket: WebSocket,
    client_id: str = Query(default=None),
    token: str = Query(default=None)
):
    """
    WebSocket endpoint for admin panel operations.

    This is the admin real-time channel, so the connection is authenticated
    (and restricted to administrators) before it is accepted - it must never
    be reachable unauthenticated.

    Message types supported:
    - ping: Heartbeat ping
    """
    user, auth_error = authenticate_websocket_token(token)
    if user is None or user.account_type != AccountType.ADMIN:
        await websocket.accept()
        await websocket.close(code=4001, reason=auth_error or "Authentication failed")
        return

    # Generate client ID if not provided
    if not client_id:
        client_id = str(uuid4())

    # Accept connection
    connected = await admin_connection_manager.connect(websocket, client_id)
    if not connected:
        return

    # Send connection established message
    try:
        await websocket.send_json({
            'type': 'connection_established',
            'client_id': client_id
        })
    except Exception as e:
        logger.error(f"Failed to send connection established message: {e}")
        admin_connection_manager.disconnect(client_id)
        return

    # Start heartbeat task
    heartbeat_task = asyncio.create_task(send_heartbeat(websocket, client_id))

    try:
        while True:
            # Receive and handle messages
            data = await websocket.receive_text()

            try:
                message = json.loads(data)
                await handle_message(client_id, message)
            except json.JSONDecodeError:
                logger.warning(f"Invalid JSON from client {client_id}: {data}")
                await websocket.send_json({
                    'type': 'error',
                    'message': 'Invalid JSON format'
                })

    except WebSocketDisconnect:
        logger.info(f"Admin client {client_id} disconnected")
    except Exception as e:
        logger.error(f"Admin WebSocket error for client {client_id}: {e}")
    finally:
        # Cancel heartbeat
        heartbeat_task.cancel()
        try:
            await heartbeat_task
        except asyncio.CancelledError:
            pass

        # Clean up connection
        admin_connection_manager.disconnect(client_id)


async def send_heartbeat(websocket: WebSocket, client_id: str):
    """Send periodic heartbeat to keep connection alive"""
    try:
        while True:
            await asyncio.sleep(30)  # Send heartbeat every 30 seconds
            if admin_connection_manager.is_client_connected(client_id):
                try:
                    await websocket.send_json({
                        'type': 'heartbeat',
                        'timestamp': datetime.now().isoformat()
                    })
                except Exception as e:
                    logger.error(f"Failed to send heartbeat to {client_id}: {e}")
                    break
            else:
                break
    except asyncio.CancelledError:
        pass


async def handle_message(client_id: str, message: dict):
    """Handle incoming WebSocket message"""
    message_type = message.get('type')

    if message_type == 'ping':
        # Respond to ping with pong
        await admin_connection_manager.send_to_client(client_id, {
            'type': 'pong',
            'timestamp': datetime.now().isoformat()
        })

    else:
        logger.warning(f"Unknown message type from {client_id}: {message_type}")


def build_router(container: "AppContainer") -> APIRouter:
    """The admin WebSocket endpoint has no controller; return the module router."""
    return router
