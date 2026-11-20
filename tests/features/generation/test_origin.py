
from src.features.generation.records import Generation
from src.features.generation.repository import GenerationRepository
from src.platform.database import get_cursor

ORIGIN = {"kind": "editor", "edit_id": "ed_1", "step_id": "s7", "task": "edit_region", "run_id": "er_1"}


def make(generation_id="g1"):
    with get_cursor() as cursor:
        cursor.execute(
            "INSERT OR IGNORE INTO users (id, username, email, password_hash) VALUES ('u1', 'u1', 'u1@example.com', 'x')"
        )
    return GenerationRepository().create(Generation(id=generation_id, preset_id="p", form_data={}, user_id="u1"))


def test_a_generation_reads_back_with_no_origin_until_one_is_set(mock_db):
    repository = GenerationRepository()
    generation = make()

    assert generation.origin is None and generation.to_dict()["origin"] is None
    assert repository.update_origin("g1", ORIGIN) is True

    stored = repository.get_by_id("g1")
    assert stored.origin == ORIGIN and stored.to_dict()["origin"] == ORIGIN


def test_updating_the_origin_of_a_missing_generation_changes_nothing(mock_db):
    assert GenerationRepository().update_origin("nope", ORIGIN) is False


def test_a_row_written_before_origin_existed_reads_back_none(mock_db):
    make()
    with get_cursor() as cursor:
        cursor.execute("UPDATE generations SET origin = NULL")

    assert GenerationRepository().get_by_id("g1").origin is None
