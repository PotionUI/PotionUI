import hashlib
from pathlib import Path
from unittest.mock import patch

import pytest

from src.features.models.locations_repository import ModelLocationsRepository
from src.features.models.records import Model
from src.features.models.repository import ModelRepository
from src.features.models.roots import ModelRootsManager
from src.features.models.symlink_adoption import adopt_symlinked_model_roots
from src.platform.filesystem.model_roots import HOME_ROOT_ID, ModelRootResolver, RootProbe
from src.platform.filesystem.model_roots_repository import ModelRootRepository
from src.platform.settings.records import Setting, SettingType, SettingValueType
from src.platform.settings.repository import SettingRepository


class FakeIndexingCoordinator:
    def __init__(self):
        self.calls = []

    def cancel_and_restart(self, trigger="roots_change"):
        self.calls.append(trigger)
        return {"state": "idle"}

    def status(self):
        return {"state": "idle"}


class Harness:
    def __init__(self, tmp_path: Path):
        self.tmp_path = tmp_path
        self.home_dir = tmp_path / "models"
        self.home_dir.mkdir(parents=True, exist_ok=True)

        self.repository = ModelRootRepository()
        self.probe = RootProbe(ttl_seconds=0.0)
        self.resolver = ModelRootResolver(self.repository, self.probe, tmp_path)
        self.coordinator = FakeIndexingCoordinator()
        self.setting_repository = SettingRepository()
        self.roots_manager = ModelRootsManager(
            repository=self.repository,
            resolver=self.resolver,
            probe=self.probe,
            indexing_coordinator=self.coordinator,
            setting_repository=self.setting_repository,
            base_dir=tmp_path,
        )
        self.locations_repository = ModelLocationsRepository()
        self.model_repository = ModelRepository()

    def run(self) -> bool:
        return adopt_symlinked_model_roots(
            resolver=self.resolver,
            roots_manager=self.roots_manager,
            root_repository=self.repository,
            locations_repository=self.locations_repository,
            setting_repository=self.setting_repository,
            indexing_coordinator=self.coordinator,
        )

    def write_root_id_for(self, model_type: str):
        for row in self.repository.list_bindings(model_type):
            if row["is_write"]:
                return row["root_id"]
        return None

    def add_model_with_home_location(self, model_type: str, type_dir: str, filename: str) -> str:
        model = self.model_repository.create(
            Model(filename=filename, model_type=model_type, sha256=f"sha-{model_type}-{filename}")
        )
        self.locations_repository.upsert(
            model_id=model.id,
            binding_id=self.repository.bindings_for(HOME_ROOT_ID, model_type)[0]["id"],
            root_id=HOME_ROOT_ID,
            model_type=model_type,
            rel_path=filename,
            rel_key=filename,
            size=123,
            mtime_ns=1,
            sha256=model.sha256,
            status="present",
            seen_at="2026-09-28T00:00:00Z",
        )
        return model.id


@pytest.fixture
def harness(mock_db, tmp_path):
    return Harness(tmp_path)


@pytest.fixture
def require_symlinks(tmp_path_factory):
    probe_dir = tmp_path_factory.mktemp("symlink-probe")
    target = probe_dir / "target"
    target.mkdir()
    link = probe_dir / "link"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are not permitted in this environment")


