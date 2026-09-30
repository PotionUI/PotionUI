import os
from pathlib import Path
from typing import Any, Callable, Dict, List, NamedTuple, Optional, Sequence, Tuple

from src.features.models.locations_repository import ModelLocationsRepository
from src.platform.database.rows import now_iso
from src.platform.filesystem.model_roots import (
    HOME_ROOT_ID,
    ModelRoot,
    ModelRootResolver,
    RootProbe,
    binding_subdir_key,
    default_case_insensitive,
    paths_overlap,
    probe_case_insensitive,
    root_path_key,
    validate_rel_path,
)
from src.platform.filesystem.model_roots_repository import ModelRootRepository
from src.platform.filesystem.model_types import HEADER_CLASSIFIED_TYPES, MODEL_TYPE_TO_DIRECTORY, MODEL_TYPES
from src.platform.settings.repository import SettingRepository
from src.platform.util.ids import generate_ulid


class BindingSpec(NamedTuple):
    model_type: str
    subdir: str
    scan_headers: Optional[bool] = None


class ModelRootsError(Exception):
    code = "model_roots_error"

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _require_header_type(model_type: str, subdir: str) -> None:
    if model_type not in HEADER_CLASSIFIED_TYPES:
        raise InvalidBindingError(model_type, subdir)


class RootNotFoundError(ModelRootsError):
    code = "model_roots_not_found"

    def __init__(self, root_id: str):
        super().__init__(f"Root '{root_id}' was not found")
        self.root_id = root_id


class DuplicateRootError(ModelRootsError):
    code = "model_roots_duplicate"

    def __init__(self, path: str):
        super().__init__(f"'{path}' is already a model root")
        self.path = path


class RootOverlapError(ModelRootsError):
    code = "model_roots_overlap"

    def __init__(self, root_id: str, label: str, existing_path: str):
        super().__init__(f"overlaps '{existing_path}' on root '{label}'")
        self.root_id = root_id
        self.existing_path = existing_path


class RootOfflineError(ModelRootsError):
    code = "model_roots_offline"

    def __init__(self, root_id: str, label: str, state: str):
        super().__init__(f"Root '{label}' is {state}")
        self.root_id = root_id
        self.state = state


class RootReadOnlyRefusalError(ModelRootsError):
    code = "model_roots_read_only"

    def __init__(self, root_id: str, label: str):
        super().__init__(f"Root '{label}' is read-only")
        self.root_id = root_id


class WriteProbeFailedError(ModelRootsError):
    code = "model_roots_write_probe_failed"

    def __init__(self, root_id: str, error: str):
        super().__init__(error)
        self.root_id = root_id


class NoWriteSuccessorError(ModelRootsError):
    code = "model_roots_no_write_successor"

    def __init__(self, model_type: str):
        super().__init__(f"No other writable root can take over writes for '{model_type}'")
        self.model_type = model_type


class GenerationActiveError(ModelRootsError):
    code = "model_roots_generation_active"

    def __init__(self):
        super().__init__("A generation is currently running or queued")


class HomeProtectedError(ModelRootsError):
    code = "model_roots_home_protected"

    def __init__(self, root_id: str, label: str):
        super().__init__(f"Root '{label}' is the built-in home root and is protected")
        self.root_id = root_id


class BindingNestedError(ModelRootsError):
    code = "model_roots_binding_nested"

    def __init__(self, first: str, second: str):
        super().__init__(f"'{first}' and '{second}' overlap: folders bound on one root cannot contain each other")
        self.first = first
        self.second = second


class DuplicateBindingError(ModelRootsError):
    code = "model_roots_duplicate_binding"

    def __init__(self, model_type: str, subdir: str):
        super().__init__(f"'{subdir}' is already bound for '{model_type}' on this root")
        self.model_type = model_type
        self.subdir = subdir


class InvalidBindingError(ModelRootsError):
    code = "model_roots_invalid_binding"

    def __init__(self, model_type: str, subdir: str):
        super().__init__(f"'{subdir}' is not a usable binding for '{model_type}'")
        self.model_type = model_type
        self.subdir = subdir


def _write_probe(directory: Path) -> Optional[str]:
    probe_path = directory / f".potionui-write-probe-{generate_ulid()}"
    try:
        directory.mkdir(parents=True, exist_ok=True)
        probe_path.write_bytes(b"")
        probe_path.unlink()
        return None
    except OSError as error:
        return str(error)


