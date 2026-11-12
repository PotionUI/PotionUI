from types import SimpleNamespace

from src.features.generation.file_repository import FileRepository
from src.features.generation.records import File
from src.features.kits import operations
from src.features.kits.catalog import KitCatalog
from src.features.kits.expansion import KitExpander
from src.features.kits.routes import KitController, build_router
from src.platform.http.errors import ConflictError, NotFoundError
from tests.features.kits.support import KitsTestBase
from tests.features.kits.test_expansion import ALL_KINDS, ExpansionBase
from tests.features.kits.test_routes import BOTH_KEY, CONTENT_KEY, OTHER_KEY, READ_KEY, RoutesBase

EXTENSIONS = {"IMAGE": "png", "VIDEO": "mp4", "AUDIO": "wav"}


class HistoryMixin:

    def history_setup(self):
        self.files = FileRepository()
        self.copies = []

    def generated(self, name, file_type="IMAGE", user_id=None):
        return self.files.create(File(
            file_path=f"{name}.{EXTENSIONS[file_type]}", file_type=file_type, user_id=user_id or self.user_id,
        ))

    def copy(self, file_id, user_id):
        record = self.files.get_by_id(file_id, user_id=user_id)
        if record is None:
            raise ValueError("File not found")
        self.copies.append(file_id)
        upload = self.uploads.create(self.upload_record(record, user_id))
        return upload.id

    def upload_record(self, record, user_id):
        from src.features.media.records import Upload
        return Upload(
            user_id=user_id, filename=f"copy_{record.id}.{EXTENSIONS[record.file_type]}", original_filename=record.file_path,
            media_type=record.file_type.lower(), file_size=10, source_generation_id=f"gen_{record.id}",
        )


class TestAddHistoryItems(HistoryMixin, KitsTestBase):

    def setUp(self):
        super().setUp()
        self.history_setup()
        self.kit = operations.create_kit(self.repo, self.user_id, "Mara")

    def add(self, file_ids, **kwargs):
        return operations.add_history_items(self.repo, self.copy, self.user_id, file_ids, kwargs.pop("kit_id", self.kit.id), **kwargs)

    def test_one_and_several_items_are_added_in_request_order(self):
        first, second = self.generated("a"), self.generated("b")

        _, added = self.add([second.id, first.id])

        self.assertEqual([item.name for item in added], ["image1", "image2"])
        self.assertEqual(self.copies, [second.id, first.id])
        self.assertEqual(self.repo.count_items(self.kit.id), 2)

    def test_mixed_kinds_take_their_own_default_names(self):
        image, video, audio = self.generated("a"), self.generated("b", "VIDEO"), self.generated("c", "AUDIO")

        _, added = self.add([image.id, video.id, audio.id])

        self.assertEqual([(item.kind, item.name) for item in added], [("image", "image1"), ("video", "video1"), ("audio", "audio1")])

    def test_another_users_generation_is_refused_and_nothing_is_copied(self):
        mine, theirs = self.generated("a"), self.generated("b", user_id=self.other_user_id)

        with self.assertRaises(NotFoundError) as caught:
            self.add([mine.id, theirs.id])

        self.assertEqual(caught.exception.extra["missing"], [theirs.id])
        self.assertEqual((self.copies, self.repo.count_items(self.kit.id)), ([], 0))

    def test_a_file_that_is_not_media_is_refused(self):
        mesh = self.files.create(File(file_path="m.glb", file_type="MESH", user_id=self.user_id))

        with self.assertRaises(NotFoundError):
            self.add([mesh.id])

    def test_the_item_cap_is_enforced(self):
        for number in range(49):
            operations.add_text_item(self.repo, self.user_id, self.kit.id, "t", f"t{number}")
        one, two = self.generated("a"), self.generated("b")

        with self.assertRaises(ConflictError):
            self.add([one.id, two.id])

        self.assertEqual((self.copies, self.repo.count_items(self.kit.id)), ([], 49))

    def test_a_requested_name_is_used_and_a_clash_suggests_one(self):
        one, two = self.generated("a"), self.generated("b")

        _, added = self.add([one.id], names={one.id: "face"})
        with self.assertRaises(ConflictError):
            self.add([two.id], names={two.id: "face"})

        self.assertEqual(added[0].name, "face")

    def test_a_new_kit_is_created_when_asked(self):
        one = self.generated("a")

        kit, added = operations.add_history_items(self.repo, self.copy, self.user_id, [one.id], new_kit_name="Harbour")

        self.assertEqual((kit.name, len(added)), ("Harbour", 1))

    def test_a_failed_copy_creates_no_kit(self):
        one = self.generated("a")

        def failing(file_id, user_id):
            raise ValueError("File not found")

        with self.assertRaises(NotFoundError):
            operations.add_history_items(self.repo, failing, self.user_id, [one.id], new_kit_name="Harbour")

        self.assertEqual(self.repo.list_kits(self.user_id)[1], 1)

    def test_deleting_the_generation_afterwards_keeps_the_item(self):
        one = self.generated("a")
        _, added = self.add([one.id])

        self.files.delete(one.id)

        view = KitCatalog(self.repo, self.user_id).by_id(self.kit.id).items[0]
        self.assertFalse(view.missing)
        self.assertEqual(view.item.id, added[0].id)
        self.assertEqual(view.upload.source_generation_id, f"gen_{one.id}")


