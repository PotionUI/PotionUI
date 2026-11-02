import json
from unittest.mock import Mock, patch

import pytest

from src.features.generation.history_facade import GenerationHistoryFacade
from src.features.generation.records import File, Generation
from src.features.generation.repository import GenerationRepository
from src.features.generation.file_repository import file_repo
from src.platform.filesystem.file_store import FileStore
from src.platform.util.ids import generate_ulid

DATE = "2026-10-28"


class _NoopPlugins:
    def execute_hook(self, hook, initial_data=None):
        context = Mock()
        context.data = dict(initial_data or {})
        return context, []


@pytest.fixture
def database(mock_db):
    with patch("src.platform.database.database.db", mock_db):
        yield mock_db


@pytest.fixture
def storage(test_storage):
    return test_storage


@pytest.fixture
def facade(database, storage):
    return GenerationHistoryFacade(
        generation_repo=GenerationRepository(),
        file_service=FileStore(str(storage)),
        plugin_registry=_NoopPlugins(),
        run_report_repository=Mock(get=Mock(return_value=None)),
    )


def _execute(database, sql, params=()):
    with database.get_cursor() as cursor:
        cursor.execute(sql, params)
        return [tuple(row) for row in cursor.fetchall()]


def _count(database, table, where="1 = 1", params=()):
    return _execute(database, f"SELECT COUNT(*) FROM {table} WHERE {where}", params)[0][0]


def _user(database, name="leftovers"):
    user_id = generate_ulid()
    _execute(
        database,
        "INSERT INTO users (id, username, email, password_hash) VALUES (?, ?, ?, ?)",
        (user_id, name, f"{name}@example.com", "hashed"),
    )
    return user_id


def _generation(user_id):
    generation = Generation(
        id=generate_ulid(), preset_id="video", form_data={"prompt": "p"}, user_id=user_id, status="completed",
    )
    GenerationRepository().create(generation)
    return generation.id


def _video(storage, generation_id, user_id, name="1"):
    folder = storage / "generations" / DATE / generation_id
    (folder / "thumbnails").mkdir(parents=True, exist_ok=True)
    (folder / f"{name}.mp4").write_bytes(b"video")
    for size in ("small", "medium", "large"):
        (folder / "thumbnails" / f"{name}_{size}.jpg").write_bytes(b"jpg")
        (folder / "thumbnails" / f"{name}_{size}_animated.webp").write_bytes(b"webp")
    return File(
        file_path=f"generations/{DATE}/{generation_id}/{name}.mp4",
        file_type="VIDEO",
        user_id=user_id,
        thumbnail_small=f"thumbnails/{name}_small.jpg",
        thumbnail_medium=f"thumbnails/{name}_medium.jpg",
        thumbnail_large=f"thumbnails/{name}_large.jpg",
    )


def _notify(database, user_id, type_, generation_id):
    _execute(
        database,
        "INSERT INTO notifications (id, user_id, category, level, title, metadata, type) VALUES (?, ?, 'generation', 'info', 't', ?, ?)",
        (generate_ulid(), user_id, json.dumps({"generation_id": generation_id}), type_),
    )


def test_a_deleted_generation_takes_its_rows_its_thumbnails_and_its_folder(database, storage, facade):
    user_id = _user(database)
    generation_id = _generation(user_id)
    neighbour_id = _generation(user_id)
    created = GenerationRepository().add_file(generation_id, _video(storage, generation_id, user_id))
    GenerationRepository().add_file(neighbour_id, _video(storage, neighbour_id, user_id))
    _execute(
        database,
        "INSERT INTO media_system_tags (id, file_id, generation_id, tag, confidence, provenance) VALUES (?, ?, ?, 'cat', 0.9, 'auto')",
        (generate_ulid(), created.id, generation_id),
    )
    _execute(database, "INSERT INTO media_index_queue (id, file_id, pass_type) VALUES (?, ?, 'tags')", (generate_ulid(), created.id))
    for type_ in ("generation.completed", "generation.failed", "generation.warning", "generation.failed_admin_alert"):
        _notify(database, user_id, type_, generation_id)
    _notify(database, user_id, "generation.completed", neighbour_id)
    _execute(database, "INSERT INTO generation_stats (id, generation_id) VALUES (?, ?)", (generate_ulid(), generation_id))
    _execute(
        database,
        "INSERT INTO limit_events (id, user_id, kind, created_at, ref_id) VALUES (?, ?, 'generations_per_day', '2026-10-28T10:00:00+00:00', ?)",
        (generate_ulid(), user_id, generation_id),
    )

    result = facade.delete(generation_id, user_id)

    assert result["files_failed_fs"] == 0
    assert result["files_deleted_fs"] == 7
    assert _count(database, "files", "id = ?", (created.id,)) == 0
    assert _count(database, "media_system_tags", "file_id = ?", (created.id,)) == 0
    assert _count(database, "media_index_queue", "file_id = ?", (created.id,)) == 0
    assert _execute(database, "SELECT type FROM notifications WHERE json_extract(metadata, '$.generation_id') = ?", (generation_id,)) == [
        ("generation.failed_admin_alert",)
    ]
    assert _count(database, "notifications", "json_extract(metadata, '$.generation_id') = ?", (neighbour_id,)) == 1
    assert _count(database, "generation_stats", "generation_id = ?", (generation_id,)) == 1
    assert _count(database, "limit_events", "ref_id = ?", (generation_id,)) == 1
    assert not (storage / "generations" / DATE / generation_id).exists()
    assert (storage / "generations" / DATE / neighbour_id / "thumbnails" / "1_medium_animated.webp").is_file()
    assert len(GenerationRepository().get_files(neighbour_id)) == 1


