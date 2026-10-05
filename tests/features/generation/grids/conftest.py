import random
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from src.features.generation.dto import GenerationRequest
from src.features.generation.grids.dto import Axis, AxisValue
from src.features.generation.grids.repository import GridRepository
from src.features.generation.grids.service import GridService
from src.features.generation.records import Generation
from src.features.generation.repository import GenerationRepository
from src.features.generation.status_tracker import GenerationStatusTracker
from src.features.presets.templates import FieldTemplate, ModeTemplate, PipeTemplate
from src.platform.security.user import AccountType, User


def axis(field: str, values: List[Any], type: str = "select", labels: List[str] = None) -> Axis:
    return Axis(
        field=field,
        type=type,
        label=field,
        values=[
            AxisValue(value=value, label=(labels[index] if labels else str(value)))
            for index, value in enumerate(values)
        ],
    )


def field_index(**types) -> Dict[str, FieldTemplate]:
    return {name: FieldTemplate(type=kind, name=name) for name, kind in types.items()}


def preset(quantity_template: str = "{{ form.quantity }}", **attrs):
    pipes = [PipeTemplate(name="seed_generator", configuration={"seed": "{{ form.seed }}", "quantity": quantity_template})]
    return SimpleNamespace(modes={"txt2img": ModeTemplate(forms=[], pipes=pipes)}, **attrs)


def base_request(**overrides) -> GenerationRequest:
    fields = dict(
        preset_id="p1",
        prompt="a cat at dusk",
        form_data={"sampler": "euler", "steps": 20, "seed": 111, "quantity": 4},
        tab_id="tab-1",
    )
    fields.update(overrides)
    return GenerationRequest(**fields)


def make_user(user_id: str, admin: bool = False) -> User:
    return User(
        username=user_id,
        email=f"{user_id}@example.test",
        password_hash="x",
        account_type=AccountType.ADMIN if admin else AccountType.USER,
        id=user_id,
    )


class FakeHistory:
    def __init__(self, generations: GenerationRepository):
        self.generations = generations
        self.deleted: List[str] = []
        self.query = SimpleNamespace(serialize_generations=self.serialize)

    def serialize(self, rows, include_tags, *, viewer_id):
        return [
            {
                "id": row.id,
                "files": [
                    {"id": f"file-{row.id}", "file_type": "IMAGE", "is_final": True},
                ]
                if row.status == "completed"
                else [],
            }
            for row in rows
        ]

    def bulk_delete(self, ids, user_id):
        for generation_id in ids:
            self.deleted.append(generation_id)
            self.generations.delete(generation_id)

    def admin_bulk_delete(self, ids, admin_id):
        self.bulk_delete(ids, admin_id)


class Harness:
    def __init__(self, db):
        self.db = db
        for user_id in ("u1", "u2", "admin"):
            with db.get_cursor() as cursor:
                cursor.execute(
                    "INSERT INTO users (id, username, email, password_hash, account_type) VALUES (?, ?, ?, 'x', ?)",
                    (user_id, user_id, f"{user_id}@example.test", "ADMIN" if user_id == "admin" else "USER"),
                )
        self.users = {name: make_user(name, admin=name == "admin") for name in ("u1", "u2", "admin")}
        self.generations = GenerationRepository()
        self.grids = GridRepository()
        self.history = FakeHistory(self.generations)
        self.submitted: List[Dict[str, Any]] = []
        self.cancelled: List[str] = []
        self.fail_on: Dict[int, Exception] = {}
        self.guard = SimpleNamespace(calls=[], refusal=None)
        self.guard.check_batch = self._check_batch
        self.settings_store: Dict[str, Any] = {}
        self.index = field_index(sampler="select", steps="number", seed="seed", quantity="number")
        self.tracker = GenerationStatusTracker()
        self.preset_quantity = "{{ form.quantity }}"
        self.service = GridService(
            self.grids,
            self.generations,
            self.history,
            SimpleNamespace(
                load_preset_by_id=lambda preset_id: preset(self.preset_quantity, id=preset_id, engine="native")
            ),
            SimpleNamespace(get_setting=lambda key, default=None: self.settings_store.get(key, default)),
            self.submit,
            self.cancel,
            limit_guard=self.guard,
            is_admin=lambda user: user.account_type == AccountType.ADMIN,
            rng=random.Random(7),
        )

    def _check_batch(self, request, count):
        self.guard.calls.append((request, count))
        if self.guard.refusal is not None:
            raise self.guard.refusal

    async def submit(self, request, user, ref):
        number = len(self.submitted)
        if number in self.fail_on:
            raise self.fail_on[number]
        generation_id = f"gen-{number:03d}"
        self.generations.create(
            Generation(
                id=generation_id,
                preset_id=request.preset_id,
                form_data=request.form_data,
                user_id=user.id,
                status="pending",
                tab_id=request.tab_id,
                grid_id=ref.grid_id,
                grid_x=ref.x,
                grid_y=ref.y,
                axis_values=ref.axis_values,
            )
        )
        self.submitted.append({"id": generation_id, "request": request, "ref": ref, "user": user.id})
        return {"generation_id": generation_id}

    async def cancel(self, generation_id):
        self.cancelled.append(generation_id)
        self.set_status(generation_id, "cancelled")

    def set_status(self, generation_id, status, **extra):
        with self.db.get_cursor() as cursor:
            cursor.execute("UPDATE generations SET status = ? WHERE id = ?", (status, generation_id))
            for column, value in extra.items():
                cursor.execute(f"UPDATE generations SET {column} = ? WHERE id = ?", (value, generation_id))

    def rows(self, table="generations", where="1=1", params=()):
        with self.db.get_cursor() as cursor:
            cursor.execute(f"SELECT * FROM {table} WHERE {where}", params)
            return [dict(row) for row in cursor.fetchall()]

    def install(self, monkeypatch, index=None):
        monkeypatch.setattr(
            "src.features.generation.grids.service.form_field_index",
            lambda template, mode, form_name: index if index is not None else self.index,
        )


@pytest.fixture
def harness(mock_db, monkeypatch):
    built = Harness(mock_db)
    built.install(monkeypatch)
    return built
