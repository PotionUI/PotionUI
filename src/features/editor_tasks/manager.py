import logging
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple

from src.features.editor_tasks.declarations import (
    DeclarationCache,
    ModeDeclaration,
    TaskDeclaration,
    mode_owner,
    owned_fields,
    validate_mode,
)
from src.features.editor_tasks.registry import TaskDefinition, TaskRegistry
from src.features.presets.mode_labels import mode_display_name
from src.features.presets.templates import PresetTemplate, default_form_name

logger = logging.getLogger(__name__)

NO_BACKEND = "No backend is set up for the engine of this preset."


class EditorTaskManager:
    def __init__(
        self,
        registry: TaskRegistry,
        templates: Callable[[], Iterable[PresetTemplate]],
        declarations: DeclarationCache,
        visible_preset_ids: Callable[[Any], Set[str]],
        engine_available: Callable[[Optional[str]], bool],
    ):
        self.registry = registry
        self._templates = templates
        self._declarations = declarations
        self._visible_preset_ids = visible_preset_ids
        self._engine_available = engine_available

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
        }
        if not qualifies:
            offer["reason"] = NO_BACKEND
        return offer

    def offers(self, user: Any, editor: str = "image") -> List[Dict[str, Any]]:
        visible = self._visible_preset_ids(user)
        direct: Dict[str, List[Dict[str, Any]]] = {}
        for template, result in self.mode_declarations():
            if template.id not in visible:
                continue
            for declaration in result.tasks:
                direct.setdefault(declaration.task, []).append(self._offer(template, result.mode, declaration, None))
        for offers in direct.values():
            offers.sort(key=lambda offer: (offer["preset_name"].lower(), offer["mode"]))
        return [self._entry(task, direct) for task in self.registry.list(editor)]

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
