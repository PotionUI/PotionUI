import sqlite3

import pytest

from src.features.elixirs.identity import ROOT_INPUT, asset_key, invocation_key
from src.features.elixirs.records import (
    INVOCATIONS_TABLE,
    RUNS_TABLE,
    ElixirHasActiveRuns,
    RevisionConflict,
)
from src.features.elixirs.repository import ElixirRepository


def insert_run(db, run_id, elixir_id=None, user_id="u1", status="pending", key=None):
    with db.get_cursor() as cursor:
        cursor.execute(
            f"""
            INSERT INTO {RUNS_TABLE}
                (id, elixir_id, user_id, elixir_revision, scope, run_mode, status, graph,
                 idempotency_key, created_at, updated_at)
            VALUES (?, ?, ?, 1, 'graph', 'review', ?, '{{}}', ?, 'now', 'now')
            """,
            (run_id, elixir_id, user_id, status, key),
        )


def insert_invocation(db, invocation_id, run_id="r1", node_id="n", edge_id=None, input_key=ROOT_INPUT,
                      attempt=1, key=None, generation_id=None, accepted=0):
    key = key or invocation_key(run_id, node_id, edge_id, input_key, attempt)
    with db.get_cursor() as cursor:
        cursor.execute(
            f"""
            INSERT INTO {INVOCATIONS_TABLE}
                (id, run_id, user_id, node_id, edge_id, ordinal, input_key, attempt,
                 idempotency_key, generation_id, accepted, created_at, updated_at)
            VALUES (?, ?, 'u1', ?, ?, 0, ?, ?, ?, ?, ?, 'now', 'now')
            """,
            (invocation_id, run_id, node_id, edge_id, input_key, attempt, key, generation_id, accepted),
        )


def test_one_generation_per_invocation_slot(scratch_db):
    insert_run(scratch_db, "r1")
    insert_invocation(scratch_db, "i1", generation_id="g1")

    with pytest.raises(sqlite3.IntegrityError):
        insert_invocation(scratch_db, "i2")
    with pytest.raises(sqlite3.IntegrityError):
        insert_invocation(scratch_db, "i3", key="elixir:other")
    with pytest.raises(sqlite3.IntegrityError):
        insert_invocation(scratch_db, "i4", node_id="other", generation_id="g1")

    insert_invocation(scratch_db, "i5", attempt=2)
    insert_invocation(scratch_db, "i6", edge_id="e1", input_key="library:x")
    insert_invocation(scratch_db, "i7", edge_id="e2", input_key="library:x")


def test_only_one_accepted_attempt_per_input(scratch_db):
    insert_run(scratch_db, "r1")
    insert_invocation(scratch_db, "i1", edge_id="e", input_key="library:x", accepted=1)

    with pytest.raises(sqlite3.IntegrityError):
        insert_invocation(scratch_db, "i2", edge_id="e", input_key="library:x", attempt=2, accepted=1)
    insert_invocation(scratch_db, "i3", edge_id="e", input_key="library:x", attempt=3, accepted=0)


def test_a_brew_request_key_is_unique_per_user(scratch_db):
    insert_run(scratch_db, "r1", key="k")
    insert_run(scratch_db, "r2", user_id="u2", key="k")
    insert_run(scratch_db, "r3")
    insert_run(scratch_db, "r4")

    with pytest.raises(sqlite3.IntegrityError):
        insert_run(scratch_db, "r5", key="k")


def test_invocation_keys_change_with_every_part_and_stay_stable():
    base = invocation_key("run", "node", "edge", "library:x", 1)

    assert base == invocation_key("run", "node", "edge", "library:x", 1)
    assert base.startswith("elixir:") and len(base) <= 200
    variants = {
        invocation_key("run2", "node", "edge", "library:x", 1),
        invocation_key("run", "node2", "edge", "library:x", 1),
        invocation_key("run", "node", "edge2", "library:x", 1),
        invocation_key("run", "node", "edge", "library:y", 1),
        invocation_key("run", "node", "edge", "library:x", 2),
    }
    assert base not in variants and len(variants) == 5
    assert invocation_key("run", "node", None, None, 1) == invocation_key("run", "node", "", ROOT_INPUT, 1)


@pytest.mark.parametrize("attempt", [0, -1, True, 1.0])
def test_invocation_attempts_start_at_one(attempt):
    with pytest.raises(ValueError):
        invocation_key("run", "node", None, None, attempt)


def test_asset_keys_name_the_typed_identity():
    assert asset_key({"kind": "history", "generation_id": "g", "file_id": "7", "media_type": "image"}) == "history:g:7"
    assert asset_key({"kind": "library", "item_id": "lib"}) == "library:lib"
    with pytest.raises(ValueError):
        asset_key({"kind": "path", "item_id": "/etc/passwd"})


def test_updates_require_the_current_revision(scratch_db):
    repository = ElixirRepository()
    record = repository.create("u1", "First", "review", {"nodes": [], "edges": []})

    updated = repository.update(record.id, "u1", 1, name="Second")

    assert updated.revision == 2 and updated.name == "Second"
    with pytest.raises(RevisionConflict) as conflict:
        repository.update(record.id, "u1", 1, name="Third")
    assert conflict.value.current_revision == 2
    assert repository.update(record.id, "u2", 2, name="Theirs") is None


def test_deleting_keeps_runs_and_refuses_while_brewing(scratch_db):
    repository = ElixirRepository()
    record = repository.create("u1", "Brewing", "review", {"nodes": [], "edges": []})
    insert_run(scratch_db, "r1", elixir_id=record.id, status="running")

    with pytest.raises(ElixirHasActiveRuns):
        repository.delete(record.id, "u1")

    with scratch_db.get_cursor() as cursor:
        cursor.execute(f"UPDATE {RUNS_TABLE} SET status = 'completed' WHERE id = 'r1'")
    assert repository.delete(record.id, "u1")
    with scratch_db.get_cursor() as cursor:
        cursor.execute(f"SELECT elixir_id, graph FROM {RUNS_TABLE} WHERE id = 'r1'")
        run = cursor.fetchone()
    assert run is not None and run["elixir_id"] is None
