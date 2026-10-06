from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

import yaml

from src.platform.imaging.filters import (
    MAX_STEPS,
    PREFERRED_CUBE_SIZES,
    COLOUR,
    SPATIAL,
    Cube,
    CubeError,
    OpSpec,
    StepIssue,
    is_noop,
    read_cube,
    validate_steps,
)

SCHEMA_VERSION = 1
FILTER_FILE = "filter.yml"
MAX_FILE_BYTES = 64 * 1024
ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
LUT_PATTERN = re.compile(r"^[^/\\.][^/\\]*\.cube$")
NAME_MAX = 24
DEFAULT_GROUP = "Colour"
BUILTIN_GROUPS = ["Colour", "Film", "Black & white"]

SOURCE_BUILTIN = "builtin"
SOURCE_LOCAL = "local"
SOURCE_PLUGIN = "plugin"
SOURCE_MINE = "mine"

ERROR = "error"
WARNING = "warning"
NOTE = "note"

ALLOWED_KEYS = {
    "schema", "id", "name", "description", "group", "order", "intensity", "tags",
    "author", "license", "credit", "lut", "steps",
}


@dataclass(frozen=True)
class Finding:
    level: str
    rule: str
    path: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}: {self.message}" if self.path else self.message


def error(rule: str, path: str, message: str) -> Finding:
    return Finding(ERROR, rule, path, message)


def warning(rule: str, path: str, message: str) -> Finding:
    return Finding(WARNING, rule, path, message)


def from_step_issue(issue: StepIssue) -> Finding:
    return Finding(ERROR, issue.rule, issue.path, issue.message)


@dataclass
class FilterDefinition:
    id: str
    public_id: str
    name: str
    description: str
    group: str
    order: int
    intensity: int
    tags: List[str]
    author: str
    license: str
    credit: str
    steps: List[Dict[str, Any]]
    source: str
    directory: str
    plugin_id: Optional[str] = None
    overrides: bool = False
    lut: Optional[str] = None
    lut_size: Optional[int] = None
    revision: str = ""
    findings: List[Finding] = field(default_factory=list)

    @property
    def lut_path(self) -> Optional[Path]:
        return Path(self.directory) / self.lut if self.lut else None


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def revision_of(steps: Sequence[Mapping[str, Any]], extra: str = "") -> str:
    return hashlib.sha1((canonical(list(steps)) + extra).encode("utf-8")).hexdigest()[:8]


def step_op_ids(steps: Sequence[Mapping[str, Any]]) -> List[str]:
    seen: List[str] = []
    for step in steps:
        op_id = step.get("op") if isinstance(step, Mapping) else None
        if isinstance(op_id, str) and op_id not in seen:
            seen.append(op_id)
    return seen


def kinds_of(steps: Sequence[Mapping[str, Any]], ops: Mapping[str, OpSpec]) -> Dict[str, int]:
    counts = {COLOUR: 0, SPATIAL: 0}
    for step in steps:
        spec = ops.get(step.get("op"))
        if spec is not None:
            counts[spec.kind] += 1
    return counts


def _text(data: Mapping[str, Any], key: str, limit: int, findings: List[Finding]) -> str:
    value = data.get(key)
    if value is None:
        return ""
    if not isinstance(value, str):
        findings.append(error("schema", key, "must be a string"))
        return ""
    if len(value) > limit:
        findings.append(error("schema", key, f"is {len(value)} characters; the limit is {limit}"))
    return value


def _int_field(
    data: Mapping[str, Any], key: str, lo: int, hi: int, default: int, findings: List[Finding]
) -> int:
    if key not in data:
        return default
    value = data[key]
    if isinstance(value, bool) or not isinstance(value, int):
        findings.append(error("schema", key, "must be a whole number"))
        return default
    if not lo <= value <= hi:
        findings.append(error("schema", key, f"{value} is outside {lo}..{hi}"))
    return value


