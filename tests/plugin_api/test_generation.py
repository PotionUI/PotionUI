import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.features.content_safety.gate import ContentGate
from src.features.content_safety.ledger_repository import ContentLedger
from src.features.content_safety.manager import ContentSafetyManager
from src.features.generation.output_broadcaster import GenerationOutputBroadcaster
from src.features.generation.dto import GenerationRequest
from src.features.generation.exceptions import GenerationNotFoundException
from src.features.generation.file_repository import FileRepository
from src.features.generation.history_query import GenerationHistoryQuery
from src.features.generation.records import File, Generation
from src.features.generation.repository import GenerationRepository
from src.features.media.records import Upload
from src.features.media.upload_repository import UploadRepository
from src.features.sessions.dto import Session
from src.features.sessions.repository import SessionRepository
from src.features.sessions.version_repository import SessionVersionRepository
from src.platform.database.rows import now_utc
from src.platform.plugins import runtime_registries
from src.platform.security.user import AccountType, User
from src.plugin_api import generation as api
from tests.features.content_safety.fakes import FakeResolver, FakeSettings, FakeTagger

REPO_ROOT = Path(__file__).resolve().parents[2]


def _user(user_id, account_type=AccountType.USER):
    return User(
        id=user_id, username=user_id, email=f"{user_id}@example.test", password_hash="$2b$12$x",
        account_type=account_type, created_at=now_utc(), last_login=None,
    )


class World:
    def __init__(self, db, restricted=False):
        for user_id in ("owner", "other", "admin"):
            with db.get_cursor() as cursor:
                cursor.execute(
                    "INSERT INTO users (id, username, email, password_hash, account_type) VALUES (?, ?, ?, 'x', 'USER')",
                    (user_id, user_id, f"{user_id}@example.test"),
                )
        self.owner = _user("owner")
        self.other = _user("other")
        self.admin = _user("admin", AccountType.ADMIN)
        self.generations = GenerationRepository()
        self.files = FileRepository()
        self.uploads = UploadRepository()
        self.sessions = SessionRepository()
        self.versions = SessionVersionRepository()
        settings = FakeSettings()
        self.ledger = ContentLedger(settings)
        manager = ContentSafetyManager(
            settings=settings, resolver=FakeResolver("blocked", restricted),
            ledger=self.ledger, gate=ContentGate(FakeTagger(), settings),
        )
        self.live = {}
        self.existing_paths = set()
        self.existing_uploads = set()
        self.orchestrator = SimpleNamespace(
            status_tracker=SimpleNamespace(get=lambda gid: self.live.get(gid)),
            start_generation=AsyncMock(return_value={
                "generation_id": "g-new", "status": {"status": "pending"}, "queue_position": 2,
            }),
            cancel_generation=AsyncMock(return_value=True),
        )
        self.hub = SimpleNamespace(broadcast_to_generation=AsyncMock())
        self.container = SimpleNamespace(
            generation_orchestrator=self.orchestrator,
            generation_repository=self.generations,
            generation_history_facade=SimpleNamespace(
                query=GenerationHistoryQuery(self.generations, content_safety=manager)
            ),
            output_broadcaster=GenerationOutputBroadcaster(self.hub, self.orchestrator.status_tracker, Mock()),
            file_service=SimpleNamespace(generation_exists=lambda path: path in self.existing_paths),
            media_store=SimpleNamespace(
                upload_repo=self.uploads,
                storage_driver=SimpleNamespace(exists=lambda key: key in self.existing_uploads),
            ),
            session_repository=self.sessions,
            session_version_repository=self.versions,
        )

    def generation(self, generation_id, user_id="owner"):
        return self.generations.create(Generation(
            id=generation_id, preset_id="p", form_data={}, user_id=user_id, status="completed",
        ))

    def file(self, generation_id, file_id, *, user_id="owner", is_final=True, is_derived=False, score=0.01):
        path = f"generations/{generation_id}/{file_id}.png"
        created = self.files.create(File(
            id=file_id, file_path=path, file_type="image", user_id=user_id, mime_type="image/png",
            file_size=1, pipe_name="generator", is_final=is_final, is_derived=is_derived,
        ))
        self.files.associate_with_generation(generation_id, file_id)
        self.existing_paths.add(path)
        if score is not None:
            self.ledger.record_score(path, score)
        return created

    def upload(self, upload_id, filename="u1.png", user_id="owner"):
        self.uploads.create(Upload(
            id=upload_id, user_id=user_id, filename=filename, original_filename="cat.png",
            media_type="image", mime_type="image/png", file_size=1,
        ))
        self.existing_uploads.add(f"uploads/{filename}")


