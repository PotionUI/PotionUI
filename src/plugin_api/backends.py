"""Contributing an engine.

An *engine* is the protocol a pipeline speaks; a *backend* is one configured
instance of it. A plugin contributes an engine by registering it through the
`backend.register` hook and subclassing `InProcessBackend` to execute a
pipeline against it.

`BaseBackendConfig` declares the connection settings an instance needs - the
admin UI renders its fields, so what you declare is what the admin can set.

If the engine can enumerate the models it holds, return `BackendModel` entries
and run them through `deduplicate`; raise `ModelListingNotSupported` if it
cannot, which is a fact about the engine, not a failure.

See docs/backends.md.
"""

from src.features.backends.backend_config import (
    BaseBackendConfig,
    BackendHealth,
    BackendStatus,
)
from src.features.backends.in_process_backend import InProcessBackend
from src.features.backends.model_listing import (
    BackendModel,
    ModelListingNotSupported,
    deduplicate,
)

__all__ = [
    "BackendHealth",
    "BackendModel",
    "BackendStatus",
    "BaseBackendConfig",
    "InProcessBackend",
    "ModelListingNotSupported",
    "deduplicate",
]
