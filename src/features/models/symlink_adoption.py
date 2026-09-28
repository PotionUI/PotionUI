import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.features.models.locations_repository import ModelLocationsRepository
from src.features.models.roots import BindingSpec, ModelRootsError, ModelRootsManager
from src.platform.database.rows import now_iso
from src.platform.filesystem.model_roots import (
    HOME_ROOT_ID,
    ModelRootResolver,
    default_case_insensitive,
    root_path_key,
)
from src.platform.filesystem.model_roots_repository import ModelRootRepository
from src.platform.filesystem.model_types import MODEL_TYPE_TO_DIRECTORY, MODEL_TYPES
from src.platform.settings.repository import SettingRepository
from src.platform.util.ids import generate_ulid

logger = logging.getLogger(__name__)

_LEGACY_SETTING_KEYS = ("models_location_external_path", "models_location_overrides")


def is_windows() -> bool:
    return os.name == "nt"


def _is_link(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
    except OSError:
        pass
    is_junction = getattr(path, "is_junction", None)
    if is_junction is None:
        return False
    try:
        return bool(is_junction())
    except OSError:
        return False


def _resolve_link_target(link_path: Path) -> Optional[Path]:
    try:
        raw = os.readlink(link_path)
    except OSError:
        raw = None
    if raw is not None:
        target = Path(raw)
        if not target.is_absolute():
            target = link_path.parent / target
        return Path(os.path.normpath(str(target)))
    try:
        return link_path.resolve(strict=False)
    except OSError:
        return None


def _is_reachable(path: Path) -> bool:
    try:
        return path.is_dir()
    except OSError:
        return False


def _remove_link(link_path: Path) -> None:
    if is_windows():
        link_path.rmdir()
    else:
        link_path.unlink()


def _replace_link_with_dir(link_path: Path) -> bool:
    try:
        _remove_link(link_path)
    except OSError:
        return False
    try:
        link_path.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False
    return True


def _group_by_parent(targets: Dict[str, Path]) -> List[Tuple[Path, Dict[str, str]]]:
    buckets: Dict[str, Tuple[Path, Dict[str, Path]]] = {}
    for model_type, target in targets.items():
        parent = target.parent
        key = root_path_key(parent)
        if key not in buckets:
            buckets[key] = (parent, {})
        buckets[key][1][model_type] = target

    groups: List[Tuple[Path, Dict[str, str]]] = []
    for parent, members in buckets.values():
        if len(members) >= 2:
            groups.append((parent, {t: target.name for t, target in members.items()}))
        else:
            (model_type, target), = members.items()
            groups.append((target, {model_type: ""}))
    return groups


def _ensure_reachable_root(
    roots_manager: ModelRootsManager,
    root_path: Path,
    bindings: Dict[str, str],
) -> Optional[str]:
    specs = [BindingSpec(model_type=t, subdir=s) for t, s in bindings.items()]
    try:
        root = roots_manager.create_root(str(root_path), bindings=specs)
    except ModelRootsError:
        return None

    existing_types = {b["model_type"] for b in root["bindings"]}
    missing = [s for s in specs if s.model_type not in existing_types]
    if missing:
        try:
            root = roots_manager.update_root(root["id"], bindings=missing)
        except ModelRootsError:
            pass
    return root["id"]


def _ensure_offline_root(
    root_repository: ModelRootRepository,
    resolver: ModelRootResolver,
    roots_manager: ModelRootsManager,
    root_path: Path,
    bindings: Dict[str, str],
) -> Optional[str]:
    case_insensitive = default_case_insensitive()
    path_key = root_path_key(root_path, case_insensitive=case_insensitive)

    existing = root_repository.get_root_by_path_key(path_key)
    if existing is not None:
        root_id = existing["id"]
    else:
        root_id = generate_ulid()
        root_repository.insert_root(
            root_id,
            root_path.name or root_id,
            str(root_path),
            path_key,
            "library",
            False,
            case_insensitive,
            now_iso(),
        )

    max_positions = root_repository.max_position_by_type()
    for model_type, subdir in bindings.items():
        if root_repository.has_binding(root_id, model_type):
            continue
        position = max_positions.get(model_type, -1) + 1
        root_repository.insert_binding(root_id, model_type, subdir, position, False)
        max_positions[model_type] = position

    resolver.invalidate()
    try:
        roots_manager.probe_root(root_id)
    except ModelRootsError:
        pass
    return root_id


def adopt_symlinked_model_roots(
    resolver: ModelRootResolver,
    roots_manager: ModelRootsManager,
    root_repository: ModelRootRepository,
    locations_repository: ModelLocationsRepository,
    setting_repository: SettingRepository,
    indexing_coordinator: Any,
) -> bool:
    home_dir = resolver.home_dir()

    reachable_targets: Dict[str, Path] = {}
    dangling_targets: Dict[str, Path] = {}

    for model_type in MODEL_TYPES:
        link_path = home_dir / MODEL_TYPE_TO_DIRECTORY[model_type]
        if not _is_link(link_path):
            continue
        target = _resolve_link_target(link_path)
        if target is None:
            continue
        if _is_reachable(target):
            reachable_targets[model_type] = target
        else:
            dangling_targets[model_type] = target

    changed = False

    for root_path, bindings in _group_by_parent(reachable_targets):
        root_id = _ensure_reachable_root(roots_manager, root_path, bindings)
        if root_id is None:
            continue
        for model_type in bindings:
            moved = locations_repository.rehome(HOME_ROOT_ID, model_type, root_id)
            if moved:
                changed = True
            link_path = home_dir / MODEL_TYPE_TO_DIRECTORY[model_type]
            if _replace_link_with_dir(link_path):
                changed = True
            try:
                roots_manager.set_write_root(model_type, root_id)
                changed = True
            except ModelRootsError as exc:
                logger.warning(
                    "Could not move the write root for '%s' to '%s': %s - downloads stay on the home root",
                    model_type, root_path, exc,
                )

    for root_path, bindings in _group_by_parent(dangling_targets):
        root_id = _ensure_offline_root(root_repository, resolver, roots_manager, root_path, bindings)
        if root_id is None:
            continue
        for model_type in bindings:
            moved = locations_repository.rehome(HOME_ROOT_ID, model_type, root_id)
            if moved:
                changed = True

    remaining_links = any(_is_link(home_dir / MODEL_TYPE_TO_DIRECTORY[t]) for t in MODEL_TYPES)
    if not remaining_links:
        for key in _LEGACY_SETTING_KEYS:
            if setting_repository.delete_setting_by_key(key):
                changed = True

    if changed:
        indexing_coordinator.cancel_and_restart(trigger="roots_change")

    return changed
