import base64
import io
import json
from types import SimpleNamespace

import pytest
from PIL import Image

from src.features.elixirs.repository import ElixirRepository
from src.features.llm.tools.base import ToolContext
from src.features.llm.tools.builtin import register_builtin_tools
from src.features.llm.tools.builtin.elixir_tools import (
    ChooseOutputsTool,
    EditElixirTool,
    GetChoiceOutputsTool,
    GetElixirTool,
    GetStepSettingsTool,
)
from src.features.llm.tools.registry import ToolRegistry
from tests.features.elixirs.assistant_world import (
    build_world,
    create,
    photos_into_sharpen,
    shoot_and_sharpen,
    start_review_brew,
)
from tests.features.elixirs.conftest import catalog, contracts, port, scratch_db

__all__ = ["catalog", "contracts", "port", "scratch_db"]


class Configs:
    def __init__(self, vision):
        self.vision = vision

    def get_configuration(self, llm_id):
        return SimpleNamespace(id=llm_id, supports_vision=self.vision)


@pytest.fixture
def storage(tmp_path):
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    for name, colour in (("p1", (255, 0, 0)), ("p2", (0, 0, 255))):
        Image.new("RGB", (64, 48), colour).save(uploads / f"{name}.png")
    return tmp_path


@pytest.fixture
def world(scratch_db, port, catalog, contracts, storage):
    return build_world(port, catalog, contracts, storage_root=str(storage))


def context(world, elixir_id, vision=False):
    return ToolContext(
        user_id="alice",
        mode_id="elixirs",
        session_metadata={"elixir": {"elixir_id": elixir_id}} if elixir_id else {},
        elixirs=world.assistant,
        llm_repository=Configs(vision),
        llm_id="llm-1",
    )


def run(coroutine):
    import asyncio

    return asyncio.run(coroutine)


def test_the_elixir_tools_belong_to_the_elixirs_mode_only():
    registry = ToolRegistry()
    register_builtin_tools(registry)

    tools = {tool.name: tool for tool in registry.get_all()}

    for name in ("get_elixir", "get_step_settings", "get_choice_outputs", "edit_elixir", "choose_outputs"):
        assert tools[name].modes == ["elixirs"]
    assert tools["edit_elixir"].requires_approval and tools["choose_outputs"].requires_approval
    assert all(tools[name].read_only for name in ("get_elixir", "get_step_settings", "get_choice_outputs"))
    assert "elixirs" in tools["get_preset_info"].modes and "elixirs" in tools["get_phrasebook_values"].modes


def test_without_an_open_elixir_the_tools_say_what_to_do(world):
    result = run(GetElixirTool().execute(context(world, None)))

    assert not result.success
    assert "open the elixir" in result.error


def test_get_elixir_and_step_settings_read_the_bound_elixir(world):
    record = create(shoot_and_sharpen())

    summary = json.loads(run(GetElixirTool().execute(context(world, record.id))).data)
    settings = json.loads(run(GetStepSettingsTool().execute(context(world, record.id), step="shoot")).data)

    assert [step["id"] for step in summary["steps"]] == ["shoot", "sharpen"]
    assert settings["prompt"] == "a bottle"


def test_edit_elixir_previews_and_only_saves_after_approval(world):
    record = create(shoot_and_sharpen())
    args = {"revision": record.revision, "operations": [{"op": "set_settings", "step": "shoot", "fields": {"seed": 42}}]}

    preview = run(EditElixirTool().execute(context(world, record.id), **args))

    assert preview.success
    assert json.loads(preview.data)["status"] == "pending_approval"
    assert preview.preview.action == "Edit elixir"
    assert preview.preview.items == ["shoot: seed 7 → 42"]
    assert ElixirRepository().get(record.id, "alice").revision == record.revision
    assert world.notifier.sent == []

    applied = run(EditElixirTool().execute_confirmed(context(world, record.id), **args))

    assert applied.success
    assert json.loads(applied.data)["revision"] == record.revision + 1
    assert ElixirRepository().get(record.id, "alice").definition["nodes"][0]["overrides"]["formData"] == {"seed": 42}
    assert world.notifier.sent[-1][1]["type"] == "elixir_changed"


def test_an_approval_after_the_elixir_changed_is_refused(world):
    record = create(shoot_and_sharpen())
    args = {"revision": record.revision, "operations": [{"op": "set_settings", "step": "shoot", "fields": {"seed": 42}}]}
    run(EditElixirTool().execute(context(world, record.id), **args))
    ElixirRepository().update(record.id, "alice", record.revision, name="Renamed")

    applied = run(EditElixirTool().execute_confirmed(context(world, record.id), **args))

    assert not applied.success
    assert "Call get_elixir again" in applied.error


def test_edit_elixir_reads_operations_sent_as_text(world):
    record = create(shoot_and_sharpen())
    operations = json.dumps([{"op": "set_settings", "step": "shoot", "fields": {"seed": 3}}])

    preview = run(EditElixirTool().execute(context(world, record.id), revision=record.revision, operations=operations))

    assert preview.success


def test_the_outputs_are_shown_as_one_numbered_image_when_the_model_can_see(world):
    record = create(photos_into_sharpen(), run_mode="review")
    start_review_brew(world, record)

    result = run(GetChoiceOutputsTool().execute(context(world, record.id, vision=True)))

    payload = json.loads(result.data)
    assert [(row["number"], row["id"], row["picked"]) for row in payload["outputs"]] == [(1, "library:p1", True), (2, "library:p2", True)]
    assert "note" not in payload
    sheet = Image.open(io.BytesIO(base64.b64decode(result.image_data)))
    assert sheet.size == (640, 320)


def test_without_vision_only_the_details_are_listed(world):
    record = create(photos_into_sharpen(), run_mode="review")
    start_review_brew(world, record)

    result = run(GetChoiceOutputsTool().execute(context(world, record.id, vision=False)))

    assert result.image_data is None
    assert "Ask the user which outputs to keep" in json.loads(result.data)["note"]


def test_choose_outputs_waits_for_approval_and_never_continues(world):
    record = create(photos_into_sharpen(), run_mode="review")
    run_record = start_review_brew(world, record)

    preview = run(ChooseOutputsTool().execute(context(world, record.id), outputs=["library:p1"]))
    assert preview.success and preview.preview.items == ["#1 p1.png"]
    assert world.coordinator.store.get_run(run_record["id"], "alice")["checkpoints"]["e1"]["draft"] == ["library:p1", "library:p2"]

    applied = run(ChooseOutputsTool().execute_confirmed(context(world, record.id), outputs=["library:p1"]))

    assert applied.success
    assert "press Continue" in json.loads(applied.data)["summary"]
    checkpoint = world.coordinator.store.get_run(run_record["id"], "alice")["checkpoints"]["e1"]
    assert checkpoint["draft"] == ["library:p1"] and checkpoint["confirmed"] is None
    assert world.port.submits == []


def test_choose_outputs_at_a_step_that_is_not_waiting_is_refused(world):
    record = create(photos_into_sharpen(), run_mode="review")
    start_review_brew(world, record)

    result = run(ChooseOutputsTool().execute(context(world, record.id), step="sharpen", outputs=[1]))

    assert not result.success
    assert "is not waiting for a choice" in result.error
