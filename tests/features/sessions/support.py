import sqlite3
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from unittest.mock import patch

import src.features.sessions.repository as session_repository_module
from src.features.models.exceptions import ModelAccessDeniedException, ModelNotFoundException
from src.features.presets.templates import FieldTemplate, FormTemplate, ModeTemplate, PresetTemplate
from src.features.sessions.dto import Session
from src.features.sessions.generate import SessionRequests
from src.features.sessions.repository import SessionRepository
from src.platform.security.user import AccountType

SCHEMA = """
CREATE TABLE users (id TEXT PRIMARY KEY);
CREATE TABLE sessions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    preset_id TEXT NOT NULL,
    name TEXT NOT NULL,
    data TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    pinned INTEGER NOT NULL DEFAULT 0,
    pinned_at TIMESTAMP,
    version INTEGER NOT NULL DEFAULT 1,
    last_origin TEXT,
    UNIQUE(user_id, preset_id, name)
);
"""

PRESET = "flux-test"
OTHER_PRESET = "wan-test"
FIXED = "kodachrome film still"


@contextmanager
def memory_sessions():
    connection = sqlite3.connect(":memory:", check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.executescript(SCHEMA)
    connection.commit()

    @contextmanager
    def get_connection():
        yield connection

    with patch.object(session_repository_module, "get_database_connection", get_connection):
        yield SessionRepository(), connection
    connection.close()


def field(name: str, kind: str, **fields: Any) -> FieldTemplate:
    configuration = fields.pop("configuration", None)
    return FieldTemplate(type=kind, name=name, label=name.title(), configuration=configuration, **fields)


def default_fields() -> List[FieldTemplate]:
    return [
        field("seed", "seed", default=-1),
        field("quantity", "stepper", default=1, configuration={"min": 1, "max": 8}),
        field("steps", "slider", default=20, configuration={"min": 1, "max": 60}),
        field("resolution", "select", default="1024x1024", configuration={"options": [{"value": "1024x1024"}, {"value": "1216x832"}]}),
        field("checkpoint", "model", default=None),
        field("image", "image", default=None),
        field("note", "alert"),
    ]


def template(
    preset_id: str = PRESET, name: str = "Flux Test", fields: Optional[List[FieldTemplate]] = None,
    modes=("txt2img",), vars: Optional[Dict[str, Any]] = None, engine: str = "native",
) -> PresetTemplate:
    form = FormTemplate(name="custom", fields=fields if fields is not None else default_fields())
    return PresetTemplate(
        id=preset_id, name=name, version="1", path="/x",
        modes={mode: ModeTemplate(forms=[form], pipes=[]) for mode in modes},
        vars=vars if vars is not None else {}, engine=engine,
    )


class Loader:
    def __init__(self, *templates: PresetTemplate):
        self.templates = {item.id: item for item in templates}

    def load_preset_by_id(self, preset_id: str):
        return self.templates.get(preset_id)


class PresetDb:
    def __init__(self, available: Dict[str, List[str]], overrides: Optional[Dict[str, Any]] = None):
        self.available = available
        self.overrides = overrides or {}

    def get_available_preset_ids_for_user(self, user_id: str):
        return self.available.get(user_id, [])

    def get_preset_form_overrides(self, preset_id: str):
        return self.overrides.get(preset_id, {})


class Users:
    def __init__(self, **accounts: AccountType):
        self.accounts = accounts

    def get_by_id(self, user_id: str):
        kind = self.accounts.get(user_id)
        return SimpleNamespace(id=user_id, account_type=kind) if kind else None


class Models:
    def __init__(self, known: Dict[str, str], allowed: Dict[str, List[str]]):
        self.known = known
        self.allowed = allowed
        self.model_repo = SimpleNamespace(get_by_id=lambda model_id, include_providers=False: SimpleNamespace(name=self.known.get(model_id)))

    def verify_model_access(self, model_id: str, user: Any):
        if model_id not in self.known:
            raise ModelNotFoundException(model_id)
        if user.account_type != AccountType.ADMIN and model_id not in self.allowed.get(user.id, []):
            raise ModelAccessDeniedException(model_id)


class Versions:
    def __init__(self, history: Optional[List[SimpleNamespace]] = None):
        self.history = history or []

    def list_for_session(self, session_id: str):
        return [SimpleNamespace(version_number=item.version_number) for item in self.history]

    def get(self, session_id: str, number: int):
        return next((item for item in self.history if item.version_number == number), None)


class Settings:
    def __init__(self, storage: str):
        self.storage = storage

    def get_file_storage_directory(self, user_id: str):
        return self.storage


def segment(identifier: str, content: str, **fields: Any) -> Dict[str, Any]:
    return {"id": identifier, "type": "content", "content": content, "chips": {}, "enabled": True, **fields}


def entry(**fields: Any) -> Dict[str, Any]:
    base = {
        "prompt": "a lighthouse", "negativePrompt": "blur",
        "promptSegments": [segment("a", "a lighthouse")], "negativePromptSegments": [segment("n", "blur")],
        "formData": {"seed": 5, "quantity": 2, "steps": 30}, "leftPanelWidth": 380,
    }
    return {**base, **fields}


def build_service(
    sessions: SessionRepository, storage: str, *templates: PresetTemplate, available: Optional[Dict[str, List[str]]] = None,
    overrides: Optional[Dict[str, Any]] = None, history: Optional[List[SimpleNamespace]] = None, batch: bool = True,
    models: Optional[Models] = None, users: Optional[Users] = None,
) -> SessionRequests:
    loaded = templates or (template(),)
    return SessionRequests(
        sessions=sessions,
        versions=Versions(history),
        preset_loader=Loader(*loaded),
        preset_db=PresetDb(available if available is not None else {"u1": [item.id for item in loaded], "u2": [item.id for item in loaded]}, overrides),
        users=users or Users(u1=AccountType.USER, u2=AccountType.USER, root=AccountType.ADMIN),
        models=models or Models({"m1": "Model One", "m2": "Model Two"}, {"u1": ["m1"], "u2": ["m1"]}),
        settings=Settings(storage),
        prompt_batch=lambda engine: batch,
    )


def save(sessions: SessionRepository, user_id: str = "u1", preset_id: str = PRESET, name: str = "Portrait", data=None, session_id: str = "s1") -> Session:
    return sessions.create(
        Session(id=session_id, user_id=user_id, preset_id=preset_id, name=name, data=data if data is not None else {"txt2img": entry()})
    )
