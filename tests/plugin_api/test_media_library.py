import math
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import cv2
import numpy as np
import pytest
from PIL import Image

import src.plugin_api as root
from src.features.content_safety.gate import ContentGate
from src.features.content_safety.ledger_repository import ContentLedger
from src.features.content_safety.manager import ContentSafetyManager
from src.features.generation.file_repository import FileRepository
from src.features.generation.history_query import GenerationHistoryQuery
from src.features.generation.records import File, Generation
from src.features.generation.repository import GenerationRepository
from src.features.media import validators
from src.features.media.file_resolver import FilePathResolver
from src.features.media.hooks import MEDIA_HOOKS
from src.features.media.image_processor import ImageProcessor
from src.features.media.media_types import MediaTypeResolver
from src.features.media.records import Upload
from src.features.media.store import MediaStore
from src.features.media.upload_repository import UploadRepository
from src.features.plans.errors import LimitExceeded
from src.platform.database.rows import now_utc
from src.platform.filesystem.file_store import FileStore
from src.platform.filesystem.storage_driver import LocalFileStorageDriver
from src.platform.plugins import PluginRegistry, runtime_registries
from src.platform.security.user import AccountType, User
from src.platform.settings.settings import Settings
from src.platform.util.video_transcode import VideoProbe, find_ffprobe
from src.plugin_api import generation, media
from src.plugin_api.media import MediaRefError, extract_frame, save_user_media
from src.plugin_api.presets import bind_director_media
from tests.features.content_safety.fakes import FakeResolver, FakeSettings, FakeTagger
from tests.fixtures.seed import seed_user

FRAMES = 10
FPS = 10.0
SIZE = 32


def red_of(frame_index):
    return frame_index * 25


def write_clip(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), FPS, (SIZE, SIZE))
    for index in range(FRAMES):
        frame = np.zeros((SIZE, SIZE, 3), dtype=np.uint8)
        frame[:, :, 2] = red_of(index)
        writer.write(frame)
    writer.release()


def _user(user_id, account_type=AccountType.USER):
    return User(
        id=user_id, username=user_id, email=f"{user_id}@example.test", password_hash="$2b$12$x",
        account_type=account_type, created_at=now_utc(), last_login=None,
    )


def limit_error():
    return LimitExceeded(
        [{
            "message": "Storage plan is full", "code": "storage_full", "kind": "storage", "label": "Storage",
            "format": "bytes", "used": 1, "limit": 1, "percent": 100, "resets_at": None,
        }],
        "upload",
        None,
    )


class Guard:
    def __init__(self):
        self.refusal = None
        self.checked = []

    def check_items(self, request):
        self.checked.append(request.incoming_bytes)
        if self.refusal is not None:
            raise self.refusal

    def admit(self, request):
        if self.refusal is not None:
            raise self.refusal


