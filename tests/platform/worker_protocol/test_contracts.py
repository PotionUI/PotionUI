"""Round-trip stability and the validation rules each contract carries."""

import json
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from src.platform.worker_protocol import (
    ArtifactRefV1,
    ContentDigest,
    DIGEST_ALGORITHMS,
    EventResumeRequestV1,
    ExecutionPackageV1,
    FingerprintMismatchV1,
    InputAssetManifestV1,
    InputAssetV1,
    JobErrorV1,
    JobEventKind,
    JobEventV1,
    ModelBundleEntryV1,
    ModelBundleManifestV1,
    ProcessedPipelineV1,
    TERMINAL_EVENT_KINDS,
    WorkerInfoV1,
    read_envelope,
    to_wire,
    validate_contained_relative_path,
)

from tests.platform.worker_protocol.factories import (
    ISSUED_AT,
    make_artifact,
    make_bundle,
    make_digest,
    make_event,
    make_event_resume_request,
    make_failed_event,
    make_input_asset_manifest,
    make_package,
    make_pipeline,
    make_rejected_event,
    make_worker_info,
)

BUILDERS = [
    make_worker_info,
    make_package,
    make_bundle,
    make_event,
    make_failed_event,
    make_rejected_event,
    make_artifact,
    make_event_resume_request,
]


@pytest.mark.parametrize("build", BUILDERS, ids=lambda b: b.__name__)
def test_round_trip_is_byte_stable(build):
    original = build()
    once = to_wire(original)
    twice = to_wire(read_envelope(once))

    assert once == twice
    assert read_envelope(twice) == original


@pytest.mark.parametrize("build", BUILDERS, ids=lambda b: b.__name__)
def test_wire_form_is_plain_json(build):
    """Nothing on the wire needs a custom decoder to read."""
    decoded = json.loads(to_wire(build()))
    assert isinstance(decoded, dict)
    assert decoded["schema_version"] == 1


@pytest.mark.parametrize("build", BUILDERS, ids=lambda b: b.__name__)
def test_messages_are_immutable(build):
    payload = build()
    field = next(iter(type(payload).model_fields))
    with pytest.raises(ValidationError):
        setattr(payload, field, getattr(payload, field))



class TestContentDigest:
    @pytest.mark.parametrize("algorithm", ["md5", "blake3", "sha1", "crc32", ""])
    def test_rejects_an_algorithm_core_cannot_verify(self, algorithm):
        with pytest.raises(ValidationError):
            ContentDigest(algorithm=algorithm, hex="ab" * 16)

    def test_every_advertised_algorithm_is_actually_computable(self):
        """The gate is only real if this build can perform the verification.

        An algorithm accepted here but unavailable to hashlib would pass
        validation and fail later, after the document has been trusted - which
        is the one place a digest check must never fail.
        """
        import hashlib

        assert DIGEST_ALGORITHMS
        for algorithm in DIGEST_ALGORITHMS:
            assert algorithm in hashlib.algorithms_available

    @pytest.mark.parametrize("bad", ["AB" * 32, "zz" * 32, "", "sha256:abcd"])
    def test_rejects_non_lowercase_hex(self, bad):
        with pytest.raises(ValidationError):
            ContentDigest(algorithm="sha256", hex=bad)

    def test_str_is_the_familiar_algorithm_colon_hex(self):
        assert str(make_digest("a")) == "sha256:" + "a" * 64


class TestPathContainment:
    @pytest.mark.parametrize(
        "path",
        [
            "../escape.safetensors",
            "a/../../escape.safetensors",
            "/absolute/path.safetensors",
            "\\windows\\style",
            "C:/drive/path",
            "",
            ".",
            "./..",
        ],
    )
    def test_rejects_paths_that_leave_their_root(self, path):
        with pytest.raises(ValueError):
            validate_contained_relative_path(path)

    @pytest.mark.parametrize(
        "path", ["a.safetensors", "sdxl/base.safetensors", "a/b/../c.bin", "./a.bin"]
    )
    def test_allows_contained_paths(self, path):
        assert validate_contained_relative_path(path) == path

    def test_bundle_entry_rejects_a_traversing_destination(self):
        with pytest.raises(ValidationError):
            ModelBundleEntryV1(
                logical_id="m",
                role="checkpoint",
                relative_path="../../etc/passwd",
                digest=make_digest("a"),
                size_bytes=1,
            )

    def test_artifact_rejects_a_traversing_filename(self):
        with pytest.raises(ValidationError):
            ArtifactRefV1(
                artifact_id="a",
                kind="image",
                media_type="image/png",
                size_bytes=1,
                digest=make_digest("a"),
                uri="https://example.invalid/a",
                filename="../../x.png",
            )


