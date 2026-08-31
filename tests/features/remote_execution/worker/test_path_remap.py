"""remap_model_paths: rewriting a processed pipeline's model file_paths from
the dispatching host's paths onto this worker's depot layout."""

import pytest

from src.features.remote_execution.worker.path_remap import ModelRemapError, remap_model_paths
from src.platform.worker_protocol import (
    ContentDigest,
    ModelBundleEntryV1,
    ModelBundleManifestV1,
    ProcessedPipelineV1,
    ProcessedPipeV1,
)

HOST_CHECKPOINT_PATH = "/host/models/checkpoints/sdxl.safetensors"
HOST_LORA_PATH = "/host/models/loras/style.safetensors"


def _bundle(*entries):
    return ModelBundleManifestV1(
        bundle_id="bundle-1",
        bundle_digest=ContentDigest(algorithm="sha256", hex="ab" * 32),
        entries=entries,
    )


def _entry(relative_path, logical_id=None):
    return ModelBundleEntryV1(
        logical_id=logical_id or relative_path,
        role="checkpoint",
        relative_path=relative_path,
        digest=ContentDigest(algorithm="sha256", hex="cd" * 32),
        size_bytes=1,
    )


def _stage(depot_dir, relative_path):
    dest = depot_dir / relative_path
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(b"x")


def test_a_referenced_file_path_is_rewritten_to_the_depot_path(tmp_path):
    entry = _entry("checkpoint/sdxl.safetensors")
    _stage(tmp_path, entry.relative_path)
    pipeline = ProcessedPipelineV1(pipes=(
        ProcessedPipeV1(
            pipe_id="loader", pipe_type="loader/checkpoint",
            config={"model": {"file_path": HOST_CHECKPOINT_PATH, "name": "sdxl"}},
            inputs={},
        ),
    ))

    remapped = remap_model_paths(pipeline, _bundle(entry), tmp_path)

    assert remapped.pipes[0].config["model"]["file_path"] == str(tmp_path / entry.relative_path)


def test_a_nested_lora_stack_reference_is_rewritten_too(tmp_path):
    entry = _entry("lora/style.safetensors")
    _stage(tmp_path, entry.relative_path)
    pipeline = ProcessedPipelineV1(pipes=(
        ProcessedPipeV1(
            pipe_id="loader", pipe_type="loader/checkpoint",
            config={"loras": [{"file_path": HOST_LORA_PATH, "name": "style", "weight": 0.8}]},
            inputs={},
        ),
    ))

    remapped = remap_model_paths(pipeline, _bundle(entry), tmp_path)

    assert remapped.pipes[0].config["loras"][0]["file_path"] == str(tmp_path / entry.relative_path)


def test_a_disabled_lora_slot_is_left_untouched(tmp_path):
    """weight == 0 means build_model_bundle never gave it a manifest entry -
    remapping must skip it rather than failing to find one."""
    pipeline = ProcessedPipelineV1(pipes=(
        ProcessedPipeV1(
            pipe_id="loader", pipe_type="loader/checkpoint",
            config={"loras": [{"file_path": HOST_LORA_PATH, "name": "style", "weight": 0.0}]},
            inputs={},
        ),
    ))

    remapped = remap_model_paths(pipeline, _bundle(), tmp_path)

    assert remapped.pipes[0].config["loras"][0]["file_path"] == HOST_LORA_PATH


def test_a_file_with_no_manifest_entry_fails_the_remap(tmp_path):
    pipeline = ProcessedPipelineV1(pipes=(
        ProcessedPipeV1(
            pipe_id="loader", pipe_type="loader/checkpoint",
            config={"model": {"file_path": HOST_CHECKPOINT_PATH, "name": "sdxl"}},
            inputs={},
        ),
    ))

    with pytest.raises(ModelRemapError):
        remap_model_paths(pipeline, _bundle(), tmp_path)


def test_a_matching_manifest_entry_whose_file_is_not_actually_staged_fails(tmp_path):
    entry = _entry("checkpoint/sdxl.safetensors")
    # deliberately not staged on disk
    pipeline = ProcessedPipelineV1(pipes=(
        ProcessedPipeV1(
            pipe_id="loader", pipe_type="loader/checkpoint",
            config={"model": {"file_path": HOST_CHECKPOINT_PATH, "name": "sdxl"}},
            inputs={},
        ),
    ))

    with pytest.raises(ModelRemapError):
        remap_model_paths(pipeline, _bundle(entry), tmp_path)


def test_an_ambiguous_filename_match_fails_rather_than_guessing(tmp_path):
    entry_a = _entry("checkpoint/sdxl.safetensors", logical_id="checkpoint/sdxl.safetensors")
    entry_b = _entry("lora/sdxl.safetensors", logical_id="lora/sdxl.safetensors")
    _stage(tmp_path, entry_a.relative_path)
    _stage(tmp_path, entry_b.relative_path)
    pipeline = ProcessedPipelineV1(pipes=(
        ProcessedPipeV1(
            pipe_id="loader", pipe_type="loader/checkpoint",
            config={"model": {"file_path": "/host/anywhere/sdxl.safetensors", "name": "sdxl"}},
            inputs={},
        ),
    ))

    with pytest.raises(ModelRemapError):
        remap_model_paths(pipeline, _bundle(entry_a, entry_b), tmp_path)


def test_a_pipeline_with_no_model_references_passes_through_unchanged(tmp_path):
    pipeline = ProcessedPipelineV1(pipes=(
        ProcessedPipeV1(pipe_id="p", pipe_type="generator/plain", config={"steps": 20}, inputs={}),
    ))

    remapped = remap_model_paths(pipeline, _bundle(), tmp_path)

    assert remapped.pipes[0].config == {"steps": 20}
