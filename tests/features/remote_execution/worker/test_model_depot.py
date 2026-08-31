"""ModelDepot: presence/digest checking and staging against a real filesystem
depot - no torch, no model load."""

import hashlib
import json

import pytest

from src.features.remote_execution.worker.model_depot import ModelDepot, ModelStagingError
from src.platform.worker_protocol import ContentDigest, ModelBundleEntryV1, ModelBundleManifestV1

CONTENT = b"fake checkpoint bytes" * 100
DIGEST = hashlib.sha256(CONTENT).hexdigest()


def _entry(relative_path="checkpoint/model.safetensors", digest=DIGEST, size=len(CONTENT)):
    return ModelBundleEntryV1(
        logical_id="checkpoint/model.safetensors",
        role="checkpoint",
        relative_path=relative_path,
        digest=ContentDigest(algorithm="sha256", hex=digest),
        size_bytes=size,
    )


def _manifest(*entries):
    return ModelBundleManifestV1(
        bundle_id="bundle-1",
        bundle_digest=ContentDigest(algorithm="sha256", hex="ab" * 32),
        entries=entries,
    )


def test_inventory_reports_missing_for_an_unstaged_entry(tmp_path):
    depot = ModelDepot(depot_dir=tmp_path)
    response = depot.inventory(_manifest(_entry()))
    assert response.entries[0].status == "missing"


def test_stage_then_inventory_reports_present(tmp_path):
    depot = ModelDepot(depot_dir=tmp_path)
    entry = _entry()
    depot.stage(entry, [CONTENT])

    response = depot.inventory(_manifest(entry))
    assert response.entries[0].status == "present"


def test_a_file_present_without_ever_being_staged_is_verified_by_hashing(tmp_path):
    """No sidecar exists yet (e.g. a manually-placed file on the depot) - the
    depot must hash it once to decide, then trust that answer next time."""
    depot = ModelDepot(depot_dir=tmp_path)
    entry = _entry()
    dest = tmp_path / entry.relative_path
    dest.parent.mkdir(parents=True)
    dest.write_bytes(CONTENT)

    response = depot.inventory(_manifest(entry))
    assert response.entries[0].status == "present"
    # the hash it just computed must now be cached as a sidecar
    sidecar = dest.with_name(dest.name + ".digest")
    assert json.loads(sidecar.read_text())["digest"] == DIGEST


def test_wrong_content_at_the_right_size_is_mismatched_not_present(tmp_path):
    depot = ModelDepot(depot_dir=tmp_path)
    entry = _entry()
    dest = tmp_path / entry.relative_path
    dest.parent.mkdir(parents=True)
    dest.write_bytes(b"x" * len(CONTENT))  # same size, wrong bytes, no sidecar

    response = depot.inventory(_manifest(entry))
    assert response.entries[0].status == "mismatched"


def test_a_size_mismatch_is_reported_without_reading_the_file(tmp_path, monkeypatch):
    depot = ModelDepot(depot_dir=tmp_path)
    entry = _entry()
    dest = tmp_path / entry.relative_path
    dest.parent.mkdir(parents=True)
    dest.write_bytes(CONTENT[:-1])  # one byte short

    import src.features.remote_execution.worker.model_depot as module

    def _boom(*a, **k):
        raise AssertionError("must not hash when the size already disagrees")

    monkeypatch.setattr(module, "_hash_file", _boom)

    response = depot.inventory(_manifest(entry))
    assert response.entries[0].status == "mismatched"


def test_a_trusted_sidecar_skips_hashing_entirely(tmp_path, monkeypatch):
    depot = ModelDepot(depot_dir=tmp_path)
    entry = _entry()
    depot.stage(entry, [CONTENT])  # writes a real, correct sidecar

    import src.features.remote_execution.worker.model_depot as module

    def _boom(*a, **k):
        raise AssertionError("a trusted sidecar must not be re-hashed")

    monkeypatch.setattr(module, "_hash_file", _boom)

    response = depot.inventory(_manifest(entry))
    assert response.entries[0].status == "present"


def test_stage_rejects_a_digest_mismatch_and_leaves_no_partial_file(tmp_path):
    depot = ModelDepot(depot_dir=tmp_path)
    entry = _entry(digest="00" * 32)

    with pytest.raises(ModelStagingError):
        depot.stage(entry, [CONTENT])

    dest = tmp_path / entry.relative_path
    assert not dest.exists()
    assert not dest.with_name(dest.name + ".part").exists()


def test_stage_rejects_more_bytes_than_declared(tmp_path):
    depot = ModelDepot(depot_dir=tmp_path)
    entry = _entry(size=4)

    with pytest.raises(ModelStagingError):
        depot.stage(entry, [CONTENT])


def test_re_staging_identical_bytes_is_a_safe_no_op(tmp_path):
    depot = ModelDepot(depot_dir=tmp_path)
    entry = _entry()
    depot.stage(entry, [CONTENT])
    dest = depot.stage(entry, [CONTENT])
    assert dest.read_bytes() == CONTENT


def test_a_relative_path_escaping_the_depot_is_rejected_at_construction():
    with pytest.raises(ValueError):
        _entry(relative_path="../../etc/passwd")


def test_the_depot_itself_also_refuses_an_escaping_path(tmp_path):
    """Defense in depth: even an entry that bypassed pydantic's own structural
    check (``model_construct`` skips validators, standing in for a symlink or
    any other way a resolved path could still land outside the depot) must
    still be rejected by the depot's own resolved-path containment check."""
    depot = ModelDepot(depot_dir=tmp_path)
    malicious = ModelBundleEntryV1.model_construct(
        logical_id="checkpoint/evil",
        role="checkpoint",
        relative_path="../outside.bin",
        digest=ContentDigest(algorithm="sha256", hex=DIGEST),
        size_bytes=len(CONTENT),
    )

    with pytest.raises(ModelStagingError):
        depot.stage(malicious, [CONTENT])


def test_entry_for_returns_none_for_an_unregistered_bundle(tmp_path):
    depot = ModelDepot(depot_dir=tmp_path)
    assert depot.entry_for("never-registered", "checkpoint/model.safetensors") is None


def test_entry_for_finds_an_entry_after_inventory_registered_its_manifest(tmp_path):
    depot = ModelDepot(depot_dir=tmp_path)
    manifest = _manifest(_entry())
    depot.inventory(manifest)

    found = depot.entry_for(manifest.bundle_id, "checkpoint/model.safetensors")
    assert found is not None
    assert found.relative_path == "checkpoint/model.safetensors"
