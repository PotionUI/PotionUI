from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from src.platform.filesystem.model_types import HEADER_CLASSIFIED_TYPES, MODEL_TYPES

SUPPORTED_SCHEMA_VERSIONS = (1,)

SOURCE_MARKETPLACE = "marketplace"
SOURCE_LOCAL = "local"
SOURCE_PLUGIN = "plugin"

GENERIC_LAYOUT_ID = "generic"

READER_KINDS = frozenset(
    {
        "comfyui_extra_model_paths",
        "fooocus_config_txt",
        "swarmui_settings_fds",
        "stabilitymatrix_settings_json",
        "sdnext_config_json",
        "a1111_commandline_args",
    }
)

MARKER_KINDS = ("file", "dir")
FOLDER_BASES = ("models", "install")

MAX_FILE_BYTES = 256 * 1024

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:")
_WINDOWS_RESERVED = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{i}" for i in range(1, 10)}
    | {f"lpt{i}" for i in range(1, 10)}
)
_FORBIDDEN_SEGMENT_CHARS = set(':<>"|?*')
_GLOB_FORBIDDEN_SEGMENT_CHARS = set(':<>"|?')

_TOP_LEVEL_KEYS = frozenset(
    {
        "schema",
        "id",
        "label",
        "priority",
        "sources",
        "install_dirs",
        "markers",
        "min_marker_score",
        "min_folder_evidence",
        "match_on_folders",
        "variants",
        "models_root",
        "config_readers",
        "folders",
        "delegate",
    }
)
_MARKER_KEYS = frozenset({"path", "kind", "weight"})
_VARIANT_KEYS = frozenset({"label", "markers"})
_READER_KEYS = frozenset({"kind", "file"})
_FOLDER_KEYS = frozenset(
    {"path", "model_type", "key", "label", "base", "write", "scan_headers", "weight", "optional"}
)
_DELEGATE_KEYS = frozenset({"search", "max_candidates"})
_SOURCE_KEYS = frozenset({"title", "url"})


class ModelLayoutError(Exception):
    pass


@dataclass(frozen=True)
class LayoutMarker:
    path: str
    kind: str = "file"
    weight: int = 1


@dataclass(frozen=True)
class LayoutVariant:
    label: str
    markers: Tuple[LayoutMarker, ...]


@dataclass(frozen=True)
class LayoutReaderRef:
    kind: str
    file: Optional[str] = None


@dataclass(frozen=True)
class LayoutFolder:
    path: str
    model_type: str
    key: str
    label: str
    base: str = "models"
    write: bool = False
    scan_headers: Optional[bool] = None
    weight: int = 1
    optional: bool = False


@dataclass(frozen=True)
class LayoutDelegate:
    search: Tuple[str, ...]
    max_candidates: int = 32


@dataclass(frozen=True)
class ModelLayout:
    id: str
    label: str
    priority: int = 50
    sources: Tuple[Dict[str, str], ...] = ()
    install_dirs: Tuple[str, ...] = (".",)
    markers: Tuple[LayoutMarker, ...] = ()
    min_marker_score: int = 3
    min_folder_evidence: int = 3
    match_on_folders: bool = True
    variants: Tuple[LayoutVariant, ...] = ()
    models_root: Tuple[str, ...] = ("models",)
    config_readers: Tuple[LayoutReaderRef, ...] = ()
    folders: Tuple[LayoutFolder, ...] = ()
    delegate: Optional[LayoutDelegate] = None
    source: str = SOURCE_MARKETPLACE
    plugin_id: Optional[str] = None
    source_path: str = ""


def _err(issues: List[str], path: str, message: str) -> None:
    issues.append(f"{path}: {message}")


def _nfc_casefold(value: str) -> str:
    return unicodedata.normalize("NFC", value).casefold()


