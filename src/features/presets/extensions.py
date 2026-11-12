import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

from src.features.presets.mode_fields import mode_inventory
from src.features.presets.retired_modes import is_retired
from src.features.presets.schema import (
    ExtensionFieldGroup,
    ExtensionPipeAdd,
    PresetExtensionFile,
    PromptResourceSpec,
    _validate_prompt_resources,
    validate_extension_file,
    validate_field_list,
    validate_pipeline_file,
)
from src.features.presets.templates import FieldTemplate, ModeTemplate, PipeTemplate, PresetTemplate
from src.platform.filesystem.yaml_io import load_yaml_file
from src.platform.observability.logger import logger

BuildFields = Callable[[List[Dict[str, Any]], Path, str, Path], List[FieldTemplate]]
ModeChecks = Callable[[PresetTemplate, str, ModeTemplate], List[str]]


@dataclass
class PresetExtension:
    plugin_id: str
    target_preset_id: str
    modes: List[str]
    file: Path
    plugin_dir: Path
    declared_file: str

    @property
    def error_key(self) -> str:
        return (
            f"plugin '{self.plugin_id}' preset_extensions "
            f"(target '{self.target_preset_id}', file '{self.declared_file}')"
        )


def plugin_preset_extensions(manifests) -> List[PresetExtension]:
    extensions: List[PresetExtension] = []
    for manifest in sorted(manifests, key=lambda m: getattr(m, "id", "")):
        entries = getattr(manifest, "preset_extensions", None) or []
        plugin_dir = getattr(manifest, "plugin_dir", None)
        if not entries or not plugin_dir:
            continue
        base = Path(plugin_dir).resolve()
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            target, modes, file = entry.get("target"), entry.get("modes"), entry.get("file")
            if target and isinstance(modes, list) and modes and file:
                extensions.append(
                    PresetExtension(
                        plugin_id=manifest.id,
                        target_preset_id=target,
                        modes=list(modes),
                        file=base / file,
                        plugin_dir=base,
                        declared_file=file,
                    )
                )
    return extensions


def walk_fields(fields: Optional[List[FieldTemplate]]) -> Iterator[Tuple[FieldTemplate, List[FieldTemplate]]]:
    for field in list(fields or []):
        yield field, fields
        if isinstance(field.children, list):
            yield from walk_fields(field.children)


def _field_names(fields: Optional[List[FieldTemplate]]) -> List[str]:
    return [field.name for field, _ in walk_fields(fields) if field.name]


def _anchors(fields: Optional[List[FieldTemplate]]) -> List[Tuple[str, str]]:
    return [(field.type, field.anchor) for field, _ in walk_fields(fields) if field.anchor]


def duplicate_anchor_errors(fields: List[FieldTemplate], loc: str) -> List[str]:
    seen = set()
    errors = []
    for kind, anchor in _anchors(fields):
        if (kind, anchor) in seen:
            errors.append(f"{loc}: {kind} anchor '{anchor}' is declared more than once")
        seen.add((kind, anchor))
    return errors


def _mark_source(fields: List[FieldTemplate], plugin_id: str) -> None:
    for field, _ in walk_fields(fields):
        field.source_plugin = plugin_id


def contained(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root)
    except ValueError:
        return False
    return True


def read_extension_file(extension: PresetExtension) -> Tuple[Optional[PresetExtensionFile], List[str]]:
    if not contained(extension.file, extension.plugin_dir):
        return None, [f"file '{extension.declared_file}' resolves outside the plugin directory"]
    if not extension.file.is_file():
        return None, [f"file '{extension.declared_file}' not found"]
    try:
        data = load_yaml_file(extension.file) or {}
    except Exception as exc:
        return None, [f"{extension.declared_file}: failed to parse: {exc}"]
    return validate_extension_file(data, prefix=extension.declared_file)


