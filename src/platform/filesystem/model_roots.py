import os
import sys
import threading
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Dict, List, Optional, Tuple, Union

from src.platform.filesystem.model_types import DIRECTORY_TO_MODEL_TYPE, MODEL_TYPE_TO_DIRECTORY, MODEL_TYPES

HOME_ROOT_ID = "home"

_WINDOWS_LONG_UNC_PREFIX = "\\\\?\\UNC\\"
_WINDOWS_LONG_PREFIX = "\\\\?\\"


class ModelRootError(Exception):
    pass


class NoWriteRootError(ModelRootError):
    def __init__(self, model_type: str):
        super().__init__(f"No write root is configured for model type '{model_type}'")
        self.model_type = model_type


class RootUnavailableError(ModelRootError):
    def __init__(self, root_id: str, state: str):
        super().__init__(f"Root '{root_id}' is {state}")
        self.root_id = root_id
        self.state = state


class RootReadOnlyError(ModelRootError):
    def __init__(self, root_id: str):
        super().__init__(f"Root '{root_id}' is read-only")
        self.root_id = root_id


class InvalidRelPathError(ModelRootError):
    def __init__(self, rel_path: str):
        super().__init__(f"'{rel_path}' is not a valid relative path")
        self.rel_path = rel_path


@dataclass(frozen=True)
class ModelRoot:
    id: str
    label: str
    path: Path
    kind: str
    read_only: bool
    case_insensitive: bool
    state: str
    state_reason: Optional[str]


@dataclass(frozen=True)
class TypeDir:
    root_id: str
    model_type: str
    path: Path
    position: int
    is_write: bool


@dataclass(frozen=True)
class LogicalLocation:
    root_id: str
    model_type: str
    rel_path: str

    @property
    def logical_ref(self) -> str:
        return f"{MODEL_TYPE_TO_DIRECTORY[self.model_type]}/{self.rel_path}"


def _strip_windows_long_prefix(raw: str) -> str:
    if raw.startswith(_WINDOWS_LONG_UNC_PREFIX):
        return "\\\\" + raw[len(_WINDOWS_LONG_UNC_PREFIX):]
    if raw.startswith(_WINDOWS_LONG_PREFIX):
        return raw[len(_WINDOWS_LONG_PREFIX):]
    return raw


def root_path_key(
    path: Union[str, "os.PathLike[str]"],
    *,
    case_insensitive: bool = False,
    os_name: Optional[str] = None,
) -> str:
    os_name = os.name if os_name is None else os_name
    windows = os_name == "nt"
    raw = str(path)
    if windows:
        raw = _strip_windows_long_prefix(raw)
    real = os_name == os.name
    if real:
        raw = os.path.abspath(raw)
    if windows:
        key = PureWindowsPath(raw).as_posix()
    else:
        key = PurePosixPath(os.path.normpath(raw) if real else raw).as_posix()
    key = unicodedata.normalize("NFC", key)
    if windows or case_insensitive:
        key = key.casefold()
    return key


def default_case_insensitive(os_name: Optional[str] = None) -> bool:
    os_name = os.name if os_name is None else os_name
    return os_name == "nt" or sys.platform == "darwin"


def paths_overlap(a_key: str, b_key: str) -> bool:
    """True when two `root_path_key()` results are equal or one contains the other."""
    return a_key == b_key or a_key.startswith(b_key + "/") or b_key.startswith(a_key + "/")


def probe_case_insensitive(path: Union[str, "os.PathLike[str]"]) -> bool:
    """Best-effort: does this filesystem fold case for `path`? Falls back to the
    platform default when the path has no letters to swap or the probe fails."""
    raw = str(path)
    swapped = raw.swapcase()
    if swapped == raw:
        return default_case_insensitive()
    try:
        return os.path.exists(raw) and os.path.samefile(raw, swapped)
    except OSError:
        return default_case_insensitive()


def _validate_rel_path(rel_path: str) -> PurePosixPath:
    normalized = rel_path.replace("\\", "/")
    parsed = PurePosixPath(normalized)
    if parsed.is_absolute() or not normalized or any(part == ".." for part in parsed.parts):
        raise InvalidRelPathError(rel_path)
    return parsed


validate_rel_path = _validate_rel_path


