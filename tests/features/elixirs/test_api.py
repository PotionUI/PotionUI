from types import SimpleNamespace

import pytest

from src.features.elixirs.errors import MediaRefError
from src.features.elixirs.identity import asset_key
from src.features.elixirs.limits import Limits
from src.features.elixirs.records import RUNS_TABLE
from src.platform.security.user import AccountType, User
from tests.features.elixirs.conftest import make_client, make_components

KREA = "4TK1KBQZ2XMB8ME0PTMXS1YJQP"
H3 = "01KX47H3MINIMAX000000000VA"


def make_user(user_id):
    return User(
        id=user_id,
        username=user_id,
        email=f"{user_id}@example.com",
        password_hash="h",
        account_type=AccountType.USER,
    )


class FakeMedia:
    def __init__(self):
        self.owned = {}
        self.calls = []

    def add(self, user_id, ref, media_type="IMAGE", name="photo.png"):
        self.owned[(user_id, asset_key(ref))] = {
            "value": "uploads/x",
            "media_type": media_type,
            "original_filename": name,
            "origin": None,
        }

    def __call__(self, user, ref):
        self.calls.append(asset_key(ref))
        found = self.owned.get((user.id, asset_key(ref)))
        if found is None:
            raise MediaRefError("not_found", "The referenced media is not available")
        return found


class FakeSessions:
    def __init__(self):
        self.rows = {}
        self.versions = {}

    def get_by_id(self, session_id):
        return self.rows.get(session_id)

    def get(self, session_id, version):
        return self.versions.get((session_id, version))


@pytest.fixture
def media_store():
    return FakeMedia()


@pytest.fixture
def sessions():
    return FakeSessions()


@pytest.fixture
def harness(scratch_db, port, media_store, sessions, catalog, contracts):
    state = SimpleNamespace(user=make_user("alice"), limits=Limits(), db=scratch_db)
    components = make_components(
        port, catalog=catalog, contracts=contracts, resolver=media_store, limits=lambda: state.limits, sessions=sessions,
    )
    state.client = make_client(components, lambda: state.user)
    state.as_user = lambda user_id: setattr(state, "user", make_user(user_id))
    return state


def library(item_id):
    return {"kind": "library", "item_id": item_id}


def product_definition(assets):
    return {
        "nodes": [
            {"id": "product", "kind": "media", "title": "Product images", "media_type": "image", "assets": assets},
            {"id": "orbit", "kind": "preset", "title": "Orbit left", "preset_id": H3, "mode": "video"},
        ],
        "edges": [
            {
                "id": "to-orbit",
                "source": "product",
                "source_handle": "images",
                "target": "orbit",
                "target_handle": "first_frame",
            }
        ],
    }


def create(harness, name="Campaign", definition=None):
    response = harness.client.post("/api/elixirs", json={"name": name, "definition": definition or {}})
    assert response.status_code == 201, response.text
    return response.json()


def save_session(sessions, session_id="s1", owner="alice", data=None, preset_id=H3):
    sessions.rows[session_id] = SimpleNamespace(
        id=session_id,
        user_id=owner,
        preset_id=preset_id,
        name="Orbit",
        data=data if data is not None else {"video": {"prompt": "orbit left", "formData": {"quantity": 1}}},
    )


def test_create_list_get_update_and_delete(harness):
    created = create(harness)

    listed = harness.client.get("/api/elixirs").json()["elixirs"]
    fetched = harness.client.get(f"/api/elixirs/{created['id']}").json()
    updated = harness.client.put(
        f"/api/elixirs/{created['id']}",
        json={"expected_revision": 1, "name": "Renamed", "run_mode": "automatic"},
    ).json()
    deleted = harness.client.delete(f"/api/elixirs/{created['id']}")

    assert [item["id"] for item in listed] == [created["id"]]
    assert fetched["run_mode"] == "review" and fetched["revision"] == 1
    assert (updated["name"], updated["run_mode"], updated["revision"]) == ("Renamed", "automatic", 2)
    assert deleted.status_code == 200 and deleted.json() == {"deleted": True, "id": created["id"]}
    assert harness.client.get(f"/api/elixirs/{created['id']}").status_code == 404


