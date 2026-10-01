from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol

from src.platform.security.user import AccountType, User


@dataclass(frozen=True)
class DeclaredGroup:
    id: str
    label: str
    fields: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class LoadedForm:
    fields: Dict[str, Dict[str, Any]]
    groups: List[DeclaredGroup]
    preset_version: str


class FormUnavailable(Exception):
    pass


class FormSource(Protocol):
    def load(self, preset_id: str, mode: str, form_name: Optional[str]) -> LoadedForm: ...


@dataclass(frozen=True)
class ModelRefInfo:
    model_type: Optional[str]
    tag_ids: List[str]
    available: bool


class ModelRefChecker(Protocol):
    def inspect(self, model_id: str, user: User) -> Optional[ModelRefInfo]: ...


def flatten_fields(schema: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    found: Dict[str, Dict[str, Any]] = {}

    def walk(node: Any) -> None:
        if not isinstance(node, dict):
            return
        name = node.get("name")
        if isinstance(name, str) and name and "type" in node:
            found.setdefault(name, node)
        for child in node.get("children") or []:
            walk(child)

    for node in (schema.get("properties") or {}).values():
        walk(node)
    return found


class PresetFormSource:
    def __init__(self, collaborators: Any):
        self._collaborators = collaborators

    def load(self, preset_id: str, mode: str, form_name: Optional[str]) -> LoadedForm:
        from src.features.presets import operations as preset_operations
        from src.features.presets.formula_groups import resolve_formula_groups

        try:
            preset = self._collaborators.file_repo.find_preset_by_id(preset_id)
            if preset is None:
                raise FormUnavailable(f"Preset '{preset_id}' not found")
            form = preset_operations.get_form_schema(self._collaborators, preset_id, mode, form_name)
            groups = resolve_formula_groups(preset, mode, form_name)
        except FormUnavailable:
            raise
        except Exception as exc:
            raise FormUnavailable(f"Form for preset '{preset_id}' mode '{mode}' is not available") from exc
        return LoadedForm(
            fields=flatten_fields(form["form_schema"]),
            groups=[DeclaredGroup(id=g.id, label=g.label, fields=list(g.fields)) for g in groups],
            preset_version=str(getattr(preset, "version", "") or ""),
        )


class ModelRepositoryRefChecker:
    def __init__(self, model_repository: Any):
        self._models = model_repository

    def inspect(self, model_id: str, user: User) -> Optional[ModelRefInfo]:
        model = self._models.get_by_id(model_id, include_providers=False, include_tags=True)
        if model is None:
            return None
        if user.account_type != AccountType.ADMIN:
            if model_id not in self._models.get_available_model_ids_for_user(user.id):
                return None
        return ModelRefInfo(
            model_type=model.model_type,
            tag_ids=[tag.id for tag in (model.tags or [])],
            available=bool(model.is_available),
        )
