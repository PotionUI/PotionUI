"""The envelope every worker-protocol document travels in, and its version.

Follows the portable-document shape already used for automation exports
(`validate_automation_envelope` in `src.platform.plugins.automation_templates`)
and video-director documents (`src.features.video_director.normalize`): a
module-level integer `*_SCHEMA_VERSION` constant, a `schema`/`kind` pair naming
what the document is, and one validate-on-read function that raises a *coded*
error carrying found-vs-expected. The payload models themselves carry no
version field - the version is stated once, for the whole document.

A document looks like::

    {"schema": "potionui.worker", "kind": "job_event",
     "schema_version": 1, "payload": {...}}

**How a v2 coexists with a v1.** `PAYLOAD_MODELS` is keyed by
``(kind, schema_version)``. A v2 adds entries under version 2 pointing at new
model classes; the version-1 entries are untouched, so one core keeps
understanding a v1 worker while talking v2 to a newer one.
`read_envelope` dispatches on the pair, and `supported_versions` reports what a
given kind can be read as - which is what makes a version skew diagnosable
instead of surfacing as a confusing field error.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Mapping, Tuple, Type

from pydantic import BaseModel, ValidationError

from src.platform.worker_protocol.artifact import ArtifactRefV1
from src.platform.worker_protocol.event_resume import EventResumeRequestV1
from src.platform.worker_protocol.execution_package import ExecutionPackageV1
from src.platform.worker_protocol.job_event import JobEventV1
from src.platform.worker_protocol.model_bundle import ModelBundleManifestV1
from src.platform.worker_protocol.model_fetch import ModelFetchRequestV1
from src.platform.worker_protocol.model_inventory import ModelInventoryResponseV1
from src.platform.worker_protocol.version import WORKER_PROTOCOL_VERSION
from src.platform.worker_protocol.worker_info import WorkerInfoV1

WORKER_PROTOCOL_SCHEMA = "potionui.worker"

#: Sourced from the leaf `version` module rather than restated, because the
#: handshake fingerprint hashes the same integer from the other side of the
#: pipelines -> platform import direction. Two hand-maintained copies would let
#: the fingerprint call a worker compatible while the envelope rejects its
#: documents.
WORKER_PROTOCOL_SCHEMA_VERSION = WORKER_PROTOCOL_VERSION

KIND_WORKER_INFO = "worker_info"
KIND_EXECUTION_PACKAGE = "execution_package"
KIND_MODEL_BUNDLE_MANIFEST = "model_bundle_manifest"
KIND_MODEL_INVENTORY_RESPONSE = "model_inventory_response"
KIND_MODEL_FETCH_REQUEST = "model_fetch_request"
KIND_JOB_EVENT = "job_event"
KIND_ARTIFACT_REF = "artifact_ref"
KIND_EVENT_RESUME_REQUEST = "event_resume_request"

#: (kind, schema_version) -> the model that reads that document. A payload
#: model may appear more than once: an artifact reference is both a document in
#: its own right and a part nested inside a job event.
PAYLOAD_MODELS: Dict[Tuple[str, int], Type[BaseModel]] = {
    (KIND_WORKER_INFO, 1): WorkerInfoV1,
    (KIND_EXECUTION_PACKAGE, 1): ExecutionPackageV1,
    (KIND_MODEL_BUNDLE_MANIFEST, 1): ModelBundleManifestV1,
    (KIND_MODEL_INVENTORY_RESPONSE, 1): ModelInventoryResponseV1,
    (KIND_MODEL_FETCH_REQUEST, 1): ModelFetchRequestV1,
    (KIND_JOB_EVENT, 1): JobEventV1,
    (KIND_ARTIFACT_REF, 1): ArtifactRefV1,
    (KIND_EVENT_RESUME_REQUEST, 1): EventResumeRequestV1,
}

#: The kind to stamp on each payload type when writing an envelope.
PAYLOAD_KINDS: Dict[Type[BaseModel], str] = {
    WorkerInfoV1: KIND_WORKER_INFO,
    ExecutionPackageV1: KIND_EXECUTION_PACKAGE,
    ModelBundleManifestV1: KIND_MODEL_BUNDLE_MANIFEST,
    ModelInventoryResponseV1: KIND_MODEL_INVENTORY_RESPONSE,
    ModelFetchRequestV1: KIND_MODEL_FETCH_REQUEST,
    JobEventV1: KIND_JOB_EVENT,
    ArtifactRefV1: KIND_ARTIFACT_REF,
    EventResumeRequestV1: KIND_EVENT_RESUME_REQUEST,
}


class WorkerEnvelopeError(ValueError):
    """Raised on the first structural problem in a candidate worker document.

    Carries a `code` identifying which check failed, plus any detail the caller
    needs to compose its own message (`wrong_version` carries
    `found`/`expected`/`supported`). Callers catch this and raise their own
    error type with their own wording - it is deliberately not user-facing.
    """

    def __init__(self, code: str, **detail: Any):
        self.code = code
        self.detail = detail
        super().__init__(code)


def supported_versions(kind: str) -> Tuple[int, ...]:
    """Every schema version this build can read *kind* as, ascending."""
    return tuple(sorted(v for (k, v) in PAYLOAD_MODELS if k == kind))


def envelope(
    payload: BaseModel, *, schema_version: int = WORKER_PROTOCOL_SCHEMA_VERSION
) -> Dict[str, Any]:
    """Wrap a payload in its envelope, as a JSON-safe dict."""
    kind = PAYLOAD_KINDS.get(type(payload))
    if kind is None:
        raise WorkerEnvelopeError("unknown_payload_type", found=type(payload).__name__)

    return {
        "schema": WORKER_PROTOCOL_SCHEMA,
        "kind": kind,
        "schema_version": schema_version,
        "payload": payload.model_dump(mode="json"),
    }


def to_wire(payload: BaseModel) -> str:
    """The canonical JSON form of a payload's envelope."""
    return json.dumps(envelope(payload), separators=(",", ":"))


