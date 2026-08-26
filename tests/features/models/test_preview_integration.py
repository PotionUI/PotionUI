"""End-to-end integration for the admin-set model preview.

Exercises the real DB + repositories: set a preview from an uploaded file, then
prove (a) it round-trips on the model, (b) a real `files` row is created and
referenced via the auth-exempt /api/media/files/<id> route, (c) that file id
resolves back to the bytes on disk exactly as the media serve route does, and
(d) an image preview gets thumbnails so cards/pickers don't fetch the full-res
original for a small `?size=` request.
"""

import shutil
import tempfile
from pathlib import Path
from unittest.mock import Mock

import pytest
from PIL import Image

from tests.fixtures.persistence_base import PersistenceTestBase
from src.features.models.repository import ModelRepository
from src.features.models import operations
from src.features.models.collaborators import build_model_index_collaborators
from src.features.models.records import Model
from src.features.models.exceptions import ModelNotFoundException
from src.features.generation.file_repository import file_repo
from src.platform.filesystem.file_store import FileStore
from src.platform.security.user import User, AccountType


class TestModelPreviewIntegration(PersistenceTestBase):
    def setUp(self):
        super().setUp()
        # Repositories bind the module-level `db` at import; redirect the ones
        # this flow touches at the same key PersistenceTestBase uses.
        import src.features.models.repository as model_repository_module
        model_repository_module.db = self.db
        import src.features.tags.repository as tag_repository_module
        tag_repository_module.db = self.db

        self.repo = ModelRepository()

        # Storage lives outside self.temp_dir: PersistenceTestBase.tearDown rmdir()s
        # temp_dir and would choke on our files there.
        self._storage_root = tempfile.mkdtemp()
        self.storage = Path(self._storage_root)
        (self.storage / "uploads").mkdir(parents=True)

        self.settings = Mock()
        self.settings.get_file_storage_directory.return_value = str(self.storage)
        self.settings.get_models_media_directory.return_value = str(self.storage / "models")

        # files.user_id has a FK to users(id); the admin who sets a preview is a
        # real user in production, so seed one here.
        self.admin_id = self.create_test_user(user_id="admin", username="admin", email="admin@x.com")

        from src.features.tags.repository import tag_repo
        # models_root: without it, `__init__` falls through to the real, lazily
        # -constructed module-level scanner singleton to resolve models_dir,
        # which hits the settings DB for real.
        self.collaborators = build_model_index_collaborators(
            self.repo, tag_repo, Mock(), self.settings, Mock(), models_root=self.storage
        )

    def tearDown(self):
        shutil.rmtree(self._storage_root, ignore_errors=True)
        super().tearDown()

    def _write_upload(self, name="img.png", size=(1200, 900), color=(10, 120, 200)):
        path = self.storage / "uploads" / name
        Image.new("RGB", size, color).save(path, "PNG")
        return f"uploads/{name}"

    def _seed_model(self):
        return self.repo.create(Model(
            filename="detail.safetensors",
            file_path="/models/loras/detail.safetensors",
            model_type="lora",
        ))

    def test_set_preview_roundtrips_and_serves_via_files_route(self):
        model = self._seed_model()
        source = self._write_upload()

        operations.update_model_preview(self.collaborators, 
            model.id, {"source_path": source, "type": "image", "name": "img.png"}, user_id="admin"
        )

        reloaded = self.repo.get_by_id(model.id)
        preview = reloaded.preview_media
        assert preview is not None, "preview_media did not round-trip on the model"
        file_id = preview["file_id"]
        assert preview["url"] == f"/api/media/files/{file_id}"
        assert preview["type"] == "image"

        # The files row exists and points at the uploaded bytes.
        file_record = file_repo.get_by_id(file_id)
        assert file_record is not None
        assert file_record.file_path == source
        assert file_record.file_type == "IMAGE"

        # Resolve exactly as media serve does (get_file_by_id): storage of the
        # file's own user, then FileStore.get_full_path(relative). Must be the file.
        storage_dir = self.settings.get_file_storage_directory(file_record.user_id)
        full = Path(FileStore(storage_dir).get_full_path(file_record.file_path))
        assert full.is_file(), f"serve path did not resolve to the uploaded file: {full}"

    def test_image_preview_gets_thumbnails(self):
        """A small ?size= request must not fall back to the full-res original."""
        model = self._seed_model()
        source = self._write_upload()

        operations.update_model_preview(self.collaborators, 
            model.id, {"source_path": source, "type": "image"}, user_id="admin"
        )

        file_id = self.repo.get_by_id(model.id).preview_media["file_id"]
        file_record = file_repo.get_by_id(file_id)

        assert file_record.thumbnail_small, "image preview has no small thumbnail"
        storage_dir = self.settings.get_file_storage_directory(file_record.user_id)
        original_dir = Path(FileStore(storage_dir).get_full_path(file_record.file_path)).parent
        thumb = original_dir / file_record.thumbnail_small
        assert thumb.is_file(), f"thumbnail file missing on disk: {thumb}"

    def test_replace_deletes_previous_files_row(self):
        model = self._seed_model()
        first = operations.update_model_preview(self.collaborators, 
            model.id, {"source_path": self._write_upload("a.png"), "type": "image"}, user_id="admin"
        )
        first_file_id = first["model"]["preview_media"]["file_id"]

        operations.update_model_preview(self.collaborators, 
            model.id, {"source_path": self._write_upload("b.png"), "type": "image"}, user_id="admin"
        )

        assert file_repo.get_by_id(first_file_id) is None, "previous preview files row was not deleted"

    def test_video_preview_has_no_thumbnail(self):
        """Video sources can't be image-thumbnailed; the row simply carries none."""
        model = self._seed_model()
        # A non-image byte blob standing in for a video file.
        (self.storage / "uploads" / "clip.mp4").write_bytes(b"\x00\x01\x02fake-mp4")

        operations.update_model_preview(self.collaborators, 
            model.id, {"source_path": "uploads/clip.mp4", "type": "video"}, user_id="admin"
        )

        file_id = self.repo.get_by_id(model.id).preview_media["file_id"]
        file_record = file_repo.get_by_id(file_id)
        assert file_record.file_type == "VIDEO"
        assert not file_record.thumbnail_small


