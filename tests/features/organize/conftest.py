import json
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional

import pytest

from src.features.organize.admin import OrganizeAdmin
from src.features.organize.backfill import BackfillManager
from src.features.organize.core_actions import register_core_actions
from src.features.organize.core_facts import register_core_facts
from src.features.organize.instance_ops import InstanceModelOps
from src.features.organize.item_repository import OrganizeItemRepository
from src.features.organize.manager import OrganizeCollaborators, OrganizeManager, instance_viewer
from src.features.organize.rule_repository import OrganizeRuleRepository
from src.features.organize.run_repository import OrganizeRunRepository
from src.features.organize.worker import OrganizeWorker
from src.features.organize.write_repository import OrganizeWriteRepository
from src.features.user_groups.repository import UserGroupRepository
from src.features.users.repository import UserRepository
from src.platform.plugins.organize import OrganizeActionDefinition, OrganizeChange, OrganizeRegistry
from src.platform.security.user import AccountType, User
from src.platform.util.ids import generate_ulid
from tests.fixtures.seed import seed_user


class InlineExecutor:
    def __init__(self):
        self.deferred = []
        self.defer = False

    def submit(self, fn, *args):
        if self.defer:
            self.deferred.append((fn, args))
            return None
        return fn(*args)

    def run_deferred(self):
        pending, self.deferred = self.deferred, []
        for fn, args in pending:
            fn(*args)

    def shutdown(self, wait=False):
        pass


class FakeVisibility:
    def __init__(self):
        self.restricted = set()
        self.hidden_generations = set()

    def is_restricted(self, user_id):
        return user_id in self.restricted

    def viewable_generation_ids(self, user_id, generation_ids):
        ids = set(generation_ids)
        if user_id not in self.restricted:
            return ids
        return ids - self.hidden_generations


class NoticeRecorder:
    def __init__(self):
        self.calls: List[Dict[str, Any]] = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return []

    def of_type(self, notice_type):
        return [c for c in self.calls if c.get("type") == notice_type]


