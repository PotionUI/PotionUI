import hashlib
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from unittest.mock import patch

from src.features.models.indexer import ModelScanner
from src.features.models.locations_repository import ModelLocationsRepository
from src.features.models.records import Model
from src.features.models.repository import model_repo
from src.platform.database.rows import now_iso
from src.platform.filesystem.model_roots import ModelRoot, ModelRootResolver, TypeDir, _Snapshot, root_path_key
from src.platform.filesystem.model_roots_repository import ModelRootRepository
from src.platform.filesystem.model_types import MODEL_TYPE_TO_DIRECTORY

_POSITION_OFFSET = 2000


class FakeProbe:
    def __init__(self, states: Optional[Dict[str, Tuple[str, Optional[str]]]] = None):
        self._states = states if states is not None else {}

    def state(self, root: ModelRoot) -> Tuple[str, Optional[str]]:
        return self._states.get(root.id, ("online", None))


def _resolver(roots: List[ModelRoot], bindings: List[TypeDir], states: Optional[dict] = None) -> ModelRootResolver:
    resolver = ModelRootResolver(repository=None, probe=FakeProbe(states), base_dir=Path.cwd())
    roots_by_id = {r.id: r for r in roots}
    by_type: Dict[str, List[TypeDir]] = {}
    for binding in bindings:
        by_type.setdefault(binding.model_type, []).append(binding)
    type_dirs_by_type = {
        model_type: tuple(sorted(entries, key=lambda e: e.position))
        for model_type, entries in by_type.items()
    }
    resolver._snapshot = _Snapshot(roots=tuple(roots), roots_by_id=roots_by_id, type_dirs_by_type=type_dirs_by_type)
    return resolver


def _root(root_id: str, path, *, case_insensitive: bool = False, state: str = "online") -> ModelRoot:
    assert root_id != "home", "use a different id: migration 035 already seeds 'home'"
    now = now_iso()
    key = root_path_key(str(path), case_insensitive=case_insensitive)
    ModelRootRepository().insert_root(root_id, root_id, str(path), key, "library", False, case_insensitive, now)
    return ModelRoot(
        id=root_id, label=root_id, path=Path(path), kind="library", read_only=False,
        case_insensitive=case_insensitive, state=state, state_reason=None, raw_path=str(path),
    )


def _binding(root_id: str, model_type: str, path, position: int, *, is_write: bool = False, subdir: Optional[str] = None) -> TypeDir:
    if subdir is None:
        subdir = MODEL_TYPE_TO_DIRECTORY[model_type]
    real_position = position + _POSITION_OFFSET
    binding_id = ModelRootRepository().insert_binding(root_id, model_type, subdir, real_position, is_write)
    return TypeDir(root_id=root_id, model_type=model_type, path=Path(path), position=real_position, is_write=is_write, subdir=subdir, binding_id=binding_id)


def _write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _models() -> list:
    return model_repo.get_all(include_providers=False, include_tags=False)


def _no_hashing():
    return patch(
        "src.features.models.indexer.hashlib.sha256",
        side_effect=AssertionError("hashing must not run for an adopted row"),
    )


def _seed_migrated_row(root_id: str, model_type: str, rel_path: str, content: bytes):
    content_sha = _sha(content)
    model = model_repo.create(Model(
        filename=Path(rel_path).name, file_size=len(content),
        sha256=content_sha, model_type=model_type,
    ))
    ModelLocationsRepository().upsert(
        model_id=model.id, binding_id=ModelRootRepository().bindings_for(root_id, model_type)[0]["id"],
        root_id=root_id, model_type=model_type,
        rel_path=rel_path, rel_key=rel_path, size=len(content), mtime_ns=None,
        sha256=content_sha, status="present", seen_at=now_iso(),
    )
    return model


