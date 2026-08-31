"""What a worker's model depot already has, per entry of a requested bundle.

The request side reuses ``ModelBundleManifestV1`` itself (already an envelope
kind) rather than inventing a second document for the same content - a
worker answering an inventory query is just reporting the depot status of a
manifest it was handed, the same manifest an execution package's
``model_bundle`` field carries.
"""

from __future__ import annotations

from typing import Literal

from src.platform.worker_protocol.common import Identifier, ProtocolModel

#: "missing" - no file at the entry's depot path.
#: "present" - a file is there and its digest is trusted (verified now or
#: previously staged through this worker).
#: "mismatched" - a file is there but its size or digest disagrees with the
#: manifest entry; the caller must re-stage it.
ModelEntryStatus = Literal["missing", "present", "mismatched"]


class ModelInventoryEntryV1(ProtocolModel):
    """One bundle entry's state on this worker's depot."""

    logical_id: Identifier
    status: ModelEntryStatus


class ModelInventoryResponseV1(ProtocolModel):
    """Answer to ``POST /v1/models/inventory``."""

    bundle_id: Identifier
    entries: tuple[ModelInventoryEntryV1, ...] = ()