@pytest.fixture
def world(mock_db, monkeypatch):
    built = World(mock_db)
    monkeypatch.setattr(runtime_registries, "_container", built.container)
    return built


@pytest.fixture
def restricted_world(mock_db, monkeypatch):
    built = World(mock_db, restricted=True)
    monkeypatch.setattr(runtime_registries, "_container", built.container)
    return built


def test_the_module_does_not_import_the_inference_stack():
    script = "; ".join([
        "import sys",
        "import src.plugin_api.generation",
        "print('HEAVY:' + ','.join(sorted(m for m in ('torch', 'diffusers', 'transformers') if m in sys.modules)))",
    ])
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(p for p in [str(REPO_ROOT), os.environ.get("PYTHONPATH", "")] if p)
    result = subprocess.run([sys.executable, "-c", script], cwd=str(REPO_ROOT), env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "HEAVY:"


def test_the_public_surface_is_complete():
    for name in api.__all__:
        assert hasattr(api, name), name


@pytest.mark.asyncio
async def test_submit_goes_through_the_orchestrators_ordinary_start(world):
    request = GenerationRequest(preset_id="p", form_data={"a": 1})

    result = await api.submit_generation(world.owner, request)

    assert result == {"generation_id": "g-new", "status": "pending", "queue_position": 2}
    world.orchestrator.start_generation.assert_awaited_once_with(request, "owner")


@pytest.mark.asyncio
async def test_submit_accepts_a_plain_dict(world):
    await api.submit_generation(world.owner, {"preset_id": "p", "form_data": {"a": 1}})

    submitted = world.orchestrator.start_generation.await_args[0][0]
    assert isinstance(submitted, GenerationRequest)
    assert submitted.form_data == {"a": 1}


@pytest.mark.asyncio
async def test_submit_refuses_a_missing_user(world):
    with pytest.raises(PermissionError):
        await api.submit_generation(None, GenerationRequest(preset_id="p"))

    world.orchestrator.start_generation.assert_not_awaited()


@pytest.mark.asyncio
async def test_submit_lets_the_routes_exceptions_through(world):
    world.orchestrator.start_generation.side_effect = ValueError("bad form")

    with pytest.raises(ValueError):
        await api.submit_generation(world.owner, GenerationRequest(preset_id="p"))


@pytest.mark.asyncio
async def test_the_owner_cancels_and_subscribers_hear_about_it(world):
    world.generation("g1")
    world.live["g1"] = SimpleNamespace(user_id="owner", model_dump=lambda: {"id": "g1", "status": "cancelled"})

    assert await api.cancel_generation(world.owner, "g1") is True

    world.orchestrator.cancel_generation.assert_awaited_once_with("g1")
    sent = world.hub.broadcast_to_generation.await_args[0]
    assert sent[0] == "g1"
    assert sent[1]["type"] == "generation_cancelled"


@pytest.mark.asyncio
async def test_another_user_cannot_cancel(world):
    world.generation("g1")
    world.live["g1"] = SimpleNamespace(user_id="owner", model_dump=lambda: {"id": "g1"})

    with pytest.raises(GenerationNotFoundException):
        await api.cancel_generation(world.other, "g1")

    world.orchestrator.cancel_generation.assert_not_awaited()
    world.hub.broadcast_to_generation.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_cancel_the_orchestrator_declines_broadcasts_nothing(world):
    world.generation("g1")
    world.live["g1"] = SimpleNamespace(user_id="owner", model_dump=lambda: {"id": "g1"})
    world.orchestrator.cancel_generation.return_value = False

    assert await api.cancel_generation(world.owner, "g1") is False

    world.hub.broadcast_to_generation.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancelling_a_generation_the_tracker_no_longer_has_broadcasts_nothing(world):
    world.generation("g1")

    assert await api.cancel_generation(world.owner, "g1") is True

    world.hub.broadcast_to_generation.assert_not_awaited()


@pytest.mark.asyncio
async def test_an_admin_may_cancel_anything(world):
    world.generation("g1")

    assert await api.cancel_generation(world.admin, "g1") is True


@pytest.mark.asyncio
async def test_cancelling_an_unknown_generation_is_not_found(world):
    with pytest.raises(GenerationNotFoundException):
        await api.cancel_generation(world.owner, "nope")


def test_state_prefers_the_live_record_and_falls_back_to_the_stored_row(world):
    world.generation("g1")
    assert api.generation_state(world.owner, "g1")["id"] == "g1"

    world.live["g1"] = SimpleNamespace(user_id="owner", model_dump=lambda: {"id": "g1", "status": "running"})
    assert api.generation_state(world.owner, "g1")["status"] == "running"


def test_state_of_someone_elses_generation_is_not_found(world):
    world.generation("g1")
    world.live["g2"] = SimpleNamespace(user_id="owner", model_dump=lambda: {})
    world.generation("g2")

    for generation_id in ("g1", "g2"):
        with pytest.raises(GenerationNotFoundException):
            api.generation_state(world.other, generation_id)
    assert api.generation_state(world.admin, "g1")["id"] == "g1"


def test_files_come_in_canonical_order_with_their_unfiltered_index(world):
    world.generation("g1")
    world.file("g1", "f-a", is_final=False)
    world.file("g1", "f-b")
    world.file("g1", "f-c", is_derived=True)
    world.file("g1", "f-d")

    final = api.list_generation_files(world.owner, "g1")
    everything = api.list_generation_files(world.owner, "g1", final_only=False)

    assert [(f["id"], f["index"]) for f in final] == [("f-b", 1), ("f-d", 3)]
    assert [(f["id"], f["index"]) for f in everything] == [("f-a", 0), ("f-b", 1), ("f-c", 2), ("f-d", 3)]


def test_files_of_someone_elses_generation_are_not_found(world):
    world.generation("g1")
    world.file("g1", "f1")

    with pytest.raises(GenerationNotFoundException):
        api.list_generation_files(world.other, "g1")
    assert [f["id"] for f in api.list_generation_files(world.admin, "g1")] == ["f1"]


def test_a_restricted_user_only_gets_files_judged_safe_and_keeps_the_true_index(restricted_world):
    world = restricted_world
    world.generation("g1")
    world.file("g1", "f1-flagged", score=0.95)
    world.file("g1", "f2-safe", score=0.05)
    world.file("g1", "f3-new", score=None)

    files = api.list_generation_files(world.owner, "g1", final_only=False)

    assert [(f["id"], f["index"]) for f in files] == [("f2-safe", 1), ("f3-new", 2)]
    placeholder = files[1]
    assert placeholder["content_state"] == "unrated"
    assert "file_path" not in placeholder


def test_a_generation_with_only_flagged_files_is_not_found_for_a_restricted_user(restricted_world):
    world = restricted_world
    world.generation("g1")
    world.file("g1", "f1-flagged", score=0.99)

    with pytest.raises(GenerationNotFoundException):
        api.list_generation_files(world.owner, "g1")


def test_a_history_ref_resolves_to_the_path_a_media_field_takes(world):
    world.generation("g1")
    world.file("g1", "f-a")
    world.file("g1", "f-b")
    ref = {"kind": "history", "generation_id": "g1", "file_id": "f-b"}

    resolved = api.resolve_media_ref(world.owner, ref)

    assert resolved == {
        "value": "generations/g1/f-b.png",
        "media_type": "image",
        "original_filename": "f-b.png",
        "origin": {"generation_id": "g1", "file_index": 1},
    }


def test_a_history_ref_into_someone_elses_generation_is_refused(world):
    world.generation("g1")
    world.file("g1", "f1")

    with pytest.raises(api.MediaRefError) as refused:
        api.resolve_media_ref(world.other, {"kind": "history", "generation_id": "g1", "file_id": "f1"})

    assert refused.value.code == "not_found"


def test_a_history_ref_cannot_borrow_a_file_of_another_generation(world):
    world.generation("g1")
    world.generation("g2")
    world.file("g2", "f-other")

    with pytest.raises(api.MediaRefError) as refused:
        api.resolve_media_ref(world.owner, {"kind": "history", "generation_id": "g1", "file_id": "f-other"})

    assert refused.value.code == "not_found"


def test_a_history_ref_to_a_deleted_file_is_refused(world):
    world.generation("g1")
    world.file("g1", "f1")
    world.existing_paths.clear()

    with pytest.raises(api.MediaRefError) as refused:
        api.resolve_media_ref(world.owner, {"kind": "history", "generation_id": "g1", "file_id": "f1"})

    assert refused.value.code == "not_found"


def test_a_history_ref_to_a_file_the_content_policy_hides_is_refused(restricted_world):
    world = restricted_world
    world.generation("g1")
    world.file("g1", "f2-safe", score=0.01)
    world.file("g1", "f1-flagged", score=0.95)
    world.file("g1", "f3-new", score=None)

    assert api.resolve_media_ref(world.owner, {"kind": "history", "generation_id": "g1", "file_id": "f2-safe"})["origin"]["file_index"] == 1
    for file_id in ("f1-flagged", "f3-new"):
        with pytest.raises(api.MediaRefError):
            api.resolve_media_ref(world.owner, {"kind": "history", "generation_id": "g1", "file_id": file_id})


def test_a_library_ref_resolves_to_the_upload_path(world):
    world.upload("up1", filename="u1.png")

    resolved = api.resolve_media_ref(world.owner, {"kind": "library", "item_id": "up1"})

    assert resolved == {
        "value": "uploads/u1.png",
        "media_type": "image",
        "original_filename": "cat.png",
        "origin": None,
    }


def test_a_library_ref_to_someone_elses_upload_is_refused(world):
    world.upload("up1")

    with pytest.raises(api.MediaRefError) as refused:
        api.resolve_media_ref(world.other, {"kind": "library", "item_id": "up1"})

    assert refused.value.code == "not_found"


def test_a_library_ref_whose_file_is_gone_is_refused(world):
    world.upload("up1")
    world.existing_uploads.clear()

    with pytest.raises(api.MediaRefError) as refused:
        api.resolve_media_ref(world.owner, {"kind": "library", "item_id": "up1"})

    assert refused.value.code == "not_found"


@pytest.mark.parametrize("ref", [
    {"kind": "url", "url": "http://example.test/a.png"},
    {"kind": "path", "path": "uploads/u1.png"},
    {"kind": "library"},
    {"kind": "library", "item_id": "up1", "path": "../../etc/passwd"},
    {"kind": "history", "generation_id": "g1"},
    {"kind": "history", "generation_id": "g1", "file_id": "f1", "item_id": "up1"},
    {"kind": "history", "generation_id": 1, "file_id": "f1"},
    {"item_id": "up1"},
    "uploads/u1.png",
    None,
])
def test_a_malformed_or_unsupported_ref_is_invalid(world, ref):
    world.upload("up1")

    with pytest.raises(api.MediaRefError) as refused:
        api.resolve_media_ref(world.owner, ref)

    assert refused.value.code == "invalid_ref"


def test_a_session_snapshot_returns_the_owners_payload(world):
    world.sessions.create(Session(id="s1", user_id="owner", preset_id="p", name="mine", data={"modes": {"txt2img": {"prompt": "a"}}}))

    snapshot = api.get_session_snapshot(world.owner, "s1")

    assert snapshot["data"] == {"modes": {"txt2img": {"prompt": "a"}}}
    assert (snapshot["session_id"], snapshot["preset_id"], snapshot["name"], snapshot["version"]) == ("s1", "p", "mine", None)


def test_a_session_snapshot_can_read_a_saved_version(world):
    world.sessions.create(Session(id="s1", user_id="owner", preset_id="p", name="mine", data={"v": "current"}))
    world.versions.create_if_changed("s1", {"v": "first"}, None)

    assert api.get_session_snapshot(world.owner, "s1", version=1)["data"] == {"v": "first"}
    with pytest.raises(api.SessionNotFoundError):
        api.get_session_snapshot(world.owner, "s1", version=9)


def test_another_users_session_and_an_unknown_one_look_the_same(world):
    world.sessions.create(Session(id="s1", user_id="owner", preset_id="p", name="mine", data={}))

    for user, session_id in ((world.other, "s1"), (world.admin, "s1"), (world.owner, "nope")):
        with pytest.raises(api.SessionNotFoundError):
            api.get_session_snapshot(user, session_id)
