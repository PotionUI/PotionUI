"""Configuration model for the ComfyUI engine's backends."""

from typing import Any, ClassVar, Dict, Optional
from pydantic import Field, field_validator

from src.plugin_api import BaseBackendConfig


class ComfyUIBackendConfig(BaseBackendConfig):
    """
    Configuration for a ComfyUI backend - one configured ComfyUI server.

    Inherits id/name/engine/enabled/priority/timeout_seconds from BaseBackendConfig.
    """

    engine: str = Field(default="comfyui")

    engine_label: ClassVar[Optional[str]] = "ComfyUI"

    # These descriptors drive the admin "create/edit backend" form. Core frontend
    # code knows nothing about them - see BaseBackendConfig.engine_fields().
    host: str = Field(
        default="127.0.0.1",
        title="Host",
        description="Hostname or IP of the ComfyUI server",
    )
    port: int = Field(
        default=8188,
        title="Port",
        description="Port the ComfyUI server listens on",
    )
    secure: bool = Field(
        default=False,
        title="Use HTTPS/WSS",
        description="Connect over TLS",
    )
    api_key: Optional[str] = Field(
        default=None,
        title="API Key",
        description="Optional. For authenticated instances",
        json_schema_extra={"secret": True},
    )
    client_id: Optional[str] = Field(
        default=None,
        title="Client ID",
        description="Optional. For multi-client scenarios",
    )

    @field_validator('host')
    @classmethod
    def host_not_empty(cls, v):
        if not v or not v.strip():
            raise ValueError('Host cannot be empty')
        return v.strip()

    @field_validator('port')
    @classmethod
    def port_valid(cls, v):
        if not 1 <= v <= 65535:
            raise ValueError('Port must be between 1 and 65535')
        return v

    def get_base_url(self) -> str:
        """Get the base HTTP URL for the ComfyUI server"""
        protocol = "https" if self.secure else "http"
        return f"{protocol}://{self.host}:{self.port}"

    def get_ws_url(self) -> str:
        """Get the WebSocket URL for the ComfyUI server"""
        protocol = "wss" if self.secure else "ws"
        return f"{protocol}://{self.host}:{self.port}/ws"

    def to_connection_config(self) -> Dict[str, Any]:
        """Return connection config dict for use by ComfyUIPipe"""
        return {
            "host": self.host,
            "port": self.port,
            "secure": self.secure,
            "client_id": self.client_id,
            "api_key": self.api_key,
            "timeout": self.timeout_seconds
        }
