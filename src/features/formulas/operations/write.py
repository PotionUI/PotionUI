import dataclasses
from typing import Any, Dict, List, Optional, Tuple

from src.features.formulas.collaborators import FormulaCollaborators
from src.features.formulas.dto import (
    MAX_FORMULAS_PER_SCOPE,
    CreateFormulaRequest,
    FormulaContent,
    UpdateFormulaRequest,
)
from src.features.formulas.errors import FormulaError, not_found
from src.features.formulas.records import Formula
from src.features.formulas.repository import FormulaLimitReached, FormulaNameTaken
from src.features.formulas.signatures import field_signature, owning_field
from src.features.formulas.sources import FormUnavailable

def _name_taken(name: str) -> FormulaError:
    return FormulaError("formula_name_exists", f"A formula named '{name}' already exists for this preset and mode", 409)


def _build_content(
    collaborators: FormulaCollaborators, preset_id: str, mode: str, content: FormulaContent
) -> Tuple[List[Dict[str, Any]], Dict[str, Any], Dict[str, Any], str]:
    try:
        loaded = collaborators.forms.load(preset_id, mode, content.variant)
    except FormUnavailable as exc:
        raise FormulaError("form_unavailable", str(exc), 422) from exc
    if not loaded.groups:
        raise FormulaError("formulas_not_declared", "This preset mode does not declare any formula groups", 422)

    declared_ids = {group.id for group in loaded.groups}
    selected = {item.id for item in content.groups}
    unknown = sorted(selected - declared_ids)
    if unknown:
        raise FormulaError("unknown_group", f"Unknown formula group: {', '.join(unknown)}", 422)

    declared: Dict[str, str] = {}
    for group in loaded.groups:
        if group.id in selected:
            for name in group.fields:
                if name in loaded.fields:
                    declared.setdefault(name, group.id)

    values: Dict[str, Any] = {}
    for key, value in content.values.items():
        owner = owning_field(key, declared)
        if owner is not None:
            values[key] = value
    if not values:
        raise FormulaError("formula_empty", "No value in the request belongs to a selected formula group", 422)

    signatures = {name: field_signature(loaded.fields[name]) for name in declared if name in values}
    for name, claimed in content.signatures.items():
        if name in signatures and claimed != signatures[name]:
            raise FormulaError(
                "signature_mismatch", f"The signature for '{name}' does not match the preset's field", 422
            )

    groups = []
    for group in loaded.groups:
        if group.id not in selected:
            continue
        present = [name for name in group.fields if name in signatures]
        if present:
            groups.append({"id": group.id, "label": group.label, "fields": present})
    return groups, values, signatures, loaded.preset_version


def _apply_content(
    collaborators: FormulaCollaborators, formula: Formula, content: FormulaContent
) -> Formula:
    groups, values, signatures, preset_version = _build_content(
        collaborators, formula.preset_id, formula.mode, content
    )
    return dataclasses.replace(
        formula,
        variant=content.variant,
        groups=groups,
        values=values,
        signatures=signatures,
        preset_version=content.preset_version or preset_version,
    )


def _limit_reached() -> FormulaError:
    return FormulaError(
        "formula_limit_reached",
        f"At most {MAX_FORMULAS_PER_SCOPE} formulas can be saved per preset and mode",
        409,
    )


def list_formulas(collaborators: FormulaCollaborators, owner_id: str, preset_id: str, mode: str) -> List[Formula]:
    return collaborators.repository.list_for_owner(owner_id, preset_id, mode)


def get_formula(collaborators: FormulaCollaborators, owner_id: str, formula_id: str) -> Formula:
    formula = collaborators.repository.get_for_owner(owner_id, formula_id)
    if formula is None:
        raise not_found()
    return formula


def create_formula(collaborators: FormulaCollaborators, owner_id: str, request: CreateFormulaRequest) -> Formula:
    draft = Formula(
        id="",
        owner_id=owner_id,
        preset_id=request.preset_id,
        mode=request.mode,
        name=request.name,
        note=request.note or None,
    )
    draft = _apply_content(collaborators, draft, request)
    try:
        return collaborators.repository.create(draft, MAX_FORMULAS_PER_SCOPE)
    except FormulaNameTaken as exc:
        raise _name_taken(request.name) from exc
    except FormulaLimitReached as exc:
        raise _limit_reached() from exc


def update_formula(
    collaborators: FormulaCollaborators, owner_id: str, formula_id: str, request: UpdateFormulaRequest
) -> Formula:
    formula = get_formula(collaborators, owner_id, formula_id)
    if request.name is not None:
        formula = dataclasses.replace(formula, name=request.name)
    if "note" in request.model_fields_set:
        formula = dataclasses.replace(formula, note=request.note or None)
    if request.content is not None:
        formula = _apply_content(collaborators, formula, request.content)
    try:
        updated = collaborators.repository.update(formula)
    except FormulaNameTaken as exc:
        raise _name_taken(formula.name) from exc
    if updated is None:
        raise not_found()
    return updated


def duplicate_formula(collaborators: FormulaCollaborators, owner_id: str, formula_id: str) -> Formula:
    formula = get_formula(collaborators, owner_id, formula_id)
    taken = {name.casefold() for name in collaborators.repository.names_for_owner(
        owner_id, formula.preset_id, formula.mode
    )}
    name = f"{formula.name} copy"
    index = 2
    while name.casefold() in taken:
        name = f"{formula.name} copy {index}"
        index += 1
    try:
        return collaborators.repository.create(dataclasses.replace(formula, name=name), MAX_FORMULAS_PER_SCOPE)
    except FormulaNameTaken as exc:
        raise _name_taken(name) from exc
    except FormulaLimitReached as exc:
        raise _limit_reached() from exc


def delete_formula(collaborators: FormulaCollaborators, owner_id: str, formula_id: str) -> None:
    if not collaborators.repository.delete(owner_id, formula_id):
        raise not_found()