class RootProbe:

    def __init__(self, ttl_seconds: float = 15.0, timeout_seconds: float = 2.0):
        self._ttl = ttl_seconds
        self._timeout = timeout_seconds
        self._lock = threading.Lock()
        self._cache: Dict[str, Tuple[str, Optional[str], float]] = {}

    def state(self, root: ModelRoot) -> Tuple[str, Optional[str]]:
        now = time.monotonic()
        with self._lock:
            cached = self._cache.get(root.id)
        if cached is not None and (now - cached[2]) < self._ttl:
            return cached[0], cached[1]
        state, reason = self._probe_now(root)
        with self._lock:
            self._cache[root.id] = (state, reason, now)
        return state, reason

    def refresh(self, root_id: Optional[str] = None) -> None:
        with self._lock:
            if root_id is None:
                self._cache.clear()
            else:
                self._cache.pop(root_id, None)

    def _probe_now(self, root: ModelRoot) -> Tuple[str, Optional[str]]:
        result: Dict[str, Any] = {}

        def _target() -> None:
            try:
                result["exists"] = root.path.is_dir()
            except OSError as error:
                result["error"] = str(error)

        worker = threading.Thread(target=_target, daemon=True)
        worker.start()
        worker.join(self._timeout)
        if worker.is_alive():
            return "offline", "timed out probing the root"
        if "error" in result:
            return "unreadable", result["error"]
        if result.get("exists"):
            return "online", None
        return "offline", "the root path does not exist"


@dataclass(frozen=True)
class _Snapshot:
    roots: Tuple[ModelRoot, ...]
    roots_by_id: Dict[str, ModelRoot]
    type_dirs_by_type: Dict[str, Tuple[TypeDir, ...]]


