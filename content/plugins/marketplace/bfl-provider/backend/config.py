from typing import ClassVar, Optional
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator

from src.plugin_api.cloud import CloudBackendConfig

REGIONS = {
    "global": "https://api.bfl.ai/v1",
    "eu": "https://api.eu.bfl.ai/v1",
    "us": "https://api.us.bfl.ai/v1",
}
DEFAULT_REGION = "global"


class BflConfig(CloudBackendConfig):
    driver: str = Field(default="cloud.bfl")
    api_key: str = Field(
        title="API key",
        description="Created at api.bfl.ai under API Keys. Stored encrypted and never shown again.",
        json_schema_extra={"secret": True},
    )
    region: str = Field(
        default=DEFAULT_REGION,
        title="Region",
        description="global lets BFL pick the nearest cluster and fail over. eu keeps every request in the EU, us in the US.",
        json_schema_extra={"options": list(REGIONS)},
    )
    base_url: str = Field(
        default="",
        title="API address",
        description="Leave empty to use the region above. Set it only to reach BFL through a proxy, for example https://proxy.example/v1.",
    )
    timeout_seconds: int = Field(
        default=600,
        ge=30,
        le=3600,
        title="Timeout (seconds)",
        description="Longest a single picture may take before PotionUI stops waiting. BFL keeps a finished picture for 10 minutes.",
    )
    send_user_hash: bool = Field(
        default=False,
        title="Send an anonymous user id",
        description="Send an anonymous id per user so BFL can tell users apart without knowing who they are. It cannot be traced back to a person without this server's secret key.",
    )

    engine_label: ClassVar[Optional[str]] = "Black Forest Labs"

    @field_validator("region")
    @classmethod
    def _known_region(cls, value: str) -> str:
        text = (value or "").strip().lower() or DEFAULT_REGION
        if text not in REGIONS:
            raise ValueError(f"Choose one of the regions: {', '.join(REGIONS)}.")
        return text

    @field_validator("base_url")
    @classmethod
    def _base_url_is_a_web_address(cls, value: str) -> str:
        text = (value or "").strip().rstrip("/")
        if not text:
            return ""
        parts = urlsplit(text)
        if not text.isascii() or not text.isprintable() or " " in text or parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError("Enter the API address as a web address such as https://proxy.example/v1, or leave it empty.")
        return text

    @model_validator(mode="before")
    @classmethod
    def _require_api_key(cls, data):
        if isinstance(data, dict):
            key = data.get("api_key")
            if not isinstance(key, str) or not key.strip():
                raise ValueError("Add your BFL API key.")
            data = {**data, "api_key": key.strip()}
        return data

    def api_address(self) -> str:
        return self.base_url or REGIONS[self.region]
