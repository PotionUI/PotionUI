"""Envelope identification, version rejection and v1/v2 coexistence."""

import json

import pytest

from src.platform.worker_protocol import (
    ArtifactRefV1,
    EventResumeRequestV1,
    ExecutionPackageV1,
    JobEventV1,
    ModelBundleManifestV1,
    ModelFetchRequestV1,
    ModelInventoryResponseV1,
    WORKER_PROTOCOL_SCHEMA,
    WORKER_PROTOCOL_SCHEMA_VERSION,
    WorkerEnvelopeError,
    WorkerInfoV1,
    envelope,
    read_envelope,
    supported_versions,
    to_wire,
    validate_envelope,
)
from src.platform.worker_protocol.envelope import PAYLOAD_KINDS, PAYLOAD_MODELS

from tests.platform.worker_protocol.factories import (
    make_artifact,
    make_bundle,
    make_event,
    make_event_resume_request,
    make_model_fetch_request,
    make_model_inventory_response,
    make_package,
    make_worker_info,
)

KINDS = [
    (make_worker_info, "worker_info", WorkerInfoV1),
    (make_package, "execution_package", ExecutionPackageV1),
    (make_bundle, "model_bundle_manifest", ModelBundleManifestV1),
    (make_model_inventory_response, "model_inventory_response", ModelInventoryResponseV1),
    (make_model_fetch_request, "model_fetch_request", ModelFetchRequestV1),
    (make_event, "job_event", JobEventV1),
    (make_artifact, "artifact_ref", ArtifactRefV1),
    (make_event_resume_request, "event_resume_request", EventResumeRequestV1),
]


@pytest.mark.parametrize("build,kind,model", KINDS, ids=[k for _, k, _ in KINDS])
def test_every_payload_gets_a_kind_and_a_version(build, kind, model):
    document = envelope(build())

    assert document["schema"] == WORKER_PROTOCOL_SCHEMA
    assert document["kind"] == kind
    assert document["schema_version"] == WORKER_PROTOCOL_SCHEMA_VERSION
    assert PAYLOAD_MODELS[(kind, 1)] is model


@pytest.mark.parametrize("build,kind,model", KINDS, ids=[k for _, k, _ in KINDS])
def test_read_envelope_dispatches_on_kind(build, kind, model):
    original = build()
    assert read_envelope(to_wire(original)) == original


def test_payload_models_carry_no_version_of_their_own():
    """The envelope states the version once; a second copy could disagree."""
    for _, _, model in KINDS:
        assert "version" not in model.model_fields
        assert "schema_version" not in model.model_fields


class TestVersionRejection:
    def test_a_newer_version_is_refused_with_found_and_expected(self):
        document = envelope(make_event())
        document["schema_version"] = 2

        with pytest.raises(WorkerEnvelopeError) as excinfo:
            read_envelope(document)

        assert excinfo.value.code == "wrong_version"
        assert excinfo.value.detail["found"] == 2
        assert excinfo.value.detail["expected"] == 1
        assert excinfo.value.detail["supported"] == (1,)

    def test_wrong_version_wins_over_whatever_field_the_new_version_added(self):
        """A v2 body must report as a version skew, not as an unknown field."""
        document = envelope(make_event())
        document["schema_version"] = 2
        document["payload"]["a_field_v2_added"] = True

        with pytest.raises(WorkerEnvelopeError) as excinfo:
            read_envelope(document)
        assert excinfo.value.code == "wrong_version"

    def test_a_missing_version_is_a_version_error_not_a_crash(self):
        document = envelope(make_event())
        del document["schema_version"]

        with pytest.raises(WorkerEnvelopeError) as excinfo:
            read_envelope(document)
        assert excinfo.value.code == "wrong_version"
        assert excinfo.value.detail["found"] is None

    def test_validating_the_payload_directly_would_have_missed_the_skew(self):
        """Why read_envelope is the only sanctioned door."""
        document = envelope(make_event())
        document["schema_version"] = 2

        assert JobEventV1.model_validate(document["payload"]) is not None
        with pytest.raises(WorkerEnvelopeError):
            read_envelope(document)