class TestModelPreviewListIntegration(PersistenceTestBase):
    """Multiple admin-set previews per model, via the model_preview_media list.

    Mirrors TestModelPreviewIntegration's fixtures. Exercises the real DB so the
    position-0-mirrors-column contract and the legacy-seeding path are proven
    end-to-end, not mocked.
    """

    def setUp(self):
        super().setUp()
        import src.features.models.repository as model_repository_module
        model_repository_module.db = self.db
        import src.features.tags.repository as tag_repository_module
        tag_repository_module.db = self.db

        self.repo = ModelRepository()

        self._storage_root = tempfile.mkdtemp()
        self.storage = Path(self._storage_root)
        (self.storage / "uploads").mkdir(parents=True)

        self.settings = Mock()
        self.settings.get_file_storage_directory.return_value = str(self.storage)
        self.settings.get_models_media_directory.return_value = str(self.storage / "models")

        self.admin_id = self.create_test_user(user_id="admin", username="admin", email="admin@x.com")

        from src.features.tags.repository import tag_repo
        # models_root: without it, `__init__` falls through to the real, lazily
        # -constructed module-level scanner singleton to resolve models_dir,
        # which hits the settings DB for real.
        self.collaborators = build_model_index_collaborators(
            self.repo, tag_repo, Mock(), self.settings, Mock(), models_root=self.storage
        )

    def tearDown(self):
        shutil.rmtree(self._storage_root, ignore_errors=True)
        super().tearDown()

    def _write_upload(self, name="img.png", size=(400, 300), color=(10, 120, 200)):
        path = self.storage / "uploads" / name
        Image.new("RGB", size, color).save(path, "PNG")
        return f"uploads/{name}"

    def _seed_model(self):
        return self.repo.create(Model(
            filename="detail.safetensors",
            file_path="/models/loras/detail.safetensors",
            model_type="lora",
        ))

    def test_add_multiple_previews_appends_in_order(self):
        model = self._seed_model()
        first = operations.add_model_preview(self.collaborators, 
            model.id, {"source_path": self._write_upload("a.png"), "type": "image"}, user_id="admin"
        )
        second = operations.add_model_preview(self.collaborators, 
            model.id, {"source_path": self._write_upload("b.png"), "type": "image"}, user_id="admin"
        )

        assert [p["position"] for p in second["previews"]] == [0, 1]
        assert first["id"] in [p["id"] for p in second["previews"]]

    def test_first_add_mirrors_into_legacy_column(self):
        """Requirement: the first/primary preview keeps working via model.preview_media."""
        model = self._seed_model()
        operations.add_model_preview(self.collaborators, 
            model.id, {"source_path": self._write_upload(), "type": "image"}, user_id="admin"
        )

        reloaded = self.repo.get_by_id(model.id)
        assert reloaded.preview_media is not None
        assert reloaded.preview_media["type"] == "image"

    def test_delete_non_primary_leaves_primary_column_untouched(self):
        model = self._seed_model()
        operations.add_model_preview(self.collaborators, 
            model.id, {"source_path": self._write_upload("a.png"), "type": "image"}, user_id="admin"
        )
        added_second = operations.add_model_preview(self.collaborators, 
            model.id, {"source_path": self._write_upload("b.png"), "type": "image"}, user_id="admin"
        )
        primary_before = self.repo.get_by_id(model.id).preview_media["file_id"]

        operations.delete_model_preview(self.collaborators, model.id, added_second["id"])

        primary_after = self.repo.get_by_id(model.id).preview_media["file_id"]
        assert primary_after == primary_before

    def test_delete_primary_promotes_next_and_deletes_its_file_row(self):
        model = self._seed_model()
        added_first = operations.add_model_preview(self.collaborators, 
            model.id, {"source_path": self._write_upload("a.png"), "type": "image"}, user_id="admin"
        )
        first_file_id = operations.list_model_previews(self.collaborators, model.id)[0]["file_id"]
        operations.add_model_preview(self.collaborators, 
            model.id, {"source_path": self._write_upload("b.png"), "type": "image"}, user_id="admin"
        )

        result = operations.delete_model_preview(self.collaborators, model.id, added_first["id"])

        assert file_repo.get_by_id(first_file_id) is None, "deleted preview's files row was not dropped"
        assert [p["position"] for p in result["previews"]] == [0]
        reloaded = self.repo.get_by_id(model.id)
        assert reloaded.preview_media["file_id"] == result["previews"][0]["file_id"]

    def test_delete_last_preview_clears_legacy_column(self):
        model = self._seed_model()
        added = operations.add_model_preview(self.collaborators, 
            model.id, {"source_path": self._write_upload(), "type": "image"}, user_id="admin"
        )

        operations.delete_model_preview(self.collaborators, model.id, added["id"])

        assert self.repo.get_by_id(model.id).preview_media is None
        assert operations.list_model_previews(self.collaborators, model.id) == []

    def test_reorder_promotes_a_different_preview_to_primary(self):
        model = self._seed_model()
        first = operations.add_model_preview(self.collaborators, 
            model.id, {"source_path": self._write_upload("a.png"), "type": "image"}, user_id="admin"
        )
        second = operations.add_model_preview(self.collaborators, 
            model.id, {"source_path": self._write_upload("b.png"), "type": "image"}, user_id="admin"
        )
        second_file_id = operations.list_model_previews(self.collaborators, model.id)[1]["file_id"]

        operations.reorder_model_previews(self.collaborators, model.id, [second["id"], first["id"]])

        reloaded = self.repo.get_by_id(model.id)
        assert reloaded.preview_media["file_id"] == second_file_id

    def test_reorder_rejects_mismatched_id_set(self):
        from src.features.models.exceptions import ModelIndexingException

        model = self._seed_model()
        operations.add_model_preview(self.collaborators, 
            model.id, {"source_path": self._write_upload(), "type": "image"}, user_id="admin"
        )

        with pytest.raises(ModelIndexingException):
            operations.reorder_model_previews(self.collaborators, model.id, ["not-a-real-id"])

    def test_list_lazily_seeds_from_legacy_single_preview(self):
        """A model set via the old single-preview endpoint has no list rows yet;
        the list endpoint must still surface it as the sole/primary entry."""
        model = self._seed_model()
        operations.update_model_preview(self.collaborators, 
            model.id, {"source_path": self._write_upload(), "type": "image"}, user_id="admin"
        )

        previews = operations.list_model_previews(self.collaborators, model.id)

        assert len(previews) == 1
        assert previews[0]["position"] == 0
        legacy_file_id = self.repo.get_by_id(model.id).preview_media["file_id"]
        assert previews[0]["file_id"] == legacy_file_id

    def test_add_after_legacy_seed_appends_at_position_one(self):
        model = self._seed_model()
        operations.update_model_preview(self.collaborators, 
            model.id, {"source_path": self._write_upload("legacy.png"), "type": "image"}, user_id="admin"
        )

        operations.add_model_preview(self.collaborators, 
            model.id, {"source_path": self._write_upload("new.png"), "type": "image"}, user_id="admin"
        )

        previews = operations.list_model_previews(self.collaborators, model.id)
        assert [p["position"] for p in previews] == [0, 1]
        # The legacy single-set preview stays primary; unchanged column contract.
        legacy_file_id = self.repo.get_by_id(model.id).preview_media["file_id"]
        assert previews[0]["file_id"] == legacy_file_id


