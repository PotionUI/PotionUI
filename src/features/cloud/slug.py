import re

_DISALLOWED = re.compile(r"[^a-z0-9._~-]")


def cloud_model_slug(provider_key: str, provider_model_id: str) -> str:
    body = provider_model_id.lower().replace("/", "~")
    return _DISALLOWED.sub("", f"{provider_key}~{body}")
