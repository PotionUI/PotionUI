from typing import ClassVar, List, Optional

from pydantic import Field, model_validator

from src.plugin_api.cloud import CloudBackendConfig

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"


class OpenRouterConfig(CloudBackendConfig):
    driver: str = Field(default="cloud.openrouter")
    api_key: str = Field(
        title="API key",
        description="Created at openrouter.ai under Keys. Stored encrypted and never shown again.",
        json_schema_extra={"secret": True},
    )
    base_url: str = Field(
        default=DEFAULT_BASE_URL,
        title="API address",
        description="Change only to reach OpenRouter through a proxy",
    )
    app_title: str = Field(
        default="PotionUI",
        title="App name",
        description="Shown to OpenRouter as the name of this app in its usage statistics",
    )
    app_url: str = Field(
        default="",
        title="App address",
        description="Shown to OpenRouter as the address of this app. Leave empty to send none.",
    )
    upstream_providers: str = Field(
        default="",
        title="Allowed upstream providers",
        description="Comma separated. When set, OpenRouter may only route requests to these providers. Leave empty to allow all.",
    )
    send_user_hash: bool = Field(
        default=False,
        title="Send an anonymous user id",
        description="Send an anonymous id per user so OpenRouter can tell users apart without knowing who they are. It cannot be traced back to a person without this server's secret key.",
    )

    @model_validator(mode="before")
    @classmethod
    def _require_api_key(cls, data):
        if isinstance(data, dict):
            key = data.get("api_key")
            if not isinstance(key, str) or not key.strip():
                raise ValueError("Add your OpenRouter API key.")
            data = {**data, "api_key": key.strip()}
        return data

    engine_label: ClassVar[Optional[str]] = "OpenRouter"

    def upstream_list(self) -> List[str]:
        return [name.strip() for name in self.upstream_providers.split(",") if name.strip()]
