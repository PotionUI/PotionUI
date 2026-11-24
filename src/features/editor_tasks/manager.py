import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple

from src.features.editor_tasks.default_repository import TaskDefault, TaskDefaultRepository
from src.features.editor_tasks.dto import SetTaskDefaultRequest
from src.features.editor_tasks.registry import TaskDefinition, TaskRegistry
from src.features.presets.editor_mode_lint import lint_editor_mode
from src.features.presets.editor_modes import input_field_names
from src.features.presets.extensions import walk_fields
from src.features.presets.mode_labels import mode_display_name, mode_display_icon
from src.features.presets.templates import (
    EDITOR_KEY_PREFIX,
    EditorModeSpec,
    ModeTemplate,
    PresetTemplate,
    find_mode,
    is_editor_key,
    is_mode_unvalidated,
)
from src.platform.database.rows import dt_iso, now_iso
from src.platform.http.errors import DomainError, NotFoundError

logger = logging.getLogger(__name__)

NO_BACKEND = "No backend is set up for the engine of this preset."
EDITOR_SESSION_KIND = "editor"

OwnedLookup = Callable[[str, Iterable[str]], Dict[str, Any]]


@dataclass(frozen=True)
class EditorEntry:
    template: PresetTemplate
    name: str
    mode: ModeTemplate
    spec: EditorModeSpec

    @property
    def key(self) -> str:
        return f"{EDITOR_KEY_PREFIX}{self.name}"


@dataclass(frozen=True)
class ResolvedOffer:
    task: TaskDefinition
    template: PresetTemplate
    mode_name: str
    mode: ModeTemplate
    spec: EditorModeSpec
    via: Optional[str]


def owned_names(spec: EditorModeSpec) -> Set[str]:
    return {*input_field_names(spec), *spec.fixed, *spec.hide}


def scale_limits(mode: ModeTemplate, spec: EditorModeSpec) -> Optional[Tuple[float, float]]:
    name = spec.inputs.get("scale")
    if not name:
        return None
    for form in mode.forms:
        for field, _ in walk_fields(form.fields):
            if field.name == name:
                configuration = field.configuration or {}
                low, high = configuration.get("min"), configuration.get("max")
                if isinstance(low, (int, float)) and isinstance(high, (int, float)):
                    return float(low), float(high)
    return None


def short_description_of(entry: EditorEntry) -> Optional[str]:
    if entry.spec.short_description:
        return entry.spec.short_description
    forms = entry.mode.forms
    form = next((item for item in forms if item.default), forms[0] if forms else None)
    return form.short_description if form is not None else None


