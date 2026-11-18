from contextlib import contextmanager
from types import SimpleNamespace

from src.features.edits.manager import EditManager
from src.features.edits.repository import EditRepository
from src.features.editor_tasks.dto import StartEditorRunRequest
from src.features.editor_tasks.repository import EditorRunRepository
from src.features.editor_tasks.runs import EditorRunManager
from src.features.media.upload_repository import UploadRepository
from tests.features.editor_tasks.clip_support import lossless_clip
from tests.features.editor_tasks.run_support import Fakes
from src.features.editor_tasks.manager import EditorTaskManager
from src.features.editor_tasks.registry import TaskRegistry, register_core_tasks
from tests.features.editor_tasks.support import BASE_FIELDS, CROP, load, write_preset

CLIP_FIELD = {"name": "clip", "type": "video", "label": "Clip"}
PRESET = "V-RANGE"


def build_tasks(tmp_path, task, **changes):
    root = tmp_path / "presets"
    root.mkdir()
    write_preset(root, range_mode(task, **changes), fields=[CLIP_FIELD, *BASE_FIELDS], preset_id=PRESET)
    write_preset(root, CROP, preset_id="A-CROP")
    loader = load(root)
    return EditorTaskManager(
        register_core_tasks(TaskRegistry()), lambda: loader.all(), lambda user: {PRESET, "A-CROP"}, lambda engine: True
    )


def range_mode(task="upscale", **changes):
    return {
        "schema_version": 1,
        "task": task,
        "from": "edit",
        "region": "range",
        "inputs": {"source": "clip"},
        **changes,
    }


class ClipFakes(Fakes):
    def __init__(self, tmp_path, frames=20):
        super().__init__(tmp_path)
        self.files["clip"] = lossless_clip(self.root / "clip.mp4", frames=frames, fps=10)

    @contextmanager
    def render_clip(self, user, ref, steps):
        yield self.files["clip"]

    async def upload_media(self, file_data, filename, content_type, user_id, purpose, internal=False):
        stored = await super().upload_media(file_data, filename, content_type, user_id, purpose, internal)
        path = self.files[stored.id]
        suffix = "." + filename.rsplit(".", 1)[-1]
        if path.suffix != suffix:
            self.files[stored.id] = path.rename(path.with_suffix(suffix))
        return stored

    def add_clip_result(self, file_id, path):
        self.files[file_id] = path
        self.generation_files.append(SimpleNamespace(id=file_id))

    def upload_bytes(self, filename):
        entry = next(item for item in reversed(self.uploads) if item["filename"] == filename)
        return self.files[entry["id"]]

    def uploads_named(self, filename):
        return [item for item in self.uploads if item["filename"] == filename]


def build_clip_manager(tmp_path, task="upscale", frames=20, **changes):
    fakes = ClipFakes(tmp_path, frames)
    tasks = build_tasks(tmp_path, task, **changes)
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
        fakes,
    )
    return manager, fakes


def clip_body(edit_id, span=None, preset=PRESET, mode="editor:edit", **extra):
    data = {
        "edit": {"id": edit_id, "step_id": "s9"},
        "input": {"item_id": "clip"},
        "setup": {"preset_id": preset, "mode": mode, "form_data": {"quantity": 2}},
    }
    if span is not None:
        data["range"] = {"start_s": span[0], "end_s": span[1]}
    data.update(extra)
    return StartEditorRunRequest.model_validate(data)
