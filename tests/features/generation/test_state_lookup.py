from types import SimpleNamespace

from src.features.generation.state_lookup import resolve_generation_state


class _Repository:
    def __init__(self, rows):
        self.rows = rows
        self.asked = []

    def get_by_id(self, generation_id):
        self.asked.append(generation_id)
        return self.rows.get(generation_id)


def _row(owner):
    return SimpleNamespace(user_id=owner, to_dict=lambda: {"id": "g1", "source": "db"})


def test_live_record_wins_and_the_repository_is_not_read():
    repository = _Repository({"g1": _row("db-owner")})
    live = SimpleNamespace(user_id="live-owner", model_dump=lambda: {"id": "g1", "source": "live"})

    view = resolve_generation_state(live, repository, "g1")

    assert (view.owner_id, view.payload["source"]) == ("live-owner", "live")
    assert repository.asked == []


def test_stored_row_is_used_when_nothing_is_live():
    view = resolve_generation_state(None, _Repository({"g1": _row("db-owner")}), "g1")

    assert (view.owner_id, view.payload["source"]) == ("db-owner", "db")


def test_unknown_generation_resolves_to_none():
    assert resolve_generation_state(None, _Repository({}), "g1") is None
