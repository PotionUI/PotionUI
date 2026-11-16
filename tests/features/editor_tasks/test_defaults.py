import dataclasses
from types import SimpleNamespace

import pytest

from src.features.editor_tasks.declarations import DECLARATION_FILE, DeclarationCache
from src.features.editor_tasks.default_repository import TaskDefaultRepository
from src.features.editor_tasks.dto import SetTaskDefaultRequest
from src.features.editor_tasks.manager import EditorTaskManager
from src.features.editor_tasks.registry import TaskRegistry, register_core_tasks
from src.features.formulas.records import Formula
from src.features.formulas.repository import FormulaRepository
from src.features.sessions.dto import Session
from src.features.sessions.repository import SessionRepository
from src.platform.database import get_cursor
from src.platform.http.errors import DomainError, NotFoundError
from tests.features.editor_tasks.support import CROP, MASK, load, write_preset

USER = SimpleNamespace(id="u1")
OTHER = SimpleNamespace(id="u2")


class World:
    def __init__(self, tmp_path):
        root = tmp_path / "presets"
        root.mkdir()
        self.dirs = {pid: write_preset(root, editor, preset_id=pid) for pid, editor in {"A-CROP": CROP, "B-MASK": MASK}.items()}
        self.templates = list(load(root).all())
        self.visible = {"u1": {"A-CROP", "B-MASK"}, "u2": {"B-MASK"}}
        self.engine_ok = True
        self.calls = {"defaults": 0, "sessions": 0, "formulas": 0}
        self.defaults = TaskDefaultRepository()
        self.sessions = SessionRepository()
        self.formulas = FormulaRepository()
        self.manager = EditorTaskManager(
            register_core_tasks(TaskRegistry()),
            lambda: list(self.templates),
            DeclarationCache(lambda: list(self.templates), dict),
            lambda user: set(self.visible[user.id]),
            lambda engine: self.engine_ok,
            self,
            self._sessions,
            self._formulas,
        )
        with get_cursor() as cursor:
            for user_id in ("u1", "u2"):
                cursor.execute(
                    "INSERT INTO users (id, username, email, password_hash) VALUES (?, ?, ?, 'x')",
                    (user_id, user_id, f"{user_id}@example.com"),
                )

    def list_for_user(self, user_id):
        self.calls["defaults"] += 1
        return self.defaults.list_for_user(user_id)

    def upsert(self, record):
        return self.defaults.upsert(record)

    def delete(self, user_id, task):
        return self.defaults.delete(user_id, task)

    def _sessions(self, user_id, ids):
        self.calls["sessions"] += 1
        return self.sessions.get_many_for_user(user_id, ids)

    def _formulas(self, user_id, ids):
        self.calls["formulas"] += 1
        return self.formulas.get_many_for_owner(user_id, ids)

    def session(self, session_id="s1", user_id="u1", preset_id="A-CROP", mode="edit", kind="editor", name="Soft portrait"):
        return self.sessions.create(
            Session(id=session_id, user_id=user_id, preset_id=preset_id, name=name, data={mode: {"formData": {"steps": 20}}}, kind=kind)
        )

    def formula(self, user_id="u1", preset_id="A-CROP", mode="edit", name="Warm"):
        return self.formulas.create(Formula(id="", owner_id=user_id, preset_id=preset_id, mode=mode, name=name), 50)

    def put(self, task="edit_region", preset="A-CROP", mode="edit", setup=None, user=USER, variant=None):
        body = SetTaskDefaultRequest(preset_id=preset, mode=mode, variant=variant, setup=setup or {"kind": "defaults"})
        return self.manager.set_default(user, task, body)

    def default(self, task="edit_region", user=USER):
        return next(item for item in self.manager.offers(user) if item["id"] == task)["default"]

    def withdraw(self, preset="A-CROP"):
        (self.dirs[preset] / "modes" / "edit" / DECLARATION_FILE).unlink()
        return True

    def no_backend(self):
        self.engine_ok = False
        return True

    def stored(self, user="u1"):
        return {item.task: item for item in self.defaults.list_for_user(user)}