def _group_field_dicts(
    group: ExtensionFieldGroup, index: int, extension: PresetExtension
) -> Tuple[List[Dict[str, Any]], List[str], Path]:
    prefix = f"{extension.declared_file}: fields[{index}]"
    root = extension.file.parent
    raw = group.fields
    if group.file is not None:
        path = root / group.file
        if not contained(path, extension.plugin_dir):
            return [], [f"{prefix}: file '{group.file}' resolves outside the plugin directory"], root
        if not path.is_file():
            return [], [f"{prefix}: file '{group.file}' not found"], root
        try:
            data = load_yaml_file(path) or {}
        except Exception as exc:
            return [], [f"{prefix}: file '{group.file}' failed to parse: {exc}"], root
        raw = data.get("fields") if isinstance(data, dict) else None
        if not isinstance(raw, list):
            return [], [f"{prefix}: file '{group.file}' has no `fields:` list"], root
        prefix = f"{prefix} -> {group.file}"
    validated, errors = validate_field_list(raw or [], prefix=prefix)
    if errors:
        return [], errors, root
    return [spec.model_dump() for spec in validated], [], root


def _build_groups(
    spec: PresetExtensionFile, extension: PresetExtension, build_fields: BuildFields
) -> Tuple[List[Tuple[ExtensionFieldGroup, List[FieldTemplate]]], List[str]]:
    groups = []
    problems: List[str] = []
    for index, group in enumerate(spec.fields):
        dicts, errors, root = _group_field_dicts(group, index, extension)
        if errors:
            problems.extend(errors)
            continue
        if not dicts:
            problems.append(f"{extension.declared_file}: fields[{index}]: adds no fields")
            continue
        try:
            templates = build_fields(dicts, root, f"{extension.declared_file}: fields[{index}]", extension.plugin_dir)
        except Exception as exc:
            problems.append(f"{extension.declared_file}: fields[{index}]: {exc}")
            continue
        _mark_source(templates, extension.plugin_id)
        groups.append((group, templates))
    return groups, problems


def _merge_field_group(
    form_fields: List[FieldTemplate], group: ExtensionFieldGroup, templates: List[FieldTemplate], loc: str
) -> List[str]:
    kind, anchor = group.into.kind, group.into.anchor
    containers = [field for field, _ in walk_fields(form_fields) if field.type == kind and field.anchor == anchor]
    if not containers:
        return [f"{loc}: no {kind} with anchor '{anchor}'"]
    if len(containers) > 1:
        return [f"{loc}: {kind} anchor '{anchor}' matches more than one {kind}"]
    container = containers[0]

    new_nodes = copy.deepcopy(templates)
    problems = []
    existing_names = set(_field_names(form_fields))
    new_names = _field_names(new_nodes)
    for name in sorted({n for n in new_names if n in existing_names}):
        problems.append(f"{loc}: field '{name}' already exists")
    for name in sorted({n for n in new_names if new_names.count(n) > 1}):
        problems.append(f"{loc}: field '{name}' is added more than once")
    existing_anchors = set(_anchors(form_fields))
    for node_kind, node_anchor in _anchors(new_nodes):
        if (node_kind, node_anchor) in existing_anchors:
            problems.append(f"{loc}: {node_kind} anchor '{node_anchor}' already exists")
    if problems:
        return problems

    if not isinstance(container.children, list):
        container.children = []
    reference = group.after if group.after is not None else group.before
    if reference is None:
        container.children.extend(new_nodes)
        return []
    hit = next(((field, parent) for field, parent in walk_fields(container.children) if field.name == reference), None)
    if hit is None:
        return [f"{loc}: no field '{reference}' in {kind} '{anchor}'"]
    field, parent = hit
    position = next(i for i, node in enumerate(parent) if node is field)
    if group.after is not None:
        position += 1
    parent[position:position] = new_nodes
    return []