def validate_envelope(
    document: Any,
    *,
    schema: str = WORKER_PROTOCOL_SCHEMA,
    schema_version: int = WORKER_PROTOCOL_SCHEMA_VERSION,
) -> None:
    """Structural validation of a worker document, without decoding the payload.

    Raises `WorkerEnvelopeError` on the first violated check:

    - `not_dict` - document isn't a JSON object
    - `wrong_schema` - `schema` doesn't match (detail: found, expected)
    - `missing_kind` - `kind` absent or not a string
    - `wrong_version` - `schema_version` doesn't match (detail: found,
      expected, supported)
    - `unknown_kind` - `kind` is not a document type this build reads
    - `missing_payload` - `payload` absent or not a JSON object

    `schema_version` is checked against the *expected* version first so a
    version skew reports as `wrong_version` rather than as whatever field the
    newer version happened to add.
    """
    if not isinstance(document, Mapping):
        raise WorkerEnvelopeError("not_dict")

    found_schema = document.get("schema")
    if found_schema != schema:
        raise WorkerEnvelopeError(
            "wrong_schema", found=found_schema, expected=schema
        )

    kind = document.get("kind")
    if not isinstance(kind, str) or not kind:
        raise WorkerEnvelopeError("missing_kind")

    found_version = document.get("schema_version")
    if found_version != schema_version:
        raise WorkerEnvelopeError(
            "wrong_version",
            found=found_version,
            expected=schema_version,
            supported=supported_versions(kind),
        )

    if (kind, schema_version) not in PAYLOAD_MODELS:
        raise WorkerEnvelopeError("unknown_kind", found=kind)

    if not isinstance(document.get("payload"), Mapping):
        raise WorkerEnvelopeError("missing_payload")


def read_envelope(
    document: Any, *, schema_version: int = WORKER_PROTOCOL_SCHEMA_VERSION
) -> BaseModel:
    """Decode an inbound document into its payload model.

    This is the only sanctioned entry point for anything arriving from a peer:
    validating a payload directly against `JobEventV1` skips the version check,
    so a v2 document would be rejected for the wrong reason - or, if v2 only
    widened a value, quietly accepted as v1.
    """
    if isinstance(document, (str, bytes)):
        try:
            document = json.loads(document)
        except (ValueError, TypeError) as exc:
            raise WorkerEnvelopeError("not_json", detail=str(exc)) from exc

    validate_envelope(document, schema_version=schema_version)

    model = PAYLOAD_MODELS[(document["kind"], schema_version)]
    try:
        return model.model_validate(document["payload"])
    except ValidationError as exc:
        # include_context=False: a field validator that raises ValueError
        # (e.g. validate_contained_relative_path) puts the exception object
        # itself in ctx, which a caller that JSON-serializes this detail
        # (every HTTP route does) cannot encode.
        raise WorkerEnvelopeError(
            "invalid_payload", kind=document["kind"], errors=exc.errors(include_context=False)
        ) from exc