class World:
    def __init__(self, db, root_dir, restricted=False, blocked_hook=None):
        self.db = db
        self.seeded = set()
        for user_id in ("owner", "other"):
            seed_user(db, user_id)
        self.owner = _user("owner")
        self.other = _user("other")
        self.outputs = root_dir / "outputs"
        self.library = root_dir / "library"
        self.generations = GenerationRepository()
        self.files = FileRepository()
        self.uploads = UploadRepository()
        settings = FakeSettings()
        self.ledger = ContentLedger(settings)
        safety = ContentSafetyManager(
            settings=settings, resolver=FakeResolver("blocked", restricted),
            ledger=self.ledger, gate=ContentGate(FakeTagger(), settings),
        )
        self.guard = Guard()
        self.hooks = Mock(spec=PluginRegistry)
        self.hooks.execute_hook.side_effect = lambda hook, initial_data=None: (
            SimpleNamespace(data={**(initial_data or {}), "blocked": hook == blocked_hook, "block_reason": "Blocked by policy"}),
            [],
        )
        file_service = FileStore(str(self.outputs))
        self.store = MediaStore(
            file_resolver=Mock(spec=FilePathResolver),
            image_processor=ImageProcessor(),
            media_type_resolver=MediaTypeResolver(),
            file_repository=Mock(),
            generation_repository=Mock(),
            settings=Mock(spec=Settings),
            file_service=Mock(),
            plugin_registry=self.hooks,
            upload_repository=self.uploads,
            storage_driver=LocalFileStorageDriver(str(self.library)),
            limit_guard=self.guard,
        )
        self.container = SimpleNamespace(
            generation_orchestrator=SimpleNamespace(status_tracker=SimpleNamespace(get=lambda gid: None)),
            generation_repository=self.generations,
            generation_history_facade=SimpleNamespace(
                query=GenerationHistoryQuery(self.generations, content_safety=safety)
            ),
            file_service=file_service,
            media_store=self.store,
        )

    def library_clip(self, item_id="clip-1", user_id="owner"):
        write_clip(self.library / "uploads" / f"{item_id}.avi")
        self.seeded.add(item_id)
        self.uploads.create(Upload(
            id=item_id, user_id=user_id, filename=f"{item_id}.avi", original_filename="clip.avi",
            media_type="video", mime_type="video/avi", file_size=1,
        ))
        return {"kind": "library", "item_id": item_id}

    def library_image(self, item_id="still-1"):
        (self.library / "uploads").mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (4, 4)).save(self.library / "uploads" / f"{item_id}.png")
        self.seeded.add(item_id)
        self.uploads.create(Upload(
            id=item_id, user_id="owner", filename=f"{item_id}.png", original_filename="still.png",
            media_type="image", mime_type="image/png", file_size=1,
        ))
        return {"kind": "library", "item_id": item_id}

    def history_clip(self, generation_id="g1", file_id="f1", score=0.01):
        path = f"generations/2026-11-07/{generation_id}/out.avi"
        write_clip(self.outputs / path)
        self.generations.create(Generation(
            id=generation_id, preset_id="p", form_data={}, user_id="owner", status="completed",
        ))
        self.files.create(File(
            id=file_id, file_path=path, file_type="video", user_id="owner", mime_type="video/avi",
            file_size=1, pipe_name="generator", is_final=True, is_derived=False,
        ))
        self.files.associate_with_generation(generation_id, file_id)
        if score is not None:
            self.ledger.record_score(path, score)
        return {"kind": "history", "generation_id": generation_id, "file_id": file_id}

    def uploads_of(self, user_id):
        with self.db.get_cursor() as cursor:
            cursor.execute("SELECT id, purpose, original_filename FROM uploads WHERE user_id = ? ORDER BY rowid", (user_id,))
            return [(row["purpose"], row["original_filename"]) for row in cursor.fetchall() if row["id"] not in self.seeded]


def _probe(duration=FRAMES / FPS, fps=FPS):
    return VideoProbe(duration=duration, width=SIZE, height=SIZE, fps=fps, video_codec="mjpeg", has_audio=False, size_bytes=1)


@pytest.fixture
def probe(monkeypatch):
    found = Mock(return_value=_probe())
    monkeypatch.setattr(media, "probe_video", found)
    return found


def _world(mock_db, tmp_path, monkeypatch, **options):
    built = World(mock_db, tmp_path, **options)
    monkeypatch.setattr(runtime_registries, "_container", built.container)
    return built


@pytest.fixture
def world(mock_db, tmp_path, monkeypatch, probe):
    return _world(mock_db, tmp_path, monkeypatch)


