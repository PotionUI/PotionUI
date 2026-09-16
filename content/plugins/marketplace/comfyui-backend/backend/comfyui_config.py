"""Configuration model for the ComfyUI engine's backends."""

from typing import Any, ClassVar, Dict, Optional, Tuple
from pydantic import Field, field_validator

from src.plugin_api import BaseBackendConfig


def resolve_comfyui_endpoint(host: str, port: int, secure: bool) -> Tuple[str, str]:
    host = (host or "").strip()
    protocol = "https" if secure else "http"
    ws_protocol = "wss" if secure else "ws"

    if "://" in host:
        scheme, _, rest = host.partition("://")
        rest = rest.rstrip("/")
        ws_scheme = "wss" if scheme == "https" else "ws"
        return f"{scheme}://{rest}", f"{ws_scheme}://{rest}"

    if host.startswith("["):
        hostport = host if "]:" in host else f"{host}:{port}"
        return f"{protocol}://{hostport}", f"{ws_protocol}://{hostport}"

    colon_count = host.count(":")
    if colon_count == 1 and host.rsplit(":", 1)[1].isdigit():
        return f"{protocol}://{host}", f"{ws_protocol}://{host}"

    if colon_count >= 1:
        host = f"[{host}]"

    return f"{protocol}://{host}:{port}", f"{ws_protocol}://{host}:{port}"


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
        http_base, _ = resolve_comfyui_endpoint(self.host, self.port, self.secure)
        return http_base

    def get_ws_url(self) -> str:
        """Get the WebSocket URL for the ComfyUI server"""
        _, ws_base = resolve_comfyui_endpoint(self.host, self.port, self.secure)
        return f"{ws_base}/ws"

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
