import os
from pathlib import Path
from unittest.mock import Mock

import pytest

from src.features.models.roots import (
    BindingSpec,
    DuplicateRootError,
    GenerationActiveError,
    HomeProtectedError,
    InvalidBindingError,
    ModelRootsManager,
    RootNotFoundError,
    RootOfflineError,
    RootOverlapError,
    RootReadOnlyRefusalError,
    WriteProbeFailedError,
)
from src.platform.filesystem.model_roots import HOME_ROOT_ID, ModelRootResolver, RootProbe
from src.platform.filesystem.model_roots_repository import ModelRootRepository
from src.platform.settings.repository import SettingRepository


class FakeIndexingCoordinator:
    def __init__(self):
        self.calls = []

    def cancel_and_restart(self, trigger="location_change"):
        self.calls.append(trigger)
        return {"state": "idle"}

    def status(self):
        return {"state": "idle"}


@pytest.fixture
def manager(mock_db, tmp_path):
    repository = ModelRootRepository()
    probe = RootProbe(ttl_seconds=0.0)
    resolver = ModelRootResolver(repository, probe, tmp_path)
    coordinator = FakeIndexingCoordinator()
    mgr = ModelRootsManager(
        repository=repository,
        resolver=resolver,
        probe=probe,
        indexing_coordinator=coordinator,
        setting_repository=SettingRepository(),
        base_dir=tmp_path,
    )
    mgr.coordinator = coordinator
    return mgr


@pytest.fixture
def library_dir(tmp_path):
    root_dir = tmp_path / "library"
    (root_dir / "loras").mkdir(parents=True)
    (root_dir / "checkpoints").mkdir(parents=True)
    return root_dir


class TestCreateRoot:
    def test_creates_a_root_with_bindings(self, manager, library_dir):
        result = manager.create_root(
            str(library_dir),
            label="Library",
            bindings=[BindingSpec("lora", "loras"), BindingSpec("checkpoint", "checkpoints")],
        )

        assert result["label"] == "Library"
        types = {b["model_type"] for b in result["bindings"]}
        assert types == {"lora", "checkpoint"}
        assert manager.coordinator.calls == ["roots_change"]

    def test_is_idempotent_by_path_key(self, manager, library_dir):
        first = manager.create_root(str(library_dir), bindings=[BindingSpec("lora", "loras")])
        second = manager.create_root(str(library_dir), bindings=[BindingSpec("lora", "loras")])

        assert first["id"] == second["id"]

    def test_not_idempotent_raises_duplicate(self, manager, library_dir):
        manager.create_root(str(library_dir), bindings=[BindingSpec("lora", "loras")])

        with pytest.raises(DuplicateRootError):
            manager.create_root(str(library_dir), bindings=[BindingSpec("lora", "loras")], idempotent=False)

    def test_missing_subdir_is_refused(self, manager, library_dir):
        with pytest.raises(InvalidBindingError):
            manager.create_root(str(library_dir), bindings=[BindingSpec("vae", "vae")])

    def test_overlap_with_another_root_is_refused(self, manager, library_dir):
        manager.create_root(str(library_dir), bindings=[BindingSpec("lora", "loras")])

        with pytest.raises(RootOverlapError):
            manager.create_root(
                str(library_dir.parent), bindings=[BindingSpec("lora", "library/loras")]
            )

    def test_write_types_sets_the_write_binding(self, manager, library_dir):
        result = manager.create_root(
            str(library_dir), bindings=[BindingSpec("lora", "loras")], write_types=["lora"]
        )

        binding = next(b for b in result["bindings"] if b["model_type"] == "lora")
        assert binding["is_write"] is True

    def test_write_types_refused_when_read_only(self, manager, library_dir):
        with pytest.raises(RootReadOnlyRefusalError):
            manager.create_root(
                str(library_dir),
                bindings=[BindingSpec("lora", "loras")],
                read_only=True,
                write_types=["lora"],
            )


class TestUpdateRoot:
    def test_relink_keeps_id_and_updates_path(self, manager, library_dir, tmp_path):
        created = manager.create_root(str(library_dir), bindings=[BindingSpec("lora", "loras")])
        new_dir = tmp_path / "relinked"
        (new_dir / "loras").mkdir(parents=True)

        updated = manager.update_root(created["id"], path=str(new_dir))

        assert updated["id"] == created["id"]
        assert updated["path"] == str(new_dir)

    def test_refuses_path_change_while_generation_active(self, manager, library_dir, tmp_path):
        created = manager.create_root(str(library_dir), bindings=[BindingSpec("lora", "loras")])
        manager._generation_active = lambda: True
        new_dir = tmp_path / "relinked"
        (new_dir / "loras").mkdir(parents=True)

        with pytest.raises(GenerationActiveError):
            manager.update_root(created["id"], path=str(new_dir))

    def test_label_change_allowed_while_generation_active(self, manager, library_dir):
        created = manager.create_root(str(library_dir), bindings=[BindingSpec("lora", "loras")])
        manager._generation_active = lambda: True

        updated = manager.update_root(created["id"], label="Renamed")

        assert updated["label"] == "Renamed"

    def test_home_cannot_be_marked_read_only(self, manager):
        with pytest.raises(HomeProtectedError):
            manager.update_root(HOME_ROOT_ID, read_only=True)

    def test_unknown_root_raises_not_found(self, manager):
        with pytest.raises(RootNotFoundError):
            manager.update_root("does-not-exist", label="x")


