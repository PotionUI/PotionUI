import asyncio
import copy
from typing import Callable, Optional

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.features.elixirs.components import ElixirsComponents
from src.features.elixirs.coordinator import Coordinator
from src.features.elixirs.errors import MediaRefError
from src.features.elixirs.follow import SessionFollows
from src.features.elixirs.identity import asset_key
from src.features.elixirs.limits import Limits
from src.features.elixirs.repository import ElixirRepository
from src.features.elixirs.routes import create_router
from src.features.elixirs.run_repository import RunStore
from src.features.elixirs.runtime import ElixirRuntime
from src.features.elixirs.session_reader import SessionReader
from src.features.generation.exceptions import GenerationNotFoundException
from src.features.video_director.binding import bind_director_media
from src.platform.security.current_user import get_current_active_user
from src.platform.security.user import AccountType, User
from tests.fixtures.db_template import copy_template_db

USER_IDS = ("alice", "bob", "carol", "u1", "u2")

KREA = "4TK1KBQZ2XMB8ME0PTMXS1YJQP"
H3 = "01KX47H3MINIMAX000000000VA"
SEEDVR = "01KXB7C553THYMSMKY1QSYESFM"


def _fields(**fields):
    return {name: {"type": kind, "required": required} for name, (kind, required) in fields.items()}


FAKE_CATALOG = {
    KREA: {
        "name": "Krea-2",
        "director_modes": [],
        "modes": {
            "txt2img": {
                "default_form": "custom",
                "forms": {"custom": _fields(seed=("seed", False), quantity=("stepper", False))},
            },
            "enhance": {
                "default_form": "custom",
                "forms": {
                    "custom": _fields(
                        source_image=("image", True), seed=("seed", False), quantity=("stepper", False)
                    )
                },
            },
        },
    },
    H3: {
        "name": "MiniMax-H3",
        "director_modes": ["video", "refs"],
        "director_shapes": {"video": ["director", "flf", "i2v", "t2v"], "refs": ["director", "flf", "i2v", "t2v"]},
        "modes": {
            "video": {
                "default_form": "custom",
                "forms": {"custom": _fields(seed=("seed", False), quantity=("stepper", False))},
            },
            "upscale": {
                "default_form": "custom",
                "forms": {
                    "custom": _fields(
                        input_video=("video", True), seed=("seed", False), quantity=("stepper", False)
                    )
                },
            },
            "refs": {"default_form": "custom", "forms": {"custom": {}}},
        },
    },
    SEEDVR: {
        "name": "SeedVR2",
        "director_modes": [],
        "modes": {
            "upscale": {
                "default_form": "custom",
                "forms": {"custom": _fields(input_image=("image", True), seed=("seed", False))},
            },
            "video_upscale": {
                "default_form": "custom",
                "forms": {"custom": _fields(input_video=("video", True), seed=("seed", False))},
            },
        },
    },
}


@pytest.fixture
def catalog():
    return copy.deepcopy(FAKE_CATALOG)


@pytest.fixture
def contracts():
    from src.features.elixirs.contracts import load_contracts

    return load_contracts()


@pytest.fixture
def scratch_db(tmp_path, monkeypatch):
    from src.platform.database.database import db

    path = tmp_path / "elixirs.sqlite"
    copy_template_db(path)
    monkeypatch.setattr(db, "db_path", path)
    with db.get_cursor() as cursor:
        for user_id in USER_IDS:
            cursor.execute(
                "INSERT INTO users (id, username, email, password_hash) VALUES (?, ?, ?, 'x')",
                (user_id, user_id, f"{user_id}@example.com"),
            )
    return db


def make_user(user_id):
    return User(id=user_id, username=user_id, email=f"{user_id}@example.com", password_hash="h", account_type=AccountType.USER)