def validate_document(
    data: Any,
    ops: Mapping[str, OpSpec],
    directory_name: Optional[str] = None,
) -> "tuple[Dict[str, Any], List[Finding]]":
    findings: List[Finding] = []
    if not isinstance(data, dict):
        return {}, [error("schema", "", "the file must be a YAML mapping")]
    for key in data:
        if key not in ALLOWED_KEYS:
            findings.append(error("schema", str(key), "unknown key"))
    if data.get("schema") != SCHEMA_VERSION or isinstance(data.get("schema"), bool):
        findings.append(error("schema", "schema", f"must be {SCHEMA_VERSION}"))

    filter_id = data.get("id")
    if not isinstance(filter_id, str) or not filter_id:
        findings.append(error("schema", "id", "is required and must be a string"))
        filter_id = ""
    elif ":" in filter_id:
        findings.append(error("reserved_id", "id", "must not contain ':'; that is reserved for plugin and user filter ids"))
    elif not ID_PATTERN.match(filter_id):
        findings.append(error("schema", "id", "must match ^[a-z0-9][a-z0-9-]{0,39}$"))
    if filter_id and directory_name is not None and filter_id != directory_name:
        findings.append(error("id_dir", "id", f"directory '{directory_name}' must equal the filter id '{filter_id}'"))

    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        findings.append(error("schema", "name", "is required and must be a non-empty string"))
        name = ""
    elif len(name) > NAME_MAX:
        findings.append(warning("name_len", "name", f"is {len(name)} characters; {NAME_MAX} fit under a thumbnail"))

    description = _text(data, "description", 240, findings)
    group = data.get("group", DEFAULT_GROUP)
    if not isinstance(group, str) or not 1 <= len(group) <= NAME_MAX:
        findings.append(error("schema", "group", f"must be 1 to {NAME_MAX} characters"))
        group = DEFAULT_GROUP
    order = _int_field(data, "order", 0, 9999, 0, findings)
    intensity = _int_field(data, "intensity", 0, 100, 100, findings)

    tags = data.get("tags", [])
    if not isinstance(tags, list) or len(tags) > 12 or any(not isinstance(t, str) or len(t) > 24 for t in tags):
        findings.append(error("schema", "tags", "must be a list of at most 12 strings of at most 24 characters"))
        tags = []

    author = _text(data, "author", 80, findings)
    license_ = _text(data, "license", 80, findings)
    credit = _text(data, "credit", 200, findings)

    lut = data.get("lut")
    if lut is not None and not isinstance(lut, str):
        findings.append(error("schema", "lut", "must be a string"))
        lut = None
    if lut and not license_:
        findings.append(error("lut_license", "lut", "a filter that ships a .cube must declare license:"))

    steps = data.get("steps")
    if steps is None:
        findings.append(error("schema", "steps", "is required"))
        steps = []
    else:
        findings.extend(from_step_issue(issue) for issue in validate_steps(steps, ops))
        if not isinstance(steps, list):
            steps = []

    document = {
        "id": filter_id,
        "name": name,
        "description": description,
        "group": group,
        "order": order,
        "intensity": intensity,
        "tags": list(tags),
        "author": author,
        "license": license_,
        "credit": credit,
        "lut": lut,
        "steps": [dict(step) for step in steps if isinstance(step, dict)],
    }
    return document, findings


def check_lut(directory: Path, lut: str, findings: List[Finding]) -> Optional[Cube]:
    if not LUT_PATTERN.match(lut):
        findings.append(error("lut_path", "lut", "must be a plain file name ending in .cube inside the filter directory"))
        return None
    target = directory / lut
    try:
        resolved = target.resolve()
    except OSError:
        findings.append(error("lut_path", "lut", "could not be resolved"))
        return None
    if directory.resolve() not in resolved.parents:
        findings.append(error("lut_path", "lut", "escapes the filter directory"))
        return None
    if not resolved.is_file():
        findings.append(error("lut_missing", "lut", f"file '{lut}' does not exist"))
        return None
    try:
        cube = read_cube(resolved)
    except CubeError as exc:
        findings.append(error("lut_format", "lut", str(exc)))
        return None
    if cube.size not in PREFERRED_CUBE_SIZES:
        findings.append(
            warning("lut_size", "lut", f"cube size {cube.size} is not one of {', '.join(map(str, PREFERRED_CUBE_SIZES))}")
        )
    return cube


