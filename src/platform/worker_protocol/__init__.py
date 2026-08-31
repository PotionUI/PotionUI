"""Provider-neutral worker protocol, schema version 1.

The contracts a headless worker and PotionUI core exchange to run one processed
pipeline off-box. Nothing here knows about a transport, an HTTP client, or a
compute provider - it is the vocabulary those layers carry, and it deliberately
lives in ``src/platform`` so that features, pipelines, plugin providers and the
worker process itself can all speak it without any of them importing each other.

Five payloads:

===========================  ==================================================
``WorkerInfoV1``             worker -> core handshake: identity, capabilities,
                             compatibility fingerprints
``ExecutionPackageV1``       core -> worker: the job, including the JSON-safe
                             ``processed_pipes`` pipeline
``ModelBundleManifestV1``    the content-addressed model working set
``JobEventV1``               worker -> core progress, on a monotonic cursor
``ArtifactRefV1``            a checksummed reference to a produced artifact
``EventResumeRequestV1``     core -> worker: resend everything after a cursor
===========================  ==================================================

Each travels inside the envelope defined in ``envelope`` - write one with
:func:`to_wire`, read one with :func:`read_envelope`. The payload models carry
no version of their own; the envelope states it once for the whole document.

The sixth contract, the core-side ``RemoteExecution`` row and its state
machine, is core state rather than wire format and lives with the feature that
owns it (``src.features.remote_execution``).
"""

from src.platform.worker_protocol.artifact import ArtifactRefV1
from src.platform.worker_protocol.common import (
    DIGEST_ALGORITHMS,
    ContentDigest,
    ProtocolModel,
    validate_contained_relative_path,
)
from src.platform.worker_protocol.envelope import (
    KIND_ARTIFACT_REF,
    KIND_EVENT_RESUME_REQUEST,
    KIND_EXECUTION_PACKAGE,
    KIND_JOB_EVENT,
    KIND_MODEL_BUNDLE_MANIFEST,
    KIND_MODEL_INVENTORY_RESPONSE,
    KIND_WORKER_INFO,
    PAYLOAD_KINDS,
    PAYLOAD_MODELS,
    WORKER_PROTOCOL_SCHEMA,
    WORKER_PROTOCOL_SCHEMA_VERSION,
    WorkerEnvelopeError,
    envelope,
    read_envelope,
    supported_versions,
    to_wire,
    validate_envelope,
)
from src.platform.worker_protocol.event_resume import EventResumeRequestV1
from src.platform.worker_protocol.execution_package import (
    ExecutionLimitsV1,
    ExecutionPackageV1,
    ProcessedPipelineV1,
    ProcessedPipeV1,
)
from src.platform.worker_protocol.input_asset import InputAssetManifestV1, InputAssetV1
from src.platform.worker_protocol.job_event import (
    TERMINAL_EVENT_KINDS,
    FingerprintMismatchV1,
    JobErrorV1,
    JobEventKind,
    JobEventV1,
)
from src.platform.worker_protocol.model_bundle import (
    ModelBundleEntryV1,
    ModelBundleManifestV1,
)
from src.platform.worker_protocol.model_fetch import ModelFetchRequestV1
from src.platform.worker_protocol.model_inventory import (
    ModelEntryStatus,
    ModelInventoryEntryV1,
    ModelInventoryResponseV1,
)
from src.platform.worker_protocol.worker_info import (
    FINGERPRINT_DOMAINS,
    GpuInfoV1,
    WorkerCapabilitiesV1,
    WorkerInfoV1,
)

__all__ = [
    "ArtifactRefV1",
    "ContentDigest",
    "DIGEST_ALGORITHMS",
    "EventResumeRequestV1",
    "ExecutionLimitsV1",
    "ExecutionPackageV1",
    "FINGERPRINT_DOMAINS",
    "FingerprintMismatchV1",
    "GpuInfoV1",
    "InputAssetManifestV1",
    "InputAssetV1",
    "JobErrorV1",
    "JobEventKind",
    "JobEventV1",
    "KIND_ARTIFACT_REF",
    "KIND_EVENT_RESUME_REQUEST",
    "KIND_EXECUTION_PACKAGE",
    "KIND_JOB_EVENT",
    "KIND_MODEL_BUNDLE_MANIFEST",
    "KIND_MODEL_FETCH_REQUEST",
    "KIND_MODEL_INVENTORY_RESPONSE",
    "KIND_WORKER_INFO",
    "ModelBundleEntryV1",
    "ModelBundleManifestV1",
    "ModelEntryStatus",
    "ModelFetchRequestV1",
    "ModelInventoryEntryV1",
    "ModelInventoryResponseV1",
    "PAYLOAD_KINDS",
    "PAYLOAD_MODELS",
    "ProcessedPipeV1",
    "ProcessedPipelineV1",
    "ProtocolModel",
    "TERMINAL_EVENT_KINDS",
    "WORKER_PROTOCOL_SCHEMA",
    "WORKER_PROTOCOL_SCHEMA_VERSION",
    "WorkerCapabilitiesV1",
    "WorkerEnvelopeError",
    "WorkerInfoV1",
    "envelope",
    "read_envelope",
    "supported_versions",
    "to_wire",
    "validate_contained_relative_path",
    "validate_envelope",
]
