import functools
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.features.collections import operations
from src.features.collections.repository import CollectionRepository
from src.features.collections.routes import CollectionController, build_router
from src.features.generation.history_facade import GenerationHistoryFacade
from src.features.generation.hooks import GENERATION_HOOKS
from src.features.generation.records import File, Generation
from src.features.generation.repository import GenerationRepository
from src.features.library import operations as library_operations
from src.features.library.collaborators import LibraryCollaborators
from src.features.library.repository import LibraryRepository
from src.features.media.file_resolver import FilePathResolver
from src.features.media.records import Upload
from src.features.media.upload_repository import UploadRepository
from src.platform.filesystem.file_store import FileStore
from src.platform.filesystem.storage_driver import LocalFileStorageDriver
from src.platform.http.errors import InvalidInputError, NotFoundError
from src.platform.security.current_user import get_current_active_user
from src.platform.security.user import AccountType, User
from src.platform.util.ids import generate_ulid

DATE = "2026-11-10"


class _Plugins:
    def __init__(self):
        self.refuse = {}

    def execute_hook(self, hook, initial_data=None):
        context = Mock()
        context.data = dict(initial_data or {})
        if hook == GENERATION_HOOKS.before_delete and context.data.get("generation_id") in self.refuse:
            context.data["blocked"] = True
            context.data["block_reason"] = self.refuse[context.data["generation_id"]]
        return context, []


class _StorageSettings:
    def __init__(self, storage_dir: Path):
        self._storage_dir = str(storage_dir)

    def get_file_storage_directory(self, user_id=None) -> str:
        return self._storage_dir


@pytest.fixture
def database(mock_db):
    with patch("src.platform.database.database.db", mock_db):
        yield mock_db


@pytest.fixture
def storage(test_storage):
    return test_storage


@pytest.fixture
def plugins():
    return _Plugins()


@pytest.fixture
def facade(database, storage, plugins):
    return GenerationHistoryFacade(
        generation_repo=GenerationRepository(),
        file_service=FileStore(str(storage)),
        plugin_registry=plugins,
        run_report_repository=Mock(get=Mock(return_value=None)),
    )


@pytest.fixture
def resolver(storage):
    return FilePathResolver(_StorageSettings(storage))


@pytest.fixture
def library(database, storage, resolver):
    return LibraryCollaborators(
        repository=LibraryRepository(),
        upload_repository=UploadRepository(),
        tag_repository=Mock(),
        file_repository=Mock(),
        file_resolver=resolver,
        file_store=FileStore(str(storage)),
        storage_driver=LocalFileStorageDriver(str(storage)),
    )


@pytest.fixture
def deleters(facade, library):
    return {
        "history": facade.delete,
        "library": functools.partial(library_operations.delete_item, library),
    }


@pytest.fixture
def collections(database):
    return CollectionRepository()


def _execute(database, sql, params=()):
    with database.get_cursor() as cursor:
        cursor.execute(sql, params)
        return [tuple(row) for row in cursor.fetchall()]


def _count(database, table, where="1 = 1", params=()):
    return _execute(database, f"SELECT COUNT(*) FROM {table} WHERE {where}", params)[0][0]


def _user(database, name):
    user_id = generate_ulid()
    _execute(
        database,
        "INSERT INTO users (id, username, email, password_hash) VALUES (?, ?, ?, ?)",
        (user_id, name, f"{name}@example.com", "hashed"),
    )
    return user_id


def _generation(storage, user_id):
    generation = Generation(
        id=generate_ulid(), preset_id="image", form_data={"prompt": "p"}, user_id=user_id, status="completed",
    )
    repository = GenerationRepository()
    repository.create(generation)
    folder = storage / "generations" / DATE / generation.id
    folder.mkdir(parents=True)
    (folder / "1.png").write_bytes(b"png")
    repository.add_file(generation.id, File(
        file_path=f"generations/{DATE}/{generation.id}/1.png", file_type="IMAGE", user_id=user_id,
    ))
    return generation.id, folder / "1.png"


def _upload(resolver, user_id, name):
    path = resolver.get_uploads_directory(user_id) / name
    path.write_bytes(b"upload")
    upload = UploadRepository().create(Upload(
        user_id=user_id, filename=name, original_filename=name,
        media_type="image", mime_type="image/png", file_size=6,
    ))
    return upload.id, path


def _file_into(database, collection_id, generation_id):
    _execute(
        database,
        "INSERT INTO collection_generations (collection_id, generation_id) VALUES (?, ?)",
        (collection_id, generation_id),
    )


