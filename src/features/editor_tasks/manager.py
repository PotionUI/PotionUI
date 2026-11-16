import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple

from src.features.editor_tasks.declarations import (
    DeclarationCache,
    ModeDeclaration,
    TaskDeclaration,
    mode_owner,
    owned_fields,
    validate_mode,
)
from src.features.editor_tasks.default_repository import TaskDefault, TaskDefaultRepository
from src.features.editor_tasks.dto import SetTaskDefaultRequest
from src.features.editor_tasks.registry import TaskDefinition, TaskRegistry
from src.features.presets.mode_labels import mode_display_name
from src.features.presets.templates import ModeTemplate, PresetTemplate, default_form_name
from src.platform.database.rows import dt_iso, now_iso
from src.platform.http.errors import DomainError, NotFoundError

logger = logging.getLogger(__name__)

NO_BACKEND = "No backend is set up for the engine of this preset."
EDITOR_SESSION_KIND = "editor"

OwnedLookup = Callable[[str, Iterable[str]], Dict[str, Any]]


@dataclass(frozen=True)
class ResolvedOffer:
    task: TaskDefinition
    template: PresetTemplate
    mode_name: str
    mode: ModeTemplate
    declaration: TaskDeclaration
    via: Optional[str]


class EditorTaskManager:
    def __init__(
        self,
        registry: TaskRegistry,
        templates: Callable[[], Iterable[PresetTemplate]],
        declarations: DeclarationCache,
        visible_preset_ids: Callable[[Any], Set[str]],
        engine_available: Callable[[Optional[str]], bool],
        defaults: Optional[TaskDefaultRepository] = None,
        sessions_by_id: Optional[OwnedLookup] = None,
        formulas_by_id: Optional[OwnedLookup] = None,
    ):
        self.registry = registry
        self._templates = templates
        self._declarations = declarations
        self._visible_preset_ids = visible_preset_ids
        self._engine_available = engine_available
        self._defaults = defaults
        self._sessions = sessions_by_id
        self._formulas = formulas_by_id

    def _prompt_need(self, task_id: str) -> Optional[str]:
        task = self.registry.get(task_id)
        return task.prompt if task is not None else None

    def mode_declarations(self) -> List[Tuple[PresetTemplate, ModeDeclaration]]:
        parsed = self._declarations()
        found: List[Tuple[PresetTemplate, ModeDeclaration]] = []
        for template in self._templates():
            for mode_name, mode in (template.modes or {}).items():
                file = parsed.get((template.id, mode_name))
                if file is None or not file.present:
                    continue
                result = validate_mode(
                    template.id,
                    mode_name,
                    mode,
                    file,
                    self._prompt_need,
                    mode_owner(template, mode),
                    (template.vars or {}).get("promptless_modes") or [],
                )
                for message in result.errors:
                    logger.warning("Editor tasks skipped for %s: %s", template.id, message)
                found.append((template, result))
        return found

    def _offer(self, template: PresetTemplate, mode_name: str, declaration: TaskDeclaration, via: Optional[str]) -> Dict[str, Any]:
        mode = template.modes[mode_name]
        mode_label = mode_display_name(mode_name, mode.mode_label)
        qualifies = self._engine_available(template.engine)
        offer: Dict[str, Any] = {
            "preset_id": template.id,
            "preset_name": template.name,
            "mode": mode_name,
            "mode_label": mode_label,
            "label": declaration.label or mode_label,
            "variant": default_form_name(mode),
            "region_strategy": declaration.region,
            "via": via,
            "qualifies": qualifies,
            "owned": sorted(owned_fields(declaration, mode)),
        }
        if not qualifies:
            offer["reason"] = NO_BACKEND
        return offer

    def offers(self, user: Any, editor: str = "image") -> List[Dict[str, Any]]:
        visible = self._visible_preset_ids(user)
        declared = self.mode_declarations()
        return self._entries(user, self.registry.list(editor), visible, declared)

    def entry_for(self, user: Any, task_id: str) -> Dict[str, Any]:
        task = self.registry.get(task_id)
        if task is None:
            raise NotFoundError("task_not_found", "This task does not exist")
        return self._entries(user, [task], self._visible_preset_ids(user), self.mode_declarations())[0]

    def _entries(
        self,
        user: Any,
        tasks: List[TaskDefinition],
        visible: Set[str],
        declared: List[Tuple[PresetTemplate, ModeDeclaration]],
    ) -> List[Dict[str, Any]]:
        direct: Dict[str, List[Dict[str, Any]]] = {}
        for template, result in declared:
            if template.id not in visible:
                continue
            for declaration in result.tasks:
                direct.setdefault(declaration.task, []).append(self._offer(template, result.mode, declaration, None))
        for offers in direct.values():
            offers.sort(key=lambda offer: (offer["preset_name"].lower(), offer["mode"]))
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
            if not offers:
                offers = [{**offer, "via": fallback_id} for offer in direct.get(fallback_id, [])]
        return {
            "id": task.id,
            "label": task.label,
            "icon": task.icon,
            "region": task.region,
            "prompt": task.prompt,
            "fallback": fallback,
            "offers": offers,
            "default": None,
        }

    def _defaults_by_task(
        self,
        user: Any,
        tasks: List[TaskDefinition],
        visible: Set[str],
        declared: List[Tuple[PresetTemplate, ModeDeclaration]],
    ) -> Dict[str, Dict[str, Any]]:
        if self._defaults is None:
            return {}
        stored = {item.task: item for item in self._defaults.list_for_user(user.id)}
        wanted = [(task, stored[task.id]) for task in tasks if task.id in stored]
        if not wanted:
            return {}
        templates = {template.id: template for template in self._templates()}
        offered: Dict[Tuple[str, str], Set[str]] = {}
        for template, result in declared:
            offered.setdefault((template.id, result.mode), set()).update(item.task for item in result.tasks)
        session_ids = [item.setup_id for _, item in wanted if item.setup_kind == "session"]
        formula_ids = [item.setup_id for _, item in wanted if item.setup_kind == "formula"]
        sessions = self._sessions(user.id, session_ids) if session_ids and self._sessions else {}
        formulas = self._formulas(user.id, formula_ids) if formula_ids and self._formulas else {}
        return {
            task.id: self._default_entry(task, item, templates, offered, visible, sessions, formulas)
            for task, item in wanted
        }

    def _default_entry(
        self,
        task: TaskDefinition,
        item: TaskDefault,
        templates: Dict[str, PresetTemplate],
        offered: Dict[Tuple[str, str], Set[str]],
        visible: Set[str],
        sessions: Dict[str, Any],
        formulas: Dict[str, Any],
    ) -> Dict[str, Any]:
        template = templates.get(item.preset_id)
        mode = (template.modes or {}).get(item.mode) if template is not None else None
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
            wanted = {task.id} | ({task.fallback[0]} if task.fallback is not None else set())
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
                if not self._formula_fits(formula, item.preset_id, item.mode):
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
            "variant": item.variant,
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
    def _formula_fits(formula: Any, preset_id: str, mode_name: str) -> bool:
        return formula.preset_id == preset_id and formula.mode == mode_name

    def set_default(self, user: Any, task_id: str, body: SetTaskDefaultRequest) -> Dict[str, Any]:
        if self._defaults is None:
            raise DomainError("defaults_unavailable", "Task defaults are not available", 503)
        offer = self.resolve_offer(user, task_id, body.preset_id, body.mode)
        setup = body.setup
        setup_name: Optional[str] = None
        if setup.kind == "session":
            session = (self._sessions(user.id, [setup.id]) if self._sessions else {}).get(setup.id)
            if session is None:
                raise NotFoundError("default_session_not_found", "This session does not exist")
            if not self._session_fits(session, body.preset_id, body.mode):
                raise DomainError(
                    "default_session_mismatch", "This session is not an editor session of that preset and mode", 422
                )
            setup_name = session.name
        elif setup.kind == "formula":
            formula = (self._formulas(user.id, [setup.id]) if self._formulas else {}).get(setup.id)
            if formula is None:
                raise NotFoundError("default_formula_not_found", "This formula does not exist")
            if not self._formula_fits(formula, body.preset_id, body.mode):
                raise DomainError("default_formula_mismatch", "This formula does not apply to that preset and mode", 422)
            setup_name = formula.name
        self._defaults.upsert(
            TaskDefault(
                user_id=user.id,
                task=offer.task.id,
                preset_id=body.preset_id,
                mode=body.mode,
                variant=body.variant,
                setup_kind=setup.kind,
                setup_id=setup.id,
                setup_name=setup_name,
                preset_name=offer.template.name,
                mode_label=mode_display_name(body.mode, offer.mode.mode_label),
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
        template = next((item for item in self._templates() if item.id == preset_id), None)
        if template is None or mode_name not in (template.modes or {}):
            return "unknown"
        for found, result in self.mode_declarations():
            if found.id == preset_id and result.mode == mode_name and result.tasks:
                return "ok"
        return "not_offered"

    def owned_fields(self, preset_id: str, mode_name: str) -> Set[str]:
        owned: Set[str] = set()
        for template, result in self.mode_declarations():
            if template.id == preset_id and result.mode == mode_name:
                for declaration in result.tasks:
                    owned |= owned_fields(declaration, template.modes[mode_name])
        return owned

    def resolve_offer(self, user: Any, task_id: str, preset_id: str, mode_name: str) -> ResolvedOffer:
        task = self.registry.get(task_id)
        if task is None:
            raise NotFoundError("task_not_found", "This task does not exist")
        if preset_id not in self._visible_preset_ids(user):
            raise NotFoundError("preset_not_found", "The selected preset is not available")
        wanted = [(task.id, None)]
        if task.fallback is not None:
            wanted.append((task.fallback[0], task.fallback[0]))
        for template, result in self.mode_declarations():
            if template.id != preset_id or result.mode != mode_name:
                continue
            for candidate, via in wanted:
                declaration = next((item for item in result.tasks if item.task == candidate), None)
                if declaration is None:
                    continue
                if not self._engine_available(template.engine):
                    raise DomainError("offer_unavailable", NO_BACKEND, 422)
                return ResolvedOffer(task, template, mode_name, template.modes[mode_name], declaration, via)
        raise DomainError("task_not_offered", "This mode does not offer that task", 422)