class Seeder:
    def __init__(self, database):
        self.db = database

    def _exec(self, sql, params=()):
        with self.db.get_cursor() as cursor:
            cursor.execute(sql, params)

    def user(self, user_id, admin=False):
        seed_user(self.db, user_id, account_type="ADMIN" if admin else "USER")
        return User(username=user_id, email=f"{user_id}@example.test", password_hash="x",
                    account_type=AccountType.ADMIN if admin else AccountType.USER, id=user_id)

    def model(self, filename="krea.safetensors", model_type="checkpoint", sha=None, family=None, created_at=None):
        model_id = generate_ulid()
        sha = sha or generate_ulid()
        self._exec(
            "INSERT INTO models (id, filename, model_type, sha256, created_at) VALUES (?, ?, ?, ?, COALESCE(?, CURRENT_TIMESTAMP))",
            (model_id, filename, model_type, sha, created_at),
        )
        if family:
            self._exec(
                "INSERT INTO model_header_verdicts (sha256, format, status, family, registry_fingerprint, classified_at) "
                "VALUES (?, 'safetensors', 'decided', ?, 'x', CURRENT_TIMESTAMP)",
                (sha, family),
            )
        return model_id

    def assign(self, user_id, model_id):
        self._exec(
            "INSERT INTO user_models (id, user_id, model_id) VALUES (?, ?, ?)", (generate_ulid(), user_id, model_id)
        )

    def flag_nsfw(self, model_id):
        self._exec(
            "INSERT INTO providers (id, model_id, provider, nsfw) VALUES (?, ?, 'civitai', 1)", (generate_ulid(), model_id)
        )

    def generation(self, user_id, preset_id="krea-2", mode="txt2img", status="completed", prompt="a cat",
                   models=(), files=((1344, 768, "IMAGE", None),), form_extra=None, linked_models=(), prompt_state=None):
        generation_id = generate_ulid()
        form = {"prompt": prompt, "negative_prompt": "blurry dog"} if prompt_state is None else {"seed": 1}
        for index, model_id in enumerate(models):
            form[f"model_{index}"] = f"model:{model_id}"
        form.update(form_extra or {})
        self._exec(
            "INSERT INTO generations (id, preset_id, form_data, prompt_state, user_id, status, mode) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (generation_id, preset_id, json.dumps(form), json.dumps(prompt_state) if prompt_state is not None else None,
             user_id, status, mode),
        )
        for model_id in linked_models:
            self._exec(
                "INSERT INTO generation_models (id, generation_id, model_id) VALUES (?, ?, ?)",
                (generate_ulid(), generation_id, model_id),
            )
        for width, height, file_type, duration in files:
            file_id = generate_ulid()
            self._exec(
                "INSERT INTO files (id, file_path, file_type, user_id, is_final, width, height, duration_seconds) "
                "VALUES (?, ?, ?, ?, 1, ?, ?, ?)",
                (file_id, f"{file_id}.png", file_type, user_id, width, height, duration),
            )
            self._exec(
                "INSERT INTO generation_files (id, generation_id, file_id) VALUES (?, ?, ?)",
                (generate_ulid(), generation_id, file_id),
            )
        return generation_id

    def upload(self, user_id, media_type="image", width=1024, height=1024, filename="holiday.png",
               purpose="user_upload", duration=None):
        upload_id = generate_ulid()
        self._exec(
            "INSERT INTO uploads (id, user_id, filename, original_filename, media_type, width, height, purpose, duration_seconds) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (upload_id, user_id, f"{upload_id}.png", filename, media_type, width, height, purpose, duration),
        )
        return upload_id

    def collection(self, user_id, name="Landscapes", scope="history", parent_id=None):
        collection_id = generate_ulid()
        self._exec(
            "INSERT INTO collections (id, name, user_id, parent_id, scope) VALUES (?, ?, ?, ?, ?)",
            (collection_id, name, user_id, parent_id, scope),
        )
        return collection_id

    def model_collection(self, user_id, name="Checkpoints"):
        collection_id = generate_ulid()
        self._exec(
            "INSERT INTO model_collections (id, name, user_id) VALUES (?, ?, ?)", (collection_id, name, user_id)
        )
        return collection_id

    def tag_generation(self, user_id, generation_id, name):
        tag_id = generate_ulid()
        self._exec("INSERT INTO tags (id, name, type, user_id) VALUES (?, ?, 'GENERATION', ?)", (tag_id, name, user_id))
        self._exec("INSERT INTO generation_tags (generation_id, tag_id) VALUES (?, ?)", (generation_id, tag_id))
        return tag_id

    def tag_model(self, model_id, name):
        tag_id = generate_ulid()
        self._exec("INSERT INTO tags (id, name, type) VALUES (?, ?, 'MODEL')", (tag_id, name))
        self._exec("INSERT INTO model_tags (model_id, tag_id) VALUES (?, ?)", (model_id, tag_id))
        return tag_id

    def rows(self, sql, params=()):
        with self.db.get_cursor() as cursor:
            cursor.execute(sql, params)
            return [dict(r) for r in cursor.fetchall()]

    def members(self, collection_id):
        return {r["generation_id"] for r in self.rows(
            "SELECT generation_id FROM collection_generations WHERE collection_id = ?", (collection_id,)
        )}

    def generation_tags(self, generation_id):
        return sorted(r["name"] for r in self.rows(
            "SELECT t.name FROM generation_tags gt JOIN tags t ON t.id = gt.tag_id WHERE gt.generation_id = ?",
            (generation_id,),
        ))


@pytest.fixture
def seed(mock_db):
    return Seeder(mock_db)


@pytest.fixture
def registry():
    return OrganizeRegistry()


@pytest.fixture
def visibility():
    return FakeVisibility()


@pytest.fixture
def notices():
    return NoticeRecorder()


@pytest.fixture
def executor():
    return InlineExecutor()


@pytest.fixture
def presets():
    return [("krea-2", "Krea 2"), ("flux-dev", "Flux Dev")]


@pytest.fixture
def manager(mock_db, registry, visibility, notices, executor, presets):
    items = OrganizeItemRepository()
    writer = OrganizeWriteRepository()
    users = UserRepository()
    register_core_facts(registry, items, users, lambda: presets)
    register_core_actions(registry, writer)
    built = OrganizeManager(
        OrganizeCollaborators(
            rules=OrganizeRuleRepository(), runs=OrganizeRunRepository(), items=items, writer=writer,
            users=users, registry=registry, visibility=visibility, notify=notices,
        ),
        executor=executor,
    )
    yield built
    built._options_pool.shutdown(wait=False)