def test_another_users_elixir_is_not_found(harness, sessions):
    created = create(harness)
    save_session(sessions, owner="bob")
    base = f"/api/elixirs/{created['id']}"

    harness.as_user("bob")

    assert harness.client.get("/api/elixirs").json()["elixirs"] == []
    responses = [
        harness.client.get(base),
        harness.client.put(base, json={"expected_revision": 1, "name": "Mine now"}),
        harness.client.post(f"{base}/validate"),
        harness.client.post(f"{base}/nodes/orbit/session", json={"expected_revision": 1, "session_id": "s1"}),
        harness.client.delete(base),
    ]
    assert [response.status_code for response in responses] == [404] * 5
    assert {response.json()["detail"]["code"] for response in responses} == {"elixir_not_found"}

    harness.as_user("alice")
    assert harness.client.get(base).json()["name"] == "Campaign"


def test_a_stale_revision_is_refused(harness):
    created = create(harness)
    base = f"/api/elixirs/{created['id']}"
    harness.client.put(base, json={"expected_revision": 1, "name": "One"})

    response = harness.client.put(base, json={"expected_revision": 1, "name": "Two"})

    assert response.status_code == 409
    assert response.json()["detail"]["current_revision"] == 2
    assert harness.client.get(base).json()["name"] == "One"


def test_media_files_are_checked_when_they_are_added(harness, media_store):
    media_store.add("alice", library("mine"))
    media_store.add("bob", library("theirs"))

    saved = create(harness, definition=product_definition([library("mine")]))
    foreign = harness.client.post(
        "/api/elixirs",
        json={"name": "x", "definition": product_definition([library("mine"), library("theirs")])},
    )
    repeated = harness.client.post(
        "/api/elixirs",
        json={"name": "x", "definition": product_definition([library("mine"), library("mine")])},
    )

    asset = saved["definition"]["nodes"][0]["assets"][0]
    assert asset == {
        "kind": "library",
        "item_id": "mine",
        "media_type": "image",
        "original_filename": "photo.png",
        "preview_url": "/api/media/uploads/x?size=medium",
    }
    assert foreign.status_code == 422
    assert foreign.json()["detail"]["node_id"] == "product"
    assert foreign.json()["detail"]["message"].startswith("Product images")
    assert repeated.status_code == 422 and repeated.json()["detail"]["code"] == "duplicate_media"


def test_mixed_media_types_are_refused(harness, media_store):
    media_store.add("alice", library("photo"))
    media_store.add("alice", library("clip"), media_type="VIDEO")
    definition = product_definition([library("photo"), library("clip")])

    response = harness.client.post("/api/elixirs", json={"name": "x", "definition": definition})

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "mixed_media"


def test_paths_and_urls_are_never_accepted_as_media(harness):
    definition = product_definition([{"kind": "library", "item_id": "x", "path": "/etc/passwd"}])

    response = harness.client.post("/api/elixirs", json={"name": "x", "definition": definition})

    assert response.status_code == 422


def test_saved_files_are_not_rechecked_and_the_revision_tracks_changes(harness, media_store):
    media_store.add("alice", library("a"))
    media_store.add("alice", library("b"))
    created = create(harness, definition=product_definition([library("a")]))
    base = f"/api/elixirs/{created['id']}"
    media_store.owned.pop(("alice", "library:a"))
    media_store.calls.clear()

    unchanged = harness.client.put(base, json={"expected_revision": 1, "definition": product_definition([library("a")])}).json()
    grown = harness.client.put(base, json={"expected_revision": 2, "definition": product_definition([library("a"), library("b")])}).json()

    assert media_store.calls == ["library:b"]
    assert created["definition"]["nodes"][0]["source_revision"] == 1
    assert unchanged["definition"]["nodes"][0]["source_revision"] == 1
    assert grown["definition"]["nodes"][0]["source_revision"] == 2


