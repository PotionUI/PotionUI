"""Inspirations routes driven through a real FastAPI app.

The collaborators, repositories and database underneath are real (a migrated
scratch DB and a temp storage tree); only the authenticated-user dependency
is overridden. Asserting through the router is what catches a ValueError that
never becomes the right status code - a controller called directly cannot.
"""

import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.features.inspirations import operations
from src.features.inspirations.routes import InspirationController, build_router, build_media_router
from src.platform.security.current_user import get_current_active_user
from src.platform.security.user import AccountType, User

from tests.features.inspirations.test_operations import InspirationTestBase


class _Container:
    def __init__(self, controller):
        self.inspiration_controller = controller


class InspirationRoutesTestBase(InspirationTestBase):

    def setUp(self):
        super().setUp()

        self.controller = InspirationController(self.collaborators)

        app = FastAPI()
        container = _Container(self.controller)
        app.include_router(build_router(container))
        app.include_router(build_media_router(container))

        self._as_user_id = self.user_id

        async def _current_user():
            return User(
                id=self._as_user_id, username="testuser", email="test@example.com",
                password_hash="h", account_type=AccountType.USER,
            )

        app.dependency_overrides[get_current_active_user] = _current_user
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        super().tearDown()

    def _publish(self, title="Mine", filenames=None, user_id=None):
        generation_id, file_record, source = self._generated_file(
            content=b"generated-pixels", user_id=user_id
        )
        insp = operations.publish(
            self.collaborators, user_id or self.user_id, generation_id, filenames or [source.name], title
        )
        return insp, source


class TestPublishGetDeleteFlow(InspirationRoutesTestBase):

    def test_publish_then_appears_in_feed_and_get(self):
        generation_id, file_record, source = self._generated_file()

        publish = self.client.post(
            "/api/inspirations",
            json={"generation_id": generation_id, "filenames": [source.name], "title": "My Shot"},
        )
        self.assertEqual(publish.status_code, 200)
        insp_id = publish.json()["data"]["inspiration"]["id"]

        feed = self.client.get("/api/inspirations")
        self.assertEqual(feed.json()["data"]["total"], 1)

        single = self.client.get(f"/api/inspirations/{insp_id}")
        self.assertEqual(single.json()["data"]["inspiration"]["title"], "My Shot")

    def test_publish_of_a_foreign_generation_is_404(self):
        generation_id, file_record, source = self._generated_file(user_id=self.other_user_id)

        response = self.client.post(
            "/api/inspirations",
            json={"generation_id": generation_id, "filenames": [source.name], "title": "Stolen"},
        )

        self.assertEqual(response.status_code, 404)

    def test_params_snapshot_endpoint(self):
        insp, source = self._publish()

        response = self.client.get(f"/api/inspirations/{insp.id}/params")

        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(data["form_data"]["seed"], 12345)
        self.assertEqual(data["preset_id"], "preset-1")

    def test_media_is_served_and_gated_by_filename(self):
        """Mirrors `MediaController.serve_uploaded_media`'s posture: a miss is
        a `success: false` body, not an HTTP error status - see
        `src.features.media.routes`."""
        insp, source = self._publish()

        ok = self.client.get(f"/api/media/inspirations/{insp.id}/{source.name}")
        wrong_name = self.client.get(f"/api/media/inspirations/{insp.id}/not-a-real-file.png")

        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.content, b"generated-pixels")
        self.assertEqual(wrong_name.status_code, 200)
        self.assertFalse(wrong_name.json()["success"])
        self.assertEqual(wrong_name.json()["error"], "not_found")

    def test_delete_by_owner(self):
        insp, source = self._publish()

        response = self.client.delete(f"/api/inspirations/{insp.id}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get("/api/inspirations").json()["data"]["total"], 0)

    def test_delete_by_a_non_owner_is_404_and_leaves_it_alone(self):
        insp, source = self._publish()
        self._as_user_id = self.other_user_id

        response = self.client.delete(f"/api/inspirations/{insp.id}")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.client.get("/api/inspirations").json()["data"]["total"], 1)


class TestCommentsRoutes(InspirationRoutesTestBase):

    def test_add_list_and_delete_comment(self):
        insp, _ = self._publish()

        add = self.client.post(f"/api/inspirations/{insp.id}/comments", json={"body": "nice work"})
        self.assertEqual(add.status_code, 200)
        comment_id = add.json()["data"]["comment"]["id"]

        listed = self.client.get(f"/api/inspirations/{insp.id}/comments")
        self.assertEqual([c["body"] for c in listed.json()["data"]["items"]], ["nice work"])

        deleted = self.client.delete(f"/api/inspirations/{insp.id}/comments/{comment_id}")
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(self.client.get(f"/api/inspirations/{insp.id}/comments").json()["data"]["items"], [])

    def test_delete_someone_elses_comment_is_404(self):
        insp, _ = self._publish()
        add = self.client.post(f"/api/inspirations/{insp.id}/comments", json={"body": "hi"})
        comment_id = add.json()["data"]["comment"]["id"]
        self._as_user_id = self.other_user_id

        response = self.client.delete(f"/api/inspirations/{insp.id}/comments/{comment_id}")

        self.assertEqual(response.status_code, 404)

    def test_empty_comment_body_is_rejected(self):
        insp, _ = self._publish()

        response = self.client.post(f"/api/inspirations/{insp.id}/comments", json={"body": "   "})

        self.assertEqual(response.status_code, 400)


class TestSaveRoutes(InspirationRoutesTestBase):

    def test_save_to_library_then_unsave(self):
        insp, _ = self._publish()
        self._as_user_id = self.other_user_id

        saved = self.client.post(f"/api/inspirations/{insp.id}/save-to-library")
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.json()["data"], {"saved": True, "save_count": 1})

        unsaved = self.client.delete(f"/api/inspirations/{insp.id}/save")
        self.assertEqual(unsaved.json()["data"], {"saved": False, "save_count": 0})


class TestCollectionRoutes(InspirationRoutesTestBase):

    def test_create_add_remove_and_delete_collection(self):
        insp, _ = self._publish()

        create = self.client.post("/api/inspirations/collections", json={"name": "Favorites"})
        collection_id = create.json()["data"]["collection"]["id"]

        add = self.client.post(
            f"/api/inspirations/collections/{collection_id}/items", json={"inspiration_id": insp.id}
        )
        self.assertEqual(add.status_code, 200)

        listed = self.client.get("/api/inspirations/collections")
        self.assertEqual(listed.json()["data"]["items"][0]["item_count"], 1)

        remove = self.client.delete(f"/api/inspirations/collections/{collection_id}/items/{insp.id}")
        self.assertEqual(remove.status_code, 200)

        deleted = self.client.delete(f"/api/inspirations/collections/{collection_id}")
        self.assertEqual(deleted.status_code, 200)

    def test_move_into_own_subfolder_rejected(self):
        parent = self.client.post("/api/inspirations/collections", json={"name": "Parent"}).json()["data"]["collection"]
        child = self.client.post(
            "/api/inspirations/collections", json={"name": "Child", "parent_id": parent["id"]}
        ).json()["data"]["collection"]

        response = self.client.put(
            f"/api/inspirations/collections/{parent['id']}", json={"parent_id": child["id"]}
        )

        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