class EditorTaskManager:
    def __init__(
        self,
        registry: TaskRegistry,
        templates: Callable[[], Iterable[PresetTemplate]],
        visible_preset_ids: Callable[[Any], Set[str]],
        engine_available: Callable[[Optional[str]], bool],
        defaults: Optional[TaskDefaultRepository] = None,
        sessions_by_id: Optional[OwnedLookup] = None,
        formulas_by_id: Optional[OwnedLookup] = None,
    ):
        self.registry = registry
        self._templates = templates
        self._visible_preset_ids = visible_preset_ids
        self._engine_available = engine_available
        self._defaults = defaults
        self._sessions = sessions_by_id
        self._formulas = formulas_by_id

    def _prompt_need(self, task_id: str) -> Optional[str]:
        task = self.registry.get(task_id)
        return task.prompt if task is not None else None

    def _editors_of(self, task_id: str) -> Tuple[str, ...]:
        task = self.registry.get(task_id)
        return task.editors if task is not None else ("image",)

    def editor_entries(self) -> List[EditorEntry]:
        found: List[EditorEntry] = []
        for template in self._templates():
            for name, mode in sorted((template.editor_modes or {}).items()):
                spec = mode.editor
                if spec is None:
                    continue
                owner = mode.source_plugin
                if owner is None:
                    origin = template.origin if isinstance(template.origin, dict) else {}
                    owner = origin.get("plugin_id")
                issues = lint_editor_mode(template, name, mode, self._prompt_need, self._editors_of, owner)
                problems = [message for level, message in issues if level == "error"]
                if problems or self._prompt_need(spec.task) is None:
                    logger.warning("Editor mode %s of %s skipped: %s", name, template.id, "; ".join(problems) or spec.task)
                    continue
                found.append(EditorEntry(template, name, mode, spec))
        return found

    def _offer(self, entry: EditorEntry, via: Optional[str]) -> Dict[str, Any]:
        template, spec = entry.template, entry.spec
        mode_label = mode_display_name(entry.name, entry.mode.mode_label)
        qualifies = self._engine_available(template.engine)
        offer: Dict[str, Any] = {
            "preset_id": template.id,
            "preset_name": template.name,
            "mode": entry.key,
            "from_mode": spec.from_mode,
            "mode_label": mode_label,
            "mode_icon": mode_display_icon(entry.name, entry.mode.mode_icon),
            "label": spec.label or mode_label,
            "short_description": short_description_of(entry),
            "unvalidated": is_mode_unvalidated(template, spec.from_mode),
            "order": spec.order,
            "region_strategy": spec.region,
            "max_range_s": spec.max_range_s,
            "quantity_field": spec.inputs.get("quantity"),
            "strength": (
                {key: spec.strength[key] for key in ("default", "min", "max")} if spec.strength else None
            ),
            "scale": self._scale_offer(entry),
            "via": via,
            "qualifies": qualifies,
        }
        if not qualifies:
            offer["reason"] = NO_BACKEND
        return offer

    @staticmethod
    def _scale_offer(entry: EditorEntry) -> Optional[Dict[str, Any]]:
        limits = scale_limits(entry.mode, entry.spec)
        if limits is None:
            return None
        return {"field": entry.spec.inputs.get("scale"), "min": limits[0], "max": limits[1]}

    def offers(self, user: Any, editor: str = "image") -> List[Dict[str, Any]]:
        visible = self._visible_preset_ids(user)
        return self._entries(user, self.registry.list(editor), visible, self.editor_entries())

    def entry_for(self, user: Any, task_id: str) -> Dict[str, Any]:
        task = self.registry.get(task_id)
        if task is None:
            raise NotFoundError("task_not_found", "This task does not exist")
        return self._entries(user, [task], self._visible_preset_ids(user), self.editor_entries())[0]

    @staticmethod
    def _sort(offers: List[Dict[str, Any]]) -> None:
        offers.sort(key=lambda offer: (offer["preset_name"].lower(), offer["order"], offer["mode"]))

    def _entries(
        self,
        user: Any,
        tasks: List[TaskDefinition],
        visible: Set[str],
        declared: List[EditorEntry],
    ) -> List[Dict[str, Any]]:
        direct: Dict[str, List[Dict[str, Any]]] = {}
        for entry in declared:
            if entry.template.id in visible:
                direct.setdefault(entry.spec.task, []).append(self._offer(entry, None))
        for offers in direct.values():
            self._sort(offers)
        entries = [self._entry(task, direct) for task in tasks]
        defaults = self._defaults_by_task(user, tasks, visible, declared)
        for entry in entries:
            entry["default"] = defaults.get(entry["id"])
        return entries

    def _entry(self, task: TaskDefinition, direct: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
        offers = list(direct.get(task.id, []))
        fallback = None
        if task.fallback is not None:
            fallback_id, prompt = task.fallback
            fallback = {"task": fallback_id, "prompt": prompt}
            covered = {offer["preset_id"] for offer in offers}
            offers += [{**offer, "via": fallback_id} for offer in direct.get(fallback_id, []) if offer["preset_id"] not in covered]
            self._sort(offers)
        return {
            "id": task.id,
            "label": task.label,
            "icon": task.icon,
            "editors": list(task.editors),
            "region": task.region,
            "prompt": task.prompt,
            "range": task.range,
            "result": task.result,
            "audio": task.audio,
            "resizes": task.resizes,
            "produces": task.produces,
            "internal": task.internal,
            "matte": task.matte,
            "grow": task.grow,
            "grows": task.grows,
            "plan": task.plan,
            "fallback": fallback,
            "offers": offers,
            "default": None,
        }

    def _defaults_by_task(
        self,
        user: Any,
        tasks: List[TaskDefinition],
        visible: Set[str],
        declared: List[EditorEntry],
    ) -> Dict[str, Dict[str, Any]]:
        if self._defaults is None:
            return {}
        stored = {item.task: item for item in self._defaults.list_for_user(user.id)}
        wanted = [(task, stored[task.id]) for task in tasks if task.id in stored]
        if not wanted:
            return {}
        templates = {template.id: template for template in self._templates()}
        offered: Dict[Tuple[str, str], Set[str]] = {}
        direct: Dict[str, Set[str]] = {}
        for entry in declared:
            offered.setdefault((entry.template.id, entry.key), set()).add(entry.spec.task)
            direct.setdefault(entry.template.id, set()).add(entry.spec.task)
        session_ids = [item.setup_id for _, item in wanted if item.setup_kind == "session"]
        formula_ids = [item.setup_id for _, item in wanted if item.setup_kind == "formula"]
        sessions = self._sessions(user.id, session_ids) if session_ids and self._sessions else {}
        formulas = self._formulas(user.id, formula_ids) if formula_ids and self._formulas else {}
        return {
            task.id: self._default_entry(task, item, templates, offered, direct, visible, sessions, formulas)
            for task, item in wanted
        }

    def _default_entry(
        self,
        task: TaskDefinition,
        item: TaskDefault,
        templates: Dict[str, PresetTemplate],
        offered: Dict[Tuple[str, str], Set[str]],
        direct: Dict[str, Set[str]],
        visible: Set[str],
        sessions: Dict[str, Any],
        formulas: Dict[str, Any],
    ) -> Dict[str, Any]:
        template = templates.get(item.preset_id)
        mode = find_mode(template, item.mode) if template is not None and is_editor_key(item.mode) else None
        preset_name = template.name if template is not None else item.preset_name
        mode_label = mode_display_name(item.mode, mode.mode_label) if mode is not None else item.mode_label
        setup: Dict[str, Any] = {"kind": item.setup_kind, "id": item.setup_id, "name": item.setup_name}
        gone: Optional[Tuple[str, str, str]] = None
        if template is None:
            gone = ("preset_missing", "preset", item.preset_id)
        elif item.preset_id not in visible:
            gone = ("preset_unassigned", "preset", item.preset_id)
        elif mode is None:
            gone = ("mode_missing", "mode", item.mode)
        else:
            wanted = {task.id}
            if task.fallback is not None and task.id not in direct.get(item.preset_id, set()):
                wanted.add(task.fallback[0])
            if not offered.get((item.preset_id, item.mode), set()) & wanted:
                gone = ("offer_withdrawn", "task", task.id)
            elif not self._engine_available(template.engine):
                gone = ("requirements_failed", "backend", template.engine or "")
        if item.setup_kind == "session":
            session = sessions.get(item.setup_id)
            if session is None:
                gone = gone or ("session_deleted", "session", item.setup_id)
            else:
                setup.update(name=session.name, version=session.version, updated_at=dt_iso(session.updated_at))
                if not self._session_fits(session, item.preset_id, item.mode):
                    gone = gone or ("session_mismatch", "session", item.setup_id)
        elif item.setup_kind == "formula":
            formula = formulas.get(item.setup_id)
            if formula is None:
                gone = gone or ("formula_deleted", "formula", item.setup_id)
            else:
                setup.update(name=formula.name, updated_at=dt_iso(formula.updated_at))
                if not self._formula_fits(formula, item.preset_id, item.mode, mode):
                    gone = gone or ("formula_mismatch", "formula", item.setup_id)
        health: Dict[str, Any] = {"ok": True}
        if gone is not None:
            reason, kind, missing = gone
            health = {
                "ok": False,
                "reason": reason,
                "missing": {"kind": kind, "id": missing},
                "name": setup["name"],
                "preset_name": preset_name,
                "mode_label": mode_label,
            }
        return {
            "preset_id": item.preset_id,
            "preset_name": preset_name,
            "mode": item.mode,
            "mode_label": mode_label,
            "setup": setup,
            "set_at": item.set_at,
            "health": health,
        }

    @staticmethod
    def _session_fits(session: Any, preset_id: str, mode_name: str) -> bool:
        return (
            session.kind == EDITOR_SESSION_KIND
            and session.preset_id == preset_id
            and isinstance(session.data, dict)
            and mode_name in session.data
        )

    @staticmethod
    def _formula_fits(formula: Any, preset_id: str, mode_name: str, mode: Optional[ModeTemplate] = None) -> bool:
        if formula.preset_id != preset_id:
            return False
        spec = getattr(mode, "editor", None)
        return formula.mode == mode_name or (spec is not None and spec.from_mode is not None and formula.mode == spec.from_mode)

    def set_default(self, user: Any, task_id: str, body: SetTaskDefaultRequest) -> Dict[str, Any]:
        if self._defaults is None:
            raise DomainError("defaults_unavailable", "Task defaults are not available", 503)
        offer = self.resolve_offer(user, task_id, body.preset_id, body.mode)
        key = offer.mode_name
        setup = body.setup
        setup_name: Optional[str] = None
        if setup.kind == "session":
            session = (self._sessions(user.id, [setup.id]) if self._sessions else {}).get(setup.id)
            if session is None:
                raise NotFoundError("default_session_not_found", "This session does not exist")
            if not self._session_fits(session, body.preset_id, key):
                raise DomainError(
                    "default_session_mismatch", "This session is not an editor session of that preset and mode", 422
                )
            setup_name = session.name
        elif setup.kind == "formula":
            formula = (self._formulas(user.id, [setup.id]) if self._formulas else {}).get(setup.id)
            if formula is None:
                raise NotFoundError("default_formula_not_found", "This formula does not exist")
            if not self._formula_fits(formula, body.preset_id, key, offer.mode):
                raise DomainError("default_formula_mismatch", "This formula does not apply to that preset and mode", 422)
            setup_name = formula.name
        self._defaults.upsert(
            TaskDefault(
                user_id=user.id,
                task=offer.task.id,
                preset_id=body.preset_id,
                mode=key,
                setup_kind=setup.kind,
                setup_id=setup.id,
                setup_name=setup_name,
                preset_name=offer.template.name,
                mode_label=mode_display_name(key, offer.mode.mode_label),
                set_at=now_iso(),
            )
        )
        return self.entry_for(user, offer.task.id)

    def clear_default(self, user: Any, task_id: str) -> Dict[str, Any]:
        if self._defaults is None:
            raise DomainError("defaults_unavailable", "Task defaults are not available", 503)
        if self.registry.get(task_id) is None:
            raise NotFoundError("task_not_found", "This task does not exist")
        self._defaults.delete(user.id, task_id)
        return self.entry_for(user, task_id)

    def mode_status(self, preset_id: str, mode_name: str) -> str:
        key = mode_name
        template = next((item for item in self._templates() if item.id == preset_id), None)
        if not is_editor_key(key) or template is None or find_mode(template, key) is None:
            return "unknown"
        if any(entry.template.id == preset_id and entry.key == key for entry in self.editor_entries()):
            return "ok"
        return "not_offered"

    def owned_fields(self, preset_id: str, mode_name: str) -> Set[str]:
        template = next((item for item in self._templates() if item.id == preset_id), None)
        mode = find_mode(template, mode_name) if is_editor_key(mode_name) else None
        return owned_names(mode.editor) if mode is not None and mode.editor is not None else set()

    def resolve_offer(self, user: Any, task_id: str, preset_id: str, mode_name: str) -> ResolvedOffer:
        task = self.registry.get(task_id)
        if task is None:
            raise NotFoundError("task_not_found", "This task does not exist")
        if preset_id not in self._visible_preset_ids(user):
            raise NotFoundError("preset_not_found", "The selected preset is not available")
        if not is_editor_key(mode_name):
            raise DomainError("task_not_offered", "This mode does not offer that task", 422)
        key = mode_name
        entries = [entry for entry in self.editor_entries() if entry.template.id == preset_id]
        has_own = any(entry.spec.task == task.id for entry in entries)
        wanted = [(task.id, None)]
        if task.fallback is not None and not has_own:
            wanted.append((task.fallback[0], task.fallback[0]))
        for entry in entries:
            if entry.key != key:
                continue
            for candidate, via in wanted:
                if entry.spec.task != candidate:
                    continue
                if not self._engine_available(entry.template.engine):
                    raise DomainError("offer_unavailable", NO_BACKEND, 422)
                return ResolvedOffer(task, entry.template, key, entry.mode, entry.spec, via)
        raise DomainError("task_not_offered", "This mode does not offer that task", 422)
