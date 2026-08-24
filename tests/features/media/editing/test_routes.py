"""Media editing driven through a real FastAPI app.

Real migrated scratch database, real bytes in a real temporary storage tree,
the real `UploadRepository`/`FilePathResolver` pair, and the real transform
layer. Only the authenticated-user dependency is overridden, because that is
the input the whole ownership contract turns on.

Every claim here is checked against the thing itself and not against what the
code handed back: dimensions by reopening the written file, persisted metadata
by re-reading the row, and "the original is untouched" by comparing bytes.
"""

import shutil
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.features.media.editing.manager import MediaEditManager
from src.features.media.editing.operations import InvalidEditError
from src.features.media.editing.routes import MediaEditController, build_router
from src.features.media.file_resolver import FilePathResolver
from src.features.media.media_types import MediaTypeResolver
from src.features.media.records import Upload
from src.features.media.upload_repository import UploadRepository
from src.features.tags.repository import TagRepository
from src.platform.filesystem.storage_driver import LocalFileStorageDriver
from src.platform.security.current_user import get_current_active_user
from src.platform.security.user import AccountType, User

from tests.fixtures.persistence_base import PersistenceTestBase
from tests.fixtures.query_counter import CountingDb
from tests.features.media.editing.test_operations import make_image, make_video, make_wav

import src.features.media.upload_repository as upload_repository_module
import src.features.tags.repository as tag_repository_module

from PIL import Image

FFMPEG = shutil.which("ffmpeg")
needs_ffmpeg = unittest.skipIf(FFMPEG is None, "ffmpeg is not installed")


class _StorageDirSettings:
    """Minimal settings stand-in: the storage root is all FilePathResolver reads."""

    def __init__(self, storage_dir: Path):
        self._storage_dir = str(storage_dir)

    def get_file_storage_directory(self, user_id=None) -> str:
        return self._storage_dir


class _Container:
    def __init__(self, controller):
        self.media_edit_controller = controller


class MediaEditRoutesTestBase(PersistenceTestBase):

    def setUp(self):
        super().setUp()
        for module in (upload_repository_module, tag_repository_module):
            module.db = self.db

        self.storage_dir = Path(self.temp_dir) / "storage"
        self.storage_dir.mkdir(parents=True, exist_ok=True)

        self.upload_repo = UploadRepository()
        self.tag_repo = TagRepository()
        self.file_resolver = FilePathResolver(_StorageDirSettings(self.storage_dir))
        self.uploads_dir = self.file_resolver.get_uploads_directory(None)

        self.manager = MediaEditManager(
            upload_repository=self.upload_repo,
            media_type_resolver=MediaTypeResolver(),
            storage_driver=LocalFileStorageDriver(str(self.storage_dir)),
        )

        self.user_id = self.create_test_user()
        self.other_user_id = self.create_test_user("other_user", "other", "other@example.com")

        app = FastAPI()
        app.include_router(build_router(_Container(MediaEditController(self.manager))))

        async def _current_user():
            return User(
                id=self.user_id, username="testuser", email="test@example.com",
                password_hash="h", account_type=AccountType.USER,
            )

        app.dependency_overrides[get_current_active_user] = _current_user
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        # PersistenceTestBase removes its temp dir with os.rmdir, which fails if
        # anything this test wrote is still in it.
        for child in Path(self.temp_dir).iterdir():
            if child == self.temp_db_path:
                continue
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink()
        super().tearDown()

    # --- fixtures ---

    def _record(self, path: Path, media_type: str, user_id=None, **metadata) -> Upload:
        return self.upload_repo.create(Upload(
            user_id=user_id or self.user_id,
            filename=path.name,
            original_filename=f"original-{path.name}",
            media_type=media_type,
            mime_type=MediaTypeResolver().get_media_type(path.suffix),
            file_size=path.stat().st_size,
            **metadata,
        ))

    def _image_item(self, name="pic.png", user_id=None, width=200, height=100) -> Upload:
        path = make_image(self.uploads_dir / name, width, height)
        return self._record(path, "image", user_id, width=width, height=height)

    def _video_item(self, name="clip.mp4", seconds=2) -> Upload:
        path = make_video(self.uploads_dir / name, seconds=seconds, width=64, height=48)
        return self._record(path, "video", width=64, height=48, duration_seconds=float(seconds))

    def _audio_item(self, name="sound.wav", seconds=2.0, user_id=None) -> Upload:
        path = make_wav(self.uploads_dir / name, seconds=seconds)
        return self._record(path, "audio", user_id, duration_seconds=seconds)

    def _mesh_item(self, name="thing.glb") -> Upload:
        path = self.uploads_dir / name
        path.write_bytes(b"glTF-ish bytes")
        return self._record(path, "model")

    def _crop(self, **overrides):
        operation = {"type": "crop", "x": 0, "y": 0, "width": 50, "height": 40}
        operation.update(overrides)
        return operation

    def _files_in_uploads(self):
        return sorted(p.name for p in self.uploads_dir.iterdir())


