import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src.features.elixirs.coordinator import Coordinator
from src.features.elixirs.follow import SessionFollows, settings_hash
from src.features.elixirs.limits import Limits
from src.features.elixirs.repository import ElixirRepository
from src.features.elixirs.run_repository import RunStore
from src.features.elixirs.session_reader import SessionReader
from tests.features.elixirs.conftest import make_client, make_components, make_user

KREA = "4TK1KBQZ2XMB8ME0PTMXS1YJQP"
H3 = "01KX47H3MINIMAX000000000VA"

SHOOT = {"selectedVariant": "custom", "prompt": "a bottle", "formData": {"quantity": 1}}


class FakeSessions:
    def __init__(self):
        self.rows = {}

    def get_by_id(self, session_id):
        return self.rows.get(session_id)

    def save(self, session_id="s1", owner="alice", preset_id=KREA, data=None, name="Bottle shoot"):
        current = self.rows.get(session_id)
        self.rows[session_id] = SimpleNamespace(
            id=session_id,
            user_id=owner,
            preset_id=preset_id,
            name=name,
            data=data if data is not None else {"txt2img": {**SHOOT, "leftPanelWidth": 300}},
            version=(current.version + 1) if current else 1,
            created_at=datetime(2026, 11, 1, tzinfo=timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        return self.rows[session_id]


@pytest.fixture
def sessions():
    return FakeSessions()


@pytest.fixture
def follows(sessions):
    reader = SessionReader(sessions, sessions)
    return SessionFollows(ElixirRepository(), reader.get_session, make_user)


@pytest.fixture
def harness(scratch_db, port, catalog, contracts, sessions):
    coordinator = Coordinator(store=RunStore(), port=port, limits_provider=Limits, boot_id="boot-1", random_below=lambda limit: 3)
    state = SimpleNamespace(user=port.users["alice"], port=port, coordinator=coordinator)
    components = make_components(port, catalog=catalog, contracts=contracts, coordinator=coordinator, sessions=sessions)
    state.client = make_client(components, lambda: state.user)
    state.pump = lambda: asyncio.run(coordinator.pump())
    for generation_id, file_id in (("g1", "f1"), ("g3", "f3")):
        port.media[f"history:{generation_id}:{file_id}"] = {
            "value": f"generations/{generation_id}/{file_id}.png", "media_type": "image", "original_filename": f"{file_id}.png", "origin": None,
        }
    return state


def shoot_node(**fields):
    return {"id": "shoot", "kind": "preset", "title": "Shoot", "preset_id": KREA, "mode": "txt2img", **fields}


def graph(**shoot_fields):
    return {
        "nodes": [
            shoot_node(**shoot_fields),
            {"id": "sharpen", "kind": "preset", "title": "Sharpen", "preset_id": KREA, "mode": "enhance", "overrides": {"prompt": "crisp"}},
        ],
        "edges": [{"id": "e1", "source": "shoot", "source_handle": "images", "target": "sharpen", "target_handle": "source_image"}],
    }


def create(harness, definition, run_mode="automatic"):
    response = harness.client.post("/api/elixirs", json={"name": "Campaign", "run_mode": run_mode, "definition": definition})
    assert response.status_code == 201, response.text
    return response.json()


def base(created):
    return f"/api/elixirs/{created['id']}"


def follow(harness, created, revision=None, session_id="s1", node="shoot", **body):
    return harness.client.post(
        f"{base(created)}/nodes/{node}/session/follow",
        json={"expected_revision": revision or created["revision"], "session_id": session_id, **body},
    )


def node_of(elixir, node_id="shoot"):
    return next(node for node in elixir["definition"]["nodes"] if node["id"] == node_id)


def stored(created):
    return ElixirRepository().get(created["id"], "alice")


def issues_of(harness, created):
    return {(issue["code"], issue.get("node_id")): issue for issue in harness.client.post(f"{base(created)}/validate").json()["issues"]}


def followed_elixir(harness, sessions):
    sessions.save()
    created = create(harness, graph())
    response = follow(harness, created)
    assert response.status_code == 200, response.text
    return response.json()


def test_nodes_without_a_mode_take_it_from_what_they_hold(harness):
    legacy = {
        "nodes": [shoot_node()],
        "edges": [],
    }
    created = create(harness, legacy)
    record = ElixirRepository().create(
        "alice", "Old", "review",
        {"nodes": [{**shoot_node(), "overrides": {}, "session_source": {"id": "s1"}, "session_snapshot": dict(SHOOT)}], "edges": []},
    )
    moved = harness.client.put(
        f"/api/elixirs/{record.id}",
        json={"expected_revision": 1, "definition": {"nodes": [{**shoot_node(), "position": {"x": 5, "y": 5}}], "edges": []}},
    ).json()

    assert node_of(created)["session_mode"] == "new"
    assert node_of(moved)["session_mode"] == "copy"
    assert node_of(moved)["session_snapshot"] == SHOOT


def test_a_client_cannot_make_a_node_follow_a_session_by_saving_it(harness, sessions):
    sessions.save()
    forged = shoot_node(session_mode="follow", session_ref={"id": "s1"}, session_hash="x", session_snapshot={"prompt": "forged"})

    created = create(harness, {"nodes": [forged], "edges": []})

    node = node_of(created)
    assert node["session_mode"] == "new"
    assert (node["session_ref"], node["session_hash"], node["session_snapshot"]) == (None, None, None)


def test_following_a_session_stores_a_reference_a_fallback_and_its_hash(harness, sessions):
    sessions.save()
    created = create(harness, graph(overrides={"prompt": "local"}))

    response = follow(harness, created)

    assert response.status_code == 200, response.text
    body = response.json()
    node = node_of(body)
    assert body["node"] == node and body["revision"] == created["revision"] + 1
    assert node["session_mode"] == "follow"
    assert node["session_ref"]["id"] == "s1" and node["session_ref"]["name"] == "Bottle shoot"
    assert (node["session_ref"]["preset_id"], node["session_ref"]["mode"], node["session_ref"]["version"]) == (KREA, "txt2img", 1)
    assert node["session_snapshot"] == SHOOT
    assert node["session_hash"] == settings_hash(SHOOT)
    assert node["overrides"] == {} and node["session_source"] is None
    assert node["form_name"] == "custom"


def test_a_followed_node_keeps_no_local_edits_and_keeps_its_session_on_save(harness, sessions):
    followed = followed_elixir(harness, sessions)
    edited = {**node_of(followed), "overrides": {"prompt": "sneaky"}, "session_mode": "new", "session_ref": None, "position": {"x": 9, "y": 9}}
    nodes = [edited, node_of(followed, "sharpen")]

    saved = harness.client.put(base(followed), json={"expected_revision": followed["revision"], "definition": {**followed["definition"], "nodes": nodes}}).json()

    node = node_of(saved)
    assert node["session_mode"] == "follow" and node["session_ref"]["id"] == "s1"
    assert node["overrides"] == {}
    assert node["position"] == {"x": 9.0, "y": 9.0}


def test_changing_a_followed_nodes_preset_ends_the_follow(harness, sessions):
    followed = followed_elixir(harness, sessions)
    switched = {**node_of(followed), "mode": "enhance"}

    saved = harness.client.put(
        base(followed),
        json={"expected_revision": followed["revision"], "definition": {"nodes": [switched], "edges": []}},
    ).json()

    node = node_of(saved)
    assert node["session_mode"] == "new" and node["session_ref"] is None and node["session_snapshot"] is None


def test_following_needs_an_owned_session_with_the_mode(harness, sessions):
    sessions.save("theirs", owner="bob")
    sessions.save("other", data={"enhance": {"prompt": "x"}, "txt2img": {"prompt": "y"}})
    sessions.save("video", preset_id=H3, data={"video": {"prompt": "go"}})
    created = create(harness, {"nodes": [{"id": "blank", "kind": "preset"}], "edges": []})

    foreign = follow(harness, created, session_id="theirs", node="blank")
    unknown = follow(harness, created, session_id="nope", node="blank")
    ambiguous = follow(harness, created, session_id="other", node="blank")
    missing = follow(harness, created, session_id="other", node="blank", mode="upscale")
    stale = follow(harness, created, revision=created["revision"] + 5, session_id="video", node="blank")

    assert foreign.status_code == 404 and foreign.json()["detail"]["code"] == "session_not_found"
    assert unknown.status_code == 404
    assert ambiguous.json()["detail"]["code"] == "mode_required"
    assert missing.json()["detail"]["code"] == "session_mode_missing"
    assert stale.status_code == 409 and stale.json()["detail"]["code"] == "revision_conflict"


def test_making_a_copy_snapshots_the_sessions_current_settings(harness, sessions):
    followed = followed_elixir(harness, sessions)
    sessions.save(data={"txt2img": {**SHOOT, "prompt": "a blue bottle"}})

    response = harness.client.post(f"{base(followed)}/nodes/shoot/session/copy", json={"expected_revision": followed["revision"]})

    assert response.status_code == 200, response.text
    node = response.json()["node"]
    assert node["session_mode"] == "copy"
    assert node["session_snapshot"]["prompt"] == "a blue bottle"
    assert node["session_source"]["id"] == "s1" and node["session_source"]["mode"] == "txt2img"
    assert node["session_ref"] is None and node["session_hash"] is None


def test_making_a_copy_of_a_deleted_session_keeps_the_last_settings_seen(harness, sessions):
    followed = followed_elixir(harness, sessions)
    del sessions.rows["s1"]

    node = harness.client.post(f"{base(followed)}/nodes/shoot/session/copy", json={"expected_revision": followed["revision"]}).json()["node"]

    assert node["session_mode"] == "copy" and node["session_snapshot"] == SHOOT


def test_only_a_followed_node_can_be_copied(harness):
    created = create(harness, graph())

    response = harness.client.post(f"{base(created)}/nodes/shoot/session/copy", json={"expected_revision": created["revision"]})

    assert response.status_code == 409 and response.json()["detail"]["code"] == "not_following"


def test_switching_to_own_settings_keeps_what_the_node_used(harness, sessions):
    followed = followed_elixir(harness, sessions)
    copied = harness.client.post(f"{base(followed)}/nodes/shoot/session/copy", json={"expected_revision": followed["revision"]}).json()
    edited = {**node_of(copied), "overrides": {"formData": {"quantity": 3}}}
    saved = harness.client.put(
        base(copied), json={"expected_revision": copied["revision"], "definition": {**copied["definition"], "nodes": [edited, node_of(copied, "sharpen")]}}
    ).json()

    response = harness.client.post(f"{base(saved)}/nodes/shoot/session/new", json={"expected_revision": saved["revision"]})

    assert response.status_code == 200, response.text
    node = response.json()["node"]
    assert node["session_mode"] == "new"
    assert node["overrides"] == {"prompt": "a bottle", "formData": {"quantity": 3}}
    assert (node["session_source"], node["session_snapshot"], node["session_ref"]) == (None, None, None)
    assert "no_session" not in {code for code, _ in issues_of(harness, response.json())}


def test_the_followed_sessions_current_settings_and_hash_can_be_read(harness, sessions):
    followed = followed_elixir(harness, sessions)
    sessions.save(data={"txt2img": {**SHOOT, "prompt": "now", "layoutMode": "split"}})

    states = harness.client.get(f"{base(followed)}/followed-sessions").json()["nodes"]

    assert states["shoot"]["status"] == "ok"
    assert states["shoot"]["settings"] == {**SHOOT, "prompt": "now"}
    assert states["shoot"]["session_hash"] == settings_hash({**SHOOT, "prompt": "now"})
    assert states["shoot"]["session"]["version"] == 2
    assert "sharpen" not in states


def test_a_followed_session_that_is_gone_or_lacks_the_mode_is_a_problem(harness, sessions, follows):
    followed = followed_elixir(harness, sessions)
    sessions.save(data={"enhance": {"prompt": "x"}})
    lacking = issues_of(harness, followed)
    sessions.rows["s1"].user_id = "bob"
    unavailable = issues_of(harness, followed)
    del sessions.rows["s1"]
    follows.session_deleted("alice", "s1")
    deleted = issues_of(harness, followed)

    assert lacking[("followed_session_mode_missing", "shoot")]["severity"] == "error"
    assert ("followed_session_unavailable", "shoot") in unavailable
    message = deleted[("followed_session_deleted", "shoot")]["message"]
    assert "was deleted" in message and "keep a copy" in message
    assert node_of(stored(followed).to_full())["session_snapshot"] == SHOOT


def test_a_brew_refuses_settings_built_from_an_outdated_session(harness, sessions):
    followed = followed_elixir(harness, sessions)
    old_hash = node_of(followed)["session_hash"]
    sessions.save(data={"txt2img": {**SHOOT, "prompt": "changed"}})

    response = harness.client.post(f"{base(followed)}/runs", json={
        "expected_revision": followed["revision"],
        "idempotency_key": "brew-key-1",
        "templates": templates(),
        "session_hashes": {"shoot": old_hash},
    })
    missing = harness.client.post(f"{base(followed)}/runs", json={
        "expected_revision": followed["revision"],
        "idempotency_key": "brew-key-2",
        "templates": templates(),
    })

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "session_changed" and detail["node_id"] == "shoot"
    assert detail["message"] == "A session this elixir follows changed. Reload and brew again."
    assert missing.status_code == 409


def templates():
    return {
        "shoot": {"preset_id": KREA, "mode": "txt2img", "prompts": [{"positive": "a bottle", "negative": ""}], "form_data": {"seed": 1, "quantity": 1}},
        "sharpen": {"preset_id": KREA, "mode": "enhance", "prompts": [{"positive": "crisp", "negative": ""}], "form_data": {"seed": 2, "quantity": 1}},
    }


def brew_followed(harness, sessions):
    followed = followed_elixir(harness, sessions)
    current = harness.client.get(f"{base(followed)}/followed-sessions").json()["nodes"]["shoot"]["session_hash"]
    response = harness.client.post(f"{base(followed)}/runs", json={
        "expected_revision": followed["revision"],
        "idempotency_key": "brew-key-1",
        "templates": templates(),
        "session_hashes": {"shoot": current},
    })
    assert response.status_code == 201, response.text
    harness.pump()
    harness.port.finish("g1", ("f1", "IMAGE"))
    harness.pump()
    harness.port.finish("g2", ("f2", "IMAGE"))
    harness.pump()
    return followed, response.json(), current


def test_a_brew_records_which_session_settings_each_followed_node_ran_with(harness, sessions):
    followed, run, current = brew_followed(harness, sessions)

    graph_sessions = harness.coordinator.store.get_run(run["id"])["graph"]["sessions"]
    results = stored(followed).results

    assert graph_sessions == {"shoot": {"session_id": "s1", "version": 1, "session_hash": current}}
    assert results["shoot"]["session"]["session_hash"] == current
    assert results["shoot"]["stale"] is False and results["shoot"]["stale_reason"] is None
    assert results["sharpen"]["stale"] is False and "session" not in results["sharpen"]


def test_a_layout_only_session_save_leaves_results_fresh(harness, sessions, follows):
    followed, _, _ = brew_followed(harness, sessions)
    sessions.save(data={"txt2img": {**SHOOT, "leftPanelWidth": 999, "layoutMode": "stack"}})

    follows.session_written("alice", "s1", 2)

    record = stored(followed)
    assert not any(entry["stale"] for entry in record.results.values())
    assert node_of(record.to_full())["session_ref"]["version"] == 2


def test_a_session_settings_change_marks_the_follower_and_what_follows_stale(harness, sessions, follows):
    followed, _, _ = brew_followed(harness, sessions)
    before = stored(followed)
    sessions.save(data={"txt2img": {**SHOOT, "prompt": "a green bottle"}})

    changed = follows.session_written("alice", "s1", 2)

    record = stored(followed)
    assert changed == [followed["id"]]
    assert (record.results["shoot"]["stale"], record.results["shoot"]["stale_reason"]) == (True, "session_changed")
    assert (record.results["sharpen"]["stale"], record.results["sharpen"]["stale_reason"]) == (True, "session_changed")
    assert record.results["sharpen"]["stale_node_id"] == "shoot"
    assert record.revision == before.revision and record.result_revision == before.result_revision + 1
    node = node_of(record.to_full())
    assert node["session_snapshot"]["prompt"] == "a green bottle"
    assert node["session_hash"] == settings_hash({**SHOOT, "prompt": "a green bottle"})


def test_a_write_the_follower_already_saw_is_not_read_again(harness, sessions, follows):
    followed_elixir(harness, sessions)
    reads = []
    follows.reader = lambda user, session_id: reads.append(session_id)

    assert follows.session_written("alice", "s1", 1) == []
    assert follows.session_written("bob", "s1", 2) == []
    assert reads == []


def test_a_result_recorded_after_the_session_moved_on_is_born_stale(harness, sessions, follows):
    followed = followed_elixir(harness, sessions)
    current = node_of(followed)["session_hash"]
    harness.client.post(f"{base(followed)}/runs", json={
        "expected_revision": followed["revision"],
        "idempotency_key": "brew-key-1",
        "templates": templates(),
        "session_hashes": {"shoot": current},
    })
    harness.pump()
    sessions.save(data={"txt2img": {**SHOOT, "prompt": "mid-brew"}})
    follows.session_written("alice", "s1", 2)
    harness.port.finish("g1", ("f1", "IMAGE"))
    harness.pump()

    shoot = stored(followed).results["shoot"]
    assert (shoot["stale"], shoot["stale_reason"]) == (True, "session_changed")


def test_rerunning_a_node_marks_what_follows_stale_from_upstream(harness, sessions):
    followed, _, current = brew_followed(harness, sessions)

    response = harness.client.post(f"{base(followed)}/nodes/shoot/runs", json={
        "expected_revision": followed["revision"],
        "idempotency_key": "node-key-1",
        "template": templates()["shoot"],
        "session_hashes": {"shoot": current},
    })
    assert response.status_code == 201, response.text
    harness.pump()
    harness.port.finish("g3", ("f3", "IMAGE"))
    harness.pump()

    results = stored(followed).results
    assert (results["sharpen"]["stale"], results["sharpen"]["stale_reason"]) == (True, "upstream")
    assert results["shoot"]["session"]["session_hash"] == current


def test_session_hooks_never_break_the_session_write():
    from src.features.elixirs.runtime import ElixirRuntime

    class Broken:
        def session_written(self, *args):
            raise RuntimeError("boom")

        def session_deleted(self, *args):
            raise RuntimeError("boom")

    hooks = ElixirRuntime(coordinator=None, follows=Broken())
    context = SimpleNamespace(data={"session_id": "s1", "user_id": "alice", "version": 2})

    assert hooks.on_session_updated(context) is context
    assert hooks.on_session_deleted(context) is context
