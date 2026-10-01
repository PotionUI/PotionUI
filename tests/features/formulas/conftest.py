import importlib.util
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Optional
from unittest.mock import patch

import pytest

import src.features.formulas.repository as repository_module
from src.features.formulas.collaborators import FormulaCollaborators
from src.features.formulas.repository import FormulaRepository
from src.features.formulas.sources import DeclaredGroup, FormUnavailable, LoadedForm, ModelRefInfo
from src.platform.security.user import AccountType, User

MIGRATION = Path(__file__).resolve().parents[3] / "src" / "platform" / "database" / "migrations" / "049_create_formulas.py"

SESSIONS_DDL = (
    "CREATE TABLE sessions (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, preset_id TEXT NOT NULL, "
    "name TEXT NOT NULL, data TEXT NOT NULL)"
)


def build_form() -> LoadedForm:
    fields = {
        "prompt": {"type": "textbox", "name": "prompt", "title": "Prompt"},
        "seed": {"type": "seed", "name": "seed", "title": "Seed"},
        "image": {"type": "image", "name": "image", "title": "Image"},
        "speed_profile": {
            "type": "select", "name": "speed_profile", "title": "Speed profile",
            "options": [{"label": "Turbo", "value": "turbo"}, {"label": "Quality", "value": "quality"}],
        },
        "steps": {
            "type": "slider", "name": "steps", "title": "Steps", "audience": "advanced",
            "minimum": 2, "maximum": 100, "step": 1,
        },
        "sampler": {
            "type": "select", "name": "sampler", "title": "Sampler", "audience": "advanced",
            "options": [{"label": "Euler", "value": "euler"}, {"label": "Res", "value": "res_multistep"}],
        },
        "warmup": {"type": "checkbox", "name": "warmup", "title": "Warmup"},
        "note": {"type": "textbox", "name": "note", "title": "Note", "configuration": {"pattern": "^[a-z]+$"}},
        "resolution": {
            "type": "resolution", "name": "resolution", "title": "Resolution",
            "options": [{"value": "1024x1024"}, {"value": "832x480"}],
        },
        "tags": {
            "type": "checkbox_group", "name": "tags", "title": "Tags",
            "options": [{"value": "a"}, {"value": "b"}],
        },
        "checkpoint": {
            "type": "model", "name": "checkpoint", "title": "Checkpoint",
            "configuration": {"model_type": "checkpoint", "filter_tags": ["base-x"]},
        },
        "loras": {
            "type": "lora_picker", "name": "loras", "title": "LoRAs",
            "configuration": {
                "model_type": "lora", "max_items": 3, "strength_min": -2.0, "strength_max": 2.0,
                "row_fields": [{"name": "audio", "type": "checkbox"}],
            },
        },
        "loras_tagFilters": {"type": "hidden", "name": "loras_tagFilters"},
    }
    groups = [
        DeclaredGroup("speed", "Speed and sampling", ["speed_profile", "steps", "sampler"]),
        DeclaredGroup("size", "Size", ["resolution"]),
        DeclaredGroup("loras", "LoRAs", ["loras"]),
        DeclaredGroup("misc", "Misc", ["warmup", "note", "tags", "checkpoint"]),
    ]
    return LoadedForm(fields=fields, groups=groups, preset_version="1.2.0")


class FakeForms:
    def __init__(self):
        self.forms: Dict[tuple, LoadedForm] = {("preset-a", "video"): build_form()}
        self.requested = []

    def load(self, preset_id, mode, form_name):
        self.requested.append((preset_id, mode, form_name))
        form = self.forms.get((preset_id, mode))
        if form is None:
            raise FormUnavailable(f"no form for {preset_id}/{mode}")
        return form


class FakeModels:
    def __init__(self):
        self.models: Dict[str, ModelRefInfo] = {}
        self.hidden_from: Dict[str, set] = {}

    def add(self, model_id, model_type="lora", tag_ids=(), available=True):
        self.models[model_id] = ModelRefInfo(model_type, list(tag_ids), available)

    def inspect(self, model_id, user) -> Optional[ModelRefInfo]:
        if user.id in self.hidden_from.get(model_id, set()):
            return None
        return self.models.get(model_id)


@pytest.fixture
def connection():
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row

    class _Db:
        @contextmanager
        def get_cursor(self):
            cursor = conn.cursor()
            yield cursor
            conn.commit()

    spec = importlib.util.spec_from_file_location("formulas_migration", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.db = _Db()
    conn.execute("CREATE TABLE users (id TEXT PRIMARY KEY)")
    conn.execute(SESSIONS_DDL)
    for user_id in ("user-1", "user-2"):
        conn.execute("INSERT INTO users (id) VALUES (?)", (user_id,))
    module.up()

    @contextmanager
    def _get_conn():
        yield conn

    with patch.object(repository_module, "get_database_connection", _get_conn):
        yield conn
    conn.close()


@pytest.fixture
def forms():
    return FakeForms()


@pytest.fixture
def models():
    return FakeModels()


@pytest.fixture
def collaborators(connection, forms, models):
    return FormulaCollaborators(repository=FormulaRepository(), forms=forms, models=models)


def make_user(user_id="user-1", account_type=AccountType.USER) -> User:
    return User(username=user_id, email=f"{user_id}@example.test", password_hash="x", account_type=account_type, id=user_id)


@pytest.fixture
def user():
    return make_user()
