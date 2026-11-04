import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.features.content_safety.gate import ContentGate
from src.features.content_safety.ledger_repository import ContentLedger
from src.features.content_safety.manager import ContentSafetyManager
from src.features.elixirs.errors import MediaRefError, SessionNotFoundError, WriteRefused
from src.features.elixirs.generation_port import GenerationPort
from src.features.elixirs.session_reader import SessionReader
from src.features.generation.exceptions import GenerationNotFoundException
from src.features.generation.file_repository import FileRepository
from src.features.generation.history_query import GenerationHistoryQuery
from src.features.generation.output_broadcaster import GenerationOutputBroadcaster
from src.features.generation.records import File, Generation
from src.features.generation.repository import GenerationRepository
from src.features.media.records import Upload
from src.features.media.upload_repository import UploadRepository
from src.features.prompt_enhancement.collaborators import PromptEnhancementCollaborators
from src.features.sessions.dto import Session
from src.features.sessions.repository import SessionRepository
from src.features.sessions.version_repository import SessionVersionRepository
from src.platform.database.rows import now_utc
from src.platform.security.user import AccountType, User
from tests.features.content_safety.fakes import FakeResolver, FakeSettings, FakeTagger
from tests.fixtures.seed import seed_user


def _user(user_id, account_type=AccountType.USER):
    return User(
        id=user_id, username=user_id, email=f"{user_id}@example.test", password_hash="$2b$12$x",
        account_type=account_type, created_at=now_utc(), last_login=None,
    )


def _writer(tmp_path, supports_vision=True):
    from PIL import Image

    (tmp_path / "uploads").mkdir(exist_ok=True)
    Image.new("RGB", (8, 8)).save(tmp_path / "uploads" / "a.png")
    calls = []
    config = SimpleNamespace(id="llm-1", name="Writer", supports_vision=supports_vision, enabled=True)

    class Llm:
        repository = SimpleNamespace(
            get_configuration=lambda llm_id: config if llm_id == "llm-1" else None,
            get_default_configuration=lambda: config,
            get_user_llm_assignments=lambda user_id: [],
        )

        async def complete(self, llm_id, messages, *, system=None, settings=None, tools=()):
            calls.append(messages)
            return SimpleNamespace(text="written prompt", message=SimpleNamespace(reasoning=lambda: []))

    preset = SimpleNamespace(id="p", name="Preset P", short_description=None, description=None, llm={})
    presets = SimpleNamespace(
        file_repo=SimpleNamespace(find_preset_by_id=lambda preset_id: preset if preset_id == "p" else None),
        db_repo=SimpleNamespace(get_available_preset_ids_for_user=lambda user_id: ["p"] if user_id == "owner" else []),
    )
    return PromptEnhancementCollaborators(llm_service=Llm(), preset_collaborators=presets), calls


