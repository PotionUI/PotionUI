"""What a worker reports about itself at handshake."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from pydantic import Field, field_validator

from src.platform.worker_protocol.common import (
    Identifier,
    NonEmptyText,
    ProtocolModel,
)

#: Fingerprint domains core compares when deciding whether a worker may run a
#: package, named after the functions in ``src.pipelines.remote_fingerprint``
#: that derive them. The values are opaque to this module - it models the shape
#: of the handshake, not how a fingerprint is computed - and the set is *not*
#: closed by the schema: ``fingerprints`` is a free mapping, so a domain can be
#: added without a protocol version bump. Which domains must match and which
#: are advisory is dispatch policy, not a property of the message.
FINGERPRINT_DOMAINS = ("pipe_catalog", "plugin_bundle", "build")


class GpuInfoV1(ProtocolModel):
    """One accelerator visible to the worker."""

    index: Annotated[int, Field(ge=0)]
    name: NonEmptyText
    total_memory_bytes: Annotated[int, Field(ge=0)]
    free_memory_bytes: Annotated[int, Field(ge=0)] | None = None
    compute_capability: str | None = None
    driver_version: str | None = None


class WorkerCapabilitiesV1(ProtocolModel):
    """The hardware and software a worker can bring to a package."""

    gpus: tuple[GpuInfoV1, ...] = ()
    cpu_count: Annotated[int, Field(ge=0)] = 0
    total_memory_bytes: Annotated[int, Field(ge=0)] = 0
    free_disk_bytes: Annotated[int, Field(ge=0)] = 0
    python_version: str = ""
    torch_version: str = ""
    cuda_version: str | None = None
    platform: str = ""
    attention_backends: tuple[str, ...] = ()
    #: Open-ended capability tokens. This is the extension point that keeps
    #: ``extra="forbid"`` affordable: a worker advertising something new emits
    #: a token here instead of a new field, and a core that does not know the
    #: token simply never asks for it.
    features: tuple[str, ...] = ()


class WorkerInfoV1(ProtocolModel):
    """Worker -> core handshake."""


    worker_id: Identifier
    #: Opaque key of the compute provider that started this worker. Core never
    #: enumerates the legal values - providers are plugins.
    provider: Identifier
    provider_job_id: str | None = None
    engine: NonEmptyText = "native"
    #: Every protocol version this worker can speak, so core can pick one
    #: rather than discovering the mismatch on the first real payload.
    protocol_versions: tuple[Annotated[int, Field(ge=1)], ...] = (1,)
    capabilities: WorkerCapabilitiesV1 = WorkerCapabilitiesV1()
    #: The device this worker was configured for - not necessarily the one it
    #: would end up on. A worker whose CUDA probe fails degrades to CPU
    #: silently, so it is the *request* that makes ``cuda_available`` legible.
    device: str | None = None
    #: None, not False, when the worker predates this field: core must read
    #: that silence as "unknown" and keep dispatching, never as "no GPU".
    cuda_available: bool | None = None
    #: Why CUDA is unusable, as torch words it (e.g. the driver-too-old
    #: message). Only set when ``cuda_available`` is False.
    cuda_error: str | None = None
    #: domain -> opaque digest. See FINGERPRINT_DOMAINS.
    fingerprints: dict[Identifier, NonEmptyText] = {}
    started_at: datetime | None = None

    @field_validator("protocol_versions")
    @classmethod
    def _at_least_one_version(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        if not value:
            raise ValueError("a worker must declare at least one protocol version")
        return value
