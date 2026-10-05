import json

import pytest

from src.features.generation.grids.repository import GridRepository
from src.features.generation.history_query import GenerationHistoryQuery
from src.features.generation.records import Generation
from src.features.generation.repository import GenerationRepository
from tests.features.generation.grids.conftest import axis, base_request


class History:
    def __init__(self, db):
        self.db = db
        for user_id in ("u1", "u2"):
            with db.get_cursor() as cursor:
                cursor.execute(
                    "INSERT INTO users (id, username, email, password_hash, account_type) VALUES (?, ?, ?, 'x', 'USER')",
                    (user_id, user_id, f"{user_id}@example.test"),
                )
        self.repo = GenerationRepository()
        self.grids = GridRepository()
        self.query = GenerationHistoryQuery(generation_repo=self.repo)
        self.counter = 0

    def single(self, generation_id, user="u1", **fields):
        self.repo.create(Generation(id=generation_id, preset_id="p1", form_data={}, user_id=user, status="completed", **fields))

    def grid(self, grid_id, cols=3, rows=2, user="u1", statuses=None, ratings=None):
        record = self.grids.create(
            user, "p1", "tab", axis("sampler", list(range(cols))),
            axis("steps", list(range(rows))) if rows > 1 else None, True, base_request().model_dump(), {},
        )
        with self.db.get_cursor() as cursor:
            cursor.execute("UPDATE generation_grids SET id = ? WHERE id = ?", (grid_id, record.id))
        ids = []
        for y in range(rows):
            for x in range(cols):
                generation_id = f"{grid_id}-{x}{y}"
                self.repo.create(
                    Generation(
                        id=generation_id, preset_id="p1", form_data={}, user_id=user, status="completed",
                        grid_id=grid_id, grid_x=x, grid_y=y, axis_values={"sampler": str(x)},
                    )
                )
                ids.append(generation_id)
        for generation_id, status in (statuses or {}).items():
            self.set(generation_id, status=status)
        for generation_id, rating in (ratings or {}).items():
            self.set(generation_id, rating=rating)
        return ids

    def set(self, generation_id, **columns):
        with self.db.get_cursor() as cursor:
            for column, value in columns.items():
                cursor.execute(f"UPDATE generations SET {column} = ? WHERE id = ?", (value, generation_id))

    def history(self, **kwargs):
        return self.query.get_history(user_id="u1", include_tags=False, **kwargs)

    def ids(self, result):
        return [item["id"] for item in result["generations"]]


@pytest.fixture
def history(mock_db):
    return History(mock_db)


def test_the_default_listing_returns_one_entry_per_grid_with_the_first_cell_and_summary(history):
    history.single("solo")
    history.grid("g1", cols=3, rows=2)

    result = history.history(group_grids=True)

    assert sorted(history.ids(result)) == ["g1-00", "solo"]
    assert result["total"] == 2
    entry = next(item for item in result["generations"] if item["id"] == "g1-00")
    assert entry["grid"] == {"id": "g1", "cols": 3, "rows": 2, "cell_count": 6}
    assert (entry["grid_id"], entry["grid_x"], entry["grid_y"]) == ("g1", 0, 0)
    assert entry["axis_values"] == {"sampler": "0"}
    assert "grid" not in next(item for item in result["generations"] if item["id"] == "solo")


def test_without_grouping_every_cell_is_listed(history):
    history.grid("g1", cols=3, rows=2)

    result = history.history(group_grids=False)

    assert result["total"] == 6
    assert all("grid" not in item for item in result["generations"])


def test_a_single_axis_grid_is_one_row(history):
    history.grid("g1", cols=4, rows=1)

    entry = history.history(group_grids=True)["generations"][0]

    assert entry["grid"] == {"id": "g1", "cols": 4, "rows": 1, "cell_count": 4}


def test_a_filter_that_matches_the_first_cell_keeps_the_grid_as_one_entry(history):
    history.grid("g1", cols=3, rows=2, ratings={"g1-00": 5, "g1-10": 5})

    result = history.history(group_grids=True, min_rating=5)

    assert history.ids(result) == ["g1-00"]
    assert result["total"] == 1
    assert result["generations"][0]["grid"]["id"] == "g1"
    assert "grid_cols" not in result["generations"][0]


def test_a_filter_that_matches_cells_but_not_the_grid_lists_the_cells_individually(history):
    history.grid("g1", cols=3, rows=2, ratings={"g1-20": 5, "g1-11": 5})

    result = history.history(group_grids=True, min_rating=5)

    assert sorted(history.ids(result)) == ["g1-11", "g1-20"]
    assert result["total"] == 2
    for item in result["generations"]:
        assert "grid" not in item
        assert item["grid_id"] == "g1"
        assert item["grid_cols"] == 3
    cell = next(item for item in result["generations"] if item["id"] == "g1-20")
    assert (cell["grid_x"], cell["grid_y"]) == (2, 0)


def test_a_status_filter_splits_the_same_way(history):
    history.grid("g1", cols=2, rows=2, statuses={"g1-10": "failed"})

    grouped = history.history(group_grids=True)
    failed = history.history(group_grids=True, status="failed")

    assert history.ids(grouped) == ["g1-00"]
    assert history.ids(failed) == ["g1-10"]
    assert failed["total"] == 1


def test_when_the_first_cell_is_deleted_the_next_cell_represents_the_grid(history):
    history.grid("g1", cols=3, rows=1)
    history.repo.delete("g1-00")

    result = history.history(group_grids=True)

    assert history.ids(result) == ["g1-10"]
    assert result["generations"][0]["grid"]["id"] == "g1"


def test_the_grid_id_filter_lists_that_grids_cells_without_grouping(history):
    history.grid("g1", cols=3, rows=1)
    history.grid("g2", cols=2, rows=1)
    history.single("solo")

    result = history.history(group_grids=True, grid_id="g1")

    assert sorted(history.ids(result)) == ["g1-00", "g1-10", "g1-20"]
    assert result["total"] == 3
    assert all("grid" not in item for item in result["generations"])


def test_paging_counts_grids_not_cells(history):
    history.grid("g1", cols=5, rows=5)
    for number in range(4):
        history.single(f"solo-{number}")

    first = history.history(group_grids=True, limit=3, offset=0)
    second = history.history(group_grids=True, limit=3, offset=3)

    assert first["total"] == second["total"] == 5
    assert len(first["generations"]) == 3 and len(second["generations"]) == 2
    assert len(set(history.ids(first)) | set(history.ids(second))) == 5


def test_another_users_grid_never_appears(history):
    history.grid("g2", user="u2")

    assert history.history(group_grids=True)["generations"] == []


def test_grid_fields_are_in_the_history_payload_of_plain_generations(history):
    history.single("solo")

    item = history.history(group_grids=True)["generations"][0]

    assert (item["grid_id"], item["grid_x"], item["grid_y"], item["axis_values"]) == (None, None, None, None)
    json.dumps(item["axis_values"])