@pytest.fixture
def world(mock_db, tmp_path):
    return World(tmp_path)


def test_no_default_reads_null(world):
    assert all(item["default"] is None for item in world.manager.offers(USER))


def test_setting_a_default_with_model_defaults_answers_the_task_entry(world):
    entry = world.put(variant="fast")

    assert entry["id"] == "edit_region" and entry["default"] == world.default()
    default = entry["default"]
    assert default["preset_id"] == "A-CROP" and default["mode"] == "edit" and default["variant"] == "fast"
    assert default["setup"] == {"kind": "defaults", "id": None, "name": None}
    assert default["health"] == {"ok": True}
    assert default["preset_name"] == "Preset A-CROP" and default["mode_label"] and default["set_at"]


def test_a_session_default_is_stored_by_reference_and_replaced_by_the_next_set(world):
    world.session()
    world.put(setup={"kind": "session", "id": "s1"})
    first = world.default()
    formula = world.formula(preset_id="B-MASK")

    world.put(preset="B-MASK", setup={"kind": "formula", "id": formula.id})

    assert first["setup"]["kind"] == "session" and first["setup"]["name"] == "Soft portrait"
    assert first["setup"]["version"] == 1 and first["setup"]["updated_at"]
    second = world.default()
    assert (second["preset_id"], second["setup"]["kind"], second["setup"]["name"]) == ("B-MASK", "formula", "Warm")
    assert len(world.stored()) == 1


def test_clearing_removes_the_default_and_clearing_again_is_fine(world):
    world.put()

    assert world.manager.clear_default(USER, "edit_region")["default"] is None
    assert world.stored() == {}
    assert world.manager.clear_default(USER, "edit_region")["default"] is None


def test_a_default_for_a_task_that_does_not_exist_is_refused(world):
    with pytest.raises(NotFoundError) as error:
        world.put(task="nope")
    assert error.value.code == "task_not_found"
    with pytest.raises(NotFoundError):
        world.manager.clear_default(USER, "nope")


REFUSALS = {
    "preset_not_usable": (lambda w: dict(user=OTHER, preset="A-CROP"), "preset_not_found"),
    "mode_missing": (lambda w: dict(mode="ghost"), "task_not_offered"),
    "task_not_offered": (lambda w: w.withdraw() and {}, "task_not_offered"),
    "no_backend": (lambda w: w.no_backend() and {}, "offer_unavailable"),
    "session_missing": (lambda w: dict(setup={"kind": "session", "id": "ghost"}), "default_session_not_found"),
    "session_of_another_user": (lambda w: dict(setup={"kind": "session", "id": w.session("sx", user_id="u2", preset_id="B-MASK").id}, preset="B-MASK"), "default_session_not_found"),
    "session_not_editor": (lambda w: dict(setup={"kind": "session", "id": w.session("sg", kind="generation").id}), "default_session_mismatch"),
    "session_other_preset": (lambda w: dict(setup={"kind": "session", "id": w.session("sp", preset_id="B-MASK").id}), "default_session_mismatch"),
    "session_other_mode": (lambda w: dict(setup={"kind": "session", "id": w.session("sm", mode="other").id}), "default_session_mismatch"),
    "formula_missing": (lambda w: dict(setup={"kind": "formula", "id": "ghost"}), "default_formula_not_found"),
    "formula_of_another_user": (lambda w: dict(setup={"kind": "formula", "id": w.formula(user_id="u2", preset_id="B-MASK").id}, preset="B-MASK"), "default_formula_not_found"),
    "formula_other_mode": (lambda w: dict(setup={"kind": "formula", "id": w.formula(mode="other").id}), "default_formula_mismatch"),
}


@pytest.mark.parametrize("case", sorted(REFUSALS))
def test_a_refused_set_writes_nothing(world, case):
    build, code = REFUSALS[case]
    arguments = build(world)

    with pytest.raises(DomainError) as error:
        world.put(**arguments)

    assert error.value.code == code
    assert world.stored("u1") == {} and world.stored("u2") == {}