class TestFullSymlinkedInstall:
    def test_two_types_under_one_comfyui_root_become_one_root(self, harness, require_symlinks):
        comfy_models = harness.tmp_path / "ComfyUI" / "models"
        (comfy_models / "loras").mkdir(parents=True)
        (comfy_models / "checkpoints").mkdir(parents=True)

        (harness.home_dir / "loras").symlink_to(comfy_models / "loras", target_is_directory=True)
        (harness.home_dir / "checkpoints").symlink_to(comfy_models / "checkpoints", target_is_directory=True)

        harness.add_model_with_home_location("lora", "loras", "a.safetensors")
        harness.add_model_with_home_location("checkpoint", "checkpoints", "b.safetensors")

        changed = harness.run()

        assert changed is True
        roots = harness.resolver.roots()
        library_roots = [r for r in roots if r.id != HOME_ROOT_ID]
        assert len(library_roots) == 1
        root = library_roots[0]
        assert Path(root.path) == comfy_models

        lora_locations = harness.locations_repository.list_for_root_type(root.id, "lora")
        checkpoint_locations = harness.locations_repository.list_for_root_type(root.id, "checkpoint")
        assert len(lora_locations) == 1
        assert len(checkpoint_locations) == 1
        assert harness.locations_repository.list_for_root_type(HOME_ROOT_ID, "lora") == []
        assert harness.locations_repository.list_for_root_type(HOME_ROOT_ID, "checkpoint") == []

        assert not (harness.home_dir / "loras").is_symlink()
        assert (harness.home_dir / "loras").is_dir()
        assert not (harness.home_dir / "checkpoints").is_symlink()

        assert harness.coordinator.calls
        assert set(harness.coordinator.calls) == {"roots_change"}

        assert harness.write_root_id_for("lora") == root.id
        assert harness.write_root_id_for("checkpoint") == root.id


class TestWriteRootFollowsTheLink:
    def test_write_probe_failure_keeps_home_as_write_root(self, harness, require_symlinks):
        comfy_models = harness.tmp_path / "ComfyUI" / "models"
        (comfy_models / "loras").mkdir(parents=True)
        (harness.home_dir / "loras").symlink_to(comfy_models / "loras", target_is_directory=True)
        harness.add_model_with_home_location("lora", "loras", "a.safetensors")

        with patch("src.features.models.roots._write_probe", return_value="permission denied"):
            changed = harness.run()

        assert changed is True
        assert harness.write_root_id_for("lora") == HOME_ROOT_ID
        assert not (harness.home_dir / "loras").is_symlink()

    def test_dangling_link_keeps_home_then_moves_once_reachable(self, harness, require_symlinks):
        drive = harness.tmp_path / "unplugged-drive"
        drive.mkdir(parents=True)
        target = drive / "loras"
        (harness.home_dir / "loras").symlink_to(target, target_is_directory=True)
        harness.add_model_with_home_location("lora", "loras", "a.safetensors")

        harness.run()
        assert harness.write_root_id_for("lora") == HOME_ROOT_ID
        assert (harness.home_dir / "loras").is_symlink()

        target.mkdir()
        harness.run()

        assert not (harness.home_dir / "loras").is_symlink()
        library_roots = [r for r in harness.resolver.roots() if r.id != HOME_ROOT_ID]
        assert len(library_roots) == 1
        assert harness.write_root_id_for("lora") == library_roots[0].id


class TestPartialOverride:
    def test_per_type_override_becomes_its_own_single_type_root(self, harness, require_symlinks):
        comfy_models = harness.tmp_path / "ComfyUI" / "models"
        (comfy_models / "loras").mkdir(parents=True)
        (comfy_models / "checkpoints").mkdir(parents=True)
        elsewhere = harness.tmp_path / "elsewhere-vae"
        elsewhere.mkdir(parents=True)

        (harness.home_dir / "loras").symlink_to(comfy_models / "loras", target_is_directory=True)
        (harness.home_dir / "checkpoints").symlink_to(comfy_models / "checkpoints", target_is_directory=True)
        (harness.home_dir / "vae").symlink_to(elsewhere, target_is_directory=True)

        harness.add_model_with_home_location("lora", "loras", "a.safetensors")
        harness.add_model_with_home_location("checkpoint", "checkpoints", "b.safetensors")
        harness.add_model_with_home_location("vae", "vae", "c.safetensors")

        changed = harness.run()

        assert changed is True
        roots = {r.id: r for r in harness.resolver.roots() if r.id != HOME_ROOT_ID}
        assert len(roots) == 2

        vae_root = next(r for r in roots.values() if Path(r.path) == elsewhere)
        vae_bindings = harness.repository.bindings_for_root(vae_root.id)
        assert len(vae_bindings) == 1
        assert vae_bindings[0]["subdir"] == ""

        comfy_root = next(r for r in roots.values() if Path(r.path) == comfy_models)
        comfy_bindings = {b["model_type"]: b["subdir"] for b in harness.repository.bindings_for_root(comfy_root.id)}
        assert comfy_bindings == {"lora": "loras", "checkpoint": "checkpoints"}


