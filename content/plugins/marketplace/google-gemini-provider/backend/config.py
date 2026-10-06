from typing import ClassVar, Optional

from pydantic import Field, model_validator

from src.plugin_api.cloud import CloudBackendConfig

DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"


class GoogleConfig(CloudBackendConfig):
    driver: str = Field(default="cloud.google")
    api_key: str = Field(
        title="API key",
        description="Created in Google AI Studio (aistudio.google.com) under Get API key. Stored encrypted and never shown again.",
        json_schema_extra={"secret": True},
    )
    base_url: str = Field(
        default=DEFAULT_BASE_URL,
        title="API address",
        description="Change only to reach the Gemini API through a proxy",
    )

    @model_validator(mode="before")
    @classmethod
    def _require_api_key(cls, data):
        if isinstance(data, dict):
            key = data.get("api_key")
            if not isinstance(key, str) or not key.strip():
                raise ValueError("Add your Google AI Studio API key.")
            data = {**data, "api_key": key.strip()}
        return data

    engine_label: ClassVar[Optional[str]] = "Google Gemini"