@pytest.fixture
def backfill(manager):
    return BackfillManager(manager)


@pytest.fixture
def admin(manager):
    return OrganizeAdmin(manager)


@pytest.fixture
def worker(manager):
    return OrganizeWorker(manager.handle_event, prune=manager.prune)


def collection_action(collection_id: Optional[str] = None, name: Optional[str] = None, create: bool = True,
                      parent_id: Optional[str] = None) -> Dict[str, Any]:
    config: Dict[str, Any] = {"create_if_missing": create}
    if collection_id:
        config["collection_id"] = collection_id
    if name:
        config["collection_name"] = name
    if parent_id:
        config["parent_id"] = parent_id
    return {"action": "add_to_collection", "config": config}


def rule_body(subject="generation", conditions=None, actions=None, name="Krea landscapes", match="all", **extra):
    body = {"name": name, "subject": subject, "match": match, "conditions": conditions or [], "actions": actions or []}
    body.update(extra)
    return body


class FakePlugins:
    def __init__(self):
        self.vetoed = set()
        self.fired: List[Any] = []

    def execute_hook(self, hook, initial_data=None):
        data = dict(initial_data or {})
        self.fired.append((hook, data))
        if str(hook).endswith("before_assign_resource") and data.get("resource_id") in self.vetoed:
            data["blocked"] = True
            data["block_reason"] = "refused"
        return SimpleNamespace(data=data), None


class FakeTypes:
    def __init__(self, seed):
        self.seed = seed
        self.set_calls: List[Any] = []
        self.reset_calls: List[str] = []
        self.pending = False
        self.error = None

    def set_admin_type(self, model_id, model_type, user_id):
        if self.error is not None:
            raise self.error
        if self.pending:
            return {"state": "queued"}
        self.set_calls.append((model_id, model_type, user_id))
        self.seed._exec("UPDATE models SET model_type = ? WHERE id = ?", (model_type, model_id))
        return None

    def reset_admin_type(self, model_id):
        self.reset_calls.append(model_id)


class Fetch:
    def __init__(self):
        self.calls: List[Any] = []
        self.behavior: Callable[[Any], List[OrganizeChange]] = lambda item: [
            OrganizeChange(target_type="plugin", target_id=item.item_id, target_name="CivitAI")
        ]

    def __call__(self, item, config, user_id):
        self.calls.append((item.item_id, user_id))
        return self.behavior(item)


@pytest.fixture
def world(mock_db, seed):
    registry = OrganizeRegistry()
    items = OrganizeItemRepository()
    writer = OrganizeWriteRepository()
    users = UserRepository()
    plugins = FakePlugins()
    types = FakeTypes(seed)
    ops = InstanceModelOps(UserGroupRepository(), plugins, types, items)
    register_core_facts(registry, items, users, lambda: [])
    register_core_actions(registry, writer, ops)
    fetch = Fetch()
    registry.register_action(OrganizeActionDefinition(
        key="test.fetch", label="Fetch data", subjects=("model",), apply=fetch, scopes=("instance",),
        needs=("digest",), paced=True, notes=("Exact match only",), done_label="Fetched data", source="test-plugin",
        description="Looks the model up",
    ))
    notices = NoticeRecorder()
    executor = InlineExecutor()
    manager = OrganizeManager(
        OrganizeCollaborators(
            rules=OrganizeRuleRepository(), runs=OrganizeRunRepository(), items=items, writer=writer, users=users,
            registry=registry, visibility=FakeVisibility(), notify=notices, ops=ops,
        ),
        executor=executor,
    )
    admin = seed.user("root", admin=True)
    known = {"u1": seed.user("u1")}
    ns = SimpleNamespace(user=known.get,
        seed=seed, registry=registry, manager=manager, backfill=BackfillManager(manager), ops=ops, plugins=plugins,
        types=types, fetch=fetch, notices=notices, executor=executor, admin_user=admin, viewer=instance_viewer(admin),
        admin_ops=OrganizeAdmin(manager),
    )
    yield ns
    manager._options_pool.shutdown(wait=False)