class TestModelPreviewAccessControl(PersistenceTestBase):
    """The previews list is no longer admin-only - any user who can reach
    the model can list its previews, not just admins. `list_model_previews_for_user`
    is what the HTTP route calls; a denied model and a missing model both raise
    ModelNotFoundException (house 404-not-403 idiom - the endpoint can't be used
    to probe which model ids exist by comparing an "access_denied" response to a
    "not_found" one).
    """

    def setUp(self):
        super().setUp()
        import src.features.models.repository as model_repository_module
        model_repository_module.db = self.db
        import src.features.tags.repository as tag_repository_module
        tag_repository_module.db = self.db

        self.repo = ModelRepository()

        self._storage_root = tempfile.mkdtemp()
        self.storage = Path(self._storage_root)
        (self.storage / "uploads").mkdir(parents=True)

        self.settings = Mock()
        self.settings.get_file_storage_directory.return_value = str(self.storage)
        self.settings.get_models_media_directory.return_value = str(self.storage / "models")

        from src.features.tags.repository import tag_repo
        # models_root: without it, `__init__` falls through to the real, lazily
        # -constructed module-level scanner singleton to resolve models_dir,
        # which hits the settings DB for real.
        self.collaborators = build_model_index_collaborators(
            self.repo, tag_repo, Mock(), self.settings, Mock(), models_root=self.storage
        )

        # `files.user_id` is a real FK, so the uploading admin needs a row too -
        # `create_test_user` always inserts account_type=USER; the ADMIN `User`
        # used in the assertions below is built in-memory instead (see `_admin`),
        # since `ModelAccessPolicy.verify_model_access` never queries the DB for
        # an admin caller.
        self.create_test_user(user_id="uploader-admin", username="uploader-admin", email="uploader-admin@x.com")

        self.model = self.repo.create(Model(
            filename="access.safetensors",
            file_path="/models/loras/access.safetensors",
            model_type="lora",
        ))
        operations.add_model_preview(self.collaborators, 
            self.model.id, {"source_path": self._write_upload(), "type": "image"}, user_id="uploader-admin"
        )

    def tearDown(self):
        shutil.rmtree(self._storage_root, ignore_errors=True)
        super().tearDown()

    def _write_upload(self, name="img.png", size=(400, 300), color=(10, 120, 200)):
        path = self.storage / "uploads" / name
        Image.new("RGB", size, color).save(path, "PNG")
        return f"uploads/{name}"

    def _admin(self, user_id="uploader-admin") -> User:
        return User(username=user_id, email=f"{user_id}@x.com", password_hash="x",
                    account_type=AccountType.ADMIN, id=user_id)

    def _regular_user(self, user_id: str) -> User:
        self.create_test_user(user_id=user_id, username=user_id, email=f"{user_id}@x.com")
        return User(username=user_id, email=f"{user_id}@x.com", password_hash="x",
                    account_type=AccountType.USER, id=user_id)

    def test_uploader_admin_sees_the_preview(self):
        previews = operations.list_model_previews_for_user(self.collaborators, self.model.id, self._admin())
        assert len(previews) == 1

    def test_other_user_with_model_access_sees_the_preview(self):
        user = self._regular_user("user-with-access")
        self.repo.assign_model_to_user(self.model.id, user.id)

        previews = operations.list_model_previews_for_user(self.collaborators, self.model.id, user)

        assert len(previews) == 1

    def test_user_without_model_access_gets_not_found(self):
        user = self._regular_user("user-without-access")

        with pytest.raises(ModelNotFoundException):
            operations.list_model_previews_for_user(self.collaborators, self.model.id, user)

    def test_missing_model_gets_not_found(self):
        user = self._regular_user("user-without-access")

        with pytest.raises(ModelNotFoundException):
            operations.list_model_previews_for_user(self.collaborators, "does-not-exist", user)