@pytest.fixture
def export(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    path = work / "board.png"
    Image.new("RGB", (8, 6), (10, 20, 30)).save(path)
    return path


def stored_red(world, stored):
    with Image.open(world.library / stored["value"]) as image:
        return image.convert("RGB").getpixel((SIZE // 2, SIZE // 2))[0]


@pytest.mark.asyncio
async def test_the_last_frame_of_a_library_video_becomes_a_png_upload_of_the_user(world, probe):
    stored = await extract_frame(world.owner, world.library_clip())

    assert stored["value"].startswith("uploads/") and stored["value"].endswith(".png")
    assert (stored["media_type"], stored["original_filename"], stored["origin"]) == ("image", "clip-frame-last.png", None)
    assert (stored["width"], stored["height"]) == (SIZE, SIZE)
    assert abs(stored_red(world, stored) - red_of(FRAMES - 1)) <= 8
    assert world.uploads_of("owner") == [("user_upload", "clip-frame-last.png")]
    probe.assert_not_called()


@pytest.mark.asyncio
async def test_the_stored_frame_binds_as_the_first_frame_of_a_video_director_request(world):
    request = {"preset_id": "p", "form_data": {"video_director": {"mode": "t2v", "segments": [{"id": "s1"}]}}}

    stored = await extract_frame(world.owner, world.library_clip())
    bound = bind_director_media(request, "first", stored, promote=True)["form_data"]["video_director"]

    assert bound["mode"] == "i2v"
    assert [(entry["role"], entry["media"]["path"]) for entry in bound["media"]] == [("first", stored["value"])]


@pytest.mark.asyncio
async def test_the_stored_frame_is_a_library_item_its_owner_can_reference(world):
    stored = await extract_frame(world.owner, world.library_clip())

    assert generation.resolve_media_ref(world.owner, stored["ref"])["value"] == stored["value"]
    with pytest.raises(MediaRefError) as refused:
        generation.resolve_media_ref(world.other, stored["ref"])
    assert refused.value.code == "not_found"


@pytest.mark.asyncio
@pytest.mark.parametrize("at, frame_index, label", [(0, 0, "0s"), (0.45, 4, "0.45s"), (1.0, FRAMES - 1, "1s")])
async def test_seconds_pick_the_frame_shown_at_that_time(world, at, frame_index, label):
    stored = await extract_frame(world.owner, world.library_clip(), at=at)

    assert stored["original_filename"] == f"clip-frame-{label}.png"
    assert abs(stored_red(world, stored) - red_of(frame_index)) <= 8


@pytest.mark.asyncio
@pytest.mark.skipif(find_ffprobe() is None, reason="ffprobe is not installed")
async def test_seconds_go_through_the_frame_rate_ffprobe_reads(mock_db, tmp_path, monkeypatch):
    world = _world(mock_db, tmp_path, monkeypatch)

    stored = await extract_frame(world.owner, world.library_clip(), at=0.65)

    assert stored["original_filename"] == "clip-frame-0.65s.png"
    assert abs(stored_red(world, stored) - red_of(6)) <= 8


@pytest.mark.asyncio
async def test_a_time_computed_from_the_frame_rate_lands_on_that_frame(world, probe):
    probe.return_value = _probe(duration=FRAMES / 49, fps=49.0)

    stored = await extract_frame(world.owner, world.library_clip(), at=4 / 49)

    assert abs(stored_red(world, stored) - red_of(4)) <= 8


@pytest.mark.asyncio
async def test_a_frame_of_a_generated_video_is_read_from_the_generation_storage(world):
    stored = await extract_frame(world.owner, world.history_clip(), at=0.2)

    assert stored["original_filename"] == "out-frame-0.2s.png"
    assert abs(stored_red(world, stored) - red_of(2)) <= 8


@pytest.mark.asyncio
async def test_another_user_cannot_take_a_frame_of_someone_elses_video(world):
    refs = [world.library_clip(), world.history_clip()]

    for ref in refs:
        with pytest.raises(MediaRefError) as refused:
            await extract_frame(world.other, ref)
        assert refused.value.code == "not_found"
    assert world.uploads_of("other") == []
    assert world.uploads_of("owner") == []


@pytest.mark.asyncio
async def test_a_restricted_user_only_takes_frames_of_videos_judged_safe(mock_db, tmp_path, monkeypatch, probe):
    world = _world(mock_db, tmp_path, monkeypatch, restricted=True)
    safe = world.history_clip("g-safe", "f-safe", score=0.01)
    unrated = world.history_clip("g-unrated", "f-unrated", score=None)

    assert (await extract_frame(world.owner, safe))["media_type"] == "image"
    with pytest.raises(MediaRefError) as refused:
        await extract_frame(world.owner, unrated)
    assert refused.value.code == "not_found"


@pytest.mark.asyncio
@pytest.mark.parametrize("at", ["first", -1, True, math.nan, math.inf, None])
async def test_an_unreadable_at_is_an_invalid_ref(world, at):
    with pytest.raises(MediaRefError) as refused:
        await extract_frame(world.owner, world.library_clip(), at=at)

    assert refused.value.code == "invalid_ref"


@pytest.mark.asyncio
async def test_extraction_refusals_carry_their_codes(world):
    clip = world.library_clip()
    cases = [
        ({"kind": "library", "item_id": "missing"}, "last", "not_found"),
        ({"kind": "library", "item_id": "clip-1", "path": "/etc/passwd"}, "last", "invalid_ref"),
        (world.library_image(), "last", "not_a_video"),
        (clip, 2.5, "extract_failed"),
    ]
    for ref, at, code in cases:
        with pytest.raises(MediaRefError) as refused:
            await extract_frame(world.owner, ref, at=at)
        assert refused.value.code == code
        assert str(world.library) not in str(refused.value) and ".avi" not in str(refused.value)
    assert world.uploads_of("owner") == []


@pytest.mark.asyncio
async def test_a_video_whose_frame_rate_cannot_be_read_fails_for_seconds_only(world, probe):
    probe.return_value = None
    clip = world.library_clip()

    with pytest.raises(MediaRefError) as refused:
        await extract_frame(world.owner, clip, at=0.5)
    stored = await extract_frame(world.owner, clip)

    assert refused.value.code == "extract_failed"
    assert stored["media_type"] == "image"


@pytest.mark.asyncio
async def test_a_frame_can_be_kept_out_of_the_library(world):
    stored = await extract_frame(world.owner, world.library_clip(), purpose="derived_artifact")

    assert world.uploads_of("owner") == [("derived_artifact", "clip-frame-last.png")]
    assert (world.library / stored["value"]).is_file()


@pytest.mark.asyncio
async def test_an_unknown_purpose_and_a_missing_user_are_refused(world):
    clip = world.library_clip()

    with pytest.raises(MediaRefError) as refused:
        await extract_frame(world.owner, clip, purpose="plugin")
    with pytest.raises(PermissionError):
        await extract_frame(None, clip)

    assert refused.value.code == "invalid_purpose"


@pytest.mark.asyncio
async def test_a_plugin_file_becomes_a_library_upload_of_the_user(world, export):
    stored = await save_user_media(world.owner, export)

    assert stored["value"].startswith("uploads/") and stored["value"].endswith(".png")
    assert (stored["media_type"], stored["original_filename"], stored["origin"]) == ("image", "board.png", None)
    assert (stored["width"], stored["height"]) == (8, 6)
    assert (world.library / stored["value"]).read_bytes() == export.read_bytes()
    assert world.uploads_of("owner") == [("user_upload", "board.png")]
    assert generation.resolve_media_ref(world.owner, stored["ref"])["value"] == stored["value"]
    with pytest.raises(MediaRefError):
        generation.resolve_media_ref(world.other, stored["ref"])


@pytest.mark.asyncio
async def test_a_saved_file_stays_out_of_the_library_as_a_derived_artifact(world, export):
    first = await save_user_media(world.owner, str(export), purpose="derived_artifact")
    again = await save_user_media(world.owner, str(export), purpose="derived_artifact")

    assert again["value"] == first["value"]
    assert world.uploads_of("owner") == [("derived_artifact", "board.png")]


@pytest.mark.asyncio
async def test_unusable_files_are_refused_with_a_code(world, export, tmp_path):
    notes = tmp_path / "work" / "notes.txt"
    notes.write_text("not media")
    cases = [
        (Path("work") / "board.png", {}, "invalid_ref"),
        (tmp_path / "work" / "gone.png", {}, "not_found"),
        (tmp_path / "work", {}, "not_found"),
        (notes, {}, "unsupported_media"),
        (export, {"purpose": "plugin"}, "invalid_purpose"),
    ]
    for path, options, code in cases:
        with pytest.raises(MediaRefError) as refused:
            await save_user_media(world.owner, path, **options)
        assert refused.value.code == code
    assert world.uploads_of("owner") == []


@pytest.mark.asyncio
async def test_an_upload_a_plugin_hook_blocks_is_rejected(mock_db, tmp_path, monkeypatch, export):
    world = _world(mock_db, tmp_path, monkeypatch, blocked_hook=MEDIA_HOOKS.before_upload)

    with pytest.raises(MediaRefError) as refused:
        await save_user_media(world.owner, export)

    assert refused.value.code == "upload_rejected"
    assert str(refused.value) == "Blocked by policy"
    assert world.uploads_of("owner") == []


@pytest.mark.asyncio
async def test_a_full_storage_plan_refuses_the_file_as_limit_exceeded(world, export):
    world.guard.refusal = limit_error()

    with pytest.raises(LimitExceeded):
        await save_user_media(world.owner, export)

    assert world.guard.checked == [export.stat().st_size]
    assert world.uploads_of("owner") == []


@pytest.mark.asyncio
async def test_a_file_over_the_helper_file_cap_is_refused_before_it_is_read(world, export, monkeypatch):
    def unread(self):
        raise AssertionError(f"{self} was read")

    monkeypatch.setattr(validators, "DERIVED_ARTIFACT_MAX_BYTES", export.stat().st_size - 1)
    monkeypatch.setattr(Path, "read_bytes", unread)
    with pytest.raises(MediaRefError) as refused:
        await save_user_media(world.owner, export, purpose="derived_artifact")

    assert refused.value.code == "upload_rejected"
    assert world.uploads_of("owner") == []


@pytest.mark.asyncio
async def test_saving_needs_a_user(world, export):
    with pytest.raises(PermissionError):
        await save_user_media(SimpleNamespace(id=None), export)


def test_the_media_names_import_from_the_root_and_the_media_module():
    for name in ("extract_frame", "save_user_media", "StoredMedia"):
        assert name in media.__all__ and name in root.__all__
        assert getattr(root, name) is getattr(media, name)
    for name in ("resolve_media_ref", "MediaRefError", "HistoryMediaRef", "LibraryMediaRef", "ResolvedMedia"):
        assert name in media.__all__ and name in root.__all__
        assert getattr(root, name) is getattr(media, name) is getattr(generation, name)