class TestDanglingLink:
    def test_dangling_link_creates_offline_root_and_keeps_the_link(self, harness, require_symlinks):
        missing_target = harness.tmp_path / "unplugged-drive" / "loras"
        (harness.tmp_path / "unplugged-drive").mkdir(parents=True)

        (harness.home_dir / "loras").symlink_to(missing_target, target_is_directory=True)
        harness.add_model_with_home_location("lora", "loras", "a.safetensors")

        changed = harness.run()

        assert (harness.home_dir / "loras").is_symlink()

        roots = [r for r in harness.resolver.roots() if r.id != HOME_ROOT_ID]
        assert len(roots) == 1
        assert roots[0].state == "offline"
        assert Path(roots[0].path) == missing_target

        locations = harness.locations_repository.list_for_root_type(roots[0].id, "lora")
        assert len(locations) == 1
        assert changed is True


class TestIdempotency:
    def test_second_run_is_a_noop(self, harness, require_symlinks):
        comfy_models = harness.tmp_path / "ComfyUI" / "models"
        (comfy_models / "loras").mkdir(parents=True)
        (harness.home_dir / "loras").symlink_to(comfy_models / "loras", target_is_directory=True)
        harness.add_model_with_home_location("lora", "loras", "a.safetensors")

        first = harness.run()
        harness.coordinator.calls.clear()
        second = harness.run()

        assert first is True
        assert second is False
        assert harness.coordinator.calls == []
        roots = [r for r in harness.resolver.roots() if r.id != HOME_ROOT_ID]
        assert len(roots) == 1


class TestNoRehash:
    def test_rehoming_locations_never_hashes(self, harness, require_symlinks):
        comfy_models = harness.tmp_path / "ComfyUI" / "models"
        (comfy_models / "loras").mkdir(parents=True)
        (harness.home_dir / "loras").symlink_to(comfy_models / "loras", target_is_directory=True)
        model_id = harness.add_model_with_home_location("lora", "loras", "a.safetensors")

        with patch.object(hashlib, "sha256", side_effect=AssertionError("must not hash during adoption")):
            harness.run()

        model = harness.model_repository.get_by_id(model_id)
        assert model.sha256 == "sha-lora-a.safetensors"


class TestRemovalFailureTolerated:
    def test_unremovable_link_is_left_in_place_and_retried(self, harness, require_symlinks):
        comfy_models = harness.tmp_path / "ComfyUI" / "models"
        (comfy_models / "loras").mkdir(parents=True)
        (harness.home_dir / "loras").symlink_to(comfy_models / "loras", target_is_directory=True)
        harness.add_model_with_home_location("lora", "loras", "a.safetensors")

        with patch("src.features.models.symlink_adoption._remove_link", side_effect=OSError("permission denied")):
            changed = harness.run()

        assert changed is True
        assert (harness.home_dir / "loras").is_symlink()
        assert harness.coordinator.calls
        assert set(harness.coordinator.calls) == {"roots_change"}

        harness.coordinator.calls.clear()
        second_changed = harness.run()
        assert second_changed is True
        assert not (harness.home_dir / "loras").is_symlink()


class TestLegacySettingsCleanup:
    def test_legacy_settings_are_deleted_after_full_conversion(self, harness, require_symlinks):
        harness.setting_repository.create_setting(
            "models_location_external_path", "/mnt/ComfyUI", SettingValueType.STRING, setting_type=SettingType.SYSTEM
        )
        harness.setting_repository.create_setting(
            "models_location_overrides", "{}", SettingValueType.JSON, setting_type=SettingType.SYSTEM
        )

        comfy_models = harness.tmp_path / "ComfyUI" / "models"
        (comfy_models / "loras").mkdir(parents=True)
        (harness.home_dir / "loras").symlink_to(comfy_models / "loras", target_is_directory=True)
        harness.add_model_with_home_location("lora", "loras", "a.safetensors")

        harness.run()

        assert harness.setting_repository.get_setting_by_key("models_location_external_path") is None
        assert harness.setting_repository.get_setting_by_key("models_location_overrides") is None

    def test_settings_are_kept_when_a_link_cannot_be_removed(self, harness, require_symlinks):
        harness.setting_repository.create_setting(
            "models_location_external_path", "/mnt/ComfyUI", SettingValueType.STRING, setting_type=SettingType.SYSTEM
        )
        comfy_models = harness.tmp_path / "ComfyUI" / "models"
        (comfy_models / "loras").mkdir(parents=True)
        (harness.home_dir / "loras").symlink_to(comfy_models / "loras", target_is_directory=True)
        harness.add_model_with_home_location("lora", "loras", "a.safetensors")

        with patch("src.features.models.symlink_adoption._remove_link", side_effect=OSError("permission denied")):
            harness.run()

        assert harness.setting_repository.get_setting_by_key("models_location_external_path") is not None