class TestEditImage(MediaEditRoutesTestBase):

    def test_save_as_new_creates_a_second_resource_with_the_real_dimensions(self):
        item = self._image_item()

        response = self.client.post(
            f"/api/media/edit/{item.id}",
            json={"operations": [self._crop(x=10, y=10, width=50, height=40)], "mode": "new"},
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertFalse(data["replaced"])
        edited = data["item"]
        self.assertNotEqual(edited["id"], item.id)

        written = self.uploads_dir / edited["filename"]
        with Image.open(written) as image:
            self.assertEqual(image.size, (50, 40))

    def test_the_persisted_row_carries_the_new_metadata(self):
        """Read the row back - the object the manager returned proves nothing
        about what the INSERT actually stored."""
        item = self._image_item()

        response = self.client.post(
            f"/api/media/edit/{item.id}",
            json={"operations": [self._crop(width=50, height=40)]},
        )

        stored = self.upload_repo.get_by_id(response.json()["data"]["item"]["id"], self.user_id)
        written = self.uploads_dir / stored.filename
        self.assertEqual((stored.width, stored.height), (50, 40))
        self.assertEqual(stored.mime_type, "image/png")
        self.assertEqual(stored.media_type, "image")
        self.assertEqual(stored.file_size, written.stat().st_size)

    def test_save_as_new_leaves_the_original_byte_identical(self):
        item = self._image_item()
        original = self.uploads_dir / item.filename
        before = original.read_bytes()

        self.client.post(
            f"/api/media/edit/{item.id}",
            json={"operations": [self._crop()], "mode": "new"},
        )

        self.assertEqual(original.read_bytes(), before)
        self.assertIsNotNone(self.upload_repo.get_by_id(item.id, self.user_id))

    def test_the_result_is_served_by_the_unauthenticated_uploads_route(self):
        """An `<img>` cannot send a bearer token, so an edit that landed
        anywhere the uploads route does not resolve would be invisible."""
        item = self._image_item()

        response = self.client.post(
            f"/api/media/edit/{item.id}", json={"operations": [self._crop()]}
        )

        edited = response.json()["data"]["item"]
        self.assertEqual(edited["url"], f"/api/media/uploads/{edited['filename']}")

        # The resolution the serve route performs for that URL, run here.
        served = self.file_resolver.resolve_upload_file(edited["filename"], self.user_id)
        self.assertTrue(served.exists())
        self.assertEqual(served, (self.uploads_dir / edited["filename"]).resolve())

    def test_operations_chain_in_the_order_given(self):
        item = self._image_item(width=200, height=100)

        response = self.client.post(
            f"/api/media/edit/{item.id}",
            json={"operations": [
                self._crop(width=100, height=100, y=0),
                {"type": "resize", "width": 25},
                {"type": "rotate", "degrees": 90},
            ]},
        )

        edited = response.json()["data"]["item"]
        with Image.open(self.uploads_dir / edited["filename"]) as image:
            self.assertEqual(image.size, (25, 25))


class TestReplace(MediaEditRoutesTestBase):

    def _collect(self, upload_id: str) -> str:
        collection_id = "collection-1"
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO collections (id, name, user_id) VALUES (?, ?, ?)",
                (collection_id, "Favourites", self.user_id)
            )
            cursor.execute(
                "INSERT INTO collection_uploads (collection_id, upload_id) VALUES (?, ?)",
                (collection_id, upload_id)
            )
        return collection_id

    def _collection_members(self, collection_id: str):
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "SELECT upload_id FROM collection_uploads WHERE collection_id = ?",
                (collection_id,)
            )
            return [row['upload_id'] for row in cursor.fetchall()]

    def test_replace_keeps_the_row_and_its_curation(self):
        item = self._image_item()
        tag = self.tag_repo.create_tag("cats", type="UPLOAD", user_id=self.user_id)
        self.tag_repo.set_upload_tags(item.id, [tag.id])
        collection_id = self._collect(item.id)

        response = self.client.post(
            f"/api/media/edit/{item.id}",
            json={"operations": [self._crop(width=50, height=40)], "mode": "replace"},
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertTrue(data["replaced"])
        self.assertEqual(data["item"]["id"], item.id)
        self.assertEqual([t.name for t in self.tag_repo.get_upload_tags(item.id)], ["cats"])
        self.assertEqual(self._collection_members(collection_id), [item.id])

    def test_replace_swaps_the_file_and_the_persisted_metadata(self):
        item = self._image_item(width=200, height=100)

        response = self.client.post(
            f"/api/media/edit/{item.id}",
            json={"operations": [self._crop(width=50, height=40)], "mode": "replace"},
        )

        edited = response.json()["data"]["item"]
        stored = self.upload_repo.get_by_id(item.id, self.user_id)
        written = self.uploads_dir / stored.filename
        self.assertEqual(stored.filename, edited["filename"])
        self.assertNotEqual(stored.filename, item.filename)
        self.assertEqual((stored.width, stored.height), (50, 40))
        self.assertEqual(stored.file_size, written.stat().st_size)
        self.assertNotEqual(stored.file_size, item.file_size)

        with Image.open(written) as image:
            self.assertEqual(image.size, (50, 40))

    def test_replace_rewrites_every_column_that_describes_the_bytes(self):
        """A row left with one stale column beside a new filename is the bug
        this pins - the UPDATE has to carry all of them, not most of them."""
        item = self._image_item()
        with self.db.get_cursor() as cursor:
            cursor.execute("UPDATE uploads SET mime_type = NULL WHERE id = ?", (item.id,))

        self.client.post(
            f"/api/media/edit/{item.id}",
            json={"operations": [self._crop(width=50, height=40)], "mode": "replace"},
        )

        stored = self.upload_repo.get_by_id(item.id, self.user_id)
        self.assertEqual(stored.mime_type, "image/png")

    def test_replace_removes_the_file_it_replaced(self):
        item = self._image_item()

        response = self.client.post(
            f"/api/media/edit/{item.id}",
            json={"operations": [self._crop()], "mode": "replace"},
        )

        edited = response.json()["data"]["item"]
        self.assertEqual(self._files_in_uploads(), [edited["filename"]])

    def test_replace_creates_no_second_row(self):
        item = self._image_item()

        self.client.post(
            f"/api/media/edit/{item.id}",
            json={"operations": [self._crop()], "mode": "replace"},
        )

        self.assertEqual(self.upload_repo.count_for_user(self.user_id), 1)


class TestOwnership(MediaEditRoutesTestBase):

    def test_editing_another_users_item_is_404_and_matches_a_missing_id(self):
        theirs = self._image_item(name="theirs.png", user_id=self.other_user_id)

        not_yours = self.client.post(
            f"/api/media/edit/{theirs.id}", json={"operations": [self._crop()]}
        )
        absent = self.client.post(
            "/api/media/edit/does-not-exist", json={"operations": [self._crop()]}
        )

        self.assertEqual(not_yours.status_code, 404)
        self.assertEqual(absent.status_code, 404)
        self.assertEqual(not_yours.json(), absent.json())

    def test_another_users_file_is_left_alone(self):
        theirs = self._image_item(name="theirs.png", user_id=self.other_user_id)
        before = (self.uploads_dir / theirs.filename).read_bytes()

        self.client.post(
            f"/api/media/edit/{theirs.id}",
            json={"operations": [self._crop()], "mode": "replace"},
        )

        self.assertEqual((self.uploads_dir / theirs.filename).read_bytes(), before)
        self.assertEqual(self._files_in_uploads(), [theirs.filename])

    def test_a_row_whose_file_is_gone_is_a_404(self):
        item = self._image_item()
        (self.uploads_dir / item.filename).unlink()

        response = self.client.post(
            f"/api/media/edit/{item.id}", json={"operations": [self._crop()]}
        )

        self.assertEqual(response.status_code, 404)


class TestRefusals(MediaEditRoutesTestBase):

    def test_a_mesh_is_refused_by_name(self):
        item = self._mesh_item()

        response = self.client.post(
            f"/api/media/edit/{item.id}", json={"operations": [self._crop()]}
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("model", response.json()["detail"]["message"])

    def test_an_out_of_bounds_crop_is_refused_and_writes_nothing(self):
        item = self._image_item(width=200, height=100)

        response = self.client.post(
            f"/api/media/edit/{item.id}",
            json={"operations": [self._crop(x=180, y=0, width=50, height=40)]},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(self._files_in_uploads(), [item.filename])
        self.assertEqual(self.upload_repo.count_for_user(self.user_id), 1)

    def test_an_empty_operation_list_is_refused(self):
        item = self._image_item()

        response = self.client.post(f"/api/media/edit/{item.id}", json={"operations": []})

        self.assertEqual(response.status_code, 400)

    def test_a_zero_target_size_is_refused(self):
        item = self._image_item()

        response = self.client.post(
            f"/api/media/edit/{item.id}",
            json={"operations": [{"type": "resize", "width": 0}]},
        )

        self.assertEqual(response.status_code, 400)

    def test_an_unknown_operation_is_rejected_by_the_schema(self):
        item = self._image_item()

        response = self.client.post(
            f"/api/media/edit/{item.id}",
            json={"operations": [{"type": "solarize", "amount": 3}]},
        )

        self.assertEqual(response.status_code, 422)

    def test_an_unsupported_rotation_is_rejected_by_the_schema(self):
        item = self._image_item()

        response = self.client.post(
            f"/api/media/edit/{item.id}",
            json={"operations": [{"type": "rotate", "degrees": 45}]},
        )

        self.assertEqual(response.status_code, 422)

    def test_trimming_an_image_is_refused(self):
        item = self._image_item()

        response = self.client.post(
            f"/api/media/edit/{item.id}",
            json={"operations": [{"type": "trim", "start_seconds": 0, "end_seconds": 1}]},
        )

        self.assertEqual(response.status_code, 400)

    def test_an_unknown_mode_is_rejected_by_the_schema(self):
        item = self._image_item()

        response = self.client.post(
            f"/api/media/edit/{item.id}",
            json={"operations": [self._crop()], "mode": "obliterate"},
        )

        self.assertEqual(response.status_code, 422)


class TestNoOrphans(MediaEditRoutesTestBase):

    def test_a_failed_insert_leaves_no_unreferenced_file(self):
        item = self._image_item()

        def _explode(_upload):
            raise RuntimeError("the disk went away")

        self.upload_repo.create = _explode

        response = self.client.post(
            f"/api/media/edit/{item.id}", json={"operations": [self._crop()]}
        )

        self.assertEqual(response.status_code, 500)
        self.assertEqual(self._files_in_uploads(), [item.filename])

    def test_a_failed_replace_leaves_the_original_in_place(self):
        item = self._image_item()
        before = (self.uploads_dir / item.filename).read_bytes()

        def _explode(**_kwargs):
            raise RuntimeError("the disk went away")

        self.upload_repo.update_file = _explode

        response = self.client.post(
            f"/api/media/edit/{item.id}",
            json={"operations": [self._crop()], "mode": "replace"},
        )

        self.assertEqual(response.status_code, 500)
        self.assertEqual(self._files_in_uploads(), [item.filename])
        self.assertEqual((self.uploads_dir / item.filename).read_bytes(), before)
        self.assertEqual(
            self.upload_repo.get_by_id(item.id, self.user_id).filename, item.filename
        )


class TestQueryCost(MediaEditRoutesTestBase):

    def test_an_edit_costs_the_same_number_of_queries_however_many_operations(self):
        """One lookup, one write, one read-back - and nothing per operation."""
        counting = CountingDb(self.db)
        upload_repository_module.db = counting

        one_op = self._image_item(name="one.png", width=200, height=100)
        counting.statements.clear()
        self.client.post(
            f"/api/media/edit/{one_op.id}", json={"operations": [self._crop()]}
        )
        cheap = list(counting.statements)

        many_ops = self._image_item(name="many.png", width=200, height=100)
        counting.statements.clear()
        self.client.post(
            f"/api/media/edit/{many_ops.id}",
            json={"operations": [
                self._crop(width=100, height=100, y=0),
                {"type": "resize", "width": 60},
                {"type": "rotate", "degrees": 180},
                {"type": "flip", "axis": "horizontal"},
            ]},
        )
        expensive = list(counting.statements)

        self.assertEqual(
            len(expensive), len(cheap),
            f"query count grew with the operation count: {cheap} -> {expensive}"
        )
        self.assertEqual(len(cheap), 3, f"expected 3 statements, got: {cheap}")


@needs_ffmpeg
class TestVideoAndAudio(MediaEditRoutesTestBase):

    def test_trimming_a_video_stores_the_real_new_duration(self):
        item = self._video_item(seconds=2)

        response = self.client.post(
            f"/api/media/edit/{item.id}",
            json={
                "operations": [{"type": "trim", "start_seconds": 0.5, "end_seconds": 1.5}],
                "mode": "replace",
            },
        )

        self.assertEqual(response.status_code, 200)
        stored = self.upload_repo.get_by_id(item.id, self.user_id)
        self.assertAlmostEqual(stored.duration_seconds, 1.0, delta=0.25)
        self.assertGreater((self.uploads_dir / stored.filename).stat().st_size, 0)

    def test_cropping_a_video_stores_the_real_new_dimensions(self):
        item = self._video_item()

        response = self.client.post(
            f"/api/media/edit/{item.id}",
            json={"operations": [self._crop(width=32, height=24)]},
        )

        edited = response.json()["data"]["item"]
        stored = self.upload_repo.get_by_id(edited["id"], self.user_id)
        self.assertEqual((stored.width, stored.height), (32, 24))
        self.assertEqual(stored.media_type, "video")

    def test_a_trim_past_the_end_of_a_video_is_refused(self):
        item = self._video_item(seconds=2)

        response = self.client.post(
            f"/api/media/edit/{item.id}",
            json={"operations": [{"type": "trim", "start_seconds": 0, "end_seconds": 60}]},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(self._files_in_uploads(), [item.filename])

    def test_an_inverted_trim_is_refused(self):
        item = self._video_item(seconds=2)

        response = self.client.post(
            f"/api/media/edit/{item.id}",
            json={"operations": [{"type": "trim", "start_seconds": 1.5, "end_seconds": 0.5}]},
        )

        self.assertEqual(response.status_code, 400)

    def test_trimming_audio_stores_the_real_new_duration(self):
        item = self._audio_item(seconds=2.0)

        response = self.client.post(
            f"/api/media/edit/{item.id}",
            json={"operations": [{"type": "trim", "start_seconds": 0.5, "end_seconds": 1.0}]},
        )

        self.assertEqual(response.status_code, 200)
        edited = response.json()["data"]["item"]
        stored = self.upload_repo.get_by_id(edited["id"], self.user_id)
        self.assertAlmostEqual(stored.duration_seconds, 0.5, delta=0.05)
        self.assertEqual(stored.media_type, "audio")

    def test_cropping_audio_is_refused(self):
        item = self._audio_item()

        response = self.client.post(
            f"/api/media/edit/{item.id}", json={"operations": [self._crop()]}
        )

        self.assertEqual(response.status_code, 400)

    def test_extracting_a_frame_creates_an_image_resource(self):
        item = self._video_item()

        response = self.client.post(
            f"/api/media/edit/{item.id}/frame", json={"time_seconds": 1.0}
        )

        self.assertEqual(response.status_code, 200)
        edited = response.json()["data"]["item"]
        self.assertFalse(response.json()["data"]["replaced"])

        stored = self.upload_repo.get_by_id(edited["id"], self.user_id)
        self.assertEqual(stored.media_type, "image")
        self.assertEqual(stored.mime_type, "image/png")
        self.assertEqual((stored.width, stored.height), (64, 48))
        with Image.open(self.uploads_dir / stored.filename) as frame:
            self.assertEqual(frame.size, (64, 48))

    def test_extracting_a_frame_leaves_the_video_untouched(self):
        item = self._video_item()
        before = (self.uploads_dir / item.filename).read_bytes()

        self.client.post(f"/api/media/edit/{item.id}/frame", json={"time_seconds": 0.5})

        self.assertEqual((self.uploads_dir / item.filename).read_bytes(), before)
        self.assertEqual(
            self.upload_repo.get_by_id(item.id, self.user_id).media_type, "video"
        )

    def test_a_frame_past_the_end_is_refused(self):
        item = self._video_item(seconds=2)

        response = self.client.post(
            f"/api/media/edit/{item.id}/frame", json={"time_seconds": 60}
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(self._files_in_uploads(), [item.filename])


@needs_ffmpeg
class TestSplit(MediaEditRoutesTestBase):

    def test_an_exact_division_produces_one_part_per_chunk(self):
        item = self._audio_item(name="song.wav", seconds=60.0)

        response = self.client.post(
            f"/api/media/split/{item.id}", json={"part_seconds": 10.0}
        )

        self.assertEqual(response.status_code, 200)
        items = response.json()["data"]["items"]
        self.assertEqual(len(items), 6)
        for index, part in enumerate(items, start=1):
            self.assertEqual(part["media_type"], "audio")
            self.assertNotEqual(part["id"], item.id)
            self.assertEqual(
                part["original_filename"], f"original-song — part {index}/6.wav"
            )
            self.assertAlmostEqual(part["duration_seconds"], 10.0, delta=0.25)

    def test_a_short_remainder_is_kept_as_its_own_final_part(self):
        item = self._audio_item(name="song.wav", seconds=65.0)

        response = self.client.post(
            f"/api/media/split/{item.id}", json={"part_seconds": 10.0}
        )

        self.assertEqual(response.status_code, 200)
        items = response.json()["data"]["items"]
        self.assertEqual(len(items), 7)
        self.assertTrue(items[-1]["original_filename"].endswith("part 7/7.wav"))
        self.assertAlmostEqual(items[-1]["duration_seconds"], 5.0, delta=0.25)

    def test_the_original_item_is_untouched_by_a_split(self):
        item = self._audio_item(name="song.wav", seconds=20.0)
        before = (self.uploads_dir / item.filename).read_bytes()

        self.client.post(f"/api/media/split/{item.id}", json={"part_seconds": 10.0})

        self.assertEqual((self.uploads_dir / item.filename).read_bytes(), before)
        self.assertIsNotNone(self.upload_repo.get_by_id(item.id, self.user_id))

    def test_a_part_length_not_shorter_than_the_source_is_refused(self):
        item = self._audio_item(name="song.wav", seconds=5.0)

        response = self.client.post(
            f"/api/media/split/{item.id}", json={"part_seconds": 10.0}
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(self._files_in_uploads(), [item.filename])

    def test_a_part_count_over_the_cap_is_refused(self):
        item = self._audio_item(name="song.wav", seconds=5.0)

        response = self.client.post(
            f"/api/media/split/{item.id}", json={"part_seconds": 0.01}
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(self._files_in_uploads(), [item.filename])

    def test_splitting_a_video_is_refused(self):
        item = self._video_item()

        response = self.client.post(
            f"/api/media/split/{item.id}", json={"part_seconds": 1.0}
        )

        self.assertEqual(response.status_code, 400)

    def test_splitting_another_users_item_is_404(self):
        theirs = self._audio_item(name="theirs.wav", user_id=self.other_user_id)

        response = self.client.post(
            f"/api/media/split/{theirs.id}", json={"part_seconds": 1.0}
        )

        self.assertEqual(response.status_code, 404)

    def test_a_part_that_fails_to_persist_rolls_back_the_parts_before_it(self):
        item = self._audio_item(name="song.wav", seconds=30.0)
        original_create = self.upload_repo.create
        calls = {"count": 0}

        def _flaky(upload):
            calls["count"] += 1
            if calls["count"] == 2:
                raise RuntimeError("the disk went away")
            return original_create(upload)

        self.upload_repo.create = _flaky

        response = self.client.post(
            f"/api/media/split/{item.id}", json={"part_seconds": 10.0}
        )

        self.assertEqual(response.status_code, 500)
        self.assertEqual(self._files_in_uploads(), [item.filename])
        self.assertEqual(self.upload_repo.count_for_user(self.user_id), 1)


class TestFrameExtractionRefusals(MediaEditRoutesTestBase):

    def test_an_image_has_no_frames_to_extract(self):
        item = self._image_item()

        response = self.client.post(
            f"/api/media/edit/{item.id}/frame", json={"time_seconds": 0}
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("image", response.json()["detail"]["message"])

    def test_extracting_a_frame_from_another_users_video_is_404(self):
        theirs = self._image_item(name="theirs.png", user_id=self.other_user_id)

        response = self.client.post(
            f"/api/media/edit/{theirs.id}/frame", json={"time_seconds": 0}
        )

        self.assertEqual(response.status_code, 404)


async def test_the_manager_refuses_an_unknown_mode_before_touching_anything():
    """The route's schema already rejects this, so the guard is only reachable
    from a non-HTTP caller - which is exactly why it is checked here and not
    through the client. Module-level and `async def`: an `async def` method on a
    TestCase is never awaited and reports passed without running.
    """
    manager = MediaEditManager(
        upload_repository=None, media_type_resolver=None, storage_driver=None
    )

    with unittest.TestCase().assertRaises(InvalidEditError):
        await manager.edit_item("any-id", "any-user", [], mode="obliterate")


if __name__ == '__main__':
    unittest.main()
