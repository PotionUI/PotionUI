from unittest.mock import MagicMock, Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.features.tags import operations
from src.features.tags.repository import TagRepository
from src.features.tags.routes import TagController, build_router
from src.platform.security.current_user import get_current_active_user
from src.platform.security.user import AccountType, User
from tests.fixtures.persistence_base import PersistenceTestBase


class TestBulkModelTagsRepository(PersistenceTestBase):

    def setUp(self):
        super().setUp()
        self.repo = TagRepository()
        with self.db.get_cursor() as cursor:
            for model_id in ("m1", "m2", "m3"):
                cursor.execute(
                    "INSERT INTO models (id, filename, file_path, model_type) VALUES (?, ?, ?, ?)",
                    (model_id, f"{model_id}.safetensors", f"/x/{model_id}.safetensors", "checkpoint"),
                )

    def _tag_names(self, model_id):
        return sorted(tag.name for tag in self.repo.get_model_tags(model_id))

    def _by_name(self, result):
        return {item["name"]: item for item in result["tags"]}

    def test_adds_new_and_existing_tags_to_every_model(self):
        existing = self.repo.create_tag("anime", type="MODEL")
        self.repo.add_tag_to_model("m1", existing.id)

        result = self.repo.bulk_update_model_tags(["m1", "m2", "m3"], ["Anime", "portrait"], [])

        tags = self._by_name(result)
        self.assertEqual(tags["anime"]["added"], 2)
        self.assertEqual(tags["anime"]["already_present"], 1)
        self.assertEqual(tags["portrait"]["added"], 3)
        self.assertEqual(result["models"], 3)
        for model_id in ("m1", "m2", "m3"):
            self.assertEqual(self._tag_names(model_id), ["anime", "portrait"])
        self.assertEqual(len([t for t in self.repo.get_all_tags(type="MODEL") if t.name.lower() == "anime"]), 1)

    def test_is_idempotent(self):
        self.repo.bulk_update_model_tags(["m1", "m2"], ["style"], [])

        result = self.repo.bulk_update_model_tags(["m1", "m2"], ["style"], [])

        tags = self._by_name(result)
        self.assertEqual(tags["style"]["added"], 0)
        self.assertEqual(tags["style"]["already_present"], 2)
        self.assertEqual(self._tag_names("m1"), ["style"])

    def test_removes_by_id_or_name_only_from_selection(self):
        tag = self.repo.create_tag("anime", type="MODEL")
        for model_id in ("m1", "m2", "m3"):
            self.repo.add_tag_to_model(model_id, tag.id)

        result = self.repo.bulk_update_model_tags(["m1", "m2"], [], [tag.id, "missing"])

        self.assertEqual(self._by_name(result)["anime"]["removed"], 2)
        self.assertEqual(result["unknown_tags"], ["missing"])
        self.assertEqual(self._tag_names("m1"), [])
        self.assertEqual(self._tag_names("m3"), ["anime"])

    def test_reports_unknown_model_ids(self):
        result = self.repo.bulk_update_model_tags(["m1", "ghost"], ["x"], [])

        self.assertEqual(result["unknown_model_ids"], ["ghost"])
        self.assertEqual(result["models"], 1)
        self.assertEqual(self._by_name(result)["x"]["added"], 1)


def test_operation_rejects_same_tag_added_and_removed():
    with pytest.raises(ValueError, match="both added and removed"):
        operations.bulk_update_model_tags(Mock(), ["m1"], ["Anime"], ["anime"])


def test_operation_rejects_empty_change():
    with pytest.raises(ValueError, match="Nothing"):
        operations.bulk_update_model_tags(Mock(), ["m1"], [" "], [])


def test_operation_dedupes_and_trims():
    repo = Mock()
    operations.bulk_update_model_tags(repo, ["m1", "m1", ""], [" anime ", "Anime"], [])
    repo.bulk_update_model_tags.assert_called_once_with(["m1"], ["anime"], [])


def _client(account_type):
    repo = Mock()
    repo.bulk_update_model_tags.return_value = {"models": 1, "unknown_model_ids": [], "unknown_tags": [], "tags": []}
    container = MagicMock()
    container.tag_controller = TagController(repo, Mock(), Mock(), Mock())
    app = FastAPI()
    app.include_router(build_router(container))

    async def _user():
        return User(id="u1", username="u", email="u@example.com", password_hash="h", account_type=account_type)

    app.dependency_overrides[get_current_active_user] = _user
    return TestClient(app, raise_server_exceptions=False), repo


def test_route_admin_applies_changes():
    client, repo = _client(AccountType.ADMIN)

    resp = client.post("/api/tags/models/bulk", json={"model_ids": ["m1"], "add": ["anime"], "remove": []})

    assert resp.status_code == 200
    assert resp.json()["success"] is True
    repo.bulk_update_model_tags.assert_called_once_with(["m1"], ["anime"], [])


def test_route_non_admin_forbidden():
    client, repo = _client(AccountType.USER)

    resp = client.post("/api/tags/models/bulk", json={"model_ids": ["m1"], "add": ["anime"]})

    assert resp.status_code == 403
    repo.bulk_update_model_tags.assert_not_called()


def test_selection_tags_counts_and_orders():
    repo = Mock()
    anime = Mock(id="t1")
    anime.name = "anime"
    style = Mock(id="t2")
    style.name = "Style"
    repo.get_model_tags_bulk.return_value = {"m1": [anime, style], "m2": [anime], "m3": []}

    result = operations.model_selection_tags(repo, ["m1", "m2", "m3", "m1"])

    assert result == {
        "models": 3,
        "tags": [{"id": "t1", "name": "anime", "count": 2}, {"id": "t2", "name": "Style", "count": 1}],
    }
    repo.get_model_tags_bulk.assert_called_once_with(["m1", "m2", "m3"])


def test_selection_route_non_admin_forbidden():
    client, repo = _client(AccountType.USER)

    resp = client.post("/api/tags/models/selection", json={"model_ids": ["m1"]})

    assert resp.status_code == 403