class ModelRootsManager:

    def __init__(
        self,
        repository: ModelRootRepository,
        resolver: ModelRootResolver,
        probe: RootProbe,
        indexing_coordinator: Any,
        setting_repository: Optional[SettingRepository] = None,
        locations_repository: Optional[ModelLocationsRepository] = None,
        generation_active: Optional[Callable[[], bool]] = None,
        base_dir: Optional[Path] = None,
    ):
        self._repository = repository
        self._resolver = resolver
        self._probe = probe
        self._indexing = indexing_coordinator
        self._settings = setting_repository or SettingRepository()
        self._locations = locations_repository or ModelLocationsRepository()
        self._generation_active = generation_active or (lambda: False)
        self._base_dir = Path(base_dir) if base_dir is not None else Path.cwd()

    @property
    def resolver(self) -> ModelRootResolver:
        return self._resolver

    def _resolve_base(self, path: str) -> Path:
        candidate = Path(path)
        return candidate if candidate.is_absolute() else self._base_dir / candidate

    def _binding_path(self, root_path: Path, subdir: str) -> Path:
        if not subdir:
            return root_path
        rel_parts = validate_rel_path(subdir).parts
        return root_path.joinpath(*rel_parts) if rel_parts else root_path

    def _to_model_root(self, row: Dict[str, Any]) -> ModelRoot:
        return ModelRoot(
            id=row["id"],
            label=row["label"],
            path=self._resolve_base(row["path"]),
            kind=row["kind"],
            read_only=bool(row["read_only"]),
            case_insensitive=bool(row["case_insensitive"]),
            state=row["state"],
            state_reason=row.get("state_reason"),
        )

    def _existing_bindings(self, exclude_root_id: Optional[str] = None) -> List[Tuple[str, Path]]:
        pairs: List[Tuple[str, Path]] = []
        seen = set()
        for model_type in MODEL_TYPES:
            for type_dir in self._resolver.type_dirs(model_type, online_only=False):
                if type_dir.root_id == exclude_root_id:
                    continue
                key = (type_dir.root_id, str(type_dir.path))
                if key in seen:
                    continue
                seen.add(key)
                pairs.append((type_dir.root_id, type_dir.path))
        return pairs

    def _overlap_conflicts(
        self, candidate_paths: Sequence[Path], *, exclude_root_id: Optional[str] = None
    ) -> List[Tuple[str, Path]]:
        conflicts: List[Tuple[str, Path]] = []
        for root_id, existing_path in self._existing_bindings(exclude_root_id=exclude_root_id):
            existing_key = root_path_key(existing_path)
            for candidate in candidate_paths:
                candidate_key = root_path_key(candidate)
                if paths_overlap(candidate_key, existing_key):
                    conflicts.append((root_id, existing_path))
                    break
        return conflicts

    def _validate_binding_targets(self, root_path: Path, bindings: Sequence[BindingSpec]) -> None:
        for spec in bindings:
            if spec.scan_headers:
                _require_header_type(spec.model_type, spec.subdir)
            target = self._binding_path(root_path, spec.subdir)
            if not target.is_dir() or not os.access(target, os.R_OK):
                raise InvalidBindingError(spec.model_type, spec.subdir)

    def _nesting_key(self, target: Path, case_insensitive: bool) -> Tuple[str, str]:
        try:
            real = os.path.realpath(target)
        except OSError:
            real = str(target)
        return root_path_key(target, case_insensitive=case_insensitive), root_path_key(
            real, case_insensitive=case_insensitive
        )

    def _check_binding_nesting(
        self,
        root_row: Dict[str, Any],
        root_path: Path,
        specs: Sequence[BindingSpec],
    ) -> None:
        case_insensitive = bool(root_row["case_insensitive"]) if root_row is not None else default_case_insensitive()
        existing = [] if root_row is None else self._repository.bindings_for_root(root_row["id"])
        known: Dict[Tuple[str, str], str] = {
            (b["model_type"], binding_subdir_key(b["subdir"], case_insensitive=case_insensitive)): b["subdir"]
            for b in existing
        }
        seen: Dict[Tuple[str, str], str] = {}
        fresh: List[Tuple[str, str]] = []
        for spec in specs:
            key = (spec.model_type, binding_subdir_key(spec.subdir, case_insensitive=case_insensitive))
            if key in seen:
                raise DuplicateBindingError(spec.model_type, spec.subdir)
            seen[key] = spec.subdir
            if key not in known:
                fresh.append((spec.model_type, spec.subdir))

        others = [(b["model_type"], b["subdir"]) for b in existing]
        placed = list(others)
        for model_type, subdir in fresh:
            candidate = self._nesting_key(self._binding_path(root_path, subdir), case_insensitive)
            for other_type, other_subdir in placed:
                other = self._nesting_key(self._binding_path(root_path, other_subdir), case_insensitive)
                if any(paths_overlap(a, b) for a, b in zip(candidate, other)):
                    raise BindingNestedError(subdir, other_subdir)
            placed.append((model_type, subdir))

    def _label_for(self, root_id: str) -> str:
        row = self._repository.get_root(root_id)
        return row["label"] if row is not None else root_id

    def _types_bound_by(self, root_ids: Sequence[str]) -> List[str]:
        types = set()
        for row in self._repository.list_bindings():
            if row["root_id"] in root_ids:
                types.add(row["model_type"])
        return sorted(types)

    def _write_successor(self, model_type: str, leaving_root_id: str) -> Optional[str]:
        rows = {row["id"]: row for row in self._repository.list_roots()}
        bound = [
            b["root_id"] for b in sorted(self._repository.list_bindings(model_type), key=lambda b: b["position"])
            if b["root_id"] != leaving_root_id and b["root_id"] in rows and not rows[b["root_id"]]["read_only"]
        ]
        if not bound:
            return None
        ordered = sorted(bound, key=lambda root_id: root_id != HOME_ROOT_ID)
        for root_id in ordered:
            state, _reason = self._probe.state(self._to_model_root(rows[root_id]))
            if state == "online":
                return root_id
        return ordered[0]

    def _hand_write_back(self, root_id: str, model_type: str) -> None:
        successor = self._write_successor(model_type, root_id)
        if successor is None:
            raise NoWriteSuccessorError(model_type)
        self._repository.set_write(successor, model_type)

    def _hand_back_all_writes(self, root_id: str) -> None:
        for binding in self._repository.bindings_for_root(root_id):
            if binding["is_write"]:
                self._hand_write_back(root_id, binding["model_type"])

    def create_root(
        self,
        path: str,
        *,
        label: Optional[str] = None,
        bindings: Sequence[BindingSpec],
        read_only: bool = False,
        write_types: Sequence[str] = (),
        idempotent: bool = True,
    ) -> Dict[str, Any]:
        root_path = self._resolve_base(path)
        case_insensitive = probe_case_insensitive(root_path) if root_path.is_dir() else default_case_insensitive()
        path_key = root_path_key(root_path, case_insensitive=case_insensitive)

        existing = self._repository.get_root_by_path_key(path_key)
        if existing is not None:
            if not idempotent:
                raise DuplicateRootError(path)
            return self._root_view(existing["id"])

        binding_list = list(bindings)
        self._validate_binding_targets(root_path, binding_list)
        self._check_binding_nesting(None, root_path, binding_list)

        candidate_paths = [self._binding_path(root_path, spec.subdir) for spec in binding_list]
        conflicts = self._overlap_conflicts(candidate_paths)
        if conflicts:
            raise RootOverlapError(conflicts[0][0], self._label_for(conflicts[0][0]), str(conflicts[0][1]))

        root_id = generate_ulid()
        now = now_iso()
        label_value = label or root_path.name or root_id

        write_types_set = set(write_types)
        if write_types_set and read_only:
            raise RootReadOnlyRefusalError(root_id, label_value)

        self._repository.insert_root(
            root_id, label_value, path, path_key, "library", read_only, case_insensitive, now
        )

        max_positions = self._repository.max_position_by_type()
        for spec in binding_list:
            position = max_positions.get(spec.model_type, -1) + 1
            self._repository.insert_binding(
                root_id, spec.model_type, spec.subdir, position, False, spec.scan_headers
            )
            max_positions[spec.model_type] = position

        for model_type in write_types_set:
            spec = next((s for s in binding_list if s.model_type == model_type), None)
            if spec is None:
                raise InvalidBindingError(model_type, "")
            target = self._binding_path(root_path, spec.subdir)
            error = _write_probe(target)
            if error is not None:
                raise WriteProbeFailedError(root_id, error)
            self._repository.set_write(root_id, model_type, spec.subdir)

        self._resolver.invalidate()
        self._indexing.cancel_and_restart(trigger="roots_change")
        return self._root_view(root_id)

    def update_root(
        self,
        root_id: str,
        *,
        label: Optional[str] = None,
        path: Optional[str] = None,
        read_only: Optional[bool] = None,
        bindings: Optional[Sequence[BindingSpec]] = None,
        remove_types: Optional[Sequence[str]] = None,
    ) -> Dict[str, Any]:
        row = self._repository.get_root(root_id)
        if row is None:
            raise RootNotFoundError(root_id)

        remove_types_list = [t for t in (remove_types or []) if t]
        touches_disk = path is not None or bindings is not None or bool(remove_types_list)
        if touches_disk and self._generation_active():
            raise GenerationActiveError()

        if root_id == HOME_ROOT_ID and read_only:
            raise HomeProtectedError(root_id, row["label"])
        if root_id == HOME_ROOT_ID and remove_types_list:
            raise HomeProtectedError(root_id, row["label"])

        now = now_iso()
        new_path_str: Optional[str] = None
        new_path_key: Optional[str] = None
        effective_path = row["path"]

        if path is not None:
            new_root_path = self._resolve_base(path)
            case_insensitive = bool(row["case_insensitive"])
            new_path_key = root_path_key(new_root_path, case_insensitive=case_insensitive)
            duplicate = self._repository.get_root_by_path_key(new_path_key)
            if duplicate is not None and duplicate["id"] != root_id:
                raise DuplicateRootError(path)

            current_bindings = self._repository.bindings_for_root(root_id)
            candidate_paths = [
                self._binding_path(new_root_path, b["subdir"]) for b in current_bindings
            ]
            conflicts = self._overlap_conflicts(candidate_paths, exclude_root_id=root_id)
            if conflicts:
                raise RootOverlapError(conflicts[0][0], self._label_for(conflicts[0][0]), str(conflicts[0][1]))

            new_path_str = path
            effective_path = path

        if bindings is not None:
            root_path_for_bindings = self._resolve_base(effective_path)
            self._validate_binding_targets(root_path_for_bindings, list(bindings))
            self._check_binding_nesting(row, root_path_for_bindings, list(bindings))

        if read_only is True:
            self._hand_back_all_writes(root_id)

        self._repository.update_root(
            root_id, label=label, path=new_path_str, path_key=new_path_key, read_only=read_only, now=now
        )

        if bindings is not None:
            root_path_for_bindings = self._resolve_base(effective_path)
            max_positions = self._repository.max_position_by_type()
            for spec in bindings:
                if self._repository.find_binding(root_id, spec.model_type, spec.subdir) is not None:
                    self._repository.upsert_binding(
                        root_id, spec.model_type, spec.subdir, 0, False, spec.scan_headers
                    )
                    continue
                position = max_positions.get(spec.model_type, -1) + 1
                self._repository.insert_binding(
                    root_id, spec.model_type, spec.subdir, position, False, spec.scan_headers
                )
                max_positions[spec.model_type] = position

        if remove_types_list:
            current_bindings = self._repository.bindings_for_root(root_id)
            for model_type in remove_types_list:
                type_bindings = [b for b in current_bindings if b["model_type"] == model_type]
                if not type_bindings:
                    continue
                was_write = any(bool(b["is_write"]) for b in type_bindings)
                self._repository.delete_binding(root_id, model_type)
                self._locations.delete_for_root_and_type(root_id, model_type)
                if was_write:
                    self._hand_write_back(root_id, model_type)

        self._resolver.invalidate()
        if touches_disk:
            self._indexing.cancel_and_restart(trigger="roots_change")
        return self._root_view(root_id)

    def set_binding_scan_headers(self, root_id: str, model_type: str, subdir: str, enabled: bool) -> Dict[str, Any]:
        if self._repository.get_root(root_id) is None:
            raise RootNotFoundError(root_id)
        _require_header_type(model_type, subdir)
        if self._repository.find_binding(root_id, model_type, subdir) is None:
            raise InvalidBindingError(model_type, subdir)
        self._repository.set_scan_headers(root_id, model_type, enabled, subdir)
        self._resolver.invalidate()
        self._indexing.cancel_and_restart(trigger="roots_change")
        return self._root_view(root_id)

    def delete_root(self, root_id: str) -> None:
        row = self._repository.get_root(root_id)
        if row is None:
            raise RootNotFoundError(root_id)
        if root_id == HOME_ROOT_ID:
            raise HomeProtectedError(root_id, row["label"])
        if self._generation_active():
            raise GenerationActiveError()

        self._hand_back_all_writes(root_id)
        self._repository.delete_root(root_id)
        self._resolver.invalidate()
        self._indexing.cancel_and_restart(trigger="roots_change")

    def reorder(self, model_type: Optional[str], root_ids: Sequence[str]) -> None:
        root_ids = list(root_ids)
        if len(set(root_ids)) != len(root_ids):
            raise InvalidBindingError(model_type or "*", "duplicate root_ids")

        types = [model_type] if model_type else self._types_bound_by(root_ids)
        if not types:
            raise InvalidBindingError(model_type or "*", "no matching bindings")

        for a_type in types:
            current = sorted(self._repository.list_bindings(a_type), key=lambda b: b["position"])
            current_ids = list(dict.fromkeys(b["root_id"] for b in current))
            if model_type is not None:
                unknown = [rid for rid in root_ids if rid not in current_ids]
                if unknown:
                    raise InvalidBindingError(a_type, f"'{unknown[0]}' has no binding for this type")
                final_order = list(root_ids)
            else:
                ordered = [rid for rid in root_ids if rid in current_ids]
                remaining = [rid for rid in current_ids if rid not in ordered]
                final_order = ordered + remaining
            remaining_roots = [rid for rid in current_ids if rid not in final_order]
            block_order = final_order + remaining_roots
            by_root: Dict[str, List[str]] = {}
            for binding in current:
                by_root.setdefault(binding["root_id"], []).append(binding["id"])
            self._repository.reorder_bindings(
                a_type, [binding_id for rid in block_order for binding_id in by_root.get(rid, [])]
            )

        self._resolver.invalidate()
        self._indexing.cancel_and_restart(trigger="roots_change")

    def set_write_root(self, model_type: Optional[str], root_id: str) -> Dict[str, Any]:
        row = self._repository.get_root(root_id)
        if row is None:
            raise RootNotFoundError(root_id)
        if row["read_only"]:
            raise RootReadOnlyRefusalError(root_id, row["label"])

        types = [model_type] if model_type else self._types_bound_by([root_id])
        if not types:
            raise InvalidBindingError(model_type or "*", "root has no bindings")

        root_model = self._to_model_root(row)
        state, _reason = self._probe.state(root_model)
        if state != "online":
            raise RootOfflineError(root_id, row["label"], state)

        root_path = self._resolve_base(row["path"])
        bindings_by_type: Dict[str, Dict[str, Any]] = {}
        for b in self._repository.bindings_for_root(root_id):
            bindings_by_type.setdefault(b["model_type"], b)
        for a_type in types:
            binding = bindings_by_type.get(a_type)
            if binding is None:
                raise InvalidBindingError(a_type, "root has no binding for this type")
            target = self._binding_path(root_path, binding["subdir"])
            error = _write_probe(target)
            if error is not None:
                raise WriteProbeFailedError(root_id, error)

        for a_type in types:
            self._repository.set_write(root_id, a_type)

        self._resolver.invalidate()
        self._indexing.cancel_and_restart(trigger="roots_change")
        return self._root_view(root_id)

    def probe_root(self, root_id: str) -> Dict[str, Any]:
        row = self._repository.get_root(root_id)
        if row is None:
            raise RootNotFoundError(root_id)
        self._probe.refresh(root_id)
        root_model = self._to_model_root(row)
        state, reason = self._probe.state(root_model)
        self._repository.update_root_state(root_id, state, reason, now_iso())
        self._resolver.invalidate()
        return self._root_view(root_id)

    def sync_home_from_setting(self) -> None:
        setting = self._settings.get_setting_by_key("models_dir")
        value = setting.get_typed_value() if setting else "models"
        row = self._repository.get_root(HOME_ROOT_ID)
        if row is None or row["path"] == value:
            return
        root_path = self._resolve_base(value)
        case_insensitive = bool(row["case_insensitive"])
        path_key = root_path_key(root_path, case_insensitive=case_insensitive)
        self._repository.update_root(HOME_ROOT_ID, path=value, path_key=path_key, now=now_iso())
        self._resolver.invalidate()

    def _read_unplaced(self) -> List[Dict[str, Any]]:
        setting = self._settings.get_setting_by_key("model_roots_unplaced")
        value = setting.get_typed_value() if setting else []
        return [entry for entry in (value or []) if self._resolver.to_logical(entry.get("dir", "")) is None]

    def _unindexed_counts(self) -> Dict[str, int]:
        try:
            return self._indexing.scanner.count_unindexed_by_binding()
        except Exception:
            return {}

    def _root_view(self, root_id: str) -> Dict[str, Any]:
        row = self._repository.get_root(root_id)
        if row is None:
            raise RootNotFoundError(root_id)
        bindings = sorted(self._repository.bindings_for_root(root_id), key=lambda b: (b["model_type"], b["position"]))
        aggregates = self._locations.aggregate_by_binding()
        return self._build_root_dict(row, bindings, aggregates, self._unindexed_counts())

    def _build_root_dict(
        self,
        row: Dict[str, Any],
        bindings: List[Dict[str, Any]],
        aggregates: Dict[str, Dict[str, int]],
        unindexed: Optional[Dict[str, int]] = None,
    ) -> Dict[str, Any]:
        unindexed = unindexed or {}
        root_path = self._resolve_base(row["path"])
        binding_dicts = []
        for binding in bindings:
            target = self._binding_path(root_path, binding["subdir"])
            agg = aggregates.get(binding["id"], {"indexed_files": 0, "size_bytes": 0})
            binding_dicts.append({
                "binding_id": binding["id"],
                "model_type": binding["model_type"],
                "folder": MODEL_TYPE_TO_DIRECTORY[binding["model_type"]],
                "subdir": binding["subdir"],
                "path": str(target),
                "exists": target.is_dir(),
                "position": binding["position"],
                "is_write": bool(binding["is_write"]),
                "scan_headers": bool(binding.get("scan_headers", 0)),
                "indexed_files": agg["indexed_files"],
                "size_bytes": agg["size_bytes"],
                "unindexed": unindexed.get(binding["id"], 0),
            })
        return {
            "id": row["id"],
            "label": row["label"],
            "path": row["path"],
            "kind": row["kind"],
            "read_only": bool(row["read_only"]),
            "case_insensitive": bool(row["case_insensitive"]),
            "state": row["state"],
            "state_reason": row.get("state_reason"),
            "state_checked_at": row.get("state_checked_at"),
            "layout_profile": row.get("layout_profile"),
            "bindings": binding_dicts,
        }

    def get_overview(self) -> Dict[str, Any]:
        roots_rows = self._repository.list_roots()
        bindings_rows = self._repository.list_bindings()
        aggregates = self._locations.aggregate_by_binding()

        bindings_by_root: Dict[str, List[Dict[str, Any]]] = {}
        for binding in bindings_rows:
            bindings_by_root.setdefault(binding["root_id"], []).append(binding)

        unindexed = self._unindexed_counts()
        roots_out = [
            self._build_root_dict(
                row,
                sorted(bindings_by_root.get(row["id"], []), key=lambda b: (b["model_type"], b["position"])),
                aggregates,
                unindexed,
            )
            for row in roots_rows
        ]

        types_out = []
        for model_type in MODEL_TYPES:
            type_bindings = sorted(
                (b for b in bindings_rows if b["model_type"] == model_type), key=lambda b: b["position"]
            )
            write_root_id = next((b["root_id"] for b in type_bindings if b["is_write"]), None)
            types_out.append({
                "model_type": model_type,
                "folder": MODEL_TYPE_TO_DIRECTORY[model_type],
                "order": list(dict.fromkeys(b["root_id"] for b in type_bindings)),
                "bindings": [
                    {"root_id": b["root_id"], "subdir": b["subdir"], "binding_id": b["id"]} for b in type_bindings
                ],
                "write_root_id": write_root_id,
            })

        return {
            "roots": roots_out,
            "types": types_out,
            "unplaced": self._read_unplaced(),
            "indexing": self._indexing.status(),
        }