class ModelRootResolver:

    def __init__(self, repository: Any, probe: RootProbe, base_dir: Path):
        self._repository = repository
        self._probe = probe
        self._base_dir = Path(base_dir)
        self._snapshot: Optional[_Snapshot] = None

    def invalidate(self) -> None:
        self._snapshot = None

    def _resolve_root_path(self, path: str) -> Path:
        candidate = Path(path)
        return candidate if candidate.is_absolute() else self._base_dir / candidate

    def _snapshot_or_build(self) -> _Snapshot:
        if self._snapshot is None:
            self._snapshot = self._build_snapshot()
        return self._snapshot

    def _build_snapshot(self) -> _Snapshot:
        roots = tuple(
            ModelRoot(
                id=row["id"],
                label=row["label"],
                path=self._resolve_root_path(row["path"]),
                kind=row["kind"],
                read_only=bool(row["read_only"]),
                case_insensitive=bool(row["case_insensitive"]),
                state=row["state"],
                state_reason=row["state_reason"],
            )
            for row in self._repository.list_roots()
        )
        roots_by_id = {root.id: root for root in roots}

        by_type: Dict[str, List[TypeDir]] = {}
        for row in self._repository.list_bindings():
            root = roots_by_id.get(row["root_id"])
            if root is None:
                continue
            subdir = row["subdir"] or ""
            rel_parts = _validate_rel_path(subdir).parts if subdir else ()
            type_dir_path = root.path.joinpath(*rel_parts) if rel_parts else root.path
            entry = TypeDir(
                root_id=root.id,
                model_type=row["model_type"],
                path=type_dir_path,
                position=row["position"],
                is_write=bool(row["is_write"]),
            )
            by_type.setdefault(entry.model_type, []).append(entry)

        type_dirs_by_type = {
            model_type: tuple(sorted(entries, key=lambda entry: entry.position))
            for model_type, entries in by_type.items()
        }
        return _Snapshot(roots=roots, roots_by_id=roots_by_id, type_dirs_by_type=type_dirs_by_type)

    def roots(self) -> Tuple[ModelRoot, ...]:
        return self._snapshot_or_build().roots

    def type_dirs(self, model_type: str, *, online_only: bool = True) -> List[TypeDir]:
        snapshot = self._snapshot_or_build()
        entries = snapshot.type_dirs_by_type.get(model_type, ())
        if not online_only:
            return list(entries)
        online = []
        for entry in entries:
            root = snapshot.roots_by_id.get(entry.root_id)
            if root is None:
                continue
            state, _ = self._probe.state(root)
            if state == "online":
                online.append(entry)
        return online

    def write_dir(self, model_type: str) -> TypeDir:
        snapshot = self._snapshot_or_build()
        entries = snapshot.type_dirs_by_type.get(model_type, ())
        write_entry = next((entry for entry in entries if entry.is_write), None)
        if write_entry is None:
            raise NoWriteRootError(model_type)
        root = snapshot.roots_by_id.get(write_entry.root_id)
        if root is None:
            raise RootUnavailableError(write_entry.root_id, "missing")
        if root.read_only:
            raise RootReadOnlyError(root.id)
        state, _ = self._probe.state(root)
        if state != "online":
            raise RootUnavailableError(root.id, state)
        return write_entry

    def to_logical(self, path: Union[str, Path]) -> Optional[LogicalLocation]:
        snapshot = self._snapshot_or_build()
        raw = str(path).replace("\\", "/")
        best: Optional[Tuple[int, LogicalLocation]] = None
        for entries in snapshot.type_dirs_by_type.values():
            for entry in entries:
                root = snapshot.roots_by_id.get(entry.root_id)
                if root is None:
                    continue
                bound = str(entry.path).replace("\\", "/").rstrip("/")
                key_bound = root_path_key(bound, case_insensitive=root.case_insensitive)
                key_raw = root_path_key(raw, case_insensitive=root.case_insensitive)
                if key_raw == key_bound:
                    rel = ""
                elif key_raw.startswith(key_bound + "/"):
                    rel = raw[len(bound) + 1:]
                else:
                    continue
                candidate = (len(key_bound), LogicalLocation(entry.root_id, entry.model_type, rel))
                if best is None or candidate[0] > best[0]:
                    best = candidate
        if best is not None:
            return best[1]

        try:
            real = os.path.realpath(str(path))
        except OSError:
            return None
        if root_path_key(real) == root_path_key(raw):
            return None
        return self.to_logical(real)

    def physical(self, loc: LogicalLocation) -> Path:
        snapshot = self._snapshot_or_build()
        entries = snapshot.type_dirs_by_type.get(loc.model_type, ())
        entry = next((candidate for candidate in entries if candidate.root_id == loc.root_id), None)
        if entry is None:
            raise RootUnavailableError(loc.root_id, "unbound")
        rel_parts = _validate_rel_path(loc.rel_path).parts
        return entry.path.joinpath(*rel_parts) if rel_parts else entry.path

    def parse_logical_ref(self, ref: str) -> Optional[Tuple[str, str]]:
        normalized = ref.replace("\\", "/").lstrip("/")
        if "/" not in normalized:
            return None
        type_dir, rel = normalized.split("/", 1)
        model_type = DIRECTORY_TO_MODEL_TYPE.get(type_dir)
        if model_type is None or not rel:
            return None
        return model_type, rel

    def home_dir(self) -> Path:
        snapshot = self._snapshot_or_build()
        root = snapshot.roots_by_id.get(HOME_ROOT_ID)
        if root is None:
            raise RootUnavailableError(HOME_ROOT_ID, "missing")
        return root.path

    def asset_dir(self, subdir: str) -> Path:
        home = self.home_dir()
        rel_parts = _validate_rel_path(subdir).parts
        return home.joinpath(*rel_parts) if rel_parts else home


def ensure_home_bindings(
    repository: Any,
    home_path: str,
    *,
    base_dir: Optional[Path] = None,
    case_insensitive: Optional[bool] = None,
    now: Optional[str] = None,
) -> None:
    from src.platform.database.rows import now_iso

    base_dir = Path.cwd() if base_dir is None else Path(base_dir)
    case_insensitive = default_case_insensitive() if case_insensitive is None else case_insensitive
    now = now_iso() if now is None else now

    resolved = home_path if os.path.isabs(home_path) else str(base_dir / home_path)
    path_key = root_path_key(resolved, case_insensitive=case_insensitive)

    repository.ensure_root(
        root_id=HOME_ROOT_ID,
        label="PotionUI models",
        path=home_path,
        path_key=path_key,
        kind="home",
        read_only=False,
        case_insensitive=case_insensitive,
        state="online",
        now=now,
    )

    max_positions = repository.max_position_by_type()
    write_types = repository.write_bound_types()

    for model_type in MODEL_TYPES:
        if repository.has_binding(HOME_ROOT_ID, model_type):
            continue
        position = max_positions.get(model_type, -1) + 1
        is_write = model_type not in write_types
        repository.insert_binding(
            root_id=HOME_ROOT_ID,
            model_type=model_type,
            subdir=MODEL_TYPE_TO_DIRECTORY[model_type],
            position=position,
            is_write=is_write,
        )
        max_positions[model_type] = position
        if is_write:
            write_types.add(model_type)