def test_another_users_session_is_refused_exactly_like_a_missing_one(world):
    foreign = world.session("sx", user_id="u2", preset_id="B-MASK")

    with pytest.raises(DomainError) as hidden:
        world.put(preset="B-MASK", setup={"kind": "session", "id": foreign.id})
    with pytest.raises(DomainError) as missing:
        world.put(preset="B-MASK", setup={"kind": "session", "id": "ghost"})

    assert (hidden.value.code, str(hidden.value), hidden.value.status_code) == (
        missing.value.code, str(missing.value), missing.value.status_code
    )


def test_another_users_formula_is_refused_exactly_like_a_missing_one(world):
    foreign = world.formula(user_id="u2", preset_id="B-MASK")

    with pytest.raises(DomainError) as hidden:
        world.put(preset="B-MASK", setup={"kind": "formula", "id": foreign.id})
    with pytest.raises(DomainError) as missing:
        world.put(preset="B-MASK", setup={"kind": "formula", "id": "ghost"})

    assert (hidden.value.code, str(hidden.value), hidden.value.status_code) == (
        missing.value.code, str(missing.value), missing.value.status_code
    )


def test_a_setup_of_kind_defaults_takes_no_id_and_the_others_need_one():
    for setup in ({"kind": "defaults", "id": "x"}, {"kind": "session"}, {"kind": "formula"}, {"kind": "other"}):
        with pytest.raises(ValueError):
            SetTaskDefaultRequest(preset_id="p", mode="m", setup=setup)


def test_another_users_default_is_invisible_and_untouchable(world):
    world.put(preset="B-MASK")

    assert world.default(user=OTHER) is None
    world.manager.clear_default(OTHER, "edit_region")
    assert world.default() is not None
    world.put(preset="B-MASK", user=OTHER, variant="mine")
    assert world.default()["variant"] is None and world.default(user=OTHER)["variant"] == "mine"


def broken(world, task, expected, change):
    before = world.stored()[task]
    change(world)
    default = world.default(task)

    assert default["health"]["ok"] is False
    assert default["health"]["reason"] == expected
    assert world.stored()[task] == before
    assert (default["preset_id"], default["mode"], default["setup"]["kind"], default["setup"]["id"]) == (
        before.preset_id, before.mode, before.setup_kind, before.setup_id
    )
    return default


def test_a_default_whose_preset_is_gone_reads_broken_and_stays(world):
    world.put()

    default = broken(world, "edit_region", "preset_missing", lambda w: setattr(w, "templates", [t for t in w.templates if t.id != "A-CROP"]))

    assert default["health"]["missing"] == {"kind": "preset", "id": "A-CROP"}
    assert default["health"]["preset_name"] == "Preset A-CROP" and default["health"]["mode_label"]


def test_a_default_whose_preset_is_unassigned_reads_broken_and_stays(world):
    world.put()

    broken(world, "edit_region", "preset_unassigned", lambda w: w.visible.update(u1={"B-MASK"}))


def test_a_default_whose_mode_is_gone_reads_broken_and_stays(world):
    world.put()

    def drop_mode(w):
        w.templates = [dataclasses.replace(t, modes={}) if t.id == "A-CROP" else t for t in w.templates]

    broken(world, "edit_region", "mode_missing", drop_mode)


def test_a_default_whose_mode_no_longer_offers_the_task_reads_broken_and_stays(world):
    world.put()

    default = broken(world, "edit_region", "offer_withdrawn", World.withdraw)

    assert default["health"]["missing"] == {"kind": "task", "id": "edit_region"}


def test_a_default_whose_engine_has_no_backend_reads_broken_and_stays(world):
    world.put()

    default = broken(world, "edit_region", "requirements_failed", World.no_backend)

    assert default["health"]["missing"]["kind"] == "backend"