def test_a_file_another_generation_still_lists_keeps_its_row_and_its_bytes(database, storage, facade):
    user_id = _user(database)
    generation_id = _generation(user_id)
    other_id = _generation(user_id)
    created = GenerationRepository().add_file(generation_id, _video(storage, generation_id, user_id))
    file_repo.associate_with_generation(other_id, created.id)

    facade.delete(generation_id, user_id)

    assert [file.id for file in GenerationRepository().get_files(other_id)] == [created.id]
    assert (storage / created.file_path).is_file()
    assert (storage / "generations" / DATE / generation_id / "thumbnails" / "1_small_animated.webp").is_file()


def test_a_phrasebook_preview_keeps_the_file_it_shows(database, storage, facade):
    user_id = _user(database)
    generation_id = _generation(user_id)
    created = GenerationRepository().add_file(generation_id, _video(storage, generation_id, user_id))
    category_id = generate_ulid()
    value_id = generate_ulid()
    _execute(
        database,
        "INSERT INTO phrasebook_categories (id, name, path, user_id) VALUES (?, 'c', 'c', ?)",
        (category_id, user_id),
    )
    _execute(
        database,
        "INSERT INTO phrasebook_values (id, category_id, label, value, user_id, preview_file_id, preview_generation_id) VALUES (?, ?, 'l', 'v', ?, ?, ?)",
        (value_id, category_id, user_id, created.id, generation_id),
    )

    facade.delete(generation_id, user_id)

    assert _execute(database, "SELECT preview_file_id FROM phrasebook_values WHERE id = ?", (value_id,)) == [(created.id,)]
    assert _count(database, "files", "id = ?", (created.id,)) == 1
    assert (storage / created.file_path).is_file()


def test_bytes_another_file_row_points_at_stay_while_the_generation_row_goes(database, storage, facade):
    user_id = _user(database)
    generation_id = _generation(user_id)
    created = GenerationRepository().add_file(generation_id, _video(storage, generation_id, user_id))
    preview = file_repo.create(File(file_path=created.file_path, file_type="VIDEO", user_id=user_id))

    facade.delete(generation_id, user_id)

    assert _count(database, "files", "id = ?", (created.id,)) == 0
    assert _count(database, "files", "id = ?", (preview.id,)) == 1
    assert (storage / created.file_path).is_file()


def test_releasable_files_are_the_generation_files_nothing_else_needs(database, storage):
    user_id = _user(database)
    generation_id = _generation(user_id)
    other_id = _generation(user_id)
    repository = GenerationRepository()
    own = repository.add_file(generation_id, _video(storage, generation_id, user_id, "1"))
    shared = repository.add_file(generation_id, _video(storage, generation_id, user_id, "2"))
    file_repo.associate_with_generation(other_id, shared.id)

    assert [file.id for file in repository.releasable_files(generation_id)] == [own.id]


def test_a_files_storage_keys_are_itself_its_thumbnails_and_their_animated_siblings():
    record = File(
        file_path="generations/2026-10-28/g/1.mp4",
        file_type="VIDEO",
        user_id="u",
        thumbnail_small="thumbnails/1_small.jpg",
        thumbnail_large="thumbnails/1_large.jpg",
    )

    assert record.storage_keys() == [
        "generations/2026-10-28/g/1.mp4",
        "generations/2026-10-28/g/thumbnails/1_small.jpg",
        "generations/2026-10-28/g/thumbnails/1_small_animated.webp",
        "generations/2026-10-28/g/thumbnails/1_large.jpg",
        "generations/2026-10-28/g/thumbnails/1_large_animated.webp",
    ]
