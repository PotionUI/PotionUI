import asyncio
import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

import numpy as np
from PIL import Image

from src.features.edits.manager import EditManager
from src.features.edits.repository import EditRepository
from src.features.editor_tasks.dto import StartEditorRunRequest
from src.features.editor_tasks.repository import EditorRunRepository
from src.features.editor_tasks.runs import EditorRunManager
from src.features.media.upload_repository import UploadRepository
from src.platform.database import get_cursor
from tests.features.editor_tasks.support import CROP, MASK
from tests.features.editor_tasks.test_manager import build

USER = SimpleNamespace(id="u1")
OTHER = SimpleNamespace(id="u2")
STATE = {"v": 1, "stack": {"steps": [], "cursor": 0}}


def make_users():
    with get_cursor() as cursor:
        for user_id in ("u1", "u2"):
            cursor.execute(
                "INSERT INTO users (id, username, email, password_hash) VALUES (?, ?, ?, 'x')",
                (user_id, user_id, f"{user_id}@example.com"),
            )


def picture(height=400, width=500):
    rng = np.random.default_rng(1)
    return Image.fromarray(rng.integers(70, 180, (height, width, 3), dtype=np.uint8))


def mask_image(height=400, width=500, box=(150, 250, 200, 330)):
    array = np.zeros((height, width), dtype=np.uint8)
    top, bottom, left, right = box
    array[top:bottom, left:right] = 255
    return Image.fromarray(array)


class Fakes:
    def __init__(self, tmp_path: Path):
        self.root = tmp_path / "files"
        self.root.mkdir()
        self.files: Dict[str, Path] = {}
        self.uploads: List[Dict[str, Any]] = []
        self.requests: List[Any] = []
        self.origins: Dict[str, Dict[str, Any]] = {}
        self.messages: List[Dict[str, Any]] = []
        self.cancelled: List[str] = []
        self.generation = SimpleNamespace(status="running", progress=0.4, error_user_message=None)
        self.generation_files: List[Any] = []
        self.submit_error: Exception = None
        self.deleted: List[str] = []
        self.cancel_result: Any = None
        self.cancel_error: Exception = None
        self.tag_result = True
        self.gate = None
        self.patch_waiting = asyncio.Event()
        self.gone = False
        self.by_hash = {}
        self.generation_ids = iter(f"g{n}" for n in range(1, 50))
        picture().save(self.root / "picture.png")
        mask_image().save(self.root / "region.png")
        self.files["picture"] = self.root / "picture.png"
        self.files["region"] = self.root / "region.png"

    async def add_existing(self, data, purpose="derived_artifact"):
        return await self.upload_media(data, "tab.png", "image/png", "u1", purpose)

    def add_result(self, file_id="f1", size=(512, 512)):
        path = self.root / f"{file_id}.png"
        Image.fromarray(np.full((size[1], size[0], 3), 120, dtype=np.uint8)).save(path)
        self.files[file_id] = path
        self.generation_files.append(SimpleNamespace(id=file_id))

    @staticmethod
    def key(ref):
        if isinstance(ref, dict):
            return ref.get("item_id") or ref.get("generation_file_id")
        return ref.item_id or ref.generation_file_id

    @contextmanager
    def render_picture(self, user, ref, steps):
        yield self.files["picture"]

    @contextmanager
    def open_input(self, user, ref):
        yield self.files[self.key(ref)]

    async def upload_media(self, file_data, filename, content_type, user_id, purpose, internal=False):
        if purpose == "editor_run_working" and not internal:
            raise ValueError("purpose")
        if filename == "editor-patch.png" and self.gate is not None:
            self.patch_waiting.set()
            await self.gate.wait()
        import hashlib

        digest = (hashlib.sha256(file_data).hexdigest(), purpose)
        if digest in self.by_hash:
            return self.by_hash[digest]
        upload_id = f"up{len(self.uploads) + 1}"
        path = self.root / f"{upload_id}.png"
        path.write_bytes(file_data)
        self.files[upload_id] = path
        self.uploads.append({"id": upload_id, "filename": filename, "purpose": purpose, "user_id": user_id})
        result = SimpleNamespace(
            id=upload_id, filename=f"{upload_id}.png", relative_path=f"uploads/{upload_id}.png",
            url=f"/api/media/uploads/{upload_id}.png",
        )
        self.by_hash[digest] = result
        return result

    def delete_upload(self, filename, user_id):
        self.deleted.append(filename)

    def live(self):
        return {f"{item['id']}.png": item["filename"] for item in self.uploads if f"{item['id']}.png" not in self.deleted}

    def live_kinds(self):
        return sorted(kind for name, kind in self.live().items())

    async def submit(self, request, user):
        if self.submit_error is not None:
            raise self.submit_error
        self.requests.append(request)
        return {"generation_id": next(self.generation_ids)}

    async def cancel(self, generation_id, user):
        self.cancelled.append(generation_id)
        if self.cancel_error is not None:
            raise self.cancel_error
        return self.cancel_result

    def get_by_id(self, generation_id):
        return None if self.gone else self.generation

    def get_files(self, generation_id, is_final=None, file_type=None):
        return list(self.generation_files)

    def update_origin(self, generation_id, origin):
        self.origins[generation_id] = origin
        return self.tag_result

    def get_by_name(self, generation_id, name):
        return [SimpleNamespace(parameter_index=index, parameter_value=json.dumps(100 + index)) for index, _ in enumerate(self.generation_files)]

    async def broadcast_to_generation(self, generation_id, message):
        self.messages.append(message)


def build_manager(tmp_path, presets=None, visible=None, hub=None):
    fakes = Fakes(tmp_path)
    tasks = build(tmp_path, presets or {"A-CROP": CROP, "B-MASK": MASK}, visible=visible or (lambda user: {"A-CROP", "B-MASK"}))
    manager = EditorRunManager(
        EditorRunRepository(),
        tasks,
        EditManager(EditRepository(), UploadRepository()),
        fakes,
        fakes,
        fakes.submit,
        fakes.cancel,
        SimpleNamespace(get_by_id=fakes.get_by_id, get_files=fakes.get_files, update_origin=fakes.update_origin),
        SimpleNamespace(get_by_name=fakes.get_by_name),
        hub or fakes,
    )
    return manager, fakes


def make_edit(user_id="u1"):
    return EditManager(EditRepository(), UploadRepository()).create(
        user_id, "image", None, "library", "up_source", STATE
    )


def body(edit_id, preset="A-CROP", mode="edit", region=True, **extra):
    data = {
        "edit": {"id": edit_id, "step_id": "s7"},
        "input": {"item_id": "picture"},
        "region": {"item_id": "region"} if region else None,
        "prompt": "a red wool scarf",
        "setup": {"preset_id": preset, "mode": mode, "form_data": {"quantity": 3, "steps": 20}},
    }
    data.update(extra)
    return StartEditorRunRequest.model_validate(data)


async def until_terminal(manager, user, run_id, limit=300):
    for _ in range(limit):
        run = await manager.get(user, run_id)
        if run["status"] in ("done", "failed", "cancelled"):
            return run
        await asyncio.sleep(0.01)
    raise AssertionError("the run never finished")


def restarted(manager):
    import copy

    fresh = copy.copy(manager)
    fresh._live = {}
    fresh._finishing = set()
    fresh._loop = None
    return fresh