def _path_problem(value: Any, *, allow_dot: bool = False, glob: bool = False) -> Optional[str]:
    if not isinstance(value, str) or not value:
        return "must be a non-empty string"
    if value == ".":
        return None if allow_dot else "must not be '.'"
    if value.startswith("/"):
        return "must be relative (no leading '/')"
    if _WINDOWS_DRIVE_RE.match(value):
        return "must not start with a drive letter"
    if "\\" in value:
        return "must use '/' separators, not '\\'"
    if unicodedata.normalize("NFC", value) != value:
        return "must be NFC-normalised"
    forbidden = _GLOB_FORBIDDEN_SEGMENT_CHARS if glob else _FORBIDDEN_SEGMENT_CHARS
    for segment in value.split("/"):
        if segment == "":
            return "must not contain empty segments"
        if segment == ".":
            return "must not contain '.' segments"
        if segment == "..":
            return "must not contain '..'"
        if any(ord(ch) < 32 for ch in segment):
            return "must not contain control characters"
        bad = sorted(ch for ch in segment if ch in forbidden)
        if bad:
            return f"segment '{segment}' contains forbidden character(s) {''.join(bad)}"
        if segment != segment.rstrip(" ."):
            return f"segment '{segment}' must not end with a space or a dot"
        if segment.split(".", 1)[0].casefold() in _WINDOWS_RESERVED:
            return f"segment '{segment}' is a reserved Windows name"
    return None


def _check_path(issues: List[str], where: str, value: Any, **kwargs: Any) -> bool:
    problem = _path_problem(value, **kwargs)
    if problem:
        _err(issues, where, problem)
        return False
    return True


def _check_int(
    issues: List[str], data: Dict[str, Any], key: str, where: str, low: int, high: Optional[int] = None
) -> None:
    if key not in data:
        return
    value = data[key]
    if isinstance(value, bool) or not isinstance(value, int) or value < low or (high is not None and value > high):
        bound = f"between {low} and {high}" if high is not None else f">= {low}"
        _err(issues, f"{where}{key}", f"must be an integer {bound}")


def _check_bool(issues: List[str], data: Dict[str, Any], key: str, where: str) -> None:
    if key in data and not isinstance(data[key], bool):
        _err(issues, f"{where}{key}", "must be true or false")


def _check_unknown(issues: List[str], data: Dict[str, Any], allowed: frozenset, where: str) -> None:
    for key in sorted(str(k) for k in data if k not in allowed):
        _err(issues, f"{where}{key}", "unknown field")


def _validate_markers(issues: List[str], value: Any, where: str) -> None:
    if not isinstance(value, list):
        _err(issues, where, "must be a list")
        return
    seen = set()
    for i, entry in enumerate(value):
        path = f"{where}[{i}]"
        if not isinstance(entry, dict):
            _err(issues, path, "must be a mapping")
            continue
        _check_unknown(issues, entry, _MARKER_KEYS, f"{path}.")
        if _check_path(issues, f"{path}.path", entry.get("path")):
            folded = _nfc_casefold(entry["path"])
            if folded in seen:
                _err(issues, f"{path}.path", f"duplicate marker '{entry['path']}'")
            seen.add(folded)
        if entry.get("kind", "file") not in MARKER_KINDS:
            _err(issues, f"{path}.kind", f"must be one of {list(MARKER_KINDS)}")
        _check_int(issues, entry, "weight", f"{path}.", 1, 10)


def _validate_path_list(issues: List[str], value: Any, where: str, *, allow_dot: bool) -> None:
    if not isinstance(value, list) or not value:
        _err(issues, where, "must be a non-empty list")
        return
    seen = set()
    for i, entry in enumerate(value):
        if _check_path(issues, f"{where}[{i}]", entry, allow_dot=allow_dot):
            folded = _nfc_casefold(entry)
            if folded in seen:
                _err(issues, f"{where}[{i}]", f"duplicate entry '{entry}'")
            seen.add(folded)


def _segments(path: str) -> Tuple[str, ...]:
    return tuple(_nfc_casefold(part) for part in path.split("/") if part)