class TestRemoveBindingType:
    def test_removes_the_binding(self, manager, library_dir):
        created = manager.create_root(
            str(library_dir),
            bindings=[BindingSpec("lora", "loras"), BindingSpec("checkpoint", "checkpoints")],
        )

        updated = manager.update_root(created["id"], remove_types=["lora"])

        types = {b["model_type"] for b in updated["bindings"]}
        assert types == {"checkpoint"}

    def test_write_root_falls_back_to_home(self, manager, library_dir):
        created = manager.create_root(
            str(library_dir), bindings=[BindingSpec("lora", "loras")], write_types=["lora"]
        )

        manager.update_root(created["id"], remove_types=["lora"])

        overview = manager.get_overview()
        lora_type = next(t for t in overview["types"] if t["model_type"] == "lora")
        assert lora_type["write_root_id"] == HOME_ROOT_ID

    def test_removed_binding_locations_are_deleted(self, manager, library_dir):
        created = manager.create_root(str(library_dir), bindings=[BindingSpec("lora", "loras")])
        manager._locations.delete_for_root_and_type = Mock(wraps=manager._locations.delete_for_root_and_type)

        manager.update_root(created["id"], remove_types=["lora"])

        manager._locations.delete_for_root_and_type.assert_called_once_with(created["id"], "lora")

    def test_home_bindings_cannot_be_removed(self, manager):
        with pytest.raises(HomeProtectedError):
            manager.update_root(HOME_ROOT_ID, remove_types=["lora"])

    def test_refuses_while_generation_active(self, manager, library_dir):
        created = manager.create_root(str(library_dir), bindings=[BindingSpec("lora", "loras")])
        manager._generation_active = lambda: True

        with pytest.raises(GenerationActiveError):
            manager.update_root(created["id"], remove_types=["lora"])

    def test_unknown_type_is_a_no_op(self, manager, library_dir):
        created = manager.create_root(str(library_dir), bindings=[BindingSpec("lora", "loras")])

        updated = manager.update_root(created["id"], remove_types=["checkpoint"])

        types = {b["model_type"] for b in updated["bindings"]}
        assert types == {"lora"}


class TestDeleteRoot:
    def test_home_is_protected(self, manager):
        with pytest.raises(HomeProtectedError):
            manager.delete_root(HOME_ROOT_ID)

    def test_refuses_while_generation_active(self, manager, library_dir):
        created = manager.create_root(str(library_dir), bindings=[BindingSpec("lora", "loras")])
        manager._generation_active = lambda: True

        with pytest.raises(GenerationActiveError):
            manager.delete_root(created["id"])

    def test_deletes_root_and_cascades_bindings(self, manager, library_dir):
        created = manager.create_root(str(library_dir), bindings=[BindingSpec("lora", "loras")])

        manager.delete_root(created["id"])

        assert manager._repository.get_root(created["id"]) is None
        assert all(b["root_id"] != created["id"] for b in manager._repository.list_bindings())


class TestReorder:
    def test_reorders_a_single_type(self, manager, library_dir, tmp_path):
        lib_a = manager.create_root(str(library_dir), bindings=[BindingSpec("lora", "loras")])
        other_dir = tmp_path / "other"
        (other_dir / "loras").mkdir(parents=True)
        lib_b = manager.create_root(str(other_dir), bindings=[BindingSpec("lora", "loras")])

        manager.reorder("lora", [lib_b["id"], lib_a["id"], HOME_ROOT_ID])

        overview = manager.get_overview()
        lora_type = next(t for t in overview["types"] if t["model_type"] == "lora")
        assert lora_type["order"][0] == lib_b["id"]
        assert lora_type["order"][1] == lib_a["id"]

    def test_duplicate_root_ids_refused(self, manager, library_dir):
        created = manager.create_root(str(library_dir), bindings=[BindingSpec("lora", "loras")])

        with pytest.raises(InvalidBindingError):
            manager.reorder("lora", [created["id"], created["id"]])

    def test_unknown_root_for_explicit_type_refused(self, manager):
        with pytest.raises(InvalidBindingError):
            manager.reorder("lora", [HOME_ROOT_ID, "not-a-root"])


