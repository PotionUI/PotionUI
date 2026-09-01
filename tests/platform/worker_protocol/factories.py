"""Fully populated instances of every wire message.

Deliberately *not* minimal: a round-trip test built from defaults proves only
that empty fields survive serialization. Every optional field is filled here so
a serialization bug in one of them fails a test.
"""

from datetime import datetime, timezone

from src.platform.worker_protocol import (
    ArtifactRefV1,
    ContentDigest,
    EventResumeRequestV1,
    ExecutionLimitsV1,
    ExecutionPackageV1,
    FingerprintMismatchV1,
    GpuInfoV1,
    InputAssetManifestV1,
    InputAssetV1,
    JobErrorV1,
    JobEventKind,
    JobEventV1,
    ModelBundleEntryV1,
    ModelBundleManifestV1,
    ModelFetchRequestV1,
    ModelInventoryEntryV1,
    ModelInventoryResponseV1,
    ProcessedPipelineV1,
    ProcessedPipeV1,
    WorkerCapabilitiesV1,
    WorkerInfoV1,
)

ISSUED_AT = datetime(2026, 7, 27, 10, 0, 0, tzinfo=timezone.utc)
EXPIRES_AT = datetime(2026, 7, 27, 11, 0, 0, tzinfo=timezone.utc)


def make_digest(seed: str = "a") -> ContentDigest:
    return ContentDigest(algorithm="sha256", hex=(seed * 64)[:64])


def make_worker_info() -> WorkerInfoV1:
    return WorkerInfoV1(
        worker_id="worker-01",
        provider="example-provider",
        provider_job_id="job-77",
        engine="native",
        protocol_versions=(1,),
        capabilities=WorkerCapabilitiesV1(
            gpus=(
                GpuInfoV1(
                    index=0,
                    name="NVIDIA GeForce RTX 5090",
                    total_memory_bytes=34_359_738_368,
                    free_memory_bytes=30_000_000_000,
                    compute_capability="12.0",
                    driver_version="580.00",
                ),
            ),
            cpu_count=32,
            total_memory_bytes=137_438_953_472,
            free_disk_bytes=2_000_000_000_000,
            python_version="3.12.7",
            torch_version="2.9.0",
            cuda_version="12.8",
            platform="linux-x86_64",
            attention_backends=("sdpa", "sage2"),
            features=("fp8_quantize", "stream_prefetch"),
        ),
        fingerprints={"pipe_catalog": "c" * 16, "plugin_bundle": "p" * 16, "build": "b" * 16},
        started_at=ISSUED_AT,
    )


def make_bundle() -> ModelBundleManifestV1:
    return ModelBundleManifestV1(
        bundle_id="bundle-1",
        bundle_digest=make_digest("b"),
        entries=(
            ModelBundleEntryV1(
                logical_id="ckpt-main",
                role="checkpoint",
                relative_path="sdxl/base.safetensors",
                digest=make_digest("c"),
                size_bytes=6_000_000_000,
                source_uri="https://example.invalid/base.safetensors",
            ),
            ModelBundleEntryV1(
                logical_id="lora-style",
                role="lora",
                relative_path="style/one.safetensors",
                digest=make_digest("d"),
                size_bytes=200_000_000,
                source_uri=None,
            ),
        ),
    )


def make_pipeline() -> ProcessedPipelineV1:
    return ProcessedPipelineV1(
        pipes=(
            ProcessedPipeV1(
                pipe_id="loader",
                pipe_type="checkpoint_loader",
                config={"model": "ckpt-main", "dtype": "bf16"},
                inputs={},
            ),
            ProcessedPipeV1(
                pipe_id="generator",
                pipe_type="sdxl_generator",
                enabled=True,
                config={"steps": 30, "cfg": 5.5, "seed": 1234, "size": [1024, 1024]},
                inputs={"model": "loader.model", "text_encoder": "loader.text_encoder"},
            ),
        )
    )


