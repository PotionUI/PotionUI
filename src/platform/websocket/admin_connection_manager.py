"""
Admin WebSocket Connection Manager for admin panel operations.

Handles WebSocket connections for admin operations like
model indexing, plugin installation, etc.
"""
from src.platform.websocket.base_connection_manager import BaseConnectionManager


class AdminConnectionManager(BaseConnectionManager):
    """Manages WebSocket connections for admin panel operations"""

    _CONNECTION_LABEL = "Admin client"


# Global instance
admin_connection_manager = AdminConnectionManager()