class World:
    def __init__(self, db, tmp_path, restricted=False, supports_vision=True):
        for user_id in ("owner", "other", "admin"):
            seed_user(db, user_id)
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
        self.broadcaster = GenerationOutputBroadcaster(self.hub, self.orchestrator.status_tracker, Mock())
        self.prompt_writer, self.writes = _writer(tmp_path, supports_vision)
        users = {user.id: user for user in (self.owner, self.other, self.admin)}
        self.port = GenerationPort(
            users=SimpleNamespace(get_by_id=users.get),
            orchestrator=self.orchestrator,
            broadcaster=self.broadcaster,
            generations=self.generations,
            generation_files=self.files,
            history=SimpleNamespace(query=GenerationHistoryQuery(self.generations, content_safety=manager)),
            file_service=SimpleNamespace(generation_exists=lambda path: path in self.existing_paths),
            media_store=SimpleNamespace(
                upload_repo=self.uploads,
                storage_driver=SimpleNamespace(exists=lambda key: key in self.existing_uploads),
            ),
            prompt_writer=self.prompt_writer,
            storage_root=lambda: str(tmp_path),
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
def world(mock_db, tmp_path):
    return World(mock_db, tmp_path)


@pytest.fixture
def restricted_world(mock_db, tmp_path):
    return World(mock_db, tmp_path, restricted=True)


def test_a_submit_starts_the_generation_for_the_brew_owner(world):
    submitted = asyncio.run(world.port.submit(world.owner, {"preset_id": "p", "mode": "txt2img", "form_data": {"seed": 3}}))

    assert submitted == {"generation_id": "g-new", "status": "pending", "queue_position": 2}
    request, user_id = world.orchestrator.start_generation.await_args.args
    assert user_id == "owner" and request.form_data == {"seed": 3}


def test_a_submit_without_an_owner_is_refused(world):
    with pytest.raises(PermissionError):
        asyncio.run(world.port.submit(None, {"preset_id": "p"}))


def test_state_of_someone_elses_generation_is_not_found(world):
    world.generation("g1")

    assert world.port.state(world.owner, "g1")["status"] == "completed"
    assert world.port.state(world.admin, "g1")["status"] == "completed"
    with pytest.raises(GenerationNotFoundException):
        world.port.state(world.other, "g1")
    with pytest.raises(GenerationNotFoundException):
        world.port.state(world.owner, "missing")


def test_only_final_files_come_back_with_their_unfiltered_index(world):
    world.generation("g1")
    world.file("g1", "f1", is_final=False)
    world.file("g1", "f2")
    world.file("g1", "f3", is_derived=True)
    world.file("g1", "f4")

    files = world.port.files(world.owner, "g1")

    assert [(entry["id"], entry["index"]) for entry in files] == [("f2", 1), ("f4", 3)]
    with pytest.raises(GenerationNotFoundException):
        world.port.files(world.other, "g1")


def test_a_cancel_reaches_the_orchestrator_only_for_the_owner(world):
    world.generation("g1")

    with pytest.raises(GenerationNotFoundException):
        asyncio.run(world.port.cancel(world.other, "g1"))
    world.orchestrator.cancel_generation.assert_not_awaited()

    assert asyncio.run(world.port.cancel(world.owner, "g1")) is True
    world.orchestrator.cancel_generation.assert_awaited_once_with("g1")


def test_a_history_ref_resolves_to_the_path_a_media_field_takes(world):
    world.generation("g1")
    world.file("g1", "f0", is_final=False)
    world.file("g1", "f1")

    resolved = world.port.resolve(world.owner, {"kind": "history", "generation_id": "g1", "file_id": "f1"})

    assert resolved == {
        "value": "generations/g1/f1.png",
        "media_type": "image",
        "original_filename": "f1.png",
        "origin": {"generation_id": "g1", "file_index": 1},
    }


def test_a_history_ref_into_someone_elses_or_a_deleted_file_is_refused(world):
    world.generation("g1")
    world.file("g1", "f1")
    world.generation("g2")
    world.file("g2", "f2")
    world.existing_paths.discard("generations/g2/f2.png")

    for user, ref in (
        (world.other, {"kind": "history", "generation_id": "g1", "file_id": "f1"}),
        (world.owner, {"kind": "history", "generation_id": "g1", "file_id": "f2"}),
        (world.owner, {"kind": "history", "generation_id": "g2", "file_id": "f2"}),
    ):
        with pytest.raises(MediaRefError) as refused:
            world.port.resolve(user, ref)
        assert refused.value.code == "not_found"


def test_a_history_ref_to_a_file_the_content_policy_hides_is_refused(restricted_world):
    restricted_world.generation("g1")
    restricted_world.file("g1", "f1", score=0.99)

    with pytest.raises(MediaRefError):
        restricted_world.port.resolve(restricted_world.owner, {"kind": "history", "generation_id": "g1", "file_id": "f1"})


def test_a_library_ref_resolves_only_for_the_uploader_while_the_file_exists(world):
    world.upload("up1", filename="cat.png")
    ref = {"kind": "library", "item_id": "up1"}

    assert world.port.resolve(world.owner, ref) == {
        "value": "uploads/cat.png", "media_type": "image", "original_filename": "cat.png", "origin": None,
    }
    with pytest.raises(MediaRefError):
        world.port.resolve(world.other, ref)
    world.existing_uploads.clear()
    with pytest.raises(MediaRefError):
        world.port.resolve(world.owner, ref)


@pytest.mark.parametrize("ref", [
    "uploads/cat.png",
    {"kind": "path", "item_id": "x"},
    {"kind": "library", "item_id": "x", "extra": 1},
    {"kind": "history", "generation_id": "", "file_id": "f"},
    {"kind": "library", "item_id": 7},
])
def test_a_malformed_or_unsupported_ref_is_invalid(world, ref):
    with pytest.raises(MediaRefError) as refused:
        world.port.resolve(world.owner, ref)
    assert refused.value.code == "invalid_ref"


def test_the_writer_sends_the_stored_image_and_reports_the_default_llm(world):
    result = asyncio.run(world.port.write_prompt(world.owner, {
        "instruction": "Describe it", "preset_id": "p", "mode": "txt2img",
        "media": [{"value": "uploads/a.png", "media_type": "image"}],
    }))

    assert result["text"] == "written prompt"
    assert [type(part).__name__ for part in world.writes[0][0].parts] == ["TextPart", "ImagePart"]
    status = world.port.writer_status(world.owner)
    assert status["available"] is True and status["vision"] is True


def test_the_writer_refuses_a_preset_the_user_cannot_see_before_reading_media(world):
    with patch("src.features.elixirs.generation_port.encode_media", AsyncMock(side_effect=AssertionError("read media"))):
        with pytest.raises(WriteRefused) as refused:
            asyncio.run(world.port.write_prompt(world.other, {
                "instruction": "Describe it", "preset_id": "p", "media": [{"value": "uploads/a.png", "media_type": "image"}],
            }))

    assert "not available to you" in str(refused.value)
    assert world.writes == []


def test_the_writer_refuses_media_a_text_only_model_cannot_read(mock_db, tmp_path):
    world = World(mock_db, tmp_path, supports_vision=False)

    with patch("src.features.elixirs.generation_port.encode_media", AsyncMock(side_effect=AssertionError("read media"))):
        with pytest.raises(WriteRefused):
            asyncio.run(world.port.write_prompt(world.owner, {
                "instruction": "Describe it", "media": [{"value": "uploads/a.png", "media_type": "image"}],
            }))
    assert world.writes == []


def test_an_admin_may_write_for_any_preset(world):
    result = asyncio.run(world.port.write_prompt(world.admin, {"instruction": "Describe it", "preset_id": "p"}))

    assert result["text"] == "written prompt"
    assert "Target preset: Preset P" in world.writes[0][0].parts[0].text


def _session(world, session_id="s1", owner="owner"):
    return world.sessions.create(Session(id=session_id, preset_id="p", name="Shoot", data={"txt2img": {"prompt": "a"}}, user_id=owner))


def test_a_session_read_returns_only_the_owners_session(world):
    _session(world)
    reader = SessionReader(world.sessions, world.versions)

    current = reader.get_session(world.owner, "s1")

    assert (current["session_id"], current["preset_id"], current["name"]) == ("s1", "p", "Shoot")
    assert current["data"] == {"txt2img": {"prompt": "a"}} and current["version"] >= 1
    for user, session_id in ((world.other, "s1"), (world.owner, "missing")):
        with pytest.raises(SessionNotFoundError):
            reader.get_session(user, session_id)


def test_a_session_snapshot_reads_the_current_data_or_a_saved_version(world):
    _session(world)
    reader = SessionReader(world.sessions, world.versions)

    assert reader.get_snapshot(world.owner, "s1")["data"] == {"txt2img": {"prompt": "a"}}
    with pytest.raises(SessionNotFoundError):
        reader.get_snapshot(world.owner, "s1", 99)
    with pytest.raises(SessionNotFoundError):
        reader.get_snapshot(world.other, "s1")
