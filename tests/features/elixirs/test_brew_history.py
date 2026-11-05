from types import SimpleNamespace

import pytest

from src.features.elixirs.listing import summaries
from src.features.elixirs.repository import ElixirRepository
from src.features.elixirs.runtime import ElixirRuntime
from tests.features.elixirs.test_brew_api import TEMPLATES, brew, definition, elixir, harness

GRAPH = {
    "nodes": [
        {"id": "photos", "kind": "media", "title": "Photos"},
        {"id": "sharpen", "kind": "preset", "title": "Sharpen faces"},
        {"id": "blank", "kind": "preset", "title": " "},
    ],
    "edges": [],
}


def seed(record, created_at, status="completed", scope="graph", run_mode="review", node="sharpen", user="alice", states=None):
    from src.features.elixirs.run_repository import RunStore

    store = RunStore()
    with store.transaction() as cursor:
        run_id = store.insert_run(cursor, {
            "elixir_id": record.id,
            "user_id": user,
            "elixir_revision": 1,
            "scope": scope,
            "scope_node_id": node if scope == "node" else None,
            "run_mode": run_mode,
            "status": status,
            "graph": GRAPH,
            "node_states": states or {},
        })
        cursor.execute("UPDATE elixir_runs SET created_at = ? WHERE id = ?", (created_at, run_id))
    return run_id


