import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.features.models.locations_repository import ModelLocationsRepository
from src.features.models.records import Model
from src.platform.filesystem.model_roots import LogicalLocation, ModelRootError, ModelRootResolver


class ModelFileUnavailable(Exception):

    def __init__(self, reason: str, root_label: Optional[str] = None):
        super().__init__(reason)
        self.reason = reason
        self.root_label = root_label


@dataclass(frozen=True)
class LocationView:
    id: str
    root_id: str
    root_label: str
    model_type: str
    rel_path: str
    path: Optional[Path]
    status: str
    size: Optional[int]
    sha256: Optional[str]
    is_winner: bool


def _rel_key(rel_path: str, case_insensitive: bool) -> str:
    key = unicodedata.normalize("NFC", rel_path)
    return key.casefold() if case_insensitive else key


def _exists(path: Optional[Path]) -> bool:
    if path is None:
        return False
    try:
        return path.exists()
    except OSError:
        return False


class ModelLocator:

    def __init__(
        self,
        resolver: ModelRootResolver,
        locations_repository: Optional[ModelLocationsRepository] = None,
        model_repository: Optional[Any] = None,
    ):
        self._resolver = resolver
        self._locations = locations_repository or ModelLocationsRepository()
        self._model_repository = model_repository

    def _model_repo(self):
        if self._model_repository is not None:
            return self._model_repository
        from src.features.models.repository import model_repo

        return model_repo

    def _root_label(self, root_id: str) -> str:
        for root in self._resolver.roots():
            if root.id == root_id:
                return root.label
        return root_id

    def _ordered_present_rows(self, rows: List[Dict[str, Any]], model_type: str) -> List[Dict[str, Any]]:
        positions = {td.root_id: td.position for td in self._resolver.type_dirs(model_type, online_only=False)}
        present = [row for row in rows if row["status"] == "present"]
        return sorted(
            present,
            key=lambda row: (positions.get(row["root_id"], len(positions) + 1), len(row["rel_path"]), row["rel_path"]),
        )

    def path_for_model(self, model_id: str) -> Path:
        rows = self._locations.list_for_model(model_id)
        if not rows:
            raise ModelFileUnavailable(f"model '{model_id}' has no known location")

        model_type = rows[0]["model_type"]
        ordered = self._ordered_present_rows(rows, model_type)
        if not ordered:
            raise ModelFileUnavailable(f"model '{model_id}' has no present location")

        offline_label: Optional[str] = None
        for row in ordered:
            if not self._resolver.is_online_for(row["root_id"], row["model_type"]):
                if offline_label is None:
                    offline_label = self._root_label(row["root_id"])
                continue
            try:
                physical = self._resolver.physical(
                    LogicalLocation(row["root_id"], row["model_type"], row["rel_path"])
                )
            except ModelRootError:
                continue
            if _exists(physical):
                return physical

        if offline_label is not None:
            raise ModelFileUnavailable(f"is on '{offline_label}' (offline)", root_label=offline_label)
        raise ModelFileUnavailable(f"model '{model_id}' has no present copy reachable on any root")

    def path_for_ref(self, logical_ref: str) -> Path:
        parsed = self._resolver.parse_logical_ref(logical_ref)
        if parsed is None:
            raise ModelFileUnavailable(f"'{logical_ref}' is not a model reference")
        model_type, rel_path = parsed

        bindings = sorted(
            self._resolver.type_dirs(model_type, online_only=False), key=lambda td: td.position
        )
        if not bindings:
            raise ModelFileUnavailable(f"no model root is bound for '{model_type}'")

        offline_label: Optional[str] = None
        for type_dir in bindings:
            root_label = self._root_label(type_dir.root_id)
            if not self._resolver.is_online_for(type_dir.root_id, model_type):
                if offline_label is None and self._has_present_location(type_dir.root_id, model_type, rel_path):
                    offline_label = root_label
                continue
            try:
                physical = self._resolver.physical(LogicalLocation(type_dir.root_id, model_type, rel_path))
            except ModelRootError:
                continue
            if _exists(physical):
                return physical

        if offline_label is not None:
            raise ModelFileUnavailable(f"is on '{offline_label}' (offline)", root_label=offline_label)
        raise ModelFileUnavailable(f"'{logical_ref}' was not found on any model root")

    def _has_present_location(self, root_id: str, model_type: str, rel_path: str) -> bool:
        for root in self._resolver.roots():
            if root.id == root_id:
                rel_key = _rel_key(rel_path, root.case_insensitive)
                row = self._locations.get(root_id, model_type, rel_key)
                return row is not None and row["status"] == "present"
        return False

    def model_for_path(self, path: Any) -> Optional[Model]:
        loc = self._resolver.to_logical(path)
        if loc is not None:
            for root in self._resolver.roots():
                if root.id == loc.root_id:
                    rel_key = _rel_key(loc.rel_path, root.case_insensitive)
                    row = self._locations.get(loc.root_id, loc.model_type, rel_key)
                    if row is not None:
                        model = self._model_repo().get_by_id(
                            row["model_id"], include_providers=False, include_tags=False
                        )
                        if model is not None:
                            return model
                    break

        value = str(path)
        model = self._model_repo().get_by_file_path(value, include_providers=False)
        if model is not None:
            return model

        filename = value.replace("\\", "/").rsplit("/", 1)[-1]
        if not filename:
            return None
        matches = self._model_repo().get_by_filename(filename)
        return matches[0] if len(matches) == 1 else None

    def locations(self, model_id: str) -> List[LocationView]:
        rows = self._locations.list_for_model(model_id)
        if not rows:
            return []

        model_type = rows[0]["model_type"]
        ordered = self._ordered_present_rows(rows, model_type)
        winner_id = ordered[0]["id"] if ordered else None

        views: List[LocationView] = []
        for row in rows:
            try:
                physical = self._resolver.physical(
                    LogicalLocation(row["root_id"], row["model_type"], row["rel_path"])
                )
            except ModelRootError:
                physical = None
            views.append(
                LocationView(
                    id=row["id"],
                    root_id=row["root_id"],
                    root_label=self._root_label(row["root_id"]),
                    model_type=row["model_type"],
                    rel_path=row["rel_path"],
                    path=physical,
                    status=row["status"],
                    size=row.get("size"),
                    sha256=row.get("sha256"),
                    is_winner=row["id"] == winner_id,
                )
            )
        return views


_default_locator: Optional[ModelLocator] = None


def default_model_locator() -> ModelLocator:
    global _default_locator
    if _default_locator is None:
        from pathlib import Path

        from src.platform.filesystem.model_roots import ModelRootResolver, RootProbe
        from src.platform.filesystem.model_roots_repository import ModelRootRepository

        _default_locator = ModelLocator(ModelRootResolver(ModelRootRepository(), RootProbe(), Path.cwd()))
    return _default_locator