class FakePort:
    make_user = staticmethod(make_user)

    def __init__(self):
        self.users = {"alice": make_user("alice")}
        self.generations = {}
        self.by_key = {}
        self.submits = []
        self.cancels = []
        self.media = {}
        self.fail_submit = None
        self.writes = []
        self.write_error = None
        self.write_gate = None
        self.writer = {"available": True, "vision": True, "name": "Writer", "code": None, "message": None}
        self.writer_by_model = {}
        self.reasoning = None
        self.phrasebook = {}

    def phrasebook_texts(self, user, path):
        return list(self.phrasebook.get(path, []))

    async def write_prompt(self, user, request):
        self.writes.append(copy.deepcopy(request))
        if self.write_gate is not None:
            await self.write_gate.wait()
        if self.write_error is not None:
            raise self.write_error
        media = request.get("media") or []
        subject = media[0]["value"] if media else "nothing"
        return {
            "text": f"written for {subject} in {request.get('preset_id')}",
            "reasoning": self.reasoning,
            "model_id": None,
            "llm_id": request.get("llm_id") or "llm-1",
        }

    def writer_status(self, user, llm_id=None):
        return dict(self.writer_by_model.get(llm_id, self.writer))

    def user(self, user_id):
        return self.users.get(user_id)

    async def submit(self, user, request):
        await asyncio.sleep(0)
        self.submits.append(copy.deepcopy(request))
        if self.fail_submit:
            raise ValueError(self.fail_submit)
        key = request.get("idempotency_key")
        if key in self.by_key:
            return {"generation_id": self.by_key[key], "status": "pending", "queue_position": None}
        generation_id = f"g{len(self.generations) + 1}"
        self.generations[generation_id] = {"status": "pending", "files": [], "request": request}
        self.by_key[key] = generation_id
        return {"generation_id": generation_id, "status": "pending", "queue_position": None}

    def state(self, user, generation_id):
        if generation_id not in self.generations:
            raise GenerationNotFoundException(generation_id)
        record = self.generations[generation_id]
        return {"status": record["status"], "message": record.get("message")}

    def files(self, user, generation_id):
        return self.generations[generation_id]["files"]

    async def cancel(self, user, generation_id):
        self.cancels.append(generation_id)
        if self.generations[generation_id]["status"] in ("pending", "running"):
            self.generations[generation_id]["status"] = "cancelled"
        return True

    def resolve(self, user, ref):
        found = self.media.get(asset_key(ref))
        if found is None:
            raise MediaRefError("not_found", "The referenced media is not available")
        return dict(found)

    def bind_director(self, request, role, media, segment_id=None, promote=False):
        return bind_director_media(request, role, media, segment_id=segment_id, promote=promote)

    def finish(self, generation_id, *files):
        self.generations[generation_id]["status"] = "completed"
        self.generations[generation_id]["files"] = [
            {"id": file_id, "file_type": file_type, "file_path": f"generations/{generation_id}/{file_id}.png", "index": index}
            for index, (file_id, file_type) in enumerate(files)
        ]

    def fail(self, generation_id, message="CUDA out of memory"):
        self.generations[generation_id]["status"] = "failed"
        self.generations[generation_id]["message"] = message


@pytest.fixture
def port():
    fake = FakePort()
    for item_id in ("p1", "p2", "p3"):
        fake.media[f"library:{item_id}"] = {"value": f"uploads/{item_id}.png", "media_type": "image", "original_filename": f"{item_id}.png", "origin": None}
    return fake


@pytest.fixture
def alice(port):
    return port.users["alice"]


class NoSessions:
    def get_by_id(self, session_id):
        return None

    def get(self, session_id, version):
        return None


def make_components(
    port,
    *,
    catalog,
    contracts,
    coordinator: Optional[Coordinator] = None,
    resolver=None,
    repository: Optional[ElixirRepository] = None,
    limits: Callable[[], Limits] = Limits,
    sessions=None,
) -> ElixirsComponents:
    repository = repository or ElixirRepository()
    coordinator = coordinator or Coordinator(store=RunStore(), port=port, limits_provider=limits, boot_id="boot-1")
    store = sessions or NoSessions()
    reader = SessionReader(store, store)
    return ElixirsComponents(
        repository=repository,
        coordinator=coordinator,
        sessions=reader,
        runtime=ElixirRuntime(coordinator, SessionFollows(repository, reader.get_session, make_user)),
        resolver=resolver or (lambda user, ref: port.resolve(user, ref)),
        catalog=lambda ids: catalog,
        contracts=lambda: contracts,
        limits=limits,
    )


def make_client(components: ElixirsComponents, current_user: Callable[[], User]) -> TestClient:
    app = FastAPI()
    app.include_router(create_router(components))
    app.dependency_overrides[get_current_active_user] = current_user
    return TestClient(app, raise_server_exceptions=False)