class TestArtifactRole:
    def test_role_seed_and_derived_default_to_none_for_a_legacy_artifact(self):
        artifact = ArtifactRefV1(
            artifact_id="a", kind="image", media_type="image/png", size_bytes=1,
            digest=make_digest("a"), uri="https://example.invalid/a",
        )
        assert artifact.role is None
        assert artifact.seed is None
        assert artifact.derived is None

    def test_role_seed_and_derived_round_trip(self):
        artifact = make_artifact()
        assert artifact.role == "gallery"
        assert artifact.seed == 1234
        assert artifact.derived is False

        restored = read_envelope(to_wire(artifact))
        assert restored == artifact
        assert restored.role == "gallery"
        assert restored.seed == 1234
        assert restored.derived is False


class TestModelBundleManifest:
    def test_total_size_is_derived_when_absent(self):
        bundle = make_bundle()
        assert bundle.total_size_bytes == sum(e.size_bytes for e in bundle.entries)

    def test_a_total_that_disagrees_with_the_entries_is_rejected(self):
        body = make_bundle().model_dump(mode="json")
        body["total_size_bytes"] = 1
        with pytest.raises(ValidationError):
            ModelBundleManifestV1.model_validate(body)

    def test_duplicate_logical_ids_are_rejected(self):
        entry = make_bundle().entries[0]
        with pytest.raises(ValidationError):
            ModelBundleManifestV1(
                bundle_id="b",
                bundle_digest=make_digest("b"),
                entries=(entry, entry),
            )

    def test_two_entries_may_not_write_the_same_destination(self):
        first = make_bundle().entries[0]
        second = first.model_copy(update={"logical_id": "other"})
        with pytest.raises(ValidationError):
            ModelBundleManifestV1(
                bundle_id="b",
                bundle_digest=make_digest("b"),
                entries=(first, second),
            )

    def test_lookup_by_logical_id(self):
        bundle = make_bundle()
        assert bundle.entry("lora-style").role == "lora"
        assert bundle.entry("absent") is None


class TestInputAssetManifest:
    def test_total_size_is_derived_when_absent(self):
        manifest = make_input_asset_manifest()
        assert manifest.total_size_bytes == sum(a.size_bytes for a in manifest.assets)

    def test_a_total_that_disagrees_with_the_assets_is_rejected(self):
        body = make_input_asset_manifest().model_dump(mode="json")
        body["total_size_bytes"] = 1
        with pytest.raises(ValidationError):
            InputAssetManifestV1.model_validate(body)

    def test_duplicate_logical_ids_are_rejected(self):
        asset = make_input_asset_manifest().assets[0]
        with pytest.raises(ValidationError):
            InputAssetManifestV1(assets=(asset, asset))

    def test_overlapping_relative_paths_are_rejected(self):
        first = make_input_asset_manifest().assets[0]
        second = first.model_copy(update={
            "logical_id": "other",
            "relative_path": f"{first.relative_path}/nested.png",
        })
        with pytest.raises(ValidationError):
            InputAssetManifestV1(assets=(first, second))

    def test_distinct_non_overlapping_assets_are_allowed(self):
        first = make_input_asset_manifest().assets[0]
        second = first.model_copy(update={
            "logical_id": "other",
            "relative_path": "inputs/other/second.png",
        })
        manifest = InputAssetManifestV1(assets=(first, second))
        assert len(manifest.assets) == 2

    def test_lookup_by_logical_id(self):
        manifest = make_input_asset_manifest()
        found = manifest.assets[0]
        assert manifest.asset(found.logical_id) is found
        assert manifest.asset("absent") is None

    def test_relative_path_rejects_traversal(self):
        with pytest.raises(ValidationError):
            InputAssetV1(
                logical_id="a",
                relative_path="../../etc/passwd",
                digest=make_digest("a"),
                size_bytes=1,
            )

    def test_size_bytes_must_be_positive(self):
        with pytest.raises(ValidationError):
            InputAssetV1(
                logical_id="a",
                relative_path="inputs/a/f.png",
                digest=make_digest("a"),
                size_bytes=0,
            )


class TestExecutionPackage:
    def test_input_assets_defaults_to_none(self):
        body = make_package().model_dump(mode="json")
        body["input_assets"] = None
        assert ExecutionPackageV1.model_validate(body).input_assets is None


    def test_processed_pipes_must_be_strict_json(self):
        """A live Python object must fail here, not at transport time."""
        with pytest.raises(ValidationError):
            ProcessedPipelineV1.model_validate(
                {"pipes": [{"pipe_id": "p", "pipe_type": "t", "config": {"x": object()}}]}
            )

    def test_processed_pipes_carries_its_own_shape_version(self):
        """The pipeline shape can move to v2 without the package moving."""
        assert make_pipeline().shape_version == 1
        document = json.loads(to_wire(make_package()))
        assert document["schema_version"] == 1
        assert document["payload"]["processed_pipes"]["shape_version"] == 1

    def test_duplicate_pipe_ids_are_rejected(self):
        with pytest.raises(ValidationError):
            ProcessedPipelineV1.model_validate(
                {
                    "pipes": [
                        {"pipe_id": "p", "pipe_type": "a"},
                        {"pipe_id": "p", "pipe_type": "b"},
                    ]
                }
            )

    def test_expiry_must_follow_issue(self):
        body = make_package().model_dump(mode="json")
        body["expires_at"] = body["issued_at"]
        with pytest.raises(ValidationError):
            ExecutionPackageV1.model_validate(body)

    def test_a_package_without_an_expiry_is_allowed(self):
        body = make_package().model_dump(mode="json")
        body["expires_at"] = None
        assert ExecutionPackageV1.model_validate(body).expires_at is None