def listing(harness, record, **params):
    response = harness.client.get(f"/api/elixirs/{record.id}/runs", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def ids(payload):
    return [run["id"] for run in payload["runs"]]


@pytest.fixture
def history(harness):
    record = elixir()
    runs = {
        "done": seed(record, "2026-11-01T10:00:00+00:00", "completed"),
        "partial": seed(record, "2026-11-02T10:00:00+00:00", "partial", run_mode="automatic"),
        "failed": seed(record, "2026-11-03T10:00:00+00:00", "failed"),
        "interrupted": seed(record, "2026-11-03T12:00:00+00:00", "interrupted"),
        "cancelled": seed(record, "2026-11-04T10:00:00+00:00", "cancelled", scope="node"),
        "running": seed(record, "2026-11-05T09:00:00+00:00", "running", scope="node"),
        "needs": seed(record, "2026-11-05T23:59:59+00:00", "needs_attention", node="blank", scope="node"),
        "stopping": seed(record, "2026-11-06T00:00:01+00:00", "cancelling"),
    }
    return SimpleNamespace(record=record, runs=runs, harness=harness)


def test_runs_are_newest_first_with_total_and_active(history):
    payload = listing(history.harness, history.record)

    assert ids(payload) == [history.runs[name] for name in ("stopping", "needs", "running", "cancelled", "interrupted", "failed", "partial", "done")]
    assert (payload["total"], payload["active"], payload["limit"], payload["offset"]) == (8, 3, 50, 0)


@pytest.mark.parametrize(
    "status,expected",
    [
        ("all", 8),
        ("active", 3),
        ("finished", 2),
        ("failed", 2),
        ("cancelled", 1),
    ],
)
def test_status_groups(history, status, expected):
    payload = listing(history.harness, history.record, status=status)

    assert payload["total"] == expected
    assert payload["active"] == 3


def test_status_groups_map_to_the_real_statuses(history):
    wanted = {
        "active": {"stopping", "needs", "running"},
        "finished": {"done", "partial"},
        "failed": {"failed", "interrupted"},
        "cancelled": {"cancelled"},
    }
    for status, names in wanted.items():
        assert set(ids(listing(history.harness, history.record, status=status))) == {history.runs[name] for name in names}


def test_an_unknown_status_is_rejected(history):
    response = history.harness.client.get(f"/api/elixirs/{history.record.id}/runs", params={"status": "bogus"})

    assert response.status_code == 422


def test_date_range_is_inclusive_on_whole_days(history):
    one_day = listing(history.harness, history.record, **{"from": "2026-11-03", "to": "2026-11-03"})
    from_only = listing(history.harness, history.record, **{"from": "2026-11-05"})
    to_only = listing(history.harness, history.record, to="2026-11-02")
    last_second = listing(history.harness, history.record, **{"from": "2026-11-05", "to": "2026-11-05"})

    assert set(ids(one_day)) == {history.runs["failed"], history.runs["interrupted"]}
    assert set(ids(from_only)) == {history.runs["running"], history.runs["needs"], history.runs["stopping"]}
    assert set(ids(to_only)) == {history.runs["done"], history.runs["partial"]}
    assert set(ids(last_second)) == {history.runs["running"], history.runs["needs"]}
    assert one_day["total"] == 2
    assert one_day["active"] == 3


@pytest.mark.parametrize("value", ["yesterday", "2026-13-01", "2026-1-1", "2026-11-05T10:00:00"])
def test_a_malformed_date_is_rejected(history, value):
    for key in ("from", "to"):
        response = history.harness.client.get(f"/api/elixirs/{history.record.id}/runs", params={key: value})

        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "invalid_date"


def test_run_mode_filter(history):
    automatic = listing(history.harness, history.record, run_mode="automatic")
    review = listing(history.harness, history.record, run_mode="review")

    assert ids(automatic) == [history.runs["partial"]]
    assert review["total"] == 7


def test_scope_filter(history):
    node = listing(history.harness, history.record, scope="node")
    elixir_scope = listing(history.harness, history.record, scope="elixir")

    assert set(ids(node)) == {history.runs[name] for name in ("cancelled", "running", "needs")}
    assert elixir_scope["total"] == 5


def test_search_by_brew_number_and_by_step_name(history):
    by_number = listing(history.harness, history.record, q="3")
    hashed = listing(history.harness, history.record, q="#3")
    by_name = listing(history.harness, history.record, q="SHARPEN")
    untitled = listing(history.harness, history.record, q="untitled preset")
    wildcard = listing(history.harness, history.record, q="%")
    nothing = listing(history.harness, history.record, q="zzz")

    assert ids(by_number) == ids(hashed) == [history.runs["failed"]]
    assert set(ids(by_name)) == {history.runs["cancelled"], history.runs["running"]}
    assert ids(untitled) == [history.runs["needs"]]
    assert wildcard["total"] == 0
    assert nothing["total"] == 0 and nothing["runs"] == []


def test_filters_combine(history):
    payload = listing(history.harness, history.record, status="active", scope="node", q="sharpen")

    assert ids(payload) == [history.runs["running"]]
    assert payload["total"] == 1
    assert payload["active"] == 3


def test_paging_keeps_total_and_active(history):
    first = listing(history.harness, history.record, limit=3)
    second = listing(history.harness, history.record, limit=3, offset=3)
    last = listing(history.harness, history.record, limit=3, offset=6)
    beyond = listing(history.harness, history.record, limit=3, offset=30)

    assert ids(first) + ids(second) + ids(last) == ids(listing(history.harness, history.record))
    assert [len(page["runs"]) for page in (first, second, last, beyond)] == [3, 3, 2, 0]
    assert {page["total"] for page in (first, second, last, beyond)} == {8}
    assert {page["active"] for page in (first, second, last, beyond)} == {3}
    assert (second["limit"], second["offset"]) == (3, 3)


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 201}, {"offset": -1}])
def test_paging_bounds(history, params):
    response = history.harness.client.get(f"/api/elixirs/{history.record.id}/runs", params=params)

    assert response.status_code == 422


def test_a_page_of_two_hundred_is_allowed(history):
    assert listing(history.harness, history.record, limit=200)["limit"] == 200


def test_numbers_are_a_stable_per_elixir_sequence(history):
    other = elixir()
    other_run = seed(other, "2026-11-01T10:00:00+00:00")
    everything = listing(history.harness, history.record)
    numbers = {run["id"]: run["number"] for run in everything["runs"]}

    assert sorted(numbers.values()) == list(range(1, 9))
    assert numbers[history.runs["done"]] == 1 and numbers[history.runs["stopping"]] == 8
    assert listing(history.harness, other)["runs"][0]["number"] == 1
    assert listing(history.harness, other)["runs"][0]["id"] == other_run
    filtered = listing(history.harness, history.record, status="failed")
    paged = listing(history.harness, history.record, limit=2, offset=2)
    assert {run["id"]: run["number"] for run in filtered["runs"] + paged["runs"]}.items() <= numbers.items()


