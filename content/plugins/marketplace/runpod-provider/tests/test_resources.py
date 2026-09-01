"""`backend.resources.RunPodResourceManager` against a scratch sqlite db."""

from backend.resources import RunPodResourceManager


def _manager(scratch_db, monkeypatch) -> RunPodResourceManager:
    monkeypatch.setattr("src.platform.database.database.db", scratch_db)
    manager = RunPodResourceManager()
    manager.create_table()
    return manager


def test_record_and_get_round_trip(scratch_db, monkeypatch):
    manager = _manager(scratch_db, monkeypatch)

    manager.record("profile-a", "pod", "pod-123", meta={"worker_port": 8100})

    record = manager.get("profile-a", "pod")
    assert record is not None
    assert record.runpod_id == "pod-123"
    assert record.meta == {"worker_port": 8100}


def test_get_missing_returns_none(scratch_db, monkeypatch):
    manager = _manager(scratch_db, monkeypatch)
    assert manager.get("nope", "pod") is None


def test_record_upserts_on_same_profile_and_type(scratch_db, monkeypatch):
    manager = _manager(scratch_db, monkeypatch)

    manager.record("profile-a", "pod", "pod-123")
    manager.record("profile-a", "pod", "pod-456")

    records = manager.list_for_profile("profile-a")
    pod_records = [r for r in records if r.resource_type == "pod"]
    assert len(pod_records) == 1
    assert pod_records[0].runpod_id == "pod-456"


def test_pod_and_volume_tracked_independently_for_same_profile(scratch_db, monkeypatch):
    manager = _manager(scratch_db, monkeypatch)

    manager.record("profile-a", "pod", "pod-123")
    manager.record("profile-a", "network_volume", "vol-1")

    assert manager.get("profile-a", "pod").runpod_id == "pod-123"
    assert manager.get("profile-a", "network_volume").runpod_id == "vol-1"


def test_delete_removes_only_the_named_resource(scratch_db, monkeypatch):
    manager = _manager(scratch_db, monkeypatch)
    manager.record("profile-a", "pod", "pod-123")
    manager.record("profile-a", "network_volume", "vol-1")

    deleted = manager.delete("profile-a", "pod")

    assert deleted is True
    assert manager.get("profile-a", "pod") is None
    assert manager.get("profile-a", "network_volume") is not None