def has_python_impl(spec: OpSpec) -> bool:
    return spec.source == "core" or bool(spec.python)


def load_filter_dir(
    directory: Path,
    source: str,
    ops: Mapping[str, OpSpec],
    plugin_id: Optional[str] = None,
) -> "tuple[Optional[FilterDefinition], List[Finding]]":
    path = directory / FILTER_FILE
    try:
        size = path.stat().st_size
    except OSError as exc:
        return None, [error("parse", FILTER_FILE, f"could not read file: {exc}")]
    if size > MAX_FILE_BYTES:
        return None, [error("parse", FILTER_FILE, f"file is {size} bytes; the limit is {MAX_FILE_BYTES}")]
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return None, [error("parse", FILTER_FILE, f"could not parse YAML: {exc}")]

    document, findings = validate_document(data if data is not None else {}, ops, directory.name)

    cube: Optional[Cube] = None
    if document.get("lut"):
        cube = check_lut(directory, document["lut"], findings)

    if not any(f.level == ERROR for f in findings):
        steps = document["steps"]
        enabled = [s for s in steps if s.get("enabled", True) is not False]
        if not document["lut"] and not any(not is_noop(ops[s["op"]], s) for s in enabled):
            findings.append(warning("no_effect", "steps", "the filter has no LUT and every step is a no-op at its defaults"))
        for step in steps:
            spec = ops[step["op"]]
            if not has_python_impl(spec):
                findings.append(warning("op_no_backend", "steps", f"op '{spec.id}' has no Python implementation; editor only"))

    if any(f.level == ERROR for f in findings):
        return None, findings

    steps = document["steps"]
    lut_digest = ""
    if cube is not None:
        lut_digest = hashlib.sha1(cube.data.tobytes()).hexdigest()
    public_id = f"{plugin_id}:{document['id']}" if source == SOURCE_PLUGIN and plugin_id else document["id"]
    definition = FilterDefinition(
        id=document["id"],
        public_id=public_id,
        name=document["name"],
        description=document["description"],
        group=document["group"],
        order=document["order"],
        intensity=document["intensity"],
        tags=document["tags"],
        author=document["author"],
        license=document["license"],
        credit=document["credit"],
        steps=steps,
        source=source,
        directory=str(directory),
        plugin_id=plugin_id,
        lut=document["lut"],
        lut_size=cube.size if cube is not None else None,
        revision=revision_of(steps, lut_digest),
        findings=findings,
    )
    return definition, findings


FILTER_JSON_SCHEMA: Dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "PotionUI filter",
    "type": "object",
    "additionalProperties": False,
    "required": ["schema", "id", "name", "steps"],
    "properties": {
        "schema": {"const": SCHEMA_VERSION},
        "id": {"type": "string", "pattern": ID_PATTERN.pattern},
        "name": {"type": "string", "minLength": 1, "maxLength": NAME_MAX},
        "description": {"type": "string", "maxLength": 240},
        "group": {"type": "string", "minLength": 1, "maxLength": NAME_MAX},
        "order": {"type": "integer", "minimum": 0, "maximum": 9999},
        "intensity": {"type": "integer", "minimum": 0, "maximum": 100, "default": 100},
        "tags": {"type": "array", "maxItems": 12, "items": {"type": "string", "maxLength": 24}},
        "author": {"type": "string", "maxLength": 80},
        "license": {"type": "string", "maxLength": 80},
        "credit": {"type": "string", "maxLength": 200},
        "lut": {"type": "string", "pattern": LUT_PATTERN.pattern},
        "steps": {
            "type": "array",
            "maxItems": MAX_STEPS,
            "items": {
                "type": "object",
                "required": ["op"],
                "properties": {
                    "op": {"type": "string", "pattern": r"^([a-z0-9-]+\.)?[a-z][a-z0-9_]*$"},
                    "enabled": {"type": "boolean", "default": True},
                },
                "additionalProperties": {
                    "oneOf": [{"type": "number"}, {"type": "string"}, {"type": "boolean"}, {"type": "array"}]
                },
            },
        },
    },
    "allOf": [{"if": {"required": ["lut"]}, "then": {"required": ["license"]}}],
}
