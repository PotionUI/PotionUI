"""Library routes driven through a real FastAPI app.

The manager, repositories and database underneath are the real ones (a migrated
scratch DB and a temp storage tree); only the authenticated-user dependency is
overridden, because that is the input the whole user-scoping contract turns on.
Asserting through the router is what catches a route wired to the wrong handler
or a ValueError that never becomes a 404 - a controller called directly cannot.
"""

import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.features.library.routes import LibraryController, build_router
from src.platform.security.current_user import get_current_active_user
from src.platform.security.user import AccountType, User

from tests.features.library.test_manager import LibraryManagerTestBase


class _Container:
    def __init__(self, controller):
        self.library_controller = controller


class LibraryRoutesTestBase(LibraryManagerTestBase):

    def setUp(self):
        super().setUp()
        app = FastAPI()
        app.include_router(build_router(_Container(LibraryController(self.manager))))

        async def _current_user():
            return User(
                id=self.user_id, username="testuser", email="test@example.com",
                password_hash="h", account_type=AccountType.USER,
            )

        app.dependency_overrides[get_current_active_user] = _current_user
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        super().tearDown()


class TestLibraryRoutes(LibraryRoutesTestBase):

    def test_list_returns_only_the_callers_items(self):
        self._upload(filename="mine.png")
        self._upload(filename="theirs.png", user_id=self.other_user_id)

        response = self.client.get("/api/library/items")

        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(data["total"], 1)
        self.assertEqual([i["filename"] for i in data["items"]], ["mine.png"])

    def test_list_applies_filters(self):
        self._upload(filename="pic.png", media_type="image")
        self._upload(filename="clip.mp4", media_type="video")

        response = self.client.get("/api/library/items", params={"media_type": "video"})

        self.assertEqual([i["filename"] for i in response.json()["data"]["items"]], ["clip.mp4"])

    def test_list_rejects_an_unknown_media_type(self):
        response = self.client.get("/api/library/items", params={"media_type": "mesh"})

        self.assertEqual(response.status_code, 400)

    def test_tag_filter_accepts_a_comma_separated_list(self):
        both = self._upload(filename="both.png")
        self._upload(filename="one.png")
        cats = self._tag("cats")
        dogs = self._tag("dogs")
        self.manager.set_tags(both.id, [cats.id, dogs.id], self.user_id)

        response = self.client.get(
            "/api/library/items", params={"tag_ids": f"{cats.id},{dogs.id}"}
        )

        items = response.json()["data"]["items"]
        self.assertEqual([i["filename"] for i in items], ["both.png"])

    def test_delete_own_item(self):
        item = self._upload(filename="doomed.png")

        response = self.client.delete(f"/api/library/items/{item.id}")

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(self.upload_repo.get_by_id(item.id, self.user_id))

    def test_delete_of_another_users_item_is_404_not_403(self):
        """404, never 403: a 403 would confirm to a prober that the id exists."""
        theirs = self._upload(filename="theirs.png", user_id=self.other_user_id)

        response = self.client.delete(f"/api/library/items/{theirs.id}")

        self.assertEqual(response.status_code, 404)
        self.assertIsNotNone(self.upload_repo.get_by_id(theirs.id, self.other_user_id))

    def test_get_of_another_users_item_is_404_and_matches_a_missing_id(self):
        theirs = self._upload(filename="theirs.png", user_id=self.other_user_id)

        not_yours = self.client.get(f"/api/library/items/{theirs.id}")
        absent = self.client.get("/api/library/items/does-not-exist")

        self.assertEqual(not_yours.status_code, 404)
        self.assertEqual(absent.status_code, 404)
        self.assertEqual(not_yours.json(), absent.json())

    def test_set_and_get_tags(self):
        item = self._upload()
        tag = self._tag("cats")

        put = self.client.put(f"/api/library/items/{item.id}/tags", json={"tag_ids": [tag.id]})
        get = self.client.get(f"/api/library/items/{item.id}/tags")

        self.assertEqual(put.status_code, 200)
        self.assertEqual([t["name"] for t in get.json()["data"]["tags"]], ["cats"])

    def test_copy_from_generation_creates_a_library_item(self):
        _, file_record, _ = self._generated_file()

        response = self.client.post(
            "/api/library/items/from-generation", json={"file_id": file_record.id}
        )

        self.assertEqual(response.status_code, 200)
        item = response.json()["data"]["item"]
        self.assertTrue(item["url"].startswith("/api/media/uploads/"))
        self.assertEqual(self.client.get("/api/library/items").json()["data"]["total"], 1)

    def test_copy_of_another_users_file_is_404(self):
        _, file_record, _ = self._generated_file(user_id=self.other_user_id)

        response = self.client.post(
            "/api/library/items/from-generation", json={"file_id": file_record.id}
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.client.get("/api/library/items").json()["data"]["total"], 0)

    def test_facets(self):
        self._upload(filename="a.png", media_type="image")
        self._upload(filename="b.mp4", media_type="video")

        response = self.client.get("/api/library/facets")

        self.assertEqual(response.json()["data"]["media_types"], {"image": 1, "video": 1})


if __name__ == '__main__':
    unittest.main()
