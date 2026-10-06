import re
from typing import ClassVar, Optional

from pydantic import Field, field_validator, model_validator

from src.plugin_api.cloud import CloudBackendConfig

DEFAULT_BASE_URL = "https://api.openai.com/v1"
MODERATION_LEVELS = ("auto", "low")
_HEADER_ID = re.compile(r"^[A-Za-z0-9_-]*$")


class OpenAIConfig(CloudBackendConfig):
    driver: str = Field(default="cloud.openai")
    api_key: str = Field(
        title="API key",
        description="Created at platform.openai.com under API keys. Stored encrypted and never shown again.",
        json_schema_extra={"secret": True},
    )
    base_url: str = Field(
        default=DEFAULT_BASE_URL,
        title="API address",
        description="Change only to reach OpenAI through a proxy",
    )
    organization: str = Field(
        default="",
        title="Organization ID",
        description="Optional. Bills the requests to this organization (org-...). Leave empty to use the key's default organization.",
    )
    project: str = Field(
        default="",
        title="Project ID",
        description="Optional. Bills the requests to this project (proj_...). Leave empty to use the key's default project.",
    )
    moderation: str = Field(
        default="auto",
        title="Content filter",
        description="How strictly OpenAI filters prompts and pictures. auto is OpenAI's standard filter; low filters less.",
        json_schema_extra={"options": list(MODERATION_LEVELS)},
    )
    send_user_hash: bool = Field(
        default=False,
        title="Send an anonymous user id",
        description="Send an anonymous id per user so OpenAI can tell users apart when it reviews abuse, without knowing who they are. It cannot be traced back to a person without this server's secret key.",
    )

    engine_label: ClassVar[Optional[str]] = "OpenAI"

    @field_validator("organization", "project")
    @classmethod
    def _plain_id(cls, value: str) -> str:
        text = value.strip()
        if not _HEADER_ID.match(text):
            raise ValueError("Enter the id as OpenAI shows it, letters, digits, - and _ only, or leave it empty.")
        return text

    @field_validator("moderation")
    @classmethod
    def _known_level(cls, value: str) -> str:
        text = value.strip().lower()
        if text not in MODERATION_LEVELS:
            raise ValueError("Choose auto or low.")
        return text

    @model_validator(mode="before")
    @classmethod
    def _require_api_key(cls, data):
        if isinstance(data, dict):
            key = data.get("api_key")
            if not isinstance(key, str) or not key.strip():
                raise ValueError("Add your OpenAI API key.")
            data = {**data, "api_key": key.strip()}
        return data
