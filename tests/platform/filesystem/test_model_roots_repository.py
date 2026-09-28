import pytest

from src.platform.filesystem.model_roots_repository import ModelRootRepository


@pytest.fixture
def repository(mock_db):
    return ModelRootRepository()


class TestModelRootRepository:

    def test_home_root_exists_from_migration(self, repository):
        root = repository.get_root("home")

        assert root is not None
        assert root["kind"] == "home"
        assert root["read_only"] == 0

    def test_home_bindings_cover_every_model_type(self, repository):
        from src.platform.filesystem.model_types import MODEL_TYPES

        bindings = repository.list_bindings()
        home_types = {b["model_type"] for b in bindings if b["root_id"] == "home"}

        assert home_types == set(MODEL_TYPES)

    def test_ensure_root_is_idempotent(self, repository):
        repository.ensure_root(
            root_id="lib1",
            label="Library",
            path="/mnt/library",
            path_key="/mnt/library",
            kind="library",
            read_only=False,
            case_insensitive=False,
            state="online",
            now="2026-01-01T00:00:00+00:00",
        )
        repository.ensure_root(
            root_id="lib1",
            label="Library changed",
            path="/mnt/library",
            path_key="/mnt/library",
            kind="library",
            read_only=False,
            case_insensitive=False,
            state="online",
            now="2026-01-01T00:00:00+00:00",
        )

        root = repository.get_root("lib1")
        assert root["label"] == "Library"

    def test_get_root_by_path_key(self, repository):
        repository.ensure_root(
            root_id="lib2",
            label="Library 2",
            path="/mnt/lib2",
            path_key="/mnt/lib2",
            kind="library",
            read_only=False,
            case_insensitive=False,
            state="online",
            now="2026-01-01T00:00:00+00:00",
        )

        root = repository.get_root_by_path_key("/mnt/lib2")
        assert root is not None
        assert root["id"] == "lib2"

    def test_insert_binding_and_lookups(self, repository):
        repository.ensure_root(
            root_id="lib3",
            label="Library 3",
            path="/mnt/lib3",
            path_key="/mnt/lib3",
            kind="library",
            read_only=False,
            case_insensitive=False,
            state="online",
            now="2026-01-01T00:00:00+00:00",
        )
        max_before = repository.max_position_by_type().get("lora", -1)
        repository.insert_binding(
            root_id="lib3",
            model_type="lora",
            subdir="models/loras",
            position=max_before + 1,
            is_write=False,
        )

        assert repository.has_binding("lib3", "lora")
        bindings = repository.list_bindings("lora")
        assert any(b["root_id"] == "lib3" for b in bindings)

    def test_write_bound_types_includes_home_by_default(self, repository):
        write_types = repository.write_bound_types()

        assert "lora" in write_types

    def test_update_root_state(self, repository):
        repository.ensure_root(
            root_id="lib4",
            label="Library 4",
            path="/mnt/lib4",
            path_key="/mnt/lib4",
            kind="library",
            read_only=False,
            case_insensitive=False,
            state="online",
            now="2026-01-01T00:00:00+00:00",
        )

        repository.update_root_state("lib4", "offline", "unplugged", "2026-01-02T00:00:00+00:00")

        root = repository.get_root("lib4")
        assert root["state"] == "offline"
        assert root["state_reason"] == "unplugged"