class TestHistoryItemsExpand(HistoryMixin, ExpansionBase):

    def setUp(self):
        super().setUp()
        self.history_setup()

    def test_a_history_item_expands_like_a_library_item(self):
        one = self.generated("a")
        _, added = operations.add_history_items(self.repo, self.copy, self.user_id, [one.id], self.kit.id, names={one.id: "face"})

        req, expansion = self.go(f"Look at {self.cite(added[0])}")

        self.assertEqual(self.codes(expansion), [])
        self.assertEqual(len(req.form_data["references"]), 1)
        self.assertEqual(req.form_data["references"][0]["label"], "face")


class TestHistoryRoutes(HistoryMixin, RoutesBase):

    def setUp(self):
        super().setUp()
        self.history_setup()
        controller = KitController(
            self.repo, KitExpander(self.repo), lambda preset_id: self.template if preset_id == "h3" else None,
            self.media_store, copy_to_library=self.copy,
        )
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        self.client.close()
        app = FastAPI()
        app.include_router(build_router(SimpleNamespace(kit_controller=controller)))
        self.client = TestClient(app)

    def test_adding_returns_the_kit_the_added_ids_and_the_source(self):
        kit = self.kit("Mara")
        one, two = self.generated("a"), self.generated("b", "VIDEO")

        response = self.call("POST", "/add-history-items", json={"file_ids": [one.id, two.id], "kit_id": kit["id"]})

        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()["data"]
        items = data["kit"]["items"]
        self.assertEqual(data["added"], [item["id"] for item in items])
        self.assertEqual([item["source"] for item in items], ["history", "history"])
        self.assertEqual(items[0]["media"]["generation_id"], f"gen_{one.id}")
        self.assertTrue(items[0]["media"]["url"].startswith("/api/media/uploads/"))
        self.assertEqual(data["kit"]["counts"], {"image": 1, "video": 1, "audio": 0, "text": 0})

    def test_a_library_item_reports_the_library_as_its_source(self):
        kit = self.kit("Mara")
        upload = self.upload("a.png")
        self.call("POST", f"/{kit['id']}/items", json={"upload_id": upload.id})
        self.call("POST", f"/{kit['id']}/items", json={"text": "calm"})

        items = self.call("GET", f"/{kit['id']}").json()["data"]["kit"]["items"]

        self.assertEqual([item["source"] for item in items], ["library", None])
        self.assertIsNone(items[0]["media"]["generation_id"])

    def test_a_new_kit_can_be_created_by_the_same_call(self):
        one = self.generated("a")

        response = self.call("POST", "/add-history-items", json={"file_ids": [one.id], "new_kit": {"name": "Harbour"}})

        self.assertEqual(response.json()["data"]["kit"]["name"], "Harbour")

    def test_another_users_generation_is_404(self):
        kit = self.kit("Mara")
        theirs = self.generated("a", user_id=self.other_user_id)

        response = self.call("POST", "/add-history-items", json={"file_ids": [theirs.id], "kit_id": kit["id"]})

        detail = response.json()["detail"]
        self.assertEqual((response.status_code, detail["error"], detail["missing"]), (404, "history_item_not_found", [theirs.id]))

    def test_target_is_required_and_exclusive(self):
        one = self.generated("a")
        kit = self.kit("Mara")

        neither = self.call("POST", "/add-history-items", json={"file_ids": [one.id]})
        both = self.call("POST", "/add-history-items", json={"file_ids": [one.id], "kit_id": kit["id"], "new_kit": {"name": "x"}})

        self.assertEqual([neither.status_code, both.status_code], [400, 400])

    def test_it_is_a_content_write(self):
        one = self.generated("a")
        kit = self.kit("Mara")
        body = {"file_ids": [one.id], "kit_id": kit["id"]}

        self.assertEqual(self.call("POST", "/add-history-items", token=READ_KEY, json=body).status_code, 403)
        self.assertEqual(self.call("POST", "/add-history-items", token=CONTENT_KEY, json=body).status_code, 200)

    def test_another_users_key_cannot_use_my_kit(self):
        one = self.generated("a", user_id=self.other_user_id)
        kit = self.kit("Mara")

        response = self.call("POST", "/add-history-items", token=OTHER_KEY, json={"file_ids": [one.id], "kit_id": kit["id"]})

        self.assertEqual(response.status_code, 404)

    def test_resolve_works_for_a_history_item(self):
        kit = self.kit("Mara")
        one = self.generated("a")
        self.call("POST", "/add-history-items", json={"file_ids": [one.id], "kit_id": kit["id"], "names": {one.id: "face"}})

        data = self.call("POST", "/resolve", json={"cites": ["kits.mara.face"], "preset_id": "h3", "mode": "refs"}).json()["data"]

        self.assertEqual(data["text"], "<Picture 1>")
        self.assertEqual([item["name"] for item in data["items"]], ["face"])

    def test_usage_lists_the_kit_of_a_history_copy(self):
        kit = self.kit("Mara")
        one = self.generated("a")
        data = self.call("POST", "/add-history-items", json={"file_ids": [one.id], "kit_id": kit["id"]}).json()["data"]

        upload_id = data["kit"]["items"][0]["media"]["upload_id"]
        usage = self.call("GET", f"/usage?upload_ids={upload_id}").json()["data"]["usage"]

        self.assertEqual(usage[upload_id]["count"], 1)