def test_a_default_whose_session_was_deleted_reads_broken_and_names_it(world):
    world.session()
    world.put(setup={"kind": "session", "id": "s1"})

    default = broken(world, "edit_region", "session_deleted", lambda w: w.sessions.delete("s1"))

    assert default["health"]["name"] == "Soft portrait"
    assert default["health"]["missing"] == {"kind": "session", "id": "s1"}
    assert default["setup"]["name"] == "Soft portrait"


def test_a_default_whose_session_no_longer_holds_the_mode_reads_broken_and_stays(world):
    world.session()
    world.put(setup={"kind": "session", "id": "s1"})

    def reshape(w):
        stored = w.sessions.get_by_id("s1")
        w.sessions.update(stored.model_copy(update={"data": {"other": {}}}), stored.version)

    broken(world, "edit_region", "session_mismatch", reshape)


def test_a_default_whose_formula_was_deleted_reads_broken_and_names_it(world):
    formula = world.formula()
    world.put(setup={"kind": "formula", "id": formula.id})

    default = broken(world, "edit_region", "formula_deleted", lambda w: w.formulas.delete("u1", formula.id))

    assert default["health"]["name"] == "Warm"


def test_a_default_whose_formula_no_longer_applies_reads_broken_and_stays(world):
    formula = world.formula()
    world.put(setup={"kind": "formula", "id": formula.id})

    def retarget(w):
        with get_cursor() as cursor:
            cursor.execute("UPDATE formulas SET mode = 'other' WHERE id = ?", (formula.id,))

    broken(world, "edit_region", "formula_mismatch", retarget)


def test_a_broken_default_recovers_when_the_world_comes_back_and_is_never_replaced(world):
    world.put(preset="A-CROP")
    world.put(task="remove_object", preset="B-MASK")
    world.visible["u1"] = {"B-MASK"}

    assert world.default()["health"]["reason"] == "preset_unassigned"
    assert world.default("remove_object")["health"] == {"ok": True}
    assert world.stored()["edit_region"].preset_id == "A-CROP"

    world.visible["u1"] = {"A-CROP", "B-MASK"}

    assert world.default()["health"] == {"ok": True}


def test_a_default_points_at_the_session_and_follows_its_later_saves(world):
    world.session()
    world.put(setup={"kind": "session", "id": "s1"})
    before = world.default()["setup"]

    stored = world.sessions.get_by_id("s1")
    world.sessions.update(stored.model_copy(update={"name": "Soft portrait v2"}), stored.version)

    after = world.default()["setup"]
    assert (before["version"], before["name"]) == (1, "Soft portrait")
    assert (after["version"], after["name"]) == (2, "Soft portrait v2")
    assert world.default()["health"] == {"ok": True}
    assert world.stored()["edit_region"].setup_id == "s1"


def test_a_listing_with_several_defaults_asks_each_store_once(world):
    world.session()
    world.session("s2", preset_id="B-MASK")
    formula = world.formula()
    world.put(setup={"kind": "session", "id": "s1"})
    world.put(task="remove_object", setup={"kind": "formula", "id": formula.id})
    world.calls.update(defaults=0, sessions=0, formulas=0)

    entries = world.manager.offers(USER)

    assert world.calls == {"defaults": 1, "sessions": 1, "formulas": 1}
    assert all(item["default"]["health"] == {"ok": True} for item in entries)


def test_a_listing_without_session_or_formula_defaults_does_not_ask_for_them(world):
    world.put()
    world.calls.update(defaults=0, sessions=0, formulas=0)

    world.manager.offers(USER)

    assert world.calls == {"defaults": 1, "sessions": 0, "formulas": 0}


def test_deleting_a_user_removes_their_defaults(world):
    world.put()
    world.put(user=OTHER, preset="B-MASK")

    with get_cursor() as cursor:
        cursor.execute("DELETE FROM users WHERE id = 'u1'")

    assert world.stored("u1") == {} and len(world.stored("u2")) == 1


def test_deleting_the_session_does_not_cascade_the_default_away(world):
    world.session()
    world.put(setup={"kind": "session", "id": "s1"})

    world.sessions.delete("s1")

    assert world.stored()["edit_region"].setup_id == "s1"