def test_a_new_brew_takes_the_next_number(history):
    harness = history.harness
    response = brew(harness, history.record, key="fresh-key-1")

    assert response.status_code == 201
    created = {run["id"]: run["number"] for run in listing(harness, history.record)["runs"]}
    assert created[response.json()["id"]] == 9


def test_rows_carry_the_documented_fields(history):
    run = listing(history.harness, history.record, limit=1)["runs"][0]

    assert set(run) == {
        "id", "elixir_id", "number", "elixir_revision", "scope", "scope_node_id", "scope_edge_id", "base_run_id",
        "run_mode", "status", "error", "created_at", "updated_at", "finished_at",
        "scope_label", "steps_done", "steps_total", "outputs",
    }


def test_scope_label_names_the_step_of_a_node_run(history):
    runs = {run["id"]: run for run in listing(history.harness, history.record)["runs"]}

    assert runs[history.runs["running"]]["scope_label"] == "Sharpen faces"
    assert runs[history.runs["needs"]]["scope_label"] == "Untitled preset node"
    assert runs[history.runs["done"]]["scope_label"] is None


def test_steps_and_outputs_come_from_the_brew(harness):
    record = elixir(assets=("p1",))
    run = brew(harness, record).json()
    harness.pump()

    early = listing(harness, record)["runs"][0]
    harness.port.finish("g1", ("f1", "IMAGE"))
    harness.pump()
    late = listing(harness, record)["runs"][0]

    assert (early["steps_done"], early["steps_total"], early["outputs"]) == (0, 1, 0)
    assert (late["steps_done"], late["steps_total"], late["outputs"]) == (1, 1, 1)
    assert late["id"] == run["id"] and late["status"] == "completed"


def test_steps_count_only_the_nodes_a_run_tracks(history):
    states = {"sharpen": {"status": "done"}, "blank": {"status": "waiting"}}
    run = seed(history.record, "2026-11-07T10:00:00+00:00", states=states)

    row = listing(history.harness, history.record, limit=1)["runs"][0]

    assert row["id"] == run
    assert (row["steps_done"], row["steps_total"]) == (1, 2)


def test_another_users_history_is_not_found(history):
    history.harness.user = history.harness.port.make_user("bob")

    response = history.harness.client.get(f"/api/elixirs/{history.record.id}/runs")
    cancel = history.harness.client.post(f"/api/elixirs/{history.record.id}/runs/cancel", json={"all_active": True})

    assert response.status_code == 404
    assert cancel.status_code == 404


def cancel(harness, record, **body):
    response = harness.client.post(f"/api/elixirs/{record.id}/runs/cancel", json=body)
    assert response.status_code == 200, response.text
    return response.json()["cancelled"]


def status_of(harness, run_id):
    return harness.client.get(f"/api/elixirs/runs/{run_id}").json()["status"]


def test_cancelling_named_brews_skips_the_ones_that_cannot_be_stopped(history):
    runs = history.runs

    cancelled = cancel(history.harness, history.record, run_ids=[runs["running"], runs["done"], runs["stopping"], "missing"])

    assert cancelled == [runs["running"]]
    assert status_of(history.harness, runs["running"]) == "cancelled"
    assert status_of(history.harness, runs["done"]) == "completed"
    assert status_of(history.harness, runs["needs"]) == "needs_attention"


def test_all_active_is_resolved_on_the_server(history):
    runs = history.runs

    before = listing(history.harness, history.record, status="cancelled")["total"]

    cancelled = cancel(history.harness, history.record, all_active=True)

    after = listing(history.harness, history.record, status="cancelled")
    assert set(cancelled) == {runs["running"], runs["needs"]}
    assert after["total"] == before + len(cancelled) == 3
    assert set(ids(after)) == set(cancelled) | {runs["cancelled"]}
    assert status_of(history.harness, runs["failed"]) == "failed"


