"""The job a worker receives.

**The ``processed_pipes`` seam.** ``ProcessedPipelineV1`` fixes only the frame:
an ordered list of pipe instances, each with an id, a registry type, an enabled
flag, and two strict-JSON maps for its resolved configuration and its input
wiring. The *contents* of those two maps - what a resolved config key looks
like, and how an input references another pipe's output rather than a literal -
are deliberately not modelled here, because that shape is derived from the
existing pipeline builder and has not been verified by this contract. Anything
placed in them is validated as JSON and nothing more.

Two consequences follow, and both are intentional. Non-JSON values (a tensor, a
PIL image, an open file handle) fail validation loudly at the boundary instead
of failing obscurely at ``json.dumps`` time on the transport. And the shape
carries its own ``shape_version``, separate from the package's ``version``, so
the pipeline representation can go to v2 - which is the change most likely to
happen first - without dragging every other field of the package with it. When
that happens, ``ExecutionPackageV1.processed_pipes`` becomes a union
discriminated on ``shape_version``; that is the only edit required here.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, Optional

from pydantic import Field, JsonValue, model_validator

from src.platform.worker_protocol.common import (
    ContentDigest,
    Identifier,
    NonEmptyText,
    ProtocolModel,
)
from src.platform.worker_protocol.input_asset import InputAssetManifestV1
from src.platform.worker_protocol.model_bundle import ModelBundleManifestV1


class ProcessedPipeV1(ProtocolModel):
    """One fully resolved pipe instance. See this module's docstring."""

    pipe_id: Identifier
    pipe_type: Identifier
    enabled: bool = True
    config: dict[str, JsonValue] = {}
    inputs: dict[str, JsonValue] = {}


class ProcessedPipelineV1(ProtocolModel):
    """The executable pipeline, in execution order."""

    shape_version: Literal[1] = 1
    pipes: tuple[ProcessedPipeV1, ...] = ()

    @model_validator(mode="after")
    def _unique_pipe_ids(self) -> "ProcessedPipelineV1":
        seen: set[str] = set()
        for pipe in self.pipes:
            if pipe.pipe_id in seen:
                raise ValueError(f"duplicate pipe_id {pipe.pipe_id!r}")
            seen.add(pipe.pipe_id)
        return self


class ExecutionLimitsV1(ProtocolModel):
    """Bounds a worker enforces so a runaway package cannot bill forever."""

    max_wall_seconds: Optional[Annotated[int, Field(gt=0)]] = None
    max_staging_seconds: Optional[Annotated[int, Field(gt=0)]] = None
    max_artifact_bytes: Optional[Annotated[int, Field(gt=0)]] = None


class ExecutionPackageV1(ProtocolModel):
    """Core -> worker: everything needed to run one execution."""


    execution_id: Identifier
    #: Collapses a retried submission onto the existing execution instead of
    #: starting a second billed job. Core derives it; the worker only echoes it.
    idempotency_key: Identifier
    #: Digest over the canonical serialization of this package's body. The
    #: worker echoes it on every event, which is how core tells "this worker is
    #: reporting on the package I sent" from "this worker is reporting on a
    #: stale package it still had".
    request_digest: ContentDigest
    engine: NonEmptyText = "native"
    issued_at: datetime
    #: After this instant a worker must refuse the package rather than start
    #: it. Without it, a job that sat in a provider queue through an outage
    #: comes back to life against state core has already given up on.
    expires_at: Optional[datetime] = None
    #: domain -> opaque digest the worker must match before accepting. See
    #: src.platform.worker_protocol.worker_info.FINGERPRINT_DOMAINS.
    required_fingerprints: dict[Identifier, NonEmptyText] = {}
    #: pipe_type -> contract fingerprint, for exactly this package's pipes
    #: (see src.pipelines.remote_fingerprint.compute_pipe_contract_fingerprint).
    #: Empty (a package built before this field existed) makes the worker fall
    #: back to comparing required_fingerprints against its whole catalog.
    pipe_contracts: dict[Identifier, NonEmptyText] = {}
    model_bundle: ModelBundleManifestV1
    processed_pipes: ProcessedPipelineV1
    #: The content-addressed user-media files ``processed_pipes`` references
    #: by ``asset://<logical_id>`` token, when the assembler collected any.
    #: ``None`` for a package assembled without a storage root to collect
    #: against - not the same as an empty manifest.
    input_assets: Optional[InputAssetManifestV1] = None
    limits: ExecutionLimitsV1 = ExecutionLimitsV1()
    #: Free-form, JSON-safe context core wants echoed back on artifacts.
    metadata: dict[str, JsonValue] = {}

    @model_validator(mode="after")
    def _expiry_after_issue(self) -> "ExecutionPackageV1":
        if self.expires_at is not None and self.expires_at <= self.issued_at:
            raise ValueError("expires_at must be after issued_at")
        return self
