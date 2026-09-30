from typing import Any, Dict, Optional

from src.platform.filesystem.model_types import CLOUD_MODEL_TYPE

CLOUD_DRIVER_PREFIX = "cloud."


class CloudModelPresentation:
    def __init__(self, backend_registry: Any):
        self.backend_registry = backend_registry

    def fields(self, model: Any) -> Dict[str, Any]:
        if getattr(model, "model_type", None) != CLOUD_MODEL_TYPE:
            return {}
        info = next(
            (item for item in getattr(model, "providers", None) or [] if str(item.provider).startswith(CLOUD_DRIVER_PREFIX)),
            None,
        )
        driver = info.provider if info is not None else f"{CLOUD_DRIVER_PREFIX}{str(model.filename).split('~', 1)[0]}"
        vendor = info.tags[0] if info is not None and info.tags else None
        return {"provider_label": self._label(driver), "vendor": vendor}

    def apply(self, payload: Dict[str, Any], model: Any) -> Dict[str, Any]:
        payload.update(self.fields(model))
        return payload

    def _label(self, driver: str) -> Optional[str]:
        backend_class = self.backend_registry.get_registered_backend_types().get(driver)
        label = getattr(getattr(backend_class, "provider_class", None), "label", None)
        return label if isinstance(label, str) and label else None
