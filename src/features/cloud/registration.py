import re
from typing import Any

from src.features.cloud.contracts import CLOUD_ENGINE, CloudBackendConfig, CloudProvider
from src.platform.plugins.hooks import HookContext

_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_-]*$")


def driver_for(provider_class: type[CloudProvider]) -> str:
    return f"{CLOUD_ENGINE}.{provider_class.key}"


def register_cloud_provider(context: HookContext, provider_class: type[CloudProvider]) -> str:
    key = getattr(provider_class, "key", None)
    if not isinstance(key, str) or not _KEY_PATTERN.match(key):
        raise ValueError(f"{provider_class.__name__}.key must match {_KEY_PATTERN.pattern}")
    if not getattr(provider_class, "label", ""):
        raise ValueError(f"{provider_class.__name__}.label is required")
    config_class = getattr(provider_class, "config_class", None)
    if not (isinstance(config_class, type) and issubclass(config_class, CloudBackendConfig)):
        raise ValueError(f"{provider_class.__name__}.config_class must subclass CloudBackendConfig")

    driver = driver_for(provider_class)
    declared = config_class.model_fields["driver"].get_default(call_default_factory=True)
    if declared != driver:
        raise ValueError(f"{config_class.__name__} must default driver to {driver!r}, found {declared!r}")

    providers: dict[str, Any] = context.data.setdefault("cloud_providers", {})
    existing = providers.get(driver)
    if existing is not None and existing is not provider_class:
        raise ValueError(f"cloud driver {driver!r} is already registered by {existing.__name__}")
    providers[driver] = provider_class

    context.data.setdefault("config_types", {})[driver] = config_class
    return driver