class TestCoexistence:
    def test_a_v2_registers_alongside_its_v1(self):
        assert supported_versions("job_event") == (1,)

        PAYLOAD_MODELS[("job_event", 2)] = JobEventV1
        try:
            assert supported_versions("job_event") == (1, 2)

            document = envelope(make_event(), schema_version=2)
            assert read_envelope(document, schema_version=2) == make_event()
            assert read_envelope(to_wire(make_event())) == make_event()
        finally:
            PAYLOAD_MODELS.pop(("job_event", 2), None)

        assert supported_versions("job_event") == (1,)

    def test_supported_versions_of_an_unknown_kind_is_empty(self):
        assert supported_versions("not_a_kind") == ()


class TestStructuralValidation:
    @pytest.mark.parametrize(
        "mutate,code",
        [
            (lambda d: "not a mapping", "not_dict"),
            (lambda d: {**d, "schema": "something.else"}, "wrong_schema"),
            (lambda d: {k: v for k, v in d.items() if k != "schema"}, "wrong_schema"),
            (lambda d: {**d, "kind": ""}, "missing_kind"),
            (lambda d: {k: v for k, v in d.items() if k != "kind"}, "missing_kind"),
            (lambda d: {**d, "kind": 7}, "missing_kind"),
            (lambda d: {**d, "kind": "not_a_kind"}, "unknown_kind"),
            (lambda d: {**d, "payload": None}, "missing_payload"),
            (lambda d: {k: v for k, v in d.items() if k != "payload"}, "missing_payload"),
        ],
    )
    def test_each_structural_problem_gets_its_own_code(self, mutate, code):
        with pytest.raises(WorkerEnvelopeError) as excinfo:
            validate_envelope(mutate(envelope(make_event())))
        assert excinfo.value.code == code

    def test_wrong_schema_reports_found_and_expected(self):
        with pytest.raises(WorkerEnvelopeError) as excinfo:
            validate_envelope({**envelope(make_event()), "schema": "other"})
        assert excinfo.value.detail == {
            "found": "other",
            "expected": WORKER_PROTOCOL_SCHEMA,
        }

    def test_undecodable_json_is_its_own_code(self):
        with pytest.raises(WorkerEnvelopeError) as excinfo:
            read_envelope("{not json")
        assert excinfo.value.code == "not_json"

    def test_a_payload_that_fails_validation_is_reported_as_such(self):
        document = envelope(make_event())
        document["payload"]["cursor"] = 0

        with pytest.raises(WorkerEnvelopeError) as excinfo:
            read_envelope(document)
        assert excinfo.value.code == "invalid_payload"
        assert excinfo.value.detail["kind"] == "job_event"
        assert excinfo.value.detail["errors"]

    def test_unknown_payload_fields_are_rejected_rather_than_dropped(self):
        document = envelope(make_event())
        document["payload"]["something_the_worker_invented"] = 1

        with pytest.raises(WorkerEnvelopeError) as excinfo:
            read_envelope(document)
        assert excinfo.value.code == "invalid_payload"

    def test_wrapping_something_that_is_not_a_payload_is_refused(self):
        from src.platform.worker_protocol import ExecutionLimitsV1

        with pytest.raises(WorkerEnvelopeError) as excinfo:
            envelope(ExecutionLimitsV1())
        assert excinfo.value.code == "unknown_payload_type"


def test_the_wire_form_is_plain_json():
    document = json.loads(to_wire(make_package()))
    assert set(document) == {"schema", "kind", "schema_version", "payload"}


def test_the_envelope_version_is_the_shared_leaf_constant():
    """Drift guard: the handshake fingerprint hashes this same integer.

    If these two ever diverge, the fingerprint reports a compatible worker
    while the envelope rejects its documents as the wrong version.
    """
    from src.platform.worker_protocol.version import WORKER_PROTOCOL_VERSION

    assert WORKER_PROTOCOL_SCHEMA_VERSION is WORKER_PROTOCOL_VERSION


def test_the_two_kind_maps_cannot_disagree():
    """Registration is explicit now, so it can be forgotten - this is the guard.

    The auto-registering base class this replaced made a missing entry
    impossible by construction; two hand-written dicts make it a typo away. A
    payload absent from PAYLOAD_KINDS cannot be sent; absent from
    PAYLOAD_MODELS it cannot be read.
    """
    assert set(PAYLOAD_KINDS.values()) == {kind for kind, _ in PAYLOAD_MODELS}

    for (kind, version), model in PAYLOAD_MODELS.items():
        if version == WORKER_PROTOCOL_SCHEMA_VERSION:
            assert PAYLOAD_KINDS[model] == kind

    assert set(PAYLOAD_KINDS) == {model for _, _, model in KINDS}