class TestNoLinksIsANoop:
    def test_fresh_install_with_no_links_does_nothing(self, harness):
        changed = harness.run()

        assert changed is False
        assert harness.coordinator.calls == []
        assert [r for r in harness.resolver.roots() if r.id != HOME_ROOT_ID] == []


class TestWindowsSeam:
    def test_removal_uses_rmdir_on_windows(self, tmp_path):
        from src.features.models.symlink_adoption import _remove_link

        target_dir = tmp_path / "junction-target"
        target_dir.mkdir()
        link_path = tmp_path / "link"

        with patch("src.features.models.symlink_adoption.is_windows", return_value=True):
            with patch.object(Path, "rmdir") as mock_rmdir, patch.object(Path, "unlink") as mock_unlink:
                _remove_link(link_path)

        mock_rmdir.assert_called_once()
        mock_unlink.assert_not_called()

    def test_removal_uses_unlink_off_windows(self, tmp_path):
        from src.features.models.symlink_adoption import _remove_link

        link_path = tmp_path / "link"

        with patch("src.features.models.symlink_adoption.is_windows", return_value=False):
            with patch.object(Path, "rmdir") as mock_rmdir, patch.object(Path, "unlink") as mock_unlink:
                _remove_link(link_path)

        mock_unlink.assert_called_once()
        mock_rmdir.assert_not_called()


class TestWindowsLongPathPrefix:
    def test_readlink_long_path_prefix_is_stripped(self, tmp_path):
        from src.features.models.symlink_adoption import _resolve_link_target

        link_path = tmp_path / "link"

        with patch(
            "src.features.models.symlink_adoption.os.readlink",
            return_value=r"\\?\C:\Users\runneradmin\ComfyUI\models\loras",
        ):
            target = _resolve_link_target(link_path)

        assert target is not None
        assert "\\\\?\\" not in str(target)
        assert str(target).endswith(r"C:\Users\runneradmin\ComfyUI\models\loras")

    def test_resolve_fallback_long_path_prefix_is_stripped(self, tmp_path):
        from src.features.models.symlink_adoption import _resolve_link_target

        link_path = tmp_path / "link"
        long_form = Path(r"\\?\C:\Users\runneradmin\ComfyUI\models\loras")

        with patch(
            "src.features.models.symlink_adoption.os.readlink", side_effect=OSError("no symlink")
        ), patch.object(Path, "resolve", return_value=long_form):
            target = _resolve_link_target(link_path)

        assert target is not None
        assert "\\\\?\\" not in str(target)


class TestGroupByParent:
    def test_common_parent_groups_two_types_into_one_root(self):
        from src.features.models.symlink_adoption import _group_by_parent

        targets = {
            "lora": Path("/mnt/ComfyUI/models/loras"),
            "checkpoint": Path("/mnt/ComfyUI/models/checkpoints"),
        }

        groups = _group_by_parent(targets)

        assert len(groups) == 1
        root_path, bindings = groups[0]
        assert root_path == Path("/mnt/ComfyUI/models")
        assert bindings == {"lora": "loras", "checkpoint": "checkpoints"}

    def test_lone_target_becomes_its_own_root_with_empty_subdir(self):
        from src.features.models.symlink_adoption import _group_by_parent

        targets = {"lora": Path("/srv/my-loras")}

        groups = _group_by_parent(targets)

        assert groups == [(Path("/srv/my-loras"), {"lora": ""})]