def test_migrated_row_with_null_mtime_is_adopted_without_hashing(tmp_path, mock_db):
    home = tmp_path / "home"
    content = b"a migrated checkpoint"
    path = home / "checkpoints" / "m.safetensors"
    _write(path, content)

    home_root = _root("r_home", home)
    home_binding = _binding("r_home", "checkpoint", home / "checkpoints", 0)
    model = _seed_migrated_row("r_home", "checkpoint", "m.safetensors", content)

    resolver = _resolver([home_root], [home_binding])
    scanner = ModelScanner(resolver)

    with _no_hashing():
        scanner.index_models(max_workers=1)

    location = scanner.locations.list_for_model(model.id)[0]
    assert location["status"] == "present"
    assert location["mtime_ns"] is not None
    assert location["sha256"] == _sha(content)
    assert len(_models()) == 1


def test_rehomed_row_with_null_mtime_is_adopted_without_hashing(tmp_path, mock_db):
    depot = tmp_path / "depot"
    depot_alias = tmp_path / "depot-alias"
    content = b"a rehomed checkpoint"
    _write(depot / "checkpoints" / "m.safetensors", content)
    depot_alias.symlink_to(depot, target_is_directory=True)

    home_root = _root("r_home", depot)
    resolver_before = _resolver(
        [home_root], [_binding("r_home", "checkpoint", depot / "checkpoints", 0)]
    )
    scanner_before = ModelScanner(resolver_before)
    scanner_before.index_models(max_workers=1)
    model = _models()[0]
    original_location = scanner_before.locations.list_for_model(model.id)[0]
    assert original_location["mtime_ns"] is not None

    lib_root = _root("r_lib", depot_alias)
    lib_binding = _binding("r_lib", "checkpoint", depot_alias / "checkpoints", 1)
    moved = ModelLocationsRepository().rehome("r_home", "checkpoint", "r_lib")
    assert moved == 1
    rehomed = ModelLocationsRepository().get(lib_binding.binding_id, "m.safetensors")
    assert rehomed is not None
    assert rehomed["mtime_ns"] is None

    resolver_after = _resolver([lib_root], [lib_binding])
    scanner_after = ModelScanner(resolver_after)

    with _no_hashing():
        scanner_after.index_models(max_workers=1)

    refreshed = ModelLocationsRepository().get(lib_binding.binding_id, "m.safetensors")
    assert refreshed is not None
    assert refreshed["status"] == "present"
    assert refreshed["mtime_ns"] is not None
    assert refreshed["sha256"] == _sha(content)
    assert len(_models()) == 1
    assert _models()[0].id == model.id


def test_a_genuinely_resized_file_is_rehashed(tmp_path, mock_db):
    home = tmp_path / "home"
    path = home / "checkpoints" / "m.safetensors"
    _write(path, b"short")

    resolver = _resolver([_root("r_home", home)], [_binding("r_home", "checkpoint", home / "checkpoints", 0)])
    scanner = ModelScanner(resolver)
    scanner.index_models(max_workers=1)
    model_before = _models()[0]

    new_content = b"a much longer replacement payload"
    path.write_bytes(new_content)

    scanner.index_models(max_workers=1)

    models = _models()
    assert len(models) == 1
    assert models[0].id == model_before.id
    assert models[0].sha256 == _sha(new_content)


def test_same_size_content_change_with_mismatching_mtime_is_rehashed(tmp_path, mock_db):
    home = tmp_path / "home"
    path = home / "checkpoints" / "m.safetensors"
    original = b"AAAAAAAAAA"
    _write(path, original)

    resolver = _resolver([_root("r_home", home)], [_binding("r_home", "checkpoint", home / "checkpoints", 0)])
    scanner = ModelScanner(resolver)
    scanner.index_models(max_workers=1)
    model_before = _models()[0]
    location_before = scanner.locations.list_for_model(model_before.id)[0]
    assert location_before["mtime_ns"] is not None

    same_size_new_content = b"BBBBBBBBBB"
    assert len(same_size_new_content) == len(original)
    path.write_bytes(same_size_new_content)
    bumped_mtime = path.stat().st_mtime_ns + 5_000_000_000
    os.utime(path, ns=(bumped_mtime, bumped_mtime))

    scanner.index_models(max_workers=1)

    models = _models()
    assert len(models) == 1
    assert models[0].id == model_before.id
    assert models[0].sha256 == _sha(same_size_new_content)
    assert models[0].sha256 != location_before["sha256"]