def _validate_folders(issues: List[str], value: Any) -> None:
    if not isinstance(value, list):
        _err(issues, "folders", "must be a list")
        return
    writers: Dict[str, int] = {}
    parsed: List[Tuple[int, str, Tuple[str, ...]]] = []
    for i, entry in enumerate(value):
        where = f"folders[{i}]"
        if not isinstance(entry, dict):
            _err(issues, where, "must be a mapping")
            continue
        _check_unknown(issues, entry, _FOLDER_KEYS, f"{where}.")
        path_ok = _check_path(issues, f"{where}.path", entry.get("path"))
        model_type = entry.get("model_type")
        if not isinstance(model_type, str) or model_type not in MODEL_TYPES:
            _err(issues, f"{where}.model_type", f"must be one of {list(MODEL_TYPES)}, got {model_type!r}")
            model_type = None
        for text_key in ("key", "label"):
            if text_key in entry and (not isinstance(entry[text_key], str) or not entry[text_key].strip()):
                _err(issues, f"{where}.{text_key}", "must be a non-empty string")
        base = entry.get("base", "models")
        if base not in FOLDER_BASES:
            _err(issues, f"{where}.base", f"must be one of {list(FOLDER_BASES)}")
            base = "models"
        _check_bool(issues, entry, "write", f"{where}.")
        _check_bool(issues, entry, "optional", f"{where}.")
        _check_bool(issues, entry, "scan_headers", f"{where}.")
        _check_int(issues, entry, "weight", f"{where}.", 1, 10)
        if entry.get("scan_headers") is True and model_type and model_type not in HEADER_CLASSIFIED_TYPES:
            _err(
                issues,
                f"{where}.scan_headers",
                f"only allowed for {sorted(HEADER_CLASSIFIED_TYPES)}, not '{model_type}'",
            )
        if entry.get("write") is True and model_type:
            writers[model_type] = writers.get(model_type, 0) + 1
        if path_ok:
            parsed.append((i, base, _segments(entry["path"])))
    for model_type, count in sorted(writers.items()):
        if count > 1:
            _err(issues, "folders", f"more than one 'write: true' for model_type '{model_type}'")
    for a in range(len(parsed)):
        for b in range(a + 1, len(parsed)):
            ia, base_a, seg_a = parsed[a]
            ib, base_b, seg_b = parsed[b]
            if base_a != base_b:
                continue
            if seg_a == seg_b:
                _err(issues, f"folders[{ib}].path", f"collides with folders[{ia}] under case folding")
            elif seg_a[: len(seg_b)] == seg_b:
                _err(issues, f"folders[{ia}].path", f"is nested inside folders[{ib}]")
            elif seg_b[: len(seg_a)] == seg_a:
                _err(issues, f"folders[{ib}].path", f"is nested inside folders[{ia}]")


def _validate_delegate(issues: List[str], value: Any) -> None:
    if not isinstance(value, dict):
        _err(issues, "delegate", "must be a mapping")
        return
    _check_unknown(issues, value, _DELEGATE_KEYS, "delegate.")
    search = value.get("search")
    if not isinstance(search, list) or not search:
        _err(issues, "delegate.search", "must be a non-empty list of glob patterns")
    else:
        for i, pattern in enumerate(search):
            _check_path(issues, f"delegate.search[{i}]", pattern, glob=True)
    _check_int(issues, value, "max_candidates", "delegate.", 1, 256)