def test_validate_reports_problems_by_node_and_files_that_disappeared(harness, media_store):
    media_store.add("alice", library("a"))
    created = create(harness, definition=product_definition([library("a")]))
    media_store.owned.clear()

    report = harness.client.post(f"/api/elixirs/{created['id']}/validate").json()

    assert report["revision"] == 1
    assert report["valid"] is False
    found = {(issue["code"], issue.get("node_id")) for issue in report["issues"]}
    assert ("unavailable_media", "product") in found
    assert ("no_session", "orbit") in found


def test_a_complete_elixir_validates(harness, media_store, sessions):
    media_store.add("alice", library("a"))
    save_session(sessions)
    created = create(harness, definition=product_definition([library("a")]))
    base = f"/api/elixirs/{created['id']}"
    harness.client.post(f"{base}/nodes/orbit/session", json={"expected_revision": 1, "session_id": "s1"})

    report = harness.client.post(f"{base}/validate").json()

    assert report["valid"], report["issues"]
    assert report["estimates"]["total_invocations"] == 1


def test_validate_uses_the_configured_limits(harness, media_store):
    media_store.add("alice", library("a"))
    created = create(harness, definition=product_definition([library("a")]))
    harness.limits = Limits(max_nodes=1)

    report = harness.client.post(f"/api/elixirs/{created['id']}/validate").json()

    assert "too_many_nodes" in {issue["code"] for issue in report["issues"]}


def test_a_session_copy_stays_the_same_after_the_session_changes(harness, media_store, sessions):
    save_session(sessions)
    created = create(harness, definition={"nodes": [{"id": "orbit", "kind": "preset", "title": "Orbit left"}]})
    base = f"/api/elixirs/{created['id']}"

    imported = harness.client.post(f"{base}/nodes/orbit/session", json={"expected_revision": 1, "session_id": "s1"})
    sessions.rows["s1"].data["video"]["prompt"] = "changed later"
    sessions.rows["s1"].data["video"]["formData"]["quantity"] = 5
    del sessions.rows["s1"]
    later = harness.client.get(base).json()

    assert imported.status_code == 200, imported.text
    node = later["definition"]["nodes"][0]
    assert (node["preset_id"], node["mode"]) == (H3, "video")
    assert node["session_snapshot"] == {"prompt": "orbit left", "formData": {"quantity": 1}}
    assert node["session_source"]["id"] == "s1"
    assert later["revision"] == 2


def test_a_saved_version_is_copied_instead_of_the_current_session(harness, sessions):
    save_session(sessions)
    sessions.versions[("s1", 3)] = SimpleNamespace(data={"video": {"prompt": "version three"}})
    created = create(harness, definition={"nodes": [{"id": "orbit", "kind": "preset"}]})

    response = harness.client.post(
        f"/api/elixirs/{created['id']}/nodes/orbit/session",
        json={"expected_revision": 1, "session_id": "s1", "version": 3},
    )

    node = response.json()["definition"]["nodes"][0]
    assert node["session_snapshot"] == {"prompt": "version three"}
    assert node["session_source"]["version"] == 3


def test_someone_elses_session_cannot_be_imported(harness, sessions):
    save_session(sessions, owner="bob")
    created = create(harness, definition={"nodes": [{"id": "orbit", "kind": "preset"}]})
    base = f"/api/elixirs/{created['id']}"

    foreign = harness.client.post(f"{base}/nodes/orbit/session", json={"expected_revision": 1, "session_id": "s1"})
    missing_version = harness.client.post(
        f"{base}/nodes/orbit/session", json={"expected_revision": 1, "session_id": "s1", "version": 9}
    )

    assert foreign.status_code == 404
    assert foreign.json()["detail"]["code"] == "session_not_found"
    assert missing_version.status_code == 404
    assert harness.client.get(base).json()["revision"] == 1


def test_a_session_import_needs_the_current_revision(harness, sessions):
    save_session(sessions)
    created = create(harness, definition={"nodes": [{"id": "orbit", "kind": "preset"}]})
    base = f"/api/elixirs/{created['id']}"
    harness.client.put(base, json={"expected_revision": 1, "name": "Moved on"})

    response = harness.client.post(f"{base}/nodes/orbit/session", json={"expected_revision": 1, "session_id": "s1"})

    assert response.status_code == 409
    assert harness.client.get(base).json()["definition"]["nodes"][0]["session_snapshot"] is None