class TestSetWriteRoot:
    def test_sets_write_root(self, manager, library_dir):
        created = manager.create_root(str(library_dir), bindings=[BindingSpec("lora", "loras")])

        result = manager.set_write_root("lora", created["id"])

        binding = next(b for b in result["bindings"] if b["model_type"] == "lora")
        assert binding["is_write"] is True

    def test_refuses_read_only_root(self, manager, library_dir):
        created = manager.create_root(
            str(library_dir), bindings=[BindingSpec("lora", "loras")], read_only=True
        )

        with pytest.raises(RootReadOnlyRefusalError):
            manager.set_write_root("lora", created["id"])

    def test_refuses_offline_root(self, manager, library_dir, tmp_path):
        created = manager.create_root(str(library_dir), bindings=[BindingSpec("lora", "loras")])
        import shutil
        shutil.rmtree(library_dir)

        with pytest.raises(RootOfflineError):
            manager.set_write_root("lora", created["id"])

    @pytest.mark.skipif(os.name == "nt" or os.geteuid() == 0, reason="permission probes need a real non-root user")
    def test_write_probe_failure_is_reported(self, manager, library_dir):
        created = manager.create_root(str(library_dir), bindings=[BindingSpec("lora", "loras")])
        loras_dir = library_dir / "loras"
        os.chmod(loras_dir, 0o500)
        try:
            with pytest.raises(WriteProbeFailedError):
                manager.set_write_root("lora", created["id"])
        finally:
            os.chmod(loras_dir, 0o700)


class TestProbeRoot:
    def test_probe_refreshes_state(self, manager, library_dir):
        created = manager.create_root(str(library_dir), bindings=[BindingSpec("lora", "loras")])

        result = manager.probe_root(created["id"])

        assert result["state"] == "online"


class TestOverview:
    def test_overview_has_the_expected_shape(self, manager, library_dir):
        manager.create_root(str(library_dir), bindings=[BindingSpec("lora", "loras")])

        overview = manager.get_overview()

        assert "roots" in overview
        assert "types" in overview
        assert "unplaced" in overview
        assert "indexing" in overview
        assert any(r["id"] == HOME_ROOT_ID for r in overview["roots"])

    def test_unplaced_suggestions_covered_by_a_root_are_hidden(self, manager, library_dir, tmp_path):
        import json

        from src.platform.settings.records import SettingType, SettingValueType

        manager.create_root(str(library_dir), bindings=[BindingSpec("lora", "loras")])
        outside = tmp_path / "elsewhere"
        entries = [
            {"dir": str(library_dir / "loras" / "sdxl"), "count": 3, "types": ["lora"]},
            {"dir": str(outside), "count": 2, "types": ["lora"]},
        ]
        settings = SettingRepository()
        existing = settings.get_setting_by_key("model_roots_unplaced")
        if existing:
            settings.update_setting_value(existing.id, json.dumps(entries))
        else:
            settings.create_setting(
                "model_roots_unplaced", json.dumps(entries), SettingValueType.JSON, setting_type=SettingType.SYSTEM
            )

        overview = manager.get_overview()

        assert [entry["dir"] for entry in overview["unplaced"]] == [str(outside)]


class TestSyncHomeFromSetting:
    def test_updates_home_path_when_setting_changes(self, manager, tmp_path):
        from src.platform.settings.records import SettingType, SettingValueType

        settings = SettingRepository()
        existing = settings.get_setting_by_key("models_dir")
        if existing:
            settings.update_setting_value(existing.id, "custom-models")
        else:
            settings.create_setting(
                "models_dir", "custom-models", SettingValueType.STRING, setting_type=SettingType.SYSTEM
            )

        manager.sync_home_from_setting()

        home = manager._repository.get_root(HOME_ROOT_ID)
        assert home["path"] == "custom-models"


class TestEnsureHomeBindingsForNewType(object):
    def test_new_model_type_gets_a_home_binding(self, mock_db, monkeypatch):
        import src.platform.filesystem.model_roots as model_roots_module
        from src.platform.filesystem.model_roots_repository import ModelRootRepository

        monkeypatch.setattr(model_roots_module, "MODEL_TYPES", tuple(model_roots_module.MODEL_TYPES) + ("widget",))
        monkeypatch.setitem(model_roots_module.MODEL_TYPE_TO_DIRECTORY, "widget", "widgets")

        repository = ModelRootRepository()
        model_roots_module.ensure_home_bindings(repository, "models", base_dir=Path("/tmp"))

        bindings = repository.list_bindings()
        widget_binding = next((b for b in bindings if b["model_type"] == "widget" and b["root_id"] == HOME_ROOT_ID), None)
        assert widget_binding is not None
        assert widget_binding["subdir"] == "widgets"