def test_history_entries_and_their_files_are_deleted_and_the_collection_stays(database, storage, collections, deleters):
    owner = _user(database, "owner")
    collection = collections.create("Keepers", owner, "history")
    first, first_path = _generation(storage, owner)
    second, second_path = _generation(storage, owner)
    outside, outside_path = _generation(storage, owner)
    collections.add_members(collection.id, [first, second], owner, "history")

    result = operations.delete_entries(collections, deleters, collection.id, owner)

    assert result == {"deleted": 2, "blocked": [], "total": 2}
    assert _count(database, "generations", "id IN (?, ?)", (first, second)) == 0
    assert not first_path.exists()
    assert not second_path.exists()
    assert _count(database, "generations", "id = ?", (outside,)) == 1
    assert outside_path.is_file()
    kept = collections.get_by_id(collection.id, "history", owner)
    assert kept is not None
    assert kept.item_count == 0


def test_library_entries_and_their_files_are_deleted_and_the_collection_stays(database, resolver, collections, deleters):
    owner = _user(database, "owner")
    collection = collections.create("Refs", owner, "library")
    first, first_path = _upload(resolver, owner, "a.png")
    second, second_path = _upload(resolver, owner, "b.png")
    outside, outside_path = _upload(resolver, owner, "c.png")
    collections.add_upload_members(collection.id, [first, second], owner, "library")

    result = operations.delete_entries(collections, deleters, collection.id, owner)

    assert result == {"deleted": 2, "blocked": [], "total": 2}
    assert _count(database, "uploads", "id IN (?, ?)", (first, second)) == 0
    assert not first_path.exists()
    assert not second_path.exists()
    assert _count(database, "uploads", "id = ?", (outside,)) == 1
    assert outside_path.is_file()
    kept = collections.get_by_id(collection.id, "library", owner)
    assert kept is not None
    assert kept.item_count == 0


def test_items_of_other_collections_stay(database, storage, resolver, collections, deleters):
    owner = _user(database, "owner")
    stranger = _user(database, "stranger")
    target = collections.create("Target", owner, "history")
    sibling = collections.create("Sibling", owner, "history", parent_id=target.id)
    refs = collections.create("Refs", owner, "library")
    foreign = collections.create("Foreign", stranger, "history")
    doomed, doomed_path = _generation(storage, owner)
    nested, nested_path = _generation(storage, owner)
    upload_id, upload_path = _upload(resolver, owner, "a.png")
    theirs, theirs_path = _generation(storage, stranger)
    collections.add_members(target.id, [doomed], owner, "history")
    collections.add_members(sibling.id, [nested], owner, "history")
    collections.add_upload_members(refs.id, [upload_id], owner, "library")
    collections.add_members(foreign.id, [theirs], stranger, "history")

    result = operations.delete_entries(collections, deleters, target.id, owner)

    assert result == {"deleted": 1, "blocked": [], "total": 1}
    assert not doomed_path.exists()
    assert nested_path.is_file()
    assert upload_path.is_file()
    assert theirs_path.is_file()
    assert collections.member_ids(sibling.id, "history") == [nested]
    assert collections.member_ids(refs.id, "library") == [upload_id]
    assert collections.member_ids(foreign.id, "history") == [theirs]


def test_another_users_item_in_the_collection_is_reported_and_left_alone(database, storage, collections, deleters):
    owner = _user(database, "owner")
    stranger = _user(database, "stranger")
    collection = collections.create("Mixed", owner, "history")
    mine, mine_path = _generation(storage, owner)
    theirs, theirs_path = _generation(storage, stranger)
    collections.add_members(collection.id, [mine], owner, "history")
    _file_into(database, collection.id, theirs)

    result = operations.delete_entries(collections, deleters, collection.id, owner)

    assert result["total"] == 2
    assert result["deleted"] == 1
    assert [entry["id"] for entry in result["blocked"]] == [theirs]
    assert result["blocked"][0]["reason"]
    assert not mine_path.exists()
    assert _count(database, "generations", "id = ?", (theirs,)) == 1
    assert theirs_path.is_file()
    assert collections.member_ids(collection.id, "history") == [theirs]


def test_another_users_collection_answers_not_found_and_keeps_its_items(database, storage, resolver, collections, deleters):
    owner = _user(database, "owner")
    caller = _user(database, "caller")
    history = collections.create("Theirs", owner, "history")
    library = collections.create("Their refs", owner, "library")
    generation_id, generation_path = _generation(storage, owner)
    upload_id, upload_path = _upload(resolver, owner, "a.png")
    collections.add_members(history.id, [generation_id], owner, "history")
    collections.add_upload_members(library.id, [upload_id], owner, "library")

    for collection_id in (history.id, library.id):
        with pytest.raises(NotFoundError) as raised:
            operations.delete_entries(collections, deleters, collection_id, caller)
        assert raised.value.status_code == 404
        assert raised.value.code == "collection_not_found"

    assert generation_path.is_file()
    assert upload_path.is_file()
    assert collections.member_ids(history.id, "history") == [generation_id]
    assert collections.member_ids(library.id, "library") == [upload_id]