def resolve_pipe(pipes: List[PipeTemplate], reference: str) -> Tuple[Optional[int], Optional[str]]:
    by_id = [i for i, pipe in enumerate(pipes) if pipe.id == reference]
    if len(by_id) == 1:
        return by_id[0], None
    if len(by_id) > 1:
        return None, f"pipe id '{reference}' is used more than once"
    by_name = [i for i, pipe in enumerate(pipes) if pipe.name == reference]
    if len(by_name) == 1:
        return by_name[0], None
    if len(by_name) > 1:
        return None, f"pipe '{reference}' is ambiguous: more than one pipe has that name, use its id"
    return None, f"no pipe '{reference}'"


def _wired_by_name(pipes: List[PipeTemplate], name: str) -> bool:
    return any(len(row) > 1 and row[1] == name for pipe in pipes for row in (pipe.input or []))


def _merge_pipes(mode: ModeTemplate, ops, loc: str, plugin_id: str) -> List[str]:
    pipes = mode.pipes
    problems = []
    for index, op in enumerate(ops):
        where = f"{loc} pipes[{index}]"
        if isinstance(op, ExtensionPipeAdd):
            node = PipeTemplate(**op.add.model_dump(), source_plugin=plugin_id)
            if node.id in {pipe.id for pipe in pipes if pipe.id} | {pipe.name for pipe in pipes}:
                problems.append(f"{where}: pipe id '{node.id}' is already used")
                continue
            if node.name in {pipe.name for pipe in pipes} and _wired_by_name(pipes, node.name):
                problems.append(
                    f"{where}: another '{node.name}' pipe would make the existing one, wired by name, ambiguous"
                )
                continue
            reference = op.after if op.after is not None else op.before
            if reference is None:
                pipes.append(node)
                continue
            position, problem = resolve_pipe(pipes, reference)
            if problem:
                problems.append(f"{where}: {problem}")
                continue
            pipes.insert(position + 1 if op.after is not None else position, node)
            continue

        position, problem = resolve_pipe(pipes, op.patch)
        if problem:
            problems.append(f"{where}: {problem}")
            continue
        pipe = pipes[position]
        params = {row[0] for row in (pipe.input or []) if row}
        clashes = [row[0] for row in op.input_add if row[0] in params]
        clashes += [key for key in op.configuration_add if key in (pipe.configuration or {})]
        if clashes:
            problems.append(f"{where}: pipe '{op.patch}' already sets {sorted(set(clashes))}")
            continue
        if op.input_add:
            pipe.input = [*(pipe.input or []), *[list(row) for row in op.input_add]]
        if op.configuration_add:
            pipe.configuration = {**(pipe.configuration or {}), **copy.deepcopy(op.configuration_add)}
    if problems:
        return problems
    _, errors = validate_pipeline_file(
        {
            "pipeline": [
                {
                    key: value
                    for key, value in {
                        "id": pipe.id,
                        "name": pipe.name,
                        "enabled": pipe.enabled,
                        "input": pipe.input,
                        "configuration": pipe.configuration,
                    }.items()
                    if value is not None
                }
                for pipe in pipes
            ]
        },
        prefix=f"{loc} merged pipeline",
    )
    return errors


def _merge_prompt_resources(
    target: PresetTemplate, modes: Dict[str, ModeTemplate], spec: PresetExtensionFile
) -> Tuple[Dict[str, List[Dict[str, Any]]], List[str]]:
    merged = copy.deepcopy(target.prompt_resources or {})
    if not spec.prompt_resources:
        return merged, []
    problems = []
    added = [entry.model_dump(exclude_none=True) for entry in spec.prompt_resources]
    for mode_name, mode in modes.items():
        names = mode_inventory(mode)
        for entry in added:
            if entry["field"] not in names:
                problems.append(f"prompt_resources: mode '{mode_name}' has no field '{entry['field']}'")
        entries = [*merged.get(mode_name, []), *copy.deepcopy(added)]
        problems.extend(_validate_prompt_resources({mode_name: [PromptResourceSpec(**e) for e in entries]}))
        merged[mode_name] = entries
    return merged, problems