def make_input_asset_manifest() -> InputAssetManifestV1:
    return InputAssetManifestV1(
        assets=(
            InputAssetV1(
                logical_id="aaaaaaaaaaaaaaaa-reference",
                media_type="image/png",
                relative_path="inputs/aaaaaaaaaaaaaaaa-reference/reference.png",
                digest=make_digest("a"),
                size_bytes=12_345,
            ),
        ),
    )


def make_package() -> ExecutionPackageV1:
    return ExecutionPackageV1(
        execution_id="exec-1",
        idempotency_key="idem-1",
        request_digest=make_digest("e"),
        engine="native",
        issued_at=ISSUED_AT,
        expires_at=EXPIRES_AT,
        required_fingerprints={"pipe_catalog": "c" * 16, "plugin_bundle": "p" * 16},
        pipe_contracts={"checkpoint_loader": "l" * 16, "sdxl_generator": "g" * 16},
        model_bundle=make_bundle(),
        processed_pipes=make_pipeline(),
        input_assets=make_input_asset_manifest(),
        limits=ExecutionLimitsV1(
            max_wall_seconds=900,
            max_staging_seconds=600,
            max_artifact_bytes=1_000_000_000,
        ),
        metadata={"generation_id": "gen-1", "batch": 2},
    )


def make_artifact() -> ArtifactRefV1:
    return ArtifactRefV1(
        artifact_id="art-1",
        kind="image",
        media_type="image/png",
        size_bytes=1_048_576,
        digest=make_digest("f"),
        uri="https://example.invalid/artifacts/art-1.png",
        filename="out/art-1.png",
        pipe_id="generator",
        role="gallery",
        seed=1234,
        derived=False,
        metadata={"width": 1024, "height": 1024, "seed": 1234},
    )


def make_event(cursor: int = 1, kind: str = JobEventKind.RUNNING.value) -> JobEventV1:
    return JobEventV1(
        execution_id="exec-1",
        worker_id="worker-01",
        cursor=cursor,
        emitted_at=ISSUED_AT,
        kind=kind,
        pipe_id="generator",
        progress=0.5,
        detail="sampling",
        artifacts=(make_artifact(),),
        error=None,
        payload={"step": 15, "total": 30},
    )


def make_failed_event(cursor: int = 1) -> JobEventV1:
    return JobEventV1(
        execution_id="exec-1",
        worker_id="worker-01",
        cursor=cursor,
        emitted_at=ISSUED_AT,
        kind=JobEventKind.FAILED.value,
        error=JobErrorV1(
            code="cuda_oom",
            message="out of memory",
            retryable=True,
            detail="tried to allocate 12 GiB",
        ),
    )


def make_rejected_event(cursor: int = 1) -> JobEventV1:
    return JobEventV1(
        execution_id="exec-1",
        worker_id="worker-01",
        cursor=cursor,
        emitted_at=ISSUED_AT,
        kind=JobEventKind.REJECTED.value,
        error=JobErrorV1(
            code="fingerprint_mismatch",
            message="pipe catalog fingerprint mismatch",
            retryable=False,
            fingerprint_mismatch=FingerprintMismatchV1(
                domain="pipe_catalog", expected="c" * 16, actual="d" * 16,
            ),
        ),
    )


def make_event_resume_request() -> EventResumeRequestV1:
    return EventResumeRequestV1(execution_id="exec-1", after_cursor=3)


def make_model_fetch_request() -> ModelFetchRequestV1:
    return ModelFetchRequestV1(
        relative_path="checkpoint/model.safetensors",
        expected_digest=make_digest("9"),
        expected_size=6_000_000_000,
        url="https://example.invalid/model.safetensors",
        headers={"Authorization": "Bearer upstream-token"},
    )


def make_model_inventory_response() -> ModelInventoryResponseV1:
    return ModelInventoryResponseV1(
        bundle_id="bundle-1",
        entries=(
            ModelInventoryEntryV1(logical_id="ckpt-main", status="present"),
            ModelInventoryEntryV1(logical_id="lora-style", status="missing"),
        ),
    )