def test_a_missing_collection_answers_not_found(database, collections, deleters):
    owner = _user(database, "owner")

    with pytest.raises(NotFoundError):
        operations.delete_entries(collections, deleters, "missing", owner)


def test_a_prompts_collection_is_refused(database, collections, deleters):
    owner = _user(database, "owner")
    collection = collections.create("Prompts", owner, "prompts")

    with pytest.raises(InvalidInputError) as raised:
        operations.delete_entries(collections, deleters, collection.id, owner)

    assert raised.value.status_code == 400
    assert raised.value.code == "collection_scope_unsupported"
    assert collections.get_by_id(collection.id, "prompts", owner) is not None


def test_a_vetoed_generation_is_reported_with_its_reason_and_stays_in_the_collection(database, storage, plugins, collections, deleters):
    owner = _user(database, "owner")
    collection = collections.create("Brewing", owner, "history")
    held, held_path = _generation(storage, owner)
    free, free_path = _generation(storage, owner)
    collections.add_members(collection.id, [held, free], owner, "history")
    plugins.refuse[held] = "Brew 3 still needs files from this generation. Stop or finish that brew first."

    result = operations.delete_entries(collections, deleters, collection.id, owner)

    assert result == {
        "deleted": 1,
        "blocked": [{"id": held, "reason": "Brew 3 still needs files from this generation. Stop or finish that brew first."}],
        "total": 2,
    }
    assert held_path.is_file()
    assert not free_path.exists()
    assert collections.member_ids(collection.id, "history") == [held]


def test_one_failure_does_not_stop_the_rest_and_its_detail_stays_private(database, storage, collections, deleters):
    owner = _user(database, "owner")
    collection = collections.create("Shaky", owner, "history")
    broken, _ = _generation(storage, owner)
    fine, fine_path = _generation(storage, owner)
    collections.add_members(collection.id, [broken, fine], owner, "history")
    real = deleters["history"]

    def flaky(generation_id, user_id):
        if generation_id == broken:
            raise OSError("disk at /srv/secret is unhappy")
        return real(generation_id, user_id)

    result = operations.delete_entries(collections, {**deleters, "history": flaky}, collection.id, owner)

    assert result["deleted"] == 1
    assert result["total"] == 2
    assert result["blocked"] == [{"id": broken, "reason": "This item could not be deleted"}]
    assert not fine_path.exists()


def _client(controller, user_id):
    app = FastAPI()
    app.include_router(build_router(SimpleNamespace(collection_controller=controller)))
    app.dependency_overrides[get_current_active_user] = lambda: User(
        id=user_id, username="u", email="u@example.com", password_hash="h", account_type=AccountType.USER,
    )
    return TestClient(app)


def test_the_route_answers_the_counts(database, storage, collections, deleters):
    owner = _user(database, "owner")
    collection = collections.create("Keepers", owner, "history")
    generation_id, path = _generation(storage, owner)
    collections.add_members(collection.id, [generation_id], owner, "history")

    response = _client(CollectionController(collections, deleters), owner).post(
        f"/api/collections/{collection.id}/delete-entries"
    )

    assert response.status_code == 200
    assert response.json()["data"] == {"deleted": 1, "blocked": [], "total": 1}
    assert not path.exists()


def test_the_route_answers_404_for_someone_elses_collection(database, storage, collections, deleters):
    owner = _user(database, "owner")
    caller = _user(database, "caller")
    collection = collections.create("Theirs", owner, "history")
    generation_id, path = _generation(storage, owner)
    collections.add_members(collection.id, [generation_id], owner, "history")

    response = _client(CollectionController(collections, deleters), caller).post(
        f"/api/collections/{collection.id}/delete-entries"
    )

    assert response.status_code == 404
    assert response.json()["detail"]["error"] == "collection_not_found"
    assert path.is_file()


def test_the_route_answers_400_for_a_prompts_collection(database, collections, deleters):
    owner = _user(database, "owner")
    collection = collections.create("Prompts", owner, "prompts")

    response = _client(CollectionController(collections, deleters), owner).post(
        f"/api/collections/{collection.id}/delete-entries"
    )

    assert response.status_code == 400
    assert response.json()["detail"]["error"] == "collection_scope_unsupported"
