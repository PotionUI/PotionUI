from __future__ import annotations

import logging
import threading
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from src.features.filters.catalog import FilterCatalog
from src.features.filters.payload import USER_ID_PREFIX, user_revision
from src.features.filters.plugin_ops import load_op_impl
from src.features.filters.repository import UserFilterRepository
from src.features.filters.schema import has_python_impl, step_op_ids
from src.platform.imaging.filters import CORE_OPS, OpSpec
from src.platform.imaging.filters.cube import Cube, CubeError, read_cube
from src.platform.media_tools.effects.looks import Look

logger = logging.getLogger(__name__)


@lru_cache(maxsize=32)
def _cube(path: str, mtime_ns: int, size: int) -> Cube:
    return read_cube(path)


class FilterLooks:
    def __init__(self, catalog: FilterCatalog, repository: UserFilterRepository):
        self.catalog = catalog
        self.repository = repository
        self._impls: Dict[tuple, Any] = {}
        self._lock = threading.Lock()

    def _renderable(self, steps: Sequence[Mapping[str, Any]], enabled: Mapping[str, OpSpec]) -> bool:
        return all(op_id in enabled and has_python_impl(enabled[op_id]) for op_id in step_op_ids(list(steps)))

    def _load_impls(self, steps: Sequence[Mapping[str, Any]], enabled: Mapping[str, OpSpec]) -> Optional[Dict[str, Any]]:
        impls: Dict[str, Any] = {}
        dirs: Optional[Dict[str, Path]] = None
        for op_id in step_op_ids(list(steps)):
            spec = enabled[op_id]
            if op_id in CORE_OPS and spec.source == "core":
                continue
            dirs = self.catalog.plugin_dirs() if dirs is None else dirs
            plugin_dir = dirs.get(spec.plugin_id or "")
            if plugin_dir is None or not spec.python:
                return None
            key = (str(plugin_dir), spec.python)
            with self._lock:
                impl = self._impls.get(key)
                if impl is None:
                    impl = load_op_impl(plugin_dir, spec.python)
                    if impl is not None:
                        self._impls[key] = impl
            if impl is None:
                return None
            impls[op_id] = impl
        return impls

    def _file_look(self, ref: str, enabled: Mapping[str, OpSpec], with_impls: bool) -> Optional[Look]:
        definition = self.catalog.get_filter(ref)
        if definition is None or not self._renderable(definition.steps, enabled):
            return None
        cube = None
        path = definition.lut_path
        if path is not None:
            try:
                stat = path.stat()
                cube = _cube(str(path), stat.st_mtime_ns, stat.st_size)
            except (OSError, CubeError):
                logger.warning("The look %s has an unreadable LUT", ref, exc_info=True)
                return None
        impls = self._load_impls(definition.steps, enabled) if with_impls else {}
        if impls is None:
            return None
        return Look(
            id=definition.public_id,
            name=definition.name,
            group=definition.group,
            intensity=float(definition.intensity),
            steps=tuple(definition.steps),
            revision=definition.revision,
            cube=cube,
            ops=dict(enabled),
            impls=impls,
        )

    def _user_look(self, row, enabled: Mapping[str, OpSpec], with_impls: bool) -> Optional[Look]:
        if not self._renderable(row.steps, enabled):
            return None
        impls = self._load_impls(row.steps, enabled) if with_impls else {}
        if impls is None:
            return None
        return Look(
            id=f"{USER_ID_PREFIX}{row.id}",
            name=row.name,
            group=row.group_name,
            intensity=float(row.intensity),
            steps=tuple(row.steps),
            revision=user_revision(row.steps, row.intensity),
            ops=dict(enabled),
            impls=impls,
        )

    def resolve(self, ref: str, user_id: Optional[str]) -> Optional[Look]:
        enabled = self.catalog.enabled_ops()
        if not ref.startswith(USER_ID_PREFIX):
            return self._file_look(ref, enabled, True)
        if not user_id:
            return None
        row = self.repository.get_for_owner(user_id, ref[len(USER_ID_PREFIX):])
        return self._user_look(row, enabled, True) if row is not None else None

    def list(self, user_id: Optional[str]) -> List[Look]:
        enabled = self.catalog.enabled_ops()
        looks = [self._file_look(definition.public_id, enabled, False) for definition in self.catalog.list_filters()]
        if user_id:
            looks += [self._user_look(row, enabled, False) for row in self.repository.list_for_owner(user_id)]
        return [look for look in looks if look is not None]
