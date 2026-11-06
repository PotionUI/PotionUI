import asyncio
from types import SimpleNamespace

from src.features.elixirs.assistant import ElixirAssistant, SessionWriter
from src.features.elixirs.coordinator import Coordinator
from src.features.elixirs.limits import Limits
from src.features.elixirs.repository import ElixirRepository
from src.features.elixirs.run_repository import RunStore
from src.features.sessions.dto import Session
from src.features.sessions.hooks import SESSION_HOOKS
from src.features.sessions.repository import SessionRepository
from tests.features.elixirs.conftest import make_components
from tests.fixtures.fake_tab_drafts import RecordingNotifier

KREA = "4TK1KBQZ2XMB8ME0PTMXS1YJQP"
SHOOT = {"selectedVariant": "custom", "prompt": "a bottle", "formData": {"quantity": 1, "seed": 7}}


class Hooks:
    def __init__(self):
        self.runtime = None

    def execute_hook(self, hook, initial_data=None):
        data = dict(initial_data or {})
        if hook == SESSION_HOOKS.after_update and self.runtime is not None:
            self.runtime.on_session_updated(SimpleNamespace(data=data))
        return SimpleNamespace(data=data), None


class NoVersions:
    def get(self, session_id, version):
        return None


def build_world(port, catalog, contracts, storage_root=None):
    sessions = SessionRepository()
    coordinator = Coordinator(store=RunStore(), port=port, limits_provider=Limits, boot_id="boot-1", random_below=lambda limit: 3)
    components = make_components(port, catalog=catalog, contracts=contracts, coordinator=coordinator, sessions=sessions)
    hooks = Hooks()
    hooks.runtime = components.runtime
    notifier = RecordingNotifier()
    assistant = ElixirAssistant(
        components,
        session_writer=SessionWriter(sessions, hooks, None, None, notifier=notifier),
        notifier=notifier,
        storage_root=(lambda: storage_root) if storage_root else None,
    )
    return SimpleNamespace(
        assistant=assistant,
        components=components,
        coordinator=coordinator,
        sessions=sessions,
        notifier=notifier,
        port=port,
        pump=lambda: asyncio.run(coordinator.pump()),
    )


def save_session(world, session_id="s1", name="Bottle shoot", data=None):
    return world.sessions.create(Session(
        id=session_id, user_id="alice", preset_id=KREA, name=name,
        data=data if data is not None else {"txt2img": {**SHOOT, "leftPanelWidth": 300}},
    ))


def shoot_and_sharpen(**shoot):
    return {
        "schema_version": 1,
        "nodes": [
            {"id": "shoot", "kind": "preset", "title": "Shoot", "preset_id": KREA, "mode": "txt2img",
             "session_snapshot": dict(SHOOT), "session_mode": "copy", "session_source": {"id": "s0", "name": "Old shoot"}, **shoot},
            {"id": "sharpen", "kind": "preset", "title": "Sharpen", "preset_id": KREA, "mode": "enhance",
             "session_snapshot": {"prompt": "crisp", "formData": {"quantity": 1}}, "session_mode": "new", "overrides": {"prompt": "crisp"}},
        ],
        "edges": [{"id": "e1", "source": "shoot", "source_handle": "images", "target": "sharpen", "target_handle": "source_image", "iteration": "each"}],
    }


def create(definition, run_mode="automatic", name="Campaign"):
    return ElixirRepository().create("alice", name, run_mode, definition)


def library(item_id):
    return {"kind": "library", "item_id": item_id, "media_type": "image", "original_filename": f"{item_id}.png"}


def photos_into_sharpen(assets=("p1", "p2")):
    return {
        "schema_version": 1,
        "nodes": [
            {"id": "photos", "kind": "media", "title": "Photos", "media_type": "image", "assets": [library(item) for item in assets]},
            {"id": "sharpen", "kind": "preset", "title": "Sharpen", "preset_id": KREA, "mode": "enhance",
             "session_source": {"id": "s1"}, "session_snapshot": {"prompt": "crisp", "formData": {"quantity": 1}}},
        ],
        "edges": [{"id": "e1", "source": "photos", "source_handle": "images", "target": "sharpen", "target_handle": "source_image", "iteration": "each"}],
    }


def start_review_brew(world, record):
    templates = {"sharpen": {"preset_id": KREA, "mode": "enhance", "prompts": [{"positive": "crisp", "negative": ""}], "form_data": {"seed": 5, "quantity": 1}}}
    return world.coordinator.start_graph_run(
        world.port.users["alice"],
        {"id": record.id, "revision": record.revision, "definition": record.definition},
        world.components.contracts(),
        templates,
        "review",
        "brew-key-1",
        catalog=world.components.catalog([KREA]),
        sessions={},
    )
