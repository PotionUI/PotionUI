from typing import Any, Dict

from fastapi import FastAPI

SESSION_SCHEME = "OAuth2PasswordBearer"
API_KEY_SCHEME = "ApiKey"

API_KEY_DESCRIPTION = (
    "An API key from Settings, API keys. It starts with pui_ and is sent as "
    "Authorization: Bearer pui_YOUR_KEY. Paste the key as it is; the page adds the Bearer prefix."
)


def add_api_key_scheme(schema: Dict[str, Any]) -> Dict[str, Any]:
    schemes = schema.setdefault("components", {}).setdefault("securitySchemes", {})
    if API_KEY_SCHEME in schemes:
        return schema
    schemes[API_KEY_SCHEME] = {
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "API key",
        "description": API_KEY_DESCRIPTION,
    }
    for path_item in schema.get("paths", {}).values():
        for operation in path_item.values():
            if not isinstance(operation, dict):
                continue
            security = operation.get("security")
            if security and {SESSION_SCHEME: []} in security and {API_KEY_SCHEME: []} not in security:
                security.append({API_KEY_SCHEME: []})
    return schema


def install_api_key_scheme(app: FastAPI) -> None:
    generate = app.openapi

    def openapi() -> Dict[str, Any]:
        return add_api_key_scheme(generate())

    app.openapi = openapi