def validate_layout_dict(data: Any) -> List[str]:
    if not isinstance(data, dict):
        return ["layout file must be a YAML mapping at the top level"]
    issues: List[str] = []
    _check_unknown(issues, data, _TOP_LEVEL_KEYS, "")

    schema = data.get("schema")
    if isinstance(schema, bool) or not isinstance(schema, int) or schema not in SUPPORTED_SCHEMA_VERSIONS:
        _err(issues, "schema", f"must be one of {list(SUPPORTED_SCHEMA_VERSIONS)}, got {schema!r}")

    layout_id = data.get("id")
    if not isinstance(layout_id, str) or not _ID_RE.match(layout_id):
        _err(issues, "id", "must match ^[a-z0-9][a-z0-9-]{0,39}$")
    elif layout_id == GENERIC_LAYOUT_ID:
        _err(issues, "id", f"'{GENERIC_LAYOUT_ID}' is reserved")

    label = data.get("label")
    if not isinstance(label, str) or not label.strip():
        _err(issues, "label", "must be a non-empty string")

    _check_int(issues, data, "priority", "", 0, 100)
    _check_int(issues, data, "min_marker_score", "", 1)
    _check_int(issues, data, "min_folder_evidence", "", 1)
    _check_bool(issues, data, "match_on_folders", "")

    sources = data.get("sources", [])
    if not isinstance(sources, list):
        _err(issues, "sources", "must be a list")
    else:
        for i, entry in enumerate(sources):
            where = f"sources[{i}]"
            if not isinstance(entry, dict):
                _err(issues, where, "must be a mapping")
                continue
            _check_unknown(issues, entry, _SOURCE_KEYS, f"{where}.")
            for text_key in ("title", "url"):
                if not isinstance(entry.get(text_key), str) or not entry[text_key].strip():
                    _err(issues, f"{where}.{text_key}", "must be a non-empty string")

    if "install_dirs" in data:
        _validate_path_list(issues, data["install_dirs"], "install_dirs", allow_dot=True)
    if "models_root" in data:
        _validate_path_list(issues, data["models_root"], "models_root", allow_dot=True)

    has_markers = "markers" in data
    if has_markers:
        _validate_markers(issues, data["markers"], "markers")

    variants = data.get("variants", [])
    if not isinstance(variants, list):
        _err(issues, "variants", "must be a list")
    else:
        for i, entry in enumerate(variants):
            where = f"variants[{i}]"
            if not isinstance(entry, dict):
                _err(issues, where, "must be a mapping")
                continue
            _check_unknown(issues, entry, _VARIANT_KEYS, f"{where}.")
            if not isinstance(entry.get("label"), str) or not entry["label"].strip():
                _err(issues, f"{where}.label", "must be a non-empty string")
            _validate_markers(issues, entry.get("markers"), f"{where}.markers")
            if entry.get("markers") == []:
                _err(issues, f"{where}.markers", "must not be empty")

    readers = data.get("config_readers", [])
    if not isinstance(readers, list):
        _err(issues, "config_readers", "must be a list")
    else:
        for i, entry in enumerate(readers):
            where = f"config_readers[{i}]"
            if not isinstance(entry, dict):
                _err(issues, where, "must be a mapping")
                continue
            _check_unknown(issues, entry, _READER_KEYS, f"{where}.")
            kind = entry.get("kind")
            if kind not in READER_KINDS:
                _err(issues, f"{where}.kind", f"unknown reader kind {kind!r}; known: {sorted(READER_KINDS)}")
            if "file" in entry:
                _check_path(issues, f"{where}.file", entry["file"])

    folders = data.get("folders")
    has_folders = "folders" in data and folders not in (None, [])
    if "folders" in data:
        _validate_folders(issues, folders if folders is not None else [])

    has_delegate = "delegate" in data
    if has_delegate:
        _validate_delegate(issues, data["delegate"])
        if has_folders:
            _err(issues, "delegate", "is only allowed on a layout without folders")

    if not has_delegate and not has_folders:
        _err(issues, "folders", "a layout needs folders (or a delegate)")

    if not has_delegate and not (has_markers and data.get("markers")):
        _err(issues, "markers", "a layout needs markers (or a delegate)")

    return issues


def _parse_markers(entries: List[Dict[str, Any]]) -> Tuple[LayoutMarker, ...]:
    return tuple(
        LayoutMarker(path=e["path"], kind=e.get("kind", "file"), weight=e.get("weight", 1)) for e in entries
    )


def parse_layout(
    data: Dict[str, Any], source_path: str = "", source: str = SOURCE_MARKETPLACE, plugin_id: Optional[str] = None
) -> ModelLayout:
    issues = validate_layout_dict(data)
    if issues:
        raise ModelLayoutError("; ".join(issues))
    folders = tuple(
        LayoutFolder(
            path=f["path"],
            model_type=f["model_type"],
            key=f.get("key") or f["path"],
            label=f.get("label") or f["path"].rsplit("/", 1)[-1],
            base=f.get("base", "models"),
            write=f.get("write", False),
            scan_headers=f.get("scan_headers"),
            weight=f.get("weight", 1),
            optional=f.get("optional", False),
        )
        for f in data.get("folders") or []
    )
    delegate = None
    if "delegate" in data:
        raw = data["delegate"]
        delegate = LayoutDelegate(search=tuple(raw["search"]), max_candidates=raw.get("max_candidates", 32))
    return ModelLayout(
        id=data["id"],
        label=data["label"].strip(),
        priority=data.get("priority", 50),
        sources=tuple({"title": s["title"], "url": s["url"]} for s in data.get("sources", [])),
        install_dirs=tuple(data.get("install_dirs", ["."])),
        markers=_parse_markers(data.get("markers", [])),
        min_marker_score=data.get("min_marker_score", 3),
        min_folder_evidence=data.get("min_folder_evidence", 3),
        match_on_folders=data.get("match_on_folders", True),
        variants=tuple(
            LayoutVariant(label=v["label"].strip(), markers=_parse_markers(v["markers"]))
            for v in data.get("variants", [])
        ),
        models_root=tuple(data.get("models_root", ["models"])),
        config_readers=tuple(
            LayoutReaderRef(kind=r["kind"], file=r.get("file")) for r in data.get("config_readers", [])
        ),
        folders=folders,
        delegate=delegate,
        source=source,
        plugin_id=plugin_id,
        source_path=source_path,
    )