def _merge_director_reference_fields(
    target: PresetTemplate, modes: Dict[str, ModeTemplate], spec: PresetExtensionFile
) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    merged = copy.deepcopy(target.vars)
    if not spec.director_reference_fields:
        return merged, []
    director = (merged or {}).get("video_director")
    if not isinstance(director, dict):
        return merged, [f"director_reference_fields: preset '{target.id}' has no Video Director"]
    director_modes = director.get("preset_modes")
    problems = []
    for mode_name, mode in modes.items():
        if isinstance(director_modes, list) and mode_name not in director_modes:
            problems.append(f"director_reference_fields: mode '{mode_name}' is not a Video Director mode")
            continue
        names = mode_inventory(mode)
        for name in spec.director_reference_fields:
            if name not in names:
                problems.append(f"director_reference_fields: mode '{mode_name}' has no field '{name}'")
        overrides = director.setdefault("preset_mode_overrides", {})
        override = overrides.setdefault(mode_name, {})
        current = list(override["reference_fields"] if "reference_fields" in override else director.get("reference_fields") or [])
        for name in spec.director_reference_fields:
            if name in current:
                problems.append(f"director_reference_fields: mode '{mode_name}' already lists '{name}'")
        override["reference_fields"] = current + list(spec.director_reference_fields)
    return merged, problems


def _apply_one(
    target: PresetTemplate, extension: PresetExtension, build_fields: BuildFields, mode_checks: ModeChecks
) -> List[str]:
    spec, problems = read_extension_file(extension)
    if problems:
        return problems
    retired = [name for name in extension.modes if name not in target.modes and is_retired(target.vars, name)]
    applicable = [name for name in extension.modes if name not in retired]
    if retired:
        logger.warning(
            f"{extension.error_key} lists retired mode(s) {', '.join(retired)} of preset '{target.id}'; skipping them"
        )
    if not applicable:
        return []
    missing = [name for name in applicable if name not in target.modes]
    if missing:
        return [f"preset '{target.id}' has no mode '{name}'" for name in missing]

    groups, problems = _build_groups(spec, extension, build_fields)
    if problems:
        return problems

    modes = {name: copy.deepcopy(target.modes[name]) for name in applicable}
    for mode_name, mode in modes.items():
        for form in mode.forms:
            loc = f"modes/{mode_name}/form '{form.name}'"
            for group, templates in groups:
                problems.extend(_merge_field_group(form.fields, group, templates, loc))
        problems.extend(_merge_pipes(mode, spec.pipes, f"modes/{mode_name}", extension.plugin_id))
    if problems:
        return problems

    for mode_name, mode in modes.items():
        problems.extend(mode_checks(target, mode_name, mode))
    prompt_resources, prompt_problems = _merge_prompt_resources(target, modes, spec)
    template_vars, director_problems = _merge_director_reference_fields(target, modes, spec)
    problems.extend(prompt_problems)
    problems.extend(director_problems)
    if problems:
        return problems

    target.modes.update(modes)
    target.prompt_resources = prompt_resources
    target.vars = template_vars
    return []


def apply_preset_extensions(
    presets: List[PresetTemplate],
    errors: Dict[str, List[str]],
    extensions: List[PresetExtension],
    build_fields: BuildFields,
    mode_checks: ModeChecks,
) -> None:
    by_id: Dict[str, PresetTemplate] = {}
    for preset in presets:
        by_id.setdefault(preset.id, preset)
    for extension in extensions:
        target = by_id.get(extension.target_preset_id)
        if target is None:
            continue
        problems = _apply_one(target, extension, build_fields, mode_checks)
        if problems:
            logger.warning(f"Rejected {extension.error_key}: {'; '.join(problems)}")
            errors.setdefault(extension.error_key, []).extend(problems)