class TestJobEvent:
    def test_cursor_starts_at_one(self):
        body = make_event().model_dump(mode="json")
        body["cursor"] = 0
        with pytest.raises(ValidationError):
            JobEventV1.model_validate(body)

    def test_a_failure_must_say_why(self):
        body = make_event().model_dump(mode="json")
        body["kind"] = JobEventKind.FAILED.value
        body["error"] = None
        with pytest.raises(ValidationError):
            JobEventV1.model_validate(body)

    def test_a_kind_core_does_not_model_is_carried_not_rejected(self):
        """Plugin pipes emit progress vocabulary core has no list of."""
        event = make_event(kind="plugin_specific_progress")
        assert event.known_kind is None
        assert event.is_terminal is False
        assert read_envelope(to_wire(event)) == event

    @pytest.mark.parametrize("kind", sorted(k.value for k in TERMINAL_EVENT_KINDS))
    def test_terminal_kinds_report_themselves_as_terminal(self, kind):
        if kind == JobEventKind.FAILED.value:
            assert make_failed_event().is_terminal
        elif kind == JobEventKind.REJECTED.value:
            assert make_rejected_event().is_terminal
        else:
            assert make_event(kind=kind).is_terminal

    def test_progress_is_a_fraction(self):
        body = make_event().model_dump(mode="json")
        body["progress"] = 1.5
        with pytest.raises(ValidationError):
            JobEventV1.model_validate(body)

    def test_events_carry_artifact_references_not_bytes(self):
        event = make_event()
        assert event.artifacts[0].uri
        assert not hasattr(event.artifacts[0], "data")

    def test_a_rejection_must_say_why(self):
        body = make_rejected_event().model_dump(mode="json")
        body["error"] = None
        with pytest.raises(ValidationError):
            JobEventV1.model_validate(body)

    def test_a_fingerprint_mismatch_code_requires_the_structured_field(self):
        body = make_rejected_event().model_dump(mode="json")
        body["error"]["fingerprint_mismatch"] = None
        with pytest.raises(ValidationError):
            JobEventV1.model_validate(body)

    def test_a_fingerprint_mismatch_domain_must_be_a_known_one(self):
        with pytest.raises(ValidationError):
            FingerprintMismatchV1(domain="not_a_domain", expected="a", actual="b")

    def test_a_non_mismatch_error_code_does_not_require_the_structured_field(self):
        """The pairing rule is one-directional: any other error code is free
        to leave fingerprint_mismatch unset."""
        event = make_failed_event()
        assert event.error.code != "fingerprint_mismatch"
        assert event.error.fingerprint_mismatch is None


class TestWorkerInfo:
    def test_a_worker_must_declare_at_least_one_protocol_version(self):
        body = make_worker_info().model_dump(mode="json")
        body["protocol_versions"] = []
        with pytest.raises(ValidationError):
            WorkerInfoV1.model_validate(body)

    def test_fingerprints_are_opaque_and_open_ended(self):
        """A new fingerprint domain must not require a protocol version bump."""
        worker = make_worker_info().model_copy(
            update={"fingerprints": {"pipe_catalog": "x", "a_new_domain": "y"}}
        )
        assert read_envelope(to_wire(worker)).fingerprints["a_new_domain"] == "y"

    def test_an_empty_fingerprint_value_is_rejected(self):
        body = make_worker_info().model_dump(mode="json")
        body["fingerprints"]["pipe_catalog"] = ""
        with pytest.raises(ValidationError):
            WorkerInfoV1.model_validate(body)

    def test_capabilities_extend_through_feature_tokens(self):
        worker = make_worker_info()
        assert "fp8_quantize" in worker.capabilities.features


class TestEventResumeRequest:
    def test_after_cursor_zero_means_from_the_beginning(self):
        request = EventResumeRequestV1(execution_id="exec-1", after_cursor=0)
        assert request.after_cursor == 0

    def test_after_cursor_cannot_be_negative(self):
        with pytest.raises(ValidationError):
            EventResumeRequestV1(execution_id="exec-1", after_cursor=-1)


def test_datetimes_survive_the_round_trip_with_their_zone():
    parsed = read_envelope(to_wire(make_package()))
    assert parsed.issued_at == ISSUED_AT
    assert parsed.issued_at.tzinfo is not None
    assert parsed.issued_at.utcoffset() == timezone.utc.utcoffset(None)