def test_cancelling_twice_is_idempotent(history):
    first = cancel(history.harness, history.record, all_active=True)
    second = cancel(history.harness, history.record, all_active=True)
    named = cancel(history.harness, history.record, run_ids=first)

    assert first
    assert second == [] and named == []


def test_cancel_only_reaches_the_owners_runs(history):
    other = elixir()
    foreign = seed(other, "2026-11-05T10:00:00+00:00", "running")
    bobs = seed(history.record, "2026-11-05T11:00:00+00:00", "running", user="bob")

    cancelled = cancel(history.harness, history.record, run_ids=[foreign, bobs])
    everything = cancel(history.harness, history.record, all_active=True)

    assert cancelled == []
    assert foreign not in everything and bobs not in everything
    assert status_of(history.harness, foreign) == "running"


def test_cancel_needs_something_to_stop(history):
    empty = history.harness.client.post(f"/api/elixirs/{history.record.id}/runs/cancel", json={})
    none = history.harness.client.post(f"/api/elixirs/{history.record.id}/runs/cancel", json={"run_ids": []})

    assert empty.status_code == 422
    assert none.status_code == 200 and none.json() == {"cancelled": []}


def test_the_picker_summary_counts_active_brews(history):
    records = [history.record, elixir()]

    result = summaries(ElixirRepository(), records, "alice")

    assert result[records[0].id]["active_runs"] == 3
    assert result[records[1].id]["active_runs"] == 0
    listed = {item["id"]: item for item in history.harness.client.get("/api/elixirs").json()["elixirs"]}
    assert listed[history.record.id]["active_runs"] == 3


def hold(harness, assets=("p1",), name="Product shots"):
    record = ElixirRepository().create("alice", name, "automatic", definition(assets=assets))
    run = brew(harness, record, key=f"hold-{name}-key").json()
    harness.pump()
    return record, run


def test_a_held_generation_names_the_elixir_and_brew(harness):
    record, run = hold(harness)
    hooks = ElixirRuntime(harness.coordinator, follows=None)
    held = SimpleNamespace(data={"generation_id": "g1"})
    free = SimpleNamespace(data={"generation_id": "other"})

    hooks.on_before_delete(held)
    hooks.on_before_delete(free)

    assert held.data["blocked"] is True
    assert held.data["block_reason"] == "Brew 1 of Product shots still needs files from this generation. Stop or finish that brew first."
    assert held.data["block_details"] == {
        "blocking_runs": [{"elixir_id": record.id, "elixir_name": "Product shots", "run_id": run["id"], "number": 1}]
    }
    assert "blocked" not in free.data and "block_details" not in free.data


def test_a_bulk_delete_names_the_count_and_first_holder(harness):
    record, run = hold(harness)
    hooks = ElixirRuntime(harness.coordinator, follows=None)
    bulk = SimpleNamespace(data={"generation_ids": ["g1", "nope"]})

    hooks.on_before_bulk_delete(bulk)

    assert bulk.data["block_reason"] == (
        "1 of the selected generations is still needed by running brews, starting with Brew 1 of Product shots. "
        "Stop or finish those brews first."
    )
    assert bulk.data["block_details"]["blocking_runs"] == [
        {"elixir_id": record.id, "elixir_name": "Product shots", "run_id": run["id"], "number": 1}
    ]


def test_the_delete_block_clears_once_the_brew_is_stopped(harness):
    record, run = hold(harness)
    hooks = ElixirRuntime(harness.coordinator, follows=None)
    harness.client.post(f"/api/elixirs/{record.id}/runs/cancel", json={"run_ids": [run["id"]]})
    harness.pump()
    context = SimpleNamespace(data={"generation_id": "g1"})

    hooks.on_before_delete(context)

    assert "blocked" not in context.data
