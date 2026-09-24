import json
import logging
from typing import Dict, List, Optional, Set
from fastapi import WebSocket

class ConnectionHub:
    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}
        self.generation_connections: Dict[str, List[str]] = {}  # generation_id -> [client_ids]
        self.privileged_clients: Set[str] = set()

    async def connect(self, websocket: WebSocket, client_id: str):
        try:
            logging.info(f"Attempting to accept WebSocket connection for client {client_id}")
            await websocket.accept()
            logging.info(f"WebSocket connection accepted for client {client_id}")
            self.active_connections[client_id] = websocket
            logging.info(f"Client {client_id} added to active connections. Total connections: {len(self.active_connections)}")
            return True
        except Exception as e:
            logging.error(f"Failed to accept WebSocket connection for client {client_id}: {str(e)}")
            import traceback
            traceback.print_exc()
            return False

    def disconnect(self, client_id: str):
        if client_id in self.active_connections:
            del self.active_connections[client_id]
        self.privileged_clients.discard(client_id)

        # Remove from generation connections
        for clients in self.generation_connections.values():
            if client_id in clients:
                clients.remove(client_id)

    async def subscribe_to_generation(self, client_id: str, generation_id: str, privileged: bool = False):
        # Verify client is in active connections
        if client_id not in self.active_connections:
            return False

        if privileged:
            self.privileged_clients.add(client_id)
        else:
            self.privileged_clients.discard(client_id)

        if generation_id not in self.generation_connections:
            self.generation_connections[generation_id] = []
        if client_id not in self.generation_connections[generation_id]:
            self.generation_connections[generation_id].append(client_id)
        return True

    async def broadcast_to_generation(self, generation_id: str, message: dict, privileged_message: Optional[dict] = None):
        if generation_id not in self.generation_connections:
            return

        client_ids = self.generation_connections[generation_id].copy()

        privileged_json = None
        if privileged_message is not None:
            try:
                privileged_json = json.dumps(privileged_message)
            except (TypeError, ValueError):
                privileged_json = None

        # First, try to serialize the message to JSON
        try:
            json_message = json.dumps(message)
        except TypeError as e:
            # Try to create a simplified version of the message
            try:
                # Create a simplified version of the message with problematic objects removed
                simplified_message = {
                    'type': message.get('type', 'unknown'),
                    'data': {
                        'error': f"Failed to serialize message: {str(e)}",
                        'original_type': message.get('type', 'unknown')
                    }
                }
                json_message = json.dumps(simplified_message)
            except Exception:
                return  # Cannot proceed if we can't create a valid JSON message
        except Exception:
            return  # Cannot proceed if we can't create a valid JSON message

        for client_id in client_ids:
            if client_id in self.active_connections:
                try:
                    payload = privileged_json if privileged_json is not None and client_id in self.privileged_clients else json_message
                    await self.active_connections[client_id].send_text(payload)
                except Exception:
                    # Connection is broken, remove it
                    self.disconnect(client_id)