def test_a_client_cannot_write_or_replace_a_session_copy(harness, sessions):
    save_session(sessions)
    forged = {"id": "orbit", "kind": "preset", "preset_id": H3, "mode": "video", "session_snapshot": {"prompt": "forged"}}
    created = create(harness, definition={"nodes": [forged]})
    base = f"/api/elixirs/{created['id']}"
    assert created["definition"]["nodes"][0]["session_snapshot"] is None

    harness.client.post(f"{base}/nodes/orbit/session", json={"expected_revision": 1, "session_id": "s1"})
    moved = {**forged, "position": {"x": 40, "y": 80}, "overrides": {"prompt": "local edit"}}
    kept = harness.client.put(base, json={"expected_revision": 2, "definition": {"nodes": [moved]}}).json()
    switched = harness.client.put(
        base, json={"expected_revision": 3, "definition": {"nodes": [{**moved, "preset_id": KREA, "mode": "txt2img"}]}}
    ).json()

    node = kept["definition"]["nodes"][0]
    assert node["session_snapshot"] == {"prompt": "orbit left", "formData": {"quantity": 1}}
    assert node["overrides"] == {"prompt": "local edit"}
    assert node["position"] == {"x": 40.0, "y": 80.0}
    assert switched["definition"]["nodes"][0]["session_snapshot"] is None


def test_unknown_node_settings_are_refused(harness):
    node = {"id": "orbit", "kind": "preset", "overrides": {"pipeline": "rm -rf"}}

    response = harness.client.post("/api/elixirs", json={"name": "x", "definition": {"nodes": [node]}})

    assert response.status_code == 422


def test_an_elixir_that_is_brewing_cannot_be_deleted(harness):
    created = create(harness)
    with harness.db.get_cursor() as cursor:
        cursor.execute(
            f"""
            INSERT INTO {RUNS_TABLE} (id, elixir_id, user_id, elixir_revision, scope, run_mode, status, graph, created_at, updated_at)
            VALUES ('r1', ?, 'alice', 1, 'graph', 'review', 'awaiting_selection', '{{}}', 'now', 'now')
            """,
            (created["id"],),
        )

    response = harness.client.delete(f"/api/elixirs/{created['id']}")

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "elixir_is_brewing"
    assert harness.client.get(f"/api/elixirs/{created['id']}").status_code == 200


def test_history_files_preview_through_their_file_id(harness, media_store):
    ref = {"kind": "history", "generation_id": "g1", "file_id": "f 1"}
    media_store.add("alice", ref)

    created = create(harness, definition=product_definition([ref]))

    assert created["definition"]["nodes"][0]["assets"][0]["preview_url"] == "/api/media/files/f%201?size=medium"


def test_a_client_cannot_choose_a_preview_url(harness, media_store):
    media_store.add("alice", library("mine"))
    forged = {**library("mine"), "preview_url": "https://example.com/x.png"}

    created = create(harness, definition=product_definition([forged]))

    assert created["definition"]["nodes"][0]["assets"][0]["preview_url"] == "/api/media/uploads/x?size=medium"


def test_the_preset_list_offers_only_installed_modes_with_matching_contracts(harness, catalog):
    del catalog[KREA]["modes"]["enhance"]["forms"]["custom"]["source_image"]
    del catalog["01KXB7C553THYMSMKY1QSYESFM"]

    presets = harness.client.get("/api/elixirs/presets").json()["presets"]

    offered = [(item["preset_name"], item["mode"]) for item in presets]
    assert offered == [("Krea-2", "txt2img"), ("MiniMax-H3", "upscale"), ("MiniMax-H3", "video")]
    video = presets[2]
    assert video["mode_label"] == "Video"
    assert video["inputs"]["first_frame"] == {"label": "First frame", "media_type": "image", "target": "video_director_role", "role": "first"}
    assert video["outputs"]["videos"] == {"label": "Final videos", "media_type": "video"}
